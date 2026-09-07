from __future__ import annotations

import asyncio
import base64
import os
import subprocess
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import pytest

from app.domain.models import (
    AssetRecord,
    AssetStatus,
    AssetType,
    CharacterAssetContent,
    ReferenceImageGenerationRequest,
    ReferenceImageGenerationResult,
    ReferenceImageRecord,
    ReferenceImageStatus,
    TaskStatus,
)
from app.providers.image_generation import ImageGenerationProvider
from app.providers.errors import ImageProviderError
from app.providers.openai_compatible_image import OpenAICompatibleImageGenerationProvider
from app.providers.siliconflow_image import SiliconFlowImageGenerationProvider
from app.media.image_validation import ImageProbeResult
from app.queue import InProcessTaskQueue
from app.repositories.in_memory import InMemoryStore
from app.services.reference_image_service import ReferenceImageTaskService
from app.storage.local import LocalFileArtifactStorage
from app.storage.s3_compatible import S3CompatibleArtifactStorage


class Base64ImageProvider:
    async def generate_reference_image(
        self,
        request: ReferenceImageGenerationRequest,
    ) -> ReferenceImageGenerationResult:
        return ReferenceImageGenerationResult(
            image_base64=base64.b64encode(b"generated-image").decode("ascii"),
            mime_type="image/png",
            provider="test",
            model="test-image-v1",
            width=request.width,
            height=request.height,
            duration_ms=3,
        )


class RecordingIdentityImageProvider(Base64ImageProvider):
    def __init__(self) -> None:
        self.requests: list[ReferenceImageGenerationRequest] = []

    async def generate_reference_image(
        self,
        request: ReferenceImageGenerationRequest,
    ) -> ReferenceImageGenerationResult:
        self.requests.append(request)
        return await super().generate_reference_image(request)


class LocalComfyImageProvider(Base64ImageProvider):
    async def generate_reference_image(
        self,
        request: ReferenceImageGenerationRequest,
    ) -> ReferenceImageGenerationResult:
        return ReferenceImageGenerationResult(
            image_base64=base64.b64encode(b"generated-image").decode("ascii"),
            mime_type="image/png",
            provider="comfyui",
            model="flux-test",
            width=request.width,
            height=request.height,
            duration_ms=3,
            metadata={"local": True},
        )


class RecordingImageValidator:
    def __init__(self, width: int, height: int) -> None:
        self.width = width
        self.height = height
        self.calls = 0

    async def validate_bytes(self, content: bytes, content_type: str) -> ImageProbeResult:
        self.calls += 1
        assert content == b"generated-image"
        assert content_type == "image/png"
        return ImageProbeResult(
            width=self.width,
            height=self.height,
            codec_name="png",
            format_name="png_pipe",
        )


def test_reference_image_task_stores_base64_output_and_metadata(tmp_path) -> None:
    async def exercise() -> None:
        store = InMemoryStore()
        queue = InProcessTaskQueue()
        storage = LocalFileArtifactStorage(tmp_path)
        provider: ImageGenerationProvider = Base64ImageProvider()
        service = ReferenceImageTaskService(store, queue, provider, storage)
        queue.set_handler(service.run_task)
        asset = await store.save_asset_version(
            AssetRecord(
                project_id=uuid4(),
                story_bible_id=uuid4(),
                asset_type=AssetType.CHARACTER,
                name="主角",
                status=AssetStatus.READY,
                content=CharacterAssetContent(
                    role="protagonist",
                    traits=["坚定"],
                    appearance="黑发、深色外套",
                ),
                provider="test",
                model="test",
                duration_ms=0,
            )
        )

        task, reused = await service.create_task(asset.id, request=_request())
        await queue.close()
        assert reused is False
        assert (await store.get_task(task.id)).status.value == "succeeded"
        images = await store.list_reference_images(asset.id)
        assert len(images) == 1
        assert images[0].status == ReferenceImageStatus.SUCCEEDED
        assert images[0].output_uri.startswith("local://")
        assert images[0].metadata["size_bytes"] == len(b"generated-image")
        assert (tmp_path / images[0].metadata["storage_key"]).read_bytes() == b"generated-image"
        await storage.close()

    asyncio.run(exercise())


