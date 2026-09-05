"""Provider contract for transcribing audio into timed subtitle cues."""

from __future__ import annotations

from typing import Protocol

from app.domain.models import SubtitleASRGenerationRequest, SubtitleASRGenerationResult


class SubtitleASRProvider(Protocol):
    async def transcribe(
        self,
        request: SubtitleASRGenerationRequest,
    ) -> SubtitleASRGenerationResult:
        ...
