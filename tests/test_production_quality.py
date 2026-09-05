from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path
from uuid import uuid4

from app.domain.models import (
    ArtifactSummary,
    AssetRecord,
    AssetStatus,
    AssetType,
    AudioNarrationCreateRequest,
    AudioNarrationLineRequest,
    CharacterAssetContent,
    EpisodeOutlineContent,
    EpisodeRecord,
    GenerationTaskKind,
    GenerationTaskRecord,
    IdentityCalibrationRequest,
    IdentityRetryRequest,
    LipSyncCreateRequest,
    NovelProjectRecord,
    StageName,
    StageRun,
    TaskStatus,
    VoiceAssetCreateRequest,
)
from app.media.audio_validation import FFprobeAudioValidator
from app.media.identity_calibration import IdentityCalibrationService
from app.media.video_validation import FFprobeVideoValidator
from app.providers.lip_sync import MockLipSyncProvider
from app.providers.mock_tts import MockTTSProvider
from app.queue import InProcessTaskQueue
from app.repositories.in_memory import InMemoryStore
from app.services.identity_retry_service import IdentityAuditRetryService
from app.services.task_batch_service import TaskBatchService
from app.services.tts_service import TTSTaskService
from app.services.voice_asset_service import VoiceAssetService
from app.services.lip_sync_service import LipSyncTaskService
from app.storage.local import LocalFileArtifactStorage


def _episode(project_id):
    return EpisodeRecord(
        project_id=project_id,
        story_bible_id=uuid4(),
        episode_number=1,
        outline=EpisodeOutlineContent(
            episode_number=1,
            title="生产质量测试集",
            logline="验证身份、声音和唇形任务",
            objective="完成质量闭环",
            conflict="不同 Provider 需要隔离",
            turning_point="任务可以恢复",
            ending_hook="保留人工审核",
            source_chapter_numbers=[1],
            target_duration_seconds=30,
        ),
        provider="test",
        model="test",
        duration_ms=0,
    )


def _identity_task(
    project_id,
    episode_id,
    shot_index: int,
    attempt: int,
    status: str,
    *,
    updated_at=None,
) -> GenerationTaskRecord:
    task_data = {
        "project_id": project_id,
        "kind": GenerationTaskKind.VIDEO_CLIP,
        "input_data": {
            "episode_id": str(episode_id),
            "shot_index": shot_index,
            "generation_attempt": attempt,
            "prompt": "测试镜头",
            "negative_prompt": "模糊",
        },
        "status": TaskStatus.SUCCEEDED,
        "current_stage": None,
        "progress": 100,
        "stages": [StageRun(stage=StageName.VIDEO_CLIP, status=TaskStatus.SUCCEEDED, progress=100)],
        "artifacts": [
            ArtifactSummary(
                type="video_clip",
                provider="test",
                metadata={
                    "episode_id": str(episode_id),
                    "shot_index": shot_index,
                    "identity_audit": {
                        "status": status,
                        "min_similarity": 0.42 if status == "failed" else 0.82,
                    },
                },
            )
        ],
    }
    if updated_at is not None:
        task_data["updated_at"] = updated_at
    return GenerationTaskRecord(
        **task_data,
    )


def test_identity_calibration_compares_thresholds_without_mutating_configuration() -> None:
    async def exercise() -> None:
        store = InMemoryStore()
        project_id = uuid4()
        episode_id = uuid4()
        for index, score in enumerate((0.31, 0.42, 0.67), 1):
            task = _identity_task(project_id, episode_id, index, 1, "passed")
            task.artifacts[0].metadata["identity_audit"]["min_similarity"] = score
            await store.create_task(task)
        unaudited = _identity_task(project_id, episode_id, 4, 1, "unavailable")
        unaudited.artifacts[0].metadata["identity_audit"].pop("min_similarity")
        await store.create_task(unaudited)

        response = await IdentityCalibrationService(store).calibrate(
            project_id,
            IdentityCalibrationRequest(episode_id=episode_id, thresholds=[0.3, 0.5]),
            current_threshold=0.4,
        )

        assert response.current_threshold == 0.4
        assert response.artifact_count == 4
        assert response.audited_artifact_count == 4
        assert response.eligible_sample_count == 3
        assert response.excluded_artifact_count == 1
        assert response.status_counts == {"passed": 3, "unavailable": 1}
        assert [(item.threshold, item.passed_count) for item in response.thresholds] == [
            (0.3, 3),
            (0.5, 1),
        ]

    asyncio.run(exercise())


