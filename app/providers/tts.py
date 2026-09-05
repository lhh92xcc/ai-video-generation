"""Provider contract for text-to-speech generation."""

from __future__ import annotations

from typing import Protocol

from app.domain.models import TTSGenerationRequest, TTSGenerationResult


class TTSProvider(Protocol):
    async def generate_speech(
        self,
        request: TTSGenerationRequest,
    ) -> TTSGenerationResult:
        ...
