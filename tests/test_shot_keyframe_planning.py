from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.domain.models import (
    AssetRecord,
    AssetStatus,
    AssetType,
    CharacterAssetContent,
    EpisodeOutlineContent,
    EpisodeRecord,
    EpisodeTaskPlanCreateRequest,
    GenerationTaskKind,
    GenerationTaskRecord,
    ReferenceImageCreateRequest,
    ReferenceImageRecord,
    ReferenceImageStatus,
    ShotAssetReference,
    ShotContent,
    TaskStatus,
    utc_now,
)
from app.media.visual_prompts import build_shot_keyframe_prompt
from app.repositories.in_memory import InMemoryStore
from app.services.episode_task_plan_service import EpisodeTaskPlanService


class RecordingReferenceImageTaskService:
    def __init__(self, project_id: UUID, *, supports_identity: bool) -> None:
        self.project_id = project_id
        self.supports_identity = supports_identity
        self.profile_ids: list[str | None] = []
        self.requests: list[tuple[UUID, ReferenceImageCreateRequest, str | None]] = []

    def supports_identity_variants(self, provider_profile_id: str | None = None) -> bool:
        self.profile_ids.append(provider_profile_id)
        return self.supports_identity

    async def create_task(
        self,
        asset_id: UUID,
        request: ReferenceImageCreateRequest,
        idempotency_key: str | None = None,
    ) -> tuple[GenerationTaskRecord, bool]:
        self.requests.append((asset_id, request, idempotency_key))
        return (
            GenerationTaskRecord(
                project_id=self.project_id,
                kind=GenerationTaskKind.ASSET_REFERENCE_IMAGE,
                input_data={
                    "asset_id": str(asset_id),
                    "asset_version": 1,
                    "reference_role": request.reference_role,
                    "episode_id": str(request.episode_id)
                    if request.episode_id is not None
                    else None,
                    "shot_index": request.shot_index,
                },
                status=TaskStatus.QUEUED,
            ),
            False,
        )


def _episode_asset_shot() -> tuple[EpisodeRecord, AssetRecord, ShotContent]:
    project_id = uuid4()
    story_bible_id = uuid4()
    episode = EpisodeRecord(
        project_id=project_id,
        story_bible_id=story_bible_id,
        episode_number=1,
        outline=EpisodeOutlineContent(
            episode_number=1,
            title="身份关键帧测试",
            logline="验证标准人设图和逐镜头关键帧的依赖顺序。",
            objective="创建关键帧",
            conflict="身份可能漂移",
            turning_point="锁定镜头构图",
            ending_hook="进入视频生成",
            source_chapter_numbers=[1],
            target_duration_seconds=30,
        ),
        provider="test",
        model="test",
        duration_ms=0,
    )
    asset = AssetRecord(
        project_id=project_id,
        story_bible_id=story_bible_id,
        asset_type=AssetType.CHARACTER,
        name="林默",
        status=AssetStatus.READY,
        content=CharacterAssetContent(
            role="protagonist",
            traits=["冷静", "坚定"],
            appearance="黑发、深色外套、左手旧表",
            voice_notes="克制低沉",
        ),
        provider="test",
        model="test",
        duration_ms=0,
    )
    shot = ShotContent(
        shot_index=1,
        scene_index=1,
        duration_seconds=3,
        shot_size="close_up",
        camera_movement="fixed",
        characters=[asset.name],
        location="雨夜街道",
        visual_prompt="林默在雨夜抬头看向旧信，情绪克制。",
        dialogue_refs=[],
        audio_requirements=["环境声"],
        asset_requirements=["character:林默"],
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
        continuity_notes="保持发型、外套颜色和雨夜侧光一致。",
    )
    return episode, asset, shot


def _standard_reference(episode: EpisodeRecord, asset: AssetRecord) -> ReferenceImageRecord:
    return ReferenceImageRecord(
        task_id=uuid4(),
        project_id=episode.project_id,
        asset_id=asset.id,
        asset_key=asset.asset_key,
        asset_type=asset.asset_type,
        asset_version=asset.version,
        prompt="标准人设图",
        negative_prompt="模糊、变脸",
        status=ReferenceImageStatus.SUCCEEDED,
        width=512,
        height=768,
        metadata={
            "storage_key": "references/standard.png",
            "content_type": "image/png",
            "identity_anchor": True,
            "reference_role": "standard_identity",
        },
        created_at=utc_now(),
    )


def _planner(store: InMemoryStore, reference_service: RecordingReferenceImageTaskService) -> EpisodeTaskPlanService:
    return EpisodeTaskPlanService(
        store,
        None,
        None,
        None,
        reference_service,
        None,
        None,
        None,
        None,
        None,
    )


def test_shot_keyframe_request_and_plan_modes_are_validated() -> None:
    with pytest.raises(ValidationError, match="shot_keyframe"):
        ReferenceImageCreateRequest(reference_role="shot_keyframe")

    with pytest.raises(ValidationError, match="identity_lock"):
        ReferenceImageCreateRequest(
            reference_role="shot_keyframe",
            episode_id=uuid4(),
            shot_index=1,
            identity_lock=False,
        )

    with pytest.raises(ValidationError):
        EpisodeTaskPlanCreateRequest(shot_keyframe_mode="unsupported")

    request = EpisodeTaskPlanCreateRequest(shot_keyframe_mode="always")
    assert request.shot_keyframe_mode == "always"