def test_identity_retry_is_attempt_ordered_and_idempotent() -> None:
    async def exercise() -> None:
        store = InMemoryStore()
        project = NovelProjectRecord(title="重试项目", language="zh-CN", target_episode_count=1, target_episode_duration_seconds=30)
        await store.create_novel_project(project)
        episode = _episode(project.id)
        await store.save_episodes([episode])

        old = _identity_task(project.id, episode.id, 1, 1, "failed")
        newer = _identity_task(project.id, episode.id, 1, 2, "failed")
        await store.create_task(old)
        await store.create_task(newer)

        queue = InProcessTaskQueue()

        async def noop(_task_id) -> None:
            return None

        queue.set_handler(noop)
        service = IdentityAuditRetryService(
            store,
            queue,
            TaskBatchService(store, lambda _task_id: asyncio.sleep(0)),
        )
        request = IdentityRetryRequest(episode_id=episode.id, max_tasks=10)
        first = await service.retry(project.id, request, idempotency_key="retry-once")
        await queue.close()

        assert first.batch is not None
        assert len(first.retried_task_ids) == 1
        retried = await store.get_task(first.retried_task_ids[0])
        assert retried is not None
        assert retried.input_data["generation_attempt"] == 3
        assert retried.input_data["retry_of_task_id"] == str(newer.id)

        second = await service.retry(project.id, request, idempotency_key="retry-once")
        assert second.batch is not None
        assert second.batch.id == first.batch.id
        assert second.retried_task_ids == first.retried_task_ids

    asyncio.run(exercise())


def test_multi_voice_tts_uses_bound_voice_assets_and_ffmpeg_concat(tmp_path: Path) -> None:
    async def exercise() -> None:
        store = InMemoryStore()
        project = NovelProjectRecord(title="多角色项目", language="zh-CN", target_episode_count=1, target_episode_duration_seconds=30)
        await store.create_novel_project(project)
        episode = _episode(project.id)
        await store.save_episodes([episode])
        character = await store.save_asset_version(
            AssetRecord(
                project_id=project.id,
                story_bible_id=episode.story_bible_id,
                asset_type=AssetType.CHARACTER,
                name="林默",
                status=AssetStatus.READY,
                content=CharacterAssetContent(role="主角", appearance="黑发"),
                provider="test",
                model="test",
                duration_ms=0,
            )
        )
        voice_asset = await VoiceAssetService(store).create(
            project.id,
            VoiceAssetCreateRequest(
                character_asset_id=character.id,
                label="林默",
                provider="mock",
                voice="mock-linmo",
            ),
        )
        await VoiceAssetService(store).create(
            project.id,
            VoiceAssetCreateRequest(
                label="旁白",
                provider="mock",
                voice="mock-narrator",
            ),
        )

        queue = InProcessTaskQueue()
        storage = LocalFileArtifactStorage(tmp_path / "artifacts")
        service = TTSTaskService(
            store,
            queue,
            MockTTSProvider(),
            storage,
            audio_validator=FFprobeAudioValidator(),
            voice_asset_service=VoiceAssetService(store),
            configured_provider="mock",
            multi_voice_ffmpeg_binary="ffmpeg",
        )
        queue.set_handler(service.run_task)
        task, reused = await service.create_task(
            episode.id,
            AudioNarrationCreateRequest(
                voice_lines=[
                    AudioNarrationLineRequest(line_index=1, speaker="林默", text="我找到线索了。", voice_asset_id=voice_asset.id),
                    AudioNarrationLineRequest(line_index=2, speaker="旁白", text="雨夜里，灯光忽然熄灭。"),
                ]
            ),
        )
        await queue.close()

        assert reused is False
        saved = await store.get_task(task.id)
        assert saved is not None
        assert saved.status == TaskStatus.SUCCEEDED
        artifact = saved.artifacts[0]
        assert artifact.metadata["synthesis_mode"] == "multi_voice_segmented"
        assert artifact.metadata["independent_waveform_count"] == 2
        assert len(artifact.metadata["voice_lines"]) == 2
        assert artifact.metadata["voice_lines"][0]["voice_asset_id"] == str(voice_asset.id)
        assert artifact.metadata["ffprobe"]["audio_stream_count"] == 1
        assert artifact.metadata["duration_seconds"] > 1
        await storage.close()

    asyncio.run(exercise())


