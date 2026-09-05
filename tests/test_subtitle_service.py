from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.domain.models import (
    ArtifactSummary,
    EpisodeOutlineContent,
    EpisodeRecord,
    GenerationTaskRecord,
    GenerationTaskKind,
    SubtitleASRCreateRequest,
    StageName,
    SubtitleAlignmentCreateRequest,
    SubtitleCreateRequest,
    TaskStatus,
)
from app.providers.mock_subtitle_alignment import MockSentenceSubtitleAlignmentProvider
from app.providers.mock_asr import MockASRProvider
from app.providers.profiles import ASRProviderProfileError, ASRProviderProfileRegistry
from app.config import load_settings
from app.queue import InProcessTaskQueue
from app.rendering.subtitles import parse_srt
from app.repositories.in_memory import InMemoryStore
from app.services.subtitle_service import SubtitleTaskService
from app.storage.local import LocalFileArtifactStorage


def _episode(project_id):
    return EpisodeRecord(
        project_id=project_id,
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


def test_subtitle_task_writes_utf8_srt_artifact(tmp_path) -> None:
    async def exercise() -> None:
        store = InMemoryStore()
        queue = InProcessTaskQueue()
        storage = LocalFileArtifactStorage(tmp_path)
        service = SubtitleTaskService(store, queue, storage)
        queue.set_handler(service.run_task)
        episode = _episode(uuid4())
        await store.save_episodes([episode])

        task, reused = await service.create_task(
            episode.id,
            SubtitleCreateRequest(
                language="zh-CN",
                cues=[
                    {"start_seconds": 0, "end_seconds": 1.2, "text": "第一句字幕"},
                    {"start_seconds": 1.5, "end_seconds": 3, "text": "第二句字幕"},
                ],
            ),
            idempotency_key="subtitle-1",
        )
        await queue.close()

        assert reused is False
        saved_task = await store.get_task(task.id)
        assert saved_task is not None
        assert saved_task.kind == GenerationTaskKind.SUBTITLE_SRT
        assert saved_task.status == TaskStatus.SUCCEEDED
        assert saved_task.current_stage is None
        artifact = saved_task.artifacts[0]
        assert artifact.type == "subtitle_srt"
        assert artifact.metadata["content_type"] == "application/x-subrip"
        assert artifact.metadata["cue_count"] == 2
        content = await storage.get_bytes(artifact.metadata["storage_key"])
        cues = parse_srt(content)
        assert [cue.text for cue in cues] == ["第一句字幕", "第二句字幕"]
        assert content.decode("utf-8").startswith("1\n00:00:00,000 --> 00:00:01,200")
        await storage.close()

    asyncio.run(exercise())


def test_subtitle_request_rejects_overlapping_cues() -> None:
    with pytest.raises(ValidationError):
        SubtitleCreateRequest(
            cues=[
                {"start_seconds": 0, "end_seconds": 2, "text": "第一句"},
                {"start_seconds": 1, "end_seconds": 3, "text": "重叠句"},
            ]
        )


def test_sentence_alignment_task_writes_estimated_srt_artifact(tmp_path) -> None:
    async def exercise() -> None:
        store = InMemoryStore()
        queue = InProcessTaskQueue()
        storage = LocalFileArtifactStorage(tmp_path)
        service = SubtitleTaskService(
            store,
            queue,
            storage,
            alignment_provider=MockSentenceSubtitleAlignmentProvider(),
        )
        queue.set_handler(service.run_task)
        episode = _episode(uuid4())
        await store.save_episodes([episode])

        task, reused = await service.create_alignment_task(
            episode.id,
            SubtitleAlignmentCreateRequest(
                language="zh-CN",
                text="第一句。第二句，继续说明！",
                audio_duration_seconds=4.0,
            ),
            idempotency_key="align-1",
        )
        await queue.close()

        assert reused is False
        saved_task = await store.get_task(task.id)
        assert saved_task is not None
        assert saved_task.kind == GenerationTaskKind.SUBTITLE_ALIGN
        assert saved_task.status == TaskStatus.SUCCEEDED
        artifact = saved_task.artifacts[0]
        assert artifact.provider == "mock_sentence_alignment"
        assert artifact.metadata["alignment_precision"] == "sentence_estimate"
        assert artifact.metadata["alignment_method"] == "provider"
        assert artifact.metadata["audio_inspected"] is False
        assert artifact.metadata["quality"]["structural_gate_passed"] is True
        assert artifact.metadata["quality"]["text_accuracy_passed"] is True
        assert artifact.metadata["quality"]["recommendation"] == "needs_review"
        content = await storage.get_bytes(artifact.metadata["storage_key"])
        cues = parse_srt(content)
        assert [cue.text for cue in cues] == ["第一句。", "第二句，继续说明！"]
        assert cues[-1].end_seconds == 4.0
        await storage.close()

    asyncio.run(exercise())


def test_asr_task_reads_narration_artifact_and_writes_timed_srt(tmp_path) -> None:
    async def exercise() -> None:
        store = InMemoryStore()
        queue = InProcessTaskQueue()
        storage = LocalFileArtifactStorage(tmp_path)
        service = SubtitleTaskService(
            store,
            queue,
            storage,
            asr_provider=MockASRProvider(),
        )
        queue.set_handler(service.run_task)
        episode = _episode(uuid4())
        await store.save_episodes([episode])

        source_audio = await storage.put_bytes(
            "audio/episodes/source.wav",
            b"mock-audio-bytes",
            "audio/wav",
        )
        source_task = GenerationTaskRecord(
            project_id=episode.project_id,
            kind=GenerationTaskKind.AUDIO_NARRATION,
            input_data={"episode_id": str(episode.id)},
            status=TaskStatus.SUCCEEDED,
            current_stage=None,
            artifacts=[
                ArtifactSummary(
                    type="audio_narration",
                    provider="mock",
                    metadata={
                        "content_type": source_audio.content_type,
                        "storage_key": source_audio.storage_key,
                        "duration_seconds": 3.0,
                    },
                )
            ],
        )
        await store.create_task(source_task)

        task, reused = await service.create_asr_task(
            episode.id,
            SubtitleASRCreateRequest(
                audio_artifact_id=source_task.artifacts[0].id,
                language="zh-CN",
                reference_text="第一句。第二句！",
            ),
            idempotency_key="asr-1",
        )
        await queue.close()

        assert reused is False
        saved_task = await store.get_task(task.id)
        assert saved_task is not None
        assert saved_task.kind == GenerationTaskKind.SUBTITLE_ASR
        assert saved_task.status == TaskStatus.SUCCEEDED
        assert saved_task.stages[0].stage == StageName.SUBTITLE
        artifact = saved_task.artifacts[0]
        assert artifact.provider == "mock_asr"
        assert artifact.metadata["alignment_method"] == "asr"
        assert artifact.metadata["alignment_precision"] == "mock_segment_estimate"
        assert artifact.metadata["audio_artifact_id"] == str(source_task.artifacts[0].id)
        assert artifact.metadata["quality"]["structural_gate_passed"] is True
        assert artifact.metadata["quality"]["text_accuracy_passed"] is True
        assert artifact.metadata["quality"]["reference_text_provided"] is True
        content = await storage.get_bytes(artifact.metadata["storage_key"])
        assert [cue.text for cue in parse_srt(content)] == ["第一句。", "第二句！"]
        await storage.close()

    asyncio.run(exercise())


def test_asr_task_persists_selected_provider_profile(tmp_path) -> None:
    async def exercise() -> None:
        store = InMemoryStore()
        queue = InProcessTaskQueue()
        storage = LocalFileArtifactStorage(tmp_path)
        registry = ASRProviderProfileRegistry(load_settings("config/config.example.toml"))
        service = SubtitleTaskService(
            store,
            queue,
            storage,
            asr_profile_registry=registry,
        )
        queue.set_handler(service.run_task)
        episode = _episode(uuid4())
        await store.save_episodes([episode])

        source_audio = await storage.put_bytes(
            "audio/episodes/profile-source.wav",
            b"mock-audio-bytes",
            "audio/wav",
        )
        source_task = GenerationTaskRecord(
            project_id=episode.project_id,
            kind=GenerationTaskKind.AUDIO_NARRATION,
            input_data={"episode_id": str(episode.id)},
            status=TaskStatus.SUCCEEDED,
            current_stage=None,
            artifacts=[
                ArtifactSummary(
                    type="audio_narration",
                    provider="mock",
                    metadata={
                        "content_type": source_audio.content_type,
                        "storage_key": source_audio.storage_key,
                        "duration_seconds": 3.0,
                    },
                )
            ],
        )
        await store.create_task(source_task)

        task, reused = await service.create_asr_task(
            episode.id,
            SubtitleASRCreateRequest(
                audio_artifact_id=source_task.artifacts[0].id,
                provider_profile_id="asr.mock",
                reference_text="配置档案字幕测试",
            ),
            idempotency_key="asr-profile-1",
        )
        await queue.close()

        assert reused is False
        saved_task = await store.get_task(task.id)
        assert saved_task is not None
        assert saved_task.input_data["provider_profile_id"] == "asr.mock"
        assert saved_task.status == TaskStatus.SUCCEEDED
        assert saved_task.artifacts[0].provider == "mock_asr"

        await registry.close()
        await storage.close()

    asyncio.run(exercise())


def test_asr_task_rejects_unconfigured_provider_profile(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AI_VIDEO_ASR_PROVIDER", "mock")
    monkeypatch.delenv("AI_VIDEO_ASR_API_KEY", raising=False)
    monkeypatch.delenv("AI_VIDEO_ASR_SILICONFLOW_API_KEY", raising=False)
    settings = load_settings("config/config.example.toml")
    registry = ASRProviderProfileRegistry(settings)

    async def exercise() -> None:
        store = InMemoryStore()
        queue = InProcessTaskQueue()
        storage = LocalFileArtifactStorage(tmp_path)
        service = SubtitleTaskService(
            store,
            queue,
            storage,
            asr_profile_registry=registry,
        )
        episode = _episode(uuid4())
        await store.save_episodes([episode])

        with pytest.raises(ASRProviderProfileError) as error:
            await service.create_asr_task(
                episode.id,
                SubtitleASRCreateRequest(
                    audio_artifact_id=uuid4(),
                    provider_profile_id="asr.siliconflow",
                ),
            )
        assert error.value.code == "PROVIDER_PROFILE_NOT_CONFIGURED"
        await queue.close()
        await storage.close()
        await registry.close()

    asyncio.run(exercise())
