"""Asynchronous lip-sync tasks over existing video and audio Artifacts."""

from __future__ import annotations

import base64
import binascii
from uuid import UUID, uuid4

from app.domain.models import (
    ArtifactSummary,
    GenerationTaskKind,
    GenerationTaskRecord,
    LipSyncCreateRequest,
    LipSyncGenerationRequest,
    LipSyncGenerationResult,
    StageName,
    StageRun,
    TaskError,
    TaskStatus,
    utc_now,
)
from app.media.video_validation import FFprobeVideoValidator, VideoArtifactValidator
from app.providers.lip_sync import LipSyncProvider, LipSyncProviderError
from app.queue import TaskQueue
from app.repositories.protocol import NovelStore
from app.services.novel_service import EpisodeNotFoundError
from app.storage.protocol import ArtifactStorage, StorageError, StoredArtifact


class LipSyncInputError(Exception):
    """Raised when a lip-sync source Artifact is not usable."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class LipSyncTaskService:
    def __init__(
        self,
        store: NovelStore,
        task_queue: TaskQueue,
        provider: LipSyncProvider,
        artifact_storage: ArtifactStorage,
        video_validator: VideoArtifactValidator | None = None,
    ) -> None:
        self._store = store
        self._task_queue = task_queue
        self._provider = provider
        self._artifact_storage = artifact_storage
        self._video_validator = video_validator or FFprobeVideoValidator()

    async def create_task(
        self,
        episode_id: UUID,
        request: LipSyncCreateRequest,
        idempotency_key: str | None = None,
    ) -> tuple[GenerationTaskRecord, bool]:
        episode = await self._store.get_episode(episode_id)
        if episode is None:
            raise EpisodeNotFoundError
        await self._validate_sources(episode.project_id, request)
        task = GenerationTaskRecord(
            id=uuid4(),
            project_id=episode.project_id,
            kind=GenerationTaskKind.LIP_SYNC,
            input_data={
                "episode_id": str(episode_id),
                "video_artifact_id": str(request.video_artifact_id),
                "audio_artifact_id": str(request.audio_artifact_id),
                "face_region": request.face_region,
                "face_padding": request.face_padding,
            },
            status=TaskStatus.QUEUED,
            current_stage=StageName.LIP_SYNC,
            stages=[StageRun(stage=StageName.LIP_SYNC, status=TaskStatus.QUEUED)],
            updated_at=utc_now(),
        )
        task_key = (
            f"{GenerationTaskKind.LIP_SYNC.value}:{episode_id}:{idempotency_key}"
            if idempotency_key
            else None
        )
        stored, reused = await self._store.create_task(task, task_key)
        if reused:
            return stored, True
        await self._task_queue.enqueue(stored.id)
        return stored, False

    async def run_task(self, task_id: UUID) -> None:
        task = await self._get_task(task_id)
        stage = StageName.LIP_SYNC
        await self._mark_running(task, stage)
        try:
            input_data = task.input_data
            video_artifact_id = UUID(str(input_data["video_artifact_id"]))
            audio_artifact_id = UUID(str(input_data["audio_artifact_id"]))
            video_artifact = await self._get_artifact(video_artifact_id, "video")
            audio_artifact = await self._get_artifact(audio_artifact_id, "audio")
            video_key, video_type = self._storage_metadata(video_artifact, video_artifact_id, "video")
            audio_key, audio_type = self._storage_metadata(audio_artifact, audio_artifact_id, "audio")
            video_bytes = await self._artifact_storage.get_bytes(video_key)
            audio_bytes = await self._artifact_storage.get_bytes(audio_key)
            result = await self._provider.generate_lip_sync(
                LipSyncGenerationRequest(
                    video_bytes=video_bytes,
                    video_mime_type=video_type,
                    audio_bytes=audio_bytes,
                    audio_mime_type=audio_type,
                    face_region=str(input_data.get("face_region", "auto")),
                    face_padding=int(input_data.get("face_padding", 0)),
                )
            )
            content = self._decode_video_result(result)
            probe = await self._video_validator.validate_bytes(content, result.mime_type)
            stored_artifact = await self._artifact_storage.put_bytes(
                f"lip-sync/{input_data['episode_id']}/{task.id}.mp4",
                content,
                result.mime_type,
            )
            result.duration_seconds = max(1, round(probe.duration_seconds))
            result.duration_ms = round(probe.duration_seconds * 1000)
            finished_at = utc_now()
            saved = await self._get_task(task_id)
            saved.status = TaskStatus.SUCCEEDED
            saved.current_stage = None
            saved.progress = 100
            saved.updated_at = finished_at
            stage_run = self._stage_run(saved)
            stage_run.status = TaskStatus.SUCCEEDED
            stage_run.progress = 100
            stage_run.finished_at = finished_at
            output_uri = stored_artifact.uri
            saved.artifacts.append(
                ArtifactSummary(
                    type="lip_synced_video",
                    provider=result.provider,
                    metadata={
                        "model": result.model,
                        "episode_id": input_data["episode_id"],
                        "video_artifact_id": input_data["video_artifact_id"],
                        "audio_artifact_id": input_data["audio_artifact_id"],
                        "face_region": input_data.get("face_region", "auto"),
                        "face_padding": input_data.get("face_padding", 0),
                        "duration_ms": result.duration_ms,
                        "duration_seconds": probe.duration_seconds,
                        "ffprobe": probe.as_metadata(),
                        **result.metadata,
                        **self._stored_artifact_metadata(stored_artifact),
                        "output_uri": output_uri,
                    },
                    preview={
                        "episode_id": input_data["episode_id"],
                        "output_uri": output_uri,
                        "source_video_artifact_id": input_data["video_artifact_id"],
                    },
                )
            )
            await self._store.update_task(saved)
        except Exception as exc:
            failed_at = utc_now()
            error_code = self._error_code(exc)
            failed = await self._get_task(task_id)
            failed.status = TaskStatus.FAILED
            failed.current_stage = stage
            failed.updated_at = failed_at
            failed.error = TaskError(code=error_code, message=str(exc) or error_code)
            stage_run = self._stage_run(failed)
            stage_run.status = TaskStatus.FAILED
            stage_run.error_code = error_code
            stage_run.finished_at = failed_at
            await self._store.update_task(failed)

    async def _validate_sources(self, project_id: UUID, request: LipSyncCreateRequest) -> None:
        video = await self._get_artifact(request.video_artifact_id, "video")
        audio = await self._get_artifact(request.audio_artifact_id, "audio")
        if video.project_id != project_id or audio.project_id != project_id:
            raise LipSyncInputError(
                "LIP_SYNC_PROJECT_MISMATCH",
                "Lip-sync source Artifacts must belong to the episode project",
            )
        if video.type not in {"video_clip", "lip_synced_video"}:
            raise LipSyncInputError(
                "LIP_SYNC_VIDEO_ARTIFACT_INVALID",
                "Lip-sync input video must be a video_clip or lip_synced_video Artifact",
            )
        if audio.type != "audio_narration":
            raise LipSyncInputError(
                "LIP_SYNC_AUDIO_ARTIFACT_INVALID",
                "Lip-sync input audio must be an audio_narration Artifact",
            )

    async def _get_artifact(self, artifact_id: UUID, kind: str):
        artifact = await self._store.get_artifact(artifact_id)
        if artifact is None:
            raise LipSyncInputError(
                "LIP_SYNC_ARTIFACT_NOT_FOUND",
                f"{kind.capitalize()} Artifact {artifact_id} was not found",
            )
        return artifact

    async def _get_task(self, task_id: UUID) -> GenerationTaskRecord:
        task = await self._store.get_task(task_id)
        if task is None:
            raise RuntimeError(f"Task {task_id} was not found")
        return task

    @staticmethod
    def _decode_video_result(result: LipSyncGenerationResult) -> bytes:
        if not result.video_base64:
            raise LipSyncProviderError(
                "LIP_SYNC_INVALID_RESPONSE",
                "Lip-sync Provider returned no video content",
            )
        if not result.mime_type.startswith("video/"):
            raise LipSyncProviderError(
                "LIP_SYNC_INVALID_RESPONSE",
                "Lip-sync Provider returned a non-video MIME type",
            )
        try:
            content = base64.b64decode(result.video_base64, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise LipSyncProviderError(
                "LIP_SYNC_INVALID_RESPONSE",
                "Lip-sync Provider returned invalid base64 video content",
            ) from exc
        if not content:
            raise LipSyncProviderError(
                "LIP_SYNC_INVALID_RESPONSE",
                "Lip-sync Provider returned empty video content",
            )
        return content

    @staticmethod
    def _storage_metadata(artifact, artifact_id: UUID, kind: str) -> tuple[str, str]:
        storage_key = artifact.metadata.get("storage_key")
        content_type = artifact.metadata.get("content_type")
        if not isinstance(storage_key, str) or not storage_key:
            raise LipSyncInputError(
                "LIP_SYNC_ARTIFACT_MISSING",
                f"{kind.capitalize()} Artifact {artifact_id} has no storage_key",
            )
        if not isinstance(content_type, str) or not content_type.startswith(f"{kind}/"):
            raise LipSyncInputError(
                "LIP_SYNC_ARTIFACT_INVALID",
                f"{kind.capitalize()} Artifact {artifact_id} has an invalid content type",
            )
        return storage_key, content_type.split(";", 1)[0].strip().lower()

    async def _mark_running(self, task: GenerationTaskRecord, stage: StageName) -> None:
        now = utc_now()
        task.status = TaskStatus.RUNNING
        task.current_stage = stage
        task.progress = 10
        task.updated_at = now
        run = self._stage_run(task)
        run.status = TaskStatus.RUNNING
        run.progress = 10
        run.started_at = now
        await self._store.update_task(task)

    @staticmethod
    def _stage_run(task: GenerationTaskRecord) -> StageRun:
        for stage_run in task.stages:
            if stage_run.stage == StageName.LIP_SYNC:
                return stage_run
        stage_run = StageRun(stage=StageName.LIP_SYNC, status=TaskStatus.CREATED)
        task.stages.append(stage_run)
        return stage_run

    @staticmethod
    def _stored_artifact_metadata(stored: StoredArtifact) -> dict[str, object]:
        return {
            "storage_key": stored.storage_key,
            "content_type": stored.content_type,
            "size_bytes": stored.size_bytes,
            "sha256": stored.sha256,
        }

    @staticmethod
    def _error_code(exc: Exception) -> str:
        if isinstance(exc, (LipSyncInputError, LipSyncProviderError, StorageError)):
            return exc.code
        return "LIP_SYNC_FAILED"
