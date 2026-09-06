from __future__ import annotations

import asyncio
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import delete, inspect

from app.db import ArtifactRow, ShotListRow, create_engine, create_session_factory, init_db
from app.domain.models import (
    AssetRecord,
    AssetReviewRecord,
    AssetStatus,
    AssetType,
    AuditAction,
    AuditEntityType,
    AuditLogRecord,
    ArtifactSummary,
    CharacterAssetContent,
    ChapterRecord,
    DialogueLine,
    EpisodeOutlineContent,
    EpisodeRecord,
    EpisodeScriptContent,
    EpisodeScriptRecord,
    EpisodeStatus,
    GenerationTaskKind,
    GenerationTaskRecord,
    NovelProjectRecord,
    ProjectInvitationRecord,
    ProjectInvitationStatus,
    NovelSourceRecord,
    ProjectCreateRequest,
    ProjectMemberRecord,
    ProjectRole,
    ProjectRecord,
    ReferenceImageRecord,
    ReferenceImageStatus,
    RightsStatus,
    SceneScriptContent,
    ShotContent,
    ShotListRecord,
    StageName,
    StageRun,
    TaskBatchRecord,
    StoryBibleContent,
    StoryBibleRecord,
    TaskStatus,
    utc_now,
)
from app.repositories.sqlalchemy import SqlAlchemyStore


def test_sqlalchemy_store_round_trip_and_idempotency() -> None:
    async def exercise() -> None:
        engine = create_engine("sqlite+aiosqlite:///:memory:")
        await init_db(engine)
        store = SqlAlchemyStore(create_session_factory(engine))

        project_request = ProjectCreateRequest(title="持久化测试", topic="SQLAlchemy Repository")
        project = ProjectRecord(
            title=project_request.title,
            topic=project_request.topic,
            language=project_request.language,
            target_duration_seconds=project_request.target_duration_seconds,
            aspect_ratio=project_request.aspect_ratio,
            tone=project_request.tone,
        )
        saved_project = await store.create_project(project)
        loaded_project = await store.get_project(saved_project.id)

        assert loaded_project is not None
        assert loaded_project.topic == "SQLAlchemy Repository"

        task = GenerationTaskRecord(project_id=saved_project.id)
        task.status = TaskStatus.QUEUED
        task.stages = [StageRun(stage=StageName.SCRIPT, status=TaskStatus.QUEUED)]
        task.artifacts = [
            ArtifactSummary(
                type="script_json",
                provider="test",
                metadata={"storage_key": "scripts/test.json"},
            )
        ]
        first, first_reused = await store.create_task(task, "persistent-key")
        second, second_reused = await store.create_task(
            GenerationTaskRecord(project_id=saved_project.id), "persistent-key"
        )
        loaded_task = await store.get_task(first.id)

        assert first_reused is False
        assert second_reused is True
        assert second.id == first.id
        assert loaded_task is not None
        assert loaded_task.stages[0].stage == StageName.SCRIPT
        assert loaded_task.kind == GenerationTaskKind.INFO_SCRIPT
        loaded_artifact = await store.get_artifact(task.artifacts[0].id)
        assert loaded_artifact is not None
        assert loaded_artifact.task_id == first.id
        assert loaded_artifact.metadata["storage_key"] == "scripts/test.json"

        listed_tasks = await store.list_tasks(
            project_id=saved_project.id,
            kind=GenerationTaskKind.INFO_SCRIPT.value,
            status=TaskStatus.QUEUED.value,
        )
        assert [item.id for item in listed_tasks] == [first.id]

        listed_artifacts = await store.list_artifacts(
            project_id=saved_project.id,
            artifact_type="script_json",
        )
        assert [item.id for item in listed_artifacts] == [task.artifacts[0].id]

        batch = TaskBatchRecord(
            project_id=saved_project.id,
            task_ids=[first.id],
            label="持久化批次",
            total_count=1,
        )
        saved_batch, batch_reused = await store.create_task_batch(batch, "batch-key")
        loaded_batch = await store.get_task_batch(saved_batch.id)
        same_batch, same_batch_reused = await store.create_task_batch(
            batch.model_copy(update={"id": uuid4()}), "batch-key"
        )
        assert batch_reused is False
        assert same_batch_reused is True
        assert same_batch.id == saved_batch.id
        assert loaded_batch is not None
        assert loaded_batch.task_ids == [first.id]

        novel_task = GenerationTaskRecord(
            project_id=saved_project.id,
            kind=GenerationTaskKind.NOVEL_EPISODE_PLAN,
            input_data={"target_episode_count": 3},
            current_stage=StageName.EPISODE_PLAN,
            stages=[StageRun(stage=StageName.EPISODE_PLAN, status=TaskStatus.QUEUED)],
            status=TaskStatus.QUEUED,
        )
        saved_novel_task, _ = await store.create_task(novel_task, "novel-plan-key")
        loaded_novel_task = await store.get_task(saved_novel_task.id)
        assert loaded_novel_task is not None
        assert loaded_novel_task.kind == GenerationTaskKind.NOVEL_EPISODE_PLAN
        assert loaded_novel_task.input_data["target_episode_count"] == 3

        await engine.dispose()

    asyncio.run(exercise())


