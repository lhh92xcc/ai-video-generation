"""FFprobe-backed validation for generated reference-image artifacts."""

from __future__ import annotations

import asyncio
import json
import shutil
from dataclasses import dataclass
from math import gcd
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Protocol


class ImageArtifactValidationError(Exception):
    """Raised when a generated image cannot be used as a reference artifact."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class ImageProbeResult:
    width: int
    height: int
    codec_name: str
    format_name: str

    @property
    def aspect_ratio(self) -> str:
        divisor = gcd(self.width, self.height)
        return f"{self.width // divisor}:{self.height // divisor}"

    def as_metadata(self) -> dict[str, object]:
        return {
            "width": self.width,
            "height": self.height,
            "codec_name": self.codec_name,
            "format_name": self.format_name,
            "aspect_ratio": self.aspect_ratio,
        }


class ImageArtifactValidator(Protocol):
    async def validate_bytes(self, content: bytes, content_type: str) -> ImageProbeResult:
        ...


class FFprobeImageValidator:
    """Validate that image bytes are readable and contain positive dimensions."""

    def __init__(self, binary: str = "ffprobe", timeout_seconds: int = 30) -> None:
        self.binary = binary
        self.timeout_seconds = max(1, timeout_seconds)

    async def validate_bytes(
        self,
        content: bytes,
        content_type: str,
    ) -> ImageProbeResult:
        if not content:
            raise ImageArtifactValidationError(
                "IMAGE_ARTIFACT_EMPTY",
                "Reference image artifact is empty",
            )
        if not content_type.startswith("image/"):
            raise ImageArtifactValidationError(
                "IMAGE_ARTIFACT_INVALID_MIME",
                "Reference image artifact has a non-image MIME type",
            )
        if shutil.which(self.binary) is None:
            raise ImageArtifactValidationError(
                "IMAGE_FFPROBE_UNAVAILABLE",
                f"{self.binary} is required to validate reference images",
            )

        suffix = {
            "image/jpeg": ".jpg",
            "image/webp": ".webp",
            "image/gif": ".gif",
        }.get(content_type.split(";", 1)[0].lower(), ".png")
        temporary_path: Path | None = None
        try:
            with NamedTemporaryFile(prefix="ai-video-image-probe-", suffix=suffix, delete=False) as file:
                temporary_path = Path(file.name)
            await asyncio.to_thread(temporary_path.write_bytes, content)
            return await self._probe_path(temporary_path)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    async def _probe_path(self, path: Path) -> ImageProbeResult:
        command = [
            self.binary,
            "-v",
            "error",
            "-print_format",
            "json",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name,width,height",
            "-show_format",
            str(path),
        ]
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise ImageArtifactValidationError(
                "IMAGE_FFPROBE_UNAVAILABLE",
                f"{self.binary} is required to validate reference images",
            ) from exc

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=self.timeout_seconds,
            )
        except TimeoutError as exc:
            process.kill()
            await process.communicate()
            raise ImageArtifactValidationError(
                "IMAGE_FFPROBE_TIMEOUT",
                "ffprobe timed out while validating the reference image",
            ) from exc

        if process.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace").strip()
            message = "ffprobe could not read the reference image"
            if detail:
                message = f"{message}: {detail[:500]}"
            raise ImageArtifactValidationError("IMAGE_ARTIFACT_NOT_READABLE", message)

        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise ImageArtifactValidationError(
                "IMAGE_FFPROBE_INVALID_OUTPUT",
                "ffprobe returned invalid JSON for the reference image",
            ) from exc
        return self._parse_probe_payload(payload)

    @classmethod
    def _parse_probe_payload(cls, payload: Any) -> ImageProbeResult:
        if not isinstance(payload, dict):
            raise ImageArtifactValidationError(
                "IMAGE_FFPROBE_INVALID_OUTPUT",
                "ffprobe returned a non-object payload for the reference image",
            )
        streams = payload.get("streams")
        stream = streams[0] if isinstance(streams, list) and streams else None
        if not isinstance(stream, dict):
            raise ImageArtifactValidationError(
                "IMAGE_ARTIFACT_NOT_READABLE",
                "Reference image has no readable video stream",
            )
        width = cls._positive_int(stream.get("width"))
        height = cls._positive_int(stream.get("height"))
        codec_name = stream.get("codec_name")
        if width <= 0 or height <= 0 or not isinstance(codec_name, str) or not codec_name:
            raise ImageArtifactValidationError(
                "IMAGE_ARTIFACT_NOT_READABLE",
                "Reference image is missing codec or dimension metadata",
            )
        format_data = payload.get("format")
        format_name = "unknown"
        if isinstance(format_data, dict) and isinstance(format_data.get("format_name"), str):
            format_name = format_data["format_name"]
        return ImageProbeResult(
            width=width,
            height=height,
            codec_name=codec_name,
            format_name=format_name,
        )

    @staticmethod
    def _positive_int(value: Any) -> int:
        try:
            number = int(value)
        except (TypeError, ValueError):
            return 0
        return number if number > 0 else 0
