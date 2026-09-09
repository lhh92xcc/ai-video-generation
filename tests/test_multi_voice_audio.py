from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path

import pytest

from app.media.audio_validation import FFprobeAudioValidator
from app.media.multi_voice_audio import MultiVoiceAudioError, concatenate_audio_segments


def _tone_bytes(tmp_path: Path, name: str, duration: float, frequency: int) -> bytes:
    path = tmp_path / f"{name}.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={frequency}:sample_rate=24000",
            "-t",
            str(duration),
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            str(path),
        ],
        check=True,
    )
    return path.read_bytes()


def test_measured_multi_voice_mix_keeps_timeline_and_smooths_edges(tmp_path: Path) -> None:
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg and ffprobe are required for multi-voice tests")

    async def exercise() -> None:
        first = _tone_bytes(tmp_path, "first", 0.65, 440)
        second = _tone_bytes(tmp_path, "second", 0.55, 660)
        content = await concatenate_audio_segments(
            [first, second],
            ["audio/wav", "audio/wav"],
            [0.18, 0.0],
            segment_durations_seconds=[0.65, 0.55],
            transition_fade_ms=24,
            room_tone_enabled=True,
            room_tone_db=-52.0,
        )
        probe = await FFprobeAudioValidator().validate_bytes(content, "audio/wav")

        assert probe.sample_rate == 24000
        assert probe.channel_count == 1
        assert probe.duration_seconds == pytest.approx(1.38, abs=0.02)

    asyncio.run(exercise())


def test_measured_multi_voice_mix_rejects_invalid_duration() -> None:
    async def exercise() -> None:
        with pytest.raises(MultiVoiceAudioError) as error:
            await concatenate_audio_segments(
                [b"first"],
                ["audio/wav"],
                [0.0],
                segment_durations_seconds=[0.0],
            )
        assert error.value.code == "AUDIO_COMPOSITION_INPUT_INVALID"

    asyncio.run(exercise())
