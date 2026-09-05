"""FFprobe-backed validation for generated audio artifacts."""

from __future__ import annotations

import asyncio
import json
import math
import shutil
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Protocol

from app.providers.errors_audio import AudioArtifactValidationError


@dataclass(frozen=True, slots=True)
class AudioProbeResult:
    duration_seconds: float
    codec_name: str
    format_name: str
    sample_rate: int
    channel_count: int
    audio_stream_count: int

    def as_metadata(self) -> dict[str, object]:
        return {
            "duration_seconds": self.duration_seconds,
            "codec_name": self.codec_name,
            "format_name": self.format_name,
            "sample_rate": self.sample_rate,
            "channel_count": self.channel_count,
            "audio_stream_count": self.audio_stream_count,
        }


class AudioArtifactValidator(Protocol):
    async def validate_bytes(self, content: bytes, content_type: str) -> AudioProbeResult:
        ...


class FFprobeAudioValidator:
    """Validate that bytes contain a non-empty audio stream readable by ffprobe."""

    def __init__(self, binary: str = "ffprobe", timeout_seconds: int = 30) -> None:
        self.binary = binary
        self.timeout_seconds = max(1, timeout_seconds)

    async def validate_bytes(self, content: bytes, content_type: str) -> AudioProbeResult:
        if not content:
            raise AudioArtifactValidationError(
                "AUDIO_ARTIFACT_NOT_PLAYABLE",
                "Audio artifact is empty",
            )
        if not content_type.startswith("audio/"):
            raise AudioArtifactValidationError(
                "AUDIO_ARTIFACT_INVALID_MIME",
                "Audio artifact has a non-audio MIME type",
            )
        if shutil.which(self.binary) is None:
            raise AudioArtifactValidationError(
                "AUDIO_FFPROBE_UNAVAILABLE",
                f"{self.binary} is required to validate audio artifacts",
            )

        suffix = self._suffix_for_mime(content_type)
        temporary_path: Path | None = None
        try:
            with NamedTemporaryFile(prefix="ai-audio-probe-", suffix=suffix, delete=False) as file:
                temporary_path = Path(file.name)
            await asyncio.to_thread(temporary_path.write_bytes, content)
            return await self._probe_path(temporary_path)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    async def _probe_path(self, path: Path) -> AudioProbeResult:
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
            raise AudioArtifactValidationError(
                "AUDIO_FFPROBE_UNAVAILABLE",
                f"{self.binary} is required to validate audio artifacts",
            ) from exc

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=self.timeout_seconds,
            )
        except TimeoutError as exc:
            process.kill()
            await process.communicate()
            raise AudioArtifactValidationError(
                "AUDIO_FFPROBE_TIMEOUT",
                "ffprobe timed out while validating the audio artifact",
            ) from exc

        if process.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace").strip()
            message = "ffprobe could not read the audio artifact"
            if detail:
                message = f"{message}: {detail[:500]}"
            raise AudioArtifactValidationError("AUDIO_ARTIFACT_NOT_PLAYABLE", message)

        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise AudioArtifactValidationError(
                "AUDIO_FFPROBE_INVALID_OUTPUT",
                "ffprobe returned invalid JSON",
            ) from exc
        return self._parse_probe_payload(payload)

    @classmethod
    def _parse_probe_payload(cls, payload: Any) -> AudioProbeResult:
        if not isinstance(payload, dict):
            raise AudioArtifactValidationError(
                "AUDIO_FFPROBE_INVALID_OUTPUT",
                "ffprobe returned a non-object payload",
            )
        streams = payload.get("streams")
        if not isinstance(streams, list):
            streams = []
        audio_streams = [stream for stream in streams if cls._is_audio_stream(stream)]
        if not audio_streams:
            raise AudioArtifactValidationError(
                "AUDIO_ARTIFACT_NOT_PLAYABLE",
                "Audio artifact has no audio stream",
            )

        stream = audio_streams[0]
        duration = cls._duration_seconds(payload.get("format"), stream)
        codec_name = stream.get("codec_name")
        sample_rate = cls._positive_int(stream.get("sample_rate"))
        channel_count = cls._positive_int(stream.get("channels"))
        if duration <= 0 or not isinstance(codec_name, str) or sample_rate <= 0 or channel_count <= 0:
            raise AudioArtifactValidationError(
                "AUDIO_ARTIFACT_NOT_PLAYABLE",
                "Audio artifact is missing duration, codec, sample rate or channel metadata",
            )

        format_data = payload.get("format")
        format_name = "unknown"
        if isinstance(format_data, dict) and isinstance(format_data.get("format_name"), str):
            format_name = format_data["format_name"]
        return AudioProbeResult(
            duration_seconds=duration,
            codec_name=codec_name,
            format_name=format_name,
            sample_rate=sample_rate,
            channel_count=channel_count,
            audio_stream_count=len(audio_streams),
        )

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
        for candidate in (
            format_data.get("duration") if isinstance(format_data, dict) else None,
            stream.get("duration"),
        ):
            try:
                duration = float(candidate)
            except (TypeError, ValueError):
                continue
            if math.isfinite(duration) and duration > 0:
                return duration
        return 0.0

    @staticmethod
    def _suffix_for_mime(content_type: str) -> str:
        return {
            "audio/wav": ".wav",
            "audio/x-wav": ".wav",
            "audio/mpeg": ".mp3",
            "audio/mp3": ".mp3",
            "audio/ogg": ".ogg",
            "audio/mp4": ".m4a",
            "audio/aac": ".aac",
            "audio/webm": ".webm",
        }.get(content_type.split(";", 1)[0].strip().lower(), ".audio")
