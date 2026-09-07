"""Asynchronous reference-image generation for versioned assets."""

from __future__ import annotations

import base64
import binascii
import json
from uuid import UUID, uuid4

from app.domain.models import (
    ArtifactSummary,
    AssetRecord,
    AssetStatus,
    GenerationTaskKind,
    GenerationTaskRecord,
    ReferenceImageCreateRequest,
    ReferenceImageGenerationRequest,
    ReferenceImageGenerationResult,
    ReferenceImageRecord,
    ReferenceImageStatus,
    StageName,
    StageRun,
    TaskError,
    TaskStatus,
    utc_now,
)
from app.providers.errors import ImageProviderError
from app.providers.image_generation import ImageGenerationProvider
from app.providers.profiles import VisualProviderProfileError, VisualProviderProfileRegistry
from app.media.image_validation import (
    FFprobeImageValidator,
    ImageArtifactValidationError,
    ImageArtifactValidator,
)
from app.media.visual_prompts import (
    DEFAULT_REFERENCE_NEGATIVE_PROMPT,
    DEFAULT_REFERENCE_STYLE,
    strengthen_reference_prompt,
)
from app.queue import TaskQueue
from app.repositories.protocol import NovelStore
from app.storage.protocol import ArtifactStorage, StorageError, StoredArtifact


class ReferenceImageNotFoundError(Exception):
    """Raised when a reference-image record does not exist."""


class AssetNotReadyError(Exception):
    """Raised when generation is requested for a non-production asset version."""


