"""Provider contract for background-music Artifact generation."""

from __future__ import annotations

from typing import Protocol

from app.domain.models import BGMGenerationRequest, BGMGenerationResult


class BGMProvider(Protocol):
    async def generate_bgm(self, request: BGMGenerationRequest) -> BGMGenerationResult:
        ...
