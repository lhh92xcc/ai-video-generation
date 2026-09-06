from __future__ import annotations

import asyncio
import shutil
from uuid import uuid4

import pytest

from app.domain.models import (
    AssetRecord,
    AssetStatus,
    AssetType,
    CharacterAssetContent,
    EpisodeOutlineContent,
    EpisodeRecord,
    LocationAssetContent,
    ReferenceImageRecord,
    ReferenceImageStatus,
    ShotAssetReference,
    ShotContent,
    ShotListRecord,
    TaskStatus,
    VideoClipCreateRequest,
)
from app.providers.local_fixture_video import LocalFixtureVideoGenerationProvider
from app.providers.mock_video import MockVideoGenerationProvider
from app.providers.profiles import VisualProviderProfileRegistry
from app.queue import InProcessTaskQueue
from app.repositories.in_memory import InMemoryStore
from app.config import load_settings
from app.services.video_clip_service import (
    VideoClipAssetGateError,
    VideoClipTaskService,
)
from app.storage.local import LocalFileArtifactStorage


class RecordingIdentityAuditor:
    def __init__(self, status: str) -> None:
        self.status = status
        self.calls = 0

    async def audit(
        self,
        reference_bytes: bytes,
        reference_mime_type: str,
        video_bytes: bytes,
        video_mime_type: str,
    ) -> dict[str, object]:
        assert reference_bytes == b"reference-image"
        assert reference_mime_type == "image/png"
        assert video_bytes
        assert video_mime_type == "video/mp4"
        self.calls += 1
        return {
            "status": self.status,
            "min_similarity": 0.82,
            "human_review_required": self.status != "passed",
        }


async def _video_identity_fixture(store, storage, *, character: bool = True):
    project_id = uuid4()
    episode = EpisodeRecord(
        project_id=project_id,
        story_bible_id=uuid4(),
        episode_number=1,
        outline=EpisodeOutlineContent(
            episode_number=1,
            title="身份一致性测试集",
            logline="验证标准人设图绑定",
            objective="生成片段",
            conflict="身份可能漂移",
            turning_point="完成自动审核",
            ending_hook="保留人工审核入口",
            source_chapter_numbers=[1],
            target_duration_seconds=30,
        ),
        provider="test",
        model="test",
        duration_ms=0,
    )
    await store.save_episodes([episode])
    asset_type = AssetType.CHARACTER if character else AssetType.LOCATION
    asset_content = (
        CharacterAssetContent(
            role="protagonist",
            traits=["稳定"],
            appearance="黑发、深色外套",
        )
        if character
        else LocationAssetContent(description="一条雨夜街道")
    )
    asset = await store.save_asset_version(
        AssetRecord(
            project_id=project_id,
            story_bible_id=episode.story_bible_id,
            asset_type=asset_type,
            name="主角" if character else "雨夜街道",
            status=AssetStatus.READY,
            content=asset_content,
            provider="test",
            model="test",
            duration_ms=0,
        )
    )
    stored = await storage.put_bytes(
        f"references/{asset.id}.png",
        b"reference-image",
        "image/png",
    )
    reference_image = await store.save_reference_image(
        ReferenceImageRecord(
            task_id=uuid4(),
            project_id=project_id,
            asset_id=asset.id,
            asset_key=asset.asset_key,
            asset_type=asset.asset_type,
            asset_version=asset.version,
            prompt="标准人设图",
            negative_prompt="模糊",
            status=ReferenceImageStatus.SUCCEEDED,
            output_uri=stored.uri,
            width=512,
            height=512,
            metadata={
                "storage_key": stored.storage_key,
                "content_type": stored.content_type,
                "identity_anchor": character,
                "reference_role": "standard_identity" if character else "candidate_reference",
            },
        )
    )
    shot = ShotContent(
        shot_index=1,
        scene_index=1,
        duration_seconds=2,
        shot_size="medium",
        camera_movement="fixed",
        characters=["主角"] if character else [],
        location="雨夜街道",
        visual_prompt="主角在雨夜街道停下" if character else "雨夜街道的路灯倒影",
        dialogue_refs=[],
        audio_requirements=["环境声"],
        asset_requirements=[asset.name],
        asset_refs=[
            ShotAssetReference(
                asset_key=asset.asset_key,
                asset_type=asset.asset_type,
                name=asset.name,
                version=asset.version,
                status=AssetStatus.READY,
                match_kind="name",
                matched_text=asset.name,
            )
        ],
    )
    await store.save_shot_list(
        ShotListRecord(
            project_id=project_id,
            episode_id=episode.id,
            script_id=uuid4(),
            shots=[shot],
            provider="test",
            model="test",
            duration_ms=0,
        )
    )
    return episode, reference_image


