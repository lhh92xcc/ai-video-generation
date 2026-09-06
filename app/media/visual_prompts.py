"""Shared visual defaults for the local portfolio generation pipeline.

The prompt fragments live in one small module so the API/Worker path and the
local sample runner use the same quality guardrails. They are provider-neutral:
each image or video adapter can translate the intent into its own request
format without changing the business layer.
"""

from __future__ import annotations

from collections.abc import Sequence

DEFAULT_REFERENCE_STYLE = (
    "polished 2D manhwa animation production key art, clean single-frame vertical "
    "composition, crisp linework, clear facial planes, soft cel shading, controlled "
    "cinematic lighting, restrained color palette, clean silhouette, uncluttered "
    "readable background, professional webtoon production design, consistent design "
    "language for later image-to-video shots"
)

DEFAULT_REFERENCE_NEGATIVE_PROMPT = (
    "blurry, low quality, soft focus, low contrast, overexposed, underexposed, "
    "messy background, malformed face, asymmetrical eyes, distorted anatomy, "
    "deformed hands, extra fingers, cropped head, cut-off subject, duplicate person, "
    "duplicate object, split screen, split frame, diptych, triptych, collage, comic "
    "panels, character sheet, multiple views, inset image, repeated face, text, logo, "
    "watermark"
)

DEFAULT_VIDEO_NEGATIVE_PROMPT = (
    "blurry, low quality, motion smear, flicker, jitter, unstable exposure, "
    "temporal inconsistency, identity drift, face morphing, changing hairstyle, "
    "changing clothes, distorted anatomy, deformed hands, extra limbs, duplicate "
    "person, new person, crowded frame, scene change, camera cut, hard zoom, text, "
    "logo, watermark"
)

DEFAULT_VIDEO_PROMPT_SUFFIX = (
    "single continuous shot, 3 to 5 seconds, one coherent camera take, choose only "
    "one restrained micro-motion such as breathing, one blink, a small head turn or "
    "gentle cloth movement, stable exposure, preserve the exact reference identity, "
    "face shape, hairstyle, clothing, colors and silhouette, keep the original "
    "composition, no new characters, no cuts, no scene change, no large body "
    "transformation, no simultaneous complex actions"
)

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
    continuity_notes: str = "",
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
    character_clause = (
        f"Approved visible characters: {visible_characters}. Do not add any other character. "
        if visible_characters
        else "No character is visible; keep the frame focused on the approved environment or prop. "
    )
    continuity_clause = (
        f"Continuity requirements: {continuity_notes.strip()[:300]}. "
        if continuity_notes.strip()
        else "Keep identity, costume, palette, lighting direction and prop placement consistent with the reference image. "
    )
    motion_safety = _SHOT_MOTION_SAFETY.get(
        shot_size,
        "general motion plan: use only one restrained readable movement and keep the subject geometry stable",
    )
    prompt = (
        f"{source_prompt.strip()[:900]}. {framing}. Location: {location.strip()[:120]}. "
        f"Camera direction: {movement}. {character_clause}{continuity_clause}"
        f"{motion_safety}. {prompt_suffix.strip()}"
    )
    return prompt[:2000]


def build_shot_keyframe_prompt(
    *,
    source_prompt: str,
    shot_size: str,
    camera_movement: str,
    location: str,
    characters: Sequence[str],
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
    character_clause = (
        f"Visible characters already approved for this shot: {visible_characters}. "
        "Do not add any other character. "
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
    return prompt[:1500]
