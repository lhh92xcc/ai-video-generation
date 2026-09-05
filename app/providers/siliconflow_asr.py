"""SiliconFlow speech-to-text Provider.

SiliconFlow's transcription endpoint returns recognized text for the
supported ASR models, but not segment or word timestamps. This adapter keeps
that vendor-specific limitation explicit and creates sentence-level timing
estimates from the returned transcript. It must not be presented as
word-boundary alignment.
"""

from __future__ import annotations

from time import monotonic

import httpx

from app.domain.models import (
    SubtitleASRGenerationRequest,
    SubtitleASRGenerationResult,
    SubtitleAlignmentCreateRequest,
)
from app.providers.errors_asr import SubtitleASRProviderError
from app.providers.mock_subtitle_alignment import MockSentenceSubtitleAlignmentProvider


class SiliconFlowASRProvider:
    """Transcribe audio through SiliconFlow's text-only ASR endpoint."""

    def __init__(
        self,
        base_url: str,
        api_key: str | None,
        model: str,
        timeout_seconds: int,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)
        self._owns_client = client is None
        self._alignment_provider = MockSentenceSubtitleAlignmentProvider()

    async def transcribe(
        self,
        request: SubtitleASRGenerationRequest,
    ) -> SubtitleASRGenerationResult:
        if not self.base_url:
            raise SubtitleASRProviderError(
                "ASR_PROVIDER_NOT_CONFIGURED",
                "AI_VIDEO_ASR_BASE_URL is required for the SiliconFlow ASR provider",
            )
        if not self.api_key:
            raise SubtitleASRProviderError(
                "ASR_PROVIDER_AUTH_FAILED",
                "AI_VIDEO_ASR_API_KEY is required for the SiliconFlow ASR provider",
            )

        started = monotonic()
        try:
            response = await self._client.post(
                f"{self.base_url}/audio/transcriptions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                files={
                    "file": (
                        self._filename(request.mime_type),
                        request.audio_bytes,
                        request.mime_type,
                    ),
                    "model": (None, self.model),
                },
            )
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise SubtitleASRProviderError(
                "ASR_PROVIDER_TIMEOUT", "SiliconFlow ASR request timed out"
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise SubtitleASRProviderError(
                self._status_error_code(exc.response.status_code),
                f"SiliconFlow ASR request failed with HTTP {exc.response.status_code}",
            ) from exc
        except httpx.HTTPError as exc:
            raise SubtitleASRProviderError(
                "ASR_PROVIDER_HTTP_ERROR", "SiliconFlow ASR request failed"
            ) from exc

        try:
            payload = response.json()
            if not isinstance(payload, dict):
                raise TypeError("response must be a JSON object")
            transcript = str(payload.get("text", "")).strip()
            if not transcript:
                raise ValueError("response text is empty")
        except (TypeError, ValueError, KeyError) as exc:
            raise SubtitleASRProviderError(
                "ASR_PROVIDER_INVALID_RESPONSE",
                "SiliconFlow ASR response did not contain transcription text",
            ) from exc

        aligned = await self._alignment_provider.align(
            SubtitleAlignmentCreateRequest(
                text=transcript,
                language=request.language,
                audio_duration_seconds=request.audio_duration_seconds,
            )
        )
        return SubtitleASRGenerationResult(
            cues=aligned.cues,
            provider="siliconflow_asr",
            model=self.model,
            precision="transcript_sentence_estimate",
            duration_ms=max(1, int((monotonic() - started) * 1000)),
            metadata={
                "response_format": "text",
                "transcript_characters": len(transcript),
                "timestamp_source": "transcript_text_length_ratio",
                "segment_timestamps": False,
                "word_timestamps": False,
                "audio_inspected": True,
                "reference_text_used": request.reference_text is not None,
            },
        )

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    @staticmethod
    def _filename(mime_type: str) -> str:
        suffix = {
            "audio/wav": ".wav",
            "audio/mpeg": ".mp3",
            "audio/mp3": ".mp3",
            "audio/mp4": ".m4a",
            "audio/ogg": ".ogg",
            "audio/webm": ".webm",
        }.get(mime_type.lower(), ".audio")
        return f"narration{suffix}"

    @staticmethod
    def _status_error_code(status_code: int) -> str:
        if status_code in {401, 403}:
            return "ASR_PROVIDER_AUTH_FAILED"
        if status_code == 429:
            return "ASR_PROVIDER_RATE_LIMITED"
        if status_code >= 500:
            return "ASR_PROVIDER_UNAVAILABLE"
        return "ASR_PROVIDER_HTTP_ERROR"
