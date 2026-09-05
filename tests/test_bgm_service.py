from __future__ import annotations

import asyncio
from uuid import uuid4

from app.domain.models import (
    AudioBGMCreateRequest,
    EpisodeOutlineContent,
    EpisodeRecord,
    GenerationTaskKind,
    RightsStatus,
    TaskStatus,
)
from app.media.audio_normalization import FFmpegAudioNormalizer
from app.media.audio_validation import FFprobeAudioValidator
from app.providers.mock_bgm import MockBGMProvider
from app.queue import InProcessTaskQueue
from app.repositories.in_memory import InMemoryStore
from app.services.bgm_service import BGMTaskService
from app.storage.local import LocalFileArtifactStorage


def test_mock_bgm_task_writes_playable_audio_artifact(tmp_path) -> None:
    async def exercise() -> None:
        store = InMemoryStore()
        queue = InProcessTaskQueue()
        storage = LocalFileArtifactStorage(tmp_path)
        service = BGMTaskService(
            store,
            queue,
            MockBGMProvider(),
            storage,
            audio_validator=FFprobeAudioValidator(),
            audio_normalizer=FFmpegAudioNormalizer(timeout_seconds=30),
        )
        queue.set_handler(service.run_task)
        episode = EpisodeRecord(
            project_id=uuid4(),
            story_bible_id=uuid4(),
            episode_number=1,
            outline=EpisodeOutlineContent(
                episode_number=1,
                title="第一集",
                logline="主角发现线索",
                objective="推进调查",
                conflict="线索被隐藏",
                turning_point="找到关键物证",
                ending_hook="有人跟踪主角",
                source_chapter_numbers=[1],
                target_duration_seconds=30,
            ),
            provider="test",
            model="test",
            duration_ms=0,
        )
        await store.save_episodes([episode])

        task, reused = await service.create_task(
            episode.id,
            AudioBGMCreateRequest(
                label="测试 BGM",
                rights_status=RightsStatus.CONFIRMED,
                rights_holder="项目测试团队",
                rights_reference="fixture-license-001",
            ),
            idempotency_key="bgm-1",
        )
        await queue.close()

        assert reused is False
        saved_task = await store.get_task(task.id)
        assert saved_task is not None
        assert saved_task.kind == GenerationTaskKind.AUDIO_BGM
        assert saved_task.status == TaskStatus.SUCCEEDED
        assert saved_task.current_stage is None
        assert saved_task.artifacts[0].type == "audio_bgm"
        assert saved_task.artifacts[0].provider == "mock"
        assert saved_task.artifacts[0].metadata["content_type"] == "audio/wav"
        assert saved_task.artifacts[0].metadata["source_type"] == "deterministic_fixture"
        assert saved_task.artifacts[0].metadata["rights_status"] == "confirmed"
        assert saved_task.artifacts[0].metadata["rights_review_required"] is True
        assert saved_task.artifacts[0].metadata["normalization"]["enabled"] is True
        assert saved_task.artifacts[0].metadata["normalization"]["filter"] == "loudnorm"
        assert saved_task.artifacts[0].metadata["normalization"]["sample_rate"] == 48000
        assert saved_task.artifacts[0].metadata["ffprobe"]["audio_stream_count"] == 1
        assert saved_task.artifacts[0].metadata["duration_seconds"] > 0
        storage_key = saved_task.artifacts[0].metadata["storage_key"]
        assert isinstance(storage_key, str)
        assert len(await storage.get_bytes(storage_key)) > 44
        await storage.close()

    asyncio.run(exercise())
