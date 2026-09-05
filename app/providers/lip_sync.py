"""Provider contract and local subprocess adapter for MuseTalk lip-sync."""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import math
import os
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
from typing import Any, Protocol
from urllib.parse import urljoin, urlsplit

import httpx

from app.domain.models import LipSyncGenerationRequest, LipSyncGenerationResult


class LipSyncProviderError(Exception):
    """Stable errors raised by lip-sync Providers."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class LipSyncProvider(Protocol):
    """Structural interface implemented by MuseTalk adapters."""

    async def generate_lip_sync(
        self,
        request: LipSyncGenerationRequest,
    ) -> LipSyncGenerationResult:
        ...


class MockLipSyncProvider(LipSyncProvider):
    """Passthrough provider for API/Worker contract tests.

    It deliberately does not modify frames or claim that lip movement was
    generated. The service records ``mock_passthrough`` in Artifact metadata so
    a demo cannot be mistaken for a real MuseTalk result.
    """

    async def generate_lip_sync(
        self,
        request: LipSyncGenerationRequest,
    ) -> LipSyncGenerationResult:
        return LipSyncGenerationResult(
            video_base64=base64.b64encode(request.video_bytes).decode("ascii"),
            mime_type=request.video_mime_type,
            provider="mock_lip_sync",
            model="mock-passthrough-v1",
            duration_seconds=1,
            duration_ms=0,
            metadata={
                "local": True,
                "inference": "mock_passthrough",
                "lip_motion_generated": False,
            },
        )

    async def close(self) -> None:
        return None


class SubprocessMuseTalkProvider(LipSyncProvider):
    """Run a user-supplied MuseTalk wrapper in its own Python environment.

    MuseTalk installations differ in model layout and entry points. The
    application therefore owns only this stable CLI boundary; the configured
    wrapper must accept ``--video``, ``--audio`` and ``--output`` plus the
    optional face/model flags documented in ``docs/05-deployment.md``.
    """

    def __init__(
        self,
        runtime_path: str,
        script_path: str,
        model_root: str = "",
        device: str = "cpu",
        model: str = "MuseTalk-local",
        timeout_seconds: int = 900,
    ) -> None:
        self.runtime_path = Path(runtime_path)
        self.script_path = Path(script_path)
        self.model_root = model_root.strip()
        self.device = device.strip() or "cpu"
        self.model = model
        self.timeout_seconds = max(1, timeout_seconds)

    async def generate_lip_sync(
        self,
        request: LipSyncGenerationRequest,
    ) -> LipSyncGenerationResult:
        self._validate_configuration()
        started = monotonic()
        with TemporaryDirectory(prefix="ai-video-musetalk-") as directory:
            root = Path(directory)
            video_path = root / self._video_suffix(request.video_mime_type)
            audio_path = root / self._audio_suffix(request.audio_mime_type)
            output_path = root / "lip-synced.mp4"
            video_path.write_bytes(request.video_bytes)
            audio_path.write_bytes(request.audio_bytes)
            command = [
                str(self.runtime_path),
                str(self.script_path),
                "--video",
                str(video_path),
                "--audio",
                str(audio_path),
                "--output",
                str(output_path),
                "--face-region",
                request.face_region,
                "--face-padding",
                str(request.face_padding),
                "--device",
                self.device,
            ]
            if self.model_root:
                command.extend(["--model-root", self.model_root])
            stdout, stderr = await self._run(command)
            if not output_path.is_file() or output_path.stat().st_size == 0:
                detail = stderr.decode("utf-8", errors="replace").strip() or stdout.decode(
                    "utf-8", errors="replace"
                ).strip()
                suffix = f": {detail[:400]}" if detail else ""
                raise LipSyncProviderError(
                    "LIP_SYNC_INVALID_RESPONSE",
                    f"MuseTalk wrapper did not produce a video file{suffix}",
                )
            content = await asyncio.to_thread(output_path.read_bytes)

        return LipSyncGenerationResult(
            video_base64=base64.b64encode(content).decode("ascii"),
            mime_type="video/mp4",
            provider="musetalk",
            model=self.model,
            duration_seconds=1,
            duration_ms=max(1, round((monotonic() - started) * 1000)),
            metadata={
                "local": True,
                "inference": "musetalk_subprocess",
                "runtime_path": str(self.runtime_path),
                "script_path": str(self.script_path),
                "model_root": self.model_root,
                "device": self.device,
            },
        )

    def _validate_configuration(self) -> None:
        runtime = str(self.runtime_path)
        if not runtime or not self._executable_exists(runtime):
            raise LipSyncProviderError(
                "LIP_SYNC_RUNTIME_NOT_CONFIGURED",
                f"MuseTalk runtime was not found: {self.runtime_path}",
            )
        if not self.script_path.is_file():
            raise LipSyncProviderError(
                "LIP_SYNC_RUNTIME_NOT_CONFIGURED",
                f"MuseTalk wrapper was not found: {self.script_path}",
            )
        if self.model_root and not Path(self.model_root).is_dir():
            raise LipSyncProviderError(
                "LIP_SYNC_MODEL_ROOT_NOT_FOUND",
                f"MuseTalk model root was not found: {self.model_root}",
            )

    async def _run(self, command: list[str]) -> tuple[bytes, bytes]:
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**os.environ, "PYTHONUNBUFFERED": "1"},
            )
        except FileNotFoundError as exc:
            raise LipSyncProviderError(
                "LIP_SYNC_RUNTIME_NOT_CONFIGURED",
                "MuseTalk runtime could not be started",
            ) from exc
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=self.timeout_seconds,
            )
        except TimeoutError as exc:
            process.kill()
            await process.communicate()
            raise LipSyncProviderError(
                "LIP_SYNC_TIMEOUT",
                "MuseTalk inference timed out",
            ) from exc
        if process.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace").strip()
            message = "MuseTalk wrapper failed"
            if detail:
                message = f"{message}: {detail[:500]}"
            raise LipSyncProviderError("LIP_SYNC_FAILED", message)
        return stdout, stderr

    @staticmethod
    def _executable_exists(path: str) -> bool:
        candidate = Path(path)
        return candidate.is_file() if candidate.is_absolute() else shutil.which(path) is not None

    @staticmethod
    def _video_suffix(content_type: str) -> str:
        return "input.webm" if content_type.split(";", 1)[0].lower() == "video/webm" else "input.mp4"

    @staticmethod
    def _audio_suffix(content_type: str) -> str:
        normalized = content_type.split(";", 1)[0].lower()
        return {
            "audio/wav": "input.wav",
            "audio/x-wav": "input.wav",
            "audio/mpeg": "input.mp3",
            "audio/mp3": "input.mp3",
            "audio/ogg": "input.ogg",
            "audio/webm": "input.webm",
        }.get(normalized, "input.audio")

    async def close(self) -> None:
        return None


class HttpMuseTalkProvider(LipSyncProvider):
    """Call a MuseTalk HTTP bridge running beside ComfyUI on Windows.

    The bridge owns the CUDA runtime and model files.  This adapter only sends
    the already stored video/audio bytes and converts the bridge response back
    into the stable application Provider contract.  Keeping the boundary HTTP
    based lets the Docker API/Worker remain portable and prevents a Windows
    Python environment from leaking into application code.
    """

    def __init__(
        self,
        base_url: str,
        create_path: str = "/v1/lip-sync",
        health_path: str = "/healthz",
        api_key: str | None = None,
        model: str = "MuseTalk-local",
        timeout_seconds: int = 900,
        max_download_bytes: int = 524_288_000,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.create_path = create_path
        self.health_path = health_path
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = max(1, timeout_seconds)
        self.max_download_bytes = max(1, max_download_bytes)
        self._client = client or httpx.AsyncClient(timeout=self.timeout_seconds)
        self._owns_client = client is None

    async def generate_lip_sync(
        self,
        request: LipSyncGenerationRequest,
    ) -> LipSyncGenerationResult:
        if not self.base_url:
            raise LipSyncProviderError(
                "LIP_SYNC_INVALID_CONFIGURATION",
                "AI_VIDEO_LIP_SYNC_BASE_URL is required for the HTTP MuseTalk provider",
            )
        started = monotonic()
        headers = self._headers()
        data = {
            "face_region": request.face_region,
            "face_padding": str(request.face_padding),
            "model": self.model,
        }
        files = {
            "video": (
                self._filename(request.video_mime_type, "input.mp4"),
                request.video_bytes,
                request.video_mime_type,
            ),
            "audio": (
                self._filename(request.audio_mime_type, "input.wav"),
                request.audio_bytes,
                request.audio_mime_type,
            ),
        }
        try:
            response = await self._client.post(
                self._build_url(self.create_path),
                headers=headers,
                data=data,
                files=files,
            )
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise LipSyncProviderError("LIP_SYNC_TIMEOUT", "MuseTalk HTTP request timed out") from exc
        except httpx.HTTPStatusError as exc:
            raise LipSyncProviderError(
                self._status_error_code(exc.response.status_code),
                f"MuseTalk HTTP request failed with HTTP {exc.response.status_code}",
            ) from exc
        except httpx.HTTPError as exc:
            raise LipSyncProviderError("LIP_SYNC_HTTP_ERROR", "MuseTalk HTTP request failed") from exc

        try:
            payload = response.json()
            if not isinstance(payload, dict):
                raise TypeError("response must be an object")
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise LipSyncProviderError(
                "LIP_SYNC_INVALID_RESPONSE",
                "MuseTalk HTTP bridge returned invalid JSON",
            ) from exc

        parsed = self._parse_payload(payload)
        output = self._extract_output(parsed)
        if output is None:
            raise LipSyncProviderError(
                "LIP_SYNC_INVALID_RESPONSE",
                "MuseTalk HTTP bridge returned no video output",
            )
        video_base64, mime_type = await self._resolve_output(output, headers)
        self._validate_base64_size(video_base64)
        duration_seconds, duration_ms = self._reported_duration(parsed, started)
        return LipSyncGenerationResult(
            video_base64=video_base64,
            mime_type=mime_type,
            provider=str(parsed.get("provider") or "musetalk_http"),
            model=str(parsed.get("model") or self.model),
            duration_seconds=duration_seconds,
            duration_ms=duration_ms,
            metadata={
                "local": False,
                "inference": "musetalk_http",
                "bridge_url": self.base_url,
                "remote_request_id": self._extract_id(parsed),
            },
        )

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            return {}
        return {"Authorization": f"Bearer {self.api_key}"}

    def _build_url(self, path_or_url: str) -> str:
        if path_or_url.startswith(("http://", "https://")):
            return path_or_url
        return urljoin(f"{self.base_url}/", path_or_url.lstrip("/"))

    def _build_output_url(self, path_or_url: str) -> str:
        """Resolve bridge output links without changing the create URL prefix."""

        if path_or_url.startswith(("http://", "https://")):
            return path_or_url
        if path_or_url.startswith("/"):
            parsed = urlsplit(self.base_url)
            if parsed.scheme and parsed.netloc:
                return f"{parsed.scheme}://{parsed.netloc}{path_or_url}"
        return self._build_url(path_or_url)

    @staticmethod
    def _filename(content_type: str, fallback: str) -> str:
        normalized = content_type.split(";", 1)[0].lower()
        suffix = {
            "video/webm": ".webm",
            "audio/mpeg": ".mp3",
            "audio/mp3": ".mp3",
            "audio/ogg": ".ogg",
            "audio/webm": ".webm",
        }.get(normalized)
        if suffix is None:
            return fallback
        return fallback.rsplit(".", 1)[0] + suffix

    @classmethod
    def _parse_payload(cls, payload: dict[str, Any]) -> dict[str, Any]:
        data = payload.get("data")
        if isinstance(data, dict):
            return {**payload, **data}
        result = payload.get("result")
        if isinstance(result, dict):
            return {**payload, **result}
        return payload

    @classmethod
    def _extract_output(cls, payload: dict[str, Any]) -> dict[str, Any] | None:
        for key in ("video", "output", "result"):
            value = payload.get(key)
            if isinstance(value, dict) and (
                cls._extract_uri(value) or cls._extract_base64(value)
            ):
                return value
        if cls._extract_uri(payload) or cls._extract_base64(payload):
            return payload
        return None

    @staticmethod
    def _extract_uri(payload: dict[str, Any]) -> str | None:
        for key in ("output_uri", "output_url", "video_url", "download_url", "url"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value
        return None

    @staticmethod
    def _extract_base64(payload: dict[str, Any]) -> str | None:
        for key in ("video_base64", "b64_json", "base64", "b64"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value
        return None

    @staticmethod
    def _extract_mime(payload: dict[str, Any]) -> str:
        for key in ("mime_type", "mimeType", "content_type", "contentType"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value.split(";", 1)[0]
        return "video/mp4"

    async def _resolve_output(
        self,
        output: dict[str, Any],
        headers: dict[str, str],
    ) -> tuple[str, str]:
        encoded = self._extract_base64(output)
        mime_type = self._extract_mime(output)
        uri = self._extract_uri(output)
        if encoded:
            if uri and uri.startswith("data:"):
                mime_type, encoded = self._decode_data_uri(uri)
            return encoded, mime_type
        if not uri:
            raise LipSyncProviderError(
                "LIP_SYNC_INVALID_RESPONSE",
                "MuseTalk HTTP bridge returned an empty video reference",
            )
        if uri.startswith("data:"):
            return self._decode_data_uri(uri)
        try:
            response = await self._client.get(self._build_output_url(uri), headers=headers)
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise LipSyncProviderError("LIP_SYNC_TIMEOUT", "MuseTalk video download timed out") from exc
        except httpx.HTTPStatusError as exc:
            raise LipSyncProviderError(
                self._status_error_code(exc.response.status_code),
                f"MuseTalk video download failed with HTTP {exc.response.status_code}",
            ) from exc
        except httpx.HTTPError as exc:
            raise LipSyncProviderError("LIP_SYNC_HTTP_ERROR", "MuseTalk video download failed") from exc
        if len(response.content) > self.max_download_bytes:
            raise LipSyncProviderError(
                "LIP_SYNC_OUTPUT_TOO_LARGE",
                "MuseTalk output exceeds the configured download limit",
            )
        if not response.content:
            raise LipSyncProviderError("LIP_SYNC_INVALID_RESPONSE", "MuseTalk returned an empty video")
        content_type = response.headers.get("content-type", mime_type).split(";", 1)[0]
        return base64.b64encode(response.content).decode("ascii"), content_type

    @staticmethod
    def _decode_data_uri(data_uri: str) -> tuple[str, str]:
        header, separator, encoded = data_uri.partition(",")
        if not separator or ";base64" not in header:
            raise LipSyncProviderError(
                "LIP_SYNC_INVALID_RESPONSE",
                "MuseTalk returned an unsupported data URI",
            )
        mime_type = header[5:].split(";", 1)[0] or "video/mp4"
        try:
            base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise LipSyncProviderError(
                "LIP_SYNC_INVALID_RESPONSE",
                "MuseTalk returned invalid base64 video content",
            ) from exc
        return encoded, mime_type

    def _validate_base64_size(self, encoded: str) -> None:
        try:
            decoded_size = len(base64.b64decode(encoded, validate=True))
        except (ValueError, binascii.Error) as exc:
            raise LipSyncProviderError(
                "LIP_SYNC_INVALID_RESPONSE",
                "MuseTalk returned invalid base64 video content",
            ) from exc
        if decoded_size > self.max_download_bytes:
            raise LipSyncProviderError(
                "LIP_SYNC_OUTPUT_TOO_LARGE",
                "MuseTalk output exceeds the configured download limit",
            )

    @staticmethod
    def _reported_duration(
        payload: dict[str, Any],
        started: float,
    ) -> tuple[int, int]:
        """Normalize bridge duration fields without using face padding as time."""

        seconds = HttpMuseTalkProvider._positive_number(payload.get("duration_seconds"))
        milliseconds = HttpMuseTalkProvider._positive_number(payload.get("duration_ms"))
        if seconds is not None:
            return max(1, round(seconds)), max(1, round(milliseconds or seconds * 1000))
        if milliseconds is not None:
            return max(1, round(milliseconds / 1000)), max(1, round(milliseconds))
        return 1, max(1, round((monotonic() - started) * 1000))

    @staticmethod
    def _positive_number(value: object) -> float | None:
        if isinstance(value, bool):
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(number) or number <= 0:
            return None
        return number

    @staticmethod
    def _extract_id(payload: dict[str, Any]) -> str | None:
        for key in ("id", "request_id", "task_id"):
            value = payload.get(key)
            if isinstance(value, (str, int)) and str(value):
                return str(value)
        return None

    @staticmethod
    def _status_error_code(status_code: int) -> str:
        if status_code in {401, 403}:
            return "LIP_SYNC_AUTH_FAILED"
        if status_code == 429:
            return "LIP_SYNC_RATE_LIMITED"
        if status_code >= 500:
            return "LIP_SYNC_UNAVAILABLE"
        return "LIP_SYNC_HTTP_ERROR"
