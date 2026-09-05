"""Small SRT subtitle primitives used by the FFmpeg media renderer."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class SubtitleCue:
    """One subtitle interval in seconds."""

    start_seconds: float
    end_seconds: float
    text: str

    def __post_init__(self) -> None:
        if not math.isfinite(self.start_seconds) or not math.isfinite(self.end_seconds):
            raise ValueError("Subtitle cue times must be finite")
        if self.start_seconds < 0 or self.end_seconds <= self.start_seconds:
            raise ValueError("Subtitle cue must have a positive, non-negative interval")
        if not self.text.strip():
            raise ValueError("Subtitle cue text must not be blank")


def write_srt(cues: list[SubtitleCue] | tuple[SubtitleCue, ...], path: Path) -> None:
    """Write deterministic UTF-8 SRT content for FFmpeg's subtitles filter."""

    path.write_bytes(serialize_srt(cues))


def serialize_srt(cues: list[SubtitleCue] | tuple[SubtitleCue, ...]) -> bytes:
    """Serialize validated cues as deterministic UTF-8 SRT bytes."""

    previous_end = 0.0
    lines: list[str] = []
    for index, cue in enumerate(cues, start=1):
        if cue.start_seconds < previous_end:
            raise ValueError("Subtitle cues must be ordered and must not overlap")
        lines.extend(
            (
                str(index),
                f"{_format_timestamp(cue.start_seconds)} --> {_format_timestamp(cue.end_seconds)}",
                cue.text.strip(),
                "",
            )
        )
        previous_end = cue.end_seconds
    return "\n".join(lines).encode("utf-8")


def parse_srt(content: bytes) -> tuple[SubtitleCue, ...]:
    """Parse a UTF-8 SRT artifact into validated subtitle cues."""

    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("Subtitle artifact must be UTF-8 encoded SRT") from exc

    blocks = re.split(r"\r?\n\s*\r?\n", text.strip())
    cues: list[SubtitleCue] = []
    expected_index = 1
    for block in blocks:
        lines = block.splitlines()
        if len(lines) < 3:
            raise ValueError("Subtitle SRT block must contain an index, timing and text")
        try:
            index = int(lines[0].strip())
        except ValueError as exc:
            raise ValueError("Subtitle SRT index must be an integer") from exc
        if index != expected_index:
            raise ValueError("Subtitle SRT indexes must be sequential starting at 1")
        match = re.fullmatch(
            r"\s*(\d{2}:\d{2}:\d{2}[,.]\d{3})\s*-->\s*"
            r"(\d{2}:\d{2}:\d{2}[,.]\d{3})(?:\s+.*)?\s*",
            lines[1],
        )
        if match is None:
            raise ValueError("Subtitle SRT timing line is invalid")
        start = _parse_timestamp(match.group(1))
        end = _parse_timestamp(match.group(2))
        try:
            cues.append(SubtitleCue(start, end, "\n".join(lines[2:]).strip()))
        except ValueError as exc:
            raise ValueError(f"Subtitle SRT cue {index} is invalid: {exc}") from exc
        expected_index += 1
    if not cues:
        raise ValueError("Subtitle SRT artifact must contain at least one cue")
    return tuple(cues)


def _format_timestamp(seconds: float) -> str:
    milliseconds = max(0, round(seconds * 1000))
    total_seconds, millis = divmod(milliseconds, 1000)
    minutes, secs = divmod(total_seconds, 60)
    hours, mins = divmod(minutes, 60)
    return f"{hours:02d}:{mins:02d}:{secs:02d},{millis:03d}"


def _parse_timestamp(value: str) -> float:
    match = re.fullmatch(r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})", value)
    if match is None:
        raise ValueError("Subtitle timestamp is invalid")
    hours, minutes, seconds, milliseconds = (int(part) for part in match.groups())
    return hours * 3600 + minutes * 60 + seconds + milliseconds / 1000
