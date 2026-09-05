"""Offline sentence-level subtitle alignment Provider.

This Provider estimates cue boundaries from text length. It deliberately does
not inspect audio and must not be presented as ASR or word-boundary output.
"""

from __future__ import annotations

import re
import time

from app.domain.models import (
    SubtitleAlignmentCreateRequest,
    SubtitleAlignmentGenerationResult,
    SubtitleCueRequest,
)


class MockSentenceSubtitleAlignmentProvider:
    """Create deterministic sentence-level timing estimates for local testing."""

    async def align(
        self,
        request: SubtitleAlignmentCreateRequest,
    ) -> SubtitleAlignmentGenerationResult:
        started = time.perf_counter()
        segments = self._split_text(request.text)
        weights = [max(1, len(re.sub(r"\s+", "", segment))) for segment in segments]
        total_weight = sum(weights)
        cues: list[SubtitleCueRequest] = []
        start_seconds = 0.0
        for index, (segment, weight) in enumerate(zip(segments, weights, strict=True)):
            if index == len(segments) - 1:
                end_seconds = request.audio_duration_seconds
            else:
                end_seconds = request.audio_duration_seconds * (
                    sum(weights[: index + 1]) / total_weight
                )
            cues.append(
                SubtitleCueRequest(
                    start_seconds=start_seconds,
                    end_seconds=end_seconds,
                    text=segment,
                )
            )
            start_seconds = end_seconds

        return SubtitleAlignmentGenerationResult(
            cues=cues,
            provider="mock_sentence_alignment",
            model="sentence-length-v1",
            precision="sentence_estimate",
            duration_ms=round((time.perf_counter() - started) * 1000),
            metadata={
                "segmentation": "punctuation_or_1000_chars",
                "timing_method": "text_length_ratio",
                "audio_inspected": False,
            },
        )

    @classmethod
    def _split_text(cls, text: str) -> list[str]:
        raw_segments = re.split(r"(?<=[。！？!?；;])\s*|[\r\n]+", text.strip())
        segments: list[str] = []
        for raw_segment in raw_segments:
            segment = re.sub(r"\s+", " ", raw_segment).strip()
            if not segment:
                continue
            for offset in range(0, len(segment), 1000):
                segments.append(segment[offset : offset + 1000])
        return segments
