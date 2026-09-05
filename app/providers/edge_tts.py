"""Microsoft Edge TTS adapter behind the application TTS contract."""

from __future__ import annotations

import asyncio
import base64
from time import perf_counter
from typing import Any

from app.domain.models import TTSGenerationRequest, TTSGenerationResult
from app.providers.errors_audio import TTSProviderError


class EdgeTTSProvider:
    def __init__(self, timeout_seconds: int = 90) -> None:
        self.timeout_seconds = max(1, timeout_seconds)

    async def generate_speech(self, request: TTSGenerationRequest) -> TTSGenerationResult:
        try:
            import edge_tts
        except ImportError as exc:
            raise TTSProviderError(
                "TTS_PROVIDER_UNAVAILABLE",
                "edge-tts is not installed; install project dependencies before using edge_tts",
            ) from exc

        started = perf_counter()
        communicator = edge_tts.Communicate(
            request.text,
            request.voice,
            rate=request.rate,
            volume=request.volume,
            # Capture provider timing without splitting the waveform. These
            # events are metadata emitted alongside the same continuous MP3.
            boundary="WordBoundary",
        )
        try:
            content, word_boundaries = await asyncio.wait_for(
                self._collect_stream(communicator),
                timeout=self.timeout_seconds,
            )
        except TimeoutError as exc:
            raise TTSProviderError(
                "TTS_PROVIDER_TIMEOUT",
                "Edge TTS timed out while generating speech",
            ) from exc
        except Exception as exc:
            raise TTSProviderError(
                "TTS_PROVIDER_FAILED",
                f"Edge TTS failed: {str(exc)[:500]}",
            ) from exc

        if not content:
            raise TTSProviderError(
                "TTS_PROVIDER_INVALID_RESPONSE",
                "Edge TTS returned an empty audio file",
            )

        return TTSGenerationResult(
            audio_base64=base64.b64encode(content).decode("ascii"),
            mime_type="audio/mpeg",
            provider="edge_tts",
            model=request.voice,
            duration_seconds=0,
            duration_ms=round((perf_counter() - started) * 1000),
            metadata={
                "voice": request.voice,
                "rate": request.rate,
                "volume": request.volume,
                "transport": "edge-tts",
                # The provider receives the complete narration in one request;
                # downstream assembly must keep it as one continuous track.
                "synthesis_mode": "single_waveform",
                "segmentation": "continuous_text",
                "independent_waveform_count": 1,
                "timing_source": "edge_tts_word_boundary" if word_boundaries else "none",
                "alignment_precision": (
                    "word_boundary" if word_boundaries else "provider_unavailable"
                ),
                "word_boundary_count": len(word_boundaries),
                "word_boundaries": word_boundaries,
            },
        )

    @staticmethod
    async def _collect_stream(
        communicator: Any,
    ) -> tuple[bytes, list[dict[str, object]]]:
        """Collect one continuous MP3 and its provider-emitted word timings."""

        audio = bytearray()
        word_boundaries: list[dict[str, object]] = []
        async for message in communicator.stream():
            message_type = message.get("type")
            if message_type == "audio":
                data = message.get("data")
                if isinstance(data, bytes):
                    audio.extend(data)
                continue
            if message_type != "WordBoundary":
                continue
            text = message.get("text")
            offset = message.get("offset")
            duration = message.get("duration")
            if not isinstance(text, str) or not text.strip():
                continue
            if not isinstance(offset, (int, float)) or not isinstance(duration, (int, float)):
                continue
            start_seconds = float(offset) / 10_000_000
            end_seconds = start_seconds + float(duration) / 10_000_000
            if start_seconds < 0 or end_seconds <= start_seconds:
                continue
            word_boundaries.append(
                {
                    "start_seconds": round(start_seconds, 6),
                    "end_seconds": round(end_seconds, 6),
                    "text": text.strip(),
                }
            )
        return bytes(audio), word_boundaries
