from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from app.domain.models import (
    ArtifactSummary,
    AudioTrackRequest,
    EpisodeOutlineContent,
    EpisodeRecord,
    GenerationTaskKind,
    GenerationTaskRecord,
    StageName,
    StageRun,
    TaskStatus,
    VideoAssemblyCreateRequest,
)
from app.media.video_validation import FFprobeVideoValidator
from app.queue import InProcessTaskQueue
from app.repositories.in_memory import InMemoryStore
from app.services.video_assembly_service import (
    VideoAssemblyInputError,
    VideoAssemblyTaskService,
)
from app.storage.local import LocalFileArtifactStorage


def _playable_mp4(tmp_path: Path, name: str, color: str) -> bytes:
    output = tmp_path / name
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"color=c={color}:s=160x90:r=10",
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


def _playable_wav(tmp_path: Path, name: str, duration: str) -> bytes:
    output = tmp_path / name
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=16000",
            "-t",
            duration,
            "-c:a",
            "pcm_s16le",
            str(output),
        ],
        check=True,
    )
    return output.read_bytes()


def test_video_assembly_accepts_single_clip_hardware_smoke() -> None:
    clip_task_id = uuid4()
    request = VideoAssemblyCreateRequest(clip_task_ids=[clip_task_id])

    assert request.clip_task_ids == [clip_task_id]


def _episode(project_id):
    return EpisodeRecord(
        project_id=project_id,
        story_bible_id=uuid4(),
        episode_number=1,
        outline=EpisodeOutlineContent(
            episode_number=1,
            title="样片集",
            logline="两个镜头组成一个小片段",
            objective="验证成片拼接",
            conflict="镜头需要保持连续",
            turning_point="片段成功合并",
            ending_hook="继续接入音频",
            source_chapter_numbers=[1],
            target_duration_seconds=30,
        ),
        provider="test",
        model="test",
        duration_ms=0,
    )


async def _seed_clip(
    store: InMemoryStore,
    storage: LocalFileArtifactStorage,
    episode: EpisodeRecord,
    content: bytes,
    shot_index: int,
    include_object: bool = True,
    status: TaskStatus = TaskStatus.SUCCEEDED,
) -> GenerationTaskRecord:
    storage_key = f"video-clips/{episode.id}/shot-{shot_index}/clip.mp4"
    if include_object:
        stored = await storage.put_bytes(storage_key, content, "video/mp4")
        metadata = {
            "storage_key": stored.storage_key,
            "content_type": stored.content_type,
            "size_bytes": stored.size_bytes,
            "sha256": stored.sha256,
        }
    else:
        metadata = {"storage_key": storage_key, "content_type": "video/mp4"}

    task = GenerationTaskRecord(
        project_id=episode.project_id,
        kind=GenerationTaskKind.VIDEO_CLIP,
        input_data={
            "episode_id": str(episode.id),
            "shot_list_id": str(uuid4()),
            "shot_index": shot_index,
            "duration_seconds": 1,
            "prompt": "test",
            "negative_prompt": "none",
            "asset_refs": [],
        },
        status=status,
        current_stage=None if status == TaskStatus.SUCCEEDED else StageName.VIDEO_CLIP,
        stages=[
            StageRun(
                stage=StageName.VIDEO_CLIP,
                status=status,
                progress=100 if status == TaskStatus.SUCCEEDED else 0,
            )
        ],
        artifacts=[
            ArtifactSummary(
                type="video_clip",
                provider="test",
                metadata=metadata,
            )
        ],
    )
    saved, _ = await store.create_task(task)
    return saved


async def _seed_audio(
    store: InMemoryStore,
    storage: LocalFileArtifactStorage,
    episode: EpisodeRecord,
    content: bytes,
    artifact_type: str = "audio_narration",
) -> UUID:
    storage_key = f"episode-audio/{episode.id}/{artifact_type}-{uuid4()}.wav"
    stored = await storage.put_bytes(storage_key, content, "audio/wav")
    task = GenerationTaskRecord(
        project_id=episode.project_id,
        kind=GenerationTaskKind.AUDIO_NARRATION,
        input_data={"episode_id": str(episode.id), "text": "test"},
        status=TaskStatus.SUCCEEDED,
        artifacts=[
            ArtifactSummary(
                type=artifact_type,
                provider="test",
                metadata={
                    "storage_key": stored.storage_key,
                    "content_type": stored.content_type,
                    "size_bytes": stored.size_bytes,
                    "sha256": stored.sha256,
                },
            )
        ],
    )
    saved, _ = await store.create_task(task)
    return saved.artifacts[0].id


