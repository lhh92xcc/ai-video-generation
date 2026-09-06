"""ComfyUI HTTP API adapter for local reference-image generation."""

from __future__ import annotations

import base64
import copy
import hashlib
import json
from pathlib import Path
from time import monotonic
from typing import Any
from uuid import uuid4

import httpx

from app.domain.models import ReferenceImageGenerationRequest, ReferenceImageGenerationResult
from app.providers.errors import ImageProviderError


class ComfyUIImageGenerationProvider:
    """Submit an API-format ComfyUI workflow and download its first image output.

    The workflow is kept outside the provider so users can replace the model or
    node graph without changing business code. The supplied template uses the
    ``__AI_VIDEO_*__`` placeholders documented below.
    """

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8188",
        workflow_path: str = "config/comfyui/flux-schnell-t2i-api.json",
        model: str = "flux1-schnell-Q4_K_S.gguf",
        timeout_seconds: int = 600,
        steps: int = 4,
        guidance: float = 3.5,
        identity_weight: float = 0.9,
        poll_interval_seconds: float = 1.0,
        max_poll_seconds: int = 600,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.workflow_path = Path(workflow_path)
        self.model = model
        self.timeout_seconds = max(1, timeout_seconds)
        self.steps = max(1, min(30, steps))
        self.guidance = max(0.0, min(20.0, guidance))
        self.identity_weight = max(0.0, min(1.5, identity_weight))
        self.poll_interval_seconds = max(0.1, poll_interval_seconds)
        self.max_poll_seconds = max(1, max_poll_seconds)
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)
        self._owns_client = client is None

    async def generate_reference_image(
        self,
        request: ReferenceImageGenerationRequest,
    ) -> ReferenceImageGenerationResult:
        workflow = self._load_workflow()
        seed = self._seed_for(request)
        identity_image_info: dict[str, str] | None = None
        workflow_text = json.dumps(workflow, ensure_ascii=False)
        if "__AI_VIDEO_IDENTITY_IMAGE__" in workflow_text:
            if not request.identity_image_bytes:
                raise ImageProviderError(
                    "IMAGE_IDENTITY_REQUIRED",
                    "This Flux identity workflow requires a succeeded identity reference image",
                )
            identity_image_info = await self._upload_identity_image(request)
        rendered_workflow = self._replace_placeholders(
            workflow,
            {
                "__AI_VIDEO_PROMPT__": request.prompt,
                "__AI_VIDEO_NEGATIVE_PROMPT__": request.negative_prompt,
                "__AI_VIDEO_WIDTH__": request.width,
                "__AI_VIDEO_HEIGHT__": request.height,
                "__AI_VIDEO_SEED__": seed,
                "__AI_VIDEO_IMAGE_STEPS__": self.steps,
                "__AI_VIDEO_IMAGE_GUIDANCE__": self.guidance,
                "__AI_VIDEO_IMAGE_IDENTITY_WEIGHT__": self.identity_weight,
                "__AI_VIDEO_CHECKPOINT__": self.model,
                "__AI_VIDEO_IDENTITY_IMAGE__": identity_image_info["name"]
                if identity_image_info
                else "",
                "__AI_VIDEO_IDENTITY_SUBFOLDER__": identity_image_info["subfolder"]
                if identity_image_info
                else "",
                "__AI_VIDEO_IDENTITY_TYPE__": identity_image_info["type"]
                if identity_image_info
                else "input",
            },
        )
        started = monotonic()
        try:
            response = await self._client.post(
                f"{self.base_url}/prompt",
                json={"prompt": rendered_workflow, "client_id": str(uuid4())},
            )
            response.raise_for_status()
            prompt_response = response.json()
            if not isinstance(prompt_response, dict):
                raise ImageProviderError(
                    "IMAGE_PROVIDER_INVALID_RESPONSE",
                    "ComfyUI /prompt response must be a JSON object",
                )
            prompt_id = prompt_response.get("prompt_id")
            if not isinstance(prompt_id, str) or not prompt_id:
                raise ImageProviderError(
                    "IMAGE_PROVIDER_INVALID_RESPONSE",
                    "ComfyUI /prompt response did not contain prompt_id",
                )
        except ImageProviderError:
            raise
        except httpx.TimeoutException as exc:
            raise ImageProviderError("IMAGE_PROVIDER_TIMEOUT", "ComfyUI prompt submission timed out") from exc
        except httpx.HTTPStatusError as exc:
            raise ImageProviderError(
                self._status_error_code(exc.response.status_code),
                f"ComfyUI prompt submission failed with HTTP {exc.response.status_code}",
            ) from exc
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise ImageProviderError("IMAGE_PROVIDER_HTTP_ERROR", "ComfyUI prompt submission failed") from exc

        image_info = await self._wait_for_image(prompt_id, started)
        try:
            response = await self._client.get(f"{self.base_url}/view", params=image_info)
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise ImageProviderError("IMAGE_PROVIDER_TIMEOUT", "ComfyUI image download timed out") from exc
        except httpx.HTTPStatusError as exc:
            raise ImageProviderError(
                self._status_error_code(exc.response.status_code),
                f"ComfyUI image download failed with HTTP {exc.response.status_code}",
            ) from exc
        except httpx.HTTPError as exc:
            raise ImageProviderError("IMAGE_PROVIDER_HTTP_ERROR", "ComfyUI image download failed") from exc

        content = response.content
        if not content:
            raise ImageProviderError("IMAGE_PROVIDER_INVALID_RESPONSE", "ComfyUI returned an empty image")
        mime_type = self._image_mime_type(response.headers.get("content-type"), image_info["filename"])
        return ReferenceImageGenerationResult(
            image_base64=base64.b64encode(content).decode("ascii"),
            mime_type=mime_type,
            provider="comfyui",
            model=self.model,
            width=request.width,
            height=request.height,
            duration_ms=max(1, round((monotonic() - started) * 1000)),
            metadata={
                "workflow_path": str(self.workflow_path),
                "prompt_id": prompt_id,
                "filename": image_info["filename"],
                "subfolder": image_info["subfolder"],
                "seed": seed,
                "steps": self.steps,
                "guidance": self.guidance,
                "identity_weight": self.identity_weight,
                "identity_image": identity_image_info,
                "local": True,
            },
        )

    async def _upload_identity_image(
        self,
        request: ReferenceImageGenerationRequest,
    ) -> dict[str, str]:
        suffix = ".jpg" if request.identity_image_mime_type == "image/jpeg" else ".png"
        filename = f"ai-video-identity-{uuid4().hex}{suffix}"
        try:
            response = await self._client.post(
                f"{self.base_url}/upload/image",
                files={
                    "image": (
                        filename,
                        request.identity_image_bytes,
                        request.identity_image_mime_type or "image/png",
                    )
                },
                data={"overwrite": "true", "type": "input"},
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.TimeoutException as exc:
            raise ImageProviderError("IMAGE_PROVIDER_TIMEOUT", "ComfyUI identity image upload timed out") from exc
        except httpx.HTTPStatusError as exc:
            raise ImageProviderError(
                self._status_error_code(exc.response.status_code),
                f"ComfyUI identity image upload failed with HTTP {exc.response.status_code}",
            ) from exc
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise ImageProviderError("IMAGE_PROVIDER_HTTP_ERROR", "ComfyUI identity image upload failed") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("name"), str):
            raise ImageProviderError(
                "IMAGE_PROVIDER_INVALID_RESPONSE",
                "ComfyUI identity image upload did not return an input image name",
            )
        return {
            "name": payload["name"],
            "subfolder": str(payload.get("subfolder", "")),
            "type": str(payload.get("type", "input")),
        }

    async def _wait_for_image(self, prompt_id: str, started: float) -> dict[str, str]:
        while monotonic() - started <= self.max_poll_seconds:
            try:
                response = await self._client.get(f"{self.base_url}/history/{prompt_id}")
                response.raise_for_status()
                history = response.json()
            except httpx.TimeoutException as exc:
                raise ImageProviderError("IMAGE_PROVIDER_TIMEOUT", "ComfyUI history polling timed out") from exc
            except httpx.HTTPStatusError as exc:
                raise ImageProviderError(
                    self._status_error_code(exc.response.status_code),
                    f"ComfyUI history polling failed with HTTP {exc.response.status_code}",
                ) from exc
            except (httpx.HTTPError, ValueError, TypeError) as exc:
                raise ImageProviderError("IMAGE_PROVIDER_HTTP_ERROR", "ComfyUI history polling failed") from exc

            record = history.get(prompt_id) if isinstance(history, dict) else None
            if isinstance(record, dict):
                status = record.get("status")
                if isinstance(status, dict) and status.get("status_str") in {"error", "failed"}:
                    raise ImageProviderError("IMAGE_PROVIDER_FAILED", "ComfyUI workflow failed")
                image = self._first_image(record.get("outputs"))
                if image is not None:
                    return image
            await _sleep(self.poll_interval_seconds)

        raise ImageProviderError("IMAGE_PROVIDER_TIMEOUT", "ComfyUI image generation timed out")

    def _load_workflow(self) -> dict[str, Any]:
        if not self.workflow_path.is_file():
            raise ImageProviderError(
                "IMAGE_PROVIDER_NOT_CONFIGURED",
                f"ComfyUI workflow file was not found: {self.workflow_path}",
            )
        try:
            workflow = json.loads(self.workflow_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ImageProviderError(
                "IMAGE_WORKFLOW_INVALID",
                "ComfyUI workflow file is not valid UTF-8 JSON",
            ) from exc
        if not isinstance(workflow, dict) or not workflow:
            raise ImageProviderError("IMAGE_WORKFLOW_INVALID", "ComfyUI workflow must be a non-empty object")
        return workflow

    @classmethod
    def _replace_placeholders(cls, workflow: dict[str, Any], values: dict[str, object]) -> dict[str, Any]:
        original = json.dumps(workflow, ensure_ascii=False)
        if "__AI_VIDEO_PROMPT__" not in original:
            raise ImageProviderError(
                "IMAGE_WORKFLOW_INVALID",
                "ComfyUI workflow must expose a prompt placeholder",
            )
        replaced = cls._replace(copy.deepcopy(workflow), values)
        serialized = json.dumps(replaced, ensure_ascii=False)
        if "__AI_VIDEO_" in serialized:
            raise ImageProviderError(
                "IMAGE_WORKFLOW_INVALID",
                "ComfyUI workflow placeholder replacement was incomplete",
            )
        return replaced

    @classmethod
    def _replace(cls, value: Any, replacements: dict[str, object]) -> Any:
        if isinstance(value, dict):
            return {key: cls._replace(item, replacements) for key, item in value.items()}
        if isinstance(value, list):
            return [cls._replace(item, replacements) for item in value]
        if isinstance(value, str):
            if value in replacements:
                return replacements[value]
            result = value
            for marker, replacement in replacements.items():
                result = result.replace(marker, str(replacement))
            return result
        return value

    @staticmethod
    def _first_image(outputs: Any) -> dict[str, str] | None:
        if not isinstance(outputs, dict):
            return None
        for node_output in outputs.values():
            if not isinstance(node_output, dict) or not isinstance(node_output.get("images"), list):
                continue
            for image in node_output["images"]:
                if not isinstance(image, dict):
                    continue
                filename = image.get("filename")
                if isinstance(filename, str) and filename:
                    return {
                        "filename": filename,
                        "subfolder": str(image.get("subfolder", "")),
                        "type": str(image.get("type", "output")),
                    }
        return None

    @staticmethod
    def _seed_for(request: ReferenceImageGenerationRequest) -> int:
        digest = hashlib.sha256(
            f"{request.asset_key}:{request.asset_version}:{request.prompt}".encode("utf-8")
        ).hexdigest()
        return int(digest[:12], 16) % 2_147_483_647

    @staticmethod
    def _image_mime_type(content_type: str | None, filename: str) -> str:
        if content_type and content_type.startswith("image/"):
            return content_type.split(";", 1)[0]
        suffix = Path(filename).suffix.lower()
        return {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}.get(
            suffix, "image/png"
        )

    @staticmethod
    def _status_error_code(status_code: int) -> str:
        if status_code == 429:
            return "IMAGE_PROVIDER_RATE_LIMITED"
        if status_code >= 500:
            return "IMAGE_PROVIDER_UNAVAILABLE"
        return "IMAGE_PROVIDER_HTTP_ERROR"

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()


async def _sleep(seconds: float) -> None:
    import asyncio

    await asyncio.sleep(seconds)
