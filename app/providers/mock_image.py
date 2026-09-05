"""Deterministic reference-image provider used for local validation."""

from __future__ import annotations

import asyncio
import hashlib
from time import monotonic

from app.domain.models import ReferenceImageGenerationRequest, ReferenceImageGenerationResult


class MockImageGenerationProvider:
    """Return a stable placeholder URI without calling an external image model."""

    async def generate_reference_image(
        self,
        request: ReferenceImageGenerationRequest,
    ) -> ReferenceImageGenerationResult:
        started = monotonic()
        await asyncio.sleep(0.01)
        fingerprint = hashlib.sha256(
            f"{request.asset_key}:{request.asset_version}:{request.prompt}:{request.width}x{request.height}".encode(
                "utf-8"
            )
        ).hexdigest()[:16]
        return ReferenceImageGenerationResult(
            output_uri=(
                f"mock://reference-images/{request.asset_key}/v{request.asset_version}/"
                f"{fingerprint}.png"
            ),
            provider="mock",
            model="mock-reference-v1",
            width=request.width,
            height=request.height,
            duration_ms=max(1, int((monotonic() - started) * 1000)),
            metadata={"placeholder": True, "note": "Mock Provider does not create a real image file."},
        )
