from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path

import pytest

from app.media.audio_pacing import (
    AudioPacingSegment,
    FFmpegAudioPacer,
    build_narration_pacing_plan,
    build_paced_word_boundaries,
)
from app.media.audio_validation import FFprobeAudioValidator


def _raw_boundaries() -> list[dict[str, object]]:
    return [
        {"start_seconds": 0.0, "end_seconds": 0.8, "text": "甲乙丙丁"},
        {"start_seconds": 1.0, "end_seconds": 1.4, "text": "戊己"},
        {"start_seconds": 1.6, "end_seconds": 2.6, "text": "庚辛壬癸"},
    ]


def test_narration_pacing_plan_keeps_one_contiguous_source_timeline() -> None:
    scenes = [
        ("第一场", "甲乙丙丁，戊己", "画面一"),
        ("第二场", "庚辛壬癸", "画面二"),
    ]
    plan = build_narration_pacing_plan(
        scenes,
        _raw_boundaries(),
        2.8,
        target_characters_per_second=3.0,
        min_tempo_factor=0.82,
    )

    assert plan is not None
    assert plan.phrases
    assert plan.segments[0].source_start_seconds == 0
    assert plan.segments[-1].source_end_seconds == 2.8
    assert all(
        left.source_end_seconds == right.source_start_seconds
        for left, right in zip(plan.segments[:-1], plan.segments[1:], strict=True)
    )
    # The short phrase is intentionally fast enough to receive a correction;
    # a slow phrase is never accelerated by this policy.
    assert min(phrase.tempo_factor for phrase in plan.phrases) >= 0.82
    assert max(phrase.tempo_factor for phrase in plan.phrases) <= 1.0


def test_paced_word_boundaries_remain_ordered() -> None:
    scenes = [
        ("第一场", "甲乙丙丁，戊己", "画面一"),
        ("第二场", "庚辛壬癸", "画面二"),
    ]
    raw = _raw_boundaries()
    plan = build_narration_pacing_plan(scenes, raw, 2.8)
    assert plan is not None
    mapped = build_paced_word_boundaries(plan, raw, 3.0, 0.07)

    assert mapped is not None
    assert len(mapped) == len(raw)
    assert all(
        left["end_seconds"] <= right["start_seconds"]
        for left, right in zip(mapped[:-1], mapped[1:], strict=True)
    )
    assert mapped[-1]["end_seconds"] <= 3.0


def test_ffmpeg_audio_pacer_outputs_a_single_playable_wav(tmp_path: Path) -> None:
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg and ffprobe are required for audio pacing tests")

    source = tmp_path / "source.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=24000",
            "-t",
            "2",
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            str(source),
        ],
        check=True,
    )

    async def exercise() -> None:
        result = await FFmpegAudioPacer(crossfade_ms=70).pace(
            source.read_bytes(),
            "audio/wav",
            2.0,
            [
                AudioPacingSegment(0.0, 1.0, 0.9, "fast-phrase"),
                AudioPacingSegment(1.0, 2.0, 1.0, "normal-phrase"),
            ],
        )
        probe = await FFprobeAudioValidator().validate_bytes(result.content, result.content_type)

        assert result.changed is True
        assert result.content_type == "audio/wav"
        assert probe.sample_rate == 24000
        assert probe.channel_count == 1
        assert 2.0 < probe.duration_seconds < 2.2
        assert result.metadata["continuous_waveform"] is True

    asyncio.run(exercise())