def test_sqlalchemy_store_round_trips_audit_log() -> None:
    async def exercise() -> None:
        engine = create_engine("sqlite+aiosqlite:///:memory:")
        await init_db(engine)
        store = SqlAlchemyStore(create_session_factory(engine))
        project = await store.create_novel_project(
            NovelProjectRecord(
                title="审计持久化",
                language="zh-CN",
                target_episode_count=1,
                target_episode_duration_seconds=30,
            )
        )
        log = AuditLogRecord(
            project_id=project.id,
            episode_id=uuid4(),
            entity_type=AuditEntityType.EPISODE_SCRIPT,
            entity_id=uuid4(),
            action=AuditAction.SCRIPT_VERSION_PUBLISHED,
            actor_id="operator-1",
            actor_name="Operator One",
            metadata={"version": 2, "expected_version": 1},
        )
        saved = await store.save_audit_log(log)
        loaded = await store.list_audit_logs(project.id)
        filtered = await store.list_audit_logs(
            project.id,
            entity_type=AuditEntityType.EPISODE_SCRIPT,
            entity_id=log.entity_id,
        )

        assert saved.id == log.id
        assert loaded[0].action == AuditAction.SCRIPT_VERSION_PUBLISHED
        assert loaded[0].metadata["version"] == 2
        assert filtered[0].entity_id == log.entity_id
        await engine.dispose()

    asyncio.run(exercise())


def test_sqlalchemy_store_round_trips_novel_project_member() -> None:
    async def exercise() -> None:
        engine = create_engine("sqlite+aiosqlite:///:memory:")
        await init_db(engine)
        store = SqlAlchemyStore(create_session_factory(engine))
        project = await store.create_novel_project(
            NovelProjectRecord(
                title="成员持久化",
                language="zh-CN",
                target_episode_count=1,
                target_episode_duration_seconds=30,
            )
        )
        saved = await store.upsert_novel_project_member(
            ProjectMemberRecord(
                project_id=project.id,
                actor_id="reviewer-1",
                actor_name="Reviewer One",
                role=ProjectRole.REVIEWER,
            )
        )
        loaded = await store.get_novel_project_member(project.id, "reviewer-1")
        members = await store.list_novel_project_members(project.id)

        assert loaded is not None
        assert loaded.id == saved.id
        assert loaded.role == ProjectRole.REVIEWER
        assert [item.actor_id for item in members] == ["reviewer-1"]
        await store.delete_novel_project_member(project.id, "reviewer-1")
        assert await store.get_novel_project_member(project.id, "reviewer-1") is None
        await engine.dispose()

    asyncio.run(exercise())


def test_sqlalchemy_store_round_trips_novel_project_invitation() -> None:
    async def exercise() -> None:
        engine = create_engine("sqlite+aiosqlite:///:memory:")
        await init_db(engine)
        store = SqlAlchemyStore(create_session_factory(engine))
        project = await store.create_novel_project(
            NovelProjectRecord(
                title="邀请持久化",
                language="zh-CN",
                target_episode_count=1,
                target_episode_duration_seconds=30,
            )
        )
        invitation = await store.create_novel_project_invitation(
            ProjectInvitationRecord(
                project_id=project.id,
                invitee_actor_id="invitee-1",
                invitee_name="Invitee One",
                role=ProjectRole.EDITOR,
                token_hash="a" * 64,
                expires_at=utc_now() + timedelta(hours=1),
                invited_by_actor_id="owner-1",
                invited_by_name="Owner One",
            )
        )
        loaded = await store.get_novel_project_invitation(invitation.id)
        listed = await store.list_novel_project_invitations(project.id)
        updated = await store.update_novel_project_invitation(
            invitation.model_copy(update={"status": ProjectInvitationStatus.REVOKED})
        )

        assert loaded is not None
        assert loaded.token_hash == "a" * 64
        assert listed[0].invitee_actor_id == "invitee-1"
        assert updated.status == ProjectInvitationStatus.REVOKED
        await engine.dispose()

    asyncio.run(exercise())


