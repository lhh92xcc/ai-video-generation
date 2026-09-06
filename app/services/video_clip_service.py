"""Asynchronous shot-level video clip generation for the L3 sample factory."""

from __future__ import annotations

import base64
import binascii
from typing import Any
from uuid import UUID, uuid4

from app.domain.models import (
    ArtifactSummary,
    AssetStatus,
    AssetType,
    GenerationTaskKind,
    GenerationTaskRecord,
    ShotAssetReference,
    ReferenceImageStatus,
    ReferenceImageRecord,
    StageName,
    StageRun,
    TaskError,
    TaskStatus,
    VideoClipCreateRequest,
    VideoClipGenerationRequest,
    VideoClipGenerationResult,
    utc_now,
)
from app.media.video_validation import FFprobeVideoValidator, VideoArtifactValidator
from app.media.identity_audit import IdentityAuditProvider
from app.media.visual_prompts import (
    DEFAULT_VIDEO_NEGATIVE_PROMPT,
    DEFAULT_VIDEO_PROMPT_SUFFIX,
)
from app.providers.errors_video import VideoProviderError
from app.providers.video_generation import VideoGenerationProvider
from app.queue import TaskQueue
from app.repositories.protocol import NovelStore
from app.services.novel_service import (
    EpisodeNotFoundError,
    ShotListNotFoundError,
)
from app.storage.protocol import ArtifactStorage, StorageError, StoredArtifact


class ShotNotFoundError(Exception):
    """Raised when a requested shot index does not exist in the latest shot list."""


class VideoClipAssetGateError(Exception):
    """Raised when a shot is not ready for video generation."""


