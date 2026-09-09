"""Bounded FFmpeg tail-hold extension for local portfolio clips."""

from __future__ import annotations

import asyncio
import math
import shutil
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from app.providers.errors_video import VideoDurationFitError


@dataclass(frozen=True, slots=True)
class VideoDurationFitResult:
    """Video bytes and the traceable tail extension applied to them."""

    content: bytes
    content_type: str
    changed: bool
    source_duration_seconds: float
    target_duration_seconds: float
    metadata: dict[str, object]


class FFmpegVideoTailExtender:
    """Hold the last decoded frame when audio needs a little more time.

    This is intentionally a conservative fallback for the Mac portfolio demo:
    preserving intelligible narration is preferred to speeding speech beyond a
    small bound. The derived clip contains video only because narration is
    mixed as a separate, auditable audio Artifact during Assembly.
    """

    def __init__(
        self,
        binary: str = "ffmpeg",
        timeout_seconds: int = 120,
        tolerance_seconds: float = 0.03,
    ) -> None:
        if tolerance_seconds < 0:
            raise ValueError("tolerance_seconds must not be negative")
        self.binary = binary
        self.timeout_seconds = max(1, timeout_seconds)
        self.tolerance_seconds = tolerance_seconds

    async def extend(
        self,
        content: bytes,
        content_type: str,
        source_duration_seconds: float,
        target_duration_seconds: float,
    ) -> VideoDurationFitResult:
        normalized_type = content_type.split(";", 1)[0].strip().lower()
        if not content or not normalized_type.startswith("video/"):
            raise VideoDurationFitError(
                "VIDEO_DURATION_FIT_INPUT_INVALID",
                "Video duration fitting requires non-empty video content",
            )
        if (
            not math.isfinite(source_duration_seconds)
            or source_duration_seconds <= 0
            or not math.isfinite(target_duration_seconds)
            or target_duration_seconds <= 0
        ):
            raise VideoDurationFitError(
                "VIDEO_DURATION_FIT_INPUT_INVALID",
                "Video source and target durations must be positive finite values",
            )
        if target_duration_seconds > 3600:
            raise VideoDurationFitError(
                "VIDEO_DURATION_FIT_INPUT_INVALID",
                "Video target duration must not exceed 3600 seconds",
            )
        if target_duration_seconds <= source_duration_seconds + self.tolerance_seconds:
            return VideoDurationFitResult(
                content=content,
                content_type=normalized_type,
                changed=False,
                source_duration_seconds=round(source_duration_seconds, 6),
                target_duration_seconds=round(target_duration_seconds, 6),
                metadata={
                    "duration_fit": "not_needed",
                    "filter": "none",
                    "source_duration_seconds": round(source_duration_seconds, 6),
                    "target_duration_seconds": round(target_duration_seconds, 6),
                    "extension_seconds": 0.0,
                },
            )
        if shutil.which(self.binary) is None:
            raise VideoDurationFitError(
                "VIDEO_DURATION_FIT_UNAVAILABLE",
                f"{self.binary} is required to extend video duration",
            )

        extension_seconds = target_duration_seconds - source_duration_seconds
        # FFmpeg versions differ in how they round the final frame at `-t`.
        # Leave enough cloned tail for the encoder to reach the requested
        # timestamp; `-t` still clamps the delivered video to the target.
        padding_guard_seconds = 0.5
        with TemporaryDirectory(prefix="ai-video-duration-fit-") as directory:
            root = Path(directory)
            input_path = root / f"input{self._suffix_for_mime(normalized_type)}"
            output_path = root / "extended.mp4"
            await asyncio.to_thread(input_path.write_bytes, content)
            command = [
                self.binary,
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostdin",
                "-y",
                "-i",
                str(input_path),
                "-vf",
                (
                    "tpad=stop_mode=clone:"
                    f"stop_duration={extension_seconds + padding_guard_seconds:.6f},"
                    "setpts=PTS-STARTPTS"
                ),
                "-an",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                "-fps_mode",
                "cfr",
                "-t",
                f"{target_duration_seconds:.6f}",
                str(output_path),
            ]
            try:
                process = await asyncio.create_subprocess_exec(
                    *command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            except FileNotFoundError as exc:
                raise VideoDurationFitError(
                    "VIDEO_DURATION_FIT_UNAVAILABLE",
                    f"{self.binary} is required to extend video duration",
                ) from exc

            try:
                _stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=self.timeout_seconds,
                )
            except TimeoutError as exc:
                process.kill()
                await process.communicate()
                raise VideoDurationFitError(
                    "VIDEO_DURATION_FIT_TIMEOUT",
                    "FFmpeg timed out while extending video duration",
                ) from exc

            if process.returncode != 0 or not output_path.is_file():
                detail = stderr.decode("utf-8", errors="replace").strip()
                message = "FFmpeg could not extend video duration"
                if detail:
                    message = f"{message}: {detail[:500]}"
                raise VideoDurationFitError("VIDEO_DURATION_FIT_FAILED", message)
            extended_content = await asyncio.to_thread(output_path.read_bytes)

        if not extended_content:
            raise VideoDurationFitError(
                "VIDEO_DURATION_FIT_FAILED",
                "FFmpeg produced an empty extended video Artifact",
            )
        return VideoDurationFitResult(
            content=extended_content,
            content_type="video/mp4",
            changed=True,
            source_duration_seconds=round(source_duration_seconds, 6),
            target_duration_seconds=round(target_duration_seconds, 6),
            metadata={
                "duration_fit": "tail_hold",
                "filter": "tpad",
                "source_duration_seconds": round(source_duration_seconds, 6),
                "target_duration_seconds": round(target_duration_seconds, 6),
                "extension_seconds": round(extension_seconds, 6),
                "padding_guard_seconds": padding_guard_seconds,
                "output_codec": "libx264",
                "audio_removed": True,
            },
        )

    @staticmethod
    def _suffix_for_mime(content_type: str) -> str:
        return ".webm" if content_type == "video/webm" else ".mp4"


