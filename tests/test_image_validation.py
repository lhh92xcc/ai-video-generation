from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path

import pytest

from app.media.image_validation import (
    FFprobeImageValidator,
    ImageArtifactValidationError,
)


def _make_png(tmp_path: Path, width: int = 432, height: int = 768) -> bytes:
    output = tmp_path / "reference.png"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"color=c=slateblue:s={width}x{height}",
            "-frames:v",
            "1",
            str(output),
        ],
        check=True,
    )
    return output.read_bytes()


def test_ffprobe_image_validator_reads_actual_dimensions(tmp_path: Path) -> None:
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg and ffprobe are required for image validation")

    async def exercise() -> None:
        result = await FFprobeImageValidator().validate_bytes(
            _make_png(tmp_path),
            "image/png",
        )

        assert result.width == 432
        assert result.height == 768
        assert result.codec_name == "png"
        assert result.as_metadata()["aspect_ratio"] == "9:16"

    asyncio.run(exercise())


def test_ffprobe_image_validator_rejects_non_image_content() -> None:
    async def exercise() -> None:
        with pytest.raises(ImageArtifactValidationError) as error:
            await FFprobeImageValidator().validate_bytes(b"not-an-image", "image/png")
        assert error.value.code == "IMAGE_ARTIFACT_NOT_READABLE"

    asyncio.run(exercise())
