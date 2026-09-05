from __future__ import annotations

import asyncio
import io
import math
import shutil
import wave

import pytest

from app.media.audio_duration import FFmpegAudioDurationFitter
from app.media.audio_validation import FFprobeAudioValidator
from app.providers.errors_audio import AudioDurationFitError


def _wav_bytes(duration_seconds: float) -> bytes:
    sample_rate = 16_000
    frame_count = round(sample_rate * duration_seconds)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(sample_rate)
        audio.writeframes(b"\x00\x00" * frame_count)
    return buffer.getvalue()


def test_ffmpeg_duration_fitter_shortens_audio_to_target_budget() -> None:
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg and ffprobe are required for duration fitting")

    async def exercise() -> None:
        source = _wav_bytes(6.0)
        fitter = FFmpegAudioDurationFitter(timeout_seconds=30, max_tempo_factor=1.6)

        result = await fitter.fit(source, "audio/wav", 6.0, 4.0)

        assert result.changed is True
        assert math.isclose(result.tempo_factor, 1.5, rel_tol=0.001)
        assert result.content_type == "audio/wav"
        assert result.metadata["duration_fit"] == "atempo"
        probe = await FFprobeAudioValidator().validate_bytes(result.content, result.content_type)
        assert 3.98 <= probe.duration_seconds <= 4.02

    asyncio.run(exercise())


def test_ffmpeg_duration_fitter_keeps_shorter_audio_unchanged() -> None:
    async def exercise() -> None:
        source = _wav_bytes(2.0)
        result = await FFmpegAudioDurationFitter().fit(source, "audio/wav", 2.0, 4.0)

        assert result.changed is False
        assert result.content == source
        assert result.tempo_factor == 1.0
        assert result.metadata["duration_fit"] == "not_needed"

    asyncio.run(exercise())


def test_ffmpeg_duration_fitter_rejects_unsafe_speed_up() -> None:
    async def exercise() -> None:
        with pytest.raises(AudioDurationFitError) as error:
            await FFmpegAudioDurationFitter(max_tempo_factor=1.5).fit(
                _wav_bytes(5.0),
                "audio/wav",
                5.0,
                3.0,
            )

        assert error.value.code == "AUDIO_DURATION_FIT_UNSAFE"

    asyncio.run(exercise())