def test_sqlalchemy_store_consumes_invitation_and_member_once() -> None:
    async def exercise() -> None:
        engine = create_engine("sqlite+aiosqlite:///:memory:")
        await init_db(engine)
        store = SqlAlchemyStore(create_session_factory(engine))
        project = await store.create_novel_project(
            NovelProjectRecord(
                title="邀请原子接受",
                language="zh-CN",
                target_episode_count=1,
                target_episode_duration_seconds=30,
            )
        )
        token_hash = "b" * 64
        invitation = await store.create_novel_project_invitation(
            ProjectInvitationRecord(
                project_id=project.id,
                invitee_actor_id="atomic-invitee",
                invitee_name="Atomic Invitee",
                role=ProjectRole.REVIEWER,
                token_hash=token_hash,
                expires_at=utc_now() + timedelta(hours=1),
                invited_by_actor_id="atomic-owner",
                invited_by_name="Atomic Owner",
            )
        )

        first = await store.accept_novel_project_invitation(
            invitation.id,
            token_hash,
            "atomic-invitee",
            utc_now(),
        )
        second = await store.accept_novel_project_invitation(
            invitation.id,
            token_hash,
            "atomic-invitee",
            utc_now(),
        )

        assert first is not None
        assert first[0].status == ProjectInvitationStatus.ACCEPTED
        assert first[1].role == ProjectRole.REVIEWER
        assert second is None
        members = await store.list_novel_project_members(project.id)
        assert len(members) == 1
        loaded = await store.get_novel_project_invitation(invitation.id)
        assert loaded is not None
        assert loaded.status == ProjectInvitationStatus.ACCEPTED
        await engine.dispose()

    asyncio.run(exercise())


def test_sqlalchemy_store_backfills_legacy_artifact_snapshot() -> None:
    async def exercise() -> None:
        engine = create_engine("sqlite+aiosqlite:///:memory:")
        await init_db(engine)
        session_factory = create_session_factory(engine)
        store = SqlAlchemyStore(session_factory)
        project = await store.create_project(
            ProjectRecord(
                title="旧产物兼容",
                topic="legacy artifact snapshot",
                language="zh-CN",
                target_duration_seconds=15,
                aspect_ratio="9:16",
                tone="清晰",
            )
        )
        artifact = ArtifactSummary(
            type="script_json",
            provider="legacy",
            metadata={"storage_key": "scripts/legacy.json"},
        )
        task = GenerationTaskRecord(project_id=project.id, artifacts=[artifact])
        await store.create_task(task)

        async with session_factory() as session:
            await session.execute(delete(ArtifactRow).where(ArtifactRow.id == artifact.id))
            await session.commit()

        loaded = await store.get_artifact(artifact.id)
        assert loaded is not None
        assert loaded.task_id == task.id
        assert loaded.provider == "legacy"
        assert loaded.metadata["storage_key"] == "scripts/legacy.json"
        await engine.dispose()

    asyncio.run(exercise())


