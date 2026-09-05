"""Provider contract for shot-level video clip generation."""

from __future__ import annotations

from typing import Protocol

from app.domain.models import VideoClipGenerationRequest, VideoClipGenerationResult


class VideoGenerationProvider(Protocol):
    async def generate_video_clip(
        self,
        request: VideoClipGenerationRequest,
    ) -> VideoClipGenerationResult:
        ...
