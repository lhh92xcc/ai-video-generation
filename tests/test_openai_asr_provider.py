from __future__ import annotations

import asyncio

import httpx

from app.domain.models import SubtitleASRGenerationRequest
from app.providers.errors_asr import SubtitleASRProviderError
from app.providers.openai_compatible_asr import OpenAICompatibleASRProvider


def test_openai_compatible_asr_parses_segment_timestamps() -> None:
    async def exercise() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.method == "POST"
            assert request.url.path == "/v1/audio/transcriptions"
            assert request.headers["authorization"] == "Bearer asr-key"
            assert request.headers["content-type"].startswith("multipart/form-data;")
            assert b'name="model"' in request.content
            assert b'whisper-1' in request.content
            assert b'first sentence' in request.content
            return httpx.Response(
                200,
                json={
                    "text": "first sentence second sentence",
                    "segments": [
                        {"id": 0, "start": 0.0, "end": 1.25, "text": "first sentence"},
                        {"id": 1, "start": 1.25, "end": 3.0, "text": "second sentence"},
                    ],
                    "words": [
                        {"word": "first", "start": 0.0, "end": 0.5},
                        {"word": "sentence", "start": 0.5, "end": 1.25},
                    ],
                },
                request=request,
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = OpenAICompatibleASRProvider(
            base_url="https://asr.example/v1",
            api_key="asr-key",
            model="whisper-1",
            timeout_seconds=10,
            client=client,
        )
        result = await provider.transcribe(
            SubtitleASRGenerationRequest(
                audio_bytes=b"wav-bytes",
                mime_type="audio/wav",
                language="en",
                audio_duration_seconds=3.0,
                reference_text="first sentence",
            )
        )

        assert result.provider == "openai_compatible_asr"
        assert result.precision == "segment_asr"
        assert [cue.text for cue in result.cues] == ["first sentence", "second sentence"]
        assert result.metadata["audio_inspected"] is True
        await provider.close()
        await client.aclose()

    asyncio.run(exercise())


def test_openai_compatible_asr_maps_auth_error() -> None:
    async def exercise() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, request=request)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = OpenAICompatibleASRProvider(
            base_url="https://asr.example/v1",
            api_key="asr-key",
            model="whisper-1",
            timeout_seconds=10,
            client=client,
        )

        try:
            await provider.transcribe(
                SubtitleASRGenerationRequest(
                    audio_bytes=b"wav-bytes",
                    mime_type="audio/wav",
                    language="zh-CN",
                    audio_duration_seconds=1.0,
                )
            )
        except SubtitleASRProviderError as exc:
            assert exc.code == "ASR_PROVIDER_AUTH_FAILED"
        else:
            raise AssertionError("expected ASR auth error")
        await provider.close()
        await client.aclose()

    asyncio.run(exercise())
