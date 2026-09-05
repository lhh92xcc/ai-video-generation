"""Provider contract for asset reference-image generation."""

from __future__ import annotations

from typing import Protocol

from app.domain.models import ReferenceImageGenerationRequest, ReferenceImageGenerationResult


class ImageGenerationProvider(Protocol):
    async def generate_reference_image(
        self,
        request: ReferenceImageGenerationRequest,
    ) -> ReferenceImageGenerationResult:
        ...
