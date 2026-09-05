"""SiliconFlow image-generation provider."""

from __future__ import annotations

import base64
from time import monotonic
from typing import Any

import httpx

from app.domain.models import ReferenceImageGenerationRequest, ReferenceImageGenerationResult
from app.providers.errors import ImageProviderError


class SiliconFlowImageGenerationProvider:
    """Generate and immediately download one SiliconFlow image artifact."""

    def __init__(
        self,
        base_url: str,
        api_key: str | None,
        model: str,
        timeout_seconds: int,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)
        self._owns_client = client is None

    async def generate_reference_image(
        self,
        request: ReferenceImageGenerationRequest,
    ) -> ReferenceImageGenerationResult:
        self._require_configuration()
        started = monotonic()
        payload = {
            "model": self._model,
            "prompt": request.prompt,
            "negative_prompt": request.negative_prompt,
            "image_size": f"{request.width}x{request.height}",
            "batch_size": 1,
        }
        try:
            response = await self._client.post(
                self._endpoint("/images/generations"),
                headers={"Authorization": f"Bearer {self._api_key}"},
                json=payload,
            )
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise ImageProviderError(
                "IMAGE_PROVIDER_TIMEOUT", "SiliconFlow image request timed out"
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise ImageProviderError(
                self._status_error_code(exc.response.status_code),
                f"SiliconFlow image request failed with HTTP {exc.response.status_code}",
            ) from exc
        except httpx.HTTPError as exc:
            raise ImageProviderError(
                "IMAGE_PROVIDER_HTTP_ERROR", "SiliconFlow image request failed"
            ) from exc

        try:
            response_json = response.json()
            images = response_json["images"]
            item = images[0]
            remote_url = item["url"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ImageProviderError(
                "IMAGE_PROVIDER_INVALID_RESPONSE",
                "SiliconFlow response did not contain images[0].url",
            ) from exc
        if not isinstance(remote_url, str) or not remote_url:
            raise ImageProviderError(
                "IMAGE_PROVIDER_INVALID_RESPONSE",
                "SiliconFlow image URL must be a non-empty string",
            )

        try:
            image_response = await self._client.get(remote_url)
            image_response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise ImageProviderError(
                "IMAGE_PROVIDER_TIMEOUT", "SiliconFlow image download timed out"
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise ImageProviderError(
                self._status_error_code(exc.response.status_code),
                f"SiliconFlow image download failed with HTTP {exc.response.status_code}",
            ) from exc
        except httpx.HTTPError as exc:
            raise ImageProviderError(
                "IMAGE_PROVIDER_HTTP_ERROR", "SiliconFlow image download failed"
            ) from exc

        content = image_response.content
        mime_type = image_response.headers.get("content-type", "").split(";", 1)[0]
        if not mime_type.startswith("image/"):
            mime_type = self._sniff_image_mime(content)
        if not mime_type or not content:
            raise ImageProviderError(
                "IMAGE_PROVIDER_INVALID_RESPONSE",
                "SiliconFlow image download did not return non-empty image bytes",
            )
        return ReferenceImageGenerationResult(
            image_base64=base64.b64encode(content).decode("ascii"),
            mime_type=mime_type,
            provider="siliconflow",
            model=self._model,
            width=request.width,
            height=request.height,
            duration_ms=max(1, round((monotonic() - started) * 1000)),
            metadata={
                "response_format": "url",
                "remote_url": remote_url,
                "image_size": f"{request.width}x{request.height}",
            },
        )

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _require_configuration(self) -> None:
        if not self._api_key:
            raise ImageProviderError(
                "IMAGE_PROVIDER_AUTH_FAILED",
                "AI_VIDEO_IMAGE_API_KEY is required for SiliconFlow",
            )
        if not self._base_url:
            raise ImageProviderError(
                "IMAGE_PROVIDER_INVALID_CONFIGURATION",
                "AI_VIDEO_IMAGE_BASE_URL is required for SiliconFlow",
            )

    def _endpoint(self, path: str) -> str:
        versioned_base = (
            self._base_url if self._base_url.endswith("/v1") else f"{self._base_url}/v1"
        )
        return f"{versioned_base}{path}"

    @staticmethod
    def _sniff_image_mime(content: bytes) -> str:
        """Recover an image MIME type when a CDN uses application/octet-stream."""

        if content.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        if content.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"
        if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
            return "image/webp"
        if content.startswith((b"GIF87a", b"GIF89a")):
            return "image/gif"
        return ""

    @staticmethod
    def _status_error_code(status_code: int) -> str:
        if status_code in {401, 403}:
            return "IMAGE_PROVIDER_AUTH_FAILED"
        if status_code == 429:
            return "IMAGE_PROVIDER_RATE_LIMITED"
        if status_code >= 500:
            return "IMAGE_PROVIDER_UNAVAILABLE"
        return "IMAGE_PROVIDER_HTTP_ERROR"
