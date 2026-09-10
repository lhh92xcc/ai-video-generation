"""Project and generation-task application services."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

from app.domain.models import (
    ArtifactSummary,
    GenerationTaskKind,
    GenerationTaskRecord,
    ProjectCreateRequest,
    ProjectRecord,
    ProjectStatus,
    ScriptGenerationRequest,
    StageName,
    StageRun,
    TaskError,
    TaskStatus,
    utc_now,
)
from app.domain.production_run import AUTO_RUN_ID, AUTO_RUN_STATUS, run_status_from_tasks
from app.domain.topic_run import (
    TOPIC_PIPELINE,
    TOPIC_RUN_ID,
    TOPIC_RUN_ERROR_CODE,
    TOPIC_RUN_ERROR_MESSAGE,
    TOPIC_RUN_STATUS,
    topic_run_status_from_tasks,
)
from app.providers.errors import TextProviderError
from app.providers.protocol import TextProvider
from app.queue import TaskQueue
from app.repositories.protocol import ProjectTaskStore


class ProjectNotFoundError(Exception):
    """Raised when a project ID does not exist."""


class TaskNotFoundError(Exception):
    """Raised when a task ID does not exist."""


class TaskNotRetryableError(Exception):
    """Raised when a task is not currently in a retryable state."""


class TaskService:
    def __init__(
        self,
        store: ProjectTaskStore,
        text_provider: TextProvider,
        task_queue: TaskQueue,
        novel_task_runner: Callable[[UUID], Awaitable[None]] | None = None,
        reference_image_task_runner: Callable[[UUID], Awaitable[None]] | None = None,
        video_clip_task_runner: Callable[[UUID], Awaitable[None]] | None = None,
        video_assembly_task_runner: Callable[[UUID], Awaitable[None]] | None = None,
        tts_task_runner: Callable[[UUID], Awaitable[None]] | None = None,
        bgm_task_runner: Callable[[UUID], Awaitable[None]] | None = None,
        subtitle_task_runner: Callable[[UUID], Awaitable[None]] | None = None,
        lip_sync_task_runner: Callable[[UUID], Awaitable[None]] | None = None,
        topic_task_runner: Callable[[UUID], Awaitable[None]] | None = None,
    ) -> None:
        self._store = store
        self._text_provider = text_provider
        self._task_queue = task_queue
        self._novel_task_runner = novel_task_runner
        self._reference_image_task_runner = reference_image_task_runner
        self._video_clip_task_runner = video_clip_task_runner
        self._video_assembly_task_runner = video_assembly_task_runner
        self._tts_task_runner = tts_task_runner
        self._bgm_task_runner = bgm_task_runner
        self._subtitle_task_runner = subtitle_task_runner
        self._lip_sync_task_runner = lip_sync_task_runner
        self._topic_task_runner = topic_task_runner

    async def create_project(self, request: ProjectCreateRequest) -> ProjectRecord:
        project = ProjectRecord(
            title=request.title,
            topic=request.topic,
            language=request.language,
            target_duration_seconds=request.target_duration_seconds,
            aspect_ratio=request.aspect_ratio,
            tone=request.tone,
        )
        return await self._store.create_project(project)

    async def list_projects(self) -> list[ProjectRecord]:
        return await self._store.list_projects()

    async def get_project(self, project_id: UUID) -> ProjectRecord:
        project = await self._store.get_project(project_id)
        if not project:
            raise ProjectNotFoundError
        return project

    async def create_generation(
        self,
        project_id: UUID,
        idempotency_key: str | None = None,
        task_input_data: dict[str, Any] | None = None,
    ) -> tuple[GenerationTaskRecord, bool]:
        project = await self.get_project(project_id)
        task = GenerationTaskRecord(
            project_id=project.id,
            kind=GenerationTaskKind.INFO_SCRIPT,
            input_data=dict(task_input_data or {}),
        )
        task.stages = [
            StageRun(
                stage=StageName.SCRIPT,
                status=TaskStatus.QUEUED,
                attempt=1,
                progress=0,
            )
        ]
        task.status = TaskStatus.QUEUED
        task.updated_at = utc_now()

        stored_task, reused = await self._store.create_task(task, idempotency_key)
        if reused:
            return stored_task, True

        project.status = ProjectStatus.GENERATING
        project.updated_at = utc_now()
        await self._store.update_project(project)

        await self._task_queue.enqueue(stored_task.id)
        return stored_task, False

    async def get_task(self, task_id: UUID) -> GenerationTaskRecord:
        task = await self._store.get_task(task_id)
        if not task:
            raise TaskNotFoundError
        return task

    async def list_tasks(
        self,
        project_id: UUID | None = None,
        kind: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[GenerationTaskRecord]:
        return await self._store.list_tasks(project_id, kind, status, limit)

    async def run_task(self, task_id: UUID) -> None:
        task = await self.get_task(task_id)
        run_status = await self._run_control_status(task)
        if task.status == TaskStatus.CANCELED:
            return
        if run_status == "paused":
            # A paused Run keeps queued tasks durable so resume can enqueue
            # them again; it must not start a Provider call in the meantime.
            return
        if run_status == "canceled":
            if task.status in {TaskStatus.CREATED, TaskStatus.QUEUED}:
                self._mark_canceled_task(task)
                await self._store.update_task(task)
            return
        if (
            task.kind != GenerationTaskKind.INFO_SCRIPT
            and task.input_data.get(TOPIC_PIPELINE) is True
        ):
            if self._topic_task_runner is None:
                raise RuntimeError("Topic media task runner has not been configured")
            await self._topic_task_runner(task_id)
            return
        if task.kind == GenerationTaskKind.ASSET_REFERENCE_IMAGE:
            if self._reference_image_task_runner is None:
                raise RuntimeError("Reference image task runner has not been configured")
            await self._reference_image_task_runner(task_id)
            return
        if task.kind == GenerationTaskKind.VIDEO_CLIP:
            if self._video_clip_task_runner is None:
                raise RuntimeError("Video clip task runner has not been configured")
            await self._video_clip_task_runner(task_id)
            return
        if task.kind == GenerationTaskKind.VIDEO_ASSEMBLY:
            if self._video_assembly_task_runner is None:
                raise RuntimeError("Video assembly task runner has not been configured")
            await self._video_assembly_task_runner(task_id)
            return
        if task.kind == GenerationTaskKind.LIP_SYNC:
            if self._lip_sync_task_runner is None:
                raise RuntimeError("Lip-sync task runner has not been configured")
            await self._lip_sync_task_runner(task_id)
            return
        if task.kind == GenerationTaskKind.AUDIO_NARRATION:
            if self._tts_task_runner is None:
                raise RuntimeError("TTS task runner has not been configured")
            await self._tts_task_runner(task_id)
            return
        if task.kind == GenerationTaskKind.AUDIO_BGM:
            if self._bgm_task_runner is None:
                raise RuntimeError("BGM task runner has not been configured")
            await self._bgm_task_runner(task_id)
            return
        if task.kind in {
            GenerationTaskKind.SUBTITLE_SRT,
            GenerationTaskKind.SUBTITLE_ALIGN,
            GenerationTaskKind.SUBTITLE_ASR,
        }:
            if self._subtitle_task_runner is None:
                raise RuntimeError("Subtitle task runner has not been configured")
            await self._subtitle_task_runner(task_id)
            return
        if task.kind != GenerationTaskKind.INFO_SCRIPT:
            if self._novel_task_runner is None:
                raise RuntimeError("Novel task runner has not been configured")
            await self._novel_task_runner(task_id)
            return
        project = await self.get_project(task.project_id)
        now = utc_now()
        task.status = TaskStatus.RUNNING
        task.current_stage = StageName.SCRIPT
        task.progress = 10
        task.updated_at = now
        task.stages[0].status = TaskStatus.RUNNING
        task.stages[0].progress = 10
        task.stages[0].started_at = now
        await self._store.update_task(task)

        try:
            result = await self._text_provider.generate(
                ScriptGenerationRequest(
                    topic=project.topic,
                    language=project.language,
                    target_duration_seconds=project.target_duration_seconds,
                    aspect_ratio=project.aspect_ratio,
                    tone=project.tone,
                )
            )
            task = await self.get_task(task_id)
            finished_at = utc_now()
            task.status = TaskStatus.SUCCEEDED
            task.current_stage = None
            task.progress = 100
            task.updated_at = finished_at
            task.stages[0].status = TaskStatus.SUCCEEDED
            task.stages[0].progress = 100
            task.stages[0].finished_at = finished_at
            task.artifacts.append(
                ArtifactSummary(
                    type="script_json",
                    provider=result.provider,
                    metadata={
                        "model": result.model,
                        "duration_ms": result.duration_ms,
                        "stage": StageName.SCRIPT.value,
                    },
                    preview=result.content.model_dump(mode="json"),
                )
            )
            await self._store.update_task(task)

            project = await self.get_project(task.project_id)
            project.status = ProjectStatus.READY
            project.updated_at = finished_at
            await self._store.update_project(project)
        except TextProviderError as exc:
            task = await self.get_task(task_id)
            failed_at = utc_now()
            task.status = TaskStatus.FAILED
            task.current_stage = StageName.SCRIPT
            task.updated_at = failed_at
            task.error = TaskError(code=exc.code, message=exc.message)
            task.stages[0].status = TaskStatus.FAILED
            task.stages[0].error_code = exc.code
            task.stages[0].finished_at = failed_at
            await self._store.update_task(task)

            project = await self.get_project(task.project_id)
            project.status = ProjectStatus.DRAFT
            project.updated_at = failed_at
            await self._store.update_project(project)

        except Exception as exc:
            task = await self.get_task(task_id)
            failed_at = utc_now()
            task.status = TaskStatus.FAILED
            task.current_stage = StageName.SCRIPT
            task.updated_at = failed_at
            task.error = TaskError(code="SCRIPT_GENERATION_FAILED", message=str(exc))
            task.stages[0].status = TaskStatus.FAILED
            task.stages[0].error_code = "SCRIPT_GENERATION_FAILED"
            task.stages[0].finished_at = failed_at
            await self._store.update_task(task)

            project = await self.get_project(task.project_id)
            project.status = ProjectStatus.DRAFT
            project.updated_at = failed_at
            await self._store.update_project(project)

    async def retry_task(self, task_id: UUID) -> GenerationTaskRecord:
        task = await self.get_task(task_id)
        if task.status != TaskStatus.FAILED:
            raise TaskNotRetryableError

        run_status = await self._run_control_status(task)
        if run_status == "canceled":
            raise TaskNotRetryableError

        stage = task.current_stage or (task.stages[-1].stage if task.stages else None)
        if stage is None:
            raise TaskNotRetryableError

        task.status = TaskStatus.QUEUED
        task.current_stage = stage
        task.progress = 0
        task.error = None
        task.updated_at = utc_now()
        # A manual retry takes ownership of a previously scheduled automatic
        # retry.  Clear its deadline so the Worker scheduler cannot enqueue a
        # second copy, while preserving the retry counter for observability.
        task.input_data["auto_retry_pending"] = False
        task.input_data.pop("next_retry_at", None)
        if task.input_data.get("auto_run_id") and run_status != "paused":
            task.input_data["auto_advance"] = True
            task.input_data["auto_run_status"] = "active"
        if task.input_data.get(TOPIC_PIPELINE) is True:
            # A manual retry or a Worker retry re-opens a previously blocked
            # topic Run.  The orchestrator will set the marker on every task
            # again when the next dependency wave is reconciled.
            task.input_data[TOPIC_RUN_STATUS] = "active"
            task.input_data.pop(TOPIC_RUN_ERROR_CODE, None)
            task.input_data.pop(TOPIC_RUN_ERROR_MESSAGE, None)
        stage_run = next((item for item in task.stages if item.stage == stage), None)
        if stage_run is None:
            stage_run = StageRun(stage=stage, status=TaskStatus.QUEUED)
            task.stages.append(stage_run)
        else:
            stage_run.status = TaskStatus.QUEUED
            stage_run.attempt += 1
            stage_run.progress = 0
            stage_run.error_code = None
            stage_run.started_at = None
            stage_run.finished_at = None
        if task.kind in {
            GenerationTaskKind.VIDEO_CLIP,
            GenerationTaskKind.ASSET_REFERENCE_IMAGE,
        }:
            task.input_data["generation_attempt"] = stage_run.attempt
        saved_task = await self._store.update_task(task)

        if task.kind == GenerationTaskKind.INFO_SCRIPT:
            project = await self.get_project(task.project_id)
            project.status = ProjectStatus.GENERATING
            project.updated_at = utc_now()
            await self._store.update_project(project)

        await self._task_queue.enqueue(saved_task.id)
        return saved_task

    @staticmethod
    def _mark_canceled_task(task: GenerationTaskRecord) -> None:
        now = utc_now()
        task.status = TaskStatus.CANCELED
        task.updated_at = now
        task.error = TaskError(
            code="PRODUCTION_RUN_CANCELED",
            message="The production Run was canceled before this task started.",
        )
        if task.current_stage is None and task.stages:
            task.current_stage = task.stages[-1].stage
        if task.current_stage is None:
            return
        stage_run = next(
            (item for item in task.stages if item.stage == task.current_stage),
            None,
        )
        if stage_run is None:
            stage_run = StageRun(stage=task.current_stage, status=TaskStatus.CANCELED)
            task.stages.append(stage_run)
        else:
            stage_run.status = TaskStatus.CANCELED
            stage_run.error_code = "PRODUCTION_RUN_CANCELED"
            stage_run.finished_at = now

    async def _run_control_status(self, task: GenerationTaskRecord) -> str | None:
        """Read the aggregate marker to close the pause/cancel dequeue race."""

        raw_run_id = task.input_data.get(AUTO_RUN_ID)
        if raw_run_id:
            run_tasks = [
                item
                for item in await self._store.list_tasks(project_id=task.project_id, limit=5000)
                if str(item.input_data.get(AUTO_RUN_ID)) == str(raw_run_id)
            ]
            if not run_tasks:
                return str(task.input_data.get(AUTO_RUN_STATUS, "active"))
            return run_status_from_tasks(run_tasks)

        raw_topic_run_id = task.input_data.get(TOPIC_RUN_ID)
        if not raw_topic_run_id:
            return None
        topic_tasks = [
            item
            for item in await self._store.list_tasks(project_id=task.project_id, limit=5000)
            if str(item.input_data.get(TOPIC_RUN_ID)) == str(raw_topic_run_id)
        ]
        if not topic_tasks:
            return str(task.input_data.get(TOPIC_RUN_STATUS, "active"))
        return topic_run_status_from_tasks(topic_tasks)

    async def mark_failed(
        self,
        task_id: UUID,
        code: str,
        message: str,
    ) -> GenerationTaskRecord:
        """Persist a failure when the Worker fails outside a task service.

        Provider task services normally catch and persist their own errors. A
        Worker timeout, process cancellation, or unexpected orchestration
        exception can happen one layer above them, so the Worker needs a
        single safe fallback that turns an orphaned ``running`` task into a
        retryable ``failed`` task.
        """

        task = await self.get_task(task_id)
        if task.status not in {TaskStatus.CREATED, TaskStatus.QUEUED, TaskStatus.RUNNING}:
            return task
        failed_at = utc_now()
        task.status = TaskStatus.FAILED
        task.current_stage = task.current_stage or (
            task.stages[-1].stage if task.stages else None
        )
        task.progress = min(task.progress, 99)
        task.updated_at = failed_at
        task.error = TaskError(code=code, message=message)
        if task.current_stage is not None:
            stage_run = next(
                (item for item in task.stages if item.stage == task.current_stage),
                None,
            )
            if stage_run is None:
                stage_run = StageRun(stage=task.current_stage, status=TaskStatus.FAILED)
                task.stages.append(stage_run)
            stage_run.status = TaskStatus.FAILED
            stage_run.error_code = code
            stage_run.finished_at = failed_at
        return await self._store.update_task(task)
