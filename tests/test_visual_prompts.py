from app.media.visual_prompts import (
    DEFAULT_REFERENCE_NEGATIVE_PROMPT,
    DEFAULT_REFERENCE_STYLE,
    DEFAULT_VIDEO_NEGATIVE_PROMPT,
    DEFAULT_VIDEO_PROMPT_SUFFIX,
    build_video_motion_prompt,
)


def test_reference_baseline_contains_single_subject_and_clean_composition_guards() -> None:
    assert "single-frame" in DEFAULT_REFERENCE_STYLE
    assert "2D manhwa" in DEFAULT_REFERENCE_STYLE
    assert "clean silhouette" in DEFAULT_REFERENCE_STYLE
    assert "duplicate person" in DEFAULT_REFERENCE_NEGATIVE_PROMPT
    assert "multiple views" in DEFAULT_REFERENCE_NEGATIVE_PROMPT


def test_video_baseline_contains_short_shot_and_temporal_consistency_guards() -> None:
    assert "3 to 5 seconds" in DEFAULT_VIDEO_PROMPT_SUFFIX
    assert "restrained micro-motion" in DEFAULT_VIDEO_PROMPT_SUFFIX
    assert "preserve the exact reference identity" in DEFAULT_VIDEO_PROMPT_SUFFIX
    assert "clean linework" in DEFAULT_VIDEO_PROMPT_SUFFIX
    assert "temporal inconsistency" in DEFAULT_VIDEO_NEGATIVE_PROMPT
    assert "motion smear" in DEFAULT_VIDEO_NEGATIVE_PROMPT


def test_video_prompt_adds_shot_framing_camera_and_identity_constraints() -> None:
    prompt = build_video_motion_prompt(
        source_prompt="林默在雨夜抬头看向旧信",
        shot_size="close_up",
        camera_movement="dolly",
        location="旧城区街道",
        characters=["林默"],
        continuity_notes="保持深色外套和侧光方向",
    )

    assert "close-up portrait" in prompt
    assert "slow subtle push-in" in prompt
    assert "Approved visible characters: 林默" in prompt
    assert "Continuity requirements: 保持深色外套和侧光方向" in prompt
    assert "face-focused motion plan" in prompt
    assert "no unscripted speaking" in prompt
    assert "choose only one restrained micro-motion" in prompt
    assert "Do not add any other character" in prompt