class FFmpegVideoTrimmer:
    """Trim a local video when a continuous narration is shorter than visuals.

    The portfolio runner prefers a shorter complete story over a long silent
    tail. Like the tail extender above, this class writes a video-only derived
    Artifact; narration remains a separate continuous audio Artifact.
    """

    def __init__(
        self,
        binary: str = "ffmpeg",
        timeout_seconds: int = 120,
        tolerance_seconds: float = 0.03,
    ) -> None:
        if tolerance_seconds < 0:
            raise ValueError("tolerance_seconds must not be negative")
        self.binary = binary
        self.timeout_seconds = max(1, timeout_seconds)
        self.tolerance_seconds = tolerance_seconds

    async def trim(
        self,
        content: bytes,
        content_type: str,
        source_duration_seconds: float,
        target_duration_seconds: float,
    ) -> VideoDurationFitResult:
        normalized_type = content_type.split(";", 1)[0].strip().lower()
        if not content or not normalized_type.startswith("video/"):
            raise VideoDurationFitError(
                "VIDEO_DURATION_FIT_INPUT_INVALID",
                "Video duration fitting requires non-empty video content",
            )
        if (
            not math.isfinite(source_duration_seconds)
            or source_duration_seconds <= 0
            or not math.isfinite(target_duration_seconds)
            or target_duration_seconds <= 0
        ):
            raise VideoDurationFitError(
                "VIDEO_DURATION_FIT_INPUT_INVALID",
                "Video source and target durations must be positive finite values",
            )
        if target_duration_seconds > 3600:
            raise VideoDurationFitError(
                "VIDEO_DURATION_FIT_INPUT_INVALID",
                "Video target duration must not exceed 3600 seconds",
            )
        if target_duration_seconds >= source_duration_seconds - self.tolerance_seconds:
            return VideoDurationFitResult(
                content=content,
                content_type=normalized_type,
                changed=False,
                source_duration_seconds=round(source_duration_seconds, 6),
                target_duration_seconds=round(target_duration_seconds, 6),
                metadata={
                    "duration_fit": "not_needed",
                    "filter": "none",
                    "source_duration_seconds": round(source_duration_seconds, 6),
                    "target_duration_seconds": round(target_duration_seconds, 6),
                    "trim_seconds": 0.0,
                },
            )
        if target_duration_seconds >= source_duration_seconds:
            raise VideoDurationFitError(
                "VIDEO_DURATION_FIT_INPUT_INVALID",
                "Video trim target must be shorter than the source duration",
            )
        if shutil.which(self.binary) is None:
            raise VideoDurationFitError(
                "VIDEO_DURATION_FIT_UNAVAILABLE",
                f"{self.binary} is required to trim video duration",
            )

        trim_seconds = source_duration_seconds - target_duration_seconds
        with TemporaryDirectory(prefix="ai-video-duration-trim-") as directory:
            root = Path(directory)
            input_path = root / f"input{self._suffix_for_mime(normalized_type)}"
            output_path = root / "trimmed.mp4"
            await asyncio.to_thread(input_path.write_bytes, content)
            command = [
                self.binary,
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostdin",
                "-y",
                "-i",
                str(input_path),
                "-vf",
                "setpts=PTS-STARTPTS",
                "-an",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                "-t",
                f"{target_duration_seconds:.6f}",
                str(output_path),
            ]
            try:
                process = await asyncio.create_subprocess_exec(
                    *command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            except FileNotFoundError as exc:
                raise VideoDurationFitError(
                    "VIDEO_DURATION_FIT_UNAVAILABLE",
                    f"{self.binary} is required to trim video duration",
                ) from exc

            try:
                _stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=self.timeout_seconds,
                )
            except TimeoutError as exc:
                process.kill()
                await process.communicate()
                raise VideoDurationFitError(
                    "VIDEO_DURATION_FIT_TIMEOUT",
                    "FFmpeg timed out while trimming video duration",
                ) from exc

            if process.returncode != 0 or not output_path.is_file():
                detail = stderr.decode("utf-8", errors="replace").strip()
                message = "FFmpeg could not trim video duration"
                if detail:
                    message = f"{message}: {detail[:500]}"
                raise VideoDurationFitError("VIDEO_DURATION_FIT_FAILED", message)
            trimmed_content = await asyncio.to_thread(output_path.read_bytes)

        if not trimmed_content:
            raise VideoDurationFitError(
                "VIDEO_DURATION_FIT_FAILED",
                "FFmpeg produced an empty trimmed video Artifact",
            )
        return VideoDurationFitResult(
            content=trimmed_content,
            content_type="video/mp4",
            changed=True,
            source_duration_seconds=round(source_duration_seconds, 6),
            target_duration_seconds=round(target_duration_seconds, 6),
            metadata={
                "duration_fit": "trim",
                "filter": "trim",
                "source_duration_seconds": round(source_duration_seconds, 6),
                "target_duration_seconds": round(target_duration_seconds, 6),
                "trim_seconds": round(trim_seconds, 6),
                "output_codec": "libx264",
                "audio_removed": True,
            },
        )

    @staticmethod
    def _suffix_for_mime(content_type: str) -> str:
        return ".webm" if content_type == "video/webm" else ".mp4"
