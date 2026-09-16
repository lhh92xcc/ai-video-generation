from __future__ import annotations

import asyncio
import base64
import json
from uuid import uuid4

import httpx
import pytest

from app.domain.models import VideoClipGenerationRequest
from app.providers.jimeng_video import JimengVideoGenerationProvider


def test_jimeng_adapter_keeps_quality_and_reference_mapping_inside_adapter() -> None:
    async def exercise() -> None:
        video_bytes = b"jimeng-fixture-mp4"
        payloads: list[dict[str, object]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            payloads.append(json.loads(request.content))
            return httpx.Response(
                200,
                json={
                    "data": {
                        "status": "completed",
                        "b64_json": base64.b64encode(video_bytes).decode("ascii"),
                        "mime_type": "video/mp4",
                    }
                },
                request=request,
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = JimengVideoGenerationProvider(
            base_url="https://jimeng.example",
            api_key="test-key",
            model="official-model-placeholder",
            timeout_seconds=10,
            protocol_ready=True,
            client=client,
        )
        request = VideoClipGenerationRequest(
            episode_id=uuid4(),
            shot_list_id=uuid4(),
            shot_index=2,
            duration_seconds=4,
            prompt="保留角色身份，轻微抬头",
            negative_prompt="变脸、闪烁",
            keyframe_bytes=b"png-bytes",
            keyframe_mime_type="image/png",
            generation_attempt=3,
            width=432,
            height=768,
            fps=16,
            steps=8,
            cfg=5.0,
            noise_aug_strength=0.01,
        )

        result = await provider.generate_video_clip(request)

        assert result.provider == "jimeng"
        assert base64.b64decode(result.video_base64 or "") == video_bytes
        assert payloads[0]["generation_attempt"] == 3
        assert payloads[0]["quality"] == {
            "width": 432,
            "height": 768,
            "fps": 16,
            "steps": 8,
            "cfg": 5.0,
            "noise_aug_strength": 0.01,
            "size": "432x768",
        }
        assert payloads[0]["reference_image"] == {
            "mime_type": "image/png",
            "data": base64.b64encode(b"png-bytes").decode("ascii"),
        }
        await provider.close()

    asyncio.run(exercise())


def test_jimeng_adapter_requires_explicit_protocol_enablement() -> None:
    async def exercise() -> None:
        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(500)))
        provider = JimengVideoGenerationProvider(
            base_url="https://jimeng.example",
            api_key="test-key",
            model="official-model-placeholder",
            timeout_seconds=10,
            client=client,
        )

        try:
            from app.providers.errors_video import VideoProviderError

            with pytest.raises(VideoProviderError) as caught:
                await provider.generate_video_clip(
                    VideoClipGenerationRequest(
                        episode_id=uuid4(),
                        shot_list_id=uuid4(),
                        shot_index=1,
                        duration_seconds=3,
                        prompt="test",
                        negative_prompt="bad",
                    )
                )
            assert caught.value.code == "JIMENG_PROTOCOL_NOT_CONFIGURED"
        finally:
            await provider.close()

    asyncio.run(exercise())


def test_jimeng_adapter_reports_its_own_base_url_when_enabled_but_incomplete() -> None:
    async def exercise() -> None:
        provider = JimengVideoGenerationProvider(
            base_url="",
            api_key="test-key",
            model="official-model-placeholder",
            timeout_seconds=10,
            protocol_ready=True,
        )

        try:
            from app.providers.errors_video import VideoProviderError

            with pytest.raises(VideoProviderError) as caught:
                await provider.generate_video_clip(
                    VideoClipGenerationRequest(
                        episode_id=uuid4(),
                        shot_list_id=uuid4(),
                        shot_index=1,
                        duration_seconds=3,
                        prompt="test",
                        negative_prompt="bad",
                    )
                )
            assert caught.value.code == "VIDEO_PROVIDER_INVALID_CONFIGURATION"
            assert "AI_VIDEO_JIMENG_BASE_URL" in caught.value.message
        finally:
            await provider.close()

    asyncio.run(exercise())
