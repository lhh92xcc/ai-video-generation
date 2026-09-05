from __future__ import annotations

import asyncio
import base64
import json
from uuid import uuid4

import httpx
import pytest

from app.domain.models import AssetType, ReferenceImageGenerationRequest
from app.providers.errors import ImageProviderError
from app.providers.siliconflow_image import SiliconFlowImageGenerationProvider


def image_request() -> ReferenceImageGenerationRequest:
    return ReferenceImageGenerationRequest(
        asset_id=uuid4(),
        asset_key=uuid4(),
        asset_type=AssetType.CHARACTER,
        asset_version=2,
        prompt="cinematic character reference",
        negative_prompt="blurry",
        width=512,
        height=768,
    )


def test_siliconflow_image_provider_submits_and_downloads_url() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/images/generations":
            assert request.headers["Authorization"] == "Bearer image-key"
            assert json.loads(request.content) == {
                "model": "Kwai-Kolors/Kolors",
                "prompt": "cinematic character reference",
                "negative_prompt": "blurry",
                "image_size": "512x768",
                "batch_size": 1,
            }
            return httpx.Response(
                200,
                json={"images": [{"url": "https://cdn.example.test/image.png"}]},
            )
        assert request.url.host == "cdn.example.test"
        return httpx.Response(
            200,
            content=b"\x89PNG\r\n\x1a\nfake-png",
            headers={"content-type": "application/octet-stream"},
        )

    async def exercise() -> None:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = SiliconFlowImageGenerationProvider(
            base_url="https://api.siliconflow.cn",
            api_key="image-key",
            model="Kwai-Kolors/Kolors",
            timeout_seconds=5,
            client=client,
        )
        result = await provider.generate_reference_image(image_request())
        await provider.close()
        await client.aclose()

        assert result.provider == "siliconflow"
        assert result.output_uri is None
        assert result.image_base64 == base64.b64encode(b"\x89PNG\r\n\x1a\nfake-png").decode(
            "ascii"
        )
        assert result.mime_type == "image/png"
        assert result.metadata["image_size"] == "512x768"

    asyncio.run(exercise())


def test_siliconflow_image_provider_requires_key_and_maps_rate_limit() -> None:
    async def exercise() -> None:
        no_key = SiliconFlowImageGenerationProvider(
            base_url="https://api.siliconflow.cn",
            api_key=None,
            model="Kwai-Kolors/Kolors",
            timeout_seconds=5,
        )
        with pytest.raises(ImageProviderError) as auth_error:
            await no_key.generate_reference_image(image_request())
        await no_key.close()
        assert auth_error.value.code == "IMAGE_PROVIDER_AUTH_FAILED"

        async def rate_limit_handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, json={"message": "rate limited"})

        client = httpx.AsyncClient(transport=httpx.MockTransport(rate_limit_handler))
        limited = SiliconFlowImageGenerationProvider(
            base_url="https://api.siliconflow.cn/v1",
            api_key="image-key",
            model="Kwai-Kolors/Kolors",
            timeout_seconds=5,
            client=client,
        )
        with pytest.raises(ImageProviderError) as rate_error:
            await limited.generate_reference_image(image_request())
        await limited.close()
        await client.aclose()
        assert rate_error.value.code == "IMAGE_PROVIDER_RATE_LIMITED"

    asyncio.run(exercise())
