from __future__ import annotations

import asyncio
import io
import wave

from app.media.audio_normalization import FFmpegAudioNormalizer
from app.media.audio_validation import FFprobeAudioValidator


def _wav_bytes() -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(b"\x00\x00" * 8000)
    return buffer.getvalue()


def test_ffmpeg_audio_normalizer_outputs_stereo_48khz_wav() -> None:
    async def exercise() -> None:
        normalized = await FFmpegAudioNormalizer(timeout_seconds=30).normalize(
            _wav_bytes(),
            "audio/wav",
        )
        assert normalized.content_type == "audio/wav"
        assert normalized.metadata["filter"] == "loudnorm"
        assert normalized.metadata["target_i_lufs"] == -16.0

        probe = await FFprobeAudioValidator().validate_bytes(
            normalized.content,
            normalized.content_type,
        )
        assert probe.sample_rate == 48000
        assert probe.channel_count == 2
        assert probe.duration_seconds > 0

    asyncio.run(exercise())
