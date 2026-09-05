"""Deterministic video-clip provider for L3 contract validation."""

from __future__ import annotations

import asyncio
import hashlib
from time import monotonic

from app.domain.models import VideoClipGenerationRequest, VideoClipGenerationResult


class MockVideoGenerationProvider:
    """Return a stable placeholder URI without creating a playable video file."""

    def __init__(self, model: str = "mock-video-v1") -> None:
        self._model = model

    async def generate_video_clip(
        self,
        request: VideoClipGenerationRequest,
    ) -> VideoClipGenerationResult:
        started = monotonic()
        await asyncio.sleep(0.01)
        fingerprint = hashlib.sha256(
            f"{request.episode_id}:{request.shot_list_id}:{request.shot_index}:"
            f"{request.prompt}:{request.duration_seconds}".encode("utf-8")
        ).hexdigest()[:16]
        return VideoClipGenerationResult(
            output_uri=(
                f"mock://video-clips/{request.episode_id}/shot-{request.shot_index}/"
                f"{fingerprint}.mp4"
            ),
            provider="mock",
            model=self._model,
            duration_seconds=request.duration_seconds,
            duration_ms=max(1, int((monotonic() - started) * 1000)),
            metadata={
                "placeholder": True,
                "note": "Mock Provider does not create a playable video file.",
            },
        )
