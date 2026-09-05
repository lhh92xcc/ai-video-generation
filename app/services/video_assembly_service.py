"""Asynchronous assembly of validated shot-level video artifacts."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from app.domain.models import (
    AudioTrackRequest,
    ArtifactSummary,
    ArtifactRecord,
    EpisodeRecord,
    GenerationTaskKind,
    GenerationTaskRecord,
    StageName,
    StageRun,
    TaskError,
    TaskStatus,
    VideoAssemblyCreateRequest,
    utc_now,
)
from app.media.audio_validation import AudioArtifactValidator, FFprobeAudioValidator
from app.providers.errors_audio import AudioArtifactValidationError
from app.rendering.ffmpeg_renderer import AudioTrackInput, FFmpegRenderError, FFmpegVideoRenderer
from app.rendering.subtitles import SubtitleCue, parse_srt
from app.queue import TaskQueue
from app.services.novel_service import EpisodeNotFoundError
from app.storage.protocol import ArtifactStorage, StorageError, StoredArtifact


class VideoAssemblyStore(Protocol):
    async def get_episode(self, episode_id: UUID) -> EpisodeRecord | None:
        ...

    async def get_task(self, task_id: UUID) -> GenerationTaskRecord | None:
        ...

    async def get_artifact(self, artifact_id: UUID) -> ArtifactRecord | None:
        ...

    async def create_task(
        self,
        task: GenerationTaskRecord,
        idempotency_key: str | None = None,
    ) -> tuple[GenerationTaskRecord, bool]:
        ...

    async def update_task(self, task: GenerationTaskRecord) -> GenerationTaskRecord:
        ...


class VideoAssemblyInputError(Exception):
    """Raised when an assembly input task cannot be used as a source clip."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class VideoAssemblyTaskService:
    def __init__(
        self,
        store: VideoAssemblyStore,
        task_queue: TaskQueue,
        artifact_storage: ArtifactStorage,
        renderer: FFmpegVideoRenderer | None = None,
        audio_validator: AudioArtifactValidator | None = None,
    ) -> None:
        self._store = store
        self._task_queue = task_queue
        self._artifact_storage = artifact_storage
        self._renderer = renderer or FFmpegVideoRenderer()
        self._audio_validator = audio_validator or FFprobeAudioValidator()

    async def create_task(
        self,
        episode_id: UUID,
        request: VideoAssemblyCreateRequest,
        idempotency_key: str | None = None,
    ) -> tuple[GenerationTaskRecord, bool]:
        episode = await self._get_episode(episode_id)
        await self._load_sources(episode, request.clip_task_ids)
        await self._validate_optional_artifacts(episode, request)

        task = GenerationTaskRecord(
            project_id=episode.project_id,
            kind=GenerationTaskKind.VIDEO_ASSEMBLY,
            input_data={
                "episode_id": str(episode_id),
                "clip_task_ids": [str(task_id) for task_id in request.clip_task_ids],
                "audio_tracks": [
                    {
                        "artifact_id": str(track.artifact_id),
                        "track_type": track.track_type,
                        "start_seconds": track.start_seconds,
                        "volume": track.volume,
                        "loop": track.loop,
                        "fade_in_seconds": track.fade_in_seconds,
                        "fade_out_seconds": track.fade_out_seconds,
                    }
                    for track in request.audio_tracks
                ],
                "subtitle_artifact_id": (
                    str(request.subtitle_artifact_id)
                    if request.subtitle_artifact_id is not None
                    else None
                ),
                "output_format": request.output_format,
            },
            status=TaskStatus.QUEUED,
            current_stage=StageName.VIDEO_ASSEMBLY,
            stages=[StageRun(stage=StageName.VIDEO_ASSEMBLY, status=TaskStatus.QUEUED)],
            updated_at=utc_now(),
        )
        task_key = (
            f"{GenerationTaskKind.VIDEO_ASSEMBLY.value}:{episode_id}:{idempotency_key}"
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
        stage = StageName.VIDEO_ASSEMBLY
        await self._mark_running(task, stage)

        try:
            input_data = task.input_data
            episode_id = UUID(str(input_data["episode_id"]))
            episode = await self._get_episode(episode_id)
            clip_task_ids = [UUID(str(value)) for value in input_data["clip_task_ids"]]
            sources = await self._load_sources(episode, clip_task_ids)
            audio_track_requests = [
                AudioTrackRequest.model_validate(value)
                for value in input_data.get("audio_tracks", [])
            ]
            audio_tracks = await self._load_audio_tracks(episode, audio_track_requests)
            subtitle_artifact_id = input_data.get("subtitle_artifact_id")
            subtitles = await self._load_subtitles(
                episode,
                UUID(str(subtitle_artifact_id)) if subtitle_artifact_id else None,
            )

            clip_contents: list[bytes] = []
            content_types: list[str] = []
            for source_task, artifact, storage_key, content_type in sources:
                del source_task, artifact
                clip_contents.append(await self._artifact_storage.get_bytes(storage_key))
                content_types.append(content_type)
            await self._set_progress(task_id, 35)

            rendered = await self._renderer.render(
                clip_contents,
                content_types,
                audio_tracks=audio_tracks,
                subtitles=subtitles,
            )
            await self._set_progress(task_id, 80)
            stored_artifact = await self._artifact_storage.put_bytes(
                f"episode-videos/{episode_id}/{task.id}.mp4",
                rendered.content,
                rendered.content_type,
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
            saved_task.artifacts.append(
                ArtifactSummary(
                    type="rendered_video",
                    provider="ffmpeg",
                    metadata={
                        "episode_id": str(episode_id),
                        "clip_task_ids": [str(value) for value in clip_task_ids],
                        "output_format": input_data.get("output_format", "mp4"),
                        "audio_tracks": input_data.get("audio_tracks", []),
                        "subtitle_artifact_id": subtitle_artifact_id,
                        "duration_ms": round(rendered.probe.duration_seconds * 1000),
                        **rendered.as_metadata(),
                        **self._stored_artifact_metadata(stored_artifact),
                        "output_uri": stored_artifact.uri,
                    },
                    preview={
                        "episode_id": str(episode_id),
                        "output_uri": stored_artifact.uri,
                        "clip_task_ids": [str(value) for value in clip_task_ids],
                        "audio_track_count": rendered.audio_track_count,
                        "subtitle_count": rendered.subtitle_count,
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

    async def _get_episode(self, episode_id: UUID) -> EpisodeRecord:
        episode = await self._store.get_episode(episode_id)
        if episode is None:
            raise EpisodeNotFoundError
        return episode

    async def _get_task(self, task_id: UUID) -> GenerationTaskRecord:
        task = await self._store.get_task(task_id)
        if task is None:
            raise RuntimeError(f"Task {task_id} was not found")
        return task

    async def _load_sources(
        self,
        episode: EpisodeRecord,
        clip_task_ids: list[UUID],
    ) -> list[tuple[GenerationTaskRecord, ArtifactSummary, str, str]]:
        sources: list[tuple[GenerationTaskRecord, ArtifactSummary, str, str]] = []
        for clip_task_id in clip_task_ids:
            source_task = await self._store.get_task(clip_task_id)
            if source_task is None:
                raise VideoAssemblyInputError(
                    "VIDEO_ASSEMBLY_SOURCE_NOT_FOUND",
                    f"Source video clip task {clip_task_id} was not found",
                )
            if source_task.project_id != episode.project_id:
                raise VideoAssemblyInputError(
                    "VIDEO_ASSEMBLY_SOURCE_INVALID",
                    "All source video clip tasks must belong to the episode project",
                )
            if source_task.kind not in {
                GenerationTaskKind.VIDEO_CLIP,
                GenerationTaskKind.LIP_SYNC,
            }:
                raise VideoAssemblyInputError(
                    "VIDEO_ASSEMBLY_SOURCE_INVALID",
                    f"Task {clip_task_id} is not a video clip or lip-sync task",
                )
            if source_task.input_data.get("episode_id") != str(episode.id):
                raise VideoAssemblyInputError(
                    "VIDEO_ASSEMBLY_SOURCE_INVALID",
                    f"Task {clip_task_id} does not belong to episode {episode.id}",
                )
            if source_task.status != TaskStatus.SUCCEEDED:
                raise VideoAssemblyInputError(
                    "VIDEO_ASSEMBLY_SOURCE_NOT_READY",
                    f"Source video clip task {clip_task_id} has not succeeded",
                )

            expected_artifact_type = (
                "lip_synced_video"
                if source_task.kind == GenerationTaskKind.LIP_SYNC
                else "video_clip"
            )
            artifact = next(
                (item for item in source_task.artifacts if item.type == expected_artifact_type),
                None,
            )
            if artifact is None:
                raise VideoAssemblyInputError(
                    "VIDEO_ASSEMBLY_ARTIFACT_MISSING",
                    f"Source task {clip_task_id} has no {expected_artifact_type} Artifact",
                )
            storage_key = artifact.metadata.get("storage_key")
            content_type = artifact.metadata.get("content_type")
            if not isinstance(storage_key, str) or not storage_key:
                raise VideoAssemblyInputError(
                    "VIDEO_ASSEMBLY_ARTIFACT_MISSING",
                    f"Source task {clip_task_id} has no Artifact storage_key",
                )
            if not isinstance(content_type, str) or not content_type.startswith("video/"):
                raise VideoAssemblyInputError(
                    "VIDEO_ASSEMBLY_SOURCE_INVALID",
                    f"Source task {clip_task_id} has an invalid video content type",
                )
            sources.append((source_task, artifact, storage_key, content_type))
        return sources

    async def _validate_optional_artifacts(
        self,
        episode: EpisodeRecord,
        request: VideoAssemblyCreateRequest,
    ) -> None:
        for track in request.audio_tracks:
            artifact = await self._get_audio_artifact(episode, track.artifact_id)
            del artifact
        if request.subtitle_artifact_id is not None:
            artifact = await self._store.get_artifact(request.subtitle_artifact_id)
            self._validate_artifact_project(episode, artifact, "subtitle")
            if artifact is None or artifact.type != "subtitle_srt":
                raise VideoAssemblyInputError(
                    "VIDEO_ASSEMBLY_SUBTITLE_INVALID",
                    f"Artifact {request.subtitle_artifact_id} is not a subtitle_srt Artifact",
                )
            self._require_storage_metadata(
                artifact,
                request.subtitle_artifact_id,
                "subtitle",
                ("text/srt", "application/x-subrip", "text/plain"),
            )

    async def _load_audio_tracks(
        self,
        episode: EpisodeRecord,
        requests: list[AudioTrackRequest],
    ) -> list[AudioTrackInput]:
        tracks: list[AudioTrackInput] = []
        for request in requests:
            artifact = await self._get_audio_artifact(episode, request.artifact_id)
            storage_key, content_type = self._require_storage_metadata(
                artifact,
                request.artifact_id,
                "audio",
                None,
            )
            content = await self._artifact_storage.get_bytes(storage_key)
            try:
                await self._audio_validator.validate_bytes(content, content_type)
            except AudioArtifactValidationError as exc:
                raise VideoAssemblyInputError(
                    "VIDEO_ASSEMBLY_AUDIO_INVALID",
                    f"Audio Artifact {request.artifact_id} failed validation: {exc.message}",
                ) from exc
            tracks.append(
                AudioTrackInput(
                    content=content,
                    content_type=content_type,
                    track_type=request.track_type,
                    start_seconds=request.start_seconds,
                    volume=request.volume,
                    loop=request.loop,
                    fade_in_seconds=request.fade_in_seconds,
                    fade_out_seconds=request.fade_out_seconds,
                )
            )
        return tracks

    async def _load_subtitles(
        self,
        episode: EpisodeRecord,
        artifact_id: UUID | None,
    ) -> tuple[SubtitleCue, ...]:
        if artifact_id is None:
            return ()
        artifact = await self._store.get_artifact(artifact_id)
        self._validate_artifact_project(episode, artifact, "subtitle")
        if artifact is None or artifact.type != "subtitle_srt":
            raise VideoAssemblyInputError(
                "VIDEO_ASSEMBLY_SUBTITLE_INVALID",
                f"Artifact {artifact_id} is not a subtitle_srt Artifact",
            )
        storage_key, content_type = self._require_storage_metadata(
            artifact,
            artifact_id,
            "subtitle",
            ("text/srt", "application/x-subrip", "text/plain"),
        )
        if artifact.metadata.get("size_bytes", 0) > 2_000_000:
            raise VideoAssemblyInputError(
                "VIDEO_ASSEMBLY_SUBTITLE_INVALID",
                "Subtitle Artifact must not exceed 2 MB",
            )
        content = await self._artifact_storage.get_bytes(storage_key)
        del content_type
        try:
            return parse_srt(content)
        except ValueError as exc:
            raise VideoAssemblyInputError(
                "VIDEO_ASSEMBLY_SUBTITLE_INVALID",
                f"Subtitle Artifact {artifact_id} is invalid: {exc}",
            ) from exc

    async def _get_audio_artifact(
        self,
        episode: EpisodeRecord,
        artifact_id: UUID,
    ) -> ArtifactRecord:
        artifact = await self._store.get_artifact(artifact_id)
        self._validate_artifact_project(episode, artifact, "audio")
        if artifact is None or artifact.type not in {"audio_narration", "audio_bgm"}:
            raise VideoAssemblyInputError(
                "VIDEO_ASSEMBLY_AUDIO_INVALID",
                f"Artifact {artifact_id} is not an audio_narration or audio_bgm Artifact",
            )
        self._require_storage_metadata(artifact, artifact_id, "audio", None)
        return artifact

    @staticmethod
    def _validate_artifact_project(
        episode: EpisodeRecord,
        artifact: ArtifactRecord | None,
        artifact_kind: str,
    ) -> None:
        if artifact is None:
            raise VideoAssemblyInputError(
                f"VIDEO_ASSEMBLY_{artifact_kind.upper()}_ARTIFACT_NOT_FOUND",
                f"{artifact_kind.capitalize()} Artifact was not found",
            )
        if artifact.project_id != episode.project_id:
            raise VideoAssemblyInputError(
                f"VIDEO_ASSEMBLY_{artifact_kind.upper()}_ARTIFACT_INVALID",
                f"{artifact_kind.capitalize()} Artifact must belong to the episode project",
            )

    @staticmethod
    def _require_storage_metadata(
        artifact: ArtifactRecord,
        artifact_id: UUID,
        artifact_kind: str,
        allowed_content_types: tuple[str, ...] | None,
    ) -> tuple[str, str]:
        storage_key = artifact.metadata.get("storage_key")
        content_type = artifact.metadata.get("content_type")
        if not isinstance(storage_key, str) or not storage_key:
            raise VideoAssemblyInputError(
                f"VIDEO_ASSEMBLY_{artifact_kind.upper()}_ARTIFACT_MISSING",
                f"{artifact_kind.capitalize()} Artifact {artifact_id} has no storage_key",
            )
        if not isinstance(content_type, str) or not content_type:
            raise VideoAssemblyInputError(
                f"VIDEO_ASSEMBLY_{artifact_kind.upper()}_ARTIFACT_INVALID",
                f"{artifact_kind.capitalize()} Artifact {artifact_id} has no content type",
            )
        normalized = content_type.split(";", 1)[0].strip().lower()
        if artifact_kind == "audio" and not normalized.startswith("audio/"):
            raise VideoAssemblyInputError(
                "VIDEO_ASSEMBLY_AUDIO_INVALID",
                f"Audio Artifact {artifact_id} must use an audio MIME type",
            )
        if allowed_content_types is not None and normalized not in allowed_content_types:
            raise VideoAssemblyInputError(
                "VIDEO_ASSEMBLY_SUBTITLE_INVALID",
                f"Subtitle Artifact {artifact_id} must use an SRT-compatible MIME type",
            )
        return storage_key, normalized

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

    async def _set_progress(self, task_id: UUID, progress: int) -> None:
        task = await self._get_task(task_id)
        task.progress = progress
        stage_run = self._stage_run(task)
        stage_run.progress = progress
        task.updated_at = utc_now()
        await self._store.update_task(task)

    @staticmethod
    def _stage_run(task: GenerationTaskRecord) -> StageRun:
        for stage_run in task.stages:
            if stage_run.stage == StageName.VIDEO_ASSEMBLY:
                return stage_run
        stage_run = StageRun(stage=StageName.VIDEO_ASSEMBLY, status=TaskStatus.CREATED)
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
    def _error_code(exc: Exception) -> str:
        if isinstance(exc, VideoAssemblyInputError):
            return exc.code
        if isinstance(exc, (FFmpegRenderError, StorageError)):
            return exc.code
        if isinstance(exc, EpisodeNotFoundError):
            return "VIDEO_ASSEMBLY_EPISODE_NOT_FOUND"
        return "VIDEO_ASSEMBLY_FAILED"
