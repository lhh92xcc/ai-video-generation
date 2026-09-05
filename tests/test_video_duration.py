from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from app.media.video_duration import FFmpegVideoTailExtender, FFmpegVideoTrimmer
from app.media.video_validation import FFprobeVideoValidator


async def _run_ffmpeg(*arguments: str) -> None:
    process = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        *arguments,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _stdout, stderr = await process.communicate()
    if process.returncode != 0:
        raise RuntimeError(stderr.decode("utf-8", errors="replace"))


def test_video_tail_extender_holds_last_frame_without_audio() -> None:
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg and ffprobe are required for video duration fitting")

    async def exercise() -> None:
        with TemporaryDirectory(prefix="ai-video-duration-test-") as directory:
            root = Path(directory)
            source_path = root / "source.mp4"
            await _run_ffmpeg(
                "-f",
                "lavfi",
                "-i",
                "color=c=blue:s=160x90:r=8",
                "-t",
                "1",
                "-an",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                str(source_path),
            )
            source = source_path.read_bytes()
            result = await FFmpegVideoTailExtender(timeout_seconds=30).extend(
                source,
                "video/mp4",
                1.0,
                1.75,
            )

            assert result.changed is True
            assert result.metadata["duration_fit"] == "tail_hold"
            probe = await FFprobeVideoValidator().validate_bytes(result.content, result.content_type)
            assert 1.70 <= probe.duration_seconds <= 1.80
            assert probe.audio_stream_count == 0
            assert probe.width == 160
            assert probe.height == 90

    asyncio.run(exercise())


def test_video_tail_extender_keeps_input_when_target_is_within_tolerance() -> None:
    async def exercise() -> None:
        result = await FFmpegVideoTailExtender().extend(
            b"verified-video-bytes",
            "video/mp4",
            2.0,
            2.01,
        )

        assert result.changed is False
        assert result.content == b"verified-video-bytes"
        assert result.metadata["duration_fit"] == "not_needed"

    asyncio.run(exercise())


def test_video_trimmer_conforms_visual_duration_to_continuous_audio() -> None:
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg and ffprobe are required for video duration fitting")

    async def exercise() -> None:
        with TemporaryDirectory(prefix="ai-video-duration-trim-test-") as directory:
            root = Path(directory)
            source_path = root / "source.mp4"
            await _run_ffmpeg(
                "-f",
                "lavfi",
                "-i",
                "color=c=green:s=160x90:r=8",
                "-t",
                "2",
                "-an",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                str(source_path),
            )
            result = await FFmpegVideoTrimmer(timeout_seconds=30).trim(
                source_path.read_bytes(),
                "video/mp4",
                2.0,
                1.25,
            )

            assert result.changed is True
            assert result.metadata["duration_fit"] == "trim"
            probe = await FFprobeVideoValidator().validate_bytes(result.content, result.content_type)
            assert 1.20 <= probe.duration_seconds <= 1.30
            assert probe.audio_stream_count == 0
            assert probe.width == 160
            assert probe.height == 90
