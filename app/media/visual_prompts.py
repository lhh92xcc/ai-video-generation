"""Shared visual defaults for the local portfolio generation pipeline.

The prompt fragments live in one small module so the API/Worker path and the
local sample runner use the same quality guardrails. They are provider-neutral:
each image or video adapter can translate the intent into its own request
format without changing the business layer.
"""

from __future__ import annotations

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
    "single continuous shot, 3 to 5 seconds, one coherent camera take, subtle "
    "readable motion such as breathing, blinking, a small head turn or gentle cloth "
    "movement, stable exposure, preserve the exact reference identity, face shape, "
    "hairstyle, clothing, colors and silhouette, keep the original composition, no "
    "new characters, no cuts, no scene change, no large body transformation"
)
