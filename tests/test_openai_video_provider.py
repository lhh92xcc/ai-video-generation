from __future__ import annotations

import asyncio
import base64
import json
from uuid import uuid4

import httpx
import pytest

from app.domain.models import VideoClipGenerationRequest
from app.providers.errors_video import VideoProviderError
from app.providers.openai_compatible_video import OpenAICompatibleVideoGenerationProvider


def _request() -> VideoClipGenerationRequest:
    return VideoClipGenerationRequest(
        episode_id=uuid4(),
        shot_list_id=uuid4(),
        shot_index=1,
        duration_seconds=5,
        prompt="a cinematic rainy street",
        negative_prompt="blurry",
    )


def test_video_provider_polls_and_downloads_video() -> None:
    async def exercise() -> None:
        video_bytes = b"fake-mp4-content"
        calls: list[tuple[str, str]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append((request.method, request.url.path))
            if request.method == "POST" and request.url.path == "/videos/generations":
                assert request.headers["authorization"] == "Bearer test-key"
                assert json.loads(request.content)["duration_seconds"] == 5
                return httpx.Response(
                    202,
                    json={"id": "job-1", "status": "queued"},
                    request=request,
                )
            if request.method == "GET" and request.url.path == "/videos/generations/job-1":
                return httpx.Response(
                    200,
                    json={
                        "id": "job-1",
                        "status": "completed",
                        "video_url": "https://cdn.example/clip.mp4",
                    },
                    request=request,
                )
            if request.method == "GET" and request.url.host == "cdn.example":
                return httpx.Response(
                    200,
                    content=video_bytes,
                    headers={"content-type": "video/mp4"},
                    request=request,
                )
            return httpx.Response(404, request=request)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = OpenAICompatibleVideoGenerationProvider(
            base_url="https://video.example",
            api_key="test-key",
            model="video-v1",
            timeout_seconds=10,
            poll_interval_seconds=0,
            max_poll_seconds=2,
            client=client,
        )
        result = await provider.generate_video_clip(_request())

        assert result.provider == "openai_compatible"
        assert result.output_uri == "https://cdn.example/clip.mp4"
        assert result.mime_type == "video/mp4"
        assert base64.b64decode(result.video_base64 or "") == video_bytes
        assert result.metadata == {
            "request_id": "job-1",
            "polls": 1,
            "remote_output_uri": "https://cdn.example/clip.mp4",
            "downloaded": True,
        }
        assert calls == [
            ("POST", "/videos/generations"),
            ("GET", "/videos/generations/job-1"),
            ("GET", "/clip.mp4"),
        ]
        await provider.close()

    asyncio.run(exercise())


def test_video_provider_accepts_immediate_base64_response() -> None:
    async def exercise() -> None:
        encoded = base64.b64encode(b"webm-content").decode("ascii")

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "data": {
                        "status": "completed",
                        "b64_json": encoded,
                        "mime_type": "video/webm",
                    }
                },
                request=request,
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = OpenAICompatibleVideoGenerationProvider(
            base_url="https://video.example",
            api_key="test-key",
            model="video-v1",
            timeout_seconds=10,
            client=client,
        )
        result = await provider.generate_video_clip(_request())

        assert result.mime_type == "video/webm"
        assert base64.b64decode(result.video_base64 or "") == b"webm-content"
        await provider.close()

    asyncio.run(exercise())


def test_video_provider_maps_failed_job() -> None:
    async def exercise() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "POST":
                return httpx.Response(200, json={"id": "job-2", "status": "queued"}, request=request)
            return httpx.Response(
                200,
                json={"id": "job-2", "status": "failed", "message": "content rejected"},
                request=request,
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = OpenAICompatibleVideoGenerationProvider(
            base_url="https://video.example",
            api_key="test-key",
            model="video-v1",
            timeout_seconds=10,
            poll_interval_seconds=0,
            client=client,
        )

        with pytest.raises(VideoProviderError) as error:
            await provider.generate_video_clip(_request())
        assert error.value.code == "VIDEO_PROVIDER_FAILED"
        assert str(error.value) == "content rejected"
        await provider.close()

    asyncio.run(exercise())


def test_video_provider_requires_api_key() -> None:
    async def exercise() -> None:
        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(500)))
        provider = OpenAICompatibleVideoGenerationProvider(
            base_url="https://video.example",
            api_key=None,
            model="video-v1",
            timeout_seconds=10,
            client=client,
        )

        with pytest.raises(VideoProviderError) as error:
            await provider.generate_video_clip(_request())
        assert error.value.code == "VIDEO_PROVIDER_AUTH_FAILED"
        await provider.close()

    asyncio.run(exercise())
