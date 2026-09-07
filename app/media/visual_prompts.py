"""Shared visual defaults for the local portfolio generation pipeline.

The prompt fragments live in one small module so the API/Worker path and the
local sample runner use the same quality guardrails. They are provider-neutral:
each image or video adapter can translate the intent into its own request
format without changing the business layer.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.domain.models import (
    AssetRecord,
    AssetStatus,
    CharacterAssetContent,
    LocationAssetContent,
    PropAssetContent,
)

DEFAULT_REFERENCE_STYLE = (
    "polished flat 2D manhwa animation key art, same cohesive series visual bible across "
    "all characters and locations, clean single-frame 9:16 vertical composition, crisp "
    "consistent ink linework and line weight, clear facial planes, soft cel shading, "
    "controlled cinematic lighting, restrained harmonious color palette, clean silhouette "
    "and readable intentional focal hierarchy, uncluttered background, professional webtoon "
    "production design, consistent design language for later image-to-video shots"
)

DEFAULT_REFERENCE_NEGATIVE_PROMPT = (
    "blurry, low quality, soft focus, low contrast, muddy colors, overexposed, underexposed, "
    "messy background, bad composition, malformed face, asymmetrical eyes, distorted anatomy, "
    "deformed hands, extra fingers, cropped head, cut-off subject, duplicate person, "
    "duplicate object, split screen, split frame, diptych, triptych, collage, comic panels, "
    "character sheet, multiple views, inset image, repeated face, extra limbs, "
    "photorealistic, realistic skin texture, semi-realistic, painterly brushwork, glossy "
    "3d render, different art style, style drift, text, subtitles, logo, watermark"
)

DEFAULT_VIDEO_NEGATIVE_PROMPT = (
    "blurry, low quality, motion smear, ghosting, flicker, strobing, jitter, camera shake, "
    "unstable exposure, temporal inconsistency, frame-to-frame detail changes, identity drift, "
    "face morphing, changing hairstyle, changing clothes, distorted anatomy, deformed hands, "
    "extra limbs, duplicate person, new person, crowded frame, scene change, camera cut, "
    "hard zoom, overshoot, photorealistic, realistic skin texture, semi-realistic, painterly "
    "brushwork, glossy 3d render, different art style, style drift, text, subtitles, logo, watermark"
)

DEFAULT_VIDEO_PROMPT_SUFFIX = (
    "start from the exact reference frame, single continuous shot, 3 to 5 seconds, "
    "one coherent camera take, choose only one restrained micro-motion such as breathing, "
    "one blink, a small head turn or gentle cloth movement, preserve the same cohesive "
    "2D manhwa series visual bible, clean linework and crisp cel-shaded surfaces, stable "
    "exposure, preserve "
    "the exact reference identity, face shape, hairstyle, clothing, colors and silhouette, "
    "keep the original composition, no new characters, no cuts, no scene change, no large "
    "body transformation, no simultaneous complex actions, no frozen still frame or slideshow, "
    "make the chosen micro-motion visibly continuous across the full clip, no unscripted mouth "
    "movement when a later lip-sync pass is planned"
)

DEFAULT_REFERENCE_QUALITY_GUARDRAIL = (
    "Reference quality guardrails: one coherent full-frame composition, one dominant readable "
    "subject or environment, clear focal point, stable proportions, clean silhouette, "
    "controlled lighting and palette, same 2D manhwa art direction, no style mixing, no text "
    "or interface elements."
)


def _compact(value: str, limit: int) -> str:
    """Keep prompt facts readable without allowing one field to dominate."""

    compacted = " ".join(value.strip().split())
    if len(compacted) <= limit:
        return compacted
    return f"{compacted[: max(1, limit - 1)].rstrip()}…"


def strengthen_reference_prompt(prompt: str, *, max_chars: int = 2000) -> str:
    """Append positive quality constraints that also work with Flux Schnell.

    Flux workflows do not necessarily expose a negative-conditioning branch.  Keeping
    these constraints in the positive prompt makes the reference-image contract useful
    even when a ComfyUI graph ignores ``negative_prompt``; cloud adapters still receive
    the separate negative prompt as before.
    """

    source = " ".join(prompt.strip().split())
    if not source:
        return DEFAULT_REFERENCE_QUALITY_GUARDRAIL[:max_chars]
    if "Reference quality guardrails:" in source:
        return source[:max_chars]
    # Reserve both the period and space inserted between the bounded source
    # and the guardrail. Without both characters, max_chars=1500 produced a
    # 1501-character prompt and violated ReferenceImageCreateRequest.
    available = max_chars - len(DEFAULT_REFERENCE_QUALITY_GUARDRAIL) - 2
    if available <= 0:
        return DEFAULT_REFERENCE_QUALITY_GUARDRAIL[:max_chars]
    result = f"{source[:available].rstrip(' .')}. {DEFAULT_REFERENCE_QUALITY_GUARDRAIL}"
    return result[:max_chars]


def build_approved_asset_facts(
    assets: Sequence[AssetRecord],
    *,
    max_chars: int = 900,
) -> list[str]:
    """Render only approved, versioned design facts for a video Prompt.

    The source models intentionally contain more information than an I2V model
    needs.  This helper selects visual continuity fields and keeps the result
    bounded so the actual shot description and motion guardrails remain in the
    request.  Non-ready assets are ignored instead of being presented as facts.
    """

    facts: list[str] = []
    for asset in assets:
        if asset.status != AssetStatus.READY:
            continue
        content = asset.content
        if isinstance(content, CharacterAssetContent):
            details = [
                f"appearance={_compact(content.appearance, 260)}",
                f"traits={_compact('、'.join(content.traits), 120)}"
                if content.traits
                else "traits=未设定",
            ]
            if content.age_range != "待设定":
                details.append(f"age_range={_compact(content.age_range, 60)}")
        elif isinstance(content, LocationAssetContent):
            details = [
                f"description={_compact(content.description, 220)}",
                f"atmosphere={_compact(content.atmosphere, 100)}",
                f"visual_keywords={_compact('、'.join(content.visual_keywords), 120)}"
                if content.visual_keywords
                else "visual_keywords=未设定",
            ]
        elif isinstance(content, PropAssetContent):
            details = [
                f"description={_compact(content.description, 220)}",
                f"visual_keywords={_compact('、'.join(content.visual_keywords), 120)}"
                if content.visual_keywords
                else "visual_keywords=未设定",
                f"continuity={_compact(content.continuity_notes, 140)}",
            ]
        else:  # pragma: no cover - AssetContent is a closed union today.
            continue

        facts.append(
            f'{asset.asset_type.value} "{_compact(asset.name, 80)}" v{asset.version}: '
            + "; ".join(details)
        )

    if not facts:
        return []
    bounded: list[str] = []
    used = 0
    for fact in facts:
        separator = 2 if bounded else 0
        remaining = max_chars - used - separator
        if remaining <= 0:
            break
        if len(fact) > remaining:
            bounded.append(_compact(fact, remaining))
            break
        bounded.append(fact)
        used += separator + len(fact)
    return bounded

_SHOT_MOTION_SAFETY = {
    "close_up": (
        "face-focused motion plan: use only one tiny natural blink, breathing motion or eye movement; "
        "keep facial landmarks, mouth shape and hairstyle stable, with no unscripted speaking"
    ),
    "extreme_close_up": (
        "detail-focused motion plan: use only one nearly imperceptible eye, breath or fabric movement; "
        "keep the face or object edges locked and avoid any deformation"
    ),
    "medium": (
        "character motion plan: use only one small head turn, blink, breathing motion or restrained hand gesture; "
        "keep hands and facial features stable"
    ),
    "over_the_shoulder": (
        "dialogue framing motion plan: use only one slight head or shoulder movement; "
        "keep the foreground silhouette and the visible face stable"
    ),
    "wide": (
        "environment motion plan: use only one subtle cloth, hair, light or breathing change; "
        "keep the subject silhouette and background layout stable"
    ),
    "insert": (
        "detail-object motion plan: use only one subtle light glint, focus shift or material movement; "
        "keep the object contour and placement stable"
    ),
}


def build_video_motion_prompt(
    *,
    source_prompt: str,
    shot_size: str,
    camera_movement: str,
    location: str,
    characters: Sequence[str],
    primary_character: str = "",
    continuity_notes: str = "",
    approved_asset_facts: Sequence[str] = (),
    prompt_suffix: str = DEFAULT_VIDEO_PROMPT_SUFFIX,
) -> str:
    """Compose a deterministic, shot-aware prompt for an I2V Provider.

    LLM-generated ``visual_prompt`` text is useful story context, but it is not a
    reliable substitute for production constraints.  This helper adds the
    protocol-level framing, camera and continuity facts at the service boundary
    so every video adapter receives the same guardrails.
    """

    framing = {
        "wide": "wide vertical establishing shot with readable foreground, midground and background",
        "medium": "medium vertical shot with the primary subject clearly readable",
        "close_up": "close-up portrait with the face unobstructed and centered in the visual hierarchy",
        "extreme_close_up": "tight facial or detail shot with the identity still clearly recognizable",
        "over_the_shoulder": "over-the-shoulder vertical shot with a stable foreground shoulder and readable subject",
        "insert": "single detail shot with one dominant readable object",
    }.get(shot_size, "clear vertical composition")
    movement = {
        "fixed": "locked-off camera; the subject stays in place",
        "pan": "very gentle horizontal pan; the subject stays in place",
        "tilt": "very gentle vertical tilt; the subject stays in place",
        "dolly": "slow subtle push-in or pull-out with no sudden acceleration",
        "tracking": "restrained lateral tracking with the subject remaining stable",
        "handheld": "minimal stabilized handheld drift with no visible shake",
        "zoom": "very gentle optical-style push-in with no hard zoom",
    }.get(camera_movement, "restrained stabilized camera movement")
    visible_characters = ", ".join(item.strip() for item in characters if item.strip())
    primary_name = primary_character.strip() or next(
        (item.strip() for item in characters if item.strip()), ""
    )
    character_clause = (
        f"Approved visible characters: {visible_characters}. Do not add any other character. "
        + (
            f"Primary identity to preserve is {primary_name}; keep other approved characters "
            "partially turned, in silhouette or visually secondary unless their identity is "
            "explicitly anchored. "
            if len([item for item in characters if item.strip()]) > 1 and primary_name
            else ""
        )
        if visible_characters
        else "No character is visible; keep the frame focused on the approved environment or prop. "
    )
    continuity_clause = (
        f"Continuity requirements: {continuity_notes.strip()[:300]}. "
        if continuity_notes.strip()
        else "Keep identity, costume, palette, lighting direction and prop placement consistent with the reference image. "
    )
    asset_facts = "; ".join(
        _compact(fact, 420) for fact in approved_asset_facts if fact.strip()
    )
    asset_facts_clause = (
        f"Approved asset design facts: {asset_facts}. "
        if asset_facts
        else "No additional approved asset facts were supplied; follow the reference image and shot constraints. "
    )
    motion_safety = _SHOT_MOTION_SAFETY.get(
        shot_size,
        "general motion plan: use only one restrained readable movement and keep the subject geometry stable",
    )
    dynamic_context = (
        f"{source_prompt.strip()[:900]}. {framing}. Location: {location.strip()[:120]}. "
        f"Camera direction: {movement}. {character_clause}{asset_facts_clause}{continuity_clause}"
    )
    fixed_constraints = f"{motion_safety}. {prompt_suffix.strip()}"
    available_context = 2000 - len(fixed_constraints) - 1
    if available_context <= 0:
        return fixed_constraints[:2000]
    return f"{dynamic_context[:available_context].rstrip(' .')}. {fixed_constraints}"


def build_shot_keyframe_prompt(
    *,
    source_prompt: str,
    shot_size: str,
    camera_movement: str,
    location: str,
    characters: Sequence[str],
    primary_character: str = "",
    continuity_notes: str = "",
    primary_character_facts: str = "",
    style: str = DEFAULT_REFERENCE_STYLE,
) -> str:
    """Build a composed, identity-aware still for a single video shot.

    The standard asset image is an identity anchor; this prompt asks for a new
    composition for the current shot while keeping the primary character's
    stable design facts.  Provider adapters remain responsible for translating
    the prompt into their own workflow inputs.
    """

    framing = {
        "wide": "wide vertical composition with readable foreground, midground and background",
        "medium": "medium vertical composition with the primary subject clearly readable",
        "close_up": "close-up portrait composition with the primary face unobstructed",
        "extreme_close_up": "tight facial or detail composition with the identity anchor still recognizable",
        "over_the_shoulder": "over-the-shoulder vertical composition with a clear foreground shoulder and readable subject",
        "insert": "single detail composition with one dominant readable object",
    }.get(shot_size, "clear vertical composition")
    movement = {
        "fixed": "an instant suitable for a locked camera",
        "pan": "an instant suitable for a gentle pan",
        "tilt": "an instant suitable for a gentle tilt",
        "dolly": "an instant suitable for a slow dolly",
        "tracking": "an instant suitable for a restrained tracking move",
        "handheld": "an instant suitable for restrained handheld motion",
        "zoom": "an instant suitable for a very gentle push-in",
    }.get(camera_movement, "a restrained camera move")
    visible_characters = ", ".join(item.strip() for item in characters if item.strip())
    primary_name = primary_character.strip() or next(
        (item.strip() for item in characters if item.strip()), ""
    )
    character_clause = (
        f"Visible characters already approved for this shot: {visible_characters}. "
        "Do not add any other character. "
        + (
            f"Primary identity is {primary_name}; keep all other approved characters "
            "partially turned, silhouetted or visually secondary without inventing a new "
            "detailed face. "
            if len([item for item in characters if item.strip()]) > 1 and primary_name
            else ""
        )
        if visible_characters
        else "No character is visible; keep the frame focused on the approved environment or prop. "
    )
    facts_clause = (
        f"Primary character design facts to preserve: {primary_character_facts[:500]}. "
        if primary_character_facts.strip()
        else "Preserve the identity anchor's face geometry, hairstyle, clothing and palette. "
    )
    continuity_clause = (
        f"Continuity note: {continuity_notes[:300]}. " if continuity_notes.strip() else ""
    )
    prompt = (
        f"{style.strip()}. 9:16 vertical storyboard keyframe for one later 3 to 5 second video shot. "
        f"{source_prompt.strip()[:700]}. Location: {location.strip()}. {framing}; {movement}. "
        f"{character_clause}{facts_clause}{continuity_clause}"
        "Create one coherent full-frame image, not a character sheet, not a collage, not multiple views. "
        "Use a stable readable pose that can transition into subtle motion; preserve exact identity, "
        "silhouette, costume colors, lighting direction and important prop placement."
    )
    return strengthen_reference_prompt(prompt, max_chars=1500)
