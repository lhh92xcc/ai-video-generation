"""Low-cost motion evidence for generated video artifacts.

This module deliberately does not try to judge cinematic quality.  It only
decodes a small grayscale frame sample and records whether the file is
visibly changing over time.  Provider identity and human review remain the
source of truth for portfolio acceptance.
"""

from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Protocol


class VideoMotionEvidenceValidator(Protocol):
    async def validate_bytes(self, content: bytes, content_type: str) -> "VideoMotionEvidence":
        ...


@dataclass(frozen=True, slots=True)
class VideoMotionEvidence:
    status: str
    method: str
    sample_fps: float
    sample_width: int
    sample_height: int
    sample_count: int
    compared_frame_count: int
    mean_frame_delta: float | None
    max_frame_delta: float | None
    changed_frame_ratio: float | None
    frozen_frame_ratio: float | None
    reason: str | None = None

    def as_metadata(self) -> dict[str, object]:
        return {
            "status": self.status,
            "method": self.method,
            "sample_fps": self.sample_fps,
            "sample_width": self.sample_width,
            "sample_height": self.sample_height,
            "sample_count": self.sample_count,
            "compared_frame_count": self.compared_frame_count,
            "mean_frame_delta": self.mean_frame_delta,
            "max_frame_delta": self.max_frame_delta,
            "changed_frame_ratio": self.changed_frame_ratio,
            "frozen_frame_ratio": self.frozen_frame_ratio,
            "reason": self.reason,
        }


class FFmpegMotionEvidenceValidator:
    """Decode a small frame sample and detect an entirely frozen video."""

    method = "ffmpeg_gray_frame_delta_v1"

    def __init__(
        self,
        binary: str = "ffmpeg",
        timeout_seconds: int = 30,
        sample_fps: float = 4.0,
        sample_width: int = 32,
        sample_height: int = 32,
        changed_delta: float = 0.5,
        frozen_delta: float = 0.05,
    ) -> None:
        self.binary = binary
        self.timeout_seconds = max(1, timeout_seconds)
        self.sample_fps = max(1.0, sample_fps)
        self.sample_width = max(8, sample_width)
        self.sample_height = max(8, sample_height)
        self.changed_delta = max(0.0, changed_delta)
        self.frozen_delta = max(0.0, frozen_delta)

    async def validate_bytes(
        self,
        content: bytes,
        content_type: str,
    ) -> VideoMotionEvidence:
        if not content:
            return self._unavailable("video_content_empty")
        if not content_type.startswith("video/"):
            return self._unavailable("video_content_not_video_mime")
        if shutil.which(self.binary) is None:
            return self._unavailable("ffmpeg_not_found")

        suffix = ".webm" if content_type == "video/webm" else ".mp4"
        temporary_path: Path | None = None
        try:
            with NamedTemporaryFile(prefix="ai-video-motion-", suffix=suffix, delete=False) as file:
                temporary_path = Path(file.name)
            await asyncio.to_thread(temporary_path.write_bytes, content)
            frames = await self._decode_frames(temporary_path)
        except (OSError, ValueError) as exc:
            return self._unavailable(f"motion_sample_failed:{str(exc)[:160]}")
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

        frame_size = self.sample_width * self.sample_height
        sample_count = len(frames)
        if sample_count < 2:
            return self._unavailable("motion_sample_has_fewer_than_two_frames", sample_count)

        deltas = [
            self._mean_absolute_delta(left, right)
            for left, right in zip(frames, frames[1:])
        ]
        mean_delta = sum(deltas) / len(deltas)
        max_delta = max(deltas)
        changed_ratio = sum(delta >= self.changed_delta for delta in deltas) / len(deltas)
        frozen_ratio = sum(delta <= self.frozen_delta for delta in deltas) / len(deltas)

        # A compressed still frame usually has tiny codec noise but remains
        # effectively unchanged.  Use a conservative frozen classification;
        # ambiguous low-motion clips stay indeterminate for human review.
        if mean_delta <= self.frozen_delta and frozen_ratio >= 0.95:
            status = "frozen"
        elif mean_delta >= 0.25 or changed_ratio >= 0.20:
            status = "motion_detected"
        else:
            status = "indeterminate"

        if frame_size <= 0:  # defensive guard for future constructor changes
            return self._unavailable("invalid_motion_sample_size", sample_count)
        return VideoMotionEvidence(
            status=status,
            method=self.method,
            sample_fps=self.sample_fps,
            sample_width=self.sample_width,
            sample_height=self.sample_height,
            sample_count=sample_count,
            compared_frame_count=len(deltas),
            mean_frame_delta=round(mean_delta, 6),
            max_frame_delta=round(max_delta, 6),
            changed_frame_ratio=round(changed_ratio, 6),
            frozen_frame_ratio=round(frozen_ratio, 6),
        )

    async def _decode_frames(self, path: Path) -> list[bytes]:
        command = [
            self.binary,
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(path),
            "-map",
            "0:v:0",
            "-vf",
            f"fps={self.sample_fps},scale={self.sample_width}:{self.sample_height}:flags=bilinear,format=gray",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "gray",
            "pipe:1",
        ]
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise OSError("ffmpeg_not_found") from exc
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=self.timeout_seconds,
            )
        except TimeoutError as exc:
            process.kill()
            await process.communicate()
            raise ValueError("ffmpeg_motion_sample_timeout") from exc
        if process.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace").strip()
            raise ValueError(detail[:240] or "ffmpeg_motion_sample_failed")

        frame_size = self.sample_width * self.sample_height
        return [
            stdout[offset : offset + frame_size]
            for offset in range(0, len(stdout), frame_size)
            if len(stdout[offset : offset + frame_size]) == frame_size
        ]

    @staticmethod
    def _mean_absolute_delta(left: bytes, right: bytes) -> float:
        if len(left) != len(right) or not left:
            return 0.0
        return sum(abs(first - second) for first, second in zip(left, right, strict=True)) / len(left)

    def _unavailable(self, reason: str, sample_count: int = 0) -> VideoMotionEvidence:
        return VideoMotionEvidence(
            status="unavailable",
            method=self.method,
            sample_fps=self.sample_fps,
            sample_width=self.sample_width,
            sample_height=self.sample_height,
            sample_count=sample_count,
            compared_frame_count=0,
            mean_frame_delta=None,
            max_frame_delta=None,
            changed_frame_ratio=None,
            frozen_frame_ratio=None,
            reason=reason,
        )
