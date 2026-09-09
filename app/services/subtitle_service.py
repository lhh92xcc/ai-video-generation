"""Asynchronous subtitle timeline generation and alignment tasks."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID, uuid4

from app.domain.models import (
    ArtifactRecord,
    ArtifactSummary,
    GenerationTaskKind,
    GenerationTaskRecord,
    EpisodeRecord,
    StageName,
    StageRun,
    SubtitleASRCreateRequest,
    SubtitleASRGenerationRequest,
    SubtitleAlignmentCreateRequest,
    SubtitleCreateRequest,
    TaskError,
    TaskStatus,
    utc_now,
)
from app.providers.asr import SubtitleASRProvider
from app.providers.errors_asr import SubtitleASRInputError, SubtitleASRProviderError
from app.providers.errors_subtitle import SubtitleAlignmentProviderError
from app.providers.subtitle_alignment import SubtitleAlignmentProvider
from app.providers.profiles import ASRProviderProfileError, ASRProviderProfileRegistry
from app.media.subtitle_quality import evaluate_subtitle_cues
from app.rendering.subtitles import SubtitleCue, serialize_srt
from app.queue import TaskQueue
from app.services.novel_service import EpisodeNotFoundError
from app.storage.protocol import ArtifactStorage, StorageError, StoredArtifact


class SubtitleStore(Protocol):
    async def get_episode(self, episode_id: UUID) -> EpisodeRecord | None:
        ...

    async def get_task(self, task_id: UUID) -> GenerationTaskRecord | None:
        ...

    async def create_task(
        self,
        task: GenerationTaskRecord,
        idempotency_key: str | None = None,
    ) -> tuple[GenerationTaskRecord, bool]:
        ...

    async def update_task(self, task: GenerationTaskRecord) -> GenerationTaskRecord:
        ...

    async def get_artifact(self, artifact_id: UUID) -> ArtifactRecord | None:
        ...


class SubtitleTaskService:
    """Create SRT Artifacts from reviewed cues or an alignment Provider."""

    def __init__(
        self,
        store: SubtitleStore,
        task_queue: TaskQueue,
        artifact_storage: ArtifactStorage,
        alignment_provider: SubtitleAlignmentProvider | None = None,
        asr_provider: SubtitleASRProvider | None = None,
        asr_profile_registry: ASRProviderProfileRegistry | None = None,
    ) -> None:
        self._store = store
        self._task_queue = task_queue
        self._artifact_storage = artifact_storage
        self._alignment_provider = alignment_provider
        self._asr_provider = asr_provider
        self._asr_profile_registry = asr_profile_registry

    async def create_task(
        self,
        episode_id: UUID,
        request: SubtitleCreateRequest,
        idempotency_key: str | None = None,
    ) -> tuple[GenerationTaskRecord, bool]:
        episode = await self._store.get_episode(episode_id)
        if episode is None:
            raise EpisodeNotFoundError

        task = GenerationTaskRecord(
            id=uuid4(),
            project_id=episode.project_id,
            kind=GenerationTaskKind.SUBTITLE_SRT,
            input_data={
                "episode_id": str(episode_id),
                "language": request.language,
                "cues": [cue.model_dump(mode="json") for cue in request.cues],
            },
            status=TaskStatus.QUEUED,
            current_stage=StageName.SUBTITLE,
            stages=[StageRun(stage=StageName.SUBTITLE, status=TaskStatus.QUEUED)],
            updated_at=utc_now(),
        )
        task_key = (
            f"{GenerationTaskKind.SUBTITLE_SRT.value}:{episode_id}:{idempotency_key}"
            if idempotency_key
            else None
        )
        stored_task, reused = await self._store.create_task(task, task_key)
        if reused:
            return stored_task, True

        await self._task_queue.enqueue(stored_task.id)
        return stored_task, False

    async def create_asr_task(
        self,
        episode_id: UUID,
        request: SubtitleASRCreateRequest,
        idempotency_key: str | None = None,
    ) -> tuple[GenerationTaskRecord, bool]:
        episode = await self._store.get_episode(episode_id)
        if episode is None:
            raise EpisodeNotFoundError

        provider_profile_id: str | None = None
        if self._asr_profile_registry is not None:
            profile = self._asr_profile_registry.resolve(request.provider_profile_id)
            self._asr_profile_registry.ensure_configured(profile)
            provider_profile_id = profile.profile_id
        elif request.provider_profile_id is not None:
            raise ASRProviderProfileError(
                "PROVIDER_PROFILE_SELECTION_UNAVAILABLE",
                "ASR Provider profile selection is not enabled for this service",
            )

        task = GenerationTaskRecord(
            id=uuid4(),
            project_id=episode.project_id,
            kind=GenerationTaskKind.SUBTITLE_ASR,
            input_data={
                "episode_id": str(episode_id),
                "audio_artifact_id": str(request.audio_artifact_id),
                "language": request.language,
                "reference_text": request.reference_text,
                **(
                    {"provider_profile_id": provider_profile_id}
                    if provider_profile_id is not None
                    else {}
                ),
            },
            status=TaskStatus.QUEUED,
            current_stage=StageName.SUBTITLE,
            stages=[StageRun(stage=StageName.SUBTITLE, status=TaskStatus.QUEUED)],
            updated_at=utc_now(),
        )
        task_key = (
            f"{GenerationTaskKind.SUBTITLE_ASR.value}:{episode_id}:{idempotency_key}"
            if idempotency_key
            else None
        )
        stored_task, reused = await self._store.create_task(task, task_key)
        if reused:
            return stored_task, True

        await self._task_queue.enqueue(stored_task.id)
        return stored_task, False

    async def create_alignment_task(
        self,
        episode_id: UUID,
        request: SubtitleAlignmentCreateRequest,
        idempotency_key: str | None = None,
    ) -> tuple[GenerationTaskRecord, bool]:
        episode = await self._store.get_episode(episode_id)
        if episode is None:
            raise EpisodeNotFoundError

        task = GenerationTaskRecord(
            id=uuid4(),
            project_id=episode.project_id,
            kind=GenerationTaskKind.SUBTITLE_ALIGN,
            input_data={
                "episode_id": str(episode_id),
                "language": request.language,
                "text": request.text,
                "audio_duration_seconds": request.audio_duration_seconds,
            },
            status=TaskStatus.QUEUED,
            current_stage=StageName.SUBTITLE,
            stages=[StageRun(stage=StageName.SUBTITLE, status=TaskStatus.QUEUED)],
            updated_at=utc_now(),
        )
        task_key = (
            f"{GenerationTaskKind.SUBTITLE_ALIGN.value}:{episode_id}:{idempotency_key}"
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
        stage = StageName.SUBTITLE
        await self._mark_running(task, stage)

        try:
            input_data = task.input_data
            if task.kind == GenerationTaskKind.SUBTITLE_ALIGN:
                if self._alignment_provider is None:
                    raise SubtitleAlignmentProviderError(
                        "SUBTITLE_ALIGNMENT_PROVIDER_UNAVAILABLE",
                        "No subtitle alignment Provider has been configured",
                    )
                result = await self._alignment_provider.align(
                    SubtitleAlignmentCreateRequest(
                        text=str(input_data["text"]),
                        language=str(input_data["language"]),
                        audio_duration_seconds=float(input_data["audio_duration_seconds"]),
                    )
                )
                cues = tuple(self._to_rendering_cue(cue) for cue in result.cues)
                artifact_provider = result.provider
                artifact_metadata = {
                    **result.metadata,
                    "model": result.model,
                    "alignment_provider": result.provider,
                    "alignment_precision": result.precision,
                    "alignment_method": "provider",
                    "audio_duration_seconds": float(input_data["audio_duration_seconds"]),
                    "source_text_characters": len(str(input_data["text"])),
                }
                artifact_metadata["quality"] = evaluate_subtitle_cues(
                    cues,
                    audio_duration_seconds=float(input_data["audio_duration_seconds"]),
                    reference_text=str(input_data["text"]),
                    alignment_precision=result.precision,
                ).as_dict()
                preview_metadata = {
                    "alignment_precision": result.precision,
                    "alignment_provider": result.provider,
                }
            elif task.kind == GenerationTaskKind.SUBTITLE_ASR:
                asr_provider = self._asr_provider
                if self._asr_profile_registry is not None:
                    asr_provider = await self._asr_profile_registry.get_provider(
                        str(input_data.get("provider_profile_id"))
                        if input_data.get("provider_profile_id")
                        else None
                    )
                if asr_provider is None:
                    raise SubtitleASRProviderError(
                        "ASR_PROVIDER_NOT_CONFIGURED",
                        "No subtitle ASR Provider has been configured",
                    )
                audio_artifact, audio_bytes = await self._load_asr_audio(
                    task,
                    input_data,
                )
                result = await asr_provider.transcribe(
                    SubtitleASRGenerationRequest(
                        audio_bytes=audio_bytes,
                        mime_type=str(audio_artifact.metadata["content_type"]),
                        language=str(input_data["language"]),
                        audio_duration_seconds=float(audio_artifact.metadata["duration_seconds"]),
                        reference_text=(
                            str(input_data["reference_text"])
                            if input_data.get("reference_text")
                            else None
                        ),
                    )
                )
                cues = tuple(self._to_rendering_cue(cue) for cue in result.cues)
                artifact_provider = result.provider
                artifact_metadata = {
                    **result.metadata,
                    "model": result.model,
                    "alignment_provider": result.provider,
                    "alignment_precision": result.precision,
                    "alignment_method": "asr",
                    "audio_artifact_id": str(input_data["audio_artifact_id"]),
                    "audio_duration_seconds": float(audio_artifact.metadata["duration_seconds"]),
                    "reference_text_used": bool(input_data.get("reference_text")),
                }
                artifact_metadata["quality"] = evaluate_subtitle_cues(
                    cues,
                    audio_duration_seconds=float(audio_artifact.metadata["duration_seconds"]),
                    reference_text=(
                        str(input_data["reference_text"])
                        if input_data.get("reference_text")
                        else None
                    ),
                    alignment_precision=result.precision,
                ).as_dict()
                preview_metadata = {
                    "alignment_precision": result.precision,
                    "alignment_provider": result.provider,
                    "audio_artifact_id": str(input_data["audio_artifact_id"]),
                }
            else:
                cues = tuple(
                    SubtitleCue(
                        start_seconds=float(cue["start_seconds"]),
                        end_seconds=float(cue["end_seconds"]),
                        text=str(cue["text"]),
                    )
                    for cue in input_data["cues"]
                )
                artifact_provider = "deterministic_srt"
                artifact_metadata = {
                    "alignment_provider": "deterministic_srt",
                    "alignment_precision": "provided_cues",
                    "alignment_method": "provided_cues",
                }
                preview_metadata = {
                    "alignment_provider": "deterministic_srt",
                    "alignment_precision": "provided_cues",
                }

            content = serialize_srt(cues)
            episode_id = str(input_data["episode_id"])
            stored_artifact = await self._artifact_storage.put_bytes(
                f"subtitles/{episode_id}/{task.id}.srt",
                content,
                "application/x-subrip",
            )

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
            duration_seconds = max(cue.end_seconds for cue in cues)
            saved_task.artifacts.append(
                ArtifactSummary(
                    type="subtitle_srt",
                    provider=artifact_provider,
                    metadata={
                        "episode_id": episode_id,
                        "language": str(input_data["language"]),
                        "cue_count": len(cues),
                        "duration_seconds": duration_seconds,
                        "encoding": "utf-8",
                        **artifact_metadata,
                        **self._stored_artifact_metadata(stored_artifact),
                        "output_uri": output_uri,
                    },
                    preview={
                        "episode_id": episode_id,
                        "language": str(input_data["language"]),
                        "cue_count": len(cues),
                        **preview_metadata,
                        "output_uri": output_uri,
                    },
                )
            )
            await self._store.update_task(saved_task)
        except Exception as exc:
            failed_at = utc_now()
            error_code = self._error_code(exc, task.kind)
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
        task = await self._store.get_task(task_id)
        if task is None:
            raise RuntimeError(f"Task {task_id} was not found")
        return task

    async def _load_asr_audio(
        self,
        task: GenerationTaskRecord,
        input_data: dict[str, object],
    ) -> tuple[ArtifactRecord, bytes]:
        artifact_id = UUID(str(input_data["audio_artifact_id"]))
        artifact = await self._store.get_artifact(artifact_id)
        if artifact is None:
            raise SubtitleASRInputError(
                "SUBTITLE_ASR_AUDIO_ARTIFACT_NOT_FOUND",
                f"Audio Artifact {artifact_id} was not found",
            )
        if artifact.project_id != task.project_id:
            raise SubtitleASRInputError(
                "SUBTITLE_ASR_AUDIO_ARTIFACT_INVALID",
                "Audio Artifact must belong to the episode project",
            )
        if artifact.type != "audio_narration":
            raise SubtitleASRInputError(
                "SUBTITLE_ASR_AUDIO_ARTIFACT_INVALID",
                "ASR currently accepts only audio_narration Artifacts",
            )
        # Legacy artifacts may omit metadata. Recover provenance only from the
        # registered source task, never from names or storage path conventions.
        source_task = await self._store.get_task(artifact.task_id)
        episode_bindings = [artifact.metadata.get("episode_id")]
        if source_task is not None:
            episode_bindings.append(source_task.input_data.get("episode_id"))
        known_bindings = [str(value) for value in episode_bindings if value is not None]
        if not known_bindings or any(value != str(input_data["episode_id"]) for value in known_bindings):
            raise SubtitleASRInputError(
                "SUBTITLE_ASR_AUDIO_EPISODE_MISMATCH",
                "Audio Artifact must be verifiably bound to the target episode",
            )
        content_type = artifact.metadata.get("content_type")
        storage_key = artifact.metadata.get("storage_key")
        duration_seconds = artifact.metadata.get("duration_seconds")
        if not isinstance(content_type, str) or not content_type.startswith("audio/"):
            raise SubtitleASRInputError(
                "SUBTITLE_ASR_AUDIO_ARTIFACT_INVALID",
                "Audio Artifact must have an audio/* content type",
            )
        if not isinstance(storage_key, str) or not storage_key:
            raise SubtitleASRInputError(
                "SUBTITLE_ASR_AUDIO_ARTIFACT_MISSING",
                "Audio Artifact has no storage_key",
            )
        if not isinstance(duration_seconds, (int, float)) or float(duration_seconds) <= 0:
            raise SubtitleASRInputError(
                "SUBTITLE_ASR_AUDIO_ARTIFACT_INVALID",
                "Audio Artifact must include a positive duration_seconds",
            )
        audio_bytes = await self._artifact_storage.get_bytes(storage_key)
        if not audio_bytes:
            raise SubtitleASRInputError(
                "SUBTITLE_ASR_AUDIO_ARTIFACT_INVALID",
                "Audio Artifact is empty",
            )
        return artifact, audio_bytes

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
        await self._store.update_task(task)

    @staticmethod
    def _stage_run(task: GenerationTaskRecord) -> StageRun:
        for stage_run in task.stages:
            if stage_run.stage == StageName.SUBTITLE:
                return stage_run
        stage_run = StageRun(stage=StageName.SUBTITLE, status=TaskStatus.CREATED)
        task.stages.append(stage_run)
        return stage_run

    @staticmethod
    def _stored_artifact_metadata(stored_artifact: StoredArtifact) -> dict[str, object]:
        return {
            "storage_key": stored_artifact.storage_key,
            "content_type": stored_artifact.content_type,
            "size_bytes": stored_artifact.size_bytes,
            "sha256": stored_artifact.sha256,
        }

    @staticmethod
    def _to_rendering_cue(cue: object) -> SubtitleCue:
        if hasattr(cue, "model_dump"):
            cue_data = cue.model_dump()
        else:
            cue_data = cue
        if not isinstance(cue_data, dict):
            raise ValueError("Subtitle alignment Provider returned an invalid cue")
        return SubtitleCue(
            start_seconds=float(cue_data["start_seconds"]),
            end_seconds=float(cue_data["end_seconds"]),
            text=str(cue_data["text"]),
        )

    @staticmethod
    def _error_code(exc: Exception, task_kind: GenerationTaskKind) -> str:
        if isinstance(exc, (SubtitleASRProviderError, SubtitleASRInputError)):
            return exc.code
        if isinstance(exc, ASRProviderProfileError):
            return exc.code
        if isinstance(exc, SubtitleAlignmentProviderError):
            return exc.code
        if isinstance(exc, StorageError):
            return exc.code
        if isinstance(exc, ValueError):
            if task_kind == GenerationTaskKind.SUBTITLE_ALIGN:
                return "SUBTITLE_ALIGNMENT_INVALID"
            if task_kind == GenerationTaskKind.SUBTITLE_ASR:
                return "SUBTITLE_ASR_INVALID"
            return "SUBTITLE_GENERATION_INVALID"
        if task_kind == GenerationTaskKind.SUBTITLE_ASR:
            return "SUBTITLE_ASR_FAILED"
        if task_kind == GenerationTaskKind.SUBTITLE_ALIGN:
            return "SUBTITLE_ALIGNMENT_FAILED"
        return "SUBTITLE_GENERATION_FAILED"
