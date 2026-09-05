"""Asynchronous text-to-speech generation and audio Artifact registration."""

from __future__ import annotations

import base64
import binascii
from typing import Any
from time import monotonic
from uuid import UUID, uuid4

from app.domain.models import (
    ArtifactSummary,
    AudioNarrationCreateRequest,
    AudioNarrationLineRequest,
    GenerationTaskKind,
    GenerationTaskRecord,
    StageName,
    StageRun,
    TaskError,
    TaskStatus,
    TTSGenerationRequest,
    TTSGenerationResult,
    VoiceAssetRecord,
    utc_now,
)
from app.media.audio_validation import AudioArtifactValidator, FFprobeAudioValidator
from app.media.multi_voice_audio import MultiVoiceAudioError, concatenate_audio_segments
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
from app.services.voice_asset_service import (
    VoiceAssetInputError,
    VoiceAssetNotFoundError,
    VoiceAssetService,
)
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
        voice_asset_service: VoiceAssetService | None = None,
        configured_provider: str | None = None,
        multi_voice_ffmpeg_binary: str = "ffmpeg",
        multi_voice_timeout_seconds: int = 120,
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
        self._voice_asset_service = voice_asset_service
        self._configured_provider = configured_provider
        self._multi_voice_ffmpeg_binary = multi_voice_ffmpeg_binary
        self._multi_voice_timeout_seconds = max(1, multi_voice_timeout_seconds)

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
        if text and request.voice_lines:
            raise TTSInputError("Use either text or voice_lines, not both")
        if not text and not request.voice_lines:
            raise TTSInputError("TTS text or voice_lines must not be empty")
        total_characters = len(text) if text else sum(
            len(normalize_narration_text(line.text)) for line in request.voice_lines
        )
        if total_characters > self._max_text_characters:
            raise TTSInputError(
                f"TTS text must not exceed {self._max_text_characters} characters"
            )

        voice_asset = await self._resolve_voice_asset(
            episode.project_id,
            request.voice_asset_id,
            request.speaker,
        )
        voice = request.voice or (voice_asset.voice if voice_asset else self._default_voice)
        rate = request.rate or (voice_asset.rate if voice_asset else self._default_rate)
        volume = request.volume or (voice_asset.volume if voice_asset else self._default_volume)
        tts_text, replacement_count, replacements = self._pronunciation_dictionary.apply(text) if text else ("", 0, [])
        voice_lines = await self._prepare_voice_lines(
            episode.project_id,
            request.voice_lines,
            fallback_asset=voice_asset,
            voice_override=request.voice,
            rate_override=request.rate,
            volume_override=request.volume,
        )
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
                "voice_asset_id": str(voice_asset.id) if voice_asset else None,
                "speaker": request.speaker,
                "voice_lines": voice_lines,
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
            voice_lines = input_data.get("voice_lines")
            if isinstance(voice_lines, list) and voice_lines:
                result = await self._generate_multi_voice(voice_lines)
            else:
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
                        "voice_asset_id": input_data.get("voice_asset_id"),
                        "speaker": input_data.get("speaker"),
                        "voice_lines": input_data.get("voice_lines", []),
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

    async def _resolve_voice_asset(
        self,
        project_id: UUID,
        voice_asset_id: UUID | None,
        speaker: str | None,
    ) -> VoiceAssetRecord | None:
        """Resolve an explicit voice profile or a unique speaker binding.

        A legacy single-voice request remains valid without the voice-asset
        service. Explicit asset IDs, however, must never be silently ignored.
        """

        if voice_asset_id is None and not (speaker and speaker.strip()):
            return None
        if self._voice_asset_service is None:
            if voice_asset_id is not None:
                raise VoiceAssetInputError(
                    "VOICE_ASSET_SERVICE_NOT_CONFIGURED",
                    "Voice assets are not configured for this runtime",
                )
            # A speaker label is useful metadata for legacy callers even when
            # no asset registry is installed; use the configured default voice.
            return None

        asset: VoiceAssetRecord | None
        if voice_asset_id is not None:
            try:
                asset = await self._voice_asset_service.require_for_project(
                    project_id,
                    voice_asset_id,
                )
            except VoiceAssetNotFoundError as exc:
                raise VoiceAssetInputError(
                    "VOICE_ASSET_NOT_FOUND",
                    f"Voice asset {voice_asset_id} was not found",
                ) from exc
        else:
            asset = await self._voice_asset_service.find_ready_by_speaker(
                project_id,
                str(speaker),
            )
            if asset is None:
                raise VoiceAssetInputError(
                    "VOICE_ASSET_SPEAKER_NOT_FOUND",
                    f"No ready voice asset is uniquely bound to speaker {speaker!r}",
                )

        self._validate_voice_asset_provider(asset)
        return asset

    def _validate_voice_asset_provider(self, asset: VoiceAssetRecord) -> None:
        if self._configured_provider and asset.provider != self._configured_provider:
            raise VoiceAssetInputError(
                "VOICE_ASSET_PROVIDER_MISMATCH",
                f"Voice asset provider {asset.provider!r} does not match configured TTS provider {self._configured_provider!r}",
            )

    async def _prepare_voice_lines(
        self,
        project_id: UUID,
        lines: list[AudioNarrationLineRequest],
        *,
        fallback_asset: VoiceAssetRecord | None = None,
        voice_override: str | None = None,
        rate_override: str | None = None,
        volume_override: str | None = None,
    ) -> list[dict[str, object]]:
        prepared: list[dict[str, object]] = []
        for line in lines:
            line_asset = fallback_asset
            if line.voice_asset_id is not None or fallback_asset is None:
                line_asset = await self._resolve_voice_asset(
                    project_id,
                    line.voice_asset_id,
                    line.speaker,
                )
            if line_asset is not None:
                self._validate_voice_asset_provider(line_asset)

            source_text = normalize_narration_text(line.text)
            tts_text, replacement_count, replacements = self._pronunciation_dictionary.apply(
                source_text
            )
            prepared.append(
                {
                    "line_index": line.line_index,
                    "speaker": line.speaker,
                    "source_text": source_text,
                    "tts_text": tts_text,
                    "pronunciation_replacement_count": replacement_count,
                    "pronunciation_replacements": replacements,
                    "voice_asset_id": str(line_asset.id) if line_asset else None,
                    "voice": voice_override or (line_asset.voice if line_asset else self._default_voice),
                    "rate": rate_override or (line_asset.rate if line_asset else self._default_rate),
                    "volume": volume_override or (line_asset.volume if line_asset else self._default_volume),
                    "style": line_asset.style if line_asset else "",
                    "pause_after_seconds": line.pause_after_seconds,
                }
            )
        return prepared

    async def _generate_multi_voice(
        self,
        lines: list[dict[str, Any]],
    ) -> TTSGenerationResult:
        """Synthesize each role line, then join with explicit clean pauses."""

        started = monotonic()
        segments: list[bytes] = []
        content_types: list[str] = []
        pauses: list[float] = []
        timeline: list[dict[str, object]] = []
        cursor = 0.0
        providers: list[str] = []

        for position, line in enumerate(lines):
            try:
                result = await self._provider.generate_speech(
                    TTSGenerationRequest(
                        text=str(line["tts_text"]),
                        voice=str(line["voice"]),
                        rate=str(line["rate"]),
                        volume=str(line["volume"]),
                    )
                )
                source_text = str(line["source_text"])
                tts_text = str(line["tts_text"])
                self._map_provider_boundaries(result, source_text, tts_text)
                content, duration_seconds = await self._decode_and_validate_audio(result)
            except TTSProviderError as exc:
                raise TTSProviderError(
                    exc.code,
                    f"Voice line {line.get('line_index', position + 1)} ({line.get('speaker', 'unknown')}) failed: {exc.message}",
                ) from exc

            segments.append(content)
            content_types.append(result.mime_type)
            pause = float(line.get("pause_after_seconds", 0.12))
            pauses.append(pause)
            providers.append(result.provider)
            item: dict[str, object] = {
                "line_index": int(line["line_index"]),
                "speaker": str(line["speaker"]),
                "source_text": source_text,
                "tts_text": tts_text,
                "voice_asset_id": line.get("voice_asset_id"),
                "voice": str(line["voice"]),
                "rate": str(line["rate"]),
                "volume": str(line["volume"]),
                "style": str(line.get("style", "")),
                "start_seconds": round(cursor, 6),
                "end_seconds": round(cursor + duration_seconds, 6),
                "duration_seconds": round(duration_seconds, 6),
                "pause_after_seconds": pause if position < len(lines) - 1 else 0.0,
                "provider": result.provider,
                "model": result.model,
                "pronunciation_replacement_count": line.get(
                    "pronunciation_replacement_count", 0
                ),
                "pronunciation_replacements": line.get("pronunciation_replacements", []),
            }
            boundaries = result.metadata.get("word_boundaries")
            if isinstance(boundaries, list):
                item["word_boundaries"] = boundaries
                item["word_boundaries_text_basis"] = result.metadata.get(
                    "word_boundaries_text_basis", "none"
                )
            timeline.append(item)
            cursor += duration_seconds
            if position < len(lines) - 1:
                cursor += pause

        try:
            content = await concatenate_audio_segments(
                segments,
                content_types,
                pauses,
                binary=self._multi_voice_ffmpeg_binary,
                timeout_seconds=self._multi_voice_timeout_seconds,
            )
        except MultiVoiceAudioError:
            raise

        provider = providers[0] if providers and len(set(providers)) == 1 else "multi_voice"
        return TTSGenerationResult(
            audio_base64=base64.b64encode(content).decode("ascii"),
            mime_type="audio/wav",
            provider=provider,
            model="multi-voice",
            duration_seconds=0,
            duration_ms=max(1, round((monotonic() - started) * 1000)),
            metadata={
                "synthesis_mode": "multi_voice_segmented",
                "segmentation": "speaker_lines",
                "waveform_join_strategy": "ffmpeg_concat_with_silence",
                "independent_waveform_count": len(lines),
                "speaker_count": len({str(line["speaker"]) for line in lines}),
                "voice_lines": timeline,
                "audio_timeline_duration_seconds": round(cursor, 6),
                "provider_set": sorted(set(providers)),
            },
        )

    async def _decode_and_validate_audio(
        self,
        result: TTSGenerationResult,
    ) -> tuple[bytes, float]:
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
        return content, probe_result.duration_seconds

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
        if isinstance(exc, VoiceAssetInputError):
            return exc.code
        if isinstance(exc, VoiceAssetNotFoundError):
            return "VOICE_ASSET_NOT_FOUND"
        if isinstance(exc, MultiVoiceAudioError):
            return exc.code
        if isinstance(exc, StorageError):
            return exc.code
        return "AUDIO_NARRATION_GENERATION_FAILED"
