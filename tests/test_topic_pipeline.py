from __future__ import annotations

import asyncio
import shutil
from uuid import UUID, uuid4

import pytest

from app.domain.models import (
    ArtifactSummary,
    GenerationTaskKind,
    GenerationTaskRecord,
    ProjectRecord,
    ScriptContent,
    StageName,
    StageRun,
    TaskError,
    TaskStatus,
    TopicProductionCreateRequest,
)
from app.domain.topic_run import (
    TOPIC_RUN_CONTROL_REVISION,
    TOPIC_RUN_ERROR_CODE,
    TOPIC_RUN_ERROR_MESSAGE,
    TOPIC_RUN_ID,
    TOPIC_RUN_STATUS,
)
from app.providers.local_fixture_video import LocalFixtureVideoGenerationProvider
from app.providers.mock_subtitle_alignment import MockSentenceSubtitleAlignmentProvider
from app.providers.mock_tts import MockTTSProvider
from app.providers.mock_video import MockVideoGenerationProvider
from app.repositories.in_memory import InMemoryStore
from app.services.topic_pipeline_service import TopicMediaTaskService, TopicProductionOrchestrator
from app.services.task_service import TaskService
from app.storage.local import LocalFileArtifactStorage
from app.workers.generation_worker import _reconcile_runs_after_restart


class RecordingQueue:
    def __init__(self) -> None:
        self.enqueued: list[UUID] = []

    async def enqueue(self, task_id: UUID) -> None:
        self.enqueued.append(task_id)

    async def close(self) -> None:
        return None

    async def ack(self, task_id: UUID) -> None:
        del task_id

    async def heartbeat(self, payload: dict[str, object]) -> None:
        del payload

    async def worker_status(self) -> dict[str, object] | None:
        return None


def _project() -> ProjectRecord:
    return ProjectRecord(
        title="主题流水线测试",
        topic="如何选择露营装备",
        language="zh-CN",
        target_duration_seconds=15,
        aspect_ratio="9:16",
        tone="清晰、实用",
    )


def _script() -> ScriptContent:
    return ScriptContent(
        title="露营装备入门",
        hook="第一次露营，先别急着买一堆装备。",
        summary="用三个步骤选出够用的露营装备。",
        duration_seconds=15,
        scenes=[
            {
                "scene_index": 1,
                "duration_seconds": 15,
                "voiceover": "第一次露营，先从帐篷、睡袋和照明开始准备。",
                "caption": "先准备三类核心装备",
                "visual_keywords": ["camping gear", "flat illustration"],
                "transition": "cut",
                "risk_notes": [],
            }
        ],
        cta="收藏这条内容，按自己的需求逐项核对。",
        risk_notes=[],
    )


def _script_task(project_id: UUID, content: ScriptContent) -> GenerationTaskRecord:
    return GenerationTaskRecord(
        project_id=project_id,
        kind=GenerationTaskKind.INFO_SCRIPT,
        input_data={},
        status=TaskStatus.SUCCEEDED,
        current_stage=None,
        progress=100,
        stages=[StageRun(stage=StageName.SCRIPT, status=TaskStatus.SUCCEEDED, progress=100)],
        artifacts=[
            ArtifactSummary(
                type="script_json",
                provider="test",
                preview=content.model_dump(mode="json"),
            )
        ],
    )


async def _create_script_task(
    store: InMemoryStore,
    queue: RecordingQueue,
    project_id: UUID,
    idempotency_key: str,
    task_input_data: dict[str, object],
) -> tuple[GenerationTaskRecord, bool]:
    task = GenerationTaskRecord(
        project_id=project_id,
        kind=GenerationTaskKind.INFO_SCRIPT,
        input_data=task_input_data,
        status=TaskStatus.QUEUED,
        current_stage=StageName.SCRIPT,
        stages=[StageRun(stage=StageName.SCRIPT, status=TaskStatus.QUEUED)],
    )
    stored, reused = await store.create_task(task, idempotency_key)
    if not reused:
        await queue.enqueue(stored.id)
    return stored, reused


def test_topic_start_creates_one_script_root_for_same_idempotency_key() -> None:
    async def exercise() -> None:
        store = InMemoryStore()
        queue = RecordingQueue()
        project = await store.create_project(_project())

        async def creator(project_id, idempotency_key, task_input_data):
            return await _create_script_task(
                store,
                queue,
                project_id,
                idempotency_key,
                task_input_data,
            )

        orchestrator = TopicProductionOrchestrator(store, object(), script_task_creator=creator)
        first = await orchestrator.start(project.id, idempotency_key="demo-1")
        second = await orchestrator.start(project.id, idempotency_key="demo-1")
        tasks = await store.list_tasks(project_id=project.id, limit=20)

        assert first.status == "active"
        assert second.run_id == first.run_id
        assert len(tasks) == 1
        assert tasks[0].kind == GenerationTaskKind.INFO_SCRIPT
        assert tasks[0].input_data["topic_pipeline"] is True
        assert tasks[0].input_data["topic_run_id"] == str(first.run_id)
        assert queue.enqueued == [tasks[0].id]

    asyncio.run(exercise())


