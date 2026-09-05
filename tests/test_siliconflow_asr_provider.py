from __future__ import annotations

import asyncio

import httpx

from app.domain.models import SubtitleASRGenerationRequest
from app.providers.siliconflow_asr import SiliconFlowASRProvider


def test_siliconflow_asr_parses_text_and_marks_estimated_timing() -> None:
    async def exercise() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.method == "POST"
            assert request.url.path == "/v1/audio/transcriptions"
            assert request.headers["authorization"] == "Bearer siliconflow-key"
            assert request.headers["content-type"].startswith("multipart/form-data;")
            assert b'name="model"' in request.content
            assert b"FunAudioLLM/SenseVoiceSmall" in request.content
            return httpx.Response(
                200,
                request=request,
                json={"text": "第一句测试。第二句字幕。"},
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = SiliconFlowASRProvider(
            base_url="https://api.siliconflow.cn/v1",
            api_key="siliconflow-key",
            model="FunAudioLLM/SenseVoiceSmall",
            timeout_seconds=10,
            client=client,
        )
        result = await provider.transcribe(
            SubtitleASRGenerationRequest(
                audio_bytes=b"mp3-bytes",
                mime_type="audio/mpeg",
                language="zh-CN",
                audio_duration_seconds=4.0,
            )
        )

        assert result.provider == "siliconflow_asr"
        assert result.precision == "transcript_sentence_estimate"
        assert [cue.text for cue in result.cues] == ["第一句测试。", "第二句字幕。"]
        assert result.metadata["segment_timestamps"] is False
        assert result.metadata["word_timestamps"] is False
        assert result.metadata["audio_inspected"] is True
        await provider.close()
        await client.aclose()

    asyncio.run(exercise())