class VideoClipTaskService:
    def __init__(
        self,
        store: NovelStore,
        task_queue: TaskQueue,
        provider: VideoGenerationProvider,
        artifact_storage: ArtifactStorage,
        video_validator: VideoArtifactValidator | None = None,
        identity_auditor: IdentityAuditProvider | None = None,
        default_negative_prompt: str = DEFAULT_VIDEO_NEGATIVE_PROMPT,
        prompt_suffix: str = DEFAULT_VIDEO_PROMPT_SUFFIX,
    ) -> None:
        self._store = store
        self._task_queue = task_queue
        self._provider = provider
        self._artifact_storage = artifact_storage
        self._video_validator = video_validator or FFprobeVideoValidator()
        self._identity_auditor = identity_auditor
        self._default_negative_prompt = (
            default_negative_prompt.strip() or DEFAULT_VIDEO_NEGATIVE_PROMPT
        )
        self._prompt_suffix = prompt_suffix.strip()

    async def create_task(
        self,
        episode_id: UUID,
        shot_index: int,
        request: VideoClipCreateRequest,
        idempotency_key: str | None = None,
    ) -> tuple[GenerationTaskRecord, bool]:
        episode = await self._store.get_episode(episode_id)
        if episode is None:
            raise EpisodeNotFoundError
        shot_list = await self._store.get_latest_shot_list(episode_id)
        if shot_list is None:
            raise ShotListNotFoundError
        if shot_index < 1 or shot_index > len(shot_list.shots):
            raise ShotNotFoundError

        shot = shot_list.shots[shot_index - 1]
        if (
            shot.unresolved_asset_requirements
            or shot.asset_binding_warnings
            or any(reference.status != AssetStatus.READY for reference in shot.asset_refs)
        ):
            raise VideoClipAssetGateError(
                "Shot assets must be uniquely bound and ready before video generation"
            )

        reference_image_id = request.reference_image_id
        identity_anchor_reference_image_id: UUID | None = None
        if reference_image_id is None:
            reference_image_id, identity_anchor_reference_image_id = (
                await self._select_default_reference_image(shot_list.project_id, shot.asset_refs)
            )

        reference_image = None
        if reference_image_id is not None:
            reference_image = await self._store.get_reference_image(reference_image_id)
            if reference_image is None or reference_image.project_id != shot_list.project_id:
                raise VideoClipAssetGateError(
                    "The selected reference image does not belong to this project"
                )
            if reference_image.status != ReferenceImageStatus.SUCCEEDED:
                raise VideoClipAssetGateError(
                    "The selected reference image must finish successfully before video generation"
                )
            if not reference_image.metadata.get("storage_key"):
                raise VideoClipAssetGateError(
                    "The selected reference image has no locally readable Artifact"
                )
            if any(item.asset_type == AssetType.CHARACTER for item in shot.asset_refs) and (
                reference_image.asset_type != AssetType.CHARACTER
            ):
                raise VideoClipAssetGateError(
                    "A shot with a character must use a character reference image"
                )
            if identity_anchor_reference_image_id is None:
                identity_anchor_reference_image_id = self._identity_anchor_id(reference_image)

        source_prompt = request.prompt_override or shot.visual_prompt
        prompt = self._compose_prompt(source_prompt)
        negative_prompt = request.negative_prompt or self._default_negative_prompt
        task = GenerationTaskRecord(
            id=uuid4(),
            project_id=shot_list.project_id,
            kind=GenerationTaskKind.VIDEO_CLIP,
            input_data={
                "episode_id": str(episode_id),
                "shot_list_id": str(shot_list.id),
                "shot_index": shot_index,
                "duration_seconds": shot.duration_seconds,
                "prompt": prompt,
                "source_prompt": source_prompt,
                "negative_prompt": negative_prompt,
                **(
                    {"reference_image_id": str(reference_image_id)}
                    if reference_image_id is not None
                    else {}
                ),
                **(
                    {
                        "identity_anchor_reference_image_id": str(
                            identity_anchor_reference_image_id
                        )
                    }
                    if identity_anchor_reference_image_id is not None
                    else {}
                ),
                "asset_refs": [
                    reference.model_dump(mode="json") for reference in shot.asset_refs
                ],
                "generation_attempt": 1,
            },
            status=TaskStatus.QUEUED,
            current_stage=StageName.VIDEO_CLIP,
            stages=[StageRun(stage=StageName.VIDEO_CLIP, status=TaskStatus.QUEUED)],
            updated_at=utc_now(),
        )
        task_key = (
            f"{GenerationTaskKind.VIDEO_CLIP.value}:{episode_id}:{shot_index}:{idempotency_key}"
            if idempotency_key
            else None
        )
        stored_task, reused = await self._store.create_task(task, task_key)
        if reused:
            return stored_task, True

        await self._task_queue.enqueue(stored_task.id)
        return stored_task, False

    def _compose_prompt(self, source_prompt: str) -> str:
        """Add a short motion/continuity guardrail without changing source text."""

        source_prompt = source_prompt.strip()
        if not self._prompt_suffix:
            return source_prompt[:2000]
        separator = " " if source_prompt.endswith((".", "。", "！", "？")) else ". "
        return f"{source_prompt}{separator}{self._prompt_suffix}"[:2000]

    async def run_task(self, task_id: UUID) -> None:
        task = await self._get_task(task_id)
        stage = StageName.VIDEO_CLIP
        await self._mark_running(task, stage)

        try:
            input_data = task.input_data
            keyframe_bytes = None
            keyframe_mime_type = None
            reference_image_id = input_data.get("reference_image_id")
            if reference_image_id:
                reference_image = await self._store.get_reference_image(UUID(str(reference_image_id)))
                if reference_image is None:
                    raise VideoProviderError(
                        "VIDEO_KEYFRAME_NOT_FOUND",
                        "The selected reference image was not found",
                    )
                storage_key = reference_image.metadata.get("storage_key")
                if not isinstance(storage_key, str) or not storage_key:
                    raise VideoProviderError(
                        "VIDEO_KEYFRAME_NOT_FOUND",
                        "The selected reference image is not stored as a local Artifact",
                    )
                keyframe_bytes = await self._artifact_storage.get_bytes(storage_key)
                keyframe_mime_type = str(
                    reference_image.metadata.get("content_type", "image/png")
                )
            identity_reference_bytes = keyframe_bytes
            identity_reference_mime_type = keyframe_mime_type
            identity_anchor_id = input_data.get("identity_anchor_reference_image_id")
            if identity_anchor_id:
                anchor = await self._store.get_reference_image(UUID(str(identity_anchor_id)))
                if anchor is not None:
                    anchor_storage_key = anchor.metadata.get("storage_key")
                    if isinstance(anchor_storage_key, str) and anchor_storage_key:
                        identity_reference_bytes = await self._artifact_storage.get_bytes(
                            anchor_storage_key
                        )
                        identity_reference_mime_type = str(
                            anchor.metadata.get("content_type", "image/png")
                        )
            generation_attempt = max(1, int(input_data.get("generation_attempt", 1)))
            result = await self._provider.generate_video_clip(
                VideoClipGenerationRequest(
                    episode_id=UUID(str(input_data["episode_id"])),
                    shot_list_id=UUID(str(input_data["shot_list_id"])),
                    shot_index=int(input_data["shot_index"]),
                    duration_seconds=int(input_data["duration_seconds"]),
                    prompt=str(input_data["prompt"]),
                    negative_prompt=str(input_data["negative_prompt"]),
                    asset_refs=[
                        ShotAssetReference.model_validate(item)
                        for item in input_data.get("asset_refs", [])
                    ],
                    keyframe_bytes=keyframe_bytes,
                    keyframe_mime_type=keyframe_mime_type,
                    generation_attempt=generation_attempt,
                )
            )
            stored_artifact = await self._store_generated_video(task, result, generation_attempt)
            result.metadata = {
                **result.metadata,
                "generation_attempt": generation_attempt,
                "identity_audit": await self._audit_identity(
                    input_data,
                    identity_reference_bytes,
                    identity_reference_mime_type,
                    stored_artifact,
                ),
            }
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
            output_uri = stored_artifact.uri if stored_artifact else result.output_uri
            saved_task.artifacts.append(
                ArtifactSummary(
                    type="video_clip",
                    provider=result.provider,
                    metadata={
                        "model": result.model,
                        "duration_ms": result.duration_ms,
                        "duration_seconds": result.duration_seconds,
                        "episode_id": input_data["episode_id"],
                        "shot_list_id": input_data["shot_list_id"],
                        "shot_index": input_data["shot_index"],
                        **result.metadata,
                        **self._stored_artifact_metadata(stored_artifact),
                        "output_uri": output_uri,
                    },
                    preview={
                        "episode_id": input_data["episode_id"],
                        "shot_index": input_data["shot_index"],
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
            if stage_run.stage == StageName.VIDEO_CLIP:
                return stage_run
        stage_run = StageRun(stage=StageName.VIDEO_CLIP, status=TaskStatus.CREATED)
        task.stages.append(stage_run)
        return stage_run

    async def _store_generated_video(
        self,
        task: GenerationTaskRecord,
        result: VideoClipGenerationResult,
        generation_attempt: int = 1,
    ) -> StoredArtifact | None:
        if result.video_base64:
            if not result.mime_type.startswith("video/"):
                raise VideoProviderError(
                    "VIDEO_PROVIDER_INVALID_RESPONSE",
                    "Video provider returned a non-video MIME type",
                )
            try:
                content = base64.b64decode(result.video_base64, validate=True)
            except (ValueError, binascii.Error) as exc:
                raise VideoProviderError(
                    "VIDEO_PROVIDER_INVALID_RESPONSE",
                    "Video provider returned invalid base64 content",
                ) from exc
            if not content:
                raise VideoProviderError(
                    "VIDEO_PROVIDER_INVALID_RESPONSE",
                    "Video provider returned empty video content",
                )
            probe_result = await self._video_validator.validate_bytes(content, result.mime_type)
            result.metadata = {
                **result.metadata,
                "ffprobe": probe_result.as_metadata(),
            }
            input_data = task.input_data
            extension = self._extension_for_mime(result.mime_type)
            storage_key = (
                f"video-clips/{input_data['episode_id']}/"
                f"shot-{input_data['shot_index']}/"
                f"{task.id}-attempt-{generation_attempt}{extension}"
            )
            return await self._artifact_storage.put_bytes(
                storage_key,
                content,
                result.mime_type,
            )
        if result.output_uri:
            return None
        raise VideoProviderError(
            "VIDEO_PROVIDER_INVALID_RESPONSE",
            "Video provider returned neither video content nor an output URI",
        )

    async def _select_default_reference_image(
        self,
        project_id: UUID,
        asset_refs: list[ShotAssetReference],
    ) -> tuple[UUID | None, UUID | None]:
        assets = await self._store.list_assets(project_id)
        ordered_asset_refs = sorted(
            asset_refs,
            key=lambda item: 0 if item.asset_type == AssetType.CHARACTER else 1,
        )
        for asset_ref in ordered_asset_refs:
            asset = next(
                (
                    item
                    for item in assets
                    if item.asset_key == asset_ref.asset_key
                    and item.version == asset_ref.version
                    and item.asset_type == asset_ref.asset_type
                ),
                None,
            )
            if asset is None:
                continue
            images = await self._store.list_reference_images(asset.id)
            successful = [
                image
                for image in images
                if image.status == ReferenceImageStatus.SUCCEEDED
                and isinstance(image.metadata.get("storage_key"), str)
                and image.metadata.get("storage_key")
            ]
            if not successful:
                continue
            anchor = next(
                (image for image in successful if image.metadata.get("identity_anchor") is True),
                None,
            )
            if anchor is None and asset.asset_type == AssetType.CHARACTER:
                legacy_candidates = [
                    image
                    for image in sorted(successful, key=lambda item: item.created_at)
                    if image.metadata.get("reference_role") != "identity_locked_variant"
                ]
                anchor = legacy_candidates[0] if legacy_candidates else None
            selected = anchor or successful[0]
            return selected.id, anchor.id if anchor is not None else None
        return None, None

    @staticmethod
    def _identity_anchor_id(reference_image: ReferenceImageRecord) -> UUID | None:
        if reference_image.metadata.get("identity_anchor") is True:
            return reference_image.id
        value = reference_image.metadata.get("identity_anchor_reference_image_id")
        if not value:
            return None
        try:
            return UUID(str(value))
        except (ValueError, TypeError, AttributeError):
            return None

    async def _audit_identity(
        self,
        input_data: dict[str, Any],
        reference_bytes: bytes | None,
        reference_mime_type: str | None,
        stored_artifact: StoredArtifact | None,
    ) -> dict[str, Any]:
        asset_refs = [
            ShotAssetReference.model_validate(item)
            for item in input_data.get("asset_refs", [])
        ]
        if not any(item.asset_type == AssetType.CHARACTER for item in asset_refs):
            return {
                "status": "not_applicable",
                "reason": "shot_has_no_character_asset",
                "human_review_required": False,
            }
        base = {
            "reference_image_id": input_data.get("reference_image_id"),
            "identity_anchor_reference_image_id": input_data.get(
                "identity_anchor_reference_image_id"
            ),
        }
        if self._identity_auditor is None:
            return {
                **base,
                "status": "unavailable",
                "reason": "identity_auditor_not_configured",
                "human_review_required": True,
            }
        if not reference_bytes or not stored_artifact:
            return {
                **base,
                "status": "unavailable",
                "reason": "identity_audit_requires_local_reference_and_video_artifacts",
                "human_review_required": True,
            }
        try:
            video_bytes = await self._artifact_storage.get_bytes(stored_artifact.storage_key)
            report = await self._identity_auditor.audit(
                reference_bytes,
                reference_mime_type or "image/png",
                video_bytes,
                stored_artifact.content_type,
            )
        except Exception as exc:
            return {
                **base,
                "status": "error",
                "reason": "identity_audit_execution_failed",
                "detail": str(exc)[:500],
                "human_review_required": True,
            }
        return {**base, **report}

    @staticmethod
    def _stored_artifact_metadata(stored_artifact: StoredArtifact | None) -> dict[str, object]:
        if stored_artifact is None:
            return {}
        return {
            "storage_key": stored_artifact.storage_key,
            "content_type": stored_artifact.content_type,
            "size_bytes": stored_artifact.size_bytes,
            "sha256": stored_artifact.sha256,
        }

    @staticmethod
    def _extension_for_mime(mime_type: str) -> str:
        return {
            "video/mp4": ".mp4",
            "video/webm": ".webm",
        }.get(mime_type, ".bin")

    @staticmethod
    def _error_code(exc: Exception) -> str:
        if isinstance(exc, VideoProviderError):
            return exc.code
        if isinstance(exc, StorageError):
            return exc.code
        return "VIDEO_CLIP_GENERATION_FAILED"
