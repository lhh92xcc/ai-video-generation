"""Local Ken Burns-style motion over a still keyframe.

This is the default video stage for a 16GB Apple Silicon portfolio setup. It
does not pretend to be an I2V model: it turns a reviewed keyframe into a small,
playable MP4 using FFmpeg pan/zoom motion, with a deterministic color-card
fallback when no keyframe has been supplied yet.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic

from app.domain.models import VideoClipGenerationRequest, VideoClipGenerationResult
from app.providers.errors_video import VideoProviderError


class FFmpegMotionVideoGenerationProvider:
    def __init__(
        self,
        model: str = "ffmpeg-motion-v1",
        timeout_seconds: int = 300,
        binary: str = "ffmpeg",
        width: int = 576,
        height: int = 1024,
        fps: int = 24,
        motion_zoom: float = 1.12,
    ) -> None:
        self._model = model
        self._timeout_seconds = max(1, timeout_seconds)
        self._binary = binary
        self._width = max(256, width // 2 * 2)
        self._height = max(256, height // 2 * 2)
        self._fps = max(1, fps)
        self._motion_zoom = max(1.0, min(1.5, motion_zoom))

    async def generate_video_clip(
        self,
        request: VideoClipGenerationRequest,
    ) -> VideoClipGenerationResult:
        if shutil.which(self._binary) is None:
            raise VideoProviderError(
                "VIDEO_PROVIDER_UNAVAILABLE",
                f"{self._binary} is required for the local FFmpeg motion provider",
            )

        started = monotonic()
        prompt_hash = hashlib.sha256(request.prompt.encode("utf-8")).hexdigest()
        frames = request.duration_seconds * self._fps
        with TemporaryDirectory(prefix="ai-video-motion-") as directory:
            root = Path(directory)
            output_path = root / "motion.mp4"
            input_path: Path | None = None
            if request.keyframe_bytes:
                suffix = ".jpg" if request.keyframe_mime_type == "image/jpeg" else ".png"
                input_path = root / f"keyframe{suffix}"
                await asyncio.to_thread(input_path.write_bytes, request.keyframe_bytes)

            if input_path is not None:
                input_args = ["-loop", "1", "-i", str(input_path)]
                filter_graph = (
                    f"scale={self._width}:{self._height}:force_original_aspect_ratio=increase,"
                    f"crop={self._width}:{self._height},"
                    f"zoompan=z='min(zoom+0.0015,{self._motion_zoom})':"
                    f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
                    f"d={frames}:s={self._width}x{self._height}:fps={self._fps},"
                    "format=yuv420p"
                )
                source = "reviewed_reference_image"
            else:
                color = f"0x{prompt_hash[:6]}"
                input_args = [
                    "-f",
                    "lavfi",
                    "-i",
                    f"color=c={color}:s={self._width}x{self._height}:r={self._fps}",
                ]
                filter_graph = "format=yuv420p"
                source = "deterministic_color_fallback"

            command = [
                self._binary,
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostdin",
                "-y",
                *input_args,
                "-vf",
                filter_graph,
                "-t",
                str(request.duration_seconds),
                "-an",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                str(output_path),
            ]
            try:
                process = await asyncio.create_subprocess_exec(
                    *command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            except FileNotFoundError as exc:
                raise VideoProviderError(
                    "VIDEO_PROVIDER_UNAVAILABLE",
                    f"{self._binary} is required for the local FFmpeg motion provider",
                ) from exc

            try:
                _stdout, stderr = await asyncio.wait_for(
                    process.communicate(), timeout=self._timeout_seconds
                )
            except TimeoutError as exc:
                process.kill()
                await process.communicate()
                raise VideoProviderError(
                    "VIDEO_PROVIDER_TIMEOUT",
                    "FFmpeg timed out while creating the motion video",
                ) from exc

            if process.returncode != 0:
                detail = stderr.decode("utf-8", errors="replace").strip()
                message = "FFmpeg failed while creating the motion video"
                if detail:
                    message = f"{message}: {detail[:500]}"
                raise VideoProviderError("VIDEO_PROVIDER_FAILED", message)

            try:
                content = await asyncio.to_thread(output_path.read_bytes)
            except OSError as exc:
                raise VideoProviderError(
                    "VIDEO_PROVIDER_INVALID_RESPONSE",
                    "The FFmpeg motion provider did not produce a video file",
                ) from exc

        if not content:
            raise VideoProviderError(
                "VIDEO_PROVIDER_INVALID_RESPONSE",
                "The FFmpeg motion provider produced an empty video file",
            )

        return VideoClipGenerationResult(
            video_base64=base64.b64encode(content).decode("ascii"),
            mime_type="video/mp4",
            provider="ffmpeg_motion",
            model=self._model,
            duration_seconds=request.duration_seconds,
            duration_ms=max(1, round((monotonic() - started) * 1000)),
            metadata={
                "local": True,
                "motion": "ken_burns",
                "source": source,
                "width": self._width,
                "height": self._height,
                "fps": self._fps,
                "prompt_sha256": prompt_hash,
            },
        )
