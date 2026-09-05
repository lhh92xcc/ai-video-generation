"""Configurable asynchronous HTTP video-generation adapter.

There is no single provider-neutral video API shape today. This adapter keeps
the application contract stable while supporting the common job lifecycle:
submit a generation request, poll a job, then download the resulting MP4/WebM.
The endpoint paths and response fields are intentionally tolerant, but vendor-
specific request/response transformations should still live in a dedicated
adapter when a provider needs more than this common shape.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
from time import monotonic
from typing import Any
from urllib.parse import quote, urljoin

import httpx

from app.domain.models import VideoClipGenerationRequest, VideoClipGenerationResult
from app.providers.errors_video import VideoProviderError


class OpenAICompatibleVideoGenerationProvider:
    """Generate one shot through a submit/poll/download HTTP API."""

    _SUCCESS_STATUSES = {"completed", "complete", "succeeded", "success", "done"}
    _FAILED_STATUSES = {"failed", "failure", "error", "cancelled", "canceled"}

    def __init__(
        self,
        base_url: str,
        api_key: str | None,
        model: str,
        timeout_seconds: int,
        create_path: str = "/videos/generations",
        status_path_template: str = "/videos/generations/{task_id}",
        poll_interval_seconds: float = 2.0,
        max_poll_seconds: int = 900,
        max_download_bytes: int = 524_288_000,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.create_path = create_path
        self.status_path_template = status_path_template
        self.poll_interval_seconds = max(0.0, poll_interval_seconds)
        self.max_poll_seconds = max(1, max_poll_seconds)
        self.max_download_bytes = max(1, max_download_bytes)
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)
        self._owns_client = client is None

    async def generate_video_clip(
        self,
        request: VideoClipGenerationRequest,
    ) -> VideoClipGenerationResult:
        self._require_configuration()
        started = monotonic()
        headers = {"Authorization": f"Bearer {self.api_key}"}
        payload = {
            "model": self.model,
            "prompt": request.prompt,
            "negative_prompt": request.negative_prompt,
            "duration_seconds": request.duration_seconds,
            "asset_refs": [item.model_dump(mode="json") for item in request.asset_refs],
        }

        response_json = await self._request_json(
            "post",
            self._build_url(self.create_path),
            headers=headers,
            json_payload=payload,
            operation="video generation submission",
        )
        parsed = self._parse_payload(response_json)

        immediate = self._extract_video(parsed)
        if immediate is not None:
            return await self._result_from_video(
                immediate,
                request,
                started,
                headers,
                metadata={"request_id": self._extract_id(parsed), "polls": 0},
            )

        task_id = self._extract_id(parsed)
        if not task_id:
            raise VideoProviderError(
                "VIDEO_PROVIDER_INVALID_RESPONSE",
                "Video provider did not return a job ID or video output",
            )
        status_url = self._extract_status_url(parsed, task_id)
        status = self._normalize_status(parsed.get("status"))
        polls = 0
        deadline = monotonic() + self.max_poll_seconds

        while status not in self._SUCCESS_STATUSES:
            if status in self._FAILED_STATUSES:
                message = self._extract_message(parsed) or "Video provider reported a failed job"
                raise VideoProviderError("VIDEO_PROVIDER_FAILED", message)
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise VideoProviderError(
                    "VIDEO_PROVIDER_TIMEOUT",
                    "Video provider polling exceeded the configured maximum wait time",
                )
            await asyncio.sleep(min(self.poll_interval_seconds, remaining))
            parsed = await self._request_json(
                "get",
                status_url,
                headers=headers,
                operation="video generation polling",
            )
            parsed = self._parse_payload(parsed)
            status = self._normalize_status(parsed.get("status"))
            polls += 1

        output = self._extract_video(parsed)
        if output is None:
            raise VideoProviderError(
                "VIDEO_PROVIDER_INVALID_RESPONSE",
                "Video provider completed the job without a video output",
            )
        return await self._result_from_video(
            output,
            request,
            started,
            headers,
            metadata={"request_id": task_id, "polls": polls},
        )

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _require_configuration(self) -> None:
        if not self.api_key:
            raise VideoProviderError(
                "VIDEO_PROVIDER_AUTH_FAILED",
                "AI_VIDEO_VIDEO_API_KEY is required for the configured video provider",
            )
        if not self.base_url:
            raise VideoProviderError(
                "VIDEO_PROVIDER_INVALID_CONFIGURATION",
                "AI_VIDEO_VIDEO_BASE_URL is required for the configured video provider",
            )

    async def _request_json(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        operation: str,
        json_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            response = await self._client.request(
                method,
                url,
                headers=headers,
                json=json_payload,
            )
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise VideoProviderError("VIDEO_PROVIDER_TIMEOUT", f"{operation} timed out") from exc
        except httpx.HTTPStatusError as exc:
            raise VideoProviderError(
                self._status_error_code(exc.response.status_code),
                f"{operation} failed with HTTP {exc.response.status_code}",
            ) from exc
        except httpx.HTTPError as exc:
            raise VideoProviderError("VIDEO_PROVIDER_HTTP_ERROR", f"{operation} failed") from exc

        try:
            payload = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise VideoProviderError(
                "VIDEO_PROVIDER_INVALID_RESPONSE",
                f"{operation} returned invalid JSON",
            ) from exc
        if not isinstance(payload, dict):
            raise VideoProviderError(
                "VIDEO_PROVIDER_INVALID_RESPONSE",
                f"{operation} returned a non-object JSON payload",
            )
        return payload

    async def _result_from_video(
        self,
        output: dict[str, Any],
        request: VideoClipGenerationRequest,
        started: float,
        headers: dict[str, str],
        metadata: dict[str, Any],
    ) -> VideoClipGenerationResult:
        output_uri = self._extract_output_uri(output)
        video_base64 = self._extract_base64(output)
        mime_type = self._extract_mime_type(output) or "video/mp4"

        if video_base64 is None and output_uri:
            if output_uri.startswith("data:"):
                mime_type, video_base64 = self._decode_data_uri(output_uri)
            elif output_uri.startswith(("http://", "https://")):
                mime_type, video_base64 = await self._download_video(output_uri, headers)

        if video_base64 is None and not output_uri:
            raise VideoProviderError(
                "VIDEO_PROVIDER_INVALID_RESPONSE",
                "Video provider returned neither video content nor an output URI",
            )
        if video_base64 is not None:
            self._validate_base64_size(video_base64)

        metadata = {
            **metadata,
            "remote_output_uri": output_uri,
            "downloaded": video_base64 is not None and bool(output_uri),
        }
        return VideoClipGenerationResult(
            output_uri=output_uri,
            video_base64=video_base64,
            mime_type=mime_type,
            provider="openai_compatible",
            model=self.model,
            duration_seconds=request.duration_seconds,
            duration_ms=max(1, int((monotonic() - started) * 1000)),
            metadata=metadata,
        )

    async def _download_video(
        self,
        url: str,
        headers: dict[str, str],
    ) -> tuple[str, str]:
        try:
            response = await self._client.get(url, headers=headers)
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise VideoProviderError("VIDEO_PROVIDER_TIMEOUT", "Video download timed out") from exc
        except httpx.HTTPStatusError as exc:
            raise VideoProviderError(
                self._status_error_code(exc.response.status_code),
                f"Video download failed with HTTP {exc.response.status_code}",
            ) from exc
        except httpx.HTTPError as exc:
            raise VideoProviderError("VIDEO_PROVIDER_HTTP_ERROR", "Video download failed") from exc

        content_length = response.headers.get("content-length")
        if content_length:
            try:
                declared_size = int(content_length)
            except ValueError as exc:
                raise VideoProviderError(
                    "VIDEO_PROVIDER_INVALID_RESPONSE",
                    "Video download returned an invalid Content-Length",
                ) from exc
            if declared_size > self.max_download_bytes:
                raise VideoProviderError(
                    "VIDEO_PROVIDER_OUTPUT_TOO_LARGE",
                    "Video provider output exceeds the configured download limit",
                )
        content = response.content
        if len(content) > self.max_download_bytes:
            raise VideoProviderError(
                "VIDEO_PROVIDER_OUTPUT_TOO_LARGE",
                "Video provider output exceeds the configured download limit",
            )
        if not content:
            raise VideoProviderError(
                "VIDEO_PROVIDER_INVALID_RESPONSE",
                "Video provider returned an empty video file",
            )
        content_type = response.headers.get("content-type", "video/mp4").split(";", 1)[0]
        if not content_type.startswith("video/") and content_type != "application/octet-stream":
            raise VideoProviderError(
                "VIDEO_PROVIDER_INVALID_RESPONSE",
                "Video download did not return a video content type",
            )
        return (
            "video/webm" if content_type == "video/webm" else "video/mp4",
            base64.b64encode(content).decode("ascii"),
        )

    def _build_url(self, path_or_url: str) -> str:
        return urljoin(f"{self.base_url}/", path_or_url.lstrip("/"))

    def _extract_status_url(self, payload: dict[str, Any], task_id: str) -> str:
        raw_url = payload.get("status_url") or payload.get("statusUrl")
        if isinstance(raw_url, str) and raw_url:
            return self._build_url(raw_url) if not raw_url.startswith("http") else raw_url
        return self._build_url(
            self.status_path_template.format(task_id=quote(task_id, safe=""))
        )

    @classmethod
    def _parse_payload(cls, payload: dict[str, Any]) -> dict[str, Any]:
        data = payload.get("data")
        if isinstance(data, dict):
            return {**payload, **data}
        if isinstance(data, list) and data and isinstance(data[0], dict):
            return {**payload, **data[0]}
        result = payload.get("result")
        if isinstance(result, dict):
            return {**payload, **result}
        return payload

    @staticmethod
    def _extract_id(payload: dict[str, Any]) -> str | None:
        for key in ("id", "task_id", "taskId", "request_id", "requestId"):
            value = payload.get(key)
            if isinstance(value, (str, int)) and str(value):
                return str(value)
        return None

    @classmethod
    def _extract_video(cls, payload: dict[str, Any]) -> dict[str, Any] | None:
        for key in ("video", "output", "result"):
            value = payload.get(key)
            if isinstance(value, dict) and (
                cls._extract_output_uri(value) or cls._extract_base64(value)
            ):
                return value
        if cls._extract_output_uri(payload) or cls._extract_base64(payload):
            return payload
        return None

    @staticmethod
    def _extract_output_uri(payload: dict[str, Any]) -> str | None:
        for key in (
            "output_uri",
            "output_url",
            "video_url",
            "video_uri",
            "download_url",
            "url",
            "video",
            "output",
            "result",
        ):
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value
        return None

    @staticmethod
    def _extract_base64(payload: dict[str, Any]) -> str | None:
        for key in ("video_base64", "b64_json", "b64", "base64"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value
        return None

    @staticmethod
    def _extract_mime_type(payload: dict[str, Any]) -> str | None:
        for key in ("mime_type", "mimeType", "content_type", "contentType"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value.split(";", 1)[0]
        return None

    @staticmethod
    def _normalize_status(value: Any) -> str:
        return str(value or "").strip().lower()

    @staticmethod
    def _extract_message(payload: dict[str, Any]) -> str | None:
        for key in ("error", "message", "error_message", "errorMessage"):
            value = payload.get(key)
            if isinstance(value, dict):
                value = value.get("message")
            if isinstance(value, str) and value:
                return value
        return None

    @staticmethod
    def _decode_data_uri(data_uri: str) -> tuple[str, str]:
        header, separator, encoded = data_uri.partition(",")
        if not separator or ";base64" not in header:
            raise VideoProviderError(
                "VIDEO_PROVIDER_INVALID_RESPONSE",
                "Video provider returned an unsupported data URI",
            )
        mime_type = header[5:].split(";", 1)[0] or "video/mp4"
        try:
            base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise VideoProviderError(
                "VIDEO_PROVIDER_INVALID_RESPONSE",
                "Video provider returned invalid base64 video content",
            ) from exc
        return mime_type, encoded

    def _validate_base64_size(self, encoded: str) -> None:
        try:
            decoded_size = len(base64.b64decode(encoded, validate=True))
        except (ValueError, binascii.Error) as exc:
            raise VideoProviderError(
                "VIDEO_PROVIDER_INVALID_RESPONSE",
                "Video provider returned invalid base64 video content",
            ) from exc
        if decoded_size > self.max_download_bytes:
            raise VideoProviderError(
                "VIDEO_PROVIDER_OUTPUT_TOO_LARGE",
                "Video provider output exceeds the configured download limit",
            )

    @staticmethod
    def _status_error_code(status_code: int) -> str:
        if status_code in {401, 403}:
            return "VIDEO_PROVIDER_AUTH_FAILED"
        if status_code == 429:
            return "VIDEO_PROVIDER_RATE_LIMITED"
        if status_code >= 500:
            return "VIDEO_PROVIDER_UNAVAILABLE"
        return "VIDEO_PROVIDER_HTTP_ERROR"
