"""Offline ASR fixture used to verify the audio-Artifact task boundary."""

from __future__ import annotations

from app.domain.models import (
    SubtitleASRGenerationRequest,
    SubtitleASRGenerationResult,
    SubtitleAlignmentCreateRequest,
)
from app.providers.mock_subtitle_alignment import MockSentenceSubtitleAlignmentProvider


class MockASRProvider:
    """Return deterministic cues without claiming to inspect the waveform."""

    def __init__(self) -> None:
        self._sentence_provider = MockSentenceSubtitleAlignmentProvider()

    async def transcribe(
        self,
        request: SubtitleASRGenerationRequest,
    ) -> SubtitleASRGenerationResult:
        text = request.reference_text or "mock transcription"
        aligned = await self._sentence_provider.align(
            SubtitleAlignmentCreateRequest(
                text=text,
                language=request.language,
                audio_duration_seconds=request.audio_duration_seconds,
            )
        )
        return SubtitleASRGenerationResult(
            cues=aligned.cues,
            provider="mock_asr",
            model="mock-asr-v1",
            precision="mock_segment_estimate",
            duration_ms=aligned.duration_ms,
            metadata={
                "audio_inspected": False,
                "source_type": "deterministic_fixture",
                "reference_text_used": request.reference_text is not None,
            },
        )
