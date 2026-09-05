"""In-memory repository for the first phase.

The repository intentionally has an async interface so it can be replaced by a
PostgreSQL implementation without changing service or route contracts.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from uuid import UUID

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
    EpisodeScriptRecord,
    GenerationTaskRecord,
    TaskBatchRecord,
    NovelProjectRecord,
    NovelSourceRecord,
    ProjectRecord,
    ProjectMemberRecord,
    ProjectInvitationRecord,
    ProjectInvitationStatus,
    ReferenceImageRecord,
    ShotListRecord,
    StoryBibleRecord,
    VoiceAssetRecord,
    EpisodeScriptDraftRecord,
    utc_now,
)


class InMemoryStore:
    def __init__(self) -> None:
        self._projects: dict[UUID, ProjectRecord] = {}
        self._tasks: dict[UUID, GenerationTaskRecord] = {}
        self._artifacts: dict[UUID, ArtifactRecord] = {}
        self._idempotency_keys: dict[tuple[UUID, str], UUID] = {}
        self._task_batches: dict[UUID, TaskBatchRecord] = {}
        self._batch_idempotency_keys: dict[tuple[UUID, str], UUID] = {}
        self._novel_projects: dict[UUID, NovelProjectRecord] = {}
        self._novel_project_members: dict[tuple[UUID, str], ProjectMemberRecord] = {}
        self._novel_project_invitations: dict[UUID, ProjectInvitationRecord] = {}
        self._novel_sources: dict[UUID, NovelSourceRecord] = {}
        self._chapters: dict[UUID, list[ChapterRecord]] = {}
        self._story_bibles: dict[UUID, list[StoryBibleRecord]] = {}
        self._episodes: dict[UUID, EpisodeRecord] = {}
        self._episode_scripts: dict[UUID, EpisodeScriptRecord] = {}
        self._episode_script_drafts: dict[UUID, EpisodeScriptDraftRecord] = {}
        self._shot_lists: dict[UUID, ShotListRecord] = {}
        self._assets: dict[UUID, AssetRecord] = {}
        self._asset_reviews: dict[UUID, AssetReviewRecord] = {}
        self._audit_logs: dict[UUID, AuditLogRecord] = {}
        self._reference_images: dict[UUID, ReferenceImageRecord] = {}
        self._voice_assets: dict[UUID, VoiceAssetRecord] = {}
        self._lock = asyncio.Lock()

    async def create_project(self, project: ProjectRecord) -> ProjectRecord:
        async with self._lock:
            self._projects[project.id] = project
            return project.model_copy(deep=True)

    async def get_project(self, project_id: UUID) -> ProjectRecord | None:
        async with self._lock:
            project = self._projects.get(project_id)
            return project.model_copy(deep=True) if project else None

    async def list_projects(self) -> list[ProjectRecord]:
        async with self._lock:
            projects = sorted(self._projects.values(), key=lambda item: item.created_at, reverse=True)
            return [project.model_copy(deep=True) for project in projects]

    async def update_project(self, project: ProjectRecord) -> ProjectRecord:
        async with self._lock:
            self._projects[project.id] = project
            return project.model_copy(deep=True)

    async def create_task(
        self,
        task: GenerationTaskRecord,
        idempotency_key: str | None = None,
    ) -> tuple[GenerationTaskRecord, bool]:
        async with self._lock:
            if idempotency_key:
                key = (task.project_id, idempotency_key)
                existing_id = self._idempotency_keys.get(key)
                if existing_id:
                    existing = self._tasks[existing_id]
                    return existing.model_copy(deep=True), True
                self._idempotency_keys[key] = task.id

            self._tasks[task.id] = task
            self._sync_artifacts(task)
            return task.model_copy(deep=True), False

    async def get_task(self, task_id: UUID) -> GenerationTaskRecord | None:
        async with self._lock:
            task = self._tasks.get(task_id)
            return task.model_copy(deep=True) if task else None

    async def create_task_batch(
        self,
        batch: TaskBatchRecord,
        idempotency_key: str | None = None,
    ) -> tuple[TaskBatchRecord, bool]:
        async with self._lock:
            if idempotency_key:
                key = (batch.project_id, idempotency_key)
                existing_id = self._batch_idempotency_keys.get(key)
                if existing_id:
                    return self._task_batches[existing_id].model_copy(deep=True), True
                self._batch_idempotency_keys[key] = batch.id
            self._task_batches[batch.id] = batch
            return batch.model_copy(deep=True), False

    async def get_task_batch(self, batch_id: UUID) -> TaskBatchRecord | None:
        async with self._lock:
            batch = self._task_batches.get(batch_id)
            return batch.model_copy(deep=True) if batch else None

    async def get_task_batch_by_idempotency_key(
        self,
        project_id: UUID,
        idempotency_key: str,
    ) -> TaskBatchRecord | None:
        async with self._lock:
            batch_id = self._batch_idempotency_keys.get((project_id, idempotency_key))
            batch = self._task_batches.get(batch_id) if batch_id else None
            return batch.model_copy(deep=True) if batch else None

    async def list_task_batches(self, project_id: UUID, limit: int = 50) -> list[TaskBatchRecord]:
        async with self._lock:
            batches = [batch for batch in self._task_batches.values() if batch.project_id == project_id]
            batches.sort(key=lambda item: item.updated_at, reverse=True)
            return [batch.model_copy(deep=True) for batch in batches[:limit]]

    async def update_task_batch(self, batch: TaskBatchRecord) -> TaskBatchRecord:
        async with self._lock:
            self._task_batches[batch.id] = batch
            return batch.model_copy(deep=True)

    async def list_tasks(
        self,
        project_id: UUID | None = None,
        kind: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[GenerationTaskRecord]:
        async with self._lock:
            tasks = [
                task
                for task in self._tasks.values()
                if (project_id is None or task.project_id == project_id)
                and (kind is None or task.kind.value == kind)
                and (status is None or task.status.value == status)
            ]
            tasks.sort(key=lambda item: item.updated_at, reverse=True)
            return [task.model_copy(deep=True) for task in tasks[:limit]]

    async def update_task(self, task: GenerationTaskRecord) -> GenerationTaskRecord:
        async with self._lock:
            self._tasks[task.id] = task
            self._sync_artifacts(task)
            return task.model_copy(deep=True)

    async def get_artifact(self, artifact_id: UUID) -> ArtifactRecord | None:
        async with self._lock:
            artifact = self._artifacts.get(artifact_id)
            return artifact.model_copy(deep=True) if artifact else None

    async def list_artifacts(
        self,
        project_id: UUID | None = None,
        artifact_type: str | None = None,
        limit: int = 100,
    ) -> list[ArtifactRecord]:
        async with self._lock:
            artifacts = [
                artifact
                for artifact in self._artifacts.values()
                if (project_id is None or artifact.project_id == project_id)
                and (artifact_type is None or artifact.type == artifact_type)
            ]
            artifacts.sort(key=lambda item: item.created_at, reverse=True)
            return [artifact.model_copy(deep=True) for artifact in artifacts[:limit]]

    def _sync_artifacts(self, task: GenerationTaskRecord) -> None:
        for artifact in task.artifacts:
            self._artifacts[artifact.id] = ArtifactRecord(
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

    async def create_novel_project(self, project: NovelProjectRecord) -> NovelProjectRecord:
        async with self._lock:
            self._novel_projects[project.id] = project
            return project.model_copy(deep=True)

    async def get_novel_project(self, project_id: UUID) -> NovelProjectRecord | None:
        async with self._lock:
            project = self._novel_projects.get(project_id)
            return project.model_copy(deep=True) if project else None

    async def list_novel_projects(self) -> list[NovelProjectRecord]:
        async with self._lock:
            projects = sorted(
                self._novel_projects.values(),
                key=lambda item: item.updated_at,
                reverse=True,
            )
            return [project.model_copy(deep=True) for project in projects]

    async def update_novel_project(self, project: NovelProjectRecord) -> NovelProjectRecord:
        async with self._lock:
            self._novel_projects[project.id] = project
            return project.model_copy(deep=True)

    async def get_novel_project_member(
        self,
        project_id: UUID,
        actor_id: str,
    ) -> ProjectMemberRecord | None:
        async with self._lock:
            member = self._novel_project_members.get((project_id, actor_id))
            return member.model_copy(deep=True) if member else None

    async def list_novel_project_members(self, project_id: UUID) -> list[ProjectMemberRecord]:
        async with self._lock:
            members = [
                member
                for (member_project_id, _), member in self._novel_project_members.items()
                if member_project_id == project_id
            ]
            return [
                member.model_copy(deep=True)
                for member in sorted(members, key=lambda item: item.actor_id)
            ]

    async def upsert_novel_project_member(self, member: ProjectMemberRecord) -> ProjectMemberRecord:
        async with self._lock:
            key = (member.project_id, member.actor_id)
            current = self._novel_project_members.get(key)
            saved = member.model_copy(
                update={
                    "id": current.id if current else member.id,
                    "created_at": current.created_at if current else member.created_at,
                },
                deep=True,
            )
            self._novel_project_members[key] = saved
            return saved.model_copy(deep=True)

    async def delete_novel_project_member(self, project_id: UUID, actor_id: str) -> None:
        async with self._lock:
            self._novel_project_members.pop((project_id, actor_id), None)

    async def create_novel_project_invitation(
        self,
        invitation: ProjectInvitationRecord,
    ) -> ProjectInvitationRecord:
        async with self._lock:
            self._novel_project_invitations[invitation.id] = invitation
            return invitation.model_copy(deep=True)

    async def get_novel_project_invitation(
        self,
        invitation_id: UUID,
    ) -> ProjectInvitationRecord | None:
        async with self._lock:
            invitation = self._novel_project_invitations.get(invitation_id)
            return invitation.model_copy(deep=True) if invitation else None

    async def list_novel_project_invitations(
        self,
        project_id: UUID,
    ) -> list[ProjectInvitationRecord]:
        async with self._lock:
            invitations = [
                invitation
                for invitation in self._novel_project_invitations.values()
                if invitation.project_id == project_id
            ]
            invitations.sort(key=lambda item: item.created_at, reverse=True)
            return [invitation.model_copy(deep=True) for invitation in invitations]

    async def update_novel_project_invitation(
        self,
        invitation: ProjectInvitationRecord,
    ) -> ProjectInvitationRecord:
        async with self._lock:
            self._novel_project_invitations[invitation.id] = invitation
            return invitation.model_copy(deep=True)

    async def accept_novel_project_invitation(
        self,
        invitation_id: UUID,
        token_hash: str,
        invitee_actor_id: str,
        now: datetime,
    ) -> tuple[ProjectInvitationRecord, ProjectMemberRecord] | None:
        async with self._lock:
            invitation = self._novel_project_invitations.get(invitation_id)
            if invitation is None:
                return None
            if (
                invitation.status == ProjectInvitationStatus.PENDING
                and invitation.expires_at <= now
            ):
                invitation = invitation.model_copy(
                    update={
                        "status": ProjectInvitationStatus.EXPIRED,
                        "updated_at": now,
                    }
                )
                self._novel_project_invitations[invitation.id] = invitation
                return None
            if (
                invitation.status != ProjectInvitationStatus.PENDING
                or invitation.token_hash != token_hash
                or invitation.invitee_actor_id != invitee_actor_id
            ):
                return None
            member_key = (invitation.project_id, invitee_actor_id)
            if member_key in self._novel_project_members:
                return None
            member = ProjectMemberRecord(
                project_id=invitation.project_id,
                actor_id=invitee_actor_id,
                actor_name=invitation.invitee_name,
                role=invitation.role,
                created_at=now,
                updated_at=now,
            )
            accepted = invitation.model_copy(
                update={
                    "status": ProjectInvitationStatus.ACCEPTED,
                    "accepted_at": now,
                    "accepted_by_actor_id": invitee_actor_id,
                    "updated_at": now,
                }
            )
            self._novel_project_members[member_key] = member
            self._novel_project_invitations[invitation.id] = accepted
            return accepted.model_copy(deep=True), member.model_copy(deep=True)

    async def create_novel_source(
        self,
        source: NovelSourceRecord,
        chapters: list[ChapterRecord],
    ) -> tuple[NovelSourceRecord, list[ChapterRecord]]:
        async with self._lock:
            self._novel_sources[source.id] = source
            self._chapters[source.id] = chapters
            return source.model_copy(deep=True), [chapter.model_copy(deep=True) for chapter in chapters]

    async def get_novel_source(self, source_id: UUID) -> NovelSourceRecord | None:
        async with self._lock:
            source = self._novel_sources.get(source_id)
            return source.model_copy(deep=True) if source else None

    async def list_chapters(self, source_id: UUID) -> list[ChapterRecord]:
        async with self._lock:
            return [chapter.model_copy(deep=True) for chapter in self._chapters.get(source_id, [])]

    async def save_story_bible(self, story_bible: StoryBibleRecord) -> StoryBibleRecord:
        async with self._lock:
            versions = self._story_bibles.setdefault(story_bible.project_id, [])
            story_bible.version = len(versions) + 1
            versions.append(story_bible)
            return story_bible.model_copy(deep=True)

    async def get_latest_story_bible(self, project_id: UUID) -> StoryBibleRecord | None:
        async with self._lock:
            versions = self._story_bibles.get(project_id, [])
            return versions[-1].model_copy(deep=True) if versions else None

    async def save_episodes(self, episodes: list[EpisodeRecord]) -> list[EpisodeRecord]:
        async with self._lock:
            if not episodes:
                return []
            project_id = episodes[0].project_id
            latest_version = max(
                (episode.version for episode in self._episodes.values() if episode.project_id == project_id),
                default=0,
            )
            saved: list[EpisodeRecord] = []
            for episode in episodes:
                versioned = episode.model_copy(
                    update={"version": latest_version + 1},
                    deep=True,
                )
                self._episodes[versioned.id] = versioned
                saved.append(versioned.model_copy(deep=True))
            return saved

    async def list_episodes(self, project_id: UUID) -> list[EpisodeRecord]:
        async with self._lock:
            project_episodes = [
                episode for episode in self._episodes.values() if episode.project_id == project_id
            ]
            latest_version = max(
                (episode.version for episode in project_episodes),
                default=0,
            )
            return [
                episode.model_copy(deep=True)
                for episode in sorted(
                    (
                        episode
                        for episode in project_episodes
                        if episode.version == latest_version
                    ),
                    key=lambda item: item.episode_number,
                )
            ]

    async def get_episode(self, episode_id: UUID) -> EpisodeRecord | None:
        async with self._lock:
            episode = self._episodes.get(episode_id)
            return episode.model_copy(deep=True) if episode else None

    async def update_episode(self, episode: EpisodeRecord) -> EpisodeRecord:
        async with self._lock:
            self._episodes[episode.id] = episode
            return episode.model_copy(deep=True)

    async def save_episode_script(self, script: EpisodeScriptRecord) -> EpisodeScriptRecord:
        async with self._lock:
            latest_version = max(
                (
                    item.version
                    for item in self._episode_scripts.values()
                    if item.episode_id == script.episode_id
                ),
                default=0,
            )
            versioned = script.model_copy(
                update={"version": latest_version + 1},
                deep=True,
            )
            self._episode_scripts[versioned.id] = versioned
            return versioned.model_copy(deep=True)

    async def get_latest_episode_script(self, episode_id: UUID) -> EpisodeScriptRecord | None:
        async with self._lock:
            scripts = [
                item for item in self._episode_scripts.values() if item.episode_id == episode_id
            ]
            latest = max(scripts, key=lambda item: item.version, default=None)
            return latest.model_copy(deep=True) if latest else None

    async def get_episode_script_draft(
        self,
        episode_id: UUID,
    ) -> EpisodeScriptDraftRecord | None:
        async with self._lock:
            draft = self._episode_script_drafts.get(episode_id)
            return draft.model_copy(deep=True) if draft else None

    async def save_episode_script_draft(
        self,
        draft: EpisodeScriptDraftRecord,
        expected_revision: int,
    ) -> EpisodeScriptDraftRecord | None:
        async with self._lock:
            current = self._episode_script_drafts.get(draft.episode_id)
            current_revision = current.revision if current else 0
            if current_revision != expected_revision:
                return None
            saved = draft.model_copy(
                update={
                    "id": current.id if current else draft.id,
                    "revision": expected_revision + 1,
                    "created_at": current.created_at if current else draft.created_at,
                    "updated_at": utc_now(),
                },
                deep=True,
            )
            self._episode_script_drafts[draft.episode_id] = saved
            return saved.model_copy(deep=True)

    async def delete_episode_script_draft(self, episode_id: UUID) -> None:
        async with self._lock:
            self._episode_script_drafts.pop(episode_id, None)

    async def save_shot_list(self, shot_list: ShotListRecord) -> ShotListRecord:
        async with self._lock:
            latest_version = max(
                (
                    item.version
                    for item in self._shot_lists.values()
                    if item.episode_id == shot_list.episode_id
                ),
                default=0,
            )
            versioned = shot_list.model_copy(
                update={"version": latest_version + 1},
                deep=True,
            )
            self._shot_lists[versioned.id] = versioned
            return versioned.model_copy(deep=True)

    async def get_latest_shot_list(self, episode_id: UUID) -> ShotListRecord | None:
        async with self._lock:
            shot_lists = [
                item for item in self._shot_lists.values() if item.episode_id == episode_id
            ]
            latest = max(shot_lists, key=lambda item: item.version, default=None)
            return latest.model_copy(deep=True) if latest else None

    async def save_asset_version(self, asset: AssetRecord) -> AssetRecord:
        async with self._lock:
            latest = max(
                (
                    item
                    for item in self._assets.values()
                    if item.project_id == asset.project_id
                    and item.asset_type == asset.asset_type
                    and item.name == asset.name
                ),
                key=lambda item: item.version,
                default=None,
            )
            versioned = asset.model_copy(
                update={
                    "asset_key": latest.asset_key if latest is not None else asset.asset_key,
                    "version": latest.version + 1 if latest is not None else 1,
                },
                deep=True,
            )
            self._assets[versioned.id] = versioned
            return versioned.model_copy(deep=True)

    async def list_assets(
        self,
        project_id: UUID,
        asset_type: AssetType | None = None,
    ) -> list[AssetRecord]:
        async with self._lock:
            selected = [
                item
                for item in self._assets.values()
                if item.project_id == project_id
                and (asset_type is None or item.asset_type == asset_type)
            ]
            latest: dict[tuple[AssetType, str], AssetRecord] = {}
            for item in selected:
                key = (item.asset_type, item.name)
                if key not in latest or item.version > latest[key].version:
                    latest[key] = item
            return [
                item.model_copy(deep=True)
                for item in sorted(
                    latest.values(),
                    key=lambda value: (value.asset_type.value, value.name),
                )
            ]

    async def get_asset(self, asset_id: UUID) -> AssetRecord | None:
        async with self._lock:
            asset = self._assets.get(asset_id)
            return asset.model_copy(deep=True) if asset else None

    async def save_asset_review(self, review: AssetReviewRecord) -> AssetReviewRecord:
        async with self._lock:
            self._asset_reviews[review.id] = review
            return review.model_copy(deep=True)

    async def list_asset_reviews(self, asset_key: UUID) -> list[AssetReviewRecord]:
        async with self._lock:
            reviews = [
                review
                for review in self._asset_reviews.values()
                if review.asset_key == asset_key
            ]
            return [
                review.model_copy(deep=True)
                for review in sorted(reviews, key=lambda value: value.created_at)
            ]

    async def save_audit_log(self, log: AuditLogRecord) -> AuditLogRecord:
        async with self._lock:
            self._audit_logs[log.id] = log
            return log.model_copy(deep=True)

    async def list_audit_logs(
        self,
        project_id: UUID,
        entity_type: AuditEntityType | None = None,
        entity_id: UUID | None = None,
        limit: int = 100,
    ) -> list[AuditLogRecord]:
        async with self._lock:
            logs = [
                item
                for item in self._audit_logs.values()
                if item.project_id == project_id
                and (entity_type is None or item.entity_type == entity_type)
                and (entity_id is None or item.entity_id == entity_id)
            ]
            return [
                item.model_copy(deep=True)
                for item in sorted(logs, key=lambda value: value.created_at, reverse=True)[:limit]
            ]

    async def save_reference_image(self, image: ReferenceImageRecord) -> ReferenceImageRecord:
        async with self._lock:
            self._reference_images[image.id] = image
            return image.model_copy(deep=True)

    async def get_reference_image(self, image_id: UUID) -> ReferenceImageRecord | None:
        async with self._lock:
            image = self._reference_images.get(image_id)
            return image.model_copy(deep=True) if image else None

    async def list_reference_images(self, asset_id: UUID) -> list[ReferenceImageRecord]:
        async with self._lock:
            images = [
                image for image in self._reference_images.values() if image.asset_id == asset_id
            ]
            return [
                image.model_copy(deep=True)
                for image in sorted(images, key=lambda value: value.created_at, reverse=True)
            ]

    async def save_voice_asset(self, voice_asset: VoiceAssetRecord) -> VoiceAssetRecord:
        async with self._lock:
            current = self._voice_assets.get(voice_asset.id)
            saved = voice_asset.model_copy(
                update={
                    "created_at": current.created_at if current else voice_asset.created_at,
                    "updated_at": utc_now(),
                },
                deep=True,
            )
            self._voice_assets[saved.id] = saved
            return saved.model_copy(deep=True)

    async def get_voice_asset(self, voice_asset_id: UUID) -> VoiceAssetRecord | None:
        async with self._lock:
            voice_asset = self._voice_assets.get(voice_asset_id)
            return voice_asset.model_copy(deep=True) if voice_asset else None

    async def list_voice_assets(self, project_id: UUID) -> list[VoiceAssetRecord]:
        async with self._lock:
            assets = [
                asset for asset in self._voice_assets.values() if asset.project_id == project_id
            ]
            return [
                asset.model_copy(deep=True)
                for asset in sorted(assets, key=lambda item: item.created_at, reverse=True)
            ]