def test_regular_info_script_does_not_start_topic_media_dag() -> None:
    async def exercise() -> None:
        store = InMemoryStore()
        project = await store.create_project(_project())
        task = _script_task(project.id, _script())
        await store.create_task(task)
        orchestrator = TopicProductionOrchestrator(store, object())

        assert await orchestrator.on_task_finished(task.id) is None
        assert await store.list_tasks(project_id=project.id, limit=20) == [task]

    asyncio.run(exercise())


def test_topic_dag_reconciles_after_orchestrator_rebuild(tmp_path) -> None:
    async def exercise() -> None:
        store = InMemoryStore()
        queue = RecordingQueue()
        project = await store.create_project(_project())
        storage = LocalFileArtifactStorage(tmp_path / "artifacts")
        media = TopicMediaTaskService(
            store,
            queue,
            MockTTSProvider(),
            MockSentenceSubtitleAlignmentProvider(),
            LocalFixtureVideoGenerationProvider(width=160, height=284, fps=10),
            storage,
        )
        run_id = uuid4()
        plan = TopicProductionCreateRequest(include_subtitles=False).model_dump(mode="json")
        script_task = _script_task(project.id, _script())
        script_task.input_data.update(
            {
                "topic_pipeline": True,
                TOPIC_RUN_ID: str(run_id),
                "topic_run_plan": plan,
                TOPIC_RUN_STATUS: "active",
                TOPIC_RUN_CONTROL_REVISION: 0,
            }
        )
        await store.create_task(script_task)

        first_orchestrator = TopicProductionOrchestrator(store, media)
        first = await first_orchestrator.tick_all()
        assert first and first[0].status == "active"
        tasks = await store.list_tasks(project_id=project.id, limit=20)
        audio_task = next(
            task for task in tasks if task.kind == GenerationTaskKind.AUDIO_NARRATION
        )
        assert audio_task.status == TaskStatus.QUEUED

        # Simulate a process restart after the Worker finished audio but before
        # the old orchestrator delivered its completion callback.
        await media.run_task(audio_task.id)
        saved_audio = await store.get_task(audio_task.id)
        assert saved_audio is not None and saved_audio.status == TaskStatus.SUCCEEDED

        restarted_orchestrator = TopicProductionOrchestrator(store, media)

        class EmptyProductionOrchestrator:
            async def tick_all(self):
                return []

        reconciled = await _reconcile_runs_after_restart(
            EmptyProductionOrchestrator(),
            restarted_orchestrator,
            True,
        )
        assert reconciled == (0, 1)
        recovered_tasks = await store.list_tasks(project_id=project.id, limit=20)
        video_tasks = [
            task for task in recovered_tasks if task.kind == GenerationTaskKind.VIDEO_CLIP
        ]
        assert len(video_tasks) == 1
        assert video_tasks[0].status == TaskStatus.QUEUED
        assert len(
            [task for task in recovered_tasks if task.kind == GenerationTaskKind.AUDIO_NARRATION]
        ) == 1

    asyncio.run(exercise())


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg and ffprobe are required for the local topic fixture closed loop",
)
def test_topic_fixture_media_dag_reaches_assembly(tmp_path) -> None:
    async def exercise() -> None:
        store = InMemoryStore()
        queue = RecordingQueue()
        project = await store.create_project(_project())
        storage = LocalFileArtifactStorage(tmp_path / "artifacts")
        media = TopicMediaTaskService(
            store,
            queue,
            MockTTSProvider(),
            MockSentenceSubtitleAlignmentProvider(),
            LocalFixtureVideoGenerationProvider(width=160, height=284, fps=10),
            storage,
        )

        async def creator(project_id, idempotency_key, task_input_data):
            return await _create_script_task(
                store,
                queue,
                project_id,
                idempotency_key,
                task_input_data,
            )

        orchestrator = TopicProductionOrchestrator(
            store,
            media,
            script_task_creator=creator,
        )
        started = await orchestrator.start(
            project.id,
            TopicProductionCreateRequest(include_subtitles=False),
            idempotency_key="fixture-1",
        )
        root = await store.get_task(started.task_ids[0])
        assert root is not None
        root.status = TaskStatus.SUCCEEDED
        root.current_stage = None
        root.progress = 100
        root.artifacts = _script_task(project.id, _script()).artifacts
        root.stages[0].status = TaskStatus.SUCCEEDED
        root.stages[0].progress = 100
        await store.update_task(root)
        await orchestrator.on_task_finished(root.id)

        processed: set[UUID] = {root.id}
        while True:
            tasks = await store.list_tasks(project_id=project.id, limit=100)
            pending = [
                task
                for task in tasks
                if task.id not in processed and task.status == TaskStatus.QUEUED
            ]
            if not pending:
                break
            task = pending[-1]
            processed.add(task.id)
            await media.run_task(task.id)
            completed = await store.get_task(task.id)
            assert completed is not None
            assert completed.status == TaskStatus.SUCCEEDED, completed.error
            await orchestrator.on_task_finished(task.id)

        final_tasks = await store.list_tasks(project_id=project.id, limit=100)
        assembly = next(
            task for task in final_tasks if task.kind == GenerationTaskKind.VIDEO_ASSEMBLY
        )
        assert assembly.status == TaskStatus.SUCCEEDED
        assert assembly.artifacts[0].type == "rendered_video"
        assert assembly.artifacts[0].metadata["input_clip_count"] == 1

    asyncio.run(exercise())