def test_local_comfy_reference_requires_actual_quality_dimensions(tmp_path) -> None:
    async def exercise() -> None:
        store = InMemoryStore()
        queue = InProcessTaskQueue()
        storage = LocalFileArtifactStorage(tmp_path)
        validator = RecordingImageValidator(width=432, height=768)
        service = ReferenceImageTaskService(
            store,
            queue,
            LocalComfyImageProvider(),
            storage,
            image_validator=validator,
        )
        queue.set_handler(service.run_task)
        asset = await store.save_asset_version(
            AssetRecord(
                project_id=uuid4(),
                story_bible_id=uuid4(),
                asset_type=AssetType.CHARACTER,
                name="质量门禁角色",
                status=AssetStatus.READY,
                content=CharacterAssetContent(
                    role="protagonist",
                    traits=["稳定"],
                    appearance="黑发、深色外套",
                ),
                provider="test",
                model="test",
                duration_ms=0,
            )
        )

        task, _ = await service.create_task(asset.id, _request(width=432, height=768))
        await queue.close()

        saved_task = await store.get_task(task.id)
        assert saved_task is not None
        assert saved_task.status == TaskStatus.SUCCEEDED
        image = (await store.list_reference_images(asset.id))[0]
        assert image.metadata["image_quality"]["status"] == "passed"
        assert validator.calls == 1
        await storage.close()

    asyncio.run(exercise())


def test_local_comfy_reference_dimension_mismatch_fails_before_anchor(tmp_path) -> None:
    async def exercise() -> None:
        store = InMemoryStore()
        queue = InProcessTaskQueue()
        storage = LocalFileArtifactStorage(tmp_path)
        service = ReferenceImageTaskService(
            store,
            queue,
            LocalComfyImageProvider(),
            storage,
            image_validator=RecordingImageValidator(width=512, height=512),
        )
        queue.set_handler(service.run_task)
        asset = await store.save_asset_version(
            AssetRecord(
                project_id=uuid4(),
                story_bible_id=uuid4(),
                asset_type=AssetType.CHARACTER,
                name="错误画幅角色",
                status=AssetStatus.READY,
                content=CharacterAssetContent(
                    role="protagonist",
                    traits=["待筛选"],
                    appearance="短发、灰色外套",
                ),
                provider="test",
                model="test",
                duration_ms=0,
            )
        )

        task, _ = await service.create_task(asset.id, _request(width=432, height=768))
        await queue.close()

        saved_task = await store.get_task(task.id)
        assert saved_task is not None
        assert saved_task.status == TaskStatus.FAILED
        assert saved_task.error is not None
        assert saved_task.error.code == "IMAGE_ARTIFACT_DIMENSION_MISMATCH"
        image = (await store.list_reference_images(asset.id))[0]
        assert image.status == ReferenceImageStatus.FAILED
        assert image.metadata.get("identity_anchor") is None
        await storage.close()

    asyncio.run(exercise())


def test_first_successful_character_image_becomes_anchor_and_later_image_uses_it(tmp_path) -> None:
    async def exercise() -> None:
        store = InMemoryStore()
        queue = InProcessTaskQueue()
        storage = LocalFileArtifactStorage(tmp_path)
        base_provider = Base64ImageProvider()
        identity_provider = RecordingIdentityImageProvider()
        service = ReferenceImageTaskService(
            store,
            queue,
            base_provider,
            storage,
            identity_provider=identity_provider,
        )
        queue.set_handler(service.run_task)
        asset = await store.save_asset_version(
            AssetRecord(
                project_id=uuid4(),
                story_bible_id=uuid4(),
                asset_type=AssetType.CHARACTER,
                name="林默",
                status=AssetStatus.READY,
                content=CharacterAssetContent(
                    role="protagonist",
                    traits=["冷静"],
                    appearance="黑发、深色外套",
                ),
                provider="test",
                model="test",
                duration_ms=0,
            )
        )

        first_task, _ = await service.create_task(asset.id, _request())
        await queue.close()
        first_image = (await store.list_reference_images(asset.id))[0]
        assert (await store.get_task(first_task.id)).status == TaskStatus.SUCCEEDED
        assert first_image.metadata["identity_anchor"] is True
        assert first_image.metadata["reference_role"] == "standard_identity"
        assert first_image.metadata["identity_anchor_reference_image_id"] == str(first_image.id)

        second_task, _ = await service.create_task(asset.id, _request())
        assert second_task.input_data["identity_reference_image_id"] == str(first_image.id)
        assert second_task.input_data["identity_anchor_reference_image_id"] == str(first_image.id)
        assert second_task.input_data["reference_role"] == "identity_locked_variant"
        await queue.close()

        assert len(identity_provider.requests) == 1
        assert identity_provider.requests[0].identity_image_bytes == b"generated-image"
        images = await store.list_reference_images(asset.id)
        variant = next(image for image in images if image.id != first_image.id)
        assert variant.metadata["identity_anchor"] is False
        assert variant.metadata["reference_role"] == "identity_locked_variant"
        assert variant.metadata["identity_anchor_reference_image_id"] == str(first_image.id)
        await storage.close()

    asyncio.run(exercise())