def test_mock_video_clip_task_creates_traceable_artifact(tmp_path) -> None:
    async def exercise() -> None:
        store = InMemoryStore()
        queue = InProcessTaskQueue()
        storage = LocalFileArtifactStorage(tmp_path)
        service = VideoClipTaskService(
            store,
            queue,
            MockVideoGenerationProvider(),
            storage,
            provider_registry=VisualProviderProfileRegistry(
                load_settings("config/config.example.toml")
            ),
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
        shot_list = ShotListRecord(
            project_id=project_id,
            episode_id=episode.id,
            script_id=uuid4(),
            shots=[
                ShotContent(
                    shot_index=1,
                    scene_index=1,
                    duration_seconds=5,
                    shot_size="medium",
                    camera_movement="fixed",
                    characters=["主角"],
                    location="雨夜街道",
                    visual_prompt="主角在雨夜街道发现旧信",
                    dialogue_refs=[],
                    audio_requirements=["雨声"],
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
        await store.save_shot_list(shot_list)

        task, reused = await service.create_task(
            episode.id,
            1,
            VideoClipCreateRequest(provider_profile_id="video.mock"),
            idempotency_key="shot-1",
        )
        await queue.close()

        assert reused is False
        saved_task = await store.get_task(task.id)
        assert saved_task is not None
        assert saved_task.status.value == "succeeded"
        assert saved_task.current_stage is None
        assert saved_task.artifacts[0].type == "video_clip"
        assert saved_task.artifacts[0].metadata["shot_index"] == 1
        assert saved_task.input_data["provider_profile_id"] == "video.mock"
        assert saved_task.artifacts[0].metadata["provider_profile_id"] == "video.mock"
        assert saved_task.artifacts[0].metadata["output_uri"].startswith(
            "mock://video-clips/"
        )
        await storage.close()

    asyncio.run(exercise())


def test_video_clip_prefers_matching_shot_keyframe_over_standard_identity(tmp_path) -> None:
    async def exercise() -> None:
        store = InMemoryStore()
        queue = InProcessTaskQueue()
        storage = LocalFileArtifactStorage(tmp_path)
        service = VideoClipTaskService(
            store,
            queue,
            MockVideoGenerationProvider(),
            storage,
        )
        async def noop_handler(task_id) -> None:
            del task_id

        queue.set_handler(noop_handler)
        episode, standard = await _video_identity_fixture(store, storage)
        keyframe_artifact = await storage.put_bytes(
            f"references/{episode.id}/shot-1-keyframe.png",
            b"shot-keyframe",
            "image/png",
        )
        keyframe = standard.model_copy(
            update={
                "id": uuid4(),
                "task_id": uuid4(),
                "output_uri": keyframe_artifact.uri,
                "metadata": {
                    "storage_key": keyframe_artifact.storage_key,
                    "content_type": keyframe_artifact.content_type,
                    "identity_anchor": False,
                    "reference_role": "shot_keyframe",
                    "episode_id": str(episode.id),
                    "shot_index": 1,
                    "identity_anchor_reference_image_id": str(standard.id),
                },
            },
            deep=True,
        )
        await store.save_reference_image(keyframe)

        task, reused = await service.create_task(
            episode.id,
            1,
            VideoClipCreateRequest(),
            idempotency_key="matching-keyframe",
        )
        await queue.close()

        assert reused is False
        assert task.input_data["reference_image_id"] == str(keyframe.id)
        assert task.input_data["identity_anchor_reference_image_id"] == str(standard.id)
        await storage.close()

    asyncio.run(exercise())


def test_video_clip_prompt_captures_exact_approved_asset_facts(tmp_path) -> None:
    async def exercise() -> None:
        store = InMemoryStore()
        queue = InProcessTaskQueue()
        storage = LocalFileArtifactStorage(tmp_path)
        service = VideoClipTaskService(
            store,
            queue,
            MockVideoGenerationProvider(),
            storage,
        )

        async def noop_handler(task_id) -> None:
            del task_id

        queue.set_handler(noop_handler)
        episode, _reference = await _video_identity_fixture(store, storage)

        task, reused = await service.create_task(
            episode.id,
            1,
            VideoClipCreateRequest(),
            idempotency_key="asset-facts",
        )
        await queue.close()

        assert reused is False
        assert task.input_data["approved_asset_facts"] == [
            'character "主角" v1: appearance=黑发、深色外套; traits=稳定'
        ]
        assert "黑发、深色外套" in task.input_data["prompt"]
        assert "Approved asset design facts" in task.input_data["prompt"]
        await storage.close()

    asyncio.run(exercise())


def test_video_clip_task_requires_ready_shot_assets(tmp_path) -> None:
    async def exercise() -> None:
        store = InMemoryStore()
        queue = InProcessTaskQueue()
        storage = LocalFileArtifactStorage(tmp_path)
        service = VideoClipTaskService(
            store,
            queue,
            MockVideoGenerationProvider(),
            storage,
        )
        project_id = uuid4()
        episode = EpisodeRecord(
            project_id=project_id,
            story_bible_id=uuid4(),
            episode_number=1,
            outline=EpisodeOutlineContent(
                episode_number=1,
                title="第一集",
                logline="发现线索",
                objective="调查",
                conflict="阻碍",
                turning_point="转折",
                ending_hook="悬念",
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
                        duration_seconds=5,
                        shot_size="wide",
                        camera_movement="fixed",
                        characters=["主角"],
                        location="街道",
                        visual_prompt="主角站在街道",
                        dialogue_refs=[],
                        audio_requirements=["环境声"],
                        asset_requirements=["主角"],
                        unresolved_asset_requirements=["主角"],
                    )
                ],
                provider="test",
                model="test",
                duration_ms=0,
            )
        )

        with pytest.raises(VideoClipAssetGateError):
            await service.create_task(episode.id, 1, VideoClipCreateRequest())
        await storage.close()

    asyncio.run(exercise())


@pytest.mark.parametrize("audit_status", ["passed", "no_face", "failed", "error"])
def test_video_clip_artifact_persists_conservative_identity_audit_status(
    tmp_path,
    audit_status: str,
) -> None:
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("ffmpeg and ffprobe are required for identity audit integration tests")

    async def exercise() -> None:
        store = InMemoryStore()
        queue = InProcessTaskQueue()
        storage = LocalFileArtifactStorage(tmp_path / audit_status)
        auditor = RecordingIdentityAuditor(audit_status)
        service = VideoClipTaskService(
            store,
            queue,
            LocalFixtureVideoGenerationProvider(),
            storage,
            identity_auditor=auditor,
        )
        queue.set_handler(service.run_task)
        episode, reference_image = await _video_identity_fixture(store, storage)

        task, reused = await service.create_task(
            episode.id,
            1,
            VideoClipCreateRequest(),
        )
        assert reused is False
        assert task.input_data["reference_image_id"] == str(reference_image.id)
        assert task.input_data["identity_anchor_reference_image_id"] == str(reference_image.id)
        await queue.close()

        completed = await store.get_task(task.id)
        assert completed is not None
        assert completed.status == TaskStatus.SUCCEEDED
        assert auditor.calls == 1
        identity_audit = completed.artifacts[0].metadata["identity_audit"]
        assert identity_audit["status"] == audit_status
        assert identity_audit["human_review_required"] is (audit_status != "passed")
        if audit_status != "passed":
            assert identity_audit["status"] != "passed"
        await storage.close()

    asyncio.run(exercise())


def test_video_clip_without_character_marks_identity_audit_not_applicable(tmp_path) -> None:
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("ffmpeg and ffprobe are required for identity audit integration tests")

    async def exercise() -> None:
        store = InMemoryStore()
        queue = InProcessTaskQueue()
        storage = LocalFileArtifactStorage(tmp_path)
        auditor = RecordingIdentityAuditor("passed")
        service = VideoClipTaskService(
            store,
            queue,
            LocalFixtureVideoGenerationProvider(),
            storage,
            identity_auditor=auditor,
        )
        queue.set_handler(service.run_task)
        episode, _ = await _video_identity_fixture(store, storage, character=False)

        task, _ = await service.create_task(episode.id, 1, VideoClipCreateRequest())
        await queue.close()

        completed = await store.get_task(task.id)
        assert completed is not None
        identity_audit = completed.artifacts[0].metadata["identity_audit"]
        assert identity_audit["status"] == "not_applicable"
        assert identity_audit["human_review_required"] is False
        assert auditor.calls == 0
        await storage.close()

    asyncio.run(exercise())
