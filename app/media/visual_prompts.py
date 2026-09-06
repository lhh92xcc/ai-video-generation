"""Shared visual defaults for the local portfolio generation pipeline.

The prompt fragments live in one small module so the API/Worker path and the
local sample runner use the same quality guardrails. They are provider-neutral:
each image or video adapter can translate the intent into its own request
format without changing the business layer.
"""

from __future__ import annotations

DEFAULT_REFERENCE_STYLE = (
    "polished 2D manhwa animation key art, cinematic vertical composition, "
    "clean linework, soft cel shading, controlled color palette, detailed but "
    "readable background, professional webtoon production design"
)

DEFAULT_REFERENCE_NEGATIVE_PROMPT = (
    "blurry, low quality, malformed face, asymmetrical eyes, distorted anatomy, "
    "deformed hands, extra fingers, duplicate person, duplicate object, split "
    "screen, split frame, diptych, triptych, collage, comic panels, character "
    "sheet, multiple views, inset image, repeated face, text, logo, watermark"
)

DEFAULT_VIDEO_NEGATIVE_PROMPT = (
    "blurry, low quality, flicker, jitter, temporal inconsistency, identity drift, "
    "face morphing, changing hairstyle, changing clothes, distorted anatomy, "
    "deformed hands, extra limbs, duplicate person, new person, scene change, "
    "camera cut, hard zoom, text, logo, watermark"
)

DEFAULT_VIDEO_PROMPT_SUFFIX = (
    "single continuous shot, 3 to 5 seconds, one coherent camera take, subtle "
    "natural motion, preserve the exact reference identity, face shape, hairstyle, "
    "clothing, colors and composition, no new characters, no cuts, no scene change"
)
