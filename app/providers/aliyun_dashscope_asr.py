"""Alibaba Cloud Model Studio Paraformer ASR Provider.

The Paraformer recorded-speech API is asynchronous and does not accept raw
audio bytes directly. This adapter uploads the bytes to DashScope's temporary
OSS area, submits a transcription task, polls it until completion, and turns
the returned sentence/word timestamps into the stable subtitle Provider
contract used by the application.
"""

from __future__ import annotations

import asyncio
import math
from time import monotonic
from typing import Any
from uuid import uuid4

import httpx

from app.domain.models import (
    SubtitleASRGenerationRequest,
    SubtitleASRGenerationResult,
    SubtitleCueRequest,
)
from app.providers.errors_asr import SubtitleASRProviderError


class AliyunDashScopeASRProvider:
    """Transcribe audio with Alibaba Cloud Model Studio Paraformer-v2."""

    DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com"
    _PENDING_STATUSES = {"PENDING", "RUNNING"}

    def __init__(
        self,
        base_url: str,
        api_key: str | None,
        model: str,
        timeout_seconds: int,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        configured_base_url = base_url.strip().rstrip("/")
        self.base_url = configured_base_url or self.DEFAULT_BASE_URL
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)
        self._owns_client = client is None

    async def transcribe(
        self,
        request: SubtitleASRGenerationRequest,
    ) -> SubtitleASRGenerationResult:
        if not self.api_key:
            raise SubtitleASRProviderError(
                "ASR_PROVIDER_AUTH_FAILED",
                "AI_VIDEO_ASR_API_KEY is required for the Alibaba Cloud ASR provider",
            )
        if not self.model:
            raise SubtitleASRProviderError(
                "ASR_PROVIDER_NOT_CONFIGURED",
                "AI_VIDEO_ASR_MODEL is required for the Alibaba Cloud ASR provider",
            )

        started = monotonic()
        audio_url = await self._upload_audio(request)
        task_id = await self._submit_task(audio_url, request)
        result_payload = await self._poll_task(task_id)
        try:
            cues, precision, word_count = self._parse_result(result_payload)
        except (TypeError, ValueError, KeyError) as exc:
            raise SubtitleASRProviderError(
                "ASR_PROVIDER_INVALID_RESPONSE",
                "Alibaba Cloud ASR result did not contain valid timed sentences or words",
            ) from exc

        return SubtitleASRGenerationResult(
            cues=cues,
            provider="aliyun_dashscope_asr",
            model=self.model,
            precision=precision,
            duration_ms=max(1, int((monotonic() - started) * 1000)),
            metadata={
                "response_format": "aliyun_dashscope_async_json",
                "timestamp_source": "paraformer_v2_sentences_or_words",
                "segment_timestamps": precision == "segment_asr",
                "word_timestamps": precision == "word_boundary",
                "word_count": word_count,
                "audio_inspected": True,
                "audio_upload": "dashscope_temporary_oss",
                "timestamp_alignment_enabled": True,
                "reference_text_used": False,
            },
        )

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _upload_audio(self, request: SubtitleASRGenerationRequest) -> str:
        policy_payload = await self._request_json(
            "GET",
            f"{self.base_url}/api/v1/uploads",
            params={"action": "getPolicy", "model": self.model},
        )
        policy = policy_payload.get("data")
        if not isinstance(policy, dict):
            raise SubtitleASRProviderError(
                "ASR_PROVIDER_INVALID_RESPONSE",
                "Alibaba Cloud upload policy response did not contain data",
            )

        required = {
            "upload_host": policy.get("upload_host"),
            "upload_dir": policy.get("upload_dir"),
            "oss_access_key_id": policy.get("oss_access_key_id"),
            "signature": policy.get("signature"),
            "policy": policy.get("policy"),
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise SubtitleASRProviderError(
                "ASR_PROVIDER_INVALID_RESPONSE",
                "Alibaba Cloud upload policy is missing: " + ", ".join(missing),
            )

        filename = f"narration-{uuid4().hex}{self._suffix(request.mime_type)}"
        upload_dir = str(required["upload_dir"]).rstrip("/")
        object_key = f"{upload_dir}/{filename}"
        form_fields: list[tuple[str, tuple[None, str] | tuple[str, bytes, str]]] = [
            ("OSSAccessKeyId", (None, str(required["oss_access_key_id"]))),
            ("Signature", (None, str(required["signature"]))),
            ("policy", (None, str(required["policy"]))),
            ("x-oss-object-acl", (None, str(policy.get("x_oss_object_acl", "private")))),
            (
                "x-oss-forbid-overwrite",
                (None, str(policy.get("x_oss_forbid_overwrite", "true"))),
            ),
            ("key", (None, object_key)),
            ("success_action_status", (None, "200")),
            # DashScope requires the file form field to be last.
            ("file", (filename, request.audio_bytes, request.mime_type)),
        ]

        try:
            response = await self._client.post(
                str(required["upload_host"]),
                files=form_fields,
            )
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise SubtitleASRProviderError(
                "ASR_PROVIDER_TIMEOUT", "Alibaba Cloud audio upload timed out"
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise SubtitleASRProviderError(
                self._status_error_code(exc.response.status_code),
                f"Alibaba Cloud audio upload failed with HTTP {exc.response.status_code}",
            ) from exc
        except httpx.HTTPError as exc:
            raise SubtitleASRProviderError(
                "ASR_PROVIDER_HTTP_ERROR", "Alibaba Cloud audio upload failed"
            ) from exc

        return f"oss://{object_key}"

    async def _submit_task(
        self,
        audio_url: str,
        request: SubtitleASRGenerationRequest,
    ) -> str:
        language_hint = self._language_hint(request.language)
        parameters: dict[str, Any] = {
            "channel_id": [0],
            "timestamp_alignment_enabled": True,
        }
        if language_hint:
            parameters["language_hints"] = [language_hint]

        payload = await self._request_json(
            "POST",
            f"{self.base_url}/api/v1/services/audio/asr/transcription",
            headers={
                "X-DashScope-Async": "enable",
                "X-DashScope-OssResourceResolve": "enable",
            },
            json={
                "model": self.model,
                "input": {"file_urls": [audio_url]},
                "parameters": parameters,
            },
        )
        output = payload.get("output")
        task_id = output.get("task_id") if isinstance(output, dict) else None
        if not isinstance(task_id, str) or not task_id.strip():
            raise SubtitleASRProviderError(
                "ASR_PROVIDER_INVALID_RESPONSE",
                "Alibaba Cloud transcription response did not contain a task_id",
            )
        return task_id

    async def _poll_task(self, task_id: str) -> dict[str, Any]:
        deadline = monotonic() + max(1, self.timeout_seconds)
        while True:
            payload = await self._request_json(
                "POST",
                f"{self.base_url}/api/v1/tasks/{task_id}",
                headers={
                    "X-DashScope-Async": "enable",
                    "X-DashScope-OssResourceResolve": "enable",
                },
            )
            output = payload.get("output")
            if not isinstance(output, dict):
                raise SubtitleASRProviderError(
                    "ASR_PROVIDER_INVALID_RESPONSE",
                    "Alibaba Cloud task response did not contain output",
                )

            status = str(output.get("task_status", "")).upper()
            if status in self._PENDING_STATUSES:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise SubtitleASRProviderError(
                        "ASR_PROVIDER_TIMEOUT",
                        "Alibaba Cloud ASR task exceeded the configured timeout",
                    )
                await asyncio.sleep(min(1.0, remaining))
                continue

            if status != "SUCCEEDED":
                message = output.get("message") or payload.get("message") or status
                raise SubtitleASRProviderError(
                    "ASR_PROVIDER_HTTP_ERROR",
                    f"Alibaba Cloud ASR task failed: {message}",
                )

            results = output.get("results")
            if not isinstance(results, list) or not results:
                raise SubtitleASRProviderError(
                    "ASR_PROVIDER_INVALID_RESPONSE",
                    "Alibaba Cloud ASR task did not contain results",
                )
            result = next(
                (
                    item
                    for item in results
                    if isinstance(item, dict)
                    and str(item.get("subtask_status", "")).upper() == "SUCCEEDED"
                ),
                results[0],
            )
            if not isinstance(result, dict):
                raise SubtitleASRProviderError(
                    "ASR_PROVIDER_INVALID_RESPONSE",
                    "Alibaba Cloud ASR task result is not an object",
                )
            transcription_url = result.get("transcription_url")
            if not isinstance(transcription_url, str) or not transcription_url:
                raise SubtitleASRProviderError(
                    "ASR_PROVIDER_INVALID_RESPONSE",
                    "Alibaba Cloud ASR task result did not contain transcription_url",
                )
            # The transcription URL is a signed OSS URL. Do not attach the
            # DashScope Bearer header or JSON Content-Type to that request;
            # both are outside the signed OSS request and can cause HTTP 403.
            return await self._request_json(
                "GET",
                transcription_url,
                authenticated=False,
            )

    async def _request_json(
        self,
        method: str,
        url: str,
        *,
        authenticated: bool = True,
        **kwargs: Any,
    ) -> dict[str, Any]:
        request_headers: dict[str, str] = {}
        if authenticated:
            request_headers.update(
                {
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                }
            )
        request_headers.update(kwargs.pop("headers", {}) or {})
        try:
            response = await self._client.request(
                method,
                url,
                headers=request_headers,
                **kwargs,
            )
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise SubtitleASRProviderError(
                "ASR_PROVIDER_TIMEOUT", "Alibaba Cloud ASR request timed out"
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise SubtitleASRProviderError(
                self._status_error_code(exc.response.status_code),
                f"Alibaba Cloud ASR request failed with HTTP {exc.response.status_code}",
            ) from exc
        except httpx.HTTPError as exc:
            raise SubtitleASRProviderError(
                "ASR_PROVIDER_HTTP_ERROR", "Alibaba Cloud ASR request failed"
            ) from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise SubtitleASRProviderError(
                "ASR_PROVIDER_INVALID_RESPONSE",
                "Alibaba Cloud ASR response was not valid JSON",
            ) from exc
        if not isinstance(payload, dict):
            raise SubtitleASRProviderError(
                "ASR_PROVIDER_INVALID_RESPONSE",
                "Alibaba Cloud ASR response must be a JSON object",
            )
        return payload

    @classmethod
    def _parse_result(
        cls,
        payload: dict[str, Any],
    ) -> tuple[list[SubtitleCueRequest], str, int]:
        result = cls._find_transcription_result(payload)
        sentences = result.get("sentences")
        words = result.get("words")
        if isinstance(sentences, list) and sentences:
            cues = cls._parse_timed_items(sentences)
            word_count = len(words) if isinstance(words, list) else sum(
                len(item.get("words", []))
                for item in sentences
                if isinstance(item, dict) and isinstance(item.get("words"), list)
            )
            return cues, "segment_asr", word_count
        if isinstance(words, list) and words:
            return cls._group_words(words), "word_boundary", len(words)
        raise ValueError("sentences or words are required")

    @staticmethod
    def _find_transcription_result(payload: dict[str, Any]) -> dict[str, Any]:
        if isinstance(payload.get("sentences"), list) or isinstance(payload.get("words"), list):
            return payload
        transcripts = payload.get("transcripts")
        if isinstance(transcripts, list):
            for transcript in transcripts:
                if isinstance(transcript, dict) and (
                    isinstance(transcript.get("sentences"), list)
                    or isinstance(transcript.get("words"), list)
                ):
                    return transcript
        output = payload.get("output")
        if isinstance(output, dict):
            if isinstance(output.get("sentences"), list) or isinstance(output.get("words"), list):
                return output
            results = output.get("results")
            if isinstance(results, list):
                for result in results:
                    if isinstance(result, dict) and (
                        isinstance(result.get("sentences"), list)
                        or isinstance(result.get("words"), list)
                    ):
                        return result
        raise ValueError("transcription result is missing timed fields")

    @classmethod
    def _parse_timed_items(cls, items: list[Any]) -> list[SubtitleCueRequest]:
        cues: list[SubtitleCueRequest] = []
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("timed item must be an object")
            text = str(item.get("text", "")).strip()
            if not text:
                raise ValueError("timed item text is empty")
            start = cls._time_seconds(item, "begin_time", "start")
            end = cls._time_seconds(item, "end_time", "end")
            if start is None or end is None or end <= start:
                raise ValueError("timed item has invalid timestamps")
            cues.append(
                SubtitleCueRequest(
                    start_seconds=start,
                    end_seconds=end,
                    text=text,
                )
            )
        return cues

    @classmethod
    def _group_words(cls, items: list[Any]) -> list[SubtitleCueRequest]:
        grouped: list[SubtitleCueRequest] = []
        current: list[SubtitleCueRequest] = []
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("word item must be an object")
            text = str(item.get("text", item.get("word", ""))).strip()
            punctuation = str(item.get("punctuation", ""))
            if punctuation and not text.endswith(punctuation):
                text += punctuation
            start = cls._time_seconds(item, "begin_time", "start")
            end = cls._time_seconds(item, "end_time", "end")
            if not text or start is None or end is None or end <= start:
                raise ValueError("word item is incomplete")
            current.append(
                SubtitleCueRequest(start_seconds=start, end_seconds=end, text=text)
            )
            if len(cls._join_words(current)) >= 24 or text.endswith(("。", "！", "？", ".", "!", "?")):
                grouped.append(cls._merge_words(current))
                current = []
        if current:
            grouped.append(cls._merge_words(current))
        return grouped

    @classmethod
    def _merge_words(cls, words: list[SubtitleCueRequest]) -> SubtitleCueRequest:
        return SubtitleCueRequest(
            start_seconds=words[0].start_seconds,
            end_seconds=words[-1].end_seconds,
            text=cls._join_words(words),
        )

    @staticmethod
    def _join_words(words: list[SubtitleCueRequest]) -> str:
        text = ""
        for word in words:
            if not text:
                text = word.text
                continue
            needs_space = text[-1].isascii() and word.text[0].isascii()
            text += (" " if needs_space else "") + word.text
        return text

    @staticmethod
    def _time_seconds(item: dict[str, Any], millisecond_key: str, second_key: str) -> float | None:
        raw = item.get(millisecond_key)
        if raw is not None:
            try:
                value = float(raw) / 1000.0
            except (TypeError, ValueError):
                return None
        else:
            raw = item.get(second_key)
            if raw is None:
                return None
            try:
                value = float(raw)
            except (TypeError, ValueError):
                return None
        return value if math.isfinite(value) else None

    @staticmethod
    def _language_hint(language: str) -> str | None:
        normalized = language.strip().lower().replace("_", "-")
        if normalized.startswith("zh"):
            return "zh"
        if normalized.startswith("en"):
            return "en"
        return normalized.split("-", 1)[0] or None

    @staticmethod
    def _suffix(mime_type: str) -> str:
        return {
            "audio/wav": ".wav",
            "audio/mpeg": ".mp3",
            "audio/mp3": ".mp3",
            "audio/mp4": ".m4a",
            "audio/ogg": ".ogg",
            "audio/webm": ".webm",
        }.get(mime_type.lower(), ".audio")

    @staticmethod
    def _status_error_code(status_code: int) -> str:
        if status_code in {401, 403}:
            return "ASR_PROVIDER_AUTH_FAILED"
        if status_code == 429:
            return "ASR_PROVIDER_RATE_LIMITED"
        if status_code >= 500:
            return "ASR_PROVIDER_UNAVAILABLE"
        return "ASR_PROVIDER_HTTP_ERROR"
