"""OpenAI-compatible audio transcription Provider.

The adapter uses the common ``/audio/transcriptions`` multipart contract and
keeps vendor-specific behavior out of the subtitle task service. It requires a
verbose response with segment or word timestamps; a text-only response cannot
be safely turned into a timed SRT Artifact.
"""

from __future__ import annotations

import math
from time import monotonic
from typing import Any

import httpx

from app.domain.models import (
    SubtitleASRGenerationRequest,
    SubtitleASRGenerationResult,
    SubtitleCueRequest,
)
from app.providers.errors_asr import SubtitleASRProviderError


class OpenAICompatibleASRProvider:
    """Transcribe audio through an OpenAI-compatible HTTP endpoint."""

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

    async def transcribe(
        self,
        request: SubtitleASRGenerationRequest,
    ) -> SubtitleASRGenerationResult:
        if not self.base_url:
            raise SubtitleASRProviderError(
                "ASR_PROVIDER_NOT_CONFIGURED",
                "AI_VIDEO_ASR_BASE_URL is required for the OpenAI-compatible ASR provider",
            )
        if not self.api_key:
            raise SubtitleASRProviderError(
                "ASR_PROVIDER_AUTH_FAILED",
                "AI_VIDEO_ASR_API_KEY is required for the OpenAI-compatible ASR provider",
            )

        started = monotonic()
        data: dict[str, str | list[str]] = {
            "model": self.model,
            "language": request.language,
            "response_format": "verbose_json",
            "timestamp_granularities[]": ["segment", "word"],
        }
        if request.reference_text:
            data["prompt"] = request.reference_text

        try:
            response = await self._client.post(
                f"{self.base_url}/audio/transcriptions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                data=data,
                files={
                    "file": (
                        self._filename(request.mime_type),
                        request.audio_bytes,
                        request.mime_type,
                    )
                },
            )
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise SubtitleASRProviderError(
                "ASR_PROVIDER_TIMEOUT", "ASR request timed out"
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise SubtitleASRProviderError(
                self._status_error_code(exc.response.status_code),
                f"ASR request failed with HTTP {exc.response.status_code}",
            ) from exc
        except httpx.HTTPError as exc:
            raise SubtitleASRProviderError("ASR_PROVIDER_HTTP_ERROR", "ASR request failed") from exc

        try:
            payload = response.json()
            if not isinstance(payload, dict):
                raise TypeError("response must be a JSON object")
            cues, precision, word_count = self._parse_cues(payload)
        except (TypeError, ValueError, KeyError) as exc:
            raise SubtitleASRProviderError(
                "ASR_PROVIDER_INVALID_RESPONSE",
                "ASR response did not contain valid timed segments or words",
            ) from exc

        return SubtitleASRGenerationResult(
            cues=cues,
            provider="openai_compatible_asr",
            model=self.model,
            precision=precision,
            duration_ms=max(1, int((monotonic() - started) * 1000)),
            metadata={
                "response_format": "verbose_json",
                "segment_count": len(payload.get("segments", []) or []),
                "word_count": word_count,
                "audio_inspected": True,
            },
        )

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    @classmethod
    def _parse_cues(
        cls,
        payload: dict[str, Any],
    ) -> tuple[list[SubtitleCueRequest], str, int]:
        segments = payload.get("segments")
        if isinstance(segments, list) and segments:
            cues = cls._parse_timed_items(segments)
            return cues, "segment_asr", len(payload.get("words", []) or [])

        words = payload.get("words")
        if isinstance(words, list) and words:
            cues = cls._group_word_items(words)
            return cues, "word_boundary", len(words)

        raise ValueError("timed segments or words are required")

    @staticmethod
    def _parse_timed_items(items: list[Any]) -> list[SubtitleCueRequest]:
        cues: list[SubtitleCueRequest] = []
        for item in items:
            if not isinstance(item, dict):
                raise TypeError("timed item must be an object")
            text = str(item.get("text", "")).strip()
            start = item.get("start")
            end = item.get("end")
            if not text or not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
                raise ValueError("timed item is incomplete")
            if not math.isfinite(float(start)) or not math.isfinite(float(end)):
                raise ValueError("timed item contains a non-finite timestamp")
            cues.append(
                SubtitleCueRequest(
                    start_seconds=float(start),
                    end_seconds=float(end),
                    text=text,
                )
            )
        return cues

    @classmethod
    def _group_word_items(cls, items: list[Any]) -> list[SubtitleCueRequest]:
        words: list[SubtitleCueRequest] = []
        for item in items:
            if not isinstance(item, dict):
                raise TypeError("word item must be an object")
            text = str(item.get("word", item.get("text", ""))).strip()
            start = item.get("start")
            end = item.get("end")
            if not text or not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
                raise ValueError("word item is incomplete")
            words.append(
                SubtitleCueRequest(
                    start_seconds=float(start),
                    end_seconds=float(end),
                    text=text,
                )
            )

        cues: list[SubtitleCueRequest] = []
        group: list[SubtitleCueRequest] = []
        for word in words:
            group.append(word)
            joined = " ".join(item.text for item in group)
            if len(joined) >= 24 or word.text.endswith(("。", "！", "？", ".", "!", "?")):
                cues.append(cls._merge_word_group(group))
                group = []
        if group:
            cues.append(cls._merge_word_group(group))
        return cues

    @staticmethod
    def _merge_word_group(group: list[SubtitleCueRequest]) -> SubtitleCueRequest:
        return SubtitleCueRequest(
            start_seconds=group[0].start_seconds,
            end_seconds=group[-1].end_seconds,
            text=" ".join(item.text for item in group),
        )

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
