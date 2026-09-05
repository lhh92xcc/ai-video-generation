from __future__ import annotations

import asyncio
from uuid import uuid4

from app.domain.models import (
    AudioNarrationCreateRequest,
    EpisodeOutlineContent,
    EpisodeRecord,
    GenerationTaskKind,
    TaskStatus,
    TTSGenerationRequest,
    TTSGenerationResult,
)
from app.media.audio_validation import FFprobeAudioValidator
from app.media.pronunciation import PronunciationDictionary
from app.providers.mock_tts import MockTTSProvider
from app.queue import InProcessTaskQueue
from app.repositories.in_memory import InMemoryStore
from app.services.tts_service import TTSTaskService
from app.storage.local import LocalFileArtifactStorage


class RecordingWordBoundaryProvider:
    def __init__(self) -> None:
        self.request: TTSGenerationRequest | None = None

    async def generate_speech(self, request: TTSGenerationRequest) -> TTSGenerationResult:
        self.request = request
        result = await MockTTSProvider().generate_speech(request)
        result.metadata = {
            **result.metadata,
            "word_boundaries": [
                {"start_seconds": 0.0, "end_seconds": 0.4, "text": "崇明"},
                {"start_seconds": 0.4, "end_seconds": 0.8, "text": "到了"},
            ],
            "timing_source": "test_word_boundary",
        }
        return result


def _episode() -> EpisodeRecord:
    return EpisodeRecord(
        project_id=uuid4(),
        story_bible_id=uuid4(),
        episode_number=1,
        outline=EpisodeOutlineContent(
            episode_number=1,
            title="第一集",
            logline="发现线索",
            objective="推进调查",
            conflict="线索被隐藏",
            turning_point="找到证据",
            ending_hook="有人跟踪",
            source_chapter_numbers=[1],
            target_duration_seconds=30,
        ),
        provider="test",
        model="test",
        duration_ms=0,
    )


def test_mock_tts_task_writes_playable_audio_artifact(tmp_path) -> None:
    async def exercise() -> None:
        store = InMemoryStore()
        queue = InProcessTaskQueue()
        storage = LocalFileArtifactStorage(tmp_path)
        service = TTSTaskService(
            store,
            queue,
            MockTTSProvider(),
            storage,
            audio_validator=FFprobeAudioValidator(),
        )
        queue.set_handler(service.run_task)
        project_id = uuid4()
        episode = EpisodeRecord(
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
        await store.save_episodes([episode])

        task, reused = await service.create_task(
            episode.id,
            AudioNarrationCreateRequest(text="这是一个用于测试的旁白。"),
            idempotency_key="narration-1",
        )
        await queue.close()

        assert reused is False
        saved_task = await store.get_task(task.id)
        assert saved_task is not None
        assert saved_task.kind == GenerationTaskKind.AUDIO_NARRATION
        assert saved_task.status == TaskStatus.SUCCEEDED
        assert saved_task.current_stage is None
        assert saved_task.artifacts[0].type == "audio_narration"
        assert saved_task.artifacts[0].metadata["content_type"] == "audio/wav"
        assert saved_task.artifacts[0].metadata["ffprobe"]["audio_stream_count"] == 1
        assert saved_task.artifacts[0].metadata["duration_seconds"] > 0
        storage_key = saved_task.artifacts[0].metadata["storage_key"]
        assert isinstance(storage_key, str)
        assert len(await storage.get_bytes(storage_key)) > 44
        await storage.close()

    asyncio.run(exercise())


def test_tts_task_stores_canonical_continuous_text(tmp_path) -> None:
    async def exercise() -> None:
        store = InMemoryStore()
        queue = InProcessTaskQueue()
        storage = LocalFileArtifactStorage(tmp_path)
        service = TTSTaskService(
            store,
            queue,
            MockTTSProvider(),
            storage,
            audio_validator=FFprobeAudioValidator(),
        )
        queue.set_handler(service.run_task)
        episode = EpisodeRecord(
            project_id=uuid4(),
            story_bible_id=uuid4(),
            episode_number=1,
            outline=EpisodeOutlineContent(
                episode_number=1,
                title="第一集",
                logline="发现线索",
                objective="推进调查",
                conflict="线索被隐藏",
                turning_point="找到证据",
                ending_hook="有人跟踪",
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
            AudioNarrationCreateRequest(text="第一句\n\n第二句\u2060"),
            idempotency_key="canonical-text",
        )
        assert reused is False
        assert task.input_data["text"] == "第一句 第二句"
        await queue.close()
        await storage.close()

    asyncio.run(exercise())


def test_tts_service_defaults_to_steady_continuous_narration_profile(tmp_path) -> None:
    service = TTSTaskService(
        InMemoryStore(),
        InProcessTaskQueue(),
        MockTTSProvider(),
        LocalFileArtifactStorage(tmp_path),
    )

    assert service._default_voice == "zh-CN-YunyangNeural"
    assert service._default_rate == "-35%"


def test_tts_provider_receives_pronunciation_text_and_artifact_keeps_source_text(tmp_path) -> None:
    async def exercise() -> None:
        store = InMemoryStore()
        queue = InProcessTaskQueue()
        storage = LocalFileArtifactStorage(tmp_path)
        provider = RecordingWordBoundaryProvider()
        service = TTSTaskService(
            store,
            queue,
            provider,
            storage,
            audio_validator=FFprobeAudioValidator(),
            pronunciation_dictionary=PronunciationDictionary.from_mapping({"重明": "崇明"}),
        )
        queue.set_handler(service.run_task)
        episode = _episode()
        await store.save_episodes([episode])

        task, reused = await service.create_task(
            episode.id,
            AudioNarrationCreateRequest(text="重明到了。"),
        )
        await queue.close()

        assert reused is False
        assert provider.request is not None
        assert provider.request.text == "崇明到了。"
        completed = await store.get_task(task.id)
        assert completed is not None
        assert completed.status == TaskStatus.SUCCEEDED
        metadata = completed.artifacts[0].metadata
        assert metadata["source_text"] == "重明到了。"
        assert metadata["tts_text"] == "崇明到了。"
        assert metadata["pronunciation_replacement_count"] == 1
        assert metadata["word_boundaries_text_basis"] == "source_text"
        assert metadata["word_boundaries_mapped"] is True
        assert [item["text"] for item in metadata["word_boundaries"]] == ["重明", "到了"]
        await storage.close()

    asyncio.run(exercise())
