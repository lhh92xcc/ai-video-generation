"""Provider contract for converting text and audio duration into subtitle cues."""

from __future__ import annotations

from typing import Protocol

from app.domain.models import (
    SubtitleAlignmentCreateRequest,
    SubtitleAlignmentGenerationResult,
)


class SubtitleAlignmentProvider(Protocol):
    async def align(
        self,
        request: SubtitleAlignmentCreateRequest,
    ) -> SubtitleAlignmentGenerationResult:
        ...
