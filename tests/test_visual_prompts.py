from app.media.visual_prompts import (
    DEFAULT_REFERENCE_NEGATIVE_PROMPT,
    DEFAULT_REFERENCE_STYLE,
    DEFAULT_VIDEO_NEGATIVE_PROMPT,
    DEFAULT_VIDEO_PROMPT_SUFFIX,
    build_approved_asset_facts,
    build_video_motion_prompt,
    build_shot_keyframe_prompt,
    strengthen_reference_prompt,
)
from app.domain.models import (
    AssetRecord,
    AssetStatus,
    AssetType,
    CharacterAssetContent,
    LocationAssetContent,
)
from uuid import uuid4


def test_long_video_description_does_not_erase_identity_or_continuity() -> None:
    for suffix in [DEFAULT_VIDEO_PROMPT_SUFFIX, "稳定运动 " * 1000]:
        prompt = build_video_motion_prompt(
            source_prompt="抬头看旧信。" * 300,
            shot_size="close_up", camera_movement="fixed",
            location="旧城区", characters=["林默"],
            approved_asset_facts=["黑发、深色外套"],
            continuity_notes="保持侧光方向", prompt_suffix=suffix,
        )
        for fact in ["黑发、深色外套", "林默", "旧城区", "保持侧光方向",
                     "close-up", "locked-off", "抬头看旧信"]:
            assert fact in prompt
        assert len(prompt) <= 2000


def test_long_keyframe_prompt_preserves_identity_and_shot_context() -> None:
    prompt = build_shot_keyframe_prompt(
        source_prompt="抬头看旧信。" * 200,
        style=DEFAULT_REFERENCE_STYLE * 4,
        shot_size="close_up",
        camera_movement="fixed",
        location="旧城区街道",
        characters=["林默"],
        primary_character_facts="黑发、深色外套、左手旧表",
        continuity_notes="保持侧光方向",
    )
    for fact in ["黑发、深色外套、左手旧表", "林默", "旧城区街道",
                 "保持侧光方向", "close-up portrait", "抬头看旧信",
                 "2D manhwa", "Reference quality guardrails:"]:
        assert fact in prompt
    assert len(prompt) <= 1500


def test_reference_baseline_contains_single_subject_and_clean_composition_guards() -> None:
    assert "single-frame" in DEFAULT_REFERENCE_STYLE
    assert "2D manhwa" in DEFAULT_REFERENCE_STYLE
    assert "clean silhouette" in DEFAULT_REFERENCE_STYLE
    assert "duplicate person" in DEFAULT_REFERENCE_NEGATIVE_PROMPT
    assert "multiple views" in DEFAULT_REFERENCE_NEGATIVE_PROMPT
    assert "harmonious color palette" in DEFAULT_REFERENCE_STYLE
    assert "same cohesive series visual bible" in DEFAULT_REFERENCE_STYLE
    assert "never photorealistic or photographic" in DEFAULT_REFERENCE_STYLE
    assert "semi-realistic" in DEFAULT_REFERENCE_NEGATIVE_PROMPT
    assert "product photograph" in DEFAULT_REFERENCE_NEGATIVE_PROMPT
    assert "painterly brushwork" in DEFAULT_REFERENCE_NEGATIVE_PROMPT
    assert "subtitles" in DEFAULT_REFERENCE_NEGATIVE_PROMPT


def test_reference_prompt_adds_positive_guardrails_for_flux_workflows() -> None:
    prompt = strengthen_reference_prompt("one canonical character portrait")

    assert prompt.startswith("one canonical character portrait")
    assert "Reference quality guardrails:" in prompt
    assert "one coherent full-frame composition" in prompt
    assert len(prompt) <= 2000


def test_reference_prompt_never_exceeds_provider_request_limit() -> None:
    prompt = strengthen_reference_prompt("高质量参考图约束。" * 600, max_chars=1500)

    assert len(prompt) <= 1500
    assert "Reference quality guardrails:" in prompt


def test_video_baseline_contains_short_shot_and_temporal_consistency_guards() -> None:
    assert "3 to 5 seconds" in DEFAULT_VIDEO_PROMPT_SUFFIX
    assert "restrained micro-motion" in DEFAULT_VIDEO_PROMPT_SUFFIX
    assert "preserve the exact reference identity" in DEFAULT_VIDEO_PROMPT_SUFFIX
    assert "clean linework" in DEFAULT_VIDEO_PROMPT_SUFFIX
    assert "temporal inconsistency" in DEFAULT_VIDEO_NEGATIVE_PROMPT
    assert "motion smear" in DEFAULT_VIDEO_NEGATIVE_PROMPT
    assert "different art style" in DEFAULT_VIDEO_NEGATIVE_PROMPT
    assert "same cohesive 2D manhwa series visual bible" in DEFAULT_VIDEO_PROMPT_SUFFIX
    assert "never photographic" in DEFAULT_VIDEO_PROMPT_SUFFIX
    assert "product photograph" in DEFAULT_VIDEO_NEGATIVE_PROMPT
    assert "no frozen still frame or slideshow" in DEFAULT_VIDEO_PROMPT_SUFFIX


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


def test_video_prompt_prioritizes_primary_identity_when_multiple_characters_are_visible() -> None:
    prompt = build_video_motion_prompt(
        source_prompt="两人站在走廊对话",
        shot_size="medium",
        camera_movement="fixed",
        location="旧公寓走廊",
        characters=["林默", "顾遥"],
        primary_character="林默",
    )

    assert "Primary identity to preserve is 林默" in prompt
    assert "visually secondary" in prompt


def test_approved_asset_facts_select_visual_fields_and_ignore_unapproved_assets() -> None:
    project_id = uuid4()
    story_bible_id = uuid4()
    ready_character = AssetRecord(
        project_id=project_id,
        story_bible_id=story_bible_id,
        asset_type=AssetType.CHARACTER,
        name="林默",
        status=AssetStatus.READY,
        content=CharacterAssetContent(
            role="protagonist",
            traits=["冷静", "敏锐"],
            appearance="黑发、深色外套、左手旧表",
        ),
        provider="test",
        model="test",
        duration_ms=0,
    )
    unapproved_location = AssetRecord(
        project_id=project_id,
        story_bible_id=story_bible_id,
        asset_type=AssetType.LOCATION,
        name="旧城区",
        status=AssetStatus.NEEDS_REVIEW,
        content=LocationAssetContent(
            description="狭窄的旧城区街道",
            atmosphere="潮湿、冷清",
            visual_keywords=["霓虹", "雨夜"],
        ),
        provider="test",
        model="test",
        duration_ms=0,
    )

    facts = build_approved_asset_facts([ready_character, unapproved_location])

    assert len(facts) == 1
    assert 'character "林默" v1' in facts[0]
    assert "黑发、深色外套、左手旧表" in facts[0]
    assert "冷静、敏锐" in facts[0]
    assert "旧城区" not in "".join(facts)


def test_video_prompt_includes_approved_asset_design_facts() -> None:
    prompt = build_video_motion_prompt(
        source_prompt="林默在雨夜街道停下",
        shot_size="medium",
        camera_movement="fixed",
        location="旧城区",
        characters=["林默"],
        approved_asset_facts=[
            'character "林默" v2: appearance=黑发、深色外套; traits=冷静、敏锐'
        ],
    )

    assert "Approved asset design facts" in prompt
    assert "黑发、深色外套" in prompt
    assert "v2" in prompt
