"""Deterministic Chinese narration boundaries and pause defaults.

These helpers are retained for subtitle previews and future forced-alignment
work. The local ChatTTS runner must synthesize a complete scene as one
waveform; it must not use these boundaries to create independently generated
audio fragments.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass


_STRONG_BOUNDARIES = frozenset("。！？!?；;…")
_SOFT_BOUNDARIES = frozenset("，,、：:")
_BOUNDARY_PATTERN = re.compile(
    r"[^。！？!?；;…，,：:\n]+[。！？!?；;…，,：:]+|[^。！？!?；;…，,：:\n]+$"
)


@dataclass(frozen=True, slots=True)
class NarrationTextSegment:
    """One short TTS unit and the pause after it."""

    text: str
    boundary: str
    pause_after_seconds: float

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def split_narration_text(
    text: str,
    *,
    max_segment_characters: int = 22,
) -> tuple[NarrationTextSegment, ...]:
    """Split Chinese narration into short, punctuation-aware TTS units.

    Strong sentence punctuation always creates a boundary. Long sentences are
    then cut at the latest comma/colon-like boundary within the configured
    limit; a hard character cut is only used when no punctuation is available.
    The final segment never receives trailing silence.
    """

    if max_segment_characters < 4:
        raise ValueError("max_segment_characters must be at least 4")
    normalized = re.sub(r"\s+", " ", text).strip()
    if not normalized:
        return ()

    units: list[str] = []
    for unit in _BOUNDARY_PATTERN.findall(normalized):
        cleaned = unit.strip()
        if cleaned:
            units.extend(_split_long_unit(cleaned, max_segment_characters))

    segments: list[NarrationTextSegment] = []
    for unit in units:
        boundary = _last_boundary(unit)
        segments.append(
            NarrationTextSegment(
                text=unit,
                boundary=boundary,
                pause_after_seconds=_pause_for_boundary(boundary),
            )
        )
    if segments:
        last = segments[-1]
        segments[-1] = NarrationTextSegment(
            text=last.text,
            boundary=last.boundary,
            pause_after_seconds=0.0,
        )
    return tuple(segments)


def _split_long_unit(unit: str, max_segment_characters: int) -> list[str]:
    remaining = unit
    pieces: list[str] = []
    while len(remaining) > max_segment_characters:
        search_window = remaining[: max_segment_characters + 1]
        soft_positions = [
            index + 1
            for index, character in enumerate(search_window)
            if character in _SOFT_BOUNDARIES and index + 1 >= 6
        ]
        cut = soft_positions[-1] if soft_positions else max_segment_characters
        pieces.append(remaining[:cut].strip())
        remaining = remaining[cut:].lstrip()
    if remaining:
        pieces.append(remaining)
    return pieces


def _last_boundary(text: str) -> str:
    for character in reversed(text.rstrip()):
        if character in _STRONG_BOUNDARIES or character in _SOFT_BOUNDARIES:
            return character
    return ""


def _pause_for_boundary(boundary: str) -> float:
    if boundary in "。！？!?":
        return 0.28
    if boundary in "；;":
        return 0.22
    if boundary in "…":
        return 0.32
    if boundary in "，,、：:":
        return 0.12
    return 0.10
