from __future__ import annotations

import asyncio
import base64
import shutil
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest

from app.domain.models import (
    GenerationTaskKind,
    GenerationTaskRecord,
    StageName,
    StageRun,
    TaskStatus,
    VideoClipGenerationRequest,
    VideoClipGenerationResult,
)
from app.media.video_validation import FFprobeVideoValidator
from app.media.video_motion import FFmpegMotionEvidenceValidator
from app.providers.errors_video import VideoArtifactValidationError
from app.queue import InProcessTaskQueue
from app.repositories.in_memory import InMemoryStore
from app.services.video_clip_service import VideoClipTaskService
from app.storage.local import LocalFileArtifactStorage


def _playable_mp4(tmp_path: Path) -> bytes:
    output = tmp_path / "sample.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=160x90:r=10",
            "-t",
            "1",
            "-an",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(output),
        ],
        check=True,
    )
    return output.read_bytes()


def test_ffprobe_validator_accepts_playable_mp4(tmp_path: Path) -> None:
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg and ffprobe are required for playable video validation")

    async def exercise() -> None:
        result = await FFprobeVideoValidator().validate_bytes(
            _playable_mp4(tmp_path),
            "video/mp4",
        )
        assert result.duration_seconds > 0
        assert result.width == 160
        assert result.height == 90
        assert result.codec_name == "h264"
        assert "mp4" in result.format_name

    asyncio.run(exercise())


def test_ffprobe_validator_rejects_unplayable_content() -> None:
    if shutil.which("ffprobe") is None:
        pytest.skip("ffprobe is required for playable video validation")

    async def exercise() -> None:
        with pytest.raises(VideoArtifactValidationError) as error:
            await FFprobeVideoValidator().validate_bytes(b"not-a-video", "video/mp4")
        assert error.value.code == "VIDEO_ARTIFACT_NOT_PLAYABLE"

    asyncio.run(exercise())


def test_motion_evidence_distinguishes_frozen_and_changing_video(tmp_path: Path) -> None:
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg is required for motion evidence validation")

    static_frame = tmp_path / "frame.png"
    static_video = tmp_path / "static.mp4"
    moving_video = tmp_path / "moving.mp4"
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc2=s=160x90:r=1", "-frames:v", "1",
            str(static_frame),
        ],
        check=True,
    )
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-loop", "1", "-i", str(static_frame), "-t", "2", "-r", "10",
            "-an", "-pix_fmt", "yuv420p", str(static_video),
        ],
        check=True,
    )
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc2=s=160x90:r=10", "-t", "2",
            "-an", "-pix_fmt", "yuv420p", str(moving_video),
        ],
        check=True,
    )

    async def exercise() -> None:
        validator = FFmpegMotionEvidenceValidator()
        static_result = await validator.validate_bytes(static_video.read_bytes(), "video/mp4")
        moving_result = await validator.validate_bytes(moving_video.read_bytes(), "video/mp4")
        assert static_result.status == "frozen"
        assert moving_result.status == "motion_detected"
        assert moving_result.mean_frame_delta is not None
        assert moving_result.mean_frame_delta > static_result.mean_frame_delta or static_result.mean_frame_delta is None

    asyncio.run(exercise())


def test_video_clip_service_stores_ffprobe_metadata(tmp_path: Path) -> None:
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg and ffprobe are required for playable video validation")

    class PlayableVideoProvider:
        async def generate_video_clip(
            self,
            request: VideoClipGenerationRequest,
        ) -> VideoClipGenerationResult:
            return VideoClipGenerationResult(
                video_base64=base64.b64encode(_playable_mp4(tmp_path)).decode("ascii"),
                mime_type="video/mp4",
                provider="playable-test",
                model="ffmpeg-color-v1",
                duration_seconds=request.duration_seconds,
                duration_ms=1,
            )

    async def exercise() -> None:
        store = InMemoryStore()
        queue = InProcessTaskQueue()
        storage = LocalFileArtifactStorage(tmp_path / "artifacts")
        service = VideoClipTaskService(
            store,
            queue,
            PlayableVideoProvider(),
            storage,
        )
        task = GenerationTaskRecord(
            project_id=uuid4(),
            kind=GenerationTaskKind.VIDEO_CLIP,
            input_data={
                "episode_id": str(uuid4()),
                "shot_list_id": str(uuid4()),
                "shot_index": 1,
                "duration_seconds": 1,
                "prompt": "blue test frame",
                "negative_prompt": "none",
                "asset_refs": [],
            },
            status=TaskStatus.QUEUED,
            current_stage=StageName.VIDEO_CLIP,
            stages=[StageRun(stage=StageName.VIDEO_CLIP, status=TaskStatus.QUEUED)],
        )
        saved_task, _ = await store.create_task(task)
        await service.run_task(saved_task.id)

        completed = await store.get_task(saved_task.id)
        assert completed is not None
        assert completed.status == TaskStatus.SUCCEEDED
        assert completed.artifacts[0].metadata["ffprobe"]["width"] == 160
        assert completed.artifacts[0].metadata["ffprobe"]["height"] == 90
        assert completed.artifacts[0].metadata["content_type"] == "video/mp4"
        await queue.close()
        await storage.close()

    asyncio.run(exercise())