def test_identity_locked_reference_requires_identity_provider(tmp_path) -> None:
    async def exercise() -> None:
        store = InMemoryStore()
        queue = InProcessTaskQueue()
        storage = LocalFileArtifactStorage(tmp_path)
        service = ReferenceImageTaskService(store, queue, Base64ImageProvider(), storage)
        queue.set_handler(service.run_task)
        asset = await store.save_asset_version(
            AssetRecord(
                project_id=uuid4(),
                story_bible_id=uuid4(),
                asset_type=AssetType.CHARACTER,
                name="需要身份 Provider 的角色",
                status=AssetStatus.READY,
                content=CharacterAssetContent(
                    role="supporting",
                    traits=["谨慎"],
                    appearance="短发、灰色风衣",
                ),
                provider="test",
                model="test",
                duration_ms=0,
            )
        )
        first_task, _ = await service.create_task(asset.id, _request())
        await queue.close()
        first_image = (await store.list_reference_images(asset.id))[0]

        with pytest.raises(ImageProviderError) as error:
            await service.create_task(
                asset.id,
                _request().model_copy(update={"identity_reference_image_id": first_image.id}),
            )
        assert error.value.code == "IMAGE_IDENTITY_PROVIDER_NOT_CONFIGURED"
        assert (await store.get_task(first_task.id)).status == TaskStatus.SUCCEEDED
        await storage.close()

    asyncio.run(exercise())


def test_legacy_character_reference_is_promoted_to_standard_identity(tmp_path) -> None:
    async def exercise() -> None:
        store = InMemoryStore()
        queue = InProcessTaskQueue()
        storage = LocalFileArtifactStorage(tmp_path)
        service = ReferenceImageTaskService(store, queue, Base64ImageProvider(), storage)
        asset = await store.save_asset_version(
            AssetRecord(
                project_id=uuid4(),
                story_bible_id=uuid4(),
                asset_type=AssetType.CHARACTER,
                name="历史角色",
                status=AssetStatus.READY,
                content=CharacterAssetContent(
                    role="supporting",
                    traits=["可靠"],
                    appearance="黑发、蓝色衬衫",
                ),
                provider="legacy",
                model="legacy-v1",
                duration_ms=0,
            )
        )
        stored = await storage.put_bytes("references/legacy.png", b"legacy-image", "image/png")
        legacy = await store.save_reference_image(
            ReferenceImageRecord(
                task_id=uuid4(),
                project_id=asset.project_id,
                asset_id=asset.id,
                asset_key=asset.asset_key,
                asset_type=asset.asset_type,
                asset_version=asset.version,
                prompt="旧参考图",
                negative_prompt="模糊",
                status=ReferenceImageStatus.SUCCEEDED,
                output_uri=stored.uri,
                width=512,
                height=512,
                metadata={"storage_key": stored.storage_key, "content_type": "image/png"},
            )
        )

        anchor = await service._find_identity_anchor(asset.id)

        assert anchor is not None
        assert anchor.id == legacy.id
        refreshed = await store.get_reference_image(legacy.id)
        assert refreshed is not None
        assert refreshed.metadata["identity_anchor"] is True
        assert refreshed.metadata["reference_role"] == "standard_identity"
        assert refreshed.metadata["identity_anchor_reference_image_id"] == str(legacy.id)
        await storage.close()

    asyncio.run(exercise())


