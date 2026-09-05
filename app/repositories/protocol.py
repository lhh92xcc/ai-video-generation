"""Repository protocol shared by in-memory and PostgreSQL implementations."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.domain.models import (
    AssetRecord,
    AssetReviewRecord,
    AssetType,
    AuditEntityType,
    AuditLogRecord,
    ArtifactRecord,
    ChapterRecord,
    EpisodeRecord,
    EpisodeScriptDraftRecord,
    EpisodeScriptRecord,
    GenerationTaskRecord,
    TaskBatchRecord,
    NovelProjectRecord,
    ProjectInvitationRecord,
    NovelSourceRecord,
    ProjectRecord,
    ProjectMemberRecord,
    ReferenceImageRecord,
    ShotListRecord,
    StoryBibleRecord,
    VoiceAssetRecord,
)


class ProjectTaskStore(Protocol):
    async def create_project(self, project: ProjectRecord) -> ProjectRecord:
        ...

    async def get_project(self, project_id: UUID) -> ProjectRecord | None:
        ...

    async def list_projects(self) -> list[ProjectRecord]:
        ...

    async def update_project(self, project: ProjectRecord) -> ProjectRecord:
        ...

    async def create_task(
        self,
        task: GenerationTaskRecord,
        idempotency_key: str | None = None,
    ) -> tuple[GenerationTaskRecord, bool]:
        ...

    async def get_task(self, task_id: UUID) -> GenerationTaskRecord | None:
        ...

    async def list_tasks(
        self,
        project_id: UUID | None = None,
        kind: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[GenerationTaskRecord]:
        ...

    async def update_task(self, task: GenerationTaskRecord) -> GenerationTaskRecord:
        ...

    async def create_task_batch(
        self,
        batch: TaskBatchRecord,
        idempotency_key: str | None = None,
    ) -> tuple[TaskBatchRecord, bool]:
        ...

    async def get_task_batch(self, batch_id: UUID) -> TaskBatchRecord | None:
        ...

    async def get_task_batch_by_idempotency_key(
        self,
        project_id: UUID,
        idempotency_key: str,
    ) -> TaskBatchRecord | None:
        ...

    async def list_task_batches(
        self,
        project_id: UUID,
        limit: int = 50,
    ) -> list[TaskBatchRecord]:
        ...

    async def update_task_batch(self, batch: TaskBatchRecord) -> TaskBatchRecord:
        ...

    async def get_artifact(self, artifact_id: UUID) -> ArtifactRecord | None:
        ...

    async def list_artifacts(
        self,
        project_id: UUID | None = None,
        artifact_type: str | None = None,
        limit: int = 100,
    ) -> list[ArtifactRecord]:
        ...


class NovelStore(Protocol):
    async def create_novel_project(self, project: NovelProjectRecord) -> NovelProjectRecord:
        ...

    async def get_novel_project(self, project_id: UUID) -> NovelProjectRecord | None:
        ...

    async def list_novel_projects(self) -> list[NovelProjectRecord]:
        ...

    async def update_novel_project(self, project: NovelProjectRecord) -> NovelProjectRecord:
        ...

    async def get_novel_project_member(
        self,
        project_id: UUID,
        actor_id: str,
    ) -> ProjectMemberRecord | None:
        ...

    async def list_novel_project_members(self, project_id: UUID) -> list[ProjectMemberRecord]:
        ...

    async def upsert_novel_project_member(self, member: ProjectMemberRecord) -> ProjectMemberRecord:
        ...

    async def delete_novel_project_member(self, project_id: UUID, actor_id: str) -> None:
        ...

    async def create_novel_project_invitation(
        self,
        invitation: ProjectInvitationRecord,
    ) -> ProjectInvitationRecord:
        ...

    async def get_novel_project_invitation(
        self,
        invitation_id: UUID,
    ) -> ProjectInvitationRecord | None:
        ...

    async def list_novel_project_invitations(
        self,
        project_id: UUID,
    ) -> list[ProjectInvitationRecord]:
        ...

    async def update_novel_project_invitation(
        self,
        invitation: ProjectInvitationRecord,
    ) -> ProjectInvitationRecord:
        ...

    async def accept_novel_project_invitation(
        self,
        invitation_id: UUID,
        token_hash: str,
        invitee_actor_id: str,
        now: datetime,
    ) -> tuple[ProjectInvitationRecord, ProjectMemberRecord] | None:
        """Atomically consume an invitation and create its project member."""
        ...

    async def create_novel_source(
        self,
        source: NovelSourceRecord,
        chapters: list[ChapterRecord],
    ) -> tuple[NovelSourceRecord, list[ChapterRecord]]:
        ...

    async def get_novel_source(self, source_id: UUID) -> NovelSourceRecord | None:
        ...

    async def list_chapters(self, source_id: UUID) -> list[ChapterRecord]:
        ...

    async def save_story_bible(self, story_bible: StoryBibleRecord) -> StoryBibleRecord:
        ...

    async def get_latest_story_bible(self, project_id: UUID) -> StoryBibleRecord | None:
        ...

    async def save_episodes(self, episodes: list[EpisodeRecord]) -> list[EpisodeRecord]:
        ...

    async def list_episodes(self, project_id: UUID) -> list[EpisodeRecord]:
        ...

    async def get_episode(self, episode_id: UUID) -> EpisodeRecord | None:
        ...

    async def update_episode(self, episode: EpisodeRecord) -> EpisodeRecord:
        ...

    async def save_episode_script(self, script: EpisodeScriptRecord) -> EpisodeScriptRecord:
        ...

    async def get_latest_episode_script(self, episode_id: UUID) -> EpisodeScriptRecord | None:
        ...

    async def get_episode_script_draft(
        self,
        episode_id: UUID,
    ) -> EpisodeScriptDraftRecord | None:
        ...

    async def save_episode_script_draft(
        self,
        draft: EpisodeScriptDraftRecord,
        expected_revision: int,
    ) -> EpisodeScriptDraftRecord | None:
        """Save only when the current revision equals expected_revision."""
        ...

    async def delete_episode_script_draft(self, episode_id: UUID) -> None:
        ...

    async def save_shot_list(self, shot_list: ShotListRecord) -> ShotListRecord:
        ...

    async def get_latest_shot_list(self, episode_id: UUID) -> ShotListRecord | None:
        ...

    async def save_asset_version(self, asset: AssetRecord) -> AssetRecord:
        ...

    async def list_assets(
        self,
        project_id: UUID,
        asset_type: AssetType | None = None,
    ) -> list[AssetRecord]:
        ...

    async def get_asset(self, asset_id: UUID) -> AssetRecord | None:
        ...

    async def save_asset_review(self, review: AssetReviewRecord) -> AssetReviewRecord:
        ...

    async def list_asset_reviews(self, asset_key: UUID) -> list[AssetReviewRecord]:
        ...

    async def save_audit_log(self, log: AuditLogRecord) -> AuditLogRecord:
        ...

    async def list_audit_logs(
        self,
        project_id: UUID,
        entity_type: AuditEntityType | None = None,
        entity_id: UUID | None = None,
        limit: int = 100,
    ) -> list[AuditLogRecord]:
        ...

    async def save_reference_image(self, image: ReferenceImageRecord) -> ReferenceImageRecord:
        ...

    async def get_reference_image(self, image_id: UUID) -> ReferenceImageRecord | None:
        ...

    async def list_reference_images(self, asset_id: UUID) -> list[ReferenceImageRecord]:
        ...

    async def save_voice_asset(self, voice_asset: VoiceAssetRecord) -> VoiceAssetRecord:
        ...

    async def get_voice_asset(self, voice_asset_id: UUID) -> VoiceAssetRecord | None:
        ...

    async def list_voice_assets(self, project_id: UUID) -> list[VoiceAssetRecord]:
        ...
