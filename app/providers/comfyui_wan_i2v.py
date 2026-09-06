"""ComfyUI Wan2.1 image-to-video adapter for the local target profile."""

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

from app.domain.models import VideoClipGenerationRequest, VideoClipGenerationResult
from app.providers.errors_video import VideoProviderError


class ComfyUIWanI2VVideoGenerationProvider:
    """Submit a ComfyUI API workflow and download a generated MP4/WebM.

    The workflow is intentionally external. It must contain the target Wan2.1
    nodes and expose the ``__AI_VIDEO_*__`` placeholders documented in the
    project deployment guide. This keeps ComfyUI custom-node details outside
    the task service and makes the 16GB Mac profile replaceable.
    """

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8188",
        workflow_path: str = "config/comfyui/wan2.1-i2v-api.json",
        model: str = "wan2.1-i2v-14b-480p-Q4_K_M.gguf",
        timeout_seconds: int = 1800,
        poll_interval_seconds: float = 2.0,
        max_poll_seconds: int = 1800,
        output_width: int = 480,
        output_height: int = 832,
        fps: int = 16,
        steps: int = 4,
        cfg: float = 5.0,
        noise_aug_strength: float = 0.02,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.workflow_path = Path(workflow_path)
        self.model = model
        self.timeout_seconds = max(1, timeout_seconds)
        self.poll_interval_seconds = max(0.2, poll_interval_seconds)
        self.max_poll_seconds = max(1, max_poll_seconds)
        self.output_width = max(64, output_width // 8 * 8)
        self.output_height = max(64, output_height // 8 * 8)
        self.fps = max(1, fps)
        self.steps = max(1, min(50, steps))
        self.cfg = max(0.0, min(20.0, cfg))
        self.noise_aug_strength = max(0.0, min(1.0, noise_aug_strength))
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)
        self._owns_client = client is None

    async def generate_video_clip(
        self,
        request: VideoClipGenerationRequest,
    ) -> VideoClipGenerationResult:
        if not request.keyframe_bytes:
            raise VideoProviderError(
                "VIDEO_KEYFRAME_REQUIRED",
                "Wan2.1 I2V requires a reference image Artifact",
            )

        workflow = self._load_workflow()
        started = monotonic()
        image_info = await self._upload_keyframe(request)
        output_width = self._aligned_size(request.width or self.output_width)
        output_height = self._aligned_size(request.height or self.output_height)
        fps = request.fps if request.fps is not None else self.fps
        steps = request.steps if request.steps is not None else self.steps
        cfg = request.cfg if request.cfg is not None else self.cfg
        noise_aug_strength = (
            request.noise_aug_strength
            if request.noise_aug_strength is not None
            else self.noise_aug_strength
        )
        frames = max(17, int(request.duration_seconds * fps) + 1)
        seed = self._seed_for(request)
        replacements = {
            "__AI_VIDEO_PROMPT__": request.prompt,
            "__AI_VIDEO_NEGATIVE_PROMPT__": request.negative_prompt,
            "__AI_VIDEO_WIDTH__": output_width,
            "__AI_VIDEO_HEIGHT__": output_height,
            "__AI_VIDEO_FRAMES__": frames,
            "__AI_VIDEO_FPS__": fps,
            "__AI_VIDEO_STEPS__": steps,
            "__AI_VIDEO_CFG__": cfg,
            "__AI_VIDEO_NOISE_AUG_STRENGTH__": noise_aug_strength,
            "__AI_VIDEO_SEED__": seed,
            "__AI_VIDEO_MODEL__": self.model,
            "__AI_VIDEO_INPUT_IMAGE__": image_info["name"],
            "__AI_VIDEO_INPUT_SUBFOLDER__": image_info["subfolder"],
        }
        rendered = self._replace_placeholders(workflow, replacements)
        prompt_id = await self._submit(rendered)
        video_info = await self._wait_for_video(prompt_id, started)
        content = await self._download(video_info)
        mime_type = self._mime_type(video_info["filename"])
        return VideoClipGenerationResult(
            video_base64=base64.b64encode(content).decode("ascii"),
            mime_type=mime_type,
            provider="comfyui_wan_i2v",
            model=self.model,
            duration_seconds=request.duration_seconds,
            duration_ms=max(1, round((monotonic() - started) * 1000)),
            metadata={
                "local": True,
                "workflow_path": str(self.workflow_path),
                "prompt_id": prompt_id,
                "input_image": image_info,
                "frames": frames,
                "fps": fps,
                "steps": steps,
                "cfg": cfg,
                "noise_aug_strength": noise_aug_strength,
                "width": output_width,
                "height": output_height,
                "motion": "wan2.1_i2v",
            },
        )

    async def _upload_keyframe(self, request: VideoClipGenerationRequest) -> dict[str, str]:
        suffix = ".jpg" if request.keyframe_mime_type == "image/jpeg" else ".png"
        filename = f"ai-video-i2v-{uuid4().hex}{suffix}"
        try:
            response = await self._client.post(
                f"{self.base_url}/upload/image",
                files={
                    "image": (
                        filename,
                        request.keyframe_bytes,
                        request.keyframe_mime_type or "image/png",
                    )
                },
                data={"overwrite": "true", "type": "input"},
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.TimeoutException as exc:
            raise VideoProviderError("VIDEO_PROVIDER_TIMEOUT", "ComfyUI keyframe upload timed out") from exc
        except httpx.HTTPStatusError as exc:
            raise VideoProviderError(
                self._status_error_code(exc.response.status_code),
                f"ComfyUI keyframe upload failed with HTTP {exc.response.status_code}",
            ) from exc
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise VideoProviderError("VIDEO_PROVIDER_HTTP_ERROR", "ComfyUI keyframe upload failed") from exc

        if not isinstance(payload, dict) or not isinstance(payload.get("name"), str):
            raise VideoProviderError(
                "VIDEO_PROVIDER_INVALID_RESPONSE",
                "ComfyUI keyframe upload did not return an input image name",
            )
        return {
            "name": payload["name"],
            "subfolder": str(payload.get("subfolder", "")),
            "type": str(payload.get("type", "input")),
        }

    async def _submit(self, workflow: dict[str, Any]) -> str:
        try:
            response = await self._client.post(
                f"{self.base_url}/prompt",
                json={"prompt": workflow, "client_id": str(uuid4())},
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.TimeoutException as exc:
            raise VideoProviderError("VIDEO_PROVIDER_TIMEOUT", "ComfyUI video submission timed out") from exc
        except httpx.HTTPStatusError as exc:
            raise VideoProviderError(
                self._status_error_code(exc.response.status_code),
                f"ComfyUI video submission failed with HTTP {exc.response.status_code}",
            ) from exc
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise VideoProviderError("VIDEO_PROVIDER_HTTP_ERROR", "ComfyUI video submission failed") from exc
        prompt_id = payload.get("prompt_id") if isinstance(payload, dict) else None
        if not isinstance(prompt_id, str) or not prompt_id:
            raise VideoProviderError(
                "VIDEO_PROVIDER_INVALID_RESPONSE",
                "ComfyUI video submission did not return prompt_id",
            )
        return prompt_id

    async def _wait_for_video(self, prompt_id: str, started: float) -> dict[str, str]:
        while monotonic() - started <= self.max_poll_seconds:
            try:
                response = await self._client.get(f"{self.base_url}/history/{prompt_id}")
                response.raise_for_status()
                history = response.json()
            except httpx.TimeoutException as exc:
                raise VideoProviderError("VIDEO_PROVIDER_TIMEOUT", "ComfyUI video polling timed out") from exc
            except httpx.HTTPStatusError as exc:
                raise VideoProviderError(
                    self._status_error_code(exc.response.status_code),
                    f"ComfyUI video polling failed with HTTP {exc.response.status_code}",
                ) from exc
            except (httpx.HTTPError, ValueError, TypeError) as exc:
                raise VideoProviderError("VIDEO_PROVIDER_HTTP_ERROR", "ComfyUI video polling failed") from exc

            record = history.get(prompt_id) if isinstance(history, dict) else None
            if isinstance(record, dict):
                status = record.get("status")
                if isinstance(status, dict) and status.get("status_str") in {"error", "failed"}:
                    raise VideoProviderError("VIDEO_PROVIDER_FAILED", "ComfyUI Wan workflow failed")
                video = self._first_video(record.get("outputs"))
                if video is not None:
                    return video
            await _sleep(self.poll_interval_seconds)

        # A long Wan run can finish between the last scheduled poll and the
        # deadline check. Give ComfyUI one final history read before mapping
        # the run to a timeout, so a completed MP4 is not discarded.
        try:
            response = await self._client.get(f"{self.base_url}/history/{prompt_id}")
            response.raise_for_status()
            history = response.json()
        except httpx.TimeoutException as exc:
            raise VideoProviderError("VIDEO_PROVIDER_TIMEOUT", "ComfyUI video polling timed out") from exc
        except httpx.HTTPStatusError as exc:
            raise VideoProviderError(
                self._status_error_code(exc.response.status_code),
                f"ComfyUI video polling failed with HTTP {exc.response.status_code}",
            ) from exc
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise VideoProviderError("VIDEO_PROVIDER_HTTP_ERROR", "ComfyUI video polling failed") from exc

        record = history.get(prompt_id) if isinstance(history, dict) else None
        if isinstance(record, dict):
            status = record.get("status")
            if isinstance(status, dict) and status.get("status_str") in {"error", "failed"}:
                raise VideoProviderError("VIDEO_PROVIDER_FAILED", "ComfyUI Wan workflow failed")
            video = self._first_video(record.get("outputs"))
            if video is not None:
                return video
        raise VideoProviderError("VIDEO_PROVIDER_TIMEOUT", "ComfyUI Wan workflow timed out")

    async def _download(self, video_info: dict[str, str]) -> bytes:
        try:
            response = await self._client.get(f"{self.base_url}/view", params=video_info)
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise VideoProviderError("VIDEO_PROVIDER_TIMEOUT", "ComfyUI video download timed out") from exc
        except httpx.HTTPStatusError as exc:
            raise VideoProviderError(
                self._status_error_code(exc.response.status_code),
                f"ComfyUI video download failed with HTTP {exc.response.status_code}",
            ) from exc
        except httpx.HTTPError as exc:
            raise VideoProviderError("VIDEO_PROVIDER_HTTP_ERROR", "ComfyUI video download failed") from exc
        if not response.content:
            raise VideoProviderError("VIDEO_PROVIDER_INVALID_RESPONSE", "ComfyUI returned an empty video")
        return response.content

    def _load_workflow(self) -> dict[str, Any]:
        if not self.workflow_path.is_file():
            raise VideoProviderError(
                "VIDEO_PROVIDER_NOT_CONFIGURED",
                f"Wan2.1 workflow file was not found: {self.workflow_path}",
            )
        try:
            workflow = json.loads(self.workflow_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise VideoProviderError("VIDEO_WORKFLOW_INVALID", "Wan2.1 workflow is not valid JSON") from exc
        if not isinstance(workflow, dict) or not workflow:
            raise VideoProviderError("VIDEO_WORKFLOW_INVALID", "Wan2.1 workflow must be a non-empty object")
        serialized = json.dumps(workflow, ensure_ascii=False)
        required = ("__AI_VIDEO_PROMPT__", "__AI_VIDEO_INPUT_IMAGE__")
        if any(marker not in serialized for marker in required):
            raise VideoProviderError(
                "VIDEO_WORKFLOW_INVALID",
                "Wan2.1 workflow must expose prompt and input-image placeholders",
            )
        return workflow

    @classmethod
    def _replace_placeholders(cls, workflow: dict[str, Any], values: dict[str, object]) -> dict[str, Any]:
        replaced = cls._replace(copy.deepcopy(workflow), values)
        serialized = json.dumps(replaced, ensure_ascii=False)
        if "__AI_VIDEO_" in serialized:
            raise VideoProviderError("VIDEO_WORKFLOW_INVALID", "Wan2.1 workflow placeholder replacement was incomplete")
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
    def _first_video(outputs: Any) -> dict[str, str] | None:
        if not isinstance(outputs, dict):
            return None
        for node_output in outputs.values():
            if not isinstance(node_output, dict):
                continue
            for key in ("gifs", "videos", "files"):
                items = node_output.get(key)
                if not isinstance(items, list):
                    continue
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    filename = item.get("filename")
                    if isinstance(filename, str) and filename.lower().endswith((".mp4", ".webm", ".mov", ".gif")):
                        return {
                            "filename": filename,
                            "subfolder": str(item.get("subfolder", "")),
                            "type": str(item.get("type", "output")),
                        }
        return None

    @staticmethod
    def _seed_for(request: VideoClipGenerationRequest) -> int:
        digest = hashlib.sha256(
            f"{request.episode_id}:{request.shot_list_id}:{request.shot_index}:"
            f"{request.prompt}:{request.generation_attempt}".encode()
        ).hexdigest()
        return int(digest[:12], 16) % 2_147_483_647

    @staticmethod
    def _aligned_size(value: int) -> int:
        return max(64, int(value) // 8 * 8)

    @staticmethod
    def _mime_type(filename: str) -> str:
        suffix = Path(filename).suffix.lower()
        return {".webm": "video/webm", ".mov": "video/quicktime", ".gif": "image/gif"}.get(
            suffix, "video/mp4"
        )

    @staticmethod
    def _status_error_code(status_code: int) -> str:
        if status_code == 429:
            return "VIDEO_PROVIDER_RATE_LIMITED"
        if status_code >= 500:
            return "VIDEO_PROVIDER_UNAVAILABLE"
        return "VIDEO_PROVIDER_HTTP_ERROR"

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()


async def _sleep(seconds: float) -> None:
    import asyncio

    await asyncio.sleep(seconds)
