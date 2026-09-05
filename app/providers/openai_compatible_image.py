"""OpenAI-compatible image-generation adapter.

The adapter accepts the common `/images/generations` response shapes (`b64_json`
or `url`) and normalizes them into the application image result contract. It
does not write files or know about object storage; that remains a storage concern.
"""

from __future__ import annotations

import base64
import binascii
from time import monotonic
from typing import Any

import httpx

from app.domain.models import ReferenceImageGenerationRequest, ReferenceImageGenerationResult
from app.providers.errors import ImageProviderError


class OpenAICompatibleImageGenerationProvider:
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
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)
        self._owns_client = client is None

    async def generate_reference_image(
        self,
        request: ReferenceImageGenerationRequest,
    ) -> ReferenceImageGenerationResult:
        if not self.api_key:
            raise ImageProviderError(
                "IMAGE_PROVIDER_AUTH_FAILED",
                "AI_VIDEO_IMAGE_API_KEY is required for the OpenAI-compatible image provider",
            )

        started = monotonic()
        payload = {
            "model": self.model,
            "prompt": request.prompt,
            "negative_prompt": request.negative_prompt,
            "size": f"{request.width}x{request.height}",
            "n": 1,
            "response_format": "b64_json",
        }
        try:
            response = await self._client.post(
                f"{self.base_url}/images/generations",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=payload,
            )
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise ImageProviderError("IMAGE_PROVIDER_TIMEOUT", "Image request timed out") from exc
        except httpx.HTTPStatusError as exc:
            raise ImageProviderError(
                self._status_error_code(exc.response.status_code),
                f"Image request failed with HTTP {exc.response.status_code}",
            ) from exc
        except httpx.HTTPError as exc:
            raise ImageProviderError("IMAGE_PROVIDER_HTTP_ERROR", "Image request failed") from exc

        try:
            response_json: dict[str, Any] = response.json()
            item = response_json["data"][0]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ImageProviderError(
                "IMAGE_PROVIDER_INVALID_RESPONSE",
                "Image provider response did not contain data[0]",
            ) from exc

        if not isinstance(item, dict):
            raise ImageProviderError(
                "IMAGE_PROVIDER_INVALID_RESPONSE",
                "Image provider data[0] must be an object",
            )

        image_base64 = item.get("b64_json")
        if image_base64 is not None:
            self._validate_base64(image_base64)
            return ReferenceImageGenerationResult(
                image_base64=image_base64,
                mime_type="image/png",
                provider="openai_compatible",
                model=self.model,
                width=request.width,
                height=request.height,
                duration_ms=max(1, int((monotonic() - started) * 1000)),
                metadata={"response_format": "b64_json"},
            )

        remote_url = item.get("url")
        if isinstance(remote_url, str) and remote_url:
            return await self._download_url(remote_url, request, started)

        raise ImageProviderError(
            "IMAGE_PROVIDER_INVALID_RESPONSE",
            "Image provider response must contain b64_json or url",
        )

    async def _download_url(
        self,
        remote_url: str,
        request: ReferenceImageGenerationRequest,
        started: float,
    ) -> ReferenceImageGenerationResult:
        try:
            response = await self._client.get(remote_url)
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise ImageProviderError("IMAGE_PROVIDER_TIMEOUT", "Image download timed out") from exc
        except httpx.HTTPStatusError as exc:
            raise ImageProviderError(
                self._status_error_code(exc.response.status_code),
                f"Image download failed with HTTP {exc.response.status_code}",
            ) from exc
        except httpx.HTTPError as exc:
            raise ImageProviderError("IMAGE_PROVIDER_HTTP_ERROR", "Image download failed") from exc

        mime_type = response.headers.get("content-type", "image/png").split(";", 1)[0]
        if not mime_type.startswith("image/"):
            raise ImageProviderError(
                "IMAGE_PROVIDER_INVALID_RESPONSE",
                "Image URL did not return an image content type",
            )
        return ReferenceImageGenerationResult(
            image_base64=base64.b64encode(response.content).decode("ascii"),
            mime_type=mime_type,
            provider="openai_compatible",
            model=self.model,
            width=request.width,
            height=request.height,
            duration_ms=max(1, int((monotonic() - started) * 1000)),
            metadata={"response_format": "url", "remote_url": remote_url},
        )

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    @staticmethod
    def _validate_base64(value: Any) -> None:
        if not isinstance(value, str) or not value:
            raise ImageProviderError(
                "IMAGE_PROVIDER_INVALID_RESPONSE",
                "Image provider b64_json must be a non-empty string",
            )
        try:
            base64.b64decode(value, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ImageProviderError(
                "IMAGE_PROVIDER_INVALID_RESPONSE",
                "Image provider b64_json was not valid base64",
            ) from exc

    @staticmethod
    def _status_error_code(status_code: int) -> str:
        if status_code in {401, 403}:
            return "IMAGE_PROVIDER_AUTH_FAILED"
        if status_code == 429:
            return "IMAGE_PROVIDER_RATE_LIMITED"
        if status_code >= 500:
            return "IMAGE_PROVIDER_UNAVAILABLE"
        return "IMAGE_PROVIDER_HTTP_ERROR"
