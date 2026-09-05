"""Asynchronous text-to-speech generation and audio Artifact registration."""

from __future__ import annotations

import base64
import binascii
from uuid import UUID, uuid4

from app.domain.models import (
    ArtifactSummary,
    AudioNarrationCreateRequest,
    GenerationTaskKind,
    GenerationTaskRecord,
    StageName,
    StageRun,
    TaskError,
    TaskStatus,
    TTSGenerationRequest,
    TTSGenerationResult,
    utc_now,
)
from app.media.audio_validation import AudioArtifactValidator, FFprobeAudioValidator
from app.media.narration_text import normalize_narration_text
from app.media.pronunciation import (
    PronunciationDictionary,
    map_word_boundaries_to_source,
)
from app.providers.errors_audio import AudioArtifactValidationError, TTSProviderError
from app.providers.tts import TTSProvider
from app.queue import TaskQueue
from app.repositories.protocol import NovelStore
from app.services.novel_service import EpisodeNotFoundError
from app.storage.protocol import ArtifactStorage, StorageError, StoredArtifact


class TTSInputError(Exception):
    """Raised when narration input exceeds configured production limits."""


class TTSTaskService:
    def __init__(
        self,
        store: NovelStore,
        task_queue: TaskQueue,
        provider: TTSProvider,
        artifact_storage: ArtifactStorage,
        audio_validator: AudioArtifactValidator | None = None,
        default_voice: str = "zh-CN-YunyangNeural",
        default_rate: str = "-35%",
        default_volume: str = "+0%",
        max_text_characters: int = 5000,
        pronunciation_dictionary: PronunciationDictionary | None = None,
    ) -> None:
        self._store = store
        self._task_queue = task_queue
        self._provider = provider
        self._artifact_storage = artifact_storage
        self._audio_validator = audio_validator or FFprobeAudioValidator()
        self._default_voice = default_voice
        self._default_rate = default_rate
        self._default_volume = default_volume
        self._max_text_characters = max(1, max_text_characters)
        self._pronunciation_dictionary = pronunciation_dictionary or PronunciationDictionary()

    async def create_task(
        self,
        episode_id: UUID,
        request: AudioNarrationCreateRequest,
        idempotency_key: str | None = None,
    ) -> tuple[GenerationTaskRecord, bool]:
        episode = await self._store.get_episode(episode_id)
        if episode is None:
            raise EpisodeNotFoundError
        text = normalize_narration_text(request.text)
        if not text:
            raise TTSInputError("TTS text must not be empty")
        if len(text) > self._max_text_characters:
            raise TTSInputError(
                f"TTS text must not exceed {self._max_text_characters} characters"
            )

        tts_text, replacement_count, replacements = self._pronunciation_dictionary.apply(text)
        voice = request.voice or self._default_voice
        rate = request.rate or self._default_rate
        volume = request.volume or self._default_volume
        task = GenerationTaskRecord(
            id=uuid4(),
            project_id=episode.project_id,
            kind=GenerationTaskKind.AUDIO_NARRATION,
            input_data={
                "episode_id": str(episode_id),
                # Store exactly the text that reaches the provider.  This
                # prevents frontend line wrapping and invisible separators from
                # changing the acoustic boundary policy in the Worker. The
                # reader-facing source text stays in ``text``; only
                # ``tts_text`` contains pronunciation substitutions.
                "text": text,
                "source_text": text,
                "tts_text": tts_text,
                "pronunciation_replacement_count": replacement_count,
                "pronunciation_replacements": replacements,
                "voice": voice,
                "rate": rate,
                "volume": volume,
            },
            status=TaskStatus.QUEUED,
            current_stage=StageName.AUDIO,
            stages=[StageRun(stage=StageName.AUDIO, status=TaskStatus.QUEUED)],
            updated_at=utc_now(),
        )
        task_key = (
            f"{GenerationTaskKind.AUDIO_NARRATION.value}:{episode_id}:{idempotency_key}"
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
            source_text = str(input_data.get("source_text", input_data["text"]))
            tts_text = str(input_data.get("tts_text", input_data["text"]))
            result = await self._provider.generate_speech(
                TTSGenerationRequest(
                    text=tts_text,
                    voice=str(input_data["voice"]),
                    rate=str(input_data["rate"]),
                    volume=str(input_data["volume"]),
                )
            )
            self._map_provider_boundaries(result, source_text, tts_text)
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
                    type="audio_narration",
                    provider=result.provider,
                    metadata={
                        "model": result.model,
                        "duration_ms": result.duration_ms,
                        "duration_seconds": result.duration_seconds,
                        "episode_id": input_data["episode_id"],
                        "source_text": source_text,
                        "tts_text": tts_text,
                        "pronunciation_replacement_count": input_data.get(
                            "pronunciation_replacement_count", 0
                        ),
                        "pronunciation_replacements": input_data.get(
                            "pronunciation_replacements", []
                        ),
                        "voice": input_data["voice"],
                        "rate": input_data["rate"],
                        "volume": input_data["volume"],
                        **result.metadata,
                        **self._stored_artifact_metadata(stored_artifact),
                        "output_uri": output_uri,
                    },
                    preview={
                        "episode_id": input_data["episode_id"],
                        "output_uri": output_uri,
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

    def _map_provider_boundaries(
        self,
        result: TTSGenerationResult,
        source_text: str,
        tts_text: str,
    ) -> None:
        """Map provider timings back to source text when substitutions changed length."""

        raw_boundaries = result.metadata.get("word_boundaries")
        if not isinstance(raw_boundaries, list) or not raw_boundaries:
            result.metadata = {
                **result.metadata,
                "source_text": source_text,
                "tts_text": tts_text,
                "word_boundaries_text_basis": "none",
                "word_boundaries_mapped": False,
            }
            return

        _, source_spans, _, _ = self._pronunciation_dictionary.apply_with_source_spans(source_text)
        mapped = map_word_boundaries_to_source(
            raw_boundaries,
            source_text,
            tts_text,
            source_spans,
        )
        metadata = {
            **result.metadata,
            "source_text": source_text,
            "tts_text": tts_text,
        }
        if mapped is None:
            # Keep the provider events for diagnosis, but explicitly state that
            # their text is based on the substituted provider string. Downstream
            # subtitle code must not present these as source-text alignment.
            metadata.update(
                {
                    "word_boundaries_text_basis": "tts_text",
                    "word_boundaries_mapped": False,
                    "word_boundary_mapping_error": "provider_events_not_mappable_to_source_text",
                }
            )
        else:
            metadata.update(
                {
                    "word_boundaries": mapped,
                    "word_boundaries_text_basis": "source_text",
                    "word_boundaries_mapped": True,
                    "timing_source": f"{metadata.get('timing_source', 'provider_word_boundary')}_source_text",
                }
            )
        result.metadata = metadata

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
        result: TTSGenerationResult,
    ) -> StoredArtifact:
        if not result.mime_type.startswith("audio/"):
            raise TTSProviderError(
                "TTS_PROVIDER_INVALID_RESPONSE",
                "TTS provider returned a non-audio MIME type",
            )
        try:
            content = base64.b64decode(result.audio_base64, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise TTSProviderError(
                "TTS_PROVIDER_INVALID_RESPONSE",
                "TTS provider returned invalid base64 audio content",
            ) from exc
        if not content:
            raise TTSProviderError(
                "TTS_PROVIDER_INVALID_RESPONSE",
                "TTS provider returned empty audio content",
            )

        probe_result = await self._audio_validator.validate_bytes(content, result.mime_type)
        result.duration_seconds = probe_result.duration_seconds
        result.metadata = {
            **result.metadata,
            "audio_duration_ms": round(probe_result.duration_seconds * 1000),
            "ffprobe": probe_result.as_metadata(),
        }
        input_data = task.input_data
        extension = self._extension_for_mime(result.mime_type)
        storage_key = f"audio/{input_data['episode_id']}/{task.id}{extension}"
        return await self._artifact_storage.put_bytes(
            storage_key,
            content,
            result.mime_type,
        )

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
        if isinstance(exc, (TTSProviderError, AudioArtifactValidationError)):
            return exc.code
        if isinstance(exc, StorageError):
            return exc.code
        return "AUDIO_NARRATION_GENERATION_FAILED"