def test_video_assembly_renders_and_registers_rendered_video(tmp_path: Path) -> None:
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg and ffprobe are required for assembly tests")

    async def exercise() -> None:
        store = InMemoryStore()
        queue = InProcessTaskQueue()
        storage = LocalFileArtifactStorage(tmp_path / "artifacts")
        service = VideoAssemblyTaskService(store, queue, storage)
        queue.set_handler(service.run_task)
        episode = _episode(uuid4())
        await store.save_episodes([episode])
        first = await _seed_clip(
            store,
            storage,
            episode,
            _playable_mp4(tmp_path, "one.mp4", "blue"),
            1,
        )
        second = await _seed_clip(
            store,
            storage,
            episode,
            _playable_mp4(tmp_path, "two.mp4", "red"),
            2,
        )

        task, reused = await service.create_task(
            episode.id,
            VideoAssemblyCreateRequest(clip_task_ids=[first.id, second.id]),
            idempotency_key="render-1",
        )
        await queue.close()

        assert reused is False
        completed = await store.get_task(task.id)
        assert completed is not None
        assert completed.status == TaskStatus.SUCCEEDED
        assert completed.current_stage is None
        assert completed.stages[0].stage == StageName.VIDEO_ASSEMBLY
        assert completed.artifacts[0].type == "rendered_video"
        assert completed.artifacts[0].metadata["input_clip_count"] == 2
        assert completed.artifacts[0].metadata["ffprobe"]["width"] == 160

        output_key = completed.artifacts[0].metadata["storage_key"]
        output = await storage.get_bytes(output_key)
        probe = await FFprobeVideoValidator().validate_bytes(output, "video/mp4")
        assert 1.7 <= probe.duration_seconds <= 2.3
        await storage.close()

    asyncio.run(exercise())


def test_video_assembly_accepts_lip_sync_task_as_shot_source(tmp_path: Path) -> None:
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg and ffprobe are required for assembly tests")

    async def exercise() -> None:
        store = InMemoryStore()
        queue = InProcessTaskQueue()
        storage = LocalFileArtifactStorage(tmp_path / "artifacts")
        service = VideoAssemblyTaskService(store, queue, storage)
        queue.set_handler(service.run_task)
        episode = _episode(uuid4())
        await store.save_episodes([episode])
        source = await _seed_clip(
            store,
            storage,
            episode,
            _playable_mp4(tmp_path, "lip-source.mp4", "blue"),
            1,
        )
        synced_stored = await storage.put_bytes(
            f"lip-sync/{episode.id}/synced.mp4",
            _playable_mp4(tmp_path, "lip-synced.mp4", "green"),
            "video/mp4",
        )
        lip_sync_task = GenerationTaskRecord(
            project_id=episode.project_id,
            kind=GenerationTaskKind.LIP_SYNC,
            input_data={
                "episode_id": str(episode.id),
                "video_artifact_id": str(source.artifacts[0].id),
                "audio_artifact_id": str(uuid4()),
            },
            status=TaskStatus.SUCCEEDED,
            artifacts=[
                ArtifactSummary(
                    type="lip_synced_video",
                    provider="musetalk-test",
                    metadata={
                        "episode_id": str(episode.id),
                        "video_artifact_id": str(source.artifacts[0].id),
                        "storage_key": synced_stored.storage_key,
                        "content_type": synced_stored.content_type,
                        "size_bytes": synced_stored.size_bytes,
                        "sha256": synced_stored.sha256,
                    },
                )
            ],
        )
        synced, _ = await store.create_task(lip_sync_task)
        fallback = await _seed_clip(
            store,
            storage,
            episode,
            _playable_mp4(tmp_path, "lip-fallback.mp4", "red"),
            2,
        )

        task, reused = await service.create_task(
            episode.id,
            VideoAssemblyCreateRequest(clip_task_ids=[synced.id, fallback.id]),
        )
        await queue.close()

        assert reused is False
        completed = await store.get_task(task.id)
        assert completed is not None
        assert completed.status == TaskStatus.SUCCEEDED
        metadata = completed.artifacts[0].metadata
        assert metadata["clip_task_ids"] == [str(synced.id), str(fallback.id)]
        assert metadata["input_clip_count"] == 2
        await storage.close()

    asyncio.run(exercise())


