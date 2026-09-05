from __future__ import annotations

import asyncio
import base64
import json
from time import monotonic

import httpx
import pytest

from app.domain.models import LipSyncGenerationRequest
from app.providers.lip_sync import HttpMuseTalkProvider, LipSyncProviderError


def lip_sync_request(face_padding: int = 0) -> LipSyncGenerationRequest:
    return LipSyncGenerationRequest(
        video_bytes=b"input-video",
        video_mime_type="video/mp4",
        audio_bytes=b"input-audio",
        audio_mime_type="audio/wav",
        face_padding=face_padding,
    )


def run(coro):
    return asyncio.run(coro)


def test_http_musetalk_provider_uploads_multipart_and_parses_seconds() -> None:
    encoded = base64.b64encode(b"output-video").decode("ascii")

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/v1/lip-sync"
        assert request.headers["authorization"] == "Bearer bridge-key"
        assert request.headers["content-type"].startswith("multipart/form-data;")
        assert b'name="video"' in request.content
        assert b'name="audio"' in request.content
        assert b'name="face_region"' in request.content
        assert b'name="face_padding"' in request.content
        return httpx.Response(
            200,
            json={
                "video_base64": encoded,
                "mime_type": "video/mp4",
                "provider": "musetalk_http",
                "model": "musetalk-test",
                "duration_seconds": 4,
                "duration_ms": 4000,
                "request_id": "bridge-1",
            },
        )

    async def exercise() -> None:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = HttpMuseTalkProvider(
            base_url="https://bridge.example",
            api_key="bridge-key",
            timeout_seconds=5,
            client=client,
        )
        result = await provider.generate_lip_sync(lip_sync_request(face_padding=42))
        assert result.video_base64 == encoded
        assert result.duration_seconds == 4
        assert result.duration_ms == 4000
        assert result.metadata["remote_request_id"] == "bridge-1"
        await provider.close()
        await client.aclose()

    run(exercise())


def test_http_musetalk_provider_downloads_url_and_uses_duration_ms() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(
                200,
                json={"output": {"url": "/files/result.mp4"}, "duration_ms": 2500},
            )
        assert request.method == "GET"
        assert request.url.path == "/files/result.mp4"
        return httpx.Response(200, content=b"downloaded-video", headers={"content-type": "video/mp4"})

    async def exercise() -> None:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = HttpMuseTalkProvider(
            base_url="https://bridge.example/api",
            timeout_seconds=5,
            client=client,
        )
        result = await provider.generate_lip_sync(lip_sync_request())
        assert result.video_base64 == base64.b64encode(b"downloaded-video").decode("ascii")
        assert result.mime_type == "video/mp4"
        assert result.duration_seconds == 2
        assert result.duration_ms == 2500
        await provider.close()
        await client.aclose()

    run(exercise())


def test_http_musetalk_provider_accepts_data_uri_and_never_uses_face_padding_as_duration() -> None:
    encoded = base64.b64encode(b"data-uri-video").decode("ascii")

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"output": {"url": f"data:video/mp4;base64,{encoded}"}},
        )

    async def exercise() -> None:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = HttpMuseTalkProvider(
            base_url="https://bridge.example",
            timeout_seconds=5,
            client=client,
        )
        result = await provider.generate_lip_sync(lip_sync_request(face_padding=99))
        assert result.duration_seconds == 1
        assert result.video_base64 == encoded
        assert HttpMuseTalkProvider._reported_duration({}, monotonic())[0] == 1
        await provider.close()
        await client.aclose()

    run(exercise())


@pytest.mark.parametrize("status_code,code", [(401, "LIP_SYNC_AUTH_FAILED"), (429, "LIP_SYNC_RATE_LIMITED"), (503, "LIP_SYNC_UNAVAILABLE")])
def test_http_musetalk_provider_maps_http_errors(status_code: int, code: str) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code)

    async def exercise() -> None:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = HttpMuseTalkProvider(
            base_url="https://bridge.example",
            timeout_seconds=5,
            client=client,
        )
        with pytest.raises(LipSyncProviderError) as error:
            await provider.generate_lip_sync(lip_sync_request())
        assert error.value.code == code
        await provider.close()
        await client.aclose()

    run(exercise())


@pytest.mark.parametrize(
    "payload,code",
    [
        (b"not-json", "LIP_SYNC_INVALID_RESPONSE"),
        (json.dumps({"status": "ok"}).encode(), "LIP_SYNC_INVALID_RESPONSE"),
        (json.dumps({"video_base64": "%%%"}).encode(), "LIP_SYNC_INVALID_RESPONSE"),
    ],
)
def test_http_musetalk_provider_rejects_invalid_bridge_output(payload: bytes, code: str) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=payload)

    async def exercise() -> None:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = HttpMuseTalkProvider(
            base_url="https://bridge.example",
            timeout_seconds=5,
            client=client,
        )
        with pytest.raises(LipSyncProviderError) as error:
            await provider.generate_lip_sync(lip_sync_request())
        assert error.value.code == code
        await provider.close()
        await client.aclose()

    run(exercise())


def test_http_musetalk_provider_rejects_oversized_output() -> None:
    encoded = base64.b64encode(b"123456").decode("ascii")

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"video_base64": encoded})

    async def exercise() -> None:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = HttpMuseTalkProvider(
            base_url="https://bridge.example",
            max_download_bytes=5,
            timeout_seconds=5,
            client=client,
        )
        with pytest.raises(LipSyncProviderError) as error:
            await provider.generate_lip_sync(lip_sync_request())
        assert error.value.code == "LIP_SYNC_OUTPUT_TOO_LARGE"
        await provider.close()
        await client.aclose()

    run(exercise())
