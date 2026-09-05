"""Canonical text shaping for continuous narration synthesis.

Narration is sent to a TTS Provider as one stream whenever the workflow asks
for one episode-level voice track.  Newlines, invisible separator characters
and spoken speaker labels make some providers introduce an audible restart or
an unexpectedly long pause, so the application keeps one small, shared text
policy at the service boundary.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from typing import Any


_INVISIBLE_SEPARATORS = "\u200b\u200c\u200d\u2060\ufeff"
_WHITESPACE_PATTERN = re.compile(r"\s+")
_STRONG_TERMINATORS = frozenset("。！？!?；;…")
_CLOSING_PUNCTUATION = frozenset("）)]】》」』”’\")'")
_SOFT_TERMINATORS = frozenset("，,、：:")
_CONTINUOUS_SUFFIX_PATTERN = re.compile(r"[。！？!?；;…，,、：:]+$")


def normalize_narration_text(text: str) -> str:
    """Collapse layout whitespace and remove invisible TTS separators.

    Newlines are an editing/layout concern, not an instruction to restart
    acoustic generation.  They therefore become a single ordinary space.  A
    provider still receives all visible punctuation from the user's text.
    """

    if not isinstance(text, str):
        raise TypeError("narration text must be a string")
    cleaned = text.translate({ord(character): None for character in _INVISIBLE_SEPARATORS})
    cleaned = "".join(
        " " if ord(character) < 32 and character not in "\t\n\r" else character
        for character in cleaned
    )
    return _WHITESPACE_PATTERN.sub(" ", cleaned).strip()


def ensure_narration_unit_boundary(text: str) -> str:
    """Make one narration unit end naturally before the next unit begins.

    Existing sentence punctuation is preserved.  A missing or dangling soft
    punctuation mark becomes a full stop, which is much more predictable for
    Chinese neural voices than concatenating two clauses directly.
    """

    cleaned = normalize_narration_text(text)
    if not cleaned:
        return ""

    closing = ""
    core = cleaned
    while core and core[-1] in _CLOSING_PUNCTUATION:
        closing = core[-1] + closing
        core = core[:-1].rstrip()
    if not core:
        return cleaned
    if core[-1] in _STRONG_TERMINATORS:
        return core + closing
    if core[-1] in _SOFT_TERMINATORS:
        core = core[:-1].rstrip()
    return f"{core}。{closing}"


def join_narration_units(
    units: Iterable[str],
    *,
    separator: str = " ",
    preserve_terminal_punctuation: bool = False,
) -> str:
    """Join scene/line units into one continuous, punctuation-safe text.

    The default is intentionally a soft ordinary-space boundary.  An empty
    separator is also allowed when the provider should infer a transition from
    the surrounding Chinese characters.  Full stops at every visual cut make
    neural voices insert a new sentence onset and a long pause; direct
    concatenation, however, can turn ``钟声`` + ``今晚`` into one ambiguous
    phrase.  A single space is the stable compromise for a continuous episode
    waveform.  Callers that need explicit sentence boundaries can opt into
    ``preserve_terminal_punctuation``.
    """

    if any(character in separator for character in "\r\n\t"):
        raise ValueError("separator must be a single-line string")
    normalized_units: list[str] = []
    for unit in units:
        cleaned = normalize_narration_text(unit)
        if not cleaned:
            continue
        if preserve_terminal_punctuation:
            cleaned = ensure_narration_unit_boundary(cleaned)
        else:
            closing = ""
            core = cleaned
            while core and core[-1] in _CLOSING_PUNCTUATION:
                closing = core[-1] + closing
                core = core[:-1].rstrip()
            core = _CONTINUOUS_SUFFIX_PATTERN.sub("", core).rstrip()
            cleaned = f"{core}{closing}"
        if cleaned:
            normalized_units.append(cleaned)
    return separator.join(normalized_units)


def join_continuous_narration_units(
    units: Iterable[str],
    *,
    beat_size: int = 4,
    soft_separator: str = "，",
    beat_separator: str = "，",
) -> str:
    """Join narration units without turning every editing boundary into a stop.

    Scene and dialogue boundaries are often much more frequent than spoken
    sentence boundaries.  A full stop after every unit makes neural TTS
    restart its prosody and can sound like separately generated clips.  This
    policy uses soft commas between editing units and one final sentence
    terminator. ``beat_size`` and ``beat_separator`` remain configurable for
    callers that have real story-beat boundaries, but the default no longer
    invents semicolon pauses at arbitrary scene/line counts. The caller still
    sends the returned value as one TTS request and keeps one waveform.
    """

    if beat_size < 1:
        raise ValueError("beat_size must be at least 1")
    if any(character in soft_separator for character in "\r\n\t"):
        raise ValueError("soft_separator must be a single-line string")
    if any(character in beat_separator for character in "\r\n\t"):
        raise ValueError("beat_separator must be a single-line string")

    normalized_units: list[str] = []
    final_source = ""
    for unit in units:
        cleaned = normalize_narration_text(unit)
        if not cleaned:
            continue
        final_source = cleaned
        closing = ""
        core = cleaned
        while core and core[-1] in _CLOSING_PUNCTUATION:
            closing = core[-1] + closing
            core = core[:-1].rstrip()
        core = _CONTINUOUS_SUFFIX_PATTERN.sub("", core).rstrip()
        if core:
            normalized_units.append(f"{core}{closing}")

    if not normalized_units:
        return ""

    parts: list[str] = []
    for index, unit in enumerate(normalized_units, start=1):
        parts.append(unit)
        if index < len(normalized_units):
            parts.append(
                beat_separator if index % beat_size == 0 else soft_separator
            )

    final_core = final_source.rstrip()
    while final_core and final_core[-1] in _CLOSING_PUNCTUATION:
        final_core = final_core[:-1].rstrip()
    final_terminator = (
        final_core[-1]
        if final_core and final_core[-1] in "！？!?"
        else "。"
    )
    return "".join(parts) + final_terminator


def join_narration_units_with_boundaries(
    units: Iterable[str],
    boundary_separators: Sequence[str],
    *,
    final_separator: str = "。",
    preserve_unit_terminal_punctuation: bool = False,
) -> str:
    """Join one waveform with an explicit, editorially natural sentence plan.

    A visual cut is not automatically a sentence boundary.  Callers that
    already know which clauses belong together can provide one separator for
    each boundary, for example ``("，", "。", "，")``.  The returned text is
    still intended for one provider request; the separators only guide the
    provider's prosody and do not create audio files or waveform joins.

    ``preserve_unit_terminal_punctuation`` is for an editorial script whose
    units already carry the punctuation that belongs to the spoken sentence.
    It lets a caller keep those punctuation marks while using an empty
    separator at a visual boundary.  This is useful when visual shot cuts
    must not manufacture a new spoken pause.
    """

    if any(character in final_separator for character in "\r\n\t"):
        raise ValueError("final_separator must be a single-line string")

    normalized_units: list[str] = []
    for unit in units:
        cleaned = normalize_narration_text(unit)
        if not cleaned:
            continue
        if preserve_unit_terminal_punctuation:
            normalized_units.append(cleaned)
            continue
        closing = ""
        core = cleaned
        while core and core[-1] in _CLOSING_PUNCTUATION:
            closing = core[-1] + closing
            core = core[:-1].rstrip()
        core = _CONTINUOUS_SUFFIX_PATTERN.sub("", core).rstrip()
        if core:
            normalized_units.append(f"{core}{closing}")

    expected_count = max(0, len(normalized_units) - 1)
    if len(boundary_separators) != expected_count:
        raise ValueError(
            "boundary_separators must contain exactly one separator per unit boundary"
        )
    if any(
        any(character in separator for character in "\r\n\t")
        for separator in boundary_separators
    ):
        raise ValueError("boundary_separators must be single-line strings")

    if not normalized_units:
        return ""

    parts: list[str] = []
    for index, unit in enumerate(normalized_units):
        parts.append(unit)
        if index < expected_count:
            parts.append(boundary_separators[index])
    if final_separator:
        parts.append(final_separator)
    return "".join(parts)


def join_editorial_narration_units(
    units: Iterable[str],
    *,
    missing_boundary_separator: str = "，",
) -> str:
    """Join episode narration while preserving the script's punctuation.

    Scene and dialogue records are editing units, not independent TTS clips.
    A punctuation mark authored in a unit is kept as-is and no second
    separator is inserted. Only a unit with no terminal punctuation receives
    the configured fallback comma. The complete result is still sent to the
    provider as one request.
    """

    if any(character in missing_boundary_separator for character in "\r\n\t"):
        raise ValueError("missing_boundary_separator must be a single-line string")

    normalized_units: list[str] = []
    for unit in units:
        cleaned = normalize_narration_text(unit)
        if cleaned:
            normalized_units.append(cleaned)
    if not normalized_units:
        return ""

    def terminal_kind(unit: str) -> str:
        core = unit.rstrip()
        while core and core[-1] in _CLOSING_PUNCTUATION:
            core = core[:-1].rstrip()
        if core and core[-1] in _STRONG_TERMINATORS:
            return "strong"
        if core and core[-1] in _SOFT_TERMINATORS:
            return "soft"
        return "missing"

    separators = [
        "" if terminal_kind(unit) != "missing" else missing_boundary_separator
        for unit in normalized_units[:-1]
    ]
    joined = join_narration_units_with_boundaries(
        normalized_units,
        separators,
        final_separator="",
        preserve_unit_terminal_punctuation=True,
    )
    if terminal_kind(normalized_units[-1]) != "strong":
        return ensure_narration_unit_boundary(joined)
    return joined


def build_episode_narration_text(script: Any) -> str:
    """Build spoken text without vocalizing speaker names.

    Scene and dialogue units are editing boundaries, not separate audio files.
    Preserve punctuation authored by the script so the provider can keep a
    coherent sentence rhythm. A comma is only added when an input unit has no
    punctuation at all; visual cuts never create independent waveforms.
    """

    units: list[str] = []
    for scene in script.content.scenes:
        narration = getattr(scene, "narration", "")
        if normalize_narration_text(narration):
            units.append(narration)
        for dialogue in scene.dialogues:
            dialogue_text = getattr(dialogue, "text", "")
            if normalize_narration_text(dialogue_text):
                units.append(dialogue_text)
    return join_editorial_narration_units(units)
