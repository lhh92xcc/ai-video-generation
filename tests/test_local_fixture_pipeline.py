from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from uuid import uuid4

import pytest

from app.domain.models import (
    AssetStatus,
    AssetType,
    EpisodeOutlineContent,
    EpisodeRecord,
    ShotAssetReference,
    ShotContent,
    ShotListRecord,
    TaskStatus,
    VideoAssemblyCreateRequest,
    VideoClipCreateRequest,
)
from app.media.video_validation import FFprobeVideoValidator
from app.providers.local_fixture_video import LocalFixtureVideoGenerationProvider
from app.queue import InProcessTaskQueue
from app.repositories.in_memory import InMemoryStore
from app.services.video_assembly_service import VideoAssemblyTaskService
from app.services.video_clip_service import VideoClipTaskService
from app.storage.local import LocalFileArtifactStorage


def test_local_fixture_clips_can_be_assembled(tmp_path: Path) -> None:
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg and ffprobe are required for fixture pipeline tests")

    async def exercise() -> None:
        store = InMemoryStore()
        storage = LocalFileArtifactStorage(tmp_path / "artifacts")
        project_id = uuid4()
        episode = EpisodeRecord(
            project_id=project_id,
            story_bible_id=uuid4(),
            episode_number=1,
            outline=EpisodeOutlineContent(
                episode_number=1,
                title="Fixture Assembly 集",
                logline="两个可播放颜色卡镜头组成样片",
                objective="验证 video_clip 到 Assembly",
                conflict="没有真实视频 Provider Key",
                turning_point="本地 Fixture 产出有效 MP4",
                ending_hook="后续替换真实 Provider",
                source_chapter_numbers=[1],
                target_duration_seconds=30,
            ),
            provider="test",
            model="test",
            duration_ms=0,
        )
        await store.save_episodes([episode])
        shots = []
        for shot_index, prompt in enumerate(("蓝色雨夜街道", "红色室内线索"), start=1):
            shots.append(
                ShotContent(
                    shot_index=shot_index,
                    scene_index=shot_index,
                    duration_seconds=1,
                    shot_size="medium",
                    camera_movement="fixed",
                    characters=["主角"],
                    location="测试场景",
                    visual_prompt=prompt,
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
            )
        await store.save_shot_list(
            ShotListRecord(
                project_id=project_id,
                episode_id=episode.id,
                script_id=uuid4(),
                shots=shots,
                provider="test",
                model="test",
                duration_ms=0,
            )
        )

        clip_queue = InProcessTaskQueue()
        clip_service = VideoClipTaskService(
            store,
            clip_queue,
            LocalFixtureVideoGenerationProvider(),
            storage,
        )
        clip_queue.set_handler(clip_service.run_task)
        clip_tasks = []
        for shot_index in (1, 2):
            task, reused = await clip_service.create_task(
                episode.id,
                shot_index,
                VideoClipCreateRequest(),
                idempotency_key=f"fixture-{shot_index}",
            )
            assert reused is False
            clip_tasks.append(task)
        await clip_queue.close()

        completed_clips = [await store.get_task(task.id) for task in clip_tasks]
        assert all(
            task is not None and task.status == TaskStatus.SUCCEEDED
            for task in completed_clips
        )
        assert all(
            task and task.artifacts[0].metadata.get("fixture") is True
            for task in completed_clips
        )

        assembly_queue = InProcessTaskQueue()
        assembly_service = VideoAssemblyTaskService(store, assembly_queue, storage)
        assembly_queue.set_handler(assembly_service.run_task)
        render_task, reused = await assembly_service.create_task(
            episode.id,
            VideoAssemblyCreateRequest(clip_task_ids=[task.id for task in clip_tasks]),
            idempotency_key="fixture-assembly",
        )
        await assembly_queue.close()

        assert reused is False
        completed_render = await store.get_task(render_task.id)
        assert completed_render is not None
        assert completed_render.status == TaskStatus.SUCCEEDED
        artifact = completed_render.artifacts[0]
        assert artifact.type == "rendered_video"
        output = await storage.get_bytes(str(artifact.metadata["storage_key"]))
        probe = await FFprobeVideoValidator().validate_bytes(output, "video/mp4")
        assert 1.7 <= probe.duration_seconds <= 2.3
        assert probe.codec_name == "h264"
        assert artifact.metadata["input_clip_count"] == 2
        await storage.close()

    asyncio.run(exercise())