def test_sqlalchemy_store_persists_novel_source_chapters_and_story_bible() -> None:
    async def exercise() -> None:
        engine = create_engine("sqlite+aiosqlite:///:memory:")
        await init_db(engine)
        store = SqlAlchemyStore(create_session_factory(engine))

        project = await store.create_novel_project(
            NovelProjectRecord(title="小说持久化", language="zh-CN", target_episode_count=3, target_episode_duration_seconds=90)
        )
        source = NovelSourceRecord(
            project_id=project.id,
            filename="novel.txt",
            content_type="text/plain",
            size_bytes=4,
            checksum="a" * 64,
            content="第一章\n内容",
            rights_status=RightsStatus.PENDING,
            chapter_count=1,
        )
        chapter = ChapterRecord(
            source_id=source.id,
            chapter_number=1,
            title="第一章",
            content="内容",
            start_offset=0,
            end_offset=5,
        )
        saved_source, saved_chapters = await store.create_novel_source(source, [chapter])
        project.source_id = saved_source.id
        await store.update_novel_project(project)

        story_bible = StoryBibleRecord(
            project_id=project.id,
            source_id=saved_source.id,
            content=StoryBibleContent(
                title="小说持久化",
                logline="测试故事",
                genre=["测试"],
                setting="测试世界",
                themes=["成长"],
                characters=[{"name": "主角", "role": "protagonist"}],
                locations=[{"name": "城市", "description": "测试地点"}],
                timeline=["第一章"],
                conflicts=["测试冲突"],
                source_chapter_numbers=[1],
            ),
            provider="mock",
            model="test",
            duration_ms=1,
        )
        saved_bible = await store.save_story_bible(story_bible)
        loaded_source = await store.get_novel_source(saved_source.id)
        loaded_chapters = await store.list_chapters(saved_source.id)
        loaded_bible = await store.get_latest_story_bible(project.id)

        assert loaded_source is not None
        assert loaded_source.rights_status == RightsStatus.PENDING
        assert len(saved_chapters) == 1
        assert loaded_chapters[0].title == "第一章"
        assert loaded_bible is not None
        assert loaded_bible.id == saved_bible.id
        assert loaded_bible.content.characters[0].name == "主角"

        await engine.dispose()

        asyncio.run(exercise())


def test_sqlalchemy_store_round_trips_reference_image() -> None:
    async def exercise() -> None:
        engine = create_engine("sqlite+aiosqlite:///:memory:")
        await init_db(engine)
        store = SqlAlchemyStore(create_session_factory(engine))
        project_id = uuid4()
        asset_id = uuid4()
        asset_key = uuid4()
        task_id = uuid4()
        image = ReferenceImageRecord(
            task_id=task_id,
            project_id=project_id,
            asset_id=asset_id,
            asset_key=asset_key,
            asset_type=AssetType.CHARACTER,
            asset_version=2,
            prompt="cinematic character reference",
            negative_prompt="blurry",
            provider="mock",
            model="mock-reference-v1",
            status=ReferenceImageStatus.SUCCEEDED,
            output_uri="mock://reference-images/example.png",
            width=1024,
            height=1024,
            duration_ms=12,
            metadata={"placeholder": True},
        )
        saved = await store.save_reference_image(image)
        loaded = await store.get_reference_image(saved.id)
        listed = await store.list_reference_images(asset_id)

        assert loaded is not None
        assert loaded.asset_key == asset_key
        assert loaded.asset_version == 2
        assert loaded.output_uri == "mock://reference-images/example.png"
        assert listed[0].status == ReferenceImageStatus.SUCCEEDED
        await engine.dispose()

    asyncio.run(exercise())