def test_mock_lip_sync_creates_assembly_compatible_artifact(tmp_path: Path) -> None:
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        import pytest

        pytest.skip("ffmpeg and ffprobe are required for MuseTalk task tests")

    async def exercise() -> None:
        video_path = tmp_path / "input.mp4"
        audio_path = tmp_path / "input.wav"
        subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "color=c=blue:s=160x90:r=10", "-t", "1",
                "-pix_fmt", "yuv420p", str(video_path),
            ],
            check=True,
        )
        subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=16000", "-t", "1",
                "-c:a", "pcm_s16le", str(audio_path),
            ],
            check=True,
        )
        store = InMemoryStore()
        project = NovelProjectRecord(title="唇形项目", language="zh-CN", target_episode_count=1, target_episode_duration_seconds=30)
        await store.create_novel_project(project)
        episode = _episode(project.id)
        await store.save_episodes([episode])
        storage = LocalFileArtifactStorage(tmp_path / "artifacts")
        video = await storage.put_bytes("inputs/video.mp4", video_path.read_bytes(), "video/mp4")
        audio = await storage.put_bytes("inputs/audio.wav", audio_path.read_bytes(), "audio/wav")
        video_task = GenerationTaskRecord(
            project_id=project.id,
            kind=GenerationTaskKind.VIDEO_CLIP,
            input_data={"episode_id": str(episode.id), "shot_index": 1},
            status=TaskStatus.SUCCEEDED,
            artifacts=[ArtifactSummary(type="video_clip", provider="test", metadata={"storage_key": video.storage_key, "content_type": video.content_type})],
        )
        audio_task = GenerationTaskRecord(
            project_id=project.id,
            kind=GenerationTaskKind.AUDIO_NARRATION,
            input_data={"episode_id": str(episode.id)},
            status=TaskStatus.SUCCEEDED,
            artifacts=[ArtifactSummary(type="audio_narration", provider="test", metadata={"storage_key": audio.storage_key, "content_type": audio.content_type})],
        )
        await store.create_task(video_task)
        await store.create_task(audio_task)
        queue = InProcessTaskQueue()
        service = LipSyncTaskService(store, queue, MockLipSyncProvider(), storage, FFprobeVideoValidator())
        queue.set_handler(service.run_task)
        task, reused = await service.create_task(
            episode.id,
            LipSyncCreateRequest(video_artifact_id=video_task.artifacts[0].id, audio_artifact_id=audio_task.artifacts[0].id),
        )
        await queue.close()

        assert reused is False
        saved = await store.get_task(task.id)
        assert saved is not None
        assert saved.status == TaskStatus.SUCCEEDED
        assert saved.artifacts[0].type == "lip_synced_video"
        assert saved.artifacts[0].metadata["inference"] == "mock_passthrough"
        await storage.close()

    asyncio.run(exercise())
