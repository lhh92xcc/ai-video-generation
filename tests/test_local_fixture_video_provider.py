from __future__ import annotations

import asyncio
import base64
import shutil
from uuid import uuid4

import pytest

from app.domain.models import VideoClipGenerationRequest
from app.domain.models import (
    AssetStatus,
    AssetType,
    EpisodeOutlineContent,
    EpisodeRecord,
    ShotAssetReference,
    ShotContent,
    ShotListRecord,
    VideoClipCreateRequest,
)
from app.media.video_validation import FFprobeVideoValidator
from app.providers.errors_video import VideoProviderError
from app.providers.local_fixture_video import LocalFixtureVideoGenerationProvider
from app.queue import InProcessTaskQueue
from app.repositories.in_memory import InMemoryStore
from app.services.video_clip_service import VideoClipTaskService
from app.storage.local import LocalFileArtifactStorage


def _request() -> VideoClipGenerationRequest:
    return VideoClipGenerationRequest(
        episode_id=uuid4(),
        shot_list_id=uuid4(),
        shot_index=1,
        duration_seconds=2,
        prompt="雨夜街道上的角色发现一封旧信",
        negative_prompt="模糊、乱码",
    )


def test_local_fixture_provider_returns_playable_mp4() -> None:
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg and ffprobe are required for fixture provider tests")

    async def exercise() -> None:
        result = await LocalFixtureVideoGenerationProvider().generate_video_clip(_request())
        assert result.output_uri is None
        assert result.video_base64 is not None
        assert result.provider == "local_fixture"
        assert result.metadata["fixture"] is True

        content = base64.b64decode(result.video_base64)
        probe = await FFprobeVideoValidator().validate_bytes(content, result.mime_type)
        assert probe.codec_name == "h264"
        assert probe.width == 320
        assert probe.height == 180
        assert 1.8 <= probe.duration_seconds <= 2.2

    asyncio.run(exercise())


def test_local_fixture_provider_reports_missing_ffmpeg() -> None:
    async def exercise() -> None:
        provider = LocalFixtureVideoGenerationProvider(binary="ffmpeg-does-not-exist")
        with pytest.raises(VideoProviderError) as error:
            await provider.generate_video_clip(_request())
        assert error.value.code == "VIDEO_PROVIDER_UNAVAILABLE"

    asyncio.run(exercise())


def test_local_fixture_video_clip_task_writes_playable_artifact(tmp_path) -> None:
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg and ffprobe are required for fixture service tests")

    async def exercise() -> None:
        store = InMemoryStore()
        queue = InProcessTaskQueue()
        storage = LocalFileArtifactStorage(tmp_path)
        service = VideoClipTaskService(
            store,
            queue,
            LocalFixtureVideoGenerationProvider(),
            storage,
        )
        queue.set_handler(service.run_task)
        project_id = uuid4()
        episode = EpisodeRecord(
            project_id=project_id,
            story_bible_id=uuid4(),
            episode_number=1,
            outline=EpisodeOutlineContent(
                episode_number=1,
                title="Fixture 集",
                logline="验证可播放视频片段",
                objective="完成本地联调",
                conflict="没有真实视频 Key",
                turning_point="Fixture Provider 生成 MP4",
                ending_hook="继续进入 Assembly",
                source_chapter_numbers=[1],
                target_duration_seconds=30,
            ),
            provider="test",
            model="test",
            duration_ms=0,
        )
        await store.save_episodes([episode])
        await store.save_shot_list(
            ShotListRecord(
                project_id=project_id,
                episode_id=episode.id,
                script_id=uuid4(),
                shots=[
                    ShotContent(
                        shot_index=1,
                        scene_index=1,
                        duration_seconds=2,
                        shot_size="medium",
                        camera_movement="fixed",
                        characters=["主角"],
                        location="雨夜街道",
                        visual_prompt="主角在雨夜街道发现旧信",
                        dialogue_refs=[],
                        audio_requirements=["环境声"],
                        asset_requirements=["主角"],
                        asset_refs=[
                            ShotAssetReference(
                                asset_key=uuid4(),
                                asset_type=AssetType.CHARACTER,
                                name="主角",
                                version=1,
                                status=AssetStatus.READY,
                                match_kind="name",
                                matched_text="主角",
                            )
                        ],
                    )
                ],
                provider="test",
                model="test",
                duration_ms=0,
            )
        )

        task, reused = await service.create_task(
            episode.id,
            1,
            VideoClipCreateRequest(),
            idempotency_key="fixture-shot-1",
        )
        await queue.close()

        assert reused is False
        completed = await store.get_task(task.id)
        assert completed is not None
        assert completed.status.value == "succeeded"
        metadata = completed.artifacts[0].metadata
        storage_key = str(metadata["storage_key"])
        content = await storage.get_bytes(storage_key)
        probe = await FFprobeVideoValidator().validate_bytes(content, "video/mp4")
        assert probe.codec_name == "h264"
        assert probe.width == 320
        assert probe.height == 180
        assert 1.8 <= probe.duration_seconds <= 2.2
        assert metadata["fixture"] is True
        await storage.close()

    asyncio.run(exercise())
