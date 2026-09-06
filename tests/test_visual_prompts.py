from app.media.visual_prompts import (
    DEFAULT_REFERENCE_NEGATIVE_PROMPT,
    DEFAULT_REFERENCE_STYLE,
    DEFAULT_VIDEO_NEGATIVE_PROMPT,
    DEFAULT_VIDEO_PROMPT_SUFFIX,
)


def test_reference_baseline_contains_single_subject_and_clean_composition_guards() -> None:
    assert "single-frame" in DEFAULT_REFERENCE_STYLE
    assert "clean silhouette" in DEFAULT_REFERENCE_STYLE
    assert "duplicate person" in DEFAULT_REFERENCE_NEGATIVE_PROMPT
    assert "multiple views" in DEFAULT_REFERENCE_NEGATIVE_PROMPT


def test_video_baseline_contains_short_shot_and_temporal_consistency_guards() -> None:
    assert "3 to 5 seconds" in DEFAULT_VIDEO_PROMPT_SUFFIX
    assert "subtle readable motion" in DEFAULT_VIDEO_PROMPT_SUFFIX
    assert "preserve the exact reference identity" in DEFAULT_VIDEO_PROMPT_SUFFIX
    assert "temporal inconsistency" in DEFAULT_VIDEO_NEGATIVE_PROMPT
    assert "motion smear" in DEFAULT_VIDEO_NEGATIVE_PROMPT