def test_video_assembly_requires_succeeded_source_tasks(tmp_path: Path) -> None:
    async def exercise() -> None:
        store = InMemoryStore()
        queue = InProcessTaskQueue()
        storage = LocalFileArtifactStorage(tmp_path / "artifacts")
        service = VideoAssemblyTaskService(store, queue, storage)
        episode = _episode(uuid4())
        await store.save_episodes([episode])
        queued = await _seed_clip(
            store,
            storage,
            episode,
            b"not-used",
            1,
            status=TaskStatus.QUEUED,
        )
        succeeded = await _seed_clip(store, storage, episode, b"not-used", 2)

        with pytest.raises(VideoAssemblyInputError) as error:
            await service.create_task(
                episode.id,
                VideoAssemblyCreateRequest(clip_task_ids=[queued.id, succeeded.id]),
            )
        assert error.value.code == "VIDEO_ASSEMBLY_SOURCE_NOT_READY"
        await storage.close()

    asyncio.run(exercise())


def test_video_assembly_mixes_audio_artifacts_on_timeline(tmp_path: Path) -> None:
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg and ffprobe are required for assembly tests")

    async def exercise() -> None:
        store = InMemoryStore()
        queue = InProcessTaskQueue()
        storage = LocalFileArtifactStorage(tmp_path / "artifacts")
        service = VideoAssemblyTaskService(store, queue, storage)
        queue.set_handler(service.run_task)
        episode = _episode(uuid4())
        await store.save_episodes([episode])
        first = await _seed_clip(
            store,
            storage,
            episode,
            _playable_mp4(tmp_path, "timeline-one.mp4", "blue"),
            1,
        )
        second = await _seed_clip(
            store,
            storage,
            episode,
            _playable_mp4(tmp_path, "timeline-two.mp4", "red"),
            2,
        )
        narration_id = await _seed_audio(
            store,
            storage,
            episode,
            _playable_wav(tmp_path, "narration.wav", duration="1"),
        )
        bgm_id = await _seed_audio(
            store,
            storage,
            episode,
            _playable_wav(tmp_path, "bgm.wav", duration="0.4"),
            artifact_type="audio_bgm",
        )

        task, reused = await service.create_task(
            episode.id,
            VideoAssemblyCreateRequest(
                clip_task_ids=[first.id, second.id],
                audio_tracks=[
                    AudioTrackRequest(artifact_id=narration_id),
                    AudioTrackRequest(
                        artifact_id=bgm_id,
                        track_type="bgm",
                        volume=0.12,
                        loop=True,
                        fade_in_seconds=0.1,
                        fade_out_seconds=0.2,
                    ),
                ],
            ),
        )
        await queue.close()

        assert reused is False
        completed = await store.get_task(task.id)
        assert completed is not None
        assert completed.status == TaskStatus.SUCCEEDED
        metadata = completed.artifacts[0].metadata
        assert metadata["audio_track_count"] == 2
        assert len(metadata["audio_tracks"]) == 2
        assert metadata["ffprobe"]["audio_stream_count"] == 1
        await storage.close()

    asyncio.run(exercise())


def test_video_assembly_rejects_missing_audio_artifact(tmp_path: Path) -> None:
    async def exercise() -> None:
        store = InMemoryStore()
        queue = InProcessTaskQueue()
        storage = LocalFileArtifactStorage(tmp_path / "artifacts")
        service = VideoAssemblyTaskService(store, queue, storage)
        episode = _episode(uuid4())
        await store.save_episodes([episode])
        first = await _seed_clip(store, storage, episode, b"not-used", 1)
        second = await _seed_clip(store, storage, episode, b"not-used", 2)

        with pytest.raises(VideoAssemblyInputError) as error:
            await service.create_task(
                episode.id,
                VideoAssemblyCreateRequest(
                    clip_task_ids=[first.id, second.id],
                    audio_tracks=[AudioTrackRequest(artifact_id=uuid4())],
                ),
            )
        assert error.value.code == "VIDEO_ASSEMBLY_AUDIO_ARTIFACT_NOT_FOUND"
        await storage.close()

    asyncio.run(exercise())


def test_video_assembly_failure_keeps_structured_storage_error(tmp_path: Path) -> None:
    async def exercise() -> None:
        store = InMemoryStore()
        queue = InProcessTaskQueue()
        storage = LocalFileArtifactStorage(tmp_path / "artifacts")
        service = VideoAssemblyTaskService(store, queue, storage)
        queue.set_handler(service.run_task)
        episode = _episode(uuid4())
        await store.save_episodes([episode])
        first = await _seed_clip(store, storage, episode, b"not-used", 1, include_object=False)
        second = await _seed_clip(store, storage, episode, b"not-used", 2)

        task, _ = await service.create_task(
            episode.id,
            VideoAssemblyCreateRequest(clip_task_ids=[first.id, second.id]),
        )
        await queue.close()

        failed = await store.get_task(task.id)
        assert failed is not None
        assert failed.status == TaskStatus.FAILED
        assert failed.error is not None
        assert failed.error.code == "STORAGE_NOT_FOUND"
        await storage.close()

    asyncio.run(exercise())
