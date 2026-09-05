from __future__ import annotations

import asyncio
import base64
import json
from uuid import uuid4

import httpx
import pytest

from app.domain.models import VideoClipGenerationRequest
from app.providers.errors_video import VideoProviderError
from app.providers.siliconflow_video import SiliconFlowVideoGenerationProvider


def video_request() -> VideoClipGenerationRequest:
    return VideoClipGenerationRequest(
        episode_id=uuid4(),
        shot_list_id=uuid4(),
        shot_index=1,
        duration_seconds=5,
        prompt="a cinematic character walking in a rainy neon street",
        negative_prompt="blurry, watermark",
    )


def test_siliconflow_video_provider_submits_polls_and_downloads() -> None:
    status_calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal status_calls
        if request.url.path == "/v1/video/submit":
            assert request.headers["Authorization"] == "Bearer video-key"
            assert json.loads(request.content) == {
                "model": "Wan-AI/Wan2.2-T2V-A14B",
                "prompt": "a cinematic character walking in a rainy neon street",
                "negative_prompt": "blurry, watermark",
                "image_size": "720x1280",
            }
            return httpx.Response(200, json={"requestId": "request-123"})
        if request.url.path == "/v1/video/status":
            assert request.headers["Authorization"] == "Bearer video-key"
            assert json.loads(request.content) == {"requestId": "request-123"}
            status_calls += 1
            if status_calls == 1:
                return httpx.Response(200, json={"status": "InProgress"})
            return httpx.Response(
                200,
                json={
                    "status": "Succeed",
                    "results": {"videos": [{"url": "https://cdn.example.test/clip.mp4"}]},
                },
            )
        assert request.url.host == "cdn.example.test"
        return httpx.Response(200, content=b"fake-mp4", headers={"content-type": "video/mp4"})

    async def exercise() -> None:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = SiliconFlowVideoGenerationProvider(
            base_url="https://api.siliconflow.cn",
            api_key="video-key",
            model="Wan-AI/Wan2.2-T2V-A14B",
            image_size="720x1280",
            timeout_seconds=5,
            poll_interval_seconds=0,
            client=client,
        )
        result = await provider.generate_video_clip(video_request())
        await provider.close()
        await client.aclose()

        assert result.provider == "siliconflow"
        assert result.output_uri == "https://cdn.example.test/clip.mp4"
        assert result.mime_type == "video/mp4"
        assert result.video_base64 == base64.b64encode(b"fake-mp4").decode("ascii")
        assert result.metadata["request_id"] == "request-123"
        assert result.metadata["polls"] == 2

    asyncio.run(exercise())


def test_siliconflow_video_provider_maps_failed_job_and_requires_key() -> None:
    async def exercise() -> None:
        no_key = SiliconFlowVideoGenerationProvider(
            base_url="https://api.siliconflow.cn",
            api_key=None,
            model="Wan-AI/Wan2.2-T2V-A14B",
            image_size="720x1280",
            timeout_seconds=5,
        )
        with pytest.raises(VideoProviderError) as auth_error:
            await no_key.generate_video_clip(video_request())
        await no_key.close()
        assert auth_error.value.code == "VIDEO_PROVIDER_AUTH_FAILED"

        async def failed_handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/v1/video/submit":
                return httpx.Response(200, json={"requestId": "failed-123"})
            return httpx.Response(200, json={"status": "Failed", "reason": "content rejected"})

        client = httpx.AsyncClient(transport=httpx.MockTransport(failed_handler))
        failed = SiliconFlowVideoGenerationProvider(
            base_url="https://api.siliconflow.cn/v1",
            api_key="video-key",
            model="Wan-AI/Wan2.2-T2V-A14B",
            image_size="720x1280",
            timeout_seconds=5,
            poll_interval_seconds=0,
            client=client,
        )
        with pytest.raises(VideoProviderError) as failed_error:
            await failed.generate_video_clip(video_request())
        await failed.close()
        await client.aclose()
        assert failed_error.value.code == "VIDEO_PROVIDER_FAILED"
        assert str(failed_error.value) == "content rejected"

    asyncio.run(exercise())
