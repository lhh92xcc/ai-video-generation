"""Deterministic WAV BGM provider for tests and offline development."""

from __future__ import annotations

import base64
import io
import math
import struct
import time
import wave

from app.domain.models import BGMGenerationRequest, BGMGenerationResult


class MockBGMProvider:
    async def generate_bgm(self, request: BGMGenerationRequest) -> BGMGenerationResult:
        started = time.perf_counter()
        duration_seconds = 12.0
        sample_rate = 16_000
        frame_count = int(sample_rate * duration_seconds)
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(sample_rate)
            frames = bytearray()
            for index in range(frame_count):
                # Two quiet tones make the fixture less speech-like while
                # remaining deterministic and cheap to generate.
                sample = int(
                    500
                    * (
                        math.sin(2 * math.pi * 220 * index / sample_rate)
                        + 0.5 * math.sin(2 * math.pi * 330 * index / sample_rate)
                    )
                )
                frames.extend(struct.pack("<h", sample))
            audio.writeframes(bytes(frames))

        return BGMGenerationResult(
            audio_base64=base64.b64encode(buffer.getvalue()).decode("ascii"),
            mime_type="audio/wav",
            provider="mock",
            model="mock-bgm-v1",
            duration_seconds=duration_seconds,
            duration_ms=round((time.perf_counter() - started) * 1000),
            metadata={
                "label": request.label,
                "source_type": "deterministic_fixture",
                "rights_status": request.rights_status.value,
                "rights_holder": request.rights_holder,
                "rights_reference": request.rights_reference,
                "rights_review_required": True,
                "loop_recommended": True,
                "sample_rate": sample_rate,
            },
        )
