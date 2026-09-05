from __future__ import annotations

import pytest

from app.media.subtitle_quality import evaluate_subtitle_cues
from app.rendering.subtitles import SubtitleCue


def test_subtitle_quality_reports_exact_text_and_valid_segment_timing() -> None:
    report = evaluate_subtitle_cues(
        (
            SubtitleCue(0, 1.5, "第一句。"),
            SubtitleCue(1.5, 3, "第二句！"),
        ),
        audio_duration_seconds=3,
        reference_text="第一句。第二句！",
        alignment_precision="segment_asr",
    )

    assert report.character_error_rate == 0.0
    assert report.exact_text_match is True
    assert report.timeline_coverage_ratio == 1.0
    assert report.structural_gate_passed is True
    assert report.text_accuracy_passed is True
    assert report.production_ready is True
    assert report.recommendation == "pass"


def test_subtitle_quality_marks_sentence_estimate_for_review_without_reference() -> None:
    report = evaluate_subtitle_cues(
        (SubtitleCue(0.1, 1.2, "估算字幕"),),
        audio_duration_seconds=2,
        alignment_precision="transcript_sentence_estimate",
    )

    assert report.reference_text_provided is False
    assert report.text_accuracy_passed is None
    assert report.structural_gate_passed is True
    assert report.production_ready is False
    assert report.recommendation == "needs_review"
    assert "SUBTITLE_REFERENCE_TEXT_NOT_PROVIDED" in report.warnings
    assert "SUBTITLE_SENTENCE_LEVEL_ESTIMATE" in report.warnings


def test_subtitle_quality_detects_overlap_and_text_error() -> None:
    report = evaluate_subtitle_cues(
        (
            SubtitleCue(0, 2, "甲乙"),
            SubtitleCue(1, 3, "丙"),
        ),
        audio_duration_seconds=3,
        reference_text="甲乙丁",
        alignment_precision="segment_asr",
        max_character_error_rate=0.2,
    )

    assert report.overlap_count == 1
    assert report.structural_gate_passed is False
    assert report.character_error_rate == pytest.approx(1 / 3)
    assert report.text_accuracy_passed is False
    assert report.recommendation == "failed"
    assert "SUBTITLE_TIMELINE_OVERLAP" in report.warnings
    assert "SUBTITLE_TEXT_CER_ABOVE_THRESHOLD" in report.warnings


def test_subtitle_quality_rejects_invalid_audio_duration() -> None:
    with pytest.raises(ValueError, match="audio_duration_seconds"):
        evaluate_subtitle_cues((), audio_duration_seconds=0)