@pytest.mark.integration
def test_openai_compatible_provider_writes_reference_image_to_real_minio() -> None:
    if os.getenv("AI_VIDEO_RUN_MINIO_INTEGRATION") != "1":
        pytest.skip("set AI_VIDEO_RUN_MINIO_INTEGRATION=1 to run the MinIO integration test")

    endpoint = os.getenv("AI_VIDEO_STORAGE_ENDPOINT", "http://127.0.0.1:9000")
    bucket = os.getenv("AI_VIDEO_STORAGE_BUCKET", "ai-video-artifacts")
    access_key = os.getenv("AI_VIDEO_STORAGE_ACCESS_KEY", "ai-video-dev")
    secret_key = os.getenv("AI_VIDEO_STORAGE_SECRET_KEY", "ai-video-dev-password")
    generated_content = b"openai-compatible-minio-e2e"
    generated_base64 = base64.b64encode(generated_content).decode("ascii")

    def provider_handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/images/generations"
        payload = request.read()
        assert b"e2e-image-v1" in payload
        return httpx.Response(
            200,
            request=request,
            json={"data": [{"b64_json": generated_base64}]},
        )

    async def exercise() -> None:
        provider_client = httpx.AsyncClient(transport=httpx.MockTransport(provider_handler))
        provider = OpenAICompatibleImageGenerationProvider(
            base_url="http://image-provider.test",
            api_key="test-image-key",
            model="e2e-image-v1",
            timeout_seconds=30,
            client=provider_client,
        )
        storage = S3CompatibleArtifactStorage(
            endpoint=endpoint,
            bucket=bucket,
            region=os.getenv("AI_VIDEO_STORAGE_REGION", "us-east-1"),
            access_key=access_key,
            secret_key=secret_key,
            timeout_seconds=int(os.getenv("AI_VIDEO_STORAGE_TIMEOUT_SECONDS", "30")),
            signed_url_expire_seconds=300,
        )
        queue = InProcessTaskQueue()
        store = InMemoryStore()
        service = ReferenceImageTaskService(store, queue, provider, storage)
        queue.set_handler(service.run_task)
        storage_key: str | None = None
        try:
            asset = await store.save_asset_version(
                AssetRecord(
                    project_id=uuid4(),
                    story_bible_id=uuid4(),
                    asset_type=AssetType.CHARACTER,
                    name="真实 MinIO 联调角色",
                    status=AssetStatus.READY,
                    content=CharacterAssetContent(
                        role="protagonist",
                        traits=["稳定"],
                        appearance="黑发、深色外套",
                    ),
                    provider="test",
                    model="test",
                    duration_ms=0,
                )
            )
            task, reused = await service.create_task(asset.id, request=_request())
            await queue.close()

            assert reused is False
            assert (await store.get_task(task.id)).status.value == "succeeded"
            image = (await store.list_reference_images(asset.id))[0]
            storage_key = str(image.metadata["storage_key"])
            assert image.status == ReferenceImageStatus.SUCCEEDED
            assert image.provider == "openai_compatible"
            assert image.output_uri == f"s3://{bucket}/{storage_key}"
            assert image.metadata["content_type"] == "image/png"
            assert image.metadata["size_bytes"] == len(generated_content)

            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.get(storage.create_download_url(storage_key))
            assert response.status_code == 200
            assert response.content == generated_content
        finally:
            await queue.close()
            await provider.close()
            await provider_client.aclose()
            await storage.close()
            if (
                storage_key
                and os.getenv("AI_VIDEO_MINIO_COMPOSE_CLEANUP", "1") == "1"
                and urlsplit(endpoint).hostname in {"127.0.0.1", "localhost"}
            ):
                subprocess.run(
                    [
                        "docker",
                        "compose",
                        "exec",
                        "-T",
                        "minio",
                        "mc",
                        "rm",
                        "--quiet",
                        f"local/{bucket}/{storage_key}",
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                )

    asyncio.run(exercise())


@pytest.mark.external_integration
def test_external_configured_image_provider_writes_reference_image_to_minio() -> None:
    if os.getenv("AI_VIDEO_RUN_EXTERNAL_IMAGE_INTEGRATION") != "1":
        pytest.skip(
            "set AI_VIDEO_RUN_EXTERNAL_IMAGE_INTEGRATION=1 to call the configured image API"
        )

    required = {
        "AI_VIDEO_IMAGE_API_KEY": os.getenv("AI_VIDEO_IMAGE_API_KEY"),
        "AI_VIDEO_IMAGE_BASE_URL": os.getenv("AI_VIDEO_IMAGE_BASE_URL"),
        "AI_VIDEO_IMAGE_MODEL": os.getenv("AI_VIDEO_IMAGE_MODEL"),
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        pytest.fail(f"external image integration requires: {', '.join(missing)}")

    endpoint = os.getenv("AI_VIDEO_STORAGE_ENDPOINT", "http://127.0.0.1:9000")
    bucket = os.getenv("AI_VIDEO_STORAGE_BUCKET", "ai-video-artifacts")
    access_key = os.getenv("AI_VIDEO_STORAGE_ACCESS_KEY", "ai-video-dev")
    secret_key = os.getenv("AI_VIDEO_STORAGE_SECRET_KEY", "ai-video-dev-password")
    width = int(os.getenv("AI_VIDEO_IMAGE_TEST_WIDTH", "512"))
    height = int(os.getenv("AI_VIDEO_IMAGE_TEST_HEIGHT", "512"))
    provider_name = os.getenv("AI_VIDEO_IMAGE_PROVIDER", "openai_compatible")

    async def exercise() -> None:
        provider_client = httpx.AsyncClient(
            timeout=int(os.getenv("AI_VIDEO_IMAGE_TIMEOUT_SECONDS", "120"))
        )
        if provider_name == "siliconflow":
            provider = SiliconFlowImageGenerationProvider(
                base_url=str(required["AI_VIDEO_IMAGE_BASE_URL"]),
                api_key=str(required["AI_VIDEO_IMAGE_API_KEY"]),
                model=str(required["AI_VIDEO_IMAGE_MODEL"]),
                timeout_seconds=int(os.getenv("AI_VIDEO_IMAGE_TIMEOUT_SECONDS", "120")),
                client=provider_client,
            )
        elif provider_name == "openai_compatible":
            provider = OpenAICompatibleImageGenerationProvider(
                base_url=str(required["AI_VIDEO_IMAGE_BASE_URL"]),
                api_key=str(required["AI_VIDEO_IMAGE_API_KEY"]),
                model=str(required["AI_VIDEO_IMAGE_MODEL"]),
                timeout_seconds=int(os.getenv("AI_VIDEO_IMAGE_TIMEOUT_SECONDS", "120")),
                client=provider_client,
            )
        else:
            raise AssertionError(
                "AI_VIDEO_IMAGE_PROVIDER must be siliconflow or openai_compatible "
                f"for external integration, got {provider_name!r}"
            )
        storage = S3CompatibleArtifactStorage(
            endpoint=endpoint,
            bucket=bucket,
            region=os.getenv("AI_VIDEO_STORAGE_REGION", "us-east-1"),
            access_key=access_key,
            secret_key=secret_key,
            timeout_seconds=int(os.getenv("AI_VIDEO_STORAGE_TIMEOUT_SECONDS", "30")),
            signed_url_expire_seconds=300,
        )
        queue = InProcessTaskQueue()
        store = InMemoryStore()
        service = ReferenceImageTaskService(store, queue, provider, storage)
        queue.set_handler(service.run_task)
        storage_key: str | None = None
        try:
            asset = await store.save_asset_version(
                AssetRecord(
                    project_id=uuid4(),
                    story_bible_id=uuid4(),
                    asset_type=AssetType.CHARACTER,
                    name="外部图像 API 联调角色",
                    status=AssetStatus.READY,
                    content=CharacterAssetContent(
                        role="protagonist",
                        traits=["沉着"],
                        appearance="黑发、深色外套、电影感布光",
                    ),
                    provider="external-image-integration",
                    model=str(required["AI_VIDEO_IMAGE_MODEL"]),
                    duration_ms=0,
                )
            )
            task, reused = await service.create_task(
                asset.id,
                request=_request(width=width, height=height),
            )
            await queue.close()

            assert reused is False
            assert (await store.get_task(task.id)).status.value == "succeeded"
            image = (await store.list_reference_images(asset.id))[0]
            storage_key = str(image.metadata["storage_key"])
            assert image.status == ReferenceImageStatus.SUCCEEDED
            assert image.provider == provider_name
            assert image.output_uri == f"s3://{bucket}/{storage_key}"
            assert str(image.metadata["content_type"]).startswith("image/")
            assert int(image.metadata["size_bytes"]) > 0

            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.get(storage.create_download_url(storage_key))
            assert response.status_code == 200
            assert len(response.content) == int(image.metadata["size_bytes"])
        finally:
            await queue.close()
            await provider.close()
            await provider_client.aclose()
            await storage.close()
            if (
                storage_key
                and os.getenv("AI_VIDEO_MINIO_COMPOSE_CLEANUP", "1") == "1"
                and urlsplit(endpoint).hostname in {"127.0.0.1", "localhost"}
            ):
                subprocess.run(
                    [
                        "docker",
                        "compose",
                        "exec",
                        "-T",
                        "minio",
                        "mc",
                        "rm",
                        "--quiet",
                        f"local/{bucket}/{storage_key}",
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                )

    asyncio.run(exercise())


def _request(width: int = 512, height: int = 512):
    from app.domain.models import ReferenceImageCreateRequest

    return ReferenceImageCreateRequest(style="cinematic", width=width, height=height)
