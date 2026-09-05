"""Configurable text replacements used only for TTS pronunciation.

The text shown in subtitles and the text sent to a speech provider are kept
separate.  This lets a creator teach a provider how to read a name or a
polyphonic Chinese word without changing the reader-facing script.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
import math
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class PronunciationReplacement:
    source: str
    target: str


@dataclass(frozen=True, slots=True)
class PronunciationDictionary:
    """Longest-first literal replacements for provider-facing TTS text."""

    replacements: tuple[PronunciationReplacement, ...] = ()

    @classmethod
    def from_mapping(cls, mapping: dict[str, Any] | None) -> "PronunciationDictionary":
        if not mapping:
            return cls()

        replacements: list[PronunciationReplacement] = []
        for source, target in mapping.items():
            if not isinstance(source, str) or not isinstance(target, str):
                raise ValueError("pronunciation replacements must map strings to strings")
            source = source.strip()
            target = target.strip()
            if not source or not target:
                raise ValueError("pronunciation replacement source and target must not be blank")
            if "\n" in source or "\r" in source or "\t" in source:
                raise ValueError("pronunciation replacement source must be single-line text")
            if source == target:
                continue
            replacements.append(PronunciationReplacement(source, target))

        replacements.sort(key=lambda item: (-len(item.source), item.source))
        return cls(tuple(replacements))

    def apply(self, text: str) -> tuple[str, int, list[dict[str, str]]]:
        """Return provider text, replacement count and a safe audit summary."""

        result, _source_spans, count, applied = self.apply_with_source_spans(text)
        return result, count, applied

    def apply_with_source_spans(
        self,
        text: str,
    ) -> tuple[str, list[tuple[int, int]], int, list[dict[str, str]]]:
        """Apply replacements and retain the source span of every output char.

        Edge TTS reports timings against the provider-facing text.  A
        pronunciation entry can change the number of characters (for example,
        ``重明`` → ``崇明`` or a name → a phonetic spelling), so the spans let
        the TTS service map provider word boundaries back to the reader-facing
        script without changing subtitles.
        """

        units: list[tuple[str, tuple[int, int]]] = [
            (character, (index, index + 1)) for index, character in enumerate(text)
        ]
        applied: list[dict[str, str]] = []
        count = 0
        for replacement in self.replacements:
            source_units = list(replacement.source)
            if not source_units:
                continue
            next_units: list[tuple[str, tuple[int, int]]] = []
            index = 0
            occurrences = 0
            while index < len(units):
                candidate = [item[0] for item in units[index : index + len(source_units)]]
                if candidate == source_units:
                    matched_units = units[index : index + len(source_units)]
                    spans = [item[1] for item in matched_units]
                    source_span = (min(span[0] for span in spans), max(span[1] for span in spans))
                    next_units.extend(
                        (character, span)
                        for character, span in _map_replacement_spans(
                            replacement.target,
                            [item[1] for item in matched_units],
                            source_span,
                        )
                    )
                    index += len(source_units)
                    occurrences += 1
                else:
                    next_units.append(units[index])
                    index += 1
            if occurrences:
                units = next_units
                count += occurrences
                applied.append(
                    {
                        "source": replacement.source,
                        "target": replacement.target,
                        "count": str(occurrences),
                    }
                )
        return (
            "".join(item[0] for item in units),
            [item[1] for item in units],
            count,
            applied,
        )


def _map_replacement_spans(
    target: str,
    source_spans: list[tuple[int, int]],
    whole_source_span: tuple[int, int],
) -> list[tuple[str, tuple[int, int]]]:
    """Assign replacement characters to source ranges without losing order.

    Equal-length pronunciation substitutions are mapped one-to-one, which keeps
    a replacement such as ``重明`` → ``崇明`` from producing the duplicated
    source cue ``重明`` for both provider events.  When the lengths differ, the
    source ranges are distributed monotonically; a phonetic expansion may still
    map several provider characters to one source character, but it never moves
    backward or invents source text.
    """

    if not target:
        return []
    if not source_spans:
        return [(character, whole_source_span) for character in target]
    if len(target) == len(source_spans):
        return list(zip(target, source_spans, strict=True))

    mapped: list[tuple[str, tuple[int, int]]] = []
    source_count = len(source_spans)
    target_count = len(target)
    for index, character in enumerate(target):
        source_index = min(source_count - 1, (index * source_count) // target_count)
        mapped.append((character, source_spans[source_index]))
    return mapped


def map_word_boundaries_to_source(
    raw_boundaries: object,
    source_text: str,
    tts_text: str,
    source_spans: list[tuple[int, int]],
) -> list[dict[str, object]] | None:
    """Map sequential provider timing events from ``tts_text`` to source text.

    The mapping is intentionally conservative.  If an event cannot be found
    in order, ``None`` is returned and callers can keep the original provider
    timings while marking the text basis as unmapped.
    """

    if not isinstance(raw_boundaries, list) or len(source_spans) != len(tts_text):
        return None
    if not source_text and tts_text:
        return None

    mapped: list[dict[str, object]] = []
    cursor = 0
    for item in raw_boundaries:
        if not isinstance(item, dict):
            return None
        event_text = item.get("text")
        start = item.get("start_seconds")
        end = item.get("end_seconds")
        if not isinstance(event_text, str) or not event_text.strip():
            continue
        if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
            return None
        if not math.isfinite(float(start)) or not math.isfinite(float(end)) or float(end) <= float(start):
            return None
        normalized_event = event_text.strip()
        match_start = tts_text.find(normalized_event, cursor)
        if match_start < 0:
            compact_text = "".join(character for character in event_text if not character.isspace())
            suffix = tts_text[cursor:]
            compact_tts = "".join(character for character in suffix if not character.isspace())
            compact_start = compact_tts.find(compact_text)
            if compact_start < 0:
                return None
            # The compact fallback is only safe when the event is after the
            # previous event.  Reconstructing whitespace positions is not
            # needed for Chinese WordBoundary events, but this guard prevents
            # silently mapping an out-of-order provider response.
            non_space_positions = [
                cursor + position
                for position, character in enumerate(suffix)
                if not character.isspace()
            ]
            if compact_start >= len(non_space_positions):
                return None
            match_start = non_space_positions[compact_start]
            match_end = non_space_positions[min(
                len(non_space_positions) - 1,
                compact_start + len(compact_text) - 1,
            )] + 1
        else:
            match_end = match_start + len(normalized_event)

        if match_start < cursor or match_end > len(source_spans) or match_end <= match_start:
            return None
        source_start = min(span[0] for span in source_spans[match_start:match_end])
        source_end = max(span[1] for span in source_spans[match_start:match_end])
        if source_start < 0 or source_end > len(source_text) or source_end <= source_start:
            return None
        mapped.append(
            {
                "start_seconds": round(float(start), 6),
                "end_seconds": round(float(end), 6),
                "text": source_text[source_start:source_end],
            }
        )
        cursor = max(cursor, match_end)
    return mapped


def load_pronunciation_dictionary(path: str | Path | None) -> PronunciationDictionary:
    """Load a TOML dictionary; an absent optional file means no replacements."""

    if path is None or not str(path).strip():
        return PronunciationDictionary()
    dictionary_path = Path(path)
    if not dictionary_path.exists():
        return PronunciationDictionary()
    with dictionary_path.open("rb") as dictionary_file:
        payload = tomllib.load(dictionary_file)
    replacements = payload.get("replacements", {})
    if not isinstance(replacements, dict):
        raise ValueError("pronunciation dictionary must contain a [replacements] table")
    return PronunciationDictionary.from_mapping(replacements)
