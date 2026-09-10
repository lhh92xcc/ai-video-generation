"""Topic short-video media tasks and their durable dependency graph.

The novel pipeline has episode/asset semantics that do not exist for a simple
information short.  This module keeps the topic path in the same task and
Artifact contracts without inventing a fake Episode row.  Every media task is
still independently persisted, queued, retryable and inspectable.
"""

from __future__ import annotations

import base64
import binascii
from collections import defaultdict
from collections.abc import Awaitable, Callable, Iterable
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from app.domain.models import (
    ArtifactRecord,
    ArtifactSummary,
    GenerationTaskKind,
    GenerationTaskRecord,
    ProjectRecord,
    ProjectStatus,
    ScriptContent,
    StageName,
    StageRun,
    SubtitleAlignmentCreateRequest,
    TaskError,
    TaskStatus,
    TTSGenerationRequest,
    TopicProductionCreateRequest,
    TopicProductionResponse,
    VideoClipGenerationRequest,
    utc_now,
)
from app.domain.topic_run import (
    TOPIC_PIPELINE,
    TOPIC_RUN_ERROR_CODE,
    TOPIC_RUN_ERROR_MESSAGE,
    TOPIC_RUN_CONTROL_REVISION,
    TOPIC_RUN_ID,
    TOPIC_RUN_PLAN,
    TOPIC_SCENE_INDEX,
    TOPIC_SCRIPT_TASK_ID,
    TOPIC_RUN_STATUS,
    topic_control_revision,
    topic_run_status_from_tasks,
)
from app.media.audio_validation import AudioArtifactValidator, FFprobeAudioValidator
from app.media.narration_text import join_continuous_narration_units, normalize_narration_text
from app.media.pronunciation import PronunciationDictionary
from app.media.subtitle_quality import evaluate_subtitle_cues
from app.media.video_motion import FFmpegMotionEvidenceValidator, VideoMotionEvidenceValidator
from app.media.video_validation import FFprobeVideoValidator, VideoArtifactValidator
from app.media.visual_prompts import DEFAULT_VIDEO_NEGATIVE_PROMPT
from app.providers.errors_audio import AudioArtifactValidationError, TTSProviderError
from app.providers.errors_subtitle import SubtitleAlignmentProviderError
from app.providers.errors_video import VideoProviderError
from app.providers.profiles import (
    ProviderProfileError,
    VisualProviderProfileError,
    VisualProviderProfileRegistry,
)
from app.providers.subtitle_alignment import SubtitleAlignmentProvider
from app.providers.tts import TTSProvider
from app.providers.video_generation import VideoGenerationProvider
from app.queue import TaskQueue
from app.rendering.ffmpeg_renderer import AudioTrackInput, FFmpegRenderError, FFmpegVideoRenderer
from app.rendering.subtitles import SubtitleCue, parse_srt, serialize_srt
from app.repositories.protocol import ProjectTaskStore
from app.storage.protocol import ArtifactStorage, StorageError, StoredArtifact


_ACTIVE_STATUSES = {TaskStatus.CREATED, TaskStatus.QUEUED, TaskStatus.RUNNING}


