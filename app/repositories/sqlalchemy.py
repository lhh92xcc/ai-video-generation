"""PostgreSQL-backed repository for projects and generation tasks."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db import (
    AssetRow,
    AssetReviewRow,
    AuditLogRow,
    ArtifactRow,
    EpisodeRow,
    EpisodeScriptDraftRow,
    EpisodeScriptRow,
    GenerationTaskRow,
    TaskBatchRow,
    NovelChapterRow,
    NovelProjectRow,
    NovelProjectMemberRow,
    NovelProjectInvitationRow,
    NovelSourceRow,
    ProjectRow,
    ReferenceImageRow,
    ShotListRow,
    StoryBibleRow,
    VoiceAssetRow,
)
from app.domain.models import (
    AssetRecord,
    AssetReviewRecord,
    AssetType,
    AuditEntityType,
    AuditLogRecord,
    ArtifactRecord,
    ArtifactSummary,
    ChapterRecord,
    EpisodeRecord,
    EpisodeScriptDraftRecord,
    EpisodeScriptRecord,
    GenerationTaskRecord,
    TaskBatchRecord,
    NovelProjectRecord,
    NovelSourceRecord,
    ProjectRecord,
    ProjectMemberRecord,
    ProjectInvitationRecord,
    ProjectInvitationStatus,
    ProjectRole,
    ReferenceImageRecord,
    ShotListRecord,
    StoryBibleRecord,
    VoiceAssetRecord,
    utc_now,
)
from app.domain.production_run import merge_run_control_markers


class SqlAlchemyStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create_project(self, project: ProjectRecord) -> ProjectRecord:
        async with self._session_factory() as session:
            row = self._project_to_row(project)
            session.add(row)
            await session.commit()
            return self._project_from_row(row)

    async def get_project(self, project_id: UUID) -> ProjectRecord | None:
        async with self._session_factory() as session:
            row = await session.get(ProjectRow, project_id)
            return self._project_from_row(row) if row else None

    async def list_projects(self) -> list[ProjectRecord]:
        async with self._session_factory() as session:
            result = await session.scalars(select(ProjectRow).order_by(ProjectRow.created_at.desc()))
            return [self._project_from_row(row) for row in result]

    async def update_project(self, project: ProjectRecord) -> ProjectRecord:
        async with self._session_factory() as session:
            row = await session.get(ProjectRow, project.id)
            if row is None:
                row = self._project_to_row(project)
                session.add(row)
            else:
                self._copy_project_to_row(project, row)
            await session.commit()
            return self._project_from_row(row)

    async def create_task(
        self,
        task: GenerationTaskRecord,
        idempotency_key: str | None = None,
    ) -> tuple[GenerationTaskRecord, bool]:
        async with self._session_factory() as session:
            if idempotency_key:
                existing = await self._find_by_idempotency_key(
                    session, task.project_id, idempotency_key
                )
                if existing:
                    return self._task_from_row(existing), True

            row = self._task_to_row(task, idempotency_key)
            session.add(row)
            await self._sync_artifact_rows(session, task)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                if not idempotency_key:
                    raise
                existing = await self._find_by_idempotency_key(
                    session, task.project_id, idempotency_key
                )
                if existing is None:
                    raise
                return self._task_from_row(existing), True
            return self._task_from_row(row), False

    async def get_task(self, task_id: UUID) -> GenerationTaskRecord | None:
        async with self._session_factory() as session:
            row = await session.get(GenerationTaskRow, task_id)
            return self._task_from_row(row) if row else None

    async def create_task_batch(
        self,
        batch: TaskBatchRecord,
        idempotency_key: str | None = None,
    ) -> tuple[TaskBatchRecord, bool]:
        async with self._session_factory() as session:
            if idempotency_key:
                existing = await self._find_batch_by_idempotency_key(
                    session, batch.project_id, idempotency_key
                )
                if existing:
                    return self._batch_from_row(existing), True
            row = self._batch_to_row(batch, idempotency_key)
            session.add(row)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                if not idempotency_key:
                    raise
                existing = await self._find_batch_by_idempotency_key(
                    session, batch.project_id, idempotency_key
                )
                if existing is None:
                    raise
                return self._batch_from_row(existing), True
            return self._batch_from_row(row), False

    async def get_task_batch(self, batch_id: UUID) -> TaskBatchRecord | None:
        async with self._session_factory() as session:
            row = await session.get(TaskBatchRow, batch_id)
            return self._batch_from_row(row) if row else None

    async def get_task_batch_by_idempotency_key(
        self,
        project_id: UUID,
        idempotency_key: str,
    ) -> TaskBatchRecord | None:
        async with self._session_factory() as session:
            row = await self._find_batch_by_idempotency_key(
                session,
                project_id,
                idempotency_key,
            )
            return self._batch_from_row(row) if row else None

    async def list_task_batches(self, project_id: UUID, limit: int = 50) -> list[TaskBatchRecord]:
        async with self._session_factory() as session:
            result = await session.scalars(
                select(TaskBatchRow)
                .where(TaskBatchRow.project_id == project_id)
                .order_by(TaskBatchRow.updated_at.desc())
                .limit(limit)
            )
            return [self._batch_from_row(row) for row in result]

    async def update_task_batch(self, batch: TaskBatchRecord) -> TaskBatchRecord:
        async with self._session_factory() as session:
            row = await session.get(TaskBatchRow, batch.id)
            if row is None:
                row = self._batch_to_row(batch, None)
                session.add(row)
            else:
                self._copy_batch_to_row(batch, row)
            await session.commit()
            return self._batch_from_row(row)

    async def list_tasks(
        self,
        project_id: UUID | None = None,
        kind: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[GenerationTaskRecord]:
        async with self._session_factory() as session:
            query = select(GenerationTaskRow).order_by(GenerationTaskRow.updated_at.desc())
            if project_id is not None:
                query = query.where(GenerationTaskRow.project_id == project_id)
            if kind is not None:
                query = query.where(GenerationTaskRow.kind == kind)
            if status is not None:
                query = query.where(GenerationTaskRow.status == status)
            result = await session.scalars(query.limit(limit))
            return [self._task_from_row(row) for row in result]

    async def update_task(self, task: GenerationTaskRecord) -> GenerationTaskRecord:
        async with self._session_factory() as session:
            row = await session.get(GenerationTaskRow, task.id)
            if row is None:
                row = self._task_to_row(task, None)
                session.add(row)
            else:
                task.input_data = merge_run_control_markers(
                    row.input_json,
                    task.input_data,
                )
                self._copy_task_to_row(task, row)
            await self._sync_artifact_rows(session, task)
            await session.commit()
            return self._task_from_row(row)

    async def get_artifact(self, artifact_id: UUID) -> ArtifactRecord | None:
        async with self._session_factory() as session:
            row = await session.get(ArtifactRow, artifact_id)
            if row is not None:
                return self._artifact_from_row(row)

            # Backfill artifacts written by versions that only persisted task snapshots.
            result = await session.scalars(select(GenerationTaskRow))
            for task_row in result:
                task = self._task_from_row(task_row)
                artifact = next((item for item in task.artifacts if item.id == artifact_id), None)
                if artifact is None:
                    continue
                await self._sync_artifact_rows(session, task)
                await session.commit()
                return self._artifact_from_summary(task, artifact)
            return None

    async def list_artifacts(
        self,
        project_id: UUID | None = None,
        artifact_type: str | None = None,
        limit: int = 100,
    ) -> list[ArtifactRecord]:
        async with self._session_factory() as session:
            query = select(ArtifactRow).order_by(ArtifactRow.created_at.desc())
            if project_id is not None:
                query = query.where(ArtifactRow.project_id == project_id)
            if artifact_type is not None:
                query = query.where(ArtifactRow.artifact_type == artifact_type)
            result = await session.scalars(query.limit(limit))
            return [self._artifact_from_row(row) for row in result]

    async def create_novel_project(self, project: NovelProjectRecord) -> NovelProjectRecord:
        async with self._session_factory() as session:
            row = self._novel_project_to_row(project)
            session.add(row)
            await session.commit()
            return self._novel_project_from_row(row)

    async def get_novel_project(self, project_id: UUID) -> NovelProjectRecord | None:
        async with self._session_factory() as session:
            row = await session.get(NovelProjectRow, project_id)
            return self._novel_project_from_row(row) if row else None

    async def list_novel_projects(self) -> list[NovelProjectRecord]:
        async with self._session_factory() as session:
            result = await session.scalars(
                select(NovelProjectRow).order_by(NovelProjectRow.updated_at.desc())
            )
            return [self._novel_project_from_row(row) for row in result]

    async def update_novel_project(self, project: NovelProjectRecord) -> NovelProjectRecord:
        async with self._session_factory() as session:
            row = await session.get(NovelProjectRow, project.id)
            if row is None:
                row = self._novel_project_to_row(project)
                session.add(row)
            else:
                self._copy_novel_project_to_row(project, row)
            await session.commit()
            return self._novel_project_from_row(row)

    async def get_novel_project_member(
        self,
        project_id: UUID,
        actor_id: str,
    ) -> ProjectMemberRecord | None:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(NovelProjectMemberRow).where(
                    NovelProjectMemberRow.project_id == project_id,
                    NovelProjectMemberRow.actor_id == actor_id,
                )
            )
            return self._novel_project_member_from_row(row) if row else None

    async def list_novel_project_members(self, project_id: UUID) -> list[ProjectMemberRecord]:
        async with self._session_factory() as session:
            result = await session.scalars(
                select(NovelProjectMemberRow)
                .where(NovelProjectMemberRow.project_id == project_id)
                .order_by(NovelProjectMemberRow.actor_id.asc())
            )
            return [self._novel_project_member_from_row(row) for row in result]

    async def upsert_novel_project_member(self, member: ProjectMemberRecord) -> ProjectMemberRecord:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(NovelProjectMemberRow).where(
                    NovelProjectMemberRow.project_id == member.project_id,
                    NovelProjectMemberRow.actor_id == member.actor_id,
                )
            )
            if row is None:
                row = self._novel_project_member_to_row(member)
                session.add(row)
            else:
                row.actor_name = member.actor_name
                row.role = member.role.value
                row.updated_at = member.updated_at
            await session.commit()
            return self._novel_project_member_from_row(row)

    async def delete_novel_project_member(self, project_id: UUID, actor_id: str) -> None:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(NovelProjectMemberRow).where(
                    NovelProjectMemberRow.project_id == project_id,
                    NovelProjectMemberRow.actor_id == actor_id,
                )
            )
            if row is not None:
                await session.delete(row)
                await session.commit()

    async def create_novel_project_invitation(
        self,
        invitation: ProjectInvitationRecord,
    ) -> ProjectInvitationRecord:
        async with self._session_factory() as session:
            row = self._novel_project_invitation_to_row(invitation)
            session.add(row)
            await session.commit()
            return self._novel_project_invitation_from_row(row)

    async def get_novel_project_invitation(
        self,
        invitation_id: UUID,
    ) -> ProjectInvitationRecord | None:
        async with self._session_factory() as session:
            row = await session.get(NovelProjectInvitationRow, invitation_id)
            return self._novel_project_invitation_from_row(row) if row else None

    async def list_novel_project_invitations(
        self,
        project_id: UUID,
    ) -> list[ProjectInvitationRecord]:
        async with self._session_factory() as session:
            result = await session.scalars(
                select(NovelProjectInvitationRow)
                .where(NovelProjectInvitationRow.project_id == project_id)
                .order_by(NovelProjectInvitationRow.created_at.desc())
            )
            return [self._novel_project_invitation_from_row(row) for row in result]

    async def update_novel_project_invitation(
        self,
        invitation: ProjectInvitationRecord,
    ) -> ProjectInvitationRecord:
        async with self._session_factory() as session:
            row = await session.get(NovelProjectInvitationRow, invitation.id)
            if row is None:
                row = self._novel_project_invitation_to_row(invitation)
                session.add(row)
            else:
                row.invitee_actor_id = invitation.invitee_actor_id
                row.invitee_name = invitation.invitee_name
                row.role = invitation.role.value
                row.token_hash = invitation.token_hash
                row.status = invitation.status.value
                row.expires_at = invitation.expires_at
                row.invited_by_actor_id = invitation.invited_by_actor_id
                row.invited_by_name = invitation.invited_by_name
                row.accepted_at = invitation.accepted_at
                row.accepted_by_actor_id = invitation.accepted_by_actor_id
                row.revoked_at = invitation.revoked_at
                row.created_at = invitation.created_at
                row.updated_at = invitation.updated_at
            await session.commit()
            return self._novel_project_invitation_from_row(row)

    async def accept_novel_project_invitation(
        self,
        invitation_id: UUID,
        token_hash: str,
        invitee_actor_id: str,
        now: datetime,
    ) -> tuple[ProjectInvitationRecord, ProjectMemberRecord] | None:
        """Consume an invitation and create its member in one database transaction."""
        async with self._session_factory() as session:
            try:
                async with session.begin():
                    row = await session.scalar(
                        select(NovelProjectInvitationRow)
                        .where(NovelProjectInvitationRow.id == invitation_id)
                        .with_for_update()
                    )
                    if row is None:
                        return None
                    if (
                        row.status == ProjectInvitationStatus.PENDING.value
                        and row.expires_at
                        <= (now.replace(tzinfo=None) if row.expires_at.tzinfo is None else now)
                    ):
                        row.status = ProjectInvitationStatus.EXPIRED.value
                        row.updated_at = now
                        return None
                    if (
                        row.status != ProjectInvitationStatus.PENDING.value
                        or row.token_hash != token_hash
                        or row.invitee_actor_id != invitee_actor_id
                    ):
                        return None
                    existing_member = await session.scalar(
                        select(NovelProjectMemberRow).where(
                            NovelProjectMemberRow.project_id == row.project_id,
                            NovelProjectMemberRow.actor_id == invitee_actor_id,
                        )
                    )
                    if existing_member is not None:
                        return None

                    member = ProjectMemberRecord(
                        project_id=row.project_id,
                        actor_id=invitee_actor_id,
                        actor_name=row.invitee_name,
                        role=ProjectRole(row.role),
                        created_at=now,
                        updated_at=now,
                    )
                    session.add(self._novel_project_member_to_row(member))
                    row.status = ProjectInvitationStatus.ACCEPTED.value
                    row.accepted_at = now
                    row.accepted_by_actor_id = invitee_actor_id
                    row.updated_at = now
                    await session.flush()
                    return self._novel_project_invitation_from_row(row), member
            except IntegrityError:
                # A different invitation for the same actor may have won the
                # unique member insert. The service remaps this to a conflict.
                await session.rollback()
                return None

    async def create_novel_source(
        self,
        source: NovelSourceRecord,
        chapters: list[ChapterRecord],
    ) -> tuple[NovelSourceRecord, list[ChapterRecord]]:
        async with self._session_factory() as session:
            source_row = self._novel_source_to_row(source)
            session.add(source_row)
            session.add_all(self._chapter_to_row(chapter) for chapter in chapters)
            await session.commit()
            return self._novel_source_from_row(source_row), [
                self._chapter_from_row(chapter_row)
                for chapter_row in await self._load_chapter_rows(session, source.id)
            ]

    async def get_novel_source(self, source_id: UUID) -> NovelSourceRecord | None:
        async with self._session_factory() as session:
            row = await session.get(NovelSourceRow, source_id)
            return self._novel_source_from_row(row) if row else None

    async def list_chapters(self, source_id: UUID) -> list[ChapterRecord]:
        async with self._session_factory() as session:
            rows = await self._load_chapter_rows(session, source_id)
            return [self._chapter_from_row(row) for row in rows]

    async def save_story_bible(self, story_bible: StoryBibleRecord) -> StoryBibleRecord:
        async with self._session_factory() as session:
            latest_version = await session.scalar(
                select(StoryBibleRow.version)
                .where(StoryBibleRow.project_id == story_bible.project_id)
                .order_by(StoryBibleRow.version.desc())
                .limit(1)
            )
            story_bible.version = (latest_version or 0) + 1
            row = self._story_bible_to_row(story_bible)
            session.add(row)
            await session.commit()
            return self._story_bible_from_row(row)

    async def get_latest_story_bible(self, project_id: UUID) -> StoryBibleRecord | None:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(StoryBibleRow)
                .where(StoryBibleRow.project_id == project_id)
                .order_by(StoryBibleRow.version.desc())
                .limit(1)
            )
            return self._story_bible_from_row(row) if row else None

    async def save_episodes(self, episodes: list[EpisodeRecord]) -> list[EpisodeRecord]:
        if not episodes:
            return []
        async with self._session_factory() as session:
            latest_version = await session.scalar(
                select(func.max(EpisodeRow.version)).where(
                    EpisodeRow.project_id == episodes[0].project_id
                )
            )
            version = (latest_version or 0) + 1
            versioned = [episode.model_copy(update={"version": version}) for episode in episodes]
            rows = [self._episode_to_row(episode) for episode in versioned]
            session.add_all(rows)
            await session.commit()
            return [self._episode_from_row(row) for row in rows]

    async def list_episodes(self, project_id: UUID) -> list[EpisodeRecord]:
        async with self._session_factory() as session:
            latest_version = await session.scalar(
                select(func.max(EpisodeRow.version)).where(EpisodeRow.project_id == project_id)
            )
            if latest_version is None:
                return []
            result = await session.scalars(
                select(EpisodeRow)
                .where(
                    EpisodeRow.project_id == project_id,
                    EpisodeRow.version == latest_version,
                )
                .order_by(EpisodeRow.episode_number.asc())
            )
            return [self._episode_from_row(row) for row in result]

    async def get_episode(self, episode_id: UUID) -> EpisodeRecord | None:
        async with self._session_factory() as session:
            row = await session.get(EpisodeRow, episode_id)
            return self._episode_from_row(row) if row else None

    async def update_episode(self, episode: EpisodeRecord) -> EpisodeRecord:
        async with self._session_factory() as session:
            row = await session.get(EpisodeRow, episode.id)
            if row is None:
                row = self._episode_to_row(episode)
                session.add(row)
            else:
                self._copy_episode_to_row(episode, row)
            await session.commit()
            return self._episode_from_row(row)

    async def save_episode_script(self, script: EpisodeScriptRecord) -> EpisodeScriptRecord:
        async with self._session_factory() as session:
            latest_version = await session.scalar(
                select(func.max(EpisodeScriptRow.version)).where(
                    EpisodeScriptRow.episode_id == script.episode_id
                )
            )
            versioned = script.model_copy(update={"version": (latest_version or 0) + 1})
            row = self._episode_script_to_row(versioned)
            session.add(row)
            await session.commit()
            return self._episode_script_from_row(row)

    async def get_latest_episode_script(self, episode_id: UUID) -> EpisodeScriptRecord | None:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(EpisodeScriptRow)
                .where(EpisodeScriptRow.episode_id == episode_id)
                .order_by(EpisodeScriptRow.version.desc())
                .limit(1)
            )
            return self._episode_script_from_row(row) if row else None

    async def get_episode_script_draft(
        self,
        episode_id: UUID,
    ) -> EpisodeScriptDraftRecord | None:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(EpisodeScriptDraftRow).where(
                    EpisodeScriptDraftRow.episode_id == episode_id
                )
            )
            return self._episode_script_draft_from_row(row) if row else None

    async def save_episode_script_draft(
        self,
        draft: EpisodeScriptDraftRecord,
        expected_revision: int,
    ) -> EpisodeScriptDraftRecord | None:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(EpisodeScriptDraftRow)
                .where(EpisodeScriptDraftRow.episode_id == draft.episode_id)
                .with_for_update()
            )
            current_revision = row.revision if row else 0
            if current_revision != expected_revision:
                return None

            saved = draft.model_copy(
                update={
                    "id": row.id if row else draft.id,
                    "revision": expected_revision + 1,
                    "created_at": row.created_at if row else draft.created_at,
                    "updated_at": utc_now(),
                },
                deep=True,
            )
            if row is None:
                row = self._episode_script_draft_to_row(saved)
                session.add(row)
            else:
                row.project_id = saved.project_id
                row.episode_id = saved.episode_id
                row.base_script_id = saved.base_script_id
                row.base_script_version = saved.base_script_version
                row.revision = saved.revision
                row.content_json = saved.content.model_dump(mode="json")
                row.created_at = saved.created_at
                row.updated_at = saved.updated_at
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                return None
            return self._episode_script_draft_from_row(row)

    async def delete_episode_script_draft(self, episode_id: UUID) -> None:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(EpisodeScriptDraftRow).where(
                    EpisodeScriptDraftRow.episode_id == episode_id
                )
            )
            if row is not None:
                await session.delete(row)
                await session.commit()

    async def save_shot_list(self, shot_list: ShotListRecord) -> ShotListRecord:
        async with self._session_factory() as session:
            latest_version = await session.scalar(
                select(func.max(ShotListRow.version)).where(
                    ShotListRow.episode_id == shot_list.episode_id
                )
            )
            versioned = shot_list.model_copy(update={"version": (latest_version or 0) + 1})
            row = self._shot_list_to_row(versioned)
            session.add(row)
            await session.commit()
            return self._shot_list_from_row(row)

    async def get_latest_shot_list(self, episode_id: UUID) -> ShotListRecord | None:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(ShotListRow)
                .where(ShotListRow.episode_id == episode_id)
                .order_by(ShotListRow.version.desc())
                .limit(1)
            )
            return self._shot_list_from_row(row) if row else None

    async def save_asset_version(self, asset: AssetRecord) -> AssetRecord:
        async with self._session_factory() as session:
            latest = await session.scalar(
                select(AssetRow)
                .where(
                    AssetRow.project_id == asset.project_id,
                    AssetRow.asset_type == asset.asset_type.value,
                    AssetRow.name == asset.name,
                )
                .order_by(AssetRow.version.desc())
                .limit(1)
            )
            versioned = asset.model_copy(
                update={
                    "asset_key": latest.asset_key if latest is not None else asset.asset_key,
                    "version": latest.version + 1 if latest is not None else 1,
                }
            )
            row = self._asset_to_row(versioned)
            session.add(row)
            await session.commit()
            return self._asset_from_row(row)

    async def list_assets(
        self,
        project_id: UUID,
        asset_type: AssetType | None = None,
    ) -> list[AssetRecord]:
        async with self._session_factory() as session:
            statement = select(AssetRow).where(AssetRow.project_id == project_id)
            if asset_type is not None:
                statement = statement.where(AssetRow.asset_type == asset_type.value)
            result = await session.scalars(
                statement.order_by(
                    AssetRow.asset_type.asc(),
                    AssetRow.name.asc(),
                    AssetRow.version.desc(),
                )
            )
            latest: dict[tuple[str, str], AssetRecord] = {}
            for row in result:
                key = (row.asset_type, row.name)
                if key not in latest:
                    latest[key] = self._asset_from_row(row)
            return list(latest.values())

    async def get_asset(self, asset_id: UUID) -> AssetRecord | None:
        async with self._session_factory() as session:
            row = await session.get(AssetRow, asset_id)
            return self._asset_from_row(row) if row else None

    async def get_asset_version(
        self,
        project_id: UUID,
        asset_key: UUID,
        asset_type: AssetType,
        version: int,
    ) -> AssetRecord | None:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(AssetRow).where(
                    AssetRow.project_id == project_id,
                    AssetRow.asset_key == asset_key,
                    AssetRow.asset_type == asset_type.value,
                    AssetRow.version == version,
                )
            )
            return self._asset_from_row(row) if row else None

    async def save_asset_review(self, review: AssetReviewRecord) -> AssetReviewRecord:
        async with self._session_factory() as session:
            row = self._asset_review_to_row(review)
            session.add(row)
            await session.commit()
            return self._asset_review_from_row(row)

    async def list_asset_reviews(self, asset_key: UUID) -> list[AssetReviewRecord]:
        async with self._session_factory() as session:
            result = await session.scalars(
                select(AssetReviewRow)
                .where(AssetReviewRow.asset_key == asset_key)
                .order_by(AssetReviewRow.created_at.asc())
            )
            return [self._asset_review_from_row(row) for row in result]

    async def save_audit_log(self, log: AuditLogRecord) -> AuditLogRecord:
        async with self._session_factory() as session:
            row = self._audit_log_to_row(log)
            session.add(row)
            await session.commit()
            return self._audit_log_from_row(row)

    async def list_audit_logs(
        self,
        project_id: UUID,
        entity_type: AuditEntityType | None = None,
        entity_id: UUID | None = None,
        limit: int = 100,
    ) -> list[AuditLogRecord]:
        async with self._session_factory() as session:
            statement = select(AuditLogRow).where(AuditLogRow.project_id == project_id)
            if entity_type is not None:
                statement = statement.where(AuditLogRow.entity_type == entity_type.value)
            if entity_id is not None:
                statement = statement.where(AuditLogRow.entity_id == entity_id)
            result = await session.scalars(
                statement.order_by(AuditLogRow.created_at.desc()).limit(limit)
            )
            return [self._audit_log_from_row(row) for row in result]

    async def save_reference_image(self, image: ReferenceImageRecord) -> ReferenceImageRecord:
        async with self._session_factory() as session:
            row = await session.get(ReferenceImageRow, image.id)
            if row is None:
                row = self._reference_image_to_row(image)
                session.add(row)
            else:
                self._copy_reference_image_to_row(image, row)
            await session.commit()
            return self._reference_image_from_row(row)

    async def get_reference_image(self, image_id: UUID) -> ReferenceImageRecord | None:
        async with self._session_factory() as session:
            row = await session.get(ReferenceImageRow, image_id)
            return self._reference_image_from_row(row) if row else None

    async def list_reference_images(self, asset_id: UUID) -> list[ReferenceImageRecord]:
        async with self._session_factory() as session:
            result = await session.scalars(
                select(ReferenceImageRow)
                .where(ReferenceImageRow.asset_id == asset_id)
                .order_by(ReferenceImageRow.created_at.desc())
            )
            return [self._reference_image_from_row(row) for row in result]

    async def save_voice_asset(self, voice_asset: VoiceAssetRecord) -> VoiceAssetRecord:
        async with self._session_factory() as session:
            row = await session.get(VoiceAssetRow, voice_asset.id)
            if row is None:
                row = self._voice_asset_to_row(voice_asset)
                session.add(row)
            else:
                self._copy_voice_asset_to_row(voice_asset, row)
            await session.commit()
            return self._voice_asset_from_row(row)

    async def get_voice_asset(self, voice_asset_id: UUID) -> VoiceAssetRecord | None:
        async with self._session_factory() as session:
            row = await session.get(VoiceAssetRow, voice_asset_id)
            return self._voice_asset_from_row(row) if row else None

    async def list_voice_assets(self, project_id: UUID) -> list[VoiceAssetRecord]:
        async with self._session_factory() as session:
            result = await session.scalars(
                select(VoiceAssetRow)
                .where(VoiceAssetRow.project_id == project_id)
                .order_by(VoiceAssetRow.created_at.desc())
            )
            return [self._voice_asset_from_row(row) for row in result]

    @staticmethod
    async def _find_by_idempotency_key(
        session: AsyncSession,
        project_id: UUID,
        idempotency_key: str,
    ) -> GenerationTaskRow | None:
        statement = select(GenerationTaskRow).where(
            GenerationTaskRow.project_id == project_id,
            GenerationTaskRow.idempotency_key == idempotency_key,
        )
        return await session.scalar(statement)

    @staticmethod
    async def _find_batch_by_idempotency_key(
        session: AsyncSession,
        project_id: UUID,
        idempotency_key: str,
    ) -> TaskBatchRow | None:
        statement = select(TaskBatchRow).where(
            TaskBatchRow.project_id == project_id,
            TaskBatchRow.idempotency_key == idempotency_key,
        )
        return await session.scalar(statement)

    @staticmethod
    def _batch_to_row(batch: TaskBatchRecord, idempotency_key: str | None) -> TaskBatchRow:
        return TaskBatchRow(
            id=batch.id,
            project_id=batch.project_id,
            idempotency_key=idempotency_key,
            task_ids_json=[str(task_id) for task_id in batch.task_ids],
            label=batch.label,
            status=batch.status.value,
            total_count=batch.total_count,
            succeeded_count=batch.succeeded_count,
            failed_count=batch.failed_count,
            active_count=batch.active_count,
            created_at=batch.created_at,
            updated_at=batch.updated_at,
        )

    @staticmethod
    def _copy_batch_to_row(batch: TaskBatchRecord, row: TaskBatchRow) -> None:
        row.task_ids_json = [str(task_id) for task_id in batch.task_ids]
        row.label = batch.label
        row.status = batch.status.value
        row.total_count = batch.total_count
        row.succeeded_count = batch.succeeded_count
        row.failed_count = batch.failed_count
        row.active_count = batch.active_count
        row.created_at = batch.created_at
        row.updated_at = batch.updated_at

    @staticmethod
    def _batch_from_row(row: TaskBatchRow) -> TaskBatchRecord:
        return TaskBatchRecord.model_validate(
            {
                "id": row.id,
                "project_id": row.project_id,
                "task_ids": row.task_ids_json,
                "label": row.label,
                "status": row.status,
                "total_count": row.total_count,
                "succeeded_count": row.succeeded_count,
                "failed_count": row.failed_count,
                "active_count": row.active_count,
                "created_at": row.created_at,
                "updated_at": row.updated_at,
            }
        )

    @staticmethod
    def _project_to_row(project: ProjectRecord) -> ProjectRow:
        return ProjectRow(
            id=project.id,
            title=project.title,
            topic=project.topic,
            language=project.language,
            target_duration_seconds=project.target_duration_seconds,
            aspect_ratio=project.aspect_ratio,
            tone=project.tone,
            status=project.status.value,
            created_at=project.created_at,
            updated_at=project.updated_at,
        )

    @staticmethod
    def _copy_project_to_row(project: ProjectRecord, row: ProjectRow) -> None:
        row.title = project.title
        row.topic = project.topic
        row.language = project.language
        row.target_duration_seconds = project.target_duration_seconds
        row.aspect_ratio = project.aspect_ratio
        row.tone = project.tone
        row.status = project.status.value
        row.created_at = project.created_at
        row.updated_at = project.updated_at

    @staticmethod
    def _project_from_row(row: ProjectRow) -> ProjectRecord:
        return ProjectRecord.model_validate(
            {
                "id": row.id,
                "title": row.title,
                "topic": row.topic,
                "language": row.language,
                "target_duration_seconds": row.target_duration_seconds,
                "aspect_ratio": row.aspect_ratio,
                "tone": row.tone,
                "status": row.status,
                "created_at": row.created_at,
                "updated_at": row.updated_at,
            }
        )

    @staticmethod
    def _task_to_row(task: GenerationTaskRecord, idempotency_key: str | None) -> GenerationTaskRow:
        data = task.model_dump(mode="json")
        return GenerationTaskRow(
            id=task.id,
            project_id=task.project_id,
            idempotency_key=idempotency_key,
            kind=task.kind.value,
            input_json=data["input_data"],
            status=task.status.value,
            current_stage=task.current_stage.value if task.current_stage else None,
            progress=task.progress,
            error_json=data["error"],
            stages_json=data["stages"],
            artifacts_json=data["artifacts"],
            created_at=task.created_at,
            updated_at=task.updated_at,
        )

    @staticmethod
    def _copy_task_to_row(task: GenerationTaskRecord, row: GenerationTaskRow) -> None:
        data = task.model_dump(mode="json")
        row.status = task.status.value
        row.kind = task.kind.value
        row.input_json = data["input_data"]
        row.current_stage = task.current_stage.value if task.current_stage else None
        row.progress = task.progress
        row.error_json = data["error"]
        row.stages_json = data["stages"]
        row.artifacts_json = data["artifacts"]
        row.created_at = task.created_at
        row.updated_at = task.updated_at

    @staticmethod
    def _task_from_row(row: GenerationTaskRow) -> GenerationTaskRecord:
        return GenerationTaskRecord.model_validate(
            {
                "id": row.id,
                "project_id": row.project_id,
                "kind": row.kind or "info_script",
                "input_data": row.input_json or {},
                "status": row.status,
                "current_stage": row.current_stage,
                "progress": row.progress,
                "error": row.error_json,
                "stages": row.stages_json,
                "artifacts": row.artifacts_json,
                "created_at": row.created_at,
                "updated_at": row.updated_at,
            }
        )

    @staticmethod
    async def _sync_artifact_rows(
        session: AsyncSession,
        task: GenerationTaskRecord,
    ) -> None:
        for artifact in task.artifacts:
            row = await session.get(ArtifactRow, artifact.id)
            if row is None:
                session.add(SqlAlchemyStore._artifact_to_row(task, artifact))
            else:
                SqlAlchemyStore._copy_artifact_to_row(task, artifact, row)

    @staticmethod
    def _artifact_to_row(task: GenerationTaskRecord, artifact: ArtifactSummary) -> ArtifactRow:
        return ArtifactRow(
            id=artifact.id,
            task_id=task.id,
            project_id=task.project_id,
            artifact_type=artifact.type,
            status=artifact.status,
            provider=artifact.provider,
            created_at=artifact.created_at,
            metadata_json=artifact.metadata,
            preview_json=artifact.preview,
        )

    @staticmethod
    def _copy_artifact_to_row(
        task: GenerationTaskRecord,
        artifact: ArtifactSummary,
        row: ArtifactRow,
    ) -> None:
        row.task_id = task.id
        row.project_id = task.project_id
        row.artifact_type = artifact.type
        row.status = artifact.status
        row.provider = artifact.provider
        row.created_at = artifact.created_at
        row.metadata_json = artifact.metadata
        row.preview_json = artifact.preview

    @staticmethod
    def _artifact_from_summary(
        task: GenerationTaskRecord,
        artifact: ArtifactSummary,
    ) -> ArtifactRecord:
        return ArtifactRecord(
            id=artifact.id,
            task_id=task.id,
            project_id=task.project_id,
            type=artifact.type,
            status=artifact.status,
            provider=artifact.provider,
            created_at=artifact.created_at,
            metadata=artifact.metadata,
            preview=artifact.preview,
        )

    @staticmethod
    def _artifact_from_row(row: ArtifactRow) -> ArtifactRecord:
        return ArtifactRecord(
            id=row.id,
            task_id=row.task_id,
            project_id=row.project_id,
            type=row.artifact_type,
            status=row.status,
            provider=row.provider,
            created_at=row.created_at,
            metadata=row.metadata_json or {},
            preview=row.preview_json,
        )

    @staticmethod
    async def _load_chapter_rows(session: AsyncSession, source_id: UUID) -> list[NovelChapterRow]:
        result = await session.scalars(
            select(NovelChapterRow)
            .where(NovelChapterRow.source_id == source_id)
            .order_by(NovelChapterRow.chapter_number.asc())
        )
        return list(result)

    @staticmethod
    def _novel_project_to_row(project: NovelProjectRecord) -> NovelProjectRow:
        return NovelProjectRow(
            id=project.id,
            title=project.title,
            language=project.language,
            target_episode_count=project.target_episode_count,
            target_episode_duration_seconds=project.target_episode_duration_seconds,
            rights_status=project.rights_status.value,
            status=project.status.value,
            source_id=project.source_id,
            story_bible_id=project.story_bible_id,
            created_at=project.created_at,
            updated_at=project.updated_at,
        )

    @staticmethod
    def _copy_novel_project_to_row(project: NovelProjectRecord, row: NovelProjectRow) -> None:
        row.title = project.title
        row.language = project.language
        row.target_episode_count = project.target_episode_count
        row.target_episode_duration_seconds = project.target_episode_duration_seconds
        row.rights_status = project.rights_status.value
        row.status = project.status.value
        row.source_id = project.source_id
        row.story_bible_id = project.story_bible_id
        row.created_at = project.created_at
        row.updated_at = project.updated_at

    @staticmethod
    def _novel_project_from_row(row: NovelProjectRow) -> NovelProjectRecord:
        return NovelProjectRecord.model_validate(
            {
                "id": row.id,
                "title": row.title,
                "language": row.language,
                "target_episode_count": row.target_episode_count,
                "target_episode_duration_seconds": row.target_episode_duration_seconds,
                "rights_status": row.rights_status,
                "status": row.status,
                "source_id": row.source_id,
                "story_bible_id": row.story_bible_id,
                "created_at": row.created_at,
                "updated_at": row.updated_at,
            }
        )

    @staticmethod
    def _novel_project_member_to_row(member: ProjectMemberRecord) -> NovelProjectMemberRow:
        return NovelProjectMemberRow(
            id=member.id,
            project_id=member.project_id,
            actor_id=member.actor_id,
            actor_name=member.actor_name,
            role=member.role.value,
            created_at=member.created_at,
            updated_at=member.updated_at,
        )

    @staticmethod
    def _novel_project_member_from_row(row: NovelProjectMemberRow) -> ProjectMemberRecord:
        return ProjectMemberRecord.model_validate(
            {
                "id": row.id,
                "project_id": row.project_id,
                "actor_id": row.actor_id,
                "actor_name": row.actor_name,
                "role": row.role,
                "created_at": row.created_at,
                "updated_at": row.updated_at,
            }
        )

    @staticmethod
    def _novel_project_invitation_to_row(
        invitation: ProjectInvitationRecord,
    ) -> NovelProjectInvitationRow:
        return NovelProjectInvitationRow(
            id=invitation.id,
            project_id=invitation.project_id,
            invitee_actor_id=invitation.invitee_actor_id,
            invitee_name=invitation.invitee_name,
            role=invitation.role.value,
            token_hash=invitation.token_hash,
            status=invitation.status.value,
            expires_at=invitation.expires_at,
            invited_by_actor_id=invitation.invited_by_actor_id,
            invited_by_name=invitation.invited_by_name,
            accepted_at=invitation.accepted_at,
            accepted_by_actor_id=invitation.accepted_by_actor_id,
            revoked_at=invitation.revoked_at,
            created_at=invitation.created_at,
            updated_at=invitation.updated_at,
        )

    @staticmethod
    def _novel_project_invitation_from_row(
        row: NovelProjectInvitationRow,
    ) -> ProjectInvitationRecord:
        return ProjectInvitationRecord.model_validate(
            {
                "id": row.id,
                "project_id": row.project_id,
                "invitee_actor_id": row.invitee_actor_id,
                "invitee_name": row.invitee_name,
                "role": row.role,
                "token_hash": row.token_hash,
                "status": row.status,
                "expires_at": row.expires_at,
                "invited_by_actor_id": row.invited_by_actor_id,
                "invited_by_name": row.invited_by_name,
                "accepted_at": row.accepted_at,
                "accepted_by_actor_id": row.accepted_by_actor_id,
                "revoked_at": row.revoked_at,
                "created_at": row.created_at,
                "updated_at": row.updated_at,
            }
        )

    @staticmethod
    def _novel_source_to_row(source: NovelSourceRecord) -> NovelSourceRow:
        return NovelSourceRow(
            id=source.id,
            project_id=source.project_id,
            filename=source.filename,
            content_type=source.content_type,
            size_bytes=source.size_bytes,
            checksum=source.checksum,
            content=source.content,
            rights_status=source.rights_status.value,
            chapter_count=source.chapter_count,
            created_at=source.created_at,
        )

    @staticmethod
    def _novel_source_from_row(row: NovelSourceRow) -> NovelSourceRecord:
        return NovelSourceRecord.model_validate(
            {
                "id": row.id,
                "project_id": row.project_id,
                "filename": row.filename,
                "content_type": row.content_type,
                "size_bytes": row.size_bytes,
                "checksum": row.checksum,
                "content": row.content,
                "rights_status": row.rights_status,
                "chapter_count": row.chapter_count,
                "created_at": row.created_at,
            }
        )

    @staticmethod
    def _chapter_to_row(chapter: ChapterRecord) -> NovelChapterRow:
        return NovelChapterRow(
            id=chapter.id,
            source_id=chapter.source_id,
            chapter_number=chapter.chapter_number,
            title=chapter.title,
            content=chapter.content,
            start_offset=chapter.start_offset,
            end_offset=chapter.end_offset,
            created_at=chapter.created_at,
        )

    @staticmethod
    def _chapter_from_row(row: NovelChapterRow) -> ChapterRecord:
        return ChapterRecord.model_validate(
            {
                "id": row.id,
                "source_id": row.source_id,
                "chapter_number": row.chapter_number,
                "title": row.title,
                "content": row.content,
                "start_offset": row.start_offset,
                "end_offset": row.end_offset,
                "created_at": row.created_at,
            }
        )

    @staticmethod
    def _story_bible_to_row(story_bible: StoryBibleRecord) -> StoryBibleRow:
        return StoryBibleRow(
            id=story_bible.id,
            project_id=story_bible.project_id,
            source_id=story_bible.source_id,
            version=story_bible.version,
            content_json=story_bible.content.model_dump(mode="json"),
            provider=story_bible.provider,
            model=story_bible.model,
            duration_ms=story_bible.duration_ms,
            created_at=story_bible.created_at,
        )

    @staticmethod
    def _story_bible_from_row(row: StoryBibleRow) -> StoryBibleRecord:
        return StoryBibleRecord.model_validate(
            {
                "id": row.id,
                "project_id": row.project_id,
                "source_id": row.source_id,
                "version": row.version,
                "content": row.content_json,
                "provider": row.provider,
                "model": row.model,
                "duration_ms": row.duration_ms,
                "created_at": row.created_at,
            }
        )

    @staticmethod
    def _episode_to_row(episode: EpisodeRecord) -> EpisodeRow:
        return EpisodeRow(
            id=episode.id,
            project_id=episode.project_id,
            story_bible_id=episode.story_bible_id,
            episode_number=episode.episode_number,
            version=episode.version,
            status=episode.status.value,
            outline_json=episode.outline.model_dump(mode="json"),
            provider=episode.provider,
            model=episode.model,
            duration_ms=episode.duration_ms,
            created_at=episode.created_at,
            updated_at=episode.updated_at,
        )

    @staticmethod
    def _copy_episode_to_row(episode: EpisodeRecord, row: EpisodeRow) -> None:
        row.project_id = episode.project_id
        row.story_bible_id = episode.story_bible_id
        row.episode_number = episode.episode_number
        row.version = episode.version
        row.status = episode.status.value
        row.outline_json = episode.outline.model_dump(mode="json")
        row.provider = episode.provider
        row.model = episode.model
        row.duration_ms = episode.duration_ms
        row.created_at = episode.created_at
        row.updated_at = episode.updated_at

    @staticmethod
    def _episode_from_row(row: EpisodeRow) -> EpisodeRecord:
        return EpisodeRecord.model_validate(
            {
                "id": row.id,
                "project_id": row.project_id,
                "story_bible_id": row.story_bible_id,
                "episode_number": row.episode_number,
                "version": row.version,
                "status": row.status,
                "outline": row.outline_json,
                "provider": row.provider,
                "model": row.model,
                "duration_ms": row.duration_ms,
                "created_at": row.created_at,
                "updated_at": row.updated_at,
            }
        )

    @staticmethod
    def _episode_script_to_row(script: EpisodeScriptRecord) -> EpisodeScriptRow:
        return EpisodeScriptRow(
            id=script.id,
            project_id=script.project_id,
            episode_id=script.episode_id,
            version=script.version,
            content_json=script.content.model_dump(mode="json"),
            provider=script.provider,
            model=script.model,
            duration_ms=script.duration_ms,
            created_at=script.created_at,
        )

    @staticmethod
    def _episode_script_from_row(row: EpisodeScriptRow) -> EpisodeScriptRecord:
        return EpisodeScriptRecord.model_validate(
            {
                "id": row.id,
                "project_id": row.project_id,
                "episode_id": row.episode_id,
                "version": row.version,
                "content": row.content_json,
                "provider": row.provider,
                "model": row.model,
                "duration_ms": row.duration_ms,
                "created_at": row.created_at,
            }
        )

    @staticmethod
    def _episode_script_draft_to_row(
        draft: EpisodeScriptDraftRecord,
    ) -> EpisodeScriptDraftRow:
        return EpisodeScriptDraftRow(
            id=draft.id,
            project_id=draft.project_id,
            episode_id=draft.episode_id,
            base_script_id=draft.base_script_id,
            base_script_version=draft.base_script_version,
            revision=draft.revision,
            content_json=draft.content.model_dump(mode="json"),
            created_at=draft.created_at,
            updated_at=draft.updated_at,
        )

    @staticmethod
    def _episode_script_draft_from_row(
        row: EpisodeScriptDraftRow,
    ) -> EpisodeScriptDraftRecord:
        return EpisodeScriptDraftRecord.model_validate(
            {
                "id": row.id,
                "project_id": row.project_id,
                "episode_id": row.episode_id,
                "base_script_id": row.base_script_id,
                "base_script_version": row.base_script_version,
                "revision": row.revision,
                "content": row.content_json,
                "created_at": row.created_at,
                "updated_at": row.updated_at,
            }
        )

    @staticmethod
    def _shot_list_to_row(shot_list: ShotListRecord) -> ShotListRow:
        return ShotListRow(
            id=shot_list.id,
            project_id=shot_list.project_id,
            episode_id=shot_list.episode_id,
            script_id=shot_list.script_id,
            version=shot_list.version,
            shots_json=[shot.model_dump(mode="json") for shot in shot_list.shots],
            provider=shot_list.provider,
            model=shot_list.model,
            duration_ms=shot_list.duration_ms,
            created_at=shot_list.created_at,
        )

    @staticmethod
    def _shot_list_from_row(row: ShotListRow) -> ShotListRecord:
        shots = SqlAlchemyStore._normalize_legacy_shots(row.shots_json)
        return ShotListRecord.model_validate(
            {
                "id": row.id,
                "project_id": row.project_id,
                "episode_id": row.episode_id,
                "script_id": row.script_id,
                "version": row.version,
                "shots": shots,
                "provider": row.provider,
                "model": row.model,
                "duration_ms": row.duration_ms,
                "created_at": row.created_at,
            }
        )

    @staticmethod
    def _normalize_legacy_shots(shots: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Fill fields added to asset references after older rows were persisted."""
        normalized_shots: list[dict[str, Any]] = []
        for shot in shots:
            normalized_shot = dict(shot)
            normalized_refs: list[dict[str, Any]] = []
            for reference in normalized_shot.get("asset_refs", []):
                normalized_reference = dict(reference)
                normalized_reference.setdefault("match_kind", "name")
                normalized_reference.setdefault(
                    "matched_text",
                    normalized_reference.get("name", "legacy asset"),
                )
                normalized_refs.append(normalized_reference)
            normalized_shot["asset_refs"] = normalized_refs
            normalized_shots.append(normalized_shot)
        return normalized_shots

    @staticmethod
    def _asset_to_row(asset: AssetRecord) -> AssetRow:
        return AssetRow(
            id=asset.id,
            asset_key=asset.asset_key,
            project_id=asset.project_id,
            story_bible_id=asset.story_bible_id,
            asset_type=asset.asset_type.value,
            name=asset.name,
            aliases_json=asset.aliases,
            version=asset.version,
            status=asset.status.value,
            content_json=asset.content.model_dump(mode="json"),
            source_chapter_numbers_json=asset.source_chapter_numbers,
            provider=asset.provider,
            model=asset.model,
            duration_ms=asset.duration_ms,
            created_at=asset.created_at,
            updated_at=asset.updated_at,
        )

    @staticmethod
    def _asset_from_row(row: AssetRow) -> AssetRecord:
        return AssetRecord.model_validate(
            {
                "id": row.id,
                "asset_key": row.asset_key,
                "project_id": row.project_id,
                "story_bible_id": row.story_bible_id,
                "asset_type": row.asset_type,
                "name": row.name,
                "aliases": row.aliases_json or [],
                "version": row.version,
                "status": row.status,
                "content": row.content_json,
                "source_chapter_numbers": row.source_chapter_numbers_json or [],
                "provider": row.provider,
                "model": row.model,
                "duration_ms": row.duration_ms,
                "created_at": row.created_at,
                "updated_at": row.updated_at,
            }
        )

    @staticmethod
    def _asset_review_to_row(review: AssetReviewRecord) -> AssetReviewRow:
        return AssetReviewRow(
            id=review.id,
            asset_key=review.asset_key,
            asset_id=review.asset_id,
            version=review.version,
            from_status=review.from_status.value,
            to_status=review.to_status.value,
            reviewer=review.reviewer,
            comment=review.comment,
            created_at=review.created_at,
        )

    @staticmethod
    def _asset_review_from_row(row: AssetReviewRow) -> AssetReviewRecord:
        return AssetReviewRecord.model_validate(
            {
                "id": row.id,
                "asset_key": row.asset_key,
                "asset_id": row.asset_id,
                "version": row.version,
                "from_status": row.from_status,
                "to_status": row.to_status,
                "reviewer": row.reviewer,
                "comment": row.comment,
                "created_at": row.created_at,
            }
        )

    @staticmethod
    def _audit_log_to_row(log: AuditLogRecord) -> AuditLogRow:
        return AuditLogRow(
            id=log.id,
            project_id=log.project_id,
            episode_id=log.episode_id,
            entity_type=log.entity_type.value,
            entity_id=log.entity_id,
            action=log.action.value,
            actor_id=log.actor_id,
            actor_name=log.actor_name,
            metadata_json=log.metadata,
            created_at=log.created_at,
        )

    @staticmethod
    def _audit_log_from_row(row: AuditLogRow) -> AuditLogRecord:
        return AuditLogRecord.model_validate(
            {
                "id": row.id,
                "project_id": row.project_id,
                "episode_id": row.episode_id,
                "entity_type": row.entity_type,
                "entity_id": row.entity_id,
                "action": row.action,
                "actor_id": row.actor_id,
                "actor_name": row.actor_name,
                "metadata": row.metadata_json or {},
                "created_at": row.created_at,
            }
        )

    @staticmethod
    def _reference_image_to_row(image: ReferenceImageRecord) -> ReferenceImageRow:
        data = image.model_dump(mode="json")
        return ReferenceImageRow(
            id=image.id,
            task_id=image.task_id,
            project_id=image.project_id,
            asset_id=image.asset_id,
            asset_key=image.asset_key,
            asset_type=image.asset_type.value,
            asset_version=image.asset_version,
            prompt=image.prompt,
            negative_prompt=image.negative_prompt,
            provider=image.provider,
            model=image.model,
            status=image.status.value,
            output_uri=image.output_uri,
            width=image.width,
            height=image.height,
            duration_ms=image.duration_ms,
            metadata_json=data["metadata"],
            error_json=data["error"],
            created_at=image.created_at,
            updated_at=image.updated_at,
        )

    @staticmethod
    def _copy_reference_image_to_row(image: ReferenceImageRecord, row: ReferenceImageRow) -> None:
        data = image.model_dump(mode="json")
        row.task_id = image.task_id
        row.project_id = image.project_id
        row.asset_id = image.asset_id
        row.asset_key = image.asset_key
        row.asset_type = image.asset_type.value
        row.asset_version = image.asset_version
        row.prompt = image.prompt
        row.negative_prompt = image.negative_prompt
        row.provider = image.provider
        row.model = image.model
        row.status = image.status.value
        row.output_uri = image.output_uri
        row.width = image.width
        row.height = image.height
        row.duration_ms = image.duration_ms
        row.metadata_json = data["metadata"]
        row.error_json = data["error"]
        row.created_at = image.created_at
        row.updated_at = image.updated_at

    @staticmethod
    def _reference_image_from_row(row: ReferenceImageRow) -> ReferenceImageRecord:
        return ReferenceImageRecord.model_validate(
            {
                "id": row.id,
                "task_id": row.task_id,
                "project_id": row.project_id,
                "asset_id": row.asset_id,
                "asset_key": row.asset_key,
                "asset_type": row.asset_type,
                "asset_version": row.asset_version,
                "prompt": row.prompt,
                "negative_prompt": row.negative_prompt,
                "provider": row.provider,
                "model": row.model,
                "status": row.status,
                "output_uri": row.output_uri,
                "width": row.width,
                "height": row.height,
                "duration_ms": row.duration_ms,
                "metadata": row.metadata_json or {},
                "error": row.error_json,
                "created_at": row.created_at,
                "updated_at": row.updated_at,
            }
        )

    @staticmethod
    def _voice_asset_to_row(voice_asset: VoiceAssetRecord) -> VoiceAssetRow:
        return VoiceAssetRow(
            id=voice_asset.id,
            project_id=voice_asset.project_id,
            character_asset_key=voice_asset.character_asset_key,
            label=voice_asset.label,
            language=voice_asset.language,
            provider=voice_asset.provider,
            model=voice_asset.model,
            voice=voice_asset.voice,
            rate=voice_asset.rate,
            volume=voice_asset.volume,
            style=voice_asset.style,
            status=voice_asset.status.value,
            metadata_json=voice_asset.metadata,
            created_at=voice_asset.created_at,
            updated_at=voice_asset.updated_at,
        )

    @staticmethod
    def _copy_voice_asset_to_row(voice_asset: VoiceAssetRecord, row: VoiceAssetRow) -> None:
        row.project_id = voice_asset.project_id
        row.character_asset_key = voice_asset.character_asset_key
        row.label = voice_asset.label
        row.language = voice_asset.language
        row.provider = voice_asset.provider
        row.model = voice_asset.model
        row.voice = voice_asset.voice
        row.rate = voice_asset.rate
        row.volume = voice_asset.volume
        row.style = voice_asset.style
        row.status = voice_asset.status.value
        row.metadata_json = voice_asset.metadata
        row.created_at = voice_asset.created_at
        row.updated_at = voice_asset.updated_at

    @staticmethod
    def _voice_asset_from_row(row: VoiceAssetRow) -> VoiceAssetRecord:
        return VoiceAssetRecord.model_validate(
            {
                "id": row.id,
                "project_id": row.project_id,
                "character_asset_key": row.character_asset_key,
                "label": row.label,
                "language": row.language,
                "provider": row.provider,
                "model": row.model,
                "voice": row.voice,
                "rate": row.rate,
                "volume": row.volume,
                "style": row.style,
                "status": row.status,
                "metadata": row.metadata_json or {},
                "created_at": row.created_at,
                "updated_at": row.updated_at,
            }
        )
