"""Deterministic local TTS provider used for tests and offline development."""

from __future__ import annotations

import base64
import io
import math
import struct
import time
import wave

from app.domain.models import TTSGenerationRequest, TTSGenerationResult


class MockTTSProvider:
    async def generate_speech(self, request: TTSGenerationRequest) -> TTSGenerationResult:
        started = time.perf_counter()
        duration_seconds = min(3.0, max(0.5, len(request.text) * 0.04))
        sample_rate = 16_000
        frame_count = int(sample_rate * duration_seconds)
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(sample_rate)
            frames = bytearray()
            for index in range(frame_count):
                sample = int(700 * math.sin(2 * math.pi * 440 * index / sample_rate))
                frames.extend(struct.pack("<h", sample))
            audio.writeframes(bytes(frames))

        return TTSGenerationResult(
            audio_base64=base64.b64encode(buffer.getvalue()).decode("ascii"),
            mime_type="audio/wav",
            provider="mock",
            model="mock-tts-v1",
            duration_seconds=duration_seconds,
            duration_ms=round((time.perf_counter() - started) * 1000),
            metadata={
                "voice": request.voice,
                "rate": request.rate,
                "volume": request.volume,
                "sample_rate": sample_rate,
            },
        )
