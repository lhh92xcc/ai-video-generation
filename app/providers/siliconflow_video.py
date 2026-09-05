"""SiliconFlow asynchronous video-generation provider."""

from __future__ import annotations

import asyncio
import base64
import json
from time import monotonic
from typing import Any

import httpx

from app.domain.models import VideoClipGenerationRequest, VideoClipGenerationResult
from app.providers.errors_video import VideoProviderError


class SiliconFlowVideoGenerationProvider:
    """Submit, poll and download one SiliconFlow Wan video."""

    _SUCCESS_STATUSES = {"succeed", "succeeded", "complete", "completed", "success"}
    _PENDING_STATUSES = {"inqueue", "in_progress", "inprogress", "queued", "processing"}
    _FAILED_STATUSES = {"failed", "failure", "error", "cancelled", "canceled"}

    def __init__(
        self,
        base_url: str,
        api_key: str | None,
        model: str,
        image_size: str,
        timeout_seconds: int,
        poll_interval_seconds: float = 2.0,
        max_poll_seconds: int = 900,
        max_download_bytes: int = 524_288_000,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._image_size = image_size
        self._timeout_seconds = max(1, timeout_seconds)
        self._poll_interval_seconds = max(0.0, poll_interval_seconds)
        self._max_poll_seconds = max(1, max_poll_seconds)
        self._max_download_bytes = max(1, max_download_bytes)
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)
        self._owns_client = client is None

    async def generate_video_clip(
        self,
        request: VideoClipGenerationRequest,
    ) -> VideoClipGenerationResult:
        self._require_configuration()
        started = monotonic()
        headers = {"Authorization": f"Bearer {self._api_key}"}
        payload = {
            "model": self._model,
            "prompt": request.prompt,
            "negative_prompt": request.negative_prompt,
            "image_size": self._image_size,
        }
        submitted = await self._request_json(
            "post",
            self._endpoint("/video/submit"),
            headers=headers,
            json_payload=payload,
            operation="SiliconFlow video submission",
        )
        request_id = self._extract_request_id(submitted)
        if not request_id:
            raise VideoProviderError(
                "VIDEO_PROVIDER_INVALID_RESPONSE",
                "SiliconFlow video submission did not return requestId",
            )

        polls = 0
        deadline = monotonic() + self._max_poll_seconds
        status_payload: dict[str, Any] = submitted
        while True:
            status = self._normalize_status(status_payload.get("status"))
            if status in self._SUCCESS_STATUSES:
                break
            if status in self._FAILED_STATUSES:
                reason = status_payload.get("reason") or status_payload.get("message")
                detail = (
                    reason
                    if isinstance(reason, str) and reason
                    else "SiliconFlow reported a failed video job"
                )
                raise VideoProviderError("VIDEO_PROVIDER_FAILED", detail)
            if status and status not in self._PENDING_STATUSES:
                raise VideoProviderError(
                    "VIDEO_PROVIDER_INVALID_RESPONSE",
                    f"SiliconFlow returned unknown video status: {status_payload.get('status')}",
                )
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise VideoProviderError(
                    "VIDEO_PROVIDER_TIMEOUT",
                    "SiliconFlow video polling exceeded the configured maximum wait time",
                )
            await asyncio.sleep(min(self._poll_interval_seconds, remaining))
            status_payload = await self._request_json(
                "post",
                self._endpoint("/video/status"),
                headers=headers,
                json_payload={"requestId": request_id},
                operation="SiliconFlow video polling",
            )
            polls += 1

        output_url = self._extract_video_url(status_payload)
        if not output_url:
            raise VideoProviderError(
                "VIDEO_PROVIDER_INVALID_RESPONSE",
                "SiliconFlow completed the video job without results.videos[0].url",
            )
        mime_type, video_base64 = await self._download_video(output_url, headers)
        return VideoClipGenerationResult(
            output_uri=output_url,
            video_base64=video_base64,
            mime_type=mime_type,
            provider="siliconflow",
            model=self._model,
            duration_seconds=request.duration_seconds,
            duration_ms=max(1, round((monotonic() - started) * 1000)),
            metadata={
                "request_id": request_id,
                "polls": polls,
                "image_size": self._image_size,
                "duration_is_requested_value": True,
                "note": "SiliconFlow Wan output duration must be confirmed by ffprobe.",
            },
        )

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _request_json(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        json_payload: dict[str, Any],
        operation: str,
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

    async def _download_video(
        self,
        url: str,
        headers: dict[str, str],
    ) -> tuple[str, str]:
        try:
            response = await self._client.get(url, headers=headers)
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise VideoProviderError(
                "VIDEO_PROVIDER_TIMEOUT", "SiliconFlow video download timed out"
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise VideoProviderError(
                self._status_error_code(exc.response.status_code),
                f"SiliconFlow video download failed with HTTP {exc.response.status_code}",
            ) from exc
        except httpx.HTTPError as exc:
            raise VideoProviderError(
                "VIDEO_PROVIDER_HTTP_ERROR", "SiliconFlow video download failed"
            ) from exc

        content_length = response.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > self._max_download_bytes:
                    raise VideoProviderError(
                        "VIDEO_PROVIDER_OUTPUT_TOO_LARGE",
                        "SiliconFlow video exceeds the configured download limit",
                    )
            except ValueError as exc:
                raise VideoProviderError(
                    "VIDEO_PROVIDER_INVALID_RESPONSE",
                    "SiliconFlow video returned an invalid Content-Length",
                ) from exc
        content = response.content
        if len(content) > self._max_download_bytes:
            raise VideoProviderError(
                "VIDEO_PROVIDER_OUTPUT_TOO_LARGE",
                "SiliconFlow video exceeds the configured download limit",
            )
        if not content:
            raise VideoProviderError(
                "VIDEO_PROVIDER_INVALID_RESPONSE", "SiliconFlow returned an empty video"
            )
        content_type = response.headers.get("content-type", "video/mp4").split(";", 1)[0]
        if not content_type.startswith("video/"):
            raise VideoProviderError(
                "VIDEO_PROVIDER_INVALID_RESPONSE",
                "SiliconFlow video download did not return a video MIME type",
            )
        return content_type, base64.b64encode(content).decode("ascii")

    def _endpoint(self, path: str) -> str:
        versioned_base = (
            self._base_url if self._base_url.endswith("/v1") else f"{self._base_url}/v1"
        )
        return f"{versioned_base}{path}"

    def _require_configuration(self) -> None:
        if not self._api_key:
            raise VideoProviderError(
                "VIDEO_PROVIDER_AUTH_FAILED",
                "AI_VIDEO_VIDEO_API_KEY is required for SiliconFlow",
            )
        if not self._base_url:
            raise VideoProviderError(
                "VIDEO_PROVIDER_INVALID_CONFIGURATION",
                "AI_VIDEO_VIDEO_BASE_URL is required for SiliconFlow",
            )
        if not self._image_size:
            raise VideoProviderError(
                "VIDEO_PROVIDER_INVALID_CONFIGURATION",
                "AI_VIDEO_VIDEO_IMAGE_SIZE is required for SiliconFlow",
            )

    @staticmethod
    def _extract_request_id(payload: dict[str, Any]) -> str | None:
        for key in ("requestId", "request_id", "id"):
            value = payload.get(key)
            if isinstance(value, (str, int)) and str(value):
                return str(value)
        return None

    @staticmethod
    def _normalize_status(value: Any) -> str:
        if not isinstance(value, str):
            return ""
        return value.strip().lower().replace("-", "_").replace(" ", "_")

    @staticmethod
    def _extract_video_url(payload: dict[str, Any]) -> str | None:
        results = payload.get("results")
        if not isinstance(results, dict):
            return None
        videos = results.get("videos")
        if not isinstance(videos, list) or not videos or not isinstance(videos[0], dict):
            return None
        for key in ("url", "video_url", "output_url"):
            value = videos[0].get(key)
            if isinstance(value, str) and value:
                return value
        return None

    @staticmethod
    def _status_error_code(status_code: int) -> str:
        if status_code in {401, 403}:
            return "VIDEO_PROVIDER_AUTH_FAILED"
        if status_code == 429:
            return "VIDEO_PROVIDER_RATE_LIMITED"
        if status_code >= 500:
            return "VIDEO_PROVIDER_UNAVAILABLE"
        return "VIDEO_PROVIDER_HTTP_ERROR"
