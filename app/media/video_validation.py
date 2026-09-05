"""FFprobe-backed validation for generated video artifacts."""

from __future__ import annotations

import asyncio
import json
import math
import shutil
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Protocol

from app.providers.errors_video import VideoArtifactValidationError


@dataclass(frozen=True, slots=True)
class VideoProbeResult:
    duration_seconds: float
    width: int
    height: int
    codec_name: str
    format_name: str
    video_stream_count: int
    audio_stream_count: int

    def as_metadata(self) -> dict[str, object]:
        return {
            "duration_seconds": self.duration_seconds,
            "width": self.width,
            "height": self.height,
            "codec_name": self.codec_name,
            "format_name": self.format_name,
            "video_stream_count": self.video_stream_count,
            "audio_stream_count": self.audio_stream_count,
        }


class VideoArtifactValidator(Protocol):
    async def validate_bytes(self, content: bytes, content_type: str) -> VideoProbeResult:
        ...


class FFprobeVideoValidator:
    """Validate that bytes contain a non-empty video stream readable by ffprobe."""

    def __init__(self, binary: str = "ffprobe", timeout_seconds: int = 30) -> None:
        self.binary = binary
        self.timeout_seconds = max(1, timeout_seconds)

    async def validate_bytes(
        self,
        content: bytes,
        content_type: str,
    ) -> VideoProbeResult:
        if not content:
            raise VideoArtifactValidationError(
                "VIDEO_ARTIFACT_NOT_PLAYABLE",
                "Video artifact is empty",
            )
        if not content_type.startswith("video/"):
            raise VideoArtifactValidationError(
                "VIDEO_ARTIFACT_INVALID_MIME",
                "Video artifact has a non-video MIME type",
            )
        if shutil.which(self.binary) is None:
            raise VideoArtifactValidationError(
                "VIDEO_FFPROBE_UNAVAILABLE",
                f"{self.binary} is required to validate video artifacts",
            )

        suffix = ".webm" if content_type == "video/webm" else ".mp4"
        temporary_path: Path | None = None
        try:
            with NamedTemporaryFile(prefix="ai-video-probe-", suffix=suffix, delete=False) as file:
                temporary_path = Path(file.name)
            await asyncio.to_thread(temporary_path.write_bytes, content)
            return await self._probe_path(temporary_path)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    async def _probe_path(self, path: Path) -> VideoProbeResult:
        command = [
            self.binary,
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ]
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise VideoArtifactValidationError(
                "VIDEO_FFPROBE_UNAVAILABLE",
                f"{self.binary} is required to validate video artifacts",
            ) from exc

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=self.timeout_seconds,
            )
        except TimeoutError as exc:
            process.kill()
            await process.communicate()
            raise VideoArtifactValidationError(
                "VIDEO_FFPROBE_TIMEOUT",
                "ffprobe timed out while validating the video artifact",
            ) from exc

        if process.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace").strip()
            message = "ffprobe could not read the video artifact"
            if detail:
                message = f"{message}: {detail[:500]}"
            raise VideoArtifactValidationError("VIDEO_ARTIFACT_NOT_PLAYABLE", message)

        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise VideoArtifactValidationError(
                "VIDEO_FFPROBE_INVALID_OUTPUT",
                "ffprobe returned invalid JSON",
            ) from exc
        return self._parse_probe_payload(payload)

    @classmethod
    def _parse_probe_payload(cls, payload: Any) -> VideoProbeResult:
        if not isinstance(payload, dict):
            raise VideoArtifactValidationError(
                "VIDEO_FFPROBE_INVALID_OUTPUT",
                "ffprobe returned a non-object payload",
            )
        streams = payload.get("streams")
        if not isinstance(streams, list):
            streams = []
        video_streams = [stream for stream in streams if cls._is_video_stream(stream)]
        audio_streams = [stream for stream in streams if cls._is_audio_stream(stream)]
        if not video_streams:
            raise VideoArtifactValidationError(
                "VIDEO_ARTIFACT_NOT_PLAYABLE",
                "Video artifact has no video stream",
            )

        stream = video_streams[0]
        duration = cls._duration_seconds(payload.get("format"), stream)
        width = cls._positive_int(stream.get("width"))
        height = cls._positive_int(stream.get("height"))
        codec_name = stream.get("codec_name")
        if duration <= 0 or width <= 0 or height <= 0 or not isinstance(codec_name, str):
            raise VideoArtifactValidationError(
                "VIDEO_ARTIFACT_NOT_PLAYABLE",
                "Video artifact is missing duration, dimensions or codec metadata",
            )

        format_data = payload.get("format")
        format_name = "unknown"
        if isinstance(format_data, dict) and isinstance(format_data.get("format_name"), str):
            format_name = format_data["format_name"]
        return VideoProbeResult(
            duration_seconds=duration,
            width=width,
            height=height,
            codec_name=codec_name,
            format_name=format_name,
            video_stream_count=len(video_streams),
            audio_stream_count=len(audio_streams),
        )

    @staticmethod
    def _is_video_stream(stream: Any) -> bool:
        return isinstance(stream, dict) and stream.get("codec_type") == "video"

    @staticmethod
    def _is_audio_stream(stream: Any) -> bool:
        return isinstance(stream, dict) and stream.get("codec_type") == "audio"

    @staticmethod
    def _positive_int(value: Any) -> int:
        try:
            number = int(value)
        except (TypeError, ValueError):
            return 0
        return number if number > 0 else 0

    @staticmethod
    def _duration_seconds(format_data: Any, stream: dict[str, Any]) -> float:
        candidates: list[Any] = []
        if isinstance(format_data, dict):
            candidates.append(format_data.get("duration"))
        candidates.append(stream.get("duration"))
        for candidate in candidates:
            try:
                duration = float(candidate)
            except (TypeError, ValueError):
                continue
            if math.isfinite(duration) and duration > 0:
                return duration
        return 0.0