def test_sqlalchemy_store_persists_episode_script_and_shots() -> None:
    async def exercise() -> None:
        engine = create_engine("sqlite+aiosqlite:///:memory:")
        await init_db(engine)
        store = SqlAlchemyStore(create_session_factory(engine))

        project = await store.create_novel_project(
            NovelProjectRecord(
                title="剧本工厂持久化",
                language="zh-CN",
                target_episode_count=1,
                target_episode_duration_seconds=30,
            )
        )
        source = NovelSourceRecord(
            project_id=project.id,
            filename="novel.txt",
            content_type="text/plain",
            size_bytes=4,
            checksum="b" * 64,
            content="第一章\n内容",
            rights_status=RightsStatus.CONFIRMED,
            chapter_count=1,
        )
        saved_source, _ = await store.create_novel_source(
            source,
            [
                ChapterRecord(
                    source_id=source.id,
                    chapter_number=1,
                    title="第一章",
                    content="内容",
                    start_offset=0,
                    end_offset=5,
                )
            ],
        )
        bible = StoryBibleRecord(
            project_id=project.id,
            source_id=saved_source.id,
            content=StoryBibleContent(
                title="剧本工厂持久化",
                logline="主角寻找真相。",
                genre=["悬疑"],
                setting="现代城市",
                themes=["成长"],
                characters=[{"name": "主角", "role": "protagonist"}],
                locations=[{"name": "街道", "description": "夜晚街道"}],
                timeline=["第一章"],
                conflicts=["真相与阻力"],
                source_chapter_numbers=[1],
            ),
            provider="mock",
            model="test",
            duration_ms=1,
        )
        saved_bible = await store.save_story_bible(bible)
        episode = EpisodeRecord(
            project_id=project.id,
            story_bible_id=saved_bible.id,
            episode_number=1,
            status=EpisodeStatus.PLANNED,
            outline=EpisodeOutlineContent(
                episode_number=1,
                title="第一集",
                logline="主角发现线索。",
                objective="找到线索",
                conflict="有人阻拦",
                turning_point="发现新证据",
                ending_hook="敌人出现",
                source_chapter_numbers=[1],
                target_duration_seconds=30,
            ),
            provider="mock",
            model="test",
            duration_ms=1,
        )
        saved_episode = (await store.save_episodes([episode]))[0]
        loaded_episode = (await store.list_episodes(project.id))[0]
        assert loaded_episode.id == saved_episode.id
        assert loaded_episode.outline.title == "第一集"

        script = EpisodeScriptRecord(
            project_id=project.id,
            episode_id=saved_episode.id,
            content=EpisodeScriptContent(
                episode_number=1,
                title="第一集",
                logline="主角发现线索。",
                opening_hook="平静被打破。",
                ending_hook="敌人出现。",
                total_duration_seconds=30,
                scenes=[
                    SceneScriptContent(
                        scene_index=1,
                        title="街道",
                        location="街道",
                        time="夜晚",
                        characters=["主角"],
                        duration_seconds=30,
                        action="主角寻找线索。",
                        dialogues=[
                            DialogueLine(
                                line_index=1,
                                speaker="主角",
                                text="我得查清楚。",
                                emotion="坚定",
                            )
                        ],
                        emotion="悬疑",
                        source_chapter_numbers=[1],
                    )
                ],
            ),
            provider="mock",
            model="test",
            duration_ms=1,
        )
        saved_script = await store.save_episode_script(script)
        loaded_script = await store.get_latest_episode_script(saved_episode.id)
        assert loaded_script is not None
        assert loaded_script.id == saved_script.id
        assert loaded_script.content.scenes[0].dialogues[0].speaker == "主角"

        shot_list = ShotListRecord(
            project_id=project.id,
            episode_id=saved_episode.id,
            script_id=saved_script.id,
            shots=[
                ShotContent(
                    shot_index=1,
                    scene_index=1,
                    duration_seconds=30,
                    shot_size="wide",
                    camera_movement="fixed",
                    characters=["主角"],
                    location="街道",
                    visual_prompt="电影感夜晚街道，主角寻找线索",
                    dialogue_refs=[1],
                    audio_requirements=["对白"],
                    asset_requirements=["character:主角", "location:街道"],
                )
            ],
            provider="mock",
            model="test",
            duration_ms=1,
        )
        saved_shots = await store.save_shot_list(shot_list)
        loaded_shots = await store.get_latest_shot_list(saved_episode.id)
        assert loaded_shots is not None
        assert loaded_shots.id == saved_shots.id
        assert loaded_shots.shots[0].shot_size == "wide"

        character_asset = AssetRecord(
            project_id=project.id,
            story_bible_id=saved_bible.id,
            asset_type=AssetType.CHARACTER,
            name="主角",
            aliases=["主人公"],
            status=AssetStatus.NEEDS_REVIEW,
            content=CharacterAssetContent(
                role="protagonist",
                traits=["坚定"],
                appearance="黑发、深色外套",
            ),
            source_chapter_numbers=[1],
            provider="story_bible_sync",
            model="test",
            duration_ms=0,
        )
        saved_asset = await store.save_asset_version(character_asset)
        saved_asset_v2 = await store.save_asset_version(
            character_asset.model_copy(
                update={
                    "id": uuid4(),
                    "content": CharacterAssetContent(
                        role="protagonist",
                        traits=["坚定", "敏锐"],
                        appearance="黑发、深色外套、左手旧表",
                    ),
                    "status": AssetStatus.READY,
                }
            )
        )
        loaded_assets = await store.list_assets(project.id, AssetType.CHARACTER)
        loaded_asset = await store.get_asset(saved_asset_v2.id)
        exact_v1 = await store.get_asset_version(
            project.id,
            saved_asset.asset_key,
            AssetType.CHARACTER,
            1,
        )
        assert saved_asset.version == 1
        assert saved_asset_v2.version == 2
        assert saved_asset_v2.asset_key == saved_asset.asset_key
        assert len(loaded_assets) == 1
        assert loaded_asset is not None
        assert loaded_asset.status == AssetStatus.READY
        assert loaded_asset.content.appearance.endswith("旧表")
        assert loaded_asset.aliases == ["主人公"]
        assert exact_v1 is not None
        assert exact_v1.content.appearance == "黑发、深色外套"

        saved_review = await store.save_asset_review(
            AssetReviewRecord(
                asset_key=saved_asset_v2.asset_key,
                asset_id=saved_asset_v2.id,
                version=saved_asset_v2.version,
                from_status=AssetStatus.READY,
                to_status=AssetStatus.NEEDS_REVIEW,
                reviewer="reviewer-1",
                comment="等待参考图",
            )
        )
        review_history = await store.list_asset_reviews(saved_asset_v2.asset_key)
        assert saved_review.id == review_history[0].id
        assert review_history[0].to_status == AssetStatus.NEEDS_REVIEW

        await engine.dispose()

    asyncio.run(exercise())


