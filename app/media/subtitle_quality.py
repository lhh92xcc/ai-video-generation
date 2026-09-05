"""Deterministic quality metrics for generated subtitle timelines."""

from __future__ import annotations

import math
import unicodedata
from dataclasses import dataclass
from typing import Sequence

from app.rendering.subtitles import SubtitleCue


@dataclass(frozen=True, slots=True)
class SubtitleQualityReport:
    """Machine-checkable subtitle metrics without claiming human quality."""

    reference_text_provided: bool
    cue_count: int
    non_empty_text_rate: float
    overlap_count: int
    ordering_violation_count: int
    out_of_bounds_count: int
    timeline_coverage_ratio: float
    max_gap_seconds: float
    normalized_reference_characters: int
    normalized_transcript_characters: int
    edit_distance: int | None
    character_error_rate: float | None
    exact_text_match: bool | None
    alignment_precision: str
    timing_quality: str
    structural_gate_passed: bool
    text_accuracy_passed: bool | None
    production_ready: bool
    recommendation: str
    warnings: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-serializable report for evaluation artifacts."""

        return {
            "reference_text_provided": self.reference_text_provided,
            "cue_count": self.cue_count,
            "non_empty_text_rate": self.non_empty_text_rate,
            "overlap_count": self.overlap_count,
            "ordering_violation_count": self.ordering_violation_count,
            "out_of_bounds_count": self.out_of_bounds_count,
            "timeline_coverage_ratio": self.timeline_coverage_ratio,
            "max_gap_seconds": self.max_gap_seconds,
            "normalized_reference_characters": self.normalized_reference_characters,
            "normalized_transcript_characters": self.normalized_transcript_characters,
            "edit_distance": self.edit_distance,
            "character_error_rate": self.character_error_rate,
            "exact_text_match": self.exact_text_match,
            "alignment_precision": self.alignment_precision,
            "timing_quality": self.timing_quality,
            "structural_gate_passed": self.structural_gate_passed,
            "text_accuracy_passed": self.text_accuracy_passed,
            "production_ready": self.production_ready,
            "recommendation": self.recommendation,
            "warnings": list(self.warnings),
        }


def evaluate_subtitle_cues(
    cues: Sequence[SubtitleCue],
    *,
    audio_duration_seconds: float,
    reference_text: str | None = None,
    alignment_precision: str = "unknown",
    max_character_error_rate: float = 0.05,
) -> SubtitleQualityReport:
    """Evaluate subtitle structure, text accuracy and timing limitations.

    A missing reference text deliberately leaves text accuracy unevaluated.
    Sentence-level estimates are marked as requiring review even when their
    structure is valid, because they do not prove acoustic alignment.
    """

    if not math.isfinite(audio_duration_seconds) or audio_duration_seconds <= 0:
        raise ValueError("audio_duration_seconds must be a positive finite number")
    if not math.isfinite(max_character_error_rate) or max_character_error_rate < 0:
        raise ValueError("max_character_error_rate must be a non-negative finite number")

    cue_list = tuple(cues)
    overlap_count = 0
    ordering_violation_count = 0
    out_of_bounds_count = 0
    invalid_interval_count = 0
    non_empty_count = 0
    previous_start = -math.inf
    previous_end = 0.0

    for cue in cue_list:
        start = float(cue.start_seconds)
        end = float(cue.end_seconds)
        if cue.text.strip():
            non_empty_count += 1
        if not math.isfinite(start) or not math.isfinite(end) or end <= start:
            invalid_interval_count += 1
        if start < previous_start:
            ordering_violation_count += 1
        if start < previous_end:
            overlap_count += 1
        if start < 0 or end > audio_duration_seconds:
            out_of_bounds_count += 1
        previous_start = start
        previous_end = max(previous_end, end)

    coverage_ratio = _union_duration(cue_list, audio_duration_seconds) / audio_duration_seconds
    max_gap_seconds = _max_gap(cue_list, audio_duration_seconds)
    transcript = _normalize_text("".join(cue.text for cue in cue_list))
    reference = _normalize_text(reference_text or "")
    reference_provided = bool(reference)

    edit_distance: int | None = None
    character_error_rate: float | None = None
    exact_text_match: bool | None = None
    text_accuracy_passed: bool | None = None
    if reference_provided:
        edit_distance = _levenshtein_distance(reference, transcript)
        character_error_rate = round(edit_distance / len(reference), 6)
        exact_text_match = reference == transcript
        text_accuracy_passed = character_error_rate <= max_character_error_rate

    structural_gate_passed = bool(cue_list) and non_empty_count == len(cue_list) and not (
        overlap_count
        or ordering_violation_count
        or out_of_bounds_count
        or invalid_interval_count
    )
    timing_quality = _timing_quality(alignment_precision)
    warnings: list[str] = []
    if not cue_list:
        warnings.append("SUBTITLE_NO_CUES")
    if overlap_count:
        warnings.append("SUBTITLE_TIMELINE_OVERLAP")
    if ordering_violation_count:
        warnings.append("SUBTITLE_TIMELINE_OUT_OF_ORDER")
    if out_of_bounds_count:
        warnings.append("SUBTITLE_TIMELINE_OUT_OF_BOUNDS")
    if invalid_interval_count:
        warnings.append("SUBTITLE_TIMELINE_INVALID_INTERVAL")
    if not reference_provided:
        warnings.append("SUBTITLE_REFERENCE_TEXT_NOT_PROVIDED")
    if timing_quality == "sentence_estimate":
        warnings.append("SUBTITLE_SENTENCE_LEVEL_ESTIMATE")
    if character_error_rate is not None and character_error_rate > max_character_error_rate:
        warnings.append("SUBTITLE_TEXT_CER_ABOVE_THRESHOLD")

    production_ready = (
        structural_gate_passed
        and text_accuracy_passed is True
        and timing_quality in {"word_boundary", "segment"}
    )
    if not structural_gate_passed or text_accuracy_passed is False:
        recommendation = "failed"
    elif production_ready:
        recommendation = "pass"
    else:
        recommendation = "needs_review"

    return SubtitleQualityReport(
        reference_text_provided=reference_provided,
        cue_count=len(cue_list),
        non_empty_text_rate=round(non_empty_count / len(cue_list), 6) if cue_list else 0.0,
        overlap_count=overlap_count,
        ordering_violation_count=ordering_violation_count,
        out_of_bounds_count=out_of_bounds_count,
        timeline_coverage_ratio=round(min(1.0, max(0.0, coverage_ratio)), 6),
        max_gap_seconds=round(max(0.0, max_gap_seconds), 6),
        normalized_reference_characters=len(reference),
        normalized_transcript_characters=len(transcript),
        edit_distance=edit_distance,
        character_error_rate=character_error_rate,
        exact_text_match=exact_text_match,
        alignment_precision=alignment_precision,
        timing_quality=timing_quality,
        structural_gate_passed=structural_gate_passed,
        text_accuracy_passed=text_accuracy_passed,
        production_ready=production_ready,
        recommendation=recommendation,
        warnings=tuple(warnings),
    )


def _normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(character for character in normalized if character.isalnum())


def _levenshtein_distance(left: str, right: str) -> int:
    if len(left) < len(right):
        left, right = right, left
    if not right:
        return len(left)

    previous = list(range(len(right) + 1))
    for left_index, left_character in enumerate(left, start=1):
        current = [left_index]
        for right_index, right_character in enumerate(right, start=1):
            insertion = current[right_index - 1] + 1
            deletion = previous[right_index] + 1
            substitution = previous[right_index - 1] + (left_character != right_character)
            current.append(min(insertion, deletion, substitution))
        previous = current
    return previous[-1]


def _union_duration(cues: Sequence[SubtitleCue], duration: float) -> float:
    intervals = sorted(
        (
            max(0.0, float(cue.start_seconds)),
            min(duration, float(cue.end_seconds)),
        )
        for cue in cues
        if math.isfinite(float(cue.start_seconds)) and math.isfinite(float(cue.end_seconds))
    )
    total = 0.0
    current_start: float | None = None
    current_end = 0.0
    for start, end in intervals:
        if end <= start:
            continue
        if current_start is None:
            current_start, current_end = start, end
        elif start <= current_end:
            current_end = max(current_end, end)
        else:
            total += current_end - current_start
            current_start, current_end = start, end
    if current_start is not None:
        total += current_end - current_start
    return total


def _max_gap(cues: Sequence[SubtitleCue], duration: float) -> float:
    intervals = sorted(
        (
            max(0.0, float(cue.start_seconds)),
            min(duration, float(cue.end_seconds)),
        )
        for cue in cues
        if math.isfinite(float(cue.start_seconds)) and math.isfinite(float(cue.end_seconds))
    )
    if not intervals:
        return duration
    max_gap = max(0.0, intervals[0][0])
    current_end = intervals[0][1]
    for start, end in intervals[1:]:
        if start > current_end:
            max_gap = max(max_gap, start - current_end)
        current_end = max(current_end, end)
    return max(max_gap, duration - current_end)


def _timing_quality(alignment_precision: str) -> str:
    return {
        "word_boundary": "word_boundary",
        "segment_asr": "segment",
        "transcript_sentence_estimate": "sentence_estimate",
        "sentence_estimate": "sentence_estimate",
        "mock_segment_estimate": "mock_estimate",
    }.get(alignment_precision, "unknown")