def test_shot_keyframe_prompt_keeps_shot_context_and_identity_facts() -> None:
    prompt = build_shot_keyframe_prompt(
        source_prompt="林默在雨夜抬头看向旧信",
        shot_size="close_up",
        camera_movement="fixed",
        location="雨夜街道",
        characters=["林默"],
        continuity_notes="保持侧光和外套颜色",
        primary_character_facts="name=林默; appearance=黑发、深色外套; traits=冷静",
    )

    assert "林默在雨夜抬头看向旧信" in prompt
    assert "雨夜街道" in prompt
    assert "close-up portrait composition" in prompt
    assert "Do not add any other character" in prompt
    assert "黑发、深色外套" in prompt
    assert "保持侧光和外套颜色" in prompt


def test_reference_plan_orders_standard_anchor_before_shot_keyframe() -> None:
    async def exercise() -> None:
        store = InMemoryStore()
        episode, asset, shot = _episode_asset_shot()
        await store.save_asset_version(asset)
        reference_service = RecordingReferenceImageTaskService(
            episode.project_id,
            supports_identity=True,
        )
        planner = _planner(store, reference_service)

        first = await planner._plan_reference_images(
            episode,
            [shot],
            [],
            "plan-order",
            None,
            None,
            "always",
        )
        assert first[0] is not None
        assert first[0].reason.startswith("标准参考图阶段")
        assert reference_service.requests[0][1].reference_role == "asset_anchor"

        standard = _standard_reference(episode, asset)
        await store.save_reference_image(standard)
        second = await planner._plan_reference_images(
            episode,
            [shot],
            [],
            "plan-order",
            None,
            None,
            "always",
        )
        assert second[0] is not None
        assert second[0].reason.startswith("逐镜头身份关键帧阶段")
        assert len(reference_service.requests) == 2
        keyframe_request = reference_service.requests[1][1]
        assert keyframe_request.reference_role == "shot_keyframe"
        assert keyframe_request.episode_id == episode.id
        assert keyframe_request.shot_index == shot.shot_index
        assert keyframe_request.identity_reference_image_id == standard.id
        assert keyframe_request.identity_lock is True

    asyncio.run(exercise())


def test_auto_keyframe_mode_falls_back_when_provider_has_no_identity_route() -> None:
    async def exercise() -> None:
        store = InMemoryStore()
        episode, asset, shot = _episode_asset_shot()
        standard = _standard_reference(episode, asset)
        await store.save_reference_image(standard)
        reference_service = RecordingReferenceImageTaskService(
            episode.project_id,
            supports_identity=False,
        )
        result = await _planner(store, reference_service)._plan_shot_keyframes(
            episode,
            [shot],
            {asset.asset_key: asset},
            {asset.asset_key: standard},
            [],
            "fallback",
            None,
            None,
            "auto",
        )

        assert result == (None, [], {}, [])
        assert reference_service.requests == []

    asyncio.run(exercise())


def test_always_keyframe_mode_blocks_when_provider_has_no_identity_route() -> None:
    async def exercise() -> None:
        store = InMemoryStore()
        episode, asset, shot = _episode_asset_shot()
        standard = _standard_reference(episode, asset)
        reference_service = RecordingReferenceImageTaskService(
            episode.project_id,
            supports_identity=False,
        )
        item, task_ids, keyframes, blocked = await _planner(
            store,
            reference_service,
        )._plan_shot_keyframes(
            episode,
            [shot],
            {asset.asset_key: asset},
            {asset.asset_key: standard},
            [],
            "blocked",
            None,
            None,
            "always",
        )

        assert item is not None
        assert item.action.value == "blocked"
        assert task_ids == []
        assert keyframes == {}
        assert blocked == ["SHOT_KEYFRAME_PROVIDER_UNSUPPORTED"]

    asyncio.run(exercise())


def test_legacy_reference_task_without_role_remains_a_standard_candidate() -> None:
    episode, asset, _ = _episode_asset_shot()
    legacy_task = GenerationTaskRecord(
        project_id=episode.project_id,
        kind=GenerationTaskKind.ASSET_REFERENCE_IMAGE,
        input_data={"asset_id": str(asset.id), "asset_version": asset.version},
        status=TaskStatus.SUCCEEDED,
    )

    selected = EpisodeTaskPlanService._latest_reference_task(
        [legacy_task],
        asset.id,
        asset.version,
        exclude_reference_role="shot_keyframe",
    )

    assert selected is legacy_task


def test_malformed_historical_shot_metadata_is_ignored_safely() -> None:
    episode, asset, _ = _episode_asset_shot()
    malformed = GenerationTaskRecord(
        project_id=episode.project_id,
        kind=GenerationTaskKind.ASSET_REFERENCE_IMAGE,
        input_data={
            "asset_id": str(asset.id),
            "asset_version": "not-a-number",
            "reference_role": "shot_keyframe",
            "episode_id": str(episode.id),
            "shot_index": "also-not-a-number",
        },
        status=TaskStatus.SUCCEEDED,
    )

    assert (
        EpisodeTaskPlanService._latest_reference_task(
            [malformed],
            asset.id,
            asset.version,
            reference_role="shot_keyframe",
            episode_id=episode.id,
            shot_index=1,
        )
        is None
    )
