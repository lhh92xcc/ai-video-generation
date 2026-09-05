from __future__ import annotations

import asyncio
import io
import struct
import wave

import pytest

import app.media.audio_pause_compaction as pause_compaction_module
from app.media.audio_pause_compaction import FFmpegNarrationPauseCompactor
from app.media.audio_validation import FFprobeAudioValidator
from app.providers.errors_audio import AudioPauseCompactionError


SAMPLE_RATE = 24_000


def _wav_bytes(duration_seconds: float, voiced_ranges: tuple[tuple[float, float], ...] = ()) -> bytes:
    """Create a deterministic mono fixture with optional voiced tone ranges."""

    frame_count = round(SAMPLE_RATE * duration_seconds)
    ranges = tuple(
        (round(start * SAMPLE_RATE), round(end * SAMPLE_RATE))
        for start, end in voiced_ranges
    )
    frames = bytearray()
    for index in range(frame_count):
        voiced = any(start <= index < end for start, end in ranges)
        if voiced:
            # A stable tone makes the voiced sample lengths observable after
            # FFmpeg trims and concatenates the silent gap.
            value = 10_000 if (index // 24) % 2 == 0 else -10_000
        else:
            value = 0
        frames.extend(struct.pack("<h", value))

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(SAMPLE_RATE)
        audio.writeframes(bytes(frames))
    return buffer.getvalue()


def _boundaries() -> list[dict[str, object]]:
    return [
        {"start_seconds": 0.0, "end_seconds": 0.5, "text": "甲"},
        {"start_seconds": 1.5, "end_seconds": 2.0, "text": "乙"},
    ]


def test_pause_compaction_shortens_long_pause_and_remaps_word_boundaries() -> None:
    async def exercise() -> None:
        source = _wav_bytes(2.2, ((0.0, 0.5), (1.5, 2.0)))
        result = await FFmpegNarrationPauseCompactor(
            soft_keep_seconds=0.20,
            trailing_keep_seconds=0.2,
        ).compact(
            source,
            "audio/wav; charset=binary",
            2.2,
            "甲，乙",
            _boundaries(),
        )

        assert result.changed is True
        assert result.metadata["pause_compaction"] == "applied"
        assert result.metadata["filter"] == "gap_trim_concat"
        assert result.removed_seconds == pytest.approx(0.8, abs=0.01)
        assert result.metadata["pause_cut_count"] == 1
        assert result.word_boundaries[0]["start_seconds"] == pytest.approx(0.0)
        assert result.word_boundaries[0]["end_seconds"] == pytest.approx(0.5)
        assert result.word_boundaries[1]["start_seconds"] == pytest.approx(0.7, abs=0.01)
        assert result.word_boundaries[1]["end_seconds"] == pytest.approx(1.2, abs=0.01)

        probe = await FFprobeAudioValidator().validate_bytes(result.content, result.content_type)
        assert probe.duration_seconds == pytest.approx(1.4, abs=0.03)

    asyncio.run(exercise())


def test_pause_compaction_keeps_voiced_regions_at_original_duration() -> None:
    async def exercise() -> None:
        source = _wav_bytes(2.2, ((0.0, 0.5), (1.5, 2.0)))
        result = await FFmpegNarrationPauseCompactor(
            soft_keep_seconds=0.20,
            trailing_keep_seconds=0.2,
        ).compact(source, "audio/wav", 2.2, "甲，乙", _boundaries())

        with wave.open(io.BytesIO(result.content), "rb") as audio:
            samples = [audio.readframes(1) for _ in range(audio.getnframes())]
            values = [abs(struct.unpack("<h", sample)[0]) for sample in samples]

        voiced_runs: list[int] = []
        current = 0
        for value in values:
            if value > 1_000:
                current += 1
            elif current:
                voiced_runs.append(current)
                current = 0
        if current:
            voiced_runs.append(current)

        # The two original 0.5 s voiced regions are retained; only the gap
        # between them is shortened. A small codec/filter edge tolerance is
        # allowed, but neither region may be time-stretched.
        assert len(voiced_runs) == 2
        assert voiced_runs[0] == pytest.approx(SAMPLE_RATE * 0.5, abs=SAMPLE_RATE * 0.02)
        assert voiced_runs[1] == pytest.approx(SAMPLE_RATE * 0.5, abs=SAMPLE_RATE * 0.02)
        assert result.metadata["voiced_audio_time_stretched"] is False
        assert result.word_boundaries[1]["end_seconds"] - result.word_boundaries[1]["start_seconds"] == pytest.approx(
            0.5,
            abs=0.001,
        )

    asyncio.run(exercise())


def test_pause_compaction_can_limit_cuts_to_selected_scene_boundaries() -> None:
    async def exercise() -> None:
        source = _wav_bytes(
            3.5,
            ((0.0, 0.5), (1.5, 2.0), (3.0, 3.5)),
        )
        result = await FFmpegNarrationPauseCompactor(
            soft_keep_seconds=0.20,
            trailing_keep_seconds=0.2,
            allowed_gap_keep_seconds=0.18,
        ).compact(
            source,
            "audio/wav",
            3.5,
            "甲，乙，丙",
            [
                {"start_seconds": 0.0, "end_seconds": 0.5, "text": "甲"},
                {"start_seconds": 1.5, "end_seconds": 2.0, "text": "乙"},
                {"start_seconds": 3.0, "end_seconds": 3.5, "text": "丙"},
            ],
            allowed_gap_after_character_offsets={2},
        )

        assert result.changed is True
        assert result.metadata["compaction_scope"] == "selected_character_boundaries_uniform_keep"
        assert result.metadata["pause_cut_count"] == 1
        assert result.removed_seconds == pytest.approx(0.82, abs=0.01)
        assert result.metadata["allowed_gap_keep_seconds"] == pytest.approx(0.18)
        assert result.metadata["pause_cuts"][0]["kept_gap_seconds"] == pytest.approx(0.18)
        assert result.word_boundaries[1]["start_seconds"] == pytest.approx(1.5, abs=0.001)
        assert result.word_boundaries[2]["start_seconds"] == pytest.approx(2.18, abs=0.01)

        probe = await FFprobeAudioValidator().validate_bytes(result.content, result.content_type)
        assert probe.duration_seconds == pytest.approx(2.68, abs=0.03)

    asyncio.run(exercise())


def test_pause_compaction_without_boundaries_is_a_safe_noop() -> None:
    async def exercise() -> None:
        source = _wav_bytes(1.0)
        result = await FFmpegNarrationPauseCompactor().compact(
            source,
            "audio/wav",
            1.0,
            "没有时间边界",
            [],
        )

        assert result.changed is False
        assert result.content == source
        assert result.content_type == "audio/wav"
        assert result.metadata["pause_compaction"] == "not_needed"
        assert result.metadata["word_boundaries_remapped"] is False

    asyncio.run(exercise())


def test_pause_compaction_reports_stable_error_when_ffmpeg_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pause_compaction_module.shutil, "which", lambda _binary: None)

    async def exercise() -> None:
        with pytest.raises(AudioPauseCompactionError) as error:
            await FFmpegNarrationPauseCompactor().compact(
                _wav_bytes(2.0),
                "audio/wav",
                2.0,
                "甲，乙",
                _boundaries(),
            )

        assert error.value.code == "AUDIO_PAUSE_COMPACTION_UNAVAILABLE"

    asyncio.run(exercise())
