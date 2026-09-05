"""Asynchronous BGM generation and ``audio_bgm`` Artifact registration."""

from __future__ import annotations

import base64
import binascii
from uuid import UUID, uuid4

from app.domain.models import (
    ArtifactSummary,
    AudioBGMCreateRequest,
    BGMGenerationRequest,
    BGMGenerationResult,
    GenerationTaskKind,
    GenerationTaskRecord,
    StageName,
    StageRun,
    TaskError,
    TaskStatus,
    utc_now,
)
from app.media.audio_normalization import AudioNormalizer
from app.media.audio_validation import AudioArtifactValidator, FFprobeAudioValidator
from app.providers.bgm import BGMProvider
from app.providers.errors_audio import (
    AudioArtifactValidationError,
    AudioNormalizationError,
    BGMProviderError,
)
from app.queue import TaskQueue
from app.repositories.protocol import NovelStore
from app.services.novel_service import EpisodeNotFoundError
from app.storage.protocol import ArtifactStorage, StorageError, StoredArtifact


class BGMTaskService:
    def __init__(
        self,
        store: NovelStore,
        task_queue: TaskQueue,
        provider: BGMProvider,
        artifact_storage: ArtifactStorage,
        audio_validator: AudioArtifactValidator | None = None,
        audio_normalizer: AudioNormalizer | None = None,
    ) -> None:
        self._store = store
        self._task_queue = task_queue
        self._provider = provider
        self._artifact_storage = artifact_storage
        self._audio_validator = audio_validator or FFprobeAudioValidator()
        self._audio_normalizer = audio_normalizer

    async def create_task(
        self,
        episode_id: UUID,
        request: AudioBGMCreateRequest,
        idempotency_key: str | None = None,
    ) -> tuple[GenerationTaskRecord, bool]:
        episode = await self._store.get_episode(episode_id)
        if episode is None:
            raise EpisodeNotFoundError

        task = GenerationTaskRecord(
            id=uuid4(),
            project_id=episode.project_id,
            kind=GenerationTaskKind.AUDIO_BGM,
            input_data={
                "episode_id": str(episode_id),
                "source_path": request.source_path,
                "label": request.label,
                "rights_status": request.rights_status.value,
                "rights_holder": request.rights_holder,
                "rights_reference": request.rights_reference,
            },
            status=TaskStatus.QUEUED,
            current_stage=StageName.AUDIO,
            stages=[StageRun(stage=StageName.AUDIO, status=TaskStatus.QUEUED)],
            updated_at=utc_now(),
        )
        task_key = (
            f"{GenerationTaskKind.AUDIO_BGM.value}:{episode_id}:{idempotency_key}"
            if idempotency_key
            else None
        )
        stored_task, reused = await self._store.create_task(task, task_key)
        if reused:
            return stored_task, True

        await self._task_queue.enqueue(stored_task.id)
        return stored_task, False

    async def run_task(self, task_id: UUID) -> None:
        task = await self._get_task(task_id)
        stage = StageName.AUDIO
        await self._mark_running(task, stage)

        try:
            input_data = task.input_data
            result = await self._provider.generate_bgm(
                BGMGenerationRequest(
                    source_path=(str(input_data["source_path"]) if input_data.get("source_path") else None),
                    label=str(input_data.get("label", "licensed-local-bgm")),
                    rights_status=str(input_data.get("rights_status", "unknown")),
                    rights_holder=(
                        str(input_data["rights_holder"])
                        if input_data.get("rights_holder")
                        else None
                    ),
                    rights_reference=(
                        str(input_data["rights_reference"])
                        if input_data.get("rights_reference")
                        else None
                    ),
                )
            )
            stored_artifact = await self._store_generated_audio(task, result)
            finished_at = utc_now()
            saved_task = await self._get_task(task_id)
            saved_task.status = TaskStatus.SUCCEEDED
            saved_task.current_stage = None
            saved_task.progress = 100
            saved_task.updated_at = finished_at
            stage_run = self._stage_run(saved_task)
            stage_run.status = TaskStatus.SUCCEEDED
            stage_run.progress = 100
            stage_run.finished_at = finished_at
            output_uri = stored_artifact.uri
            saved_task.artifacts.append(
                ArtifactSummary(
                    type="audio_bgm",
                    provider=result.provider,
                    metadata={
                        "model": result.model,
                        "duration_ms": result.duration_ms,
                        "duration_seconds": result.duration_seconds,
                        "episode_id": input_data["episode_id"],
                        "label": input_data.get("label", "licensed-local-bgm"),
                        **result.metadata,
                        **self._stored_artifact_metadata(stored_artifact),
                        "output_uri": output_uri,
                    },
                    preview={
                        "episode_id": input_data["episode_id"],
                        "output_uri": output_uri,
                        "source_type": result.metadata.get("source_type", "unknown"),
                    },
                )
            )
            await self._store.update_task(saved_task)
        except Exception as exc:
            failed_at = utc_now()
            error_code = self._error_code(exc)
            failed_task = await self._get_task(task_id)
            failed_task.status = TaskStatus.FAILED
            failed_task.current_stage = stage
            failed_task.updated_at = failed_at
            failed_task.error = TaskError(code=error_code, message=str(exc) or error_code)
            stage_run = self._stage_run(failed_task)
            stage_run.status = TaskStatus.FAILED
            stage_run.error_code = error_code
            stage_run.finished_at = failed_at
            await self._store.update_task(failed_task)

    async def _get_task(self, task_id: UUID) -> GenerationTaskRecord:
        task = await self._store.get_task(task_id)  # type: ignore[attr-defined]
        if task is None:
            raise RuntimeError(f"Task {task_id} was not found")
        return task

    async def _mark_running(self, task: GenerationTaskRecord, stage: StageName) -> None:
        now = utc_now()
        task.status = TaskStatus.RUNNING
        task.current_stage = stage
        task.progress = 10
        task.updated_at = now
        stage_run = self._stage_run(task)
        stage_run.status = TaskStatus.RUNNING
        stage_run.progress = 10
        stage_run.started_at = now
        await self._store.update_task(task)  # type: ignore[attr-defined]

    @staticmethod
    def _stage_run(task: GenerationTaskRecord) -> StageRun:
        for stage_run in task.stages:
            if stage_run.stage == StageName.AUDIO:
                return stage_run
        stage_run = StageRun(stage=StageName.AUDIO, status=TaskStatus.CREATED)
        task.stages.append(stage_run)
        return stage_run

    async def _store_generated_audio(
        self,
        task: GenerationTaskRecord,
        result: BGMGenerationResult,
    ) -> StoredArtifact:
        if not result.mime_type.startswith("audio/"):
            raise BGMProviderError(
                "BGM_PROVIDER_INVALID_RESPONSE",
                "BGM provider returned a non-audio MIME type",
            )
        try:
            content = base64.b64decode(result.audio_base64, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise BGMProviderError(
                "BGM_PROVIDER_INVALID_RESPONSE",
                "BGM provider returned invalid base64 audio content",
            ) from exc
        if not content:
            raise BGMProviderError(
                "BGM_PROVIDER_INVALID_RESPONSE",
                "BGM provider returned empty audio content",
            )

        normalization_metadata: dict[str, object] = {"enabled": False}
        if self._audio_normalizer is not None:
            normalized = await self._audio_normalizer.normalize(content, result.mime_type)
            content = normalized.content
            result.mime_type = normalized.content_type
            normalization_metadata = normalized.metadata

        probe_result = await self._audio_validator.validate_bytes(content, result.mime_type)
        result.duration_seconds = probe_result.duration_seconds
        result.metadata = {
            **result.metadata,
            "audio_duration_ms": round(probe_result.duration_seconds * 1000),
            "ffprobe": probe_result.as_metadata(),
            "normalization": normalization_metadata,
        }
        input_data = task.input_data
        extension = self._extension_for_mime(result.mime_type)
        storage_key = f"audio-bgm/{input_data['episode_id']}/{task.id}{extension}"
        return await self._artifact_storage.put_bytes(storage_key, content, result.mime_type)

    @staticmethod
    def _stored_artifact_metadata(stored_artifact: StoredArtifact) -> dict[str, object]:
        return {
            "storage_key": stored_artifact.storage_key,
            "content_type": stored_artifact.content_type,
            "size_bytes": stored_artifact.size_bytes,
            "sha256": stored_artifact.sha256,
        }

    @staticmethod
    def _extension_for_mime(mime_type: str) -> str:
        return {
            "audio/wav": ".wav",
            "audio/x-wav": ".wav",
            "audio/mpeg": ".mp3",
            "audio/mp3": ".mp3",
            "audio/ogg": ".ogg",
            "audio/mp4": ".m4a",
            "audio/aac": ".aac",
            "audio/webm": ".webm",
        }.get(mime_type.split(";", 1)[0].strip().lower(), ".audio")

    @staticmethod
    def _error_code(exc: Exception) -> str:
        if isinstance(exc, (BGMProviderError, AudioArtifactValidationError, AudioNormalizationError)):
            return exc.code
        if isinstance(exc, StorageError):
            return exc.code
        return "AUDIO_BGM_GENERATION_FAILED"
