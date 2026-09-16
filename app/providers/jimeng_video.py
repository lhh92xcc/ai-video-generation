"""Protocol-isolated adapter scaffold for a future Jimeng video endpoint.

Jimeng's official API contract is intentionally not guessed here.  This class
reuses the tested asynchronous HTTP lifecycle and keeps the provisional request
mapping in one adapter, so the mapping can be replaced when the official
endpoint, signing method and response examples are available.
"""

from __future__ import annotations

import base64
from typing import Any

from app.domain.models import VideoClipGenerationRequest
from app.providers.openai_compatible_video import OpenAICompatibleVideoGenerationProvider
from app.providers.errors_video import VideoProviderError


class JimengVideoGenerationProvider(OpenAICompatibleVideoGenerationProvider):
    """Run a configurable Jimeng-compatible submit/poll/download contract.

    The default mapping is a local integration contract, not a claim about the
    official Jimeng API.  It is useful for a redacted HTTP fixture and can be
    updated without touching task, Artifact or orchestration code.
    """

    def __init__(self, *args: Any, protocol_ready: bool = False, **kwargs: Any) -> None:
        self.protocol_ready = protocol_ready
        super().__init__(*args, provider_name="jimeng", **kwargs)

    def _require_configuration(self) -> None:
        if not self.protocol_ready:
            raise VideoProviderError(
                "JIMENG_PROTOCOL_NOT_CONFIGURED",
                "Jimeng adapter is disabled until its official API contract is configured",
            )
        if not self.api_key:
            raise VideoProviderError(
                "VIDEO_PROVIDER_AUTH_FAILED",
                "AI_VIDEO_JIMENG_API_KEY is required for the Jimeng adapter",
            )
        if not self.base_url:
            raise VideoProviderError(
                "VIDEO_PROVIDER_INVALID_CONFIGURATION",
                "AI_VIDEO_JIMENG_BASE_URL is required for the Jimeng adapter",
            )

    def _build_payload(self, request: VideoClipGenerationRequest) -> dict[str, Any]:
        """Map the provider-neutral request to the provisional HTTP contract."""

        quality: dict[str, Any] = {
            "width": request.width,
            "height": request.height,
            "fps": request.fps,
            "steps": request.steps,
            "cfg": request.cfg,
            "noise_aug_strength": request.noise_aug_strength,
        }
        quality = {key: value for key, value in quality.items() if value is not None}
        if request.width and request.height:
            quality["size"] = f"{request.width}x{request.height}"

        payload: dict[str, Any] = {
            "model": self.model,
            "prompt": request.prompt,
            "negative_prompt": request.negative_prompt,
            "duration_seconds": request.duration_seconds,
            "generation_attempt": request.generation_attempt,
            "quality": quality,
            "asset_refs": [item.model_dump(mode="json") for item in request.asset_refs],
        }
        if request.keyframe_bytes:
            mime_type = request.keyframe_mime_type or "image/png"
            payload["reference_image"] = {
                "mime_type": mime_type,
                "data": base64.b64encode(request.keyframe_bytes).decode("ascii"),
            }
        return payload
