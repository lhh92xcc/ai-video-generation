from __future__ import annotations

import asyncio
import mimetypes
import os
from pathlib import Path

import pytest

from app.domain.models import SubtitleASRGenerationRequest
from app.media.audio_validation import FFprobeAudioValidator
from app.providers.aliyun_dashscope_asr import AliyunDashScopeASRProvider
from app.providers.openai_compatible_asr import OpenAICompatibleASRProvider
from app.providers.siliconflow_asr import SiliconFlowASRProvider


@pytest.mark.external_integration
def test_external_configured_asr_provider_returns_timed_cues() -> None:
    """Opt-in smoke for a real OpenAI-compatible transcription endpoint.

    The test intentionally requires a caller-supplied speech file. It never
    generates synthetic audio or treats a text-only response as success.
    """

    if os.getenv("AI_VIDEO_RUN_EXTERNAL_ASR_INTEGRATION") != "1":
        pytest.skip(
            "set AI_VIDEO_RUN_EXTERNAL_ASR_INTEGRATION=1 to call the configured ASR API"
        )

    provider_name = os.getenv("AI_VIDEO_ASR_PROVIDER", "openai_compatible")
    required = {
        "AI_VIDEO_ASR_API_KEY": os.getenv("AI_VIDEO_ASR_API_KEY"),
        "AI_VIDEO_ASR_MODEL": os.getenv("AI_VIDEO_ASR_MODEL"),
        "AI_VIDEO_ASR_TEST_AUDIO_PATH": os.getenv("AI_VIDEO_ASR_TEST_AUDIO_PATH"),
    }
    if provider_name not in {"aliyun_dashscope", "siliconflow"}:
        required["AI_VIDEO_ASR_BASE_URL"] = os.getenv("AI_VIDEO_ASR_BASE_URL")
    missing = [name for name, value in required.items() if not value]
    if missing:
        pytest.fail(f"external ASR integration requires: {', '.join(missing)}")

    audio_path = Path(str(required["AI_VIDEO_ASR_TEST_AUDIO_PATH"])).expanduser()
    if not audio_path.is_file():
        pytest.fail(f"ASR smoke audio file does not exist: {audio_path}")

    content_type = os.getenv("AI_VIDEO_ASR_TEST_MIME_TYPE") or mimetypes.guess_type(
        audio_path.name
    )[0]
    if not content_type or not content_type.startswith("audio/"):
        pytest.fail(
            "set AI_VIDEO_ASR_TEST_MIME_TYPE to an audio MIME type for an unrecognized file suffix"
        )

    async def exercise() -> None:
        audio_bytes = audio_path.read_bytes()
        probe = await FFprobeAudioValidator(
            timeout_seconds=int(os.getenv("AI_VIDEO_ASR_TEST_PROBE_TIMEOUT_SECONDS", "30"))
        ).validate_bytes(audio_bytes, content_type)
        client_timeout = int(
            os.getenv(
                "AI_VIDEO_ASR_TIMEOUT_SECONDS",
                os.getenv("AI_VIDEO_ASR_TEST_TIMEOUT_SECONDS", "180"),
            )
        )
        provider_class = {
            "aliyun_dashscope": AliyunDashScopeASRProvider,
            "siliconflow": SiliconFlowASRProvider,
        }.get(provider_name, OpenAICompatibleASRProvider)
        provider = provider_class(
            base_url=str(
                required.get("AI_VIDEO_ASR_BASE_URL")
                or AliyunDashScopeASRProvider.DEFAULT_BASE_URL
            ),
            api_key=str(required["AI_VIDEO_ASR_API_KEY"]),
            model=str(required["AI_VIDEO_ASR_MODEL"]),
            timeout_seconds=client_timeout,
        )
        try:
            result = await provider.transcribe(
                SubtitleASRGenerationRequest(
                    audio_bytes=audio_bytes,
                    mime_type=content_type,
                    language=os.getenv("AI_VIDEO_ASR_TEST_LANGUAGE", "zh-CN"),
                    audio_duration_seconds=probe.duration_seconds,
                    reference_text=os.getenv("AI_VIDEO_ASR_TEST_REFERENCE_TEXT") or None,
                )
            )
        finally:
            await provider.close()

        assert result.cues
        assert result.precision in {
            "segment_asr",
            "word_boundary",
            "transcript_sentence_estimate",
        }
        previous_end = 0.0
        for cue in result.cues:
            assert 0 <= cue.start_seconds < cue.end_seconds
            assert cue.start_seconds >= previous_end
            assert cue.end_seconds <= probe.duration_seconds + 5.0
            previous_end = cue.end_seconds
        assert result.metadata["audio_inspected"] is True

    asyncio.run(exercise())
