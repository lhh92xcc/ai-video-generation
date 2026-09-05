from __future__ import annotations

import asyncio
import base64
import json
from uuid import uuid4

import httpx
import pytest

from app.domain.models import AssetType, ReferenceImageGenerationRequest
from app.providers.errors import ImageProviderError
from app.providers.openai_compatible_image import OpenAICompatibleImageGenerationProvider


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


def test_openai_compatible_image_provider_parses_base64_response() -> None:
    encoded = base64.b64encode(b"fake-png-bytes").decode("ascii")

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/images/generations"
        assert request.headers["Authorization"] == "Bearer image-key"
        payload = json.loads(request.content)
        assert payload == {
            "model": "image-test-model",
            "prompt": "cinematic character reference",
            "negative_prompt": "blurry",
            "size": "512x768",
            "n": 1,
            "response_format": "b64_json",
        }
        return httpx.Response(200, json={"data": [{"b64_json": encoded}]})

    async def exercise() -> None:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = OpenAICompatibleImageGenerationProvider(
            base_url="https://example.test",
            api_key="image-key",
            model="image-test-model",
            timeout_seconds=5,
            client=client,
        )
        result = await provider.generate_reference_image(image_request())
        await provider.close()
        await client.aclose()

        assert result.provider == "openai_compatible"
        assert result.image_base64 == encoded
        assert result.output_uri is None
        assert result.width == 512

    asyncio.run(exercise())


def test_openai_compatible_image_provider_downloads_url_response() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/images/generations":
            return httpx.Response(200, json={"data": [{"url": "https://cdn.example.test/image.png"}]})
        assert request.url.host == "cdn.example.test"
        return httpx.Response(200, content=b"fake-webp", headers={"content-type": "image/webp"})

    async def exercise() -> None:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = OpenAICompatibleImageGenerationProvider(
            base_url="https://example.test",
            api_key="image-key",
            model="image-test-model",
            timeout_seconds=5,
            client=client,
        )
        result = await provider.generate_reference_image(image_request())
        await provider.close()
        await client.aclose()

        assert result.mime_type == "image/webp"
        assert result.image_base64 == base64.b64encode(b"fake-webp").decode("ascii")
        assert result.metadata["response_format"] == "url"

    asyncio.run(exercise())


def test_openai_compatible_image_provider_maps_auth_and_invalid_response_errors() -> None:
    async def exercise() -> None:
        no_key = OpenAICompatibleImageGenerationProvider(
            base_url="https://example.test",
            api_key=None,
            model="image-test-model",
            timeout_seconds=5,
        )
        with pytest.raises(ImageProviderError) as auth_error:
            await no_key.generate_reference_image(image_request())
        await no_key.close()
        assert auth_error.value.code == "IMAGE_PROVIDER_AUTH_FAILED"

        async def invalid_handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": [{}]})

        client = httpx.AsyncClient(transport=httpx.MockTransport(invalid_handler))
        invalid = OpenAICompatibleImageGenerationProvider(
            base_url="https://example.test",
            api_key="image-key",
            model="image-test-model",
            timeout_seconds=5,
            client=client,
        )
        with pytest.raises(ImageProviderError) as response_error:
            await invalid.generate_reference_image(image_request())
        await invalid.close()
        await client.aclose()
        assert response_error.value.code == "IMAGE_PROVIDER_INVALID_RESPONSE"

    asyncio.run(exercise())