def test_sqlalchemy_store_reads_legacy_shot_asset_references() -> None:
    async def exercise() -> None:
        engine = create_engine("sqlite+aiosqlite:///:memory:")
        await init_db(engine)
        session_factory = create_session_factory(engine)
        store = SqlAlchemyStore(session_factory)
        project_id = uuid4()
        episode_id = uuid4()
        script_id = uuid4()
        shot_list = ShotListRecord(
            project_id=project_id,
            episode_id=episode_id,
            script_id=script_id,
            shots=[
                ShotContent(
                    shot_index=1,
                    scene_index=1,
                    duration_seconds=10,
                    shot_size="wide",
                    camera_movement="fixed",
                    characters=["主角"],
                    location="街道",
                    visual_prompt="夜晚街道",
                    audio_requirements=["环境声"],
                    asset_requirements=["character:主角"],
                )
            ],
            provider="legacy-test",
            model="legacy-test-v1",
            duration_ms=1,
        )
        await store.save_shot_list(shot_list)

        async with session_factory() as session:
            row = await session.get(ShotListRow, shot_list.id)
            assert row is not None
            legacy_shots = [dict(shot) for shot in row.shots_json]
            legacy_shots[0]["asset_refs"] = [
                {
                    "asset_key": str(uuid4()),
                    "asset_type": "character",
                    "name": "主角",
                    "version": 1,
                    "status": "ready",
                }
            ]
            row.shots_json = legacy_shots
            await session.commit()

        loaded = await store.get_latest_shot_list(episode_id)
        assert loaded is not None
        reference = loaded.shots[0].asset_refs[0]
        assert reference.match_kind == "name"
        assert reference.matched_text == "主角"
        await engine.dispose()

    asyncio.run(exercise())


def test_init_db_adds_task_routing_columns_to_legacy_schema() -> None:
    async def exercise() -> None:
        engine = create_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as connection:
            await connection.exec_driver_sql(
                """
                CREATE TABLE generation_tasks (
                    id VARCHAR(36) PRIMARY KEY,
                    project_id VARCHAR(36),
                    idempotency_key VARCHAR(200),
                    status VARCHAR(20),
                    current_stage VARCHAR(30),
                    progress INTEGER,
                    error_json JSON,
                    stages_json JSON,
                    artifacts_json JSON,
                    created_at DATETIME,
                    updated_at DATETIME
                )
                """
            )
        await init_db(engine)

        async with engine.connect() as connection:
            columns = await connection.run_sync(
                lambda sync_connection: {
                    column["name"]
                    for column in inspect(sync_connection).get_columns("generation_tasks")
                }
            )
        assert {"kind", "input_json"}.issubset(columns)
        await engine.dispose()

    asyncio.run(exercise())


def test_init_db_adds_stable_asset_key_to_legacy_schema() -> None:
    async def exercise() -> None:
        engine = create_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as connection:
            await connection.exec_driver_sql(
                """
                CREATE TABLE assets (
                    id VARCHAR(36) PRIMARY KEY,
                    project_id VARCHAR(36),
                    story_bible_id VARCHAR(36),
                    asset_type VARCHAR(30),
                    name VARCHAR(120),
                    version INTEGER,
                    status VARCHAR(30),
                    content_json JSON,
                    source_chapter_numbers_json JSON,
                    provider VARCHAR(80),
                    model VARCHAR(120),
                    duration_ms INTEGER,
                    created_at DATETIME,
                    updated_at DATETIME
                )
                """
            )
        await init_db(engine)

        async with engine.connect() as connection:
            columns = await connection.run_sync(
                lambda sync_connection: {
                    column["name"]
                    for column in inspect(sync_connection).get_columns("assets")
                }
            )
        assert {"asset_key", "aliases_json"}.issubset(columns)
        await engine.dispose()

    asyncio.run(exercise())