def test_mock_topic_video_fails_with_explicit_non_storable_error(tmp_path) -> None:
    async def exercise() -> None:
        store = InMemoryStore()
        queue = RecordingQueue()
        project = await store.create_project(_project())
        media = TopicMediaTaskService(
            store,
            queue,
            MockTTSProvider(),
            MockSentenceSubtitleAlignmentProvider(),
            MockVideoGenerationProvider(),
            LocalFileArtifactStorage(tmp_path / "artifacts"),
        )
        task, reused = await media.create_video_task(
            project.id,
            uuid4(),
            uuid4(),
            1,
            15,
            "a flat illustration of camping gear",
            provider_profile_id=None,
            visual_quality_profile_id=None,
            idempotency_key="mock-video-1",
        )
        await media.run_task(task.id)
        failed = await store.get_task(task.id)

        assert reused is False
        assert failed is not None
        assert failed.status == TaskStatus.FAILED
        assert failed.error is not None
        assert failed.error.code == "VIDEO_PROVIDER_OUTPUT_NOT_STORABLE"

    asyncio.run(exercise())


def test_topic_failure_marker_survives_retry_and_stale_worker_update() -> None:
    async def exercise() -> None:
        store = InMemoryStore()
        queue = RecordingQueue()
        project = await store.create_project(_project())
        run_id = uuid4()
        script_task = _script_task(project.id, _script())
        script_task.input_data.update(
            {
                TOPIC_RUN_ID: str(run_id),
                TOPIC_RUN_STATUS: "active",
                TOPIC_RUN_CONTROL_REVISION: 0,
            }
        )
        video_task = GenerationTaskRecord(
            project_id=project.id,
            kind=GenerationTaskKind.VIDEO_CLIP,
            input_data={
                "topic_pipeline": True,
                TOPIC_RUN_ID: str(run_id),
                TOPIC_RUN_STATUS: "active",
                TOPIC_RUN_CONTROL_REVISION: 0,
                "generation_attempt": 1,
                "scene_index": 1,
            },
            status=TaskStatus.FAILED,
            current_stage=StageName.VIDEO_CLIP,
            error=TaskError(
                code="VIDEO_PROVIDER_TIMEOUT",
                message="temporary",
            ),
            stages=[StageRun(stage=StageName.VIDEO_CLIP, status=TaskStatus.FAILED)],
        )
        video_task.input_data["auto_retry_pending"] = True
        await store.create_task(script_task)
        await store.create_task(video_task)
        orchestrator = TopicProductionOrchestrator(store, object())

        blocked = await orchestrator.on_task_failed(video_task.id)
        assert blocked is not None
        assert blocked.status == "blocked"
        assert blocked.error_code == "VIDEO_PROVIDER_TIMEOUT"

        stale = await store.get_task(video_task.id)
        assert stale is not None
        stale.input_data.update(
            {
                TOPIC_RUN_STATUS: "active",
                TOPIC_RUN_CONTROL_REVISION: 0,
            }
        )
        await store.update_task(stale)
        merged = await store.get_task(video_task.id)
        assert merged is not None
        assert merged.input_data[TOPIC_RUN_STATUS] == "blocked"
        assert merged.input_data[TOPIC_RUN_CONTROL_REVISION] == 1
        assert merged.input_data[TOPIC_RUN_ERROR_CODE] == "VIDEO_PROVIDER_TIMEOUT"
        assert merged.input_data[TOPIC_RUN_ERROR_MESSAGE] == "temporary"

        task_service = TaskService(store, None, queue)  # type: ignore[arg-type]
        retried = await task_service.retry_task(video_task.id)
        assert retried.status == TaskStatus.QUEUED
        assert retried.input_data[TOPIC_RUN_STATUS] == "active"
        assert TOPIC_RUN_ERROR_CODE not in retried.input_data
        assert TOPIC_RUN_ERROR_MESSAGE not in retried.input_data
        assert queue.enqueued == [video_task.id]

    asyncio.run(exercise())