class TopicPipelineError(Exception):
    """Stable error raised for invalid topic media dependencies."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class TopicMediaTaskService:
    """Run topic-scoped audio, subtitle, video and assembly tasks."""

    def __init__(
        self,
        store: ProjectTaskStore,
        task_queue: TaskQueue,
        tts_provider: TTSProvider,
        subtitle_alignment_provider: SubtitleAlignmentProvider | None,
        video_provider: VideoGenerationProvider,
        artifact_storage: ArtifactStorage,
        *,
        audio_validator: AudioArtifactValidator | None = None,
        video_validator: VideoArtifactValidator | None = None,
        motion_validator: VideoMotionEvidenceValidator | None = None,
        renderer: FFmpegVideoRenderer | None = None,
        default_voice: str = "zh-CN-YunyangNeural",
        default_rate: str = "-35%",
        default_volume: str = "+0%",
        pronunciation_dictionary: PronunciationDictionary | None = None,
        default_negative_prompt: str = DEFAULT_VIDEO_NEGATIVE_PROMPT,
        provider_registry: VisualProviderProfileRegistry | None = None,
    ) -> None:
        self._store = store
        self._task_queue = task_queue
        self._tts_provider = tts_provider
        self._subtitle_alignment_provider = subtitle_alignment_provider
        self._video_provider = video_provider
        self._artifact_storage = artifact_storage
        self._audio_validator = audio_validator or FFprobeAudioValidator()
        self._video_validator = video_validator or FFprobeVideoValidator()
        self._motion_validator = motion_validator or FFmpegMotionEvidenceValidator()
        self._renderer = renderer or FFmpegVideoRenderer()
        self._default_voice = default_voice
        self._default_rate = default_rate
        self._default_volume = default_volume
        self._pronunciation_dictionary = pronunciation_dictionary or PronunciationDictionary()
        self._default_negative_prompt = default_negative_prompt.strip() or DEFAULT_VIDEO_NEGATIVE_PROMPT
        self._provider_registry = provider_registry

    async def create_audio_task(
        self,
        project_id: UUID,
        text: str,
        run_id: UUID,
        script_task_id: UUID,
        idempotency_key: str,
        run_plan: dict[str, Any] | None = None,
    ) -> tuple[GenerationTaskRecord, bool]:
        source_text = normalize_narration_text(text)
        if not source_text:
            raise TopicPipelineError("TOPIC_NARRATION_EMPTY", "主题脚本没有可合成的旁白文本")
        tts_text, replacement_count, replacements = self._pronunciation_dictionary.apply(source_text)
        return await self._create_task(
            project_id,
            GenerationTaskKind.AUDIO_NARRATION,
            StageName.AUDIO,
            {
                TOPIC_PIPELINE: True,
                TOPIC_RUN_ID: str(run_id),
                TOPIC_RUN_STATUS: "active",
                TOPIC_RUN_PLAN: dict(run_plan or {}),
                TOPIC_SCRIPT_TASK_ID: str(script_task_id),
                "source_text": source_text,
                "tts_text": tts_text,
                "pronunciation_replacement_count": replacement_count,
                "pronunciation_replacements": replacements,
                "voice": self._default_voice,
                "rate": self._default_rate,
                "volume": self._default_volume,
            },
            idempotency_key,
        )

    async def create_subtitle_task(
        self,
        project_id: UUID,
        run_id: UUID,
        script_task_id: UUID,
        audio_task_id: UUID,
        text: str,
        language: str,
        idempotency_key: str,
        run_plan: dict[str, Any] | None = None,
    ) -> tuple[GenerationTaskRecord, bool]:
        return await self._create_task(
            project_id,
            GenerationTaskKind.SUBTITLE_ALIGN,
            StageName.SUBTITLE,
            {
                TOPIC_PIPELINE: True,
                TOPIC_RUN_ID: str(run_id),
                TOPIC_RUN_STATUS: "active",
                TOPIC_RUN_PLAN: dict(run_plan or {}),
                TOPIC_SCRIPT_TASK_ID: str(script_task_id),
                "audio_task_id": str(audio_task_id),
                "language": language,
                "text": normalize_narration_text(text),
            },
            idempotency_key,
        )

    async def create_video_task(
        self,
        project_id: UUID,
        run_id: UUID,
        script_task_id: UUID,
        scene_index: int,
        duration_seconds: int,
        prompt: str,
        *,
        provider_profile_id: str | None,
        visual_quality_profile_id: str | None,
        idempotency_key: str,
        run_plan: dict[str, Any] | None = None,
    ) -> tuple[GenerationTaskRecord, bool]:
        provider_profile, quality_snapshot = self._resolve_video_selection(
            provider_profile_id,
            visual_quality_profile_id,
        )
        return await self._create_task(
            project_id,
            GenerationTaskKind.VIDEO_CLIP,
            StageName.VIDEO_CLIP,
            {
                TOPIC_PIPELINE: True,
                TOPIC_RUN_ID: str(run_id),
                TOPIC_RUN_STATUS: "active",
                TOPIC_RUN_PLAN: dict(run_plan or {}),
                TOPIC_SCRIPT_TASK_ID: str(script_task_id),
                TOPIC_SCENE_INDEX: scene_index,
                "duration_seconds": duration_seconds,
                "prompt": prompt,
                "negative_prompt": self._default_negative_prompt,
                "generation_attempt": 1,
                **({"provider_profile_id": provider_profile} if provider_profile else {}),
                **(
                    {
                        "visual_quality_profile_id": quality_snapshot["profile_id"],
                        "visual_quality_profile": quality_snapshot,
                    }
                    if quality_snapshot
                    else {}
                ),
            },
            idempotency_key,
        )

    async def create_assembly_task(
        self,
        project_id: UUID,
        run_id: UUID,
        script_task_id: UUID,
        clip_task_ids: list[UUID],
        audio_artifact_id: UUID | None,
        subtitle_artifact_id: UUID | None,
        idempotency_key: str,
        run_plan: dict[str, Any] | None = None,
    ) -> tuple[GenerationTaskRecord, bool]:
        if not clip_task_ids:
            raise TopicPipelineError("TOPIC_VIDEO_REQUIRED", "主题成片至少需要一个视频片段")
        return await self._create_task(
            project_id,
            GenerationTaskKind.VIDEO_ASSEMBLY,
            StageName.VIDEO_ASSEMBLY,
            {
                TOPIC_PIPELINE: True,
                TOPIC_RUN_ID: str(run_id),
                TOPIC_RUN_STATUS: "active",
                TOPIC_RUN_PLAN: dict(run_plan or {}),
                TOPIC_SCRIPT_TASK_ID: str(script_task_id),
                "clip_task_ids": [str(task_id) for task_id in clip_task_ids],
                "audio_artifact_id": str(audio_artifact_id) if audio_artifact_id else None,
                "subtitle_artifact_id": str(subtitle_artifact_id) if subtitle_artifact_id else None,
                "output_format": "mp4",
            },
            idempotency_key,
        )

    async def run_task(self, task_id: UUID) -> None:
        task = await self._get_task(task_id)
        if task.input_data.get(TOPIC_PIPELINE) is not True:
            raise TopicPipelineError("TOPIC_TASK_SCOPE_INVALID", "任务不是主题短视频媒体任务")
        stage = self._stage_for_kind(task.kind)
        await self._mark_running(task, stage)
        try:
            if task.kind == GenerationTaskKind.AUDIO_NARRATION:
                await self._run_audio(task)
            elif task.kind == GenerationTaskKind.SUBTITLE_ALIGN:
                await self._run_subtitle(task)
            elif task.kind == GenerationTaskKind.VIDEO_CLIP:
                await self._run_video(task)
            elif task.kind == GenerationTaskKind.VIDEO_ASSEMBLY:
                await self._run_assembly(task)
            else:
                raise TopicPipelineError(
                    "TOPIC_TASK_KIND_UNSUPPORTED",
                    f"不支持的主题媒体任务类型：{task.kind.value}",
                )
        except Exception as exc:
            failed_at = utc_now()
            failed = await self._get_task(task_id)
            error_code = self._error_code(exc, task.kind)
            failed.status = TaskStatus.FAILED
            failed.current_stage = stage
            failed.updated_at = failed_at
            failed.error = TaskError(code=error_code, message=str(exc) or error_code)
            stage_run = self._stage_run(failed, stage)
            stage_run.status = TaskStatus.FAILED
            stage_run.error_code = error_code
            stage_run.finished_at = failed_at
            await self._store.update_task(failed)

    async def _run_audio(self, task: GenerationTaskRecord) -> None:
        input_data = task.input_data
        result = await self._tts_provider.generate_speech(
            TTSGenerationRequest(
                text=str(input_data["tts_text"]),
                voice=str(input_data["voice"]),
                rate=str(input_data["rate"]),
                volume=str(input_data["volume"]),
            )
        )
        content = self._decode_base64(result.audio_base64, "TTS provider returned invalid base64 audio content")
        if not result.mime_type.startswith("audio/"):
            raise TTSProviderError("TTS_PROVIDER_INVALID_RESPONSE", "TTS provider returned a non-audio MIME type")
        probe = await self._audio_validator.validate_bytes(content, result.mime_type)
        stored = await self._artifact_storage.put_bytes(
            f"topic-audio/{task.project_id}/{task.id}{self._audio_extension(result.mime_type)}",
            content,
            result.mime_type,
        )
        metadata = {
            "project_id": str(task.project_id),
            "source_text": str(input_data["source_text"]),
            "tts_text": str(input_data["tts_text"]),
            "voice": input_data["voice"],
            "rate": input_data["rate"],
            "volume": input_data["volume"],
            "pronunciation_replacement_count": input_data.get("pronunciation_replacement_count", 0),
            "pronunciation_replacements": input_data.get("pronunciation_replacements", []),
            "duration_seconds": probe.duration_seconds,
            "duration_ms": result.duration_ms,
            **result.metadata,
            **self._stored_artifact_metadata(stored),
            "output_uri": stored.uri,
        }
        await self._finish_task(
            task.id,
            StageName.AUDIO,
            ArtifactSummary(
                type="audio_narration",
                provider=result.provider,
                metadata=metadata,
                preview={"project_id": str(task.project_id), "output_uri": stored.uri},
            ),
        )

    async def _run_subtitle(self, task: GenerationTaskRecord) -> None:
        if self._subtitle_alignment_provider is None:
            raise SubtitleAlignmentProviderError(
                "SUBTITLE_ALIGNMENT_PROVIDER_UNAVAILABLE",
                "没有配置主题字幕对齐 Provider",
            )
        input_data = task.input_data
        audio_task = await self._get_task(UUID(str(input_data["audio_task_id"])))
        audio_artifact = self._find_artifact(audio_task, "audio_narration")
        if audio_artifact is None:
            raise TopicPipelineError("TOPIC_AUDIO_ARTIFACT_MISSING", "主题字幕找不到旁白 Artifact")
        duration = self._positive_float(audio_artifact.metadata.get("duration_seconds"))
        if duration <= 0:
            raise TopicPipelineError("TOPIC_AUDIO_DURATION_REQUIRED", "主题旁白缺少有效时长")
        result = await self._subtitle_alignment_provider.align(
            SubtitleAlignmentCreateRequest(
                text=str(input_data["text"]),
                language=str(input_data["language"]),
                audio_duration_seconds=duration,
            )
        )
        cues = tuple(self._to_subtitle_cue(cue) for cue in result.cues)
        content = serialize_srt(cues)
        stored = await self._artifact_storage.put_bytes(
            f"topic-subtitles/{task.project_id}/{task.id}.srt",
            content,
            "application/x-subrip",
        )
        quality = evaluate_subtitle_cues(
            cues,
            audio_duration_seconds=duration,
            reference_text=str(input_data["text"]),
            alignment_precision=result.precision,
        ).as_dict()
        metadata = {
            "project_id": str(task.project_id),
            "language": str(input_data["language"]),
            "cue_count": len(cues),
            "duration_seconds": max(cue.end_seconds for cue in cues),
            "alignment_provider": result.provider,
            "alignment_precision": result.precision,
            "alignment_method": "provider",
            "audio_artifact_id": str(audio_artifact.id),
            "audio_duration_seconds": duration,
            "quality": quality,
            **result.metadata,
            **self._stored_artifact_metadata(stored),
            "output_uri": stored.uri,
        }
        await self._finish_task(
            task.id,
            StageName.SUBTITLE,
            ArtifactSummary(
                type="subtitle_srt",
                provider=result.provider,
                metadata=metadata,
                preview={
                    "project_id": str(task.project_id),
                    "cue_count": len(cues),
                    "output_uri": stored.uri,
                },
            ),
        )

    async def _run_video(self, task: GenerationTaskRecord) -> None:
        input_data = task.input_data
        provider = self._video_provider
        quality_settings = None
        if self._provider_registry is not None:
            provider = await self._provider_registry.get_video_provider(
                str(input_data["provider_profile_id"])
                if input_data.get("provider_profile_id")
                else None,
                quality_profile_id=(
                    str(input_data["visual_quality_profile_id"])
                    if input_data.get("visual_quality_profile_id")
                    else None
                ),
                quality_snapshot=input_data.get("visual_quality_profile"),
            )
            quality_settings = self._provider_registry.settings_for_quality(
                str(input_data["visual_quality_profile_id"])
                if input_data.get("visual_quality_profile_id")
                else None,
                input_data.get("visual_quality_profile"),
            )
        scene_index = int(input_data[TOPIC_SCENE_INDEX])
        script_id = UUID(str(input_data[TOPIC_SCRIPT_TASK_ID]))
        topic_shot_list_id = uuid5(
            NAMESPACE_URL,
            f"ai-video:topic-shot-list:{task.project_id}:{script_id}",
        )
        result = await provider.generate_video_clip(
            VideoClipGenerationRequest(
                # The generic provider contract carries contextual UUIDs for
                # vendor adapters.  Topic tasks use stable synthetic values;
                # no Episode row is created or exposed to clients.
                episode_id=task.project_id,
                shot_list_id=topic_shot_list_id,
                shot_index=scene_index,
                duration_seconds=int(input_data["duration_seconds"]),
                prompt=str(input_data["prompt"]),
                negative_prompt=str(input_data["negative_prompt"]),
                generation_attempt=max(1, int(input_data.get("generation_attempt", 1))),
                width=quality_settings.video_output_width if quality_settings else None,
                height=quality_settings.video_output_height if quality_settings else None,
                fps=quality_settings.video_fps if quality_settings else None,
                steps=quality_settings.video_steps if quality_settings else None,
                cfg=quality_settings.video_cfg if quality_settings else None,
                noise_aug_strength=quality_settings.video_noise_aug_strength if quality_settings else None,
            )
        )
        if not result.video_base64:
            raise VideoProviderError(
                "VIDEO_PROVIDER_OUTPUT_NOT_STORABLE",
                "主题视频 Provider 没有返回可存储的视频内容；当前主题 Assembly 需要下载后的 MP4/WebM",
            )
        if not result.mime_type.startswith("video/"):
            raise VideoProviderError("VIDEO_PROVIDER_INVALID_RESPONSE", "视频 Provider 返回了非视频 MIME 类型")
        content = self._decode_base64(result.video_base64, "Video provider returned invalid base64 content")
        probe = await self._video_validator.validate_bytes(content, result.mime_type)
        motion = await self._motion_validator.validate_bytes(content, result.mime_type)
        stored = await self._artifact_storage.put_bytes(
            f"topic-video/{task.project_id}/scene-{scene_index:03d}/{task.id}-attempt-{int(input_data.get('generation_attempt', 1))}{self._video_extension(result.mime_type)}",
            content,
            result.mime_type,
        )
        metadata = {
            "project_id": str(task.project_id),
            "scene_index": scene_index,
            "duration_seconds": probe.duration_seconds,
            "duration_ms": result.duration_ms,
            "prompt": input_data.get("prompt"),
            "negative_prompt": input_data.get("negative_prompt"),
            "provider_profile_id": input_data.get("provider_profile_id"),
            "visual_quality_profile_id": input_data.get("visual_quality_profile_id"),
            "visual_quality_profile": input_data.get("visual_quality_profile"),
            "generation_attempt": int(input_data.get("generation_attempt", 1)),
            "ffprobe": probe.as_metadata(),
            "motion_evidence": motion.as_metadata(),
            **result.metadata,
            **self._stored_artifact_metadata(stored),
            "output_uri": stored.uri,
        }
        await self._finish_task(
            task.id,
            StageName.VIDEO_CLIP,
            ArtifactSummary(
                type="video_clip",
                provider=result.provider,
                metadata=metadata,
                preview={
                    "project_id": str(task.project_id),
                    "scene_index": scene_index,
                    "output_uri": stored.uri,
                },
            ),
        )

    async def _run_assembly(self, task: GenerationTaskRecord) -> None:
        input_data = task.input_data
        clip_task_ids = [UUID(str(value)) for value in input_data["clip_task_ids"]]
        clips: list[bytes] = []
        content_types: list[str] = []
        for clip_task_id in clip_task_ids:
            clip_task = await self._get_task(clip_task_id)
            if clip_task.project_id != task.project_id or clip_task.status != TaskStatus.SUCCEEDED:
                raise TopicPipelineError("TOPIC_VIDEO_CLIP_NOT_READY", f"主题视频片段任务 {clip_task_id} 尚未成功")
            artifact = self._find_artifact(clip_task, "video_clip")
            storage_key, content_type = self._require_stored_media(artifact, "video")
            clips.append(await self._artifact_storage.get_bytes(storage_key))
            content_types.append(content_type)

        audio_tracks: list[AudioTrackInput] = []
        audio_artifact_id = input_data.get("audio_artifact_id")
        if audio_artifact_id:
            audio_artifact = await self._require_artifact(task.project_id, UUID(str(audio_artifact_id)), "audio_narration")
            audio_key, audio_type = self._require_stored_media(audio_artifact, "audio")
            audio_content = await self._artifact_storage.get_bytes(audio_key)
            try:
                await self._audio_validator.validate_bytes(audio_content, audio_type)
            except AudioArtifactValidationError as exc:
                raise TopicPipelineError("TOPIC_AUDIO_INVALID", exc.message) from exc
            audio_tracks.append(AudioTrackInput(content=audio_content, content_type=audio_type))

        subtitles: tuple[SubtitleCue, ...] = ()
        subtitle_artifact_id = input_data.get("subtitle_artifact_id")
        if subtitle_artifact_id:
            subtitle_artifact = await self._require_artifact(task.project_id, UUID(str(subtitle_artifact_id)), "subtitle_srt")
            subtitle_key, _subtitle_type = self._require_stored_media(subtitle_artifact, "subtitle")
            try:
                subtitles = parse_srt(await self._artifact_storage.get_bytes(subtitle_key))
            except ValueError as exc:
                raise TopicPipelineError("TOPIC_SUBTITLE_INVALID", str(exc)) from exc

        await self._set_progress(task.id, 35)
        rendered = await self._renderer.render(
            clips,
            content_types,
            audio_tracks=audio_tracks,
            subtitles=subtitles,
        )
        await self._set_progress(task.id, 80)
        stored = await self._artifact_storage.put_bytes(
            f"topic-videos/{task.project_id}/{task.id}.mp4",
            rendered.content,
            rendered.content_type,
        )
        await self._finish_task(
            task.id,
            StageName.VIDEO_ASSEMBLY,
            ArtifactSummary(
                type="rendered_video",
                provider="ffmpeg",
                metadata={
                    "project_id": str(task.project_id),
                    "clip_task_ids": [str(value) for value in clip_task_ids],
                    "audio_artifact_id": audio_artifact_id,
                    "subtitle_artifact_id": subtitle_artifact_id,
                    "duration_ms": round(rendered.probe.duration_seconds * 1000),
                    **rendered.as_metadata(),
                    **self._stored_artifact_metadata(stored),
                    "output_uri": stored.uri,
                },
                preview={
                    "project_id": str(task.project_id),
                    "output_uri": stored.uri,
                    "clip_task_count": len(clip_task_ids),
                    "audio_track_count": rendered.audio_track_count,
                    "subtitle_count": rendered.subtitle_count,
                },
            ),
        )

    async def _create_task(
        self,
        project_id: UUID,
        kind: GenerationTaskKind,
        stage: StageName,
        input_data: dict[str, Any],
        idempotency_key: str,
    ) -> tuple[GenerationTaskRecord, bool]:
        task = GenerationTaskRecord(
            project_id=project_id,
            kind=kind,
            input_data=input_data,
            status=TaskStatus.QUEUED,
            current_stage=stage,
            stages=[StageRun(stage=stage, status=TaskStatus.QUEUED)],
            updated_at=utc_now(),
        )
        stored, reused = await self._store.create_task(task, idempotency_key)
        if not reused:
            await self._task_queue.enqueue(stored.id)
        return stored, reused

    async def _finish_task(
        self,
        task_id: UUID,
        stage: StageName,
        artifact: ArtifactSummary,
    ) -> None:
        finished_at = utc_now()
        task = await self._get_task(task_id)
        task.status = TaskStatus.SUCCEEDED
        task.current_stage = None
        task.progress = 100
        task.updated_at = finished_at
        stage_run = self._stage_run(task, stage)
        stage_run.status = TaskStatus.SUCCEEDED
        stage_run.progress = 100
        stage_run.finished_at = finished_at
        task.artifacts.append(artifact)
        await self._store.update_task(task)

    async def _mark_running(self, task: GenerationTaskRecord, stage: StageName) -> None:
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

    async def _set_progress(self, task_id: UUID, progress: int) -> None:
        task = await self._get_task(task_id)
        task.progress = max(0, min(100, progress))
        stage = self._stage_for_kind(task.kind)
        self._stage_run(task, stage).progress = task.progress
        task.updated_at = utc_now()
        await self._store.update_task(task)

    async def _get_task(self, task_id: UUID) -> GenerationTaskRecord:
        task = await self._store.get_task(task_id)
        if task is None:
            raise TopicPipelineError("TOPIC_TASK_NOT_FOUND", f"主题任务 {task_id} 不存在")
        return task

    async def _require_artifact(
        self,
        project_id: UUID,
        artifact_id: UUID,
        artifact_type: str,
    ) -> ArtifactRecord:
        artifact = await self._store.get_artifact(artifact_id)
        if artifact is None or artifact.project_id != project_id or artifact.type != artifact_type:
            raise TopicPipelineError("TOPIC_ARTIFACT_INVALID", f"主题 Artifact {artifact_id} 不可用")
        return artifact

    @staticmethod
    def _find_artifact(task: GenerationTaskRecord, artifact_type: str) -> ArtifactSummary | None:
        return next((item for item in reversed(task.artifacts) if item.type == artifact_type), None)

    @staticmethod
    def _require_stored_media(
        artifact: ArtifactRecord | ArtifactSummary | None,
        kind: str,
    ) -> tuple[str, str]:
        if artifact is None:
            raise TopicPipelineError("TOPIC_ARTIFACT_MISSING", f"主题 {kind} Artifact 不存在")
        metadata = artifact.metadata
        storage_key = metadata.get("storage_key")
        content_type = metadata.get("content_type")
        if not isinstance(storage_key, str) or not storage_key:
            raise TopicPipelineError("TOPIC_ARTIFACT_MISSING", f"主题 {kind} Artifact 缺少 storage_key")
        if not isinstance(content_type, str) or not content_type:
            raise TopicPipelineError("TOPIC_ARTIFACT_INVALID", f"主题 {kind} Artifact 缺少 content_type")
        normalized = content_type.split(";", 1)[0].strip().lower()
        if kind == "audio" and not normalized.startswith("audio/"):
            raise TopicPipelineError("TOPIC_AUDIO_INVALID", "主题音频 Artifact 不是 audio/*")
        if kind == "video" and not normalized.startswith("video/"):
            raise TopicPipelineError("TOPIC_VIDEO_INVALID", "主题视频 Artifact 不是 video/*")
        if kind == "subtitle" and normalized not in {"text/srt", "application/x-subrip", "text/plain"}:
            raise TopicPipelineError("TOPIC_SUBTITLE_INVALID", "主题字幕 Artifact 不是 SRT")
        return storage_key, normalized

    def _resolve_video_selection(
        self,
        provider_profile_id: str | None,
        visual_quality_profile_id: str | None,
    ) -> tuple[str | None, dict[str, object] | None]:
        if self._provider_registry is None:
            if provider_profile_id or visual_quality_profile_id:
                raise VisualProviderProfileError(
                    "PROVIDER_PROFILE_SELECTION_UNAVAILABLE",
                    "主题视频 Provider 选择未启用",
                )
            return None, None
        profile = self._provider_registry.resolve("video", provider_profile_id)
        self._provider_registry.ensure_configured(profile)
        quality = self._provider_registry.resolve_quality_profile(visual_quality_profile_id)
        return profile.profile_id, quality.as_snapshot()

    @staticmethod
    def _stage_for_kind(kind: GenerationTaskKind) -> StageName:
        return {
            GenerationTaskKind.AUDIO_NARRATION: StageName.AUDIO,
            GenerationTaskKind.SUBTITLE_ALIGN: StageName.SUBTITLE,
            GenerationTaskKind.VIDEO_CLIP: StageName.VIDEO_CLIP,
            GenerationTaskKind.VIDEO_ASSEMBLY: StageName.VIDEO_ASSEMBLY,
        }[kind]

    @staticmethod
    def _stage_run(task: GenerationTaskRecord, stage: StageName) -> StageRun:
        for item in task.stages:
            if item.stage == stage:
                return item
        item = StageRun(stage=stage, status=TaskStatus.CREATED)
        task.stages.append(item)
        return item

    @staticmethod
    def _decode_base64(value: str, message: str) -> bytes:
        try:
            content = base64.b64decode(value, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise TopicPipelineError("TOPIC_PROVIDER_INVALID_RESPONSE", message) from exc
        if not content:
            raise TopicPipelineError("TOPIC_PROVIDER_INVALID_RESPONSE", "Provider 返回空媒体内容")
        return content

    @staticmethod
    def _stored_artifact_metadata(stored: StoredArtifact) -> dict[str, object]:
        return {
            "storage_key": stored.storage_key,
            "content_type": stored.content_type,
            "size_bytes": stored.size_bytes,
            "sha256": stored.sha256,
        }

    @staticmethod
    def _audio_extension(mime_type: str) -> str:
        return {
            "audio/wav": ".wav",
            "audio/x-wav": ".wav",
            "audio/mpeg": ".mp3",
            "audio/mp3": ".mp3",
            "audio/ogg": ".ogg",
            "audio/mp4": ".m4a",
            "audio/aac": ".aac",
        }.get(mime_type.split(";", 1)[0].strip().lower(), ".audio")

    @staticmethod
    def _video_extension(mime_type: str) -> str:
        return ".webm" if mime_type.split(";", 1)[0].strip().lower() == "video/webm" else ".mp4"

    @staticmethod
    def _positive_float(value: object) -> float:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return 0.0
        return parsed if parsed > 0 else 0.0

    @staticmethod
    def _to_subtitle_cue(cue: object) -> SubtitleCue:
        data = cue.model_dump() if hasattr(cue, "model_dump") else cue
        if not isinstance(data, dict):
            raise TopicPipelineError("TOPIC_SUBTITLE_INVALID", "字幕 Provider 返回了非法 cue")
        return SubtitleCue(
            start_seconds=float(data["start_seconds"]),
            end_seconds=float(data["end_seconds"]),
            text=str(data["text"]),
        )

    @staticmethod
    def _error_code(exc: Exception, kind: GenerationTaskKind) -> str:
        if isinstance(exc, TopicPipelineError):
            return exc.code
        if isinstance(exc, (TTSProviderError, AudioArtifactValidationError)):
            return exc.code
        if isinstance(exc, (SubtitleAlignmentProviderError, VideoProviderError)):
            return exc.code
        if isinstance(exc, ProviderProfileError):
            return exc.code
        if isinstance(exc, (FFmpegRenderError, StorageError)):
            return exc.code
        return {
            GenerationTaskKind.AUDIO_NARRATION: "TOPIC_AUDIO_GENERATION_FAILED",
            GenerationTaskKind.SUBTITLE_ALIGN: "TOPIC_SUBTITLE_GENERATION_FAILED",
            GenerationTaskKind.VIDEO_CLIP: "TOPIC_VIDEO_GENERATION_FAILED",
            GenerationTaskKind.VIDEO_ASSEMBLY: "TOPIC_ASSEMBLY_FAILED",
        }.get(kind, "TOPIC_MEDIA_TASK_FAILED")


class TopicProductionOrchestrator:
    """Advance the topic DAG after each task and reconcile it after restarts."""

    def __init__(
        self,
        store: ProjectTaskStore,
        media: TopicMediaTaskService,
        script_task_creator: Callable[..., Awaitable[tuple[GenerationTaskRecord, bool]]] | None = None,
    ) -> None:
        import asyncio

        self._store = store
        self._media = media
        self._script_task_creator = script_task_creator
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def start(
        self,
        project_id: UUID,
        request: TopicProductionCreateRequest | None = None,
        idempotency_key: str | None = None,
    ) -> TopicProductionResponse:
        project = await self._store.get_project(project_id)
        if project is None:
            raise TopicPipelineError("PROJECT_NOT_FOUND", "主题项目不存在")
        plan = (request or TopicProductionCreateRequest()).model_dump(mode="json")
        run_id = self._run_id(project_id, idempotency_key)
        existing = await self._run_tasks(project_id, run_id)
        if existing:
            current = await self._response(project_id, run_id, existing)
            if current.status not in {"active", "blocked"}:
                return current
            script_task = await self._script_task_for_run(
                project_id,
                str(run_id),
                existing[0],
            )
            if script_task is None or script_task.status != TaskStatus.SUCCEEDED:
                return current
            plan = self._plan_from(
                next(
                    (
                        task.input_data.get(TOPIC_RUN_PLAN)
                        for task in existing
                        if isinstance(task.input_data.get(TOPIC_RUN_PLAN), dict)
                    ),
                    plan,
                )
            )
            return await self._advance_safely(project_id, run_id, script_task, plan)
        script_task = await self._latest_script_task(project_id, run_id)
        if script_task is None:
            if self._script_task_creator is None:
                return TopicProductionResponse(
                    project_id=project_id,
                    run_id=run_id,
                    status="blocked",
                    stage=GenerationTaskKind.INFO_SCRIPT.value,
                    task_ids=[],
                    auto_advance=False,
                    message="主题结构化脚本任务创建器尚未配置。",
                )
            script_task, _ = await self._script_task_creator(
                project_id,
                f"topic-dag:{run_id}:script",
                {
                    TOPIC_PIPELINE: True,
                    TOPIC_RUN_ID: str(run_id),
                    TOPIC_RUN_PLAN: plan,
                },
            )
        elif script_task.input_data.get(TOPIC_PIPELINE) is not True:
            script_task.input_data.update(
                self._markers(run_id, plan, script_task_id=script_task.id)
            )
            await self._store.update_task(script_task)
        project.status = ProjectStatus.GENERATING
        project.updated_at = utc_now()
        await self._store.update_project(project)
        if script_task.status != TaskStatus.SUCCEEDED:
            return await self._response(project_id, run_id, await self._run_tasks(project_id, run_id))
        return await self._advance_safely(project_id, run_id, script_task, plan)

    async def on_task_finished(self, task_id: UUID) -> TopicProductionResponse | None:
        task = await self._store.get_task(task_id)
        if task is None or task.status != TaskStatus.SUCCEEDED:
            return None
        if task.kind == GenerationTaskKind.INFO_SCRIPT:
            if task.input_data.get(TOPIC_PIPELINE) is not True:
                # A regular /generations request is intentionally script-only;
                # opting into the media DAG happens through the topic Run API.
                return None
            run_id = task.input_data.get(TOPIC_RUN_ID)
            if not run_id:
                return None
            plan = self._plan_from(task.input_data.get(TOPIC_RUN_PLAN))
            return await self._advance_safely(task.project_id, UUID(str(run_id)), task, plan)
        if task.input_data.get(TOPIC_PIPELINE) is not True:
            return None
        raw_run_id = task.input_data.get(TOPIC_RUN_ID)
        if not raw_run_id:
            return None
        plan = self._plan_from(task.input_data.get(TOPIC_RUN_PLAN))
        script_task = await self._script_task_for_run(task.project_id, str(raw_run_id), task)
        if script_task is None:
            return None
        return await self._advance_safely(
            task.project_id,
            UUID(str(raw_run_id)),
            script_task,
            plan,
        )

    async def tick_all(self) -> list[TopicProductionResponse]:
        tasks = await self._store.list_tasks(limit=5000)
        candidates: dict[tuple[UUID, str], GenerationTaskRecord] = {}
        for task in tasks:
            raw_run_id = task.input_data.get(TOPIC_RUN_ID)
            if task.input_data.get(TOPIC_PIPELINE) is True and raw_run_id:
                candidates[(task.project_id, str(raw_run_id))] = task
        results: list[TopicProductionResponse] = []
        for (project_id, raw_run_id), task in candidates.items():
            run_id = UUID(raw_run_id)
            plan = self._plan_from(task.input_data.get(TOPIC_RUN_PLAN))
            script_task = task if task.kind == GenerationTaskKind.INFO_SCRIPT else await self._script_task_for_run(project_id, raw_run_id, task)
            if script_task is None or script_task.status != TaskStatus.SUCCEEDED:
                continue
            result = await self._advance_safely(project_id, run_id, script_task, plan)
            if result is not None:
                results.append(result)
        return results

    async def on_task_failed(self, task_id: UUID) -> TopicProductionResponse | None:
        """Persist a topic Run failure after a Worker or API task attempt ends."""

        task = await self._store.get_task(task_id)
        if task is None or task.status != TaskStatus.FAILED:
            return None
        if task.input_data.get(TOPIC_PIPELINE) is not True:
            return None
        raw_run_id = task.input_data.get(TOPIC_RUN_ID)
        if not raw_run_id:
            return None
        run_tasks = await self._run_tasks(task.project_id, UUID(str(raw_run_id)))
        status = "blocked" if task.input_data.get("auto_retry_pending") is True else "failed"
        await self._set_run_status(
            run_tasks,
            status,
            error_code=task.error.code if task.error else None,
            error_message=task.error.message if task.error else None,
        )
        return await self._response(task.project_id, UUID(str(raw_run_id)), await self._run_tasks(task.project_id, UUID(str(raw_run_id))))

    async def get_run(
        self,
        project_id: UUID,
        run_id: UUID,
    ) -> TopicProductionResponse:
        """Reconstruct a topic Run from its durable task markers."""

        if await self._store.get_project(project_id) is None:
            raise TopicPipelineError("PROJECT_NOT_FOUND", "主题项目不存在")
        tasks = await self._run_tasks(project_id, run_id)
        if not tasks:
            raise TopicPipelineError("TOPIC_RUN_NOT_FOUND", "主题媒体 Run 不存在")
        return await self._response(project_id, run_id, tasks)

    async def get_latest_run(self, project_id: UUID) -> TopicProductionResponse:
        """Return the most recently updated topic media Run for a project."""

        if await self._store.get_project(project_id) is None:
            raise TopicPipelineError("PROJECT_NOT_FOUND", "主题项目不存在")
        tasks = await self._store.list_tasks(project_id=project_id, limit=5000)
        groups: dict[str, list[GenerationTaskRecord]] = defaultdict(list)
        for task in tasks:
            if task.input_data.get(TOPIC_PIPELINE) is not True:
                continue
            raw_run_id = task.input_data.get(TOPIC_RUN_ID)
            if raw_run_id:
                groups[str(raw_run_id)].append(task)
        if not groups:
            raise TopicPipelineError("TOPIC_RUN_NOT_FOUND", "主题媒体 Run 不存在")
        raw_run_id, run_tasks = max(
            groups.items(),
            key=lambda item: max(task.updated_at for task in item[1]),
        )
        return await self._response(project_id, UUID(raw_run_id), run_tasks)

    async def _advance(
        self,
        project_id: UUID,
        run_id: UUID,
        script_task: GenerationTaskRecord,
        plan: dict[str, Any],
    ) -> TopicProductionResponse | None:
        lock = self._locks[str(run_id)]
        if lock.locked():
            return None
        async with lock:
            project = await self._store.get_project(project_id)
            if project is None:
                return None
            run_tasks = await self._run_tasks(project_id, run_id)
            if not run_tasks:
                return None
            if any(task.status == TaskStatus.FAILED for task in run_tasks):
                return await self._response(project_id, run_id, run_tasks)
            if topic_run_status_from_tasks(run_tasks) == "completed":
                return await self._response(project_id, run_id, run_tasks)
            await self._set_run_status(run_tasks, "active")
            script = self._script_content(script_task)
            project.status = ProjectStatus.GENERATING
            project.updated_at = utc_now()
            await self._store.update_project(project)
            text = join_continuous_narration_units(scene.voiceover for scene in script.scenes)
            run_tasks = await self._run_tasks(project_id, run_id)
            by_kind = self._latest_by_kind(run_tasks)

            audio_task = by_kind.get(GenerationTaskKind.AUDIO_NARRATION)
            if bool(plan.get("include_narration", True)):
                if audio_task is None:
                    audio_task, _ = await self._media.create_audio_task(
                        project_id,
                        text,
                        run_id,
                        script_task.id,
                        f"topic-dag:{run_id}:audio:{script_task.id}",
                        run_plan=plan,
                    )
                    return await self._response(project_id, run_id, await self._run_tasks(project_id, run_id))
                if audio_task.status != TaskStatus.SUCCEEDED:
                    return await self._response(project_id, run_id, run_tasks)

            audio_artifact = (
                TopicMediaTaskService._find_artifact(audio_task, "audio_narration")
                if audio_task
                else None
            )
            subtitle_task = by_kind.get(GenerationTaskKind.SUBTITLE_ALIGN)
            if bool(plan.get("include_subtitles", True)):
                if audio_task is None or audio_artifact is None:
                    return await self._response(project_id, run_id, run_tasks)
                if subtitle_task is None:
                    subtitle_task, _ = await self._media.create_subtitle_task(
                        project_id,
                        run_id,
                        script_task.id,
                        audio_task.id,
                        text,
                        project.language,
                        f"topic-dag:{run_id}:subtitle:{audio_task.id}",
                        run_plan=plan,
                    )
                    return await self._response(project_id, run_id, await self._run_tasks(project_id, run_id))
                if subtitle_task.status != TaskStatus.SUCCEEDED:
                    return await self._response(project_id, run_id, run_tasks)

            subtitle_artifact = (
                TopicMediaTaskService._find_artifact(subtitle_task, "subtitle_srt")
                if subtitle_task
                else None
            )
            clip_tasks = self._scene_tasks(run_tasks)
            if bool(plan.get("include_video", True)):
                for scene in script.scenes:
                    scene_task = clip_tasks.get(scene.scene_index)
                    if scene_task is None:
                        scene_task, _ = await self._media.create_video_task(
                            project_id,
                            run_id,
                            script_task.id,
                            scene.scene_index,
                            scene.duration_seconds,
                            self._scene_prompt(project, script, scene),
                            provider_profile_id=plan.get("video_provider_profile_id"),
                            visual_quality_profile_id=plan.get("visual_quality_profile_id"),
                            idempotency_key=f"topic-dag:{run_id}:video:{script_task.id}:{scene.scene_index}",
                            run_plan=plan,
                        )
                run_tasks = await self._run_tasks(project_id, run_id)
                clip_tasks = self._scene_tasks(run_tasks)
                if any(task.status != TaskStatus.SUCCEEDED for task in clip_tasks.values()) or len(clip_tasks) < len(script.scenes):
                    return await self._response(project_id, run_id, run_tasks)
            else:
                clip_tasks = {}

            assembly_task = self._latest_by_kind(run_tasks).get(GenerationTaskKind.VIDEO_ASSEMBLY)
            if bool(plan.get("include_assembly", True)):
                if not clip_tasks:
                    return await self._response(project_id, run_id, run_tasks)
                if assembly_task is None:
                    assembly_task, _ = await self._media.create_assembly_task(
                        project_id,
                        run_id,
                        script_task.id,
                        [clip_tasks[index].id for index in sorted(clip_tasks)],
                        audio_artifact.id if audio_artifact else None,
                        subtitle_artifact.id if subtitle_artifact else None,
                        f"topic-dag:{run_id}:assembly:{script_task.id}",
                        run_plan=plan,
                    )
                    return await self._response(project_id, run_id, await self._run_tasks(project_id, run_id))
                if assembly_task.status != TaskStatus.SUCCEEDED:
                    return await self._response(project_id, run_id, run_tasks)

            await self._set_run_status(
                await self._run_tasks(project_id, run_id),
                "completed",
            )
            project.status = ProjectStatus.READY
            project.updated_at = utc_now()
            await self._store.update_project(project)
            return await self._response(project_id, run_id, await self._run_tasks(project_id, run_id))

    async def _advance_safely(
        self,
        project_id: UUID,
        run_id: UUID,
        script_task: GenerationTaskRecord,
        plan: dict[str, Any],
    ) -> TopicProductionResponse:
        """Advance a Run without allowing a scheduler exception to disappear."""

        try:
            result = await self._advance(project_id, run_id, script_task, plan)
            if result is not None:
                return result
        except Exception as exc:
            tasks = await self._run_tasks(project_id, run_id)
            await self._set_run_status(
                tasks,
                self._orchestration_error_status(exc),
                error_code=self._error_code_from_exception(exc),
                error_message=self._error_message_from_exception(exc),
            )
        return await self._response(project_id, run_id, await self._run_tasks(project_id, run_id))

    async def _set_run_status(
        self,
        tasks: list[GenerationTaskRecord],
        status: str,
        *,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        """Write a versioned status marker to every task in the topic Run."""

        if not tasks:
            return
        current_status = topic_run_status_from_tasks(tasks)
        current_revision = max(
            (topic_control_revision(task.input_data) for task in tasks),
            default=0,
        )
        existing_error_code = next(
            (
                str(task.input_data[TOPIC_RUN_ERROR_CODE])
                for task in tasks
                if task.input_data.get(TOPIC_RUN_ERROR_CODE)
            ),
            None,
        )
        existing_error_message = next(
            (
                str(task.input_data[TOPIC_RUN_ERROR_MESSAGE])
                for task in tasks
                if task.input_data.get(TOPIC_RUN_ERROR_MESSAGE)
            ),
            None,
        )
        changed = (
            current_status != status
            or existing_error_code != error_code
            or existing_error_message != error_message
        )
        revision = current_revision + 1 if changed else current_revision
        for task in tasks:
            latest = await self._store.get_task(task.id)
            if latest is None:
                continue
            latest.input_data[TOPIC_RUN_STATUS] = status
            latest.input_data[TOPIC_RUN_CONTROL_REVISION] = revision
            if error_code:
                latest.input_data[TOPIC_RUN_ERROR_CODE] = error_code
            else:
                latest.input_data.pop(TOPIC_RUN_ERROR_CODE, None)
            if error_message:
                latest.input_data[TOPIC_RUN_ERROR_MESSAGE] = error_message[:300]
            else:
                latest.input_data.pop(TOPIC_RUN_ERROR_MESSAGE, None)
            await self._store.update_task(latest)

    @staticmethod
    def _orchestration_error_status(exc: Exception) -> str:
        if isinstance(exc, ProviderProfileError):
            return "blocked"
        if isinstance(exc, TopicPipelineError) and exc.code not in {
            "TOPIC_SCRIPT_INVALID",
            "TOPIC_SCRIPT_ARTIFACT_MISSING",
        }:
            return "blocked"
        return "failed"

    @staticmethod
    def _error_code_from_exception(exc: Exception) -> str:
        if isinstance(exc, (TopicPipelineError, ProviderProfileError)):
            return exc.code
        return "TOPIC_DAG_ADVANCEMENT_FAILED"

    @staticmethod
    def _error_message_from_exception(exc: Exception) -> str:
        message = getattr(exc, "message", None)
        return str(message or exc)[:300]

    async def _response(
        self,
        project_id: UUID,
        run_id: UUID,
        tasks: list[GenerationTaskRecord],
    ) -> TopicProductionResponse:
        if not tasks:
            return TopicProductionResponse(
                project_id=project_id,
                run_id=run_id,
                status="blocked",
                stage=GenerationTaskKind.INFO_SCRIPT.value,
                task_ids=[],
                auto_advance=False,
                message="主题媒体 Run 尚未创建任务。",
            )
        failed_tasks = [task for task in tasks if task.status == TaskStatus.FAILED]
        if failed_tasks:
            status = (
                "blocked"
                if any(task.input_data.get("auto_retry_pending") is True for task in failed_tasks)
                else "failed"
            )
        elif any(task.status in _ACTIVE_STATUSES for task in tasks):
            status = "active"
        else:
            marker_status = topic_run_status_from_tasks(tasks)
            status = marker_status if marker_status in {"blocked", "failed", "completed"} else "completed"
        latest = max(tasks, key=lambda item: item.updated_at)
        error_code = next(
            (
                str(task.input_data[TOPIC_RUN_ERROR_CODE])
                for task in tasks
                if task.input_data.get(TOPIC_RUN_ERROR_CODE)
            ),
            None,
        )
        error_message = next(
            (
                str(task.input_data[TOPIC_RUN_ERROR_MESSAGE])
                for task in tasks
                if task.input_data.get(TOPIC_RUN_ERROR_MESSAGE)
            ),
            None,
        )
        if failed_tasks:
            failed_task = max(failed_tasks, key=lambda item: item.updated_at)
            error_code = error_code or (failed_task.error.code if failed_task.error else None)
            error_message = error_message or (
                failed_task.error.message if failed_task.error else None
            )
        message = {
            "active": "主题短视频 Run 正在由 Worker 按依赖推进。",
            "failed": "主题短视频 Run 存在失败任务，请在任务中心重试。",
            "completed": "主题短视频已完成当前配置启用的全部媒体阶段。",
            "blocked": "主题短视频 Run 等待脚本或媒体依赖。",
            "paused": "主题短视频 Run 已暂停。",
            "canceled": "主题短视频 Run 已取消。",
        }[status]
        return TopicProductionResponse(
            project_id=project_id,
            run_id=run_id,
            status=status,
            stage=latest.kind.value,
            task_ids=[task.id for task in tasks],
            auto_advance=status in {"active", "blocked"},
            message=message,
            error_code=error_code,
            error_message=error_message,
        )

    async def _latest_script_task(
        self,
        project_id: UUID,
        run_id: UUID,
    ) -> GenerationTaskRecord | None:
        tasks = await self._store.list_tasks(project_id=project_id, kind=GenerationTaskKind.INFO_SCRIPT.value, limit=5000)
        same_run = [
            task
            for task in tasks
            if str(task.input_data.get(TOPIC_RUN_ID)) == str(run_id)
        ]
        if same_run:
            return max(same_run, key=lambda item: item.updated_at)
        unclaimed = [
            task
            for task in tasks
            if task.input_data.get(TOPIC_PIPELINE) is not True
            and task.status == TaskStatus.SUCCEEDED
        ]
        return max(unclaimed, key=lambda item: item.updated_at) if unclaimed else None

    async def _script_task_for_run(
        self,
        project_id: UUID,
        run_id: str,
        task: GenerationTaskRecord,
    ) -> GenerationTaskRecord | None:
        raw_id = task.input_data.get(TOPIC_SCRIPT_TASK_ID)
        if raw_id:
            candidate = await self._store.get_task(UUID(str(raw_id)))
            if candidate is not None and candidate.status == TaskStatus.SUCCEEDED:
                return candidate
        candidates = await self._store.list_tasks(project_id=project_id, kind=GenerationTaskKind.INFO_SCRIPT.value, limit=5000)
        for candidate in sorted(candidates, key=lambda item: item.updated_at, reverse=True):
            if str(candidate.input_data.get(TOPIC_RUN_ID)) == run_id:
                return candidate
        # Never borrow an unrelated project's latest script: a Worker restart
        # must fail closed if a historical topic task lost its script marker.
        return None

    async def _run_tasks(self, project_id: UUID, run_id: UUID) -> list[GenerationTaskRecord]:
        tasks = await self._store.list_tasks(project_id=project_id, limit=5000)
        return [
            task
            for task in tasks
            if str(task.input_data.get(TOPIC_RUN_ID)) == str(run_id)
            and task.input_data.get(TOPIC_PIPELINE) is True
        ]

    @staticmethod
    def _latest_by_kind(tasks: Iterable[GenerationTaskRecord]) -> dict[GenerationTaskKind, GenerationTaskRecord]:
        result: dict[GenerationTaskKind, GenerationTaskRecord] = {}
        for task in tasks:
            if task.kind == GenerationTaskKind.INFO_SCRIPT:
                continue
            current = result.get(task.kind)
            if current is None or task.updated_at > current.updated_at:
                result[task.kind] = task
        return result

    @staticmethod
    def _scene_tasks(tasks: Iterable[GenerationTaskRecord]) -> dict[int, GenerationTaskRecord]:
        result: dict[int, GenerationTaskRecord] = {}
        for task in tasks:
            if task.kind != GenerationTaskKind.VIDEO_CLIP:
                continue
            try:
                scene_index = int(task.input_data[TOPIC_SCENE_INDEX])
            except (KeyError, TypeError, ValueError):
                continue
            current = result.get(scene_index)
            if current is None or task.updated_at > current.updated_at:
                result[scene_index] = task
        return result

    @staticmethod
    def _script_content(task: GenerationTaskRecord) -> ScriptContent:
        artifact = TopicMediaTaskService._find_artifact(task, "script_json")
        if artifact is None or not isinstance(artifact.preview, dict):
            raise TopicPipelineError("TOPIC_SCRIPT_ARTIFACT_MISSING", "主题脚本任务缺少可解析的 script_json Artifact")
        try:
            return ScriptContent.model_validate(artifact.preview)
        except ValueError as exc:
            raise TopicPipelineError("TOPIC_SCRIPT_INVALID", "主题脚本 Artifact 不符合结构化 Schema") from exc

    @staticmethod
    def _scene_prompt(project: ProjectRecord, script: ScriptContent, scene) -> str:
        keywords = "、".join(scene.visual_keywords)
        return (
            f"{project.language} vertical {project.aspect_ratio} information short-video scene, "
            f"topic: {project.topic}, title: {script.title}, scene {scene.scene_index}: {scene.caption}; "
            f"visual keywords: {keywords}; tone: {project.tone}; "
            "clear single visual subject, strong composition, readable focal point, "
            "one continuous shot, restrained natural motion, no text, no subtitles, no logo"
        )

    @staticmethod
    def _markers(run_id: UUID, plan: dict[str, Any], *, script_task_id: UUID) -> dict[str, object]:
        return {
            TOPIC_PIPELINE: True,
            TOPIC_RUN_ID: str(run_id),
            TOPIC_RUN_STATUS: "active",
            TOPIC_RUN_CONTROL_REVISION: 0,
            TOPIC_RUN_PLAN: plan,
            TOPIC_SCRIPT_TASK_ID: str(script_task_id),
        }

    @staticmethod
    def _plan_from(value: object) -> dict[str, Any]:
        if isinstance(value, dict):
            return dict(value)
        return TopicProductionCreateRequest().model_dump(mode="json")

    @staticmethod
    def _run_id(project_id: UUID, idempotency_key: str | None) -> UUID:
        if idempotency_key:
            return uuid5(NAMESPACE_URL, f"ai-video:topic-dag:{project_id}:{idempotency_key}")
        return uuid4()
