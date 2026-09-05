"""Playable deterministic video provider for local pipeline validation."""

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


class LocalFixtureVideoGenerationProvider:
    """Generate a playable color-card MP4 without calling an external service.

    This provider is intentionally a development/test fixture. It validates the
    video Artifact, queue, storage and assembly boundaries, but it does not
    represent generated visual quality or a real video model.
    """

    def __init__(
        self,
        model: str = "local-fixture-video-v1",
        timeout_seconds: int = 300,
        binary: str = "ffmpeg",
        width: int = 320,
        height: int = 180,
        fps: int = 24,
    ) -> None:
        self._model = model
        self._timeout_seconds = max(1, timeout_seconds)
        self._binary = binary
        self._width = max(16, width)
        self._height = max(16, height)
        self._fps = max(1, fps)

    async def generate_video_clip(
        self,
        request: VideoClipGenerationRequest,
    ) -> VideoClipGenerationResult:
        if shutil.which(self._binary) is None:
            raise VideoProviderError(
                "VIDEO_PROVIDER_UNAVAILABLE",
                f"{self._binary} is required for the local fixture video provider",
            )

        started = monotonic()
        prompt_hash = hashlib.sha256(request.prompt.encode("utf-8")).hexdigest()
        color = f"0x{prompt_hash[:6]}"
        with TemporaryDirectory(prefix="ai-video-fixture-") as directory:
            output_path = Path(directory) / "fixture.mp4"
            command = [
                self._binary,
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostdin",
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"color=c={color}:s={self._width}x{self._height}:r={self._fps}",
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
                    f"{self._binary} is required for the local fixture video provider",
                ) from exc

            try:
                _stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=self._timeout_seconds,
                )
            except TimeoutError as exc:
                process.kill()
                await process.communicate()
                raise VideoProviderError(
                    "VIDEO_PROVIDER_TIMEOUT",
                    "FFmpeg timed out while creating the local fixture video",
                ) from exc

            if process.returncode != 0:
                detail = stderr.decode("utf-8", errors="replace").strip()
                message = "FFmpeg failed while creating the local fixture video"
                if detail:
                    message = f"{message}: {detail[:500]}"
                raise VideoProviderError("VIDEO_PROVIDER_FAILED", message)

            try:
                content = await asyncio.to_thread(output_path.read_bytes)
            except OSError as exc:
                raise VideoProviderError(
                    "VIDEO_PROVIDER_INVALID_RESPONSE",
                    "The local fixture provider did not produce a video file",
                ) from exc

        if not content:
            raise VideoProviderError(
                "VIDEO_PROVIDER_INVALID_RESPONSE",
                "The local fixture provider produced an empty video file",
            )

        return VideoClipGenerationResult(
            video_base64=base64.b64encode(content).decode("ascii"),
            mime_type="video/mp4",
            provider="local_fixture",
            model=self._model,
            duration_seconds=request.duration_seconds,
            duration_ms=max(1, round((monotonic() - started) * 1000)),
            metadata={
                "fixture": True,
                "playable_output": True,
                "note": "Deterministic FFmpeg color-card fixture; not generated visual content.",
                "width": self._width,
                "height": self._height,
                "fps": self._fps,
                "color": color,
                "prompt_sha256": prompt_hash,
            },
        )
