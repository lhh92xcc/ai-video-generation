"""Asynchronous task orchestration for the novel content pipeline."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from app.domain.models import (
    ArtifactSummary,
    EpisodeRecord,
    EpisodeScriptRecord,
    GenerationTaskKind,
    GenerationTaskRecord,
    ShotListRecord,
    StageName,
    StageRun,
    StoryBibleRecord,
    TaskError,
    TaskStatus,
    utc_now,
)
from app.queue import TaskQueue
from app.providers.errors import TextProviderError
from app.repositories.protocol import NovelStore, ProjectTaskStore
from app.services.novel_service import (
    EpisodeNotFoundError,
    EpisodeScriptNotFoundError,
    NovelProjectNotFoundError,
    NovelService,
    NovelSourceNotFoundError,
    ShotListNotFoundError,
    StoryBibleNotFoundError,
)


class NovelTaskStore(ProjectTaskStore, NovelStore, Protocol):
    """The combined repository surface required by novel task orchestration."""


class NovelGenerationTaskService:
    def __init__(
        self,
        store: NovelTaskStore,
        novel_service: NovelService,
        task_queue: TaskQueue,
    ) -> None:
        self._store = store
        self._novel_service = novel_service
        self._task_queue = task_queue

    async def create_story_bible_task(
        self,
        project_id: UUID,
        idempotency_key: str | None = None,
    ) -> tuple[GenerationTaskRecord, bool]:
        project = await self._novel_service.get_project(project_id)
        if project.source_id is None:
            raise NovelSourceNotFoundError
        return await self._create_task(
            project_id=project.id,
            kind=GenerationTaskKind.NOVEL_STORY_BIBLE,
            stage=StageName.STORY_BIBLE,
            input_data={},
            idempotency_key=idempotency_key,
        )

    async def create_episode_plan_task(
        self,
        project_id: UUID,
        target_episode_count: int | None = None,
        idempotency_key: str | None = None,
    ) -> tuple[GenerationTaskRecord, bool]:
        await self._novel_service.get_story_bible(project_id)
        input_data = (
            {"target_episode_count": target_episode_count}
            if target_episode_count is not None
            else {}
        )
        return await self._create_task(
            project_id=project_id,
            kind=GenerationTaskKind.NOVEL_EPISODE_PLAN,
            stage=StageName.EPISODE_PLAN,
            input_data=input_data,
            idempotency_key=idempotency_key,
        )

    async def create_episode_script_task(
        self,
        episode_id: UUID,
        idempotency_key: str | None = None,
        plan_key: str | None = None,
    ) -> tuple[GenerationTaskRecord, bool]:
        episode = await self._novel_service.get_episode(episode_id)
        await self._novel_service.get_story_bible(episode.project_id)
        input_data: dict[str, object] = {"episode_id": str(episode_id)}
        if plan_key:
            input_data["episode_task_plan_key"] = plan_key
        return await self._create_task(
            project_id=episode.project_id,
            kind=GenerationTaskKind.NOVEL_EPISODE_SCRIPT,
            stage=StageName.EPISODE_SCRIPT,
            input_data=input_data,
            idempotency_key=idempotency_key,
        )

    async def create_shot_list_task(
        self,
        episode_id: UUID,
        idempotency_key: str | None = None,
        plan_key: str | None = None,
    ) -> tuple[GenerationTaskRecord, bool]:
        episode = await self._novel_service.get_episode(episode_id)
        await self._novel_service.get_episode_script(episode_id)
        await self._novel_service.get_story_bible(episode.project_id)
        input_data: dict[str, object] = {"episode_id": str(episode_id)}
        if plan_key:
            input_data["episode_task_plan_key"] = plan_key
        return await self._create_task(
            project_id=episode.project_id,
            kind=GenerationTaskKind.NOVEL_SHOT_LIST,
            stage=StageName.SHOT_LIST,
            input_data=input_data,
            idempotency_key=idempotency_key,
        )

    async def run_task(self, task_id: UUID) -> None:
        task = await self._get_task(task_id)
        stage = self._stage_for_kind(task.kind)
        task = await self._mark_running(task, stage)

        try:
            result = await self._generate(task)
            task = await self._get_task(task_id)
            finished_at = utc_now()
            task.status = TaskStatus.SUCCEEDED
            task.current_stage = None
            task.progress = 100
            task.updated_at = finished_at
            stage_run = self._stage_run(task, stage)
            stage_run.status = TaskStatus.SUCCEEDED
            stage_run.progress = 100
            stage_run.finished_at = finished_at
            task.artifacts.append(self._artifact_for_result(stage, result))
            await self._store.update_task(task)
        except Exception as exc:
            task = await self._get_task(task_id)
            failed_at = utc_now()
            error_code = self._error_code(exc)
            task.status = TaskStatus.FAILED
            task.current_stage = stage
            task.updated_at = failed_at
            task.error = TaskError(code=error_code, message=str(exc) or error_code)
            stage_run = self._stage_run(task, stage)
            stage_run.status = TaskStatus.FAILED
            stage_run.error_code = error_code
            stage_run.finished_at = failed_at
            await self._store.update_task(task)

    async def _create_task(
        self,
        project_id: UUID,
        kind: GenerationTaskKind,
        stage: StageName,
        input_data: dict[str, object],
        idempotency_key: str | None,
    ) -> tuple[GenerationTaskRecord, bool]:
        task = GenerationTaskRecord(
            project_id=project_id,
            kind=kind,
            input_data=input_data,
            status=TaskStatus.QUEUED,
            current_stage=stage,
            progress=0,
            stages=[StageRun(stage=stage, status=TaskStatus.QUEUED)],
            updated_at=utc_now(),
        )
        scoped_key = f"{kind.value}:{idempotency_key}" if idempotency_key else None
        stored_task, reused = await self._store.create_task(task, scoped_key)
        if not reused:
            await self._task_queue.enqueue(stored_task.id)
        return stored_task, reused

    async def _get_task(self, task_id: UUID) -> GenerationTaskRecord:
        task = await self._store.get_task(task_id)
        if task is None:
            raise RuntimeError(f"Task {task_id} was not found")
        return task

    async def _mark_running(self, task: GenerationTaskRecord, stage: StageName) -> GenerationTaskRecord:
        now = utc_now()
        task.status = TaskStatus.RUNNING
        task.current_stage = stage
        task.progress = 10
        task.updated_at = now
        stage_run = self._stage_run(task, stage)
        stage_run.status = TaskStatus.RUNNING
        stage_run.progress = 10
        stage_run.started_at = now
        await self._store.update_task(task)
        return task

    async def _generate(
        self,
        task: GenerationTaskRecord,
    ) -> StoryBibleRecord | list[EpisodeRecord] | EpisodeScriptRecord | ShotListRecord:
        if task.kind == GenerationTaskKind.NOVEL_STORY_BIBLE:
            return await self._novel_service.generate_story_bible(task.project_id)
        if task.kind == GenerationTaskKind.NOVEL_EPISODE_PLAN:
            target_count = task.input_data.get("target_episode_count")
            return await self._novel_service.plan_episodes(
                task.project_id,
                int(target_count) if target_count is not None else None,
            )
        episode_id = UUID(str(task.input_data["episode_id"]))
        if task.kind == GenerationTaskKind.NOVEL_EPISODE_SCRIPT:
            return await self._novel_service.generate_episode_script(episode_id)
        if task.kind == GenerationTaskKind.NOVEL_SHOT_LIST:
            return await self._novel_service.generate_shot_list(episode_id)
        raise RuntimeError(f"Unsupported novel task kind: {task.kind}")

    @staticmethod
    def _stage_for_kind(kind: GenerationTaskKind) -> StageName:
        return {
            GenerationTaskKind.NOVEL_STORY_BIBLE: StageName.STORY_BIBLE,
            GenerationTaskKind.NOVEL_EPISODE_PLAN: StageName.EPISODE_PLAN,
            GenerationTaskKind.NOVEL_EPISODE_SCRIPT: StageName.EPISODE_SCRIPT,
            GenerationTaskKind.NOVEL_SHOT_LIST: StageName.SHOT_LIST,
        }[kind]

    @staticmethod
    def _stage_run(task: GenerationTaskRecord, stage: StageName) -> StageRun:
        for stage_run in task.stages:
            if stage_run.stage == stage:
                return stage_run
        stage_run = StageRun(stage=stage, status=TaskStatus.CREATED)
        task.stages.append(stage_run)
        return stage_run

    @staticmethod
    def _artifact_for_result(
        stage: StageName,
        result: StoryBibleRecord | list[EpisodeRecord] | EpisodeScriptRecord | ShotListRecord,
    ) -> ArtifactSummary:
        if stage == StageName.STORY_BIBLE:
            assert isinstance(result, StoryBibleRecord)
            return ArtifactSummary(
                type="story_bible_json",
                provider=result.provider,
                metadata={"model": result.model, "duration_ms": result.duration_ms},
                preview=result.model_dump(mode="json"),
            )
        if stage == StageName.EPISODE_PLAN:
            assert isinstance(result, list)
            provider = result[0].provider
            model = result[0].model
            return ArtifactSummary(
                type="episode_outline_json",
                provider=provider,
                metadata={
                    "model": model,
                    "episode_count": len(result),
                    "version": result[0].version,
                },
                preview={"items": [episode.model_dump(mode="json") for episode in result]},
            )
        if stage == StageName.EPISODE_SCRIPT:
            assert isinstance(result, EpisodeScriptRecord)
            return ArtifactSummary(
                type="episode_script_json",
                provider=result.provider,
                metadata={"model": result.model, "duration_ms": result.duration_ms},
                preview=result.model_dump(mode="json"),
            )
        assert isinstance(result, ShotListRecord)
        return ArtifactSummary(
            type="shot_list_json",
            provider=result.provider,
            metadata={
                "model": result.model,
                "duration_ms": result.duration_ms,
                "shot_count": len(result.shots),
            },
            preview=result.model_dump(mode="json"),
        )

    @staticmethod
    def _error_code(exc: Exception) -> str:
        if isinstance(exc, TextProviderError):
            return exc.code
        if isinstance(exc, NovelProjectNotFoundError):
            return "NOVEL_PROJECT_NOT_FOUND"
        if isinstance(exc, NovelSourceNotFoundError):
            return "NOVEL_SOURCE_REQUIRED"
        if isinstance(exc, StoryBibleNotFoundError):
            return "STORY_BIBLE_REQUIRED"
        if isinstance(exc, EpisodeNotFoundError):
            return "EPISODE_NOT_FOUND"
        if isinstance(exc, EpisodeScriptNotFoundError):
            return "EPISODE_SCRIPT_REQUIRED"
        if isinstance(exc, ShotListNotFoundError):
            return "SHOT_LIST_NOT_FOUND"
        return "NOVEL_GENERATION_FAILED"