class ReferenceImageTaskService:
    def __init__(
        self,
        store: NovelStore,
        task_queue: TaskQueue,
        provider: ImageGenerationProvider,
        artifact_storage: ArtifactStorage,
        default_width: int = 1024,
        default_height: int = 1024,
        identity_provider: ImageGenerationProvider | None = None,
        default_style: str = DEFAULT_REFERENCE_STYLE,
        default_negative_prompt: str = DEFAULT_REFERENCE_NEGATIVE_PROMPT,
        provider_registry: VisualProviderProfileRegistry | None = None,
        image_validator: ImageArtifactValidator | None = None,
    ) -> None:
        self._store = store
        self._task_queue = task_queue
        self._provider = provider
        self._artifact_storage = artifact_storage
        self._default_width = default_width
        self._default_height = default_height
        self._identity_provider = identity_provider
        self._default_style = default_style.strip() or DEFAULT_REFERENCE_STYLE
        self._default_negative_prompt = (
            default_negative_prompt.strip() or DEFAULT_REFERENCE_NEGATIVE_PROMPT
        )
        self._provider_registry = provider_registry
        self._image_validator = image_validator or FFprobeImageValidator()

    def supports_identity_variants(self, provider_profile_id: str | None = None) -> bool:
        """Return whether the selected image route can consume an identity anchor."""

        if self._provider_registry is not None:
            profile = self._provider_registry.resolve("image", provider_profile_id)
            return profile.provider == "comfyui"
        return self._identity_provider is not None

    async def create_task(
        self,
        asset_id: UUID,
        request: ReferenceImageCreateRequest,
        idempotency_key: str | None = None,
    ) -> tuple[GenerationTaskRecord, bool]:
        asset = await self._get_asset(asset_id)
        if asset.status != AssetStatus.READY:
            raise AssetNotReadyError(
                f"Asset {asset.id} is {asset.status.value}; approve the asset before generation"
            )
        if request.reference_role == "shot_keyframe":
            if asset.asset_type.value != "character":
                raise ImageProviderError(
                    "IMAGE_SHOT_KEYFRAME_REQUIRES_CHARACTER",
                    "A shot keyframe can only be generated for a character asset",
                )
            if request.episode_id is None or request.shot_index is None:
                raise ImageProviderError(
                    "IMAGE_SHOT_KEYFRAME_CONTEXT_REQUIRED",
                    "A shot keyframe requires an episode and shot index",
                )
            episode = await self._store.get_episode(request.episode_id)
            if episode is None or episode.project_id != asset.project_id:
                raise ImageProviderError(
                    "IMAGE_SHOT_KEYFRAME_CONTEXT_INVALID",
                    "The shot keyframe episode must belong to the same project as the asset",
                )

        provider_profile_id: str | None = None
        visual_quality_profile_id: str | None = None
        visual_quality_snapshot: dict[str, object] | None = None
        if self._provider_registry is not None:
            profile = self._provider_registry.resolve("image", request.provider_profile_id)
            self._provider_registry.ensure_configured(profile)
            provider_profile_id = profile.profile_id
            quality = self._provider_registry.resolve_quality_profile(
                request.visual_quality_profile_id
            )
            visual_quality_profile_id = quality.profile_id
            visual_quality_snapshot = quality.as_snapshot()
        elif request.visual_quality_profile_id is not None:
            raise VisualProviderProfileError(
                "VISUAL_QUALITY_SELECTION_UNAVAILABLE",
                "Visual quality profile selection is not enabled for this service",
            )
        elif request.provider_profile_id is not None:
            raise VisualProviderProfileError(
                "PROVIDER_PROFILE_SELECTION_UNAVAILABLE",
                "Image Provider profile selection is not enabled for this service",
            )

        identity_reference_image_id, identity_anchor_reference_image_id = (
            await self._resolve_identity_reference(asset, request)
        )
        if identity_reference_image_id is not None:
            identity_route_available = (
                self.supports_identity_variants(provider_profile_id)
                if self._provider_registry is not None
                else self._identity_provider is not None
            )
            if not identity_route_available:
                raise ImageProviderError(
                    "IMAGE_IDENTITY_PROVIDER_NOT_CONFIGURED",
                    "An identity-locked character image requires a configured identity image Provider",
                )
        width = request.width or (
            int(visual_quality_snapshot["image_width"])
            if visual_quality_snapshot is not None
            else self._default_width
        )
        height = request.height or (
            int(visual_quality_snapshot["image_height"])
            if visual_quality_snapshot is not None
            else self._default_height
        )
        style = request.style or self._default_style
        negative_prompt = request.negative_prompt or self._default_negative_prompt
        prompt = strengthen_reference_prompt(
            request.prompt_override or self._build_prompt(asset, style),
            max_chars=2000,
        )
        reference_image_id = uuid4()
        task_id = uuid4()
        task_reference_role = (
            "shot_keyframe"
            if request.reference_role == "shot_keyframe"
            else "identity_locked_variant"
            if identity_reference_image_id is not None
            else "candidate_reference"
        )
        task = GenerationTaskRecord(
            id=task_id,
            project_id=asset.project_id,
            kind=GenerationTaskKind.ASSET_REFERENCE_IMAGE,
            input_data={
                "asset_id": str(asset.id),
                "asset_key": str(asset.asset_key),
                "asset_version": asset.version,
                "reference_image_id": str(reference_image_id),
                "width": width,
                "height": height,
                **(
                    {
                        "visual_quality_profile_id": visual_quality_profile_id,
                        "visual_quality_profile": visual_quality_snapshot,
                    }
                    if visual_quality_profile_id is not None
                    else {}
                ),
                **(
                    {"identity_reference_image_id": str(identity_reference_image_id)}
                    if identity_reference_image_id is not None
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
                "reference_role": task_reference_role,
                "generation_attempt": 1,
                **(
                    {
                        "episode_id": str(request.episode_id),
                        "shot_index": request.shot_index,
                    }
                    if request.reference_role == "shot_keyframe"
                    else {}
                ),
                **(
                    {"provider_profile_id": provider_profile_id}
                    if provider_profile_id is not None
                    else {}
                ),
            },
            status=TaskStatus.QUEUED,
            current_stage=StageName.REFERENCE_IMAGE,
            stages=[StageRun(stage=StageName.REFERENCE_IMAGE, status=TaskStatus.QUEUED)],
            updated_at=utc_now(),
        )
        stored_task, reused = await self._store.create_task(
            task,
            f"{GenerationTaskKind.ASSET_REFERENCE_IMAGE.value}:{idempotency_key}"
            if idempotency_key
            else None,
        )
        if reused:
            return stored_task, True

        await self._store.save_reference_image(
            ReferenceImageRecord(
                id=reference_image_id,
                task_id=stored_task.id,
                project_id=asset.project_id,
                asset_id=asset.id,
                asset_key=asset.asset_key,
                asset_type=asset.asset_type,
                asset_version=asset.version,
                prompt=prompt,
                negative_prompt=negative_prompt,
                status=ReferenceImageStatus.QUEUED,
                width=width,
                height=height,
            )
        )
        await self._task_queue.enqueue(stored_task.id)
        return stored_task, False

    async def list_for_asset(self, asset_id: UUID) -> list[ReferenceImageRecord]:
        await self._get_asset(asset_id)
        return await self._store.list_reference_images(asset_id)

    async def get(self, reference_image_id: UUID) -> ReferenceImageRecord:
        image = await self._store.get_reference_image(reference_image_id)
        if image is None:
            raise ReferenceImageNotFoundError
        return image

    async def run_task(self, task_id: UUID) -> None:
        task = await self._get_task(task_id)
        stage = StageName.REFERENCE_IMAGE
        reference_image = await self.get(UUID(str(task.input_data["reference_image_id"])))
        await self._mark_running(task, stage, reference_image)

        try:
            identity_image_bytes = None
            identity_image_mime_type = None
            identity_reference_image_id = task.input_data.get("identity_reference_image_id")
            if identity_reference_image_id:
                identity_reference_image = await self._store.get_reference_image(
                    UUID(str(identity_reference_image_id))
                )
                if identity_reference_image is None:
                    raise ImageProviderError(
                        "IMAGE_IDENTITY_REFERENCE_NOT_FOUND",
                        "The identity reference image was not found",
                    )
                if identity_reference_image.status != ReferenceImageStatus.SUCCEEDED:
                    raise ImageProviderError(
                        "IMAGE_IDENTITY_REFERENCE_NOT_READY",
                        "The identity reference image must finish successfully first",
                    )
                identity_storage_key = identity_reference_image.metadata.get("storage_key")
                if not isinstance(identity_storage_key, str) or not identity_storage_key:
                    raise ImageProviderError(
                        "IMAGE_IDENTITY_REFERENCE_NOT_READABLE",
                        "The identity reference image has no readable Artifact",
                    )
                identity_image_bytes = await self._artifact_storage.get_bytes(identity_storage_key)
                identity_image_mime_type = str(
                    identity_reference_image.metadata.get("content_type", "image/png")
                )
            provider = self._provider
            identity_provider = self._identity_provider
            selected_profile_id = task.input_data.get("provider_profile_id")
            quality_profile_id = task.input_data.get("visual_quality_profile_id")
            quality_snapshot = task.input_data.get("visual_quality_profile")
            quality_settings = None
            if self._provider_registry is not None:
                quality_settings = self._provider_registry.settings_for_quality(
                    str(quality_profile_id) if quality_profile_id else None,
                    quality_snapshot,
                )
                provider = await self._provider_registry.get_image_provider(
                    str(selected_profile_id) if selected_profile_id else None,
                    quality_profile_id=str(quality_profile_id) if quality_profile_id else None,
                    quality_snapshot=quality_snapshot,
                )
                if identity_reference_image_id is not None:
                    identity_provider = await self._provider_registry.get_identity_image_provider(
                        str(selected_profile_id) if selected_profile_id else None,
                        quality_profile_id=str(quality_profile_id) if quality_profile_id else None,
                        quality_snapshot=quality_snapshot,
                    )
            if identity_reference_image_id is not None:
                if identity_provider is None:
                    raise ImageProviderError(
                        "IMAGE_IDENTITY_PROVIDER_NOT_CONFIGURED",
                        "An identity-locked character image requires a configured identity image Provider",
                    )
                provider = identity_provider
            stage_run = self._stage_run(task)
            result = await provider.generate_reference_image(
                ReferenceImageGenerationRequest(
                    asset_id=reference_image.asset_id,
                    asset_key=reference_image.asset_key,
                    asset_type=reference_image.asset_type,
                    asset_version=reference_image.asset_version,
                    prompt=reference_image.prompt,
                    negative_prompt=reference_image.negative_prompt,
                    width=reference_image.width,
                    height=reference_image.height,
                    steps=quality_settings.image_steps if quality_settings is not None else None,
                    guidance=quality_settings.image_guidance if quality_settings is not None else None,
                    identity_weight=(
                        quality_settings.image_identity_weight
                        if quality_settings is not None
                        else None
                    ),
                    generation_attempt=max(1, stage_run.attempt),
                    identity_image_bytes=identity_image_bytes,
                    identity_image_mime_type=identity_image_mime_type,
                )
            )
            finished_at = utc_now()
            stored_artifact = await self._store_generated_image(reference_image, result)
            is_identity_anchor = await self._mark_identity_metadata(
                reference_image,
                task,
                stored_artifact,
            )
            identity_metadata = dict(reference_image.metadata)
            reference_image.status = ReferenceImageStatus.SUCCEEDED
            reference_image.provider = result.provider
            reference_image.model = result.model
            reference_image.output_uri = stored_artifact.uri if stored_artifact else result.output_uri
            reference_image.width = result.width
            reference_image.height = result.height
            reference_image.duration_ms = result.duration_ms
            reference_image.metadata = {
                **result.metadata,
                **identity_metadata,
                **self._stored_artifact_metadata(stored_artifact),
            }
            reference_image.updated_at = finished_at
            await self._store.save_reference_image(reference_image)

            task = await self._get_task(task_id)
            task.status = TaskStatus.SUCCEEDED
            task.current_stage = None
            task.progress = 100
            task.updated_at = finished_at
            stage_run = self._stage_run(task)
            stage_run.status = TaskStatus.SUCCEEDED
            stage_run.progress = 100
            stage_run.finished_at = finished_at
            task.artifacts.append(
                ArtifactSummary(
                    type="reference_image",
                    provider=result.provider,
                    metadata={
                        "model": result.model,
                        "duration_ms": result.duration_ms,
                        "asset_key": str(reference_image.asset_key),
                        "asset_version": reference_image.asset_version,
                        "output_uri": reference_image.output_uri,
                        "identity_anchor": is_identity_anchor,
                        "provider_profile_id": task.input_data.get("provider_profile_id"),
                        "visual_quality_profile_id": task.input_data.get(
                            "visual_quality_profile_id"
                        ),
                        "visual_quality_profile": task.input_data.get(
                            "visual_quality_profile"
                        ),
                        "reference_role": reference_image.metadata.get(
                            "reference_role", "candidate_reference"
                        ),
                        **reference_image.metadata,
                    },
                    preview={
                        "reference_image_id": str(reference_image.id),
                        "output_uri": reference_image.output_uri,
                        "asset_id": str(reference_image.asset_id),
                    },
                )
            )
            await self._store.update_task(task)
        except Exception as exc:
            failed_at = utc_now()
            error_code = self._error_code(exc)
            reference_image.status = ReferenceImageStatus.FAILED
            reference_image.error = TaskError(code=error_code, message=str(exc) or error_code)
            reference_image.updated_at = failed_at
            await self._store.save_reference_image(reference_image)

            task = await self._get_task(task_id)
            task.status = TaskStatus.FAILED
            task.current_stage = stage
            task.updated_at = failed_at
            task.error = TaskError(code=error_code, message=str(exc) or error_code)
            stage_run = self._stage_run(task)
            stage_run.status = TaskStatus.FAILED
            stage_run.error_code = error_code
            stage_run.finished_at = failed_at
            await self._store.update_task(task)

    async def _resolve_identity_reference(
        self,
        asset: AssetRecord,
        request: ReferenceImageCreateRequest,
    ) -> tuple[UUID | None, UUID | None]:
        """Choose the standard character image unless the caller supplied one."""

        if asset.asset_type.value != "character":
            return None, None

        if not request.identity_lock:
            return None, None

        candidate_id = request.identity_reference_image_id
        if candidate_id is None:
            anchor = await self._find_identity_anchor(asset.id)
            if anchor is None:
                return None, None
            candidate_id = anchor.id

        candidate = await self._store.get_reference_image(candidate_id)
        if candidate is None:
            raise ImageProviderError(
                "IMAGE_IDENTITY_REFERENCE_NOT_FOUND",
                "The identity reference image was not found",
            )
        if candidate.project_id != asset.project_id or candidate.asset_key != asset.asset_key:
            raise ImageProviderError(
                "IMAGE_IDENTITY_REFERENCE_INVALID",
                "The identity reference image must belong to the same character asset",
            )
        if candidate.status != ReferenceImageStatus.SUCCEEDED:
            raise ImageProviderError(
                "IMAGE_IDENTITY_REFERENCE_NOT_READY",
                "The identity reference image must finish successfully first",
            )
        if not candidate.metadata.get("storage_key"):
            raise ImageProviderError(
                "IMAGE_IDENTITY_REFERENCE_NOT_READABLE",
                "The identity reference image has no readable Artifact",
            )
        anchor_id = self._metadata_uuid(candidate.metadata.get("identity_anchor_reference_image_id"))
        if anchor_id is None and candidate.metadata.get("identity_anchor") is True:
            anchor_id = candidate.id
        if anchor_id is None:
            anchor = await self._find_identity_anchor(asset.id)
            anchor_id = anchor.id if anchor is not None else candidate.id
        return candidate_id, anchor_id

    async def _find_identity_anchor(self, asset_id: UUID) -> ReferenceImageRecord | None:
        images = await self._store.list_reference_images(asset_id)
        successful = [
            image
            for image in images
            if image.status == ReferenceImageStatus.SUCCEEDED
            and isinstance(image.metadata.get("storage_key"), str)
            and image.metadata.get("storage_key")
        ]
        explicit = next(
            (image for image in successful if image.metadata.get("identity_anchor") is True),
            None,
        )
        if explicit is not None:
            return explicit
        legacy = next(
            (
                image
                for image in sorted(successful, key=lambda item: item.created_at)
                if image.metadata.get("reference_role")
                not in {"identity_locked_variant", "shot_keyframe"}
            ),
            None,
        )
        if legacy is not None:
            legacy.metadata = {
                **legacy.metadata,
                "identity_anchor": True,
                "reference_role": "standard_identity",
                "identity_anchor_reference_image_id": str(legacy.id),
            }
            await self._store.save_reference_image(legacy)
        return legacy

    async def _mark_identity_metadata(
        self,
        reference_image: ReferenceImageRecord,
        task: GenerationTaskRecord,
        stored_artifact: StoredArtifact | None,
    ) -> bool:
        """Mark the first successful character image as the stable identity anchor."""

        del stored_artifact
        metadata = dict(reference_image.metadata)
        identity_reference_id = task.input_data.get("identity_reference_image_id")
        existing = await self._store.list_reference_images(reference_image.asset_id)
        prior_successes = [
            image
            for image in existing
            if image.id != reference_image.id
            and image.status == ReferenceImageStatus.SUCCEEDED
            and isinstance(image.metadata.get("storage_key"), str)
            and image.metadata.get("storage_key")
        ]
        is_character = reference_image.asset_type.value == "character"
        requested_role = str(task.input_data.get("reference_role", "candidate_reference"))
        is_shot_keyframe = requested_role == "shot_keyframe"
        is_anchor = (
            is_character
            and not is_shot_keyframe
            and not identity_reference_id
            and not prior_successes
        )
        anchor_id = self._metadata_uuid(task.input_data.get("identity_anchor_reference_image_id"))
        if is_anchor:
            anchor_id = reference_image.id
        metadata.update(
            {
                "identity_anchor": is_anchor,
                "reference_role": (
                    "shot_keyframe"
                    if is_shot_keyframe
                    else "standard_identity"
                    if is_anchor
                    else "identity_locked_variant"
                    if identity_reference_id
                    else "candidate_reference"
                ),
                **(
                    {
                        "episode_id": str(task.input_data["episode_id"]),
                        "shot_index": int(task.input_data["shot_index"]),
                    }
                    if is_shot_keyframe
                    and task.input_data.get("episode_id")
                    and task.input_data.get("shot_index") is not None
                    else {}
                ),
                **(
                    {"identity_anchor_reference_image_id": str(anchor_id)}
                    if anchor_id is not None
                    else {}
                ),
            }
        )
        reference_image.metadata = metadata
        return is_anchor

    @staticmethod
    def _metadata_uuid(value: object) -> UUID | None:
        if not value:
            return None
        try:
            return UUID(str(value))
        except (ValueError, TypeError, AttributeError):
            return None

    async def _get_asset(self, asset_id: UUID) -> AssetRecord:
        asset = await self._store.get_asset(asset_id)
        if asset is None:
            from app.services.asset_service import AssetNotFoundError

            raise AssetNotFoundError
        return asset

    async def _get_task(self, task_id: UUID) -> GenerationTaskRecord:
        task = await self._store.get_task(task_id)
        if task is None:
            raise RuntimeError(f"Task {task_id} was not found")
        return task

    async def _mark_running(
        self,
        task: GenerationTaskRecord,
        stage: StageName,
        reference_image: ReferenceImageRecord,
    ) -> None:
        now = utc_now()
        task.status = TaskStatus.RUNNING
        task.current_stage = stage
        task.progress = 10
        task.updated_at = now
        stage_run = self._stage_run(task)
        stage_run.status = TaskStatus.RUNNING
        stage_run.progress = 10
        stage_run.started_at = now
        reference_image.status = ReferenceImageStatus.RUNNING
        reference_image.error = None
        reference_image.updated_at = now
        await self._store.update_task(task)
        await self._store.save_reference_image(reference_image)

    @staticmethod
    def _stage_run(task: GenerationTaskRecord) -> StageRun:
        for stage_run in task.stages:
            if stage_run.stage == StageName.REFERENCE_IMAGE:
                return stage_run
        stage_run = StageRun(stage=StageName.REFERENCE_IMAGE, status=TaskStatus.CREATED)
        task.stages.append(stage_run)
        return stage_run

    @staticmethod
    def _build_prompt(asset: AssetRecord, style: str) -> str:
        content = json.dumps(asset.content.model_dump(mode="json"), ensure_ascii=False)
        composition = {
            "character": (
                "one identity-anchor portrait, exactly one person, head and shoulders, "
                "front-facing eye-level camera, neutral expression and pose, unobstructed face, "
                "no props, simple quiet background, consistent neutral lighting"
            ),
            "location": (
                "one coherent vertical establishing plate, stable architectural layout, "
                "clear foreground/midground/background separation, no people"
            ),
            "prop": (
                "one product-style object plate, exactly one main object, fully visible, "
                "clear silhouette, readable material and distinctive markings"
            ),
        }.get(asset.asset_type.value, "one coherent vertical composition")
        prompt = (
            f"{style.strip()}. Production reference asset for {asset.asset_type.value} "
            f'"{asset.name}". {composition}. '
            "This image is the canonical design anchor for every later shot: preserve "
            "the same face geometry, hairstyle, clothing, colors, proportions, materials "
            "and layout. Use a clean intentional composition, no action sequence, no "
            "alternate views, no story text. Structured asset facts: "
            f"{content[:1200]}"
        )
        return prompt[:2000]

    async def _store_generated_image(
        self,
        reference_image: ReferenceImageRecord,
        result: ReferenceImageGenerationResult,
    ) -> StoredArtifact | None:
        image_base64 = getattr(result, "image_base64", None)
        output_uri = getattr(result, "output_uri", None)
        if image_base64:
            try:
                content = base64.b64decode(image_base64, validate=True)
            except (ValueError, binascii.Error) as exc:
                raise ImageProviderError(
                    "IMAGE_PROVIDER_INVALID_RESPONSE",
                    "Image provider returned invalid base64 content",
                ) from exc
            if result.provider == "comfyui" or result.metadata.get("local") is True:
                probe = await self._image_validator.validate_bytes(
                    content,
                    getattr(result, "mime_type", "image/png"),
                )
                if probe.width != reference_image.width or probe.height != reference_image.height:
                    raise ImageProviderError(
                        "IMAGE_ARTIFACT_DIMENSION_MISMATCH",
                        "ComfyUI reference image dimensions do not match the selected quality profile",
                    )
                result.metadata = {
                    **result.metadata,
                    "image_quality": {
                        "status": "passed",
                        "method": "ffprobe_dimensions_v1",
                        "expected_width": reference_image.width,
                        "expected_height": reference_image.height,
                        **probe.as_metadata(),
                    },
                }
            extension = self._extension_for_mime(getattr(result, "mime_type", "image/png"))
            storage_key = (
                f"reference-images/{reference_image.asset_key}/"
                f"v{reference_image.asset_version}/{reference_image.id}{extension}"
            )
            return await self._artifact_storage.put_bytes(
                storage_key,
                content,
                getattr(result, "mime_type", "image/png"),
            )
        if output_uri:
            return None
        raise ImageProviderError(
            "IMAGE_PROVIDER_INVALID_RESPONSE",
            "Image provider returned neither image content nor an output URI",
        )

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
            "image/jpeg": ".jpg",
            "image/webp": ".webp",
            "image/png": ".png",
        }.get(mime_type, ".bin")

    @staticmethod
    def _error_code(exc: Exception) -> str:
        if isinstance(exc, ImageProviderError):
            return exc.code
        if isinstance(exc, StorageError):
            return exc.code
        if isinstance(exc, ImageArtifactValidationError):
            return exc.code
        return "REFERENCE_IMAGE_GENERATION_FAILED"
