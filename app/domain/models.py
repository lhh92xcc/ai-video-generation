"""Pydantic models shared by the API, services and in-memory repository."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ProjectStatus(StrEnum):
    DRAFT = "draft"
    GENERATING = "generating"
    READY = "ready"


class NovelProjectStatus(StrEnum):
    DRAFT = "draft"
    ANALYZING = "analyzing"
    READY = "ready"


class RightsStatus(StrEnum):
    UNKNOWN = "unknown"
    PENDING = "pending"
    CONFIRMED = "confirmed"
    DENIED = "denied"


class TaskStatus(StrEnum):
    CREATED = "created"
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"


class GenerationTaskKind(StrEnum):
    INFO_SCRIPT = "info_script"
    NOVEL_STORY_BIBLE = "novel_story_bible"
    NOVEL_EPISODE_PLAN = "novel_episode_plan"
    NOVEL_EPISODE_SCRIPT = "novel_episode_script"
    NOVEL_SHOT_LIST = "novel_shot_list"
    ASSET_REFERENCE_IMAGE = "asset_reference_image"
    VIDEO_CLIP = "video_clip"
    VIDEO_ASSEMBLY = "video_assembly"
    AUDIO_NARRATION = "audio_narration"
    AUDIO_BGM = "audio_bgm"
    SUBTITLE_SRT = "subtitle_srt"
    SUBTITLE_ALIGN = "subtitle_align"
    SUBTITLE_ASR = "subtitle_asr"


class StageName(StrEnum):
    SCRIPT = "script"
    STORY_BIBLE = "story_bible"
    EPISODE_PLAN = "episode_plan"
    EPISODE_SCRIPT = "episode_script"
    SHOT_LIST = "shot_list"
    REFERENCE_IMAGE = "reference_image"
    VIDEO_CLIP = "video_clip"
    VIDEO_ASSEMBLY = "video_assembly"
    AUDIO = "audio"
    SUBTITLE = "subtitle"


class ProjectCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    topic: str = Field(min_length=1, max_length=500)
    language: str = Field(default="zh-CN", min_length=2, max_length=20)
    target_duration_seconds: int = Field(default=60, ge=15, le=180)
    aspect_ratio: Literal["9:16", "16:9", "1:1"] = "9:16"
    tone: str = Field(default="清晰、实用", min_length=1, max_length=80)

    @field_validator("title", "topic", "language", "tone")
    @classmethod
    def strip_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


class ProjectRecord(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    title: str
    topic: str
    language: str
    target_duration_seconds: int
    aspect_ratio: Literal["9:16", "16:9", "1:1"]
    tone: str
    status: ProjectStatus = ProjectStatus.DRAFT
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class NovelProjectCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    language: str = Field(default="zh-CN", min_length=2, max_length=20)
    target_episode_count: int = Field(default=3, ge=1, le=100)
    target_episode_duration_seconds: int = Field(default=90, ge=30, le=600)
    rights_status: RightsStatus = RightsStatus.UNKNOWN

    @field_validator("title", "language")
    @classmethod
    def strip_novel_project_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


class NovelProjectRecord(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    title: str
    language: str
    target_episode_count: int
    target_episode_duration_seconds: int
    rights_status: RightsStatus = RightsStatus.UNKNOWN
    status: NovelProjectStatus = NovelProjectStatus.DRAFT
    source_id: UUID | None = None
    story_bible_id: UUID | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ProjectRole(StrEnum):
    VIEWER = "viewer"
    EDITOR = "editor"
    REVIEWER = "reviewer"
    OWNER = "owner"


class ProjectPermission(StrEnum):
    READ = "project:read"
    MANAGE_TASKS = "project:manage_tasks"
    EDIT_SCRIPT = "script:edit"
    EDIT_ASSET = "asset:edit"
    REVIEW_ASSET = "asset:review"
    MANAGE_MEMBERS = "project:manage_members"


class ProjectMemberRecord(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    project_id: UUID
    actor_id: str = Field(min_length=1, max_length=120)
    actor_name: str = Field(min_length=1, max_length=120)
    role: ProjectRole
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ProjectMemberUpsertRequest(BaseModel):
    actor_name: str = Field(min_length=1, max_length=120)
    role: ProjectRole

    @field_validator("actor_name")
    @classmethod
    def strip_member_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("actor_name must not be blank")
        return value


class ProjectInvitationStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REVOKED = "revoked"
    EXPIRED = "expired"


class ProjectInvitationRecord(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    project_id: UUID
    invitee_actor_id: str = Field(min_length=1, max_length=120)
    invitee_name: str = Field(min_length=1, max_length=120)
    role: ProjectRole
    token_hash: str = Field(min_length=64, max_length=64)
    status: ProjectInvitationStatus = ProjectInvitationStatus.PENDING
    expires_at: datetime
    invited_by_actor_id: str = Field(min_length=1, max_length=120)
    invited_by_name: str = Field(min_length=1, max_length=120)
    accepted_at: datetime | None = None
    accepted_by_actor_id: str | None = Field(default=None, max_length=120)
    revoked_at: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ProjectInvitationCreateRequest(BaseModel):
    invitee_actor_id: str = Field(min_length=1, max_length=120)
    invitee_name: str = Field(min_length=1, max_length=120)
    role: ProjectRole
    expires_in_seconds: int = Field(default=7 * 24 * 60 * 60, ge=300, le=30 * 24 * 60 * 60)

    @field_validator("invitee_actor_id", "invitee_name")
    @classmethod
    def strip_invitee_fields(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("invitee fields must not be blank")
        return value


class ProjectInvitationAcceptRequest(BaseModel):
    token: str = Field(min_length=32, max_length=300)


class ProjectInvitationSummary(BaseModel):
    id: UUID
    project_id: UUID
    invitee_actor_id: str
    invitee_name: str
    role: ProjectRole
    status: ProjectInvitationStatus
    expires_at: datetime
    invited_by_actor_id: str
    invited_by_name: str
    accepted_at: datetime | None = None
    accepted_by_actor_id: str | None = None
    revoked_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ProjectAccessRecord(BaseModel):
    project_id: UUID
    actor_id: str
    actor_name: str
    role: ProjectRole
    permissions: list[ProjectPermission]


class NovelSourceRecord(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    project_id: UUID
    filename: str
    content_type: Literal["text/plain", "text/markdown"]
    size_bytes: int = Field(ge=1)
    checksum: str = Field(min_length=64, max_length=64)
    content: str = Field(min_length=1)
    rights_status: RightsStatus = RightsStatus.UNKNOWN
    chapter_count: int = Field(ge=0)
    created_at: datetime = Field(default_factory=utc_now)


class NovelSourceSummary(BaseModel):
    id: UUID
    project_id: UUID
    filename: str
    content_type: Literal["text/plain", "text/markdown"]
    size_bytes: int
    checksum: str
    rights_status: RightsStatus
    chapter_count: int
    created_at: datetime


class ChapterRecord(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    source_id: UUID
    chapter_number: int = Field(ge=0)
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1)
    start_offset: int = Field(ge=0)
    end_offset: int = Field(ge=1)
    created_at: datetime = Field(default_factory=utc_now)


class CharacterProfile(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    role: str = Field(min_length=1, max_length=80)
    traits: list[str] = Field(default_factory=list, max_length=12)
    appearance: str = Field(default="待审核", max_length=500)
    relationships: list[str] = Field(default_factory=list, max_length=12)
    voice_notes: str = Field(default="待设定", max_length=300)


class LocationProfile(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=500)
    visual_keywords: list[str] = Field(default_factory=list, max_length=12)


class PropProfile(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    purpose: str = Field(min_length=1, max_length=300)
    description: str = Field(min_length=1, max_length=500)
    visual_keywords: list[str] = Field(default_factory=list, max_length=12)
    continuity_notes: str = Field(default="待设定", max_length=500)


class StoryBibleContent(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    logline: str = Field(min_length=1, max_length=500)
    genre: list[str] = Field(min_length=1, max_length=8)
    setting: str = Field(min_length=1, max_length=1000)
    themes: list[str] = Field(min_length=1, max_length=12)
    characters: list[CharacterProfile] = Field(min_length=1, max_length=50)
    locations: list[LocationProfile] = Field(min_length=1, max_length=50)
    props: list[PropProfile] = Field(default_factory=list, max_length=50)
    timeline: list[str] = Field(min_length=1, max_length=30)
    conflicts: list[str] = Field(min_length=1, max_length=20)
    source_chapter_numbers: list[int] = Field(min_length=1, max_length=200)


class StoryBibleRecord(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    project_id: UUID
    source_id: UUID
    version: int = Field(default=1, ge=1)
    content: StoryBibleContent
    provider: str
    model: str
    duration_ms: int = Field(ge=0)
    created_at: datetime = Field(default_factory=utc_now)


class EpisodeStatus(StrEnum):
    DRAFT = "draft"
    PLANNED = "planned"
    SCRIPTED = "scripted"
    SHOTS_READY = "shots_ready"
    NEEDS_REVIEW = "needs_review"


class EpisodeOutlineContent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    episode_number: int = Field(ge=1, le=1000)
    title: str = Field(min_length=1, max_length=120)
    logline: str = Field(min_length=1, max_length=500)
    objective: str = Field(min_length=1, max_length=500)
    conflict: str = Field(min_length=1, max_length=500)
    turning_point: str = Field(min_length=1, max_length=500)
    ending_hook: str = Field(min_length=1, max_length=500)
    source_chapter_numbers: list[int] = Field(min_length=1, max_length=200)
    target_duration_seconds: int = Field(default=90, ge=30, le=600)

    @field_validator(
        "title",
        "logline",
        "objective",
        "conflict",
        "turning_point",
        "ending_hook",
    )
    @classmethod
    def strip_outline_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


class EpisodeRecord(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    project_id: UUID
    story_bible_id: UUID
    episode_number: int = Field(ge=1, le=1000)
    version: int = Field(default=1, ge=1)
    status: EpisodeStatus = EpisodeStatus.DRAFT
    outline: EpisodeOutlineContent
    provider: str
    model: str
    duration_ms: int = Field(ge=0)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class DialogueLine(BaseModel):
    model_config = ConfigDict(extra="forbid")

    line_index: int = Field(ge=1)
    speaker: str = Field(min_length=1, max_length=80)
    text: str = Field(min_length=1, max_length=1000)
    emotion: str = Field(min_length=1, max_length=120)
    delivery_notes: str = Field(default="", max_length=300)

    @field_validator("speaker", "text", "emotion", "delivery_notes")
    @classmethod
    def strip_dialogue_text(cls, value: str) -> str:
        return value.strip()


class SceneScriptContent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scene_index: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=120)
    location: str = Field(min_length=1, max_length=120)
    time: str = Field(min_length=1, max_length=80)
    characters: list[str] = Field(min_length=1, max_length=30)
    duration_seconds: int = Field(ge=1, le=300)
    action: str = Field(min_length=1, max_length=1500)
    narration: str = Field(default="", max_length=1000)
    dialogues: list[DialogueLine] = Field(default_factory=list, max_length=30)
    emotion: str = Field(min_length=1, max_length=120)
    source_chapter_numbers: list[int] = Field(min_length=1, max_length=200)

    @field_validator(
        "title",
        "location",
        "time",
        "action",
        "narration",
        "emotion",
    )
    @classmethod
    def strip_scene_text(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def validate_dialogue_indexes(self) -> "SceneScriptContent":
        expected_indexes = list(range(1, len(self.dialogues) + 1))
        actual_indexes = [dialogue.line_index for dialogue in self.dialogues]
        if actual_indexes != expected_indexes:
            raise ValueError("line_index values must be sequential starting at 1")
        return self


class EpisodeScriptContent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    episode_number: int = Field(ge=1, le=1000)
    title: str = Field(min_length=1, max_length=120)
    logline: str = Field(min_length=1, max_length=500)
    opening_hook: str = Field(min_length=1, max_length=500)
    ending_hook: str = Field(min_length=1, max_length=500)
    total_duration_seconds: int = Field(ge=30, le=600)
    scenes: list[SceneScriptContent] = Field(min_length=1, max_length=100)
    risk_notes: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("title", "logline", "opening_hook", "ending_hook")
    @classmethod
    def strip_episode_script_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @model_validator(mode="after")
    def validate_scene_timeline(self) -> "EpisodeScriptContent":
        expected_indexes = list(range(1, len(self.scenes) + 1))
        actual_indexes = [scene.scene_index for scene in self.scenes]
        if actual_indexes != expected_indexes:
            raise ValueError("scene_index values must be sequential starting at 1")

        total_duration = sum(scene.duration_seconds for scene in self.scenes)
        allowed_delta = max(3, round(self.total_duration_seconds * 0.1))
        if abs(total_duration - self.total_duration_seconds) > allowed_delta:
            raise ValueError("scene durations must be close to total_duration_seconds")
        return self


class EpisodeScriptRecord(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    project_id: UUID
    episode_id: UUID
    version: int = Field(default=1, ge=1)
    content: EpisodeScriptContent
    provider: str
    model: str
    duration_ms: int = Field(ge=0)
    created_at: datetime = Field(default_factory=utc_now)


class EpisodeScriptVersionCreateRequest(BaseModel):
    expected_version: int | None = Field(default=None, ge=1)
    content: EpisodeScriptContent


class EpisodeScriptDraftRecord(BaseModel):
    """The latest recoverable, unpublished script edit for an episode."""

    id: UUID = Field(default_factory=uuid4)
    project_id: UUID
    episode_id: UUID
    base_script_id: UUID
    base_script_version: int = Field(ge=1)
    revision: int = Field(default=1, ge=1)
    content: EpisodeScriptContent
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class EpisodeScriptDraftUpsertRequest(BaseModel):
    """A compare-and-swap request for autosaving a script draft."""

    expected_revision: int = Field(default=0, ge=0)
    content: EpisodeScriptContent


class EpisodeScriptDraftMergePreviewRequest(BaseModel):
    """A read-only three-way merge preview for a local script edit."""

    base_script_id: UUID
    base_script_version: int = Field(ge=1)
    content: EpisodeScriptContent


class EpisodeScriptDraftMergePreviewResponse(BaseModel):
    """The result of comparing the published script, server draft and local edit."""

    episode_id: UUID
    base_script_id: UUID
    base_script_version: int = Field(ge=1)
    current_revision: int = Field(ge=0)
    safe_to_apply: bool
    mergeable_paths: list[str] = Field(default_factory=list)
    conflict_paths: list[str] = Field(default_factory=list)
    merged_content: EpisodeScriptContent | None = None


class AuditEntityType(StrEnum):
    EPISODE_SCRIPT = "episode_script"
    EPISODE_SCRIPT_DRAFT = "episode_script_draft"
    ASSET = "asset"
    ASSET_REVIEW = "asset_review"
    PROJECT_MEMBER = "project_member"
    PROJECT_INVITATION = "project_invitation"


class AuditAction(StrEnum):
    SCRIPT_DRAFT_SAVED = "script_draft_saved"
    SCRIPT_DRAFT_DELETED = "script_draft_deleted"
    SCRIPT_VERSION_PUBLISHED = "script_version_published"
    ASSET_VERSION_CREATED = "asset_version_created"
    ASSET_REVIEW_CREATED = "asset_review_created"
    PROJECT_MEMBER_ADDED = "project_member_added"
    PROJECT_MEMBER_UPDATED = "project_member_updated"
    PROJECT_MEMBER_ROLE_CHANGED = "project_member_role_changed"
    PROJECT_MEMBER_REMOVED = "project_member_removed"
    PROJECT_INVITATION_CREATED = "project_invitation_created"
    PROJECT_INVITATION_ACCEPTED = "project_invitation_accepted"
    PROJECT_INVITATION_REVOKED = "project_invitation_revoked"


class AuditLogRecord(BaseModel):
    """An immutable record of a user-facing content editing operation."""

    id: UUID = Field(default_factory=uuid4)
    project_id: UUID
    episode_id: UUID | None = None
    entity_type: AuditEntityType
    entity_id: UUID
    action: AuditAction
    actor_id: str = Field(min_length=1, max_length=120)
    actor_name: str = Field(min_length=1, max_length=120)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class AssetType(StrEnum):
    CHARACTER = "character"
    LOCATION = "location"
    PROP = "prop"


class AssetStatus(StrEnum):
    DRAFT = "draft"
    NEEDS_REVIEW = "needs_review"
    READY = "ready"
    ARCHIVED = "archived"


class ShotAssetReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_key: UUID
    asset_type: AssetType
    name: str = Field(min_length=1, max_length=120)
    version: int = Field(ge=1)
    status: AssetStatus
    match_kind: Literal["name", "alias"]
    matched_text: str = Field(min_length=1, max_length=120)


class ShotContent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shot_index: int = Field(ge=1)
    scene_index: int = Field(ge=1)
    duration_seconds: int = Field(ge=1, le=120)
    shot_size: Literal[
        "wide",
        "medium",
        "close_up",
        "extreme_close_up",
        "over_the_shoulder",
        "insert",
    ]
    camera_movement: Literal[
        "fixed",
        "pan",
        "tilt",
        "dolly",
        "tracking",
        "handheld",
        "zoom",
    ]
    # Pure prop/environment inserts are valid shots without a visible character.
    characters: list[str] = Field(default_factory=list, max_length=30)
    location: str = Field(min_length=1, max_length=120)
    visual_prompt: str = Field(min_length=1, max_length=1500)
    dialogue_refs: list[int] = Field(default_factory=list, max_length=30)
    audio_requirements: list[str] = Field(min_length=1, max_length=12)
    asset_requirements: list[str] = Field(min_length=1, max_length=20)
    asset_refs: list[ShotAssetReference] = Field(default_factory=list, max_length=20)
    unresolved_asset_requirements: list[str] = Field(default_factory=list, max_length=20)
    asset_binding_warnings: list[str] = Field(default_factory=list, max_length=20)
    continuity_notes: str = Field(default="", max_length=500)

    @field_validator("location", "visual_prompt", "continuity_notes")
    @classmethod
    def strip_shot_text(cls, value: str) -> str:
        return value.strip()


class ShotListRecord(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    project_id: UUID
    episode_id: UUID
    script_id: UUID
    version: int = Field(default=1, ge=1)
    shots: list[ShotContent] = Field(min_length=1, max_length=500)
    provider: str
    model: str
    duration_ms: int = Field(ge=0)
    created_at: datetime = Field(default_factory=utc_now)


class EpisodeScriptImpactShot(BaseModel):
    shot_index: int = Field(ge=1)
    scene_index: int = Field(ge=1)
    requires_regeneration: bool
    asset_gate: Literal["ready", "needs_review", "blocked"]
    unresolved_asset_requirements: list[str] = Field(default_factory=list)
    asset_binding_warnings: list[str] = Field(default_factory=list)


class EpisodeScriptImpactAsset(BaseModel):
    asset_key: UUID
    asset_type: AssetType
    name: str = Field(min_length=1, max_length=120)
    version: int = Field(ge=1)
    status: AssetStatus
    shot_indexes: list[int] = Field(min_length=1)
    requires_review: bool


class EpisodeScriptImpactReport(BaseModel):
    episode_id: UUID
    script_id: UUID
    script_version: int = Field(ge=1)
    shot_list_id: UUID | None = None
    shot_list_version: int | None = Field(default=None, ge=1)
    shot_list_script_id: UUID | None = None
    shot_list_state: Literal["missing", "current", "stale"]
    requires_shot_regeneration: bool
    video_generation_gate: Literal["ready", "blocked"]
    requires_asset_review: bool
    affected_shot_count: int = Field(ge=0)
    unresolved_asset_requirement_count: int = Field(ge=0)
    asset_binding_warning_count: int = Field(ge=0)
    shots: list[EpisodeScriptImpactShot] = Field(default_factory=list)
    assets: list[EpisodeScriptImpactAsset] = Field(default_factory=list)
    recommended_actions: list[
        Literal["generate_shot_list", "regenerate_shot_list", "review_asset_bindings"]
    ] = Field(default_factory=list)


class CharacterAssetContent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    age_range: str = Field(default="待设定", max_length=80)
    role: str = Field(min_length=1, max_length=120)
    traits: list[str] = Field(default_factory=list, max_length=20)
    appearance: str = Field(min_length=1, max_length=1000)
    relationships: list[str] = Field(default_factory=list, max_length=20)
    voice_notes: str = Field(default="待设定", max_length=500)


class LocationAssetContent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str = Field(min_length=1, max_length=1000)
    time_period: str = Field(default="待设定", max_length=120)
    atmosphere: str = Field(default="待设定", max_length=300)
    visual_keywords: list[str] = Field(default_factory=list, max_length=20)


class PropAssetContent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    purpose: str = Field(min_length=1, max_length=300)
    description: str = Field(min_length=1, max_length=1000)
    visual_keywords: list[str] = Field(default_factory=list, max_length=20)
    continuity_notes: str = Field(default="待设定", max_length=500)


AssetContent = CharacterAssetContent | LocationAssetContent | PropAssetContent


class AssetRecord(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    asset_key: UUID = Field(default_factory=uuid4)
    project_id: UUID
    story_bible_id: UUID
    asset_type: AssetType
    name: str = Field(min_length=1, max_length=120)
    aliases: list[str] = Field(default_factory=list, max_length=20)
    version: int = Field(default=1, ge=1)
    status: AssetStatus = AssetStatus.DRAFT
    content: AssetContent
    source_chapter_numbers: list[int] = Field(default_factory=list, max_length=200)
    provider: str
    model: str
    duration_ms: int = Field(ge=0)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("aliases")
    @classmethod
    def normalize_aliases(cls, value: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for alias in value:
            normalized = alias.strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            result.append(normalized)
        return result


class AssetCreateRequest(BaseModel):
    asset_type: AssetType
    name: str = Field(min_length=1, max_length=120)
    aliases: list[str] = Field(default_factory=list, max_length=20)
    status: AssetStatus = AssetStatus.NEEDS_REVIEW
    content: AssetContent
    source_chapter_numbers: list[int] = Field(default_factory=list, max_length=200)

    @field_validator("name")
    @classmethod
    def strip_asset_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @field_validator("aliases")
    @classmethod
    def normalize_aliases(cls, value: list[str]) -> list[str]:
        return AssetRecord.normalize_aliases(value)


class AssetVersionCreateRequest(BaseModel):
    expected_version: int | None = Field(default=None, ge=1)
    status: AssetStatus = AssetStatus.NEEDS_REVIEW
    content: AssetContent
    aliases: list[str] | None = Field(default=None, max_length=20)
    source_chapter_numbers: list[int] = Field(default_factory=list, max_length=200)

    @field_validator("aliases")
    @classmethod
    def normalize_aliases(cls, value: list[str] | None) -> list[str] | None:
        return AssetRecord.normalize_aliases(value) if value is not None else None


class AssetReviewRequest(BaseModel):
    status: AssetStatus
    reviewer: str = Field(min_length=1, max_length=120)
    comment: str = Field(default="", max_length=1000)

    @field_validator("reviewer", "comment")
    @classmethod
    def strip_review_text(cls, value: str) -> str:
        return value.strip()


class AssetBatchReviewRequest(BaseModel):
    asset_ids: list[UUID] | None = Field(default=None, max_length=500)
    status: AssetStatus = AssetStatus.READY
    reviewer: str = Field(min_length=1, max_length=120)
    comment: str = Field(default="", max_length=1000)

    @field_validator("reviewer", "comment")
    @classmethod
    def strip_batch_review_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("status")
    @classmethod
    def only_ready_batch_reviews(cls, value: AssetStatus) -> AssetStatus:
        if value != AssetStatus.READY:
            raise ValueError("Batch asset review only supports ready status")
        return value


class AssetReviewRecord(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    asset_key: UUID
    asset_id: UUID
    version: int = Field(ge=1)
    from_status: AssetStatus
    to_status: AssetStatus
    reviewer: str = Field(min_length=1, max_length=120)
    comment: str = Field(default="", max_length=1000)
    created_at: datetime = Field(default_factory=utc_now)


class AssetReviewResult(BaseModel):
    asset: AssetRecord
    review: AssetReviewRecord


class ReferenceImageStatus(StrEnum):
    CREATED = "created"
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"
    NEEDS_REVIEW = "needs_review"


class ReferenceImageCreateRequest(BaseModel):
    style: str = Field(default="cinematic", min_length=1, max_length=80)
    prompt_override: str | None = Field(default=None, max_length=1500)
    negative_prompt: str = Field(
        default=(
            "blurry, low quality, distorted anatomy, duplicate objects, duplicate person, "
            "split screen, split frame, diptych, triptych, collage, comic panels, "
            "character sheet, multiple views, inset image, repeated face, text, watermark"
        ),
        max_length=1000,
    )
    width: int | None = Field(default=None, ge=256, le=2048)
    height: int | None = Field(default=None, ge=256, le=2048)
    identity_reference_image_id: UUID | None = Field(
        default=None,
        description="Optional succeeded reference image used by an identity adapter workflow.",
    )

    @field_validator("style", "negative_prompt")
    @classmethod
    def strip_reference_image_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @field_validator("prompt_override")
    @classmethod
    def strip_prompt_override(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class ReferenceImageGenerationRequest(BaseModel):
    asset_id: UUID
    asset_key: UUID
    asset_type: AssetType
    asset_version: int = Field(ge=1)
    prompt: str = Field(min_length=1, max_length=2000)
    negative_prompt: str = Field(min_length=1, max_length=1000)
    width: int = Field(ge=256, le=2048)
    height: int = Field(ge=256, le=2048)
    identity_image_bytes: bytes | None = Field(default=None, exclude=True)
    identity_image_mime_type: str | None = Field(default=None, max_length=100, exclude=True)


class ReferenceImageGenerationResult(BaseModel):
    output_uri: str | None = Field(default=None, max_length=2000)
    image_base64: str | None = None
    mime_type: str = Field(default="image/png", min_length=1, max_length=100)
    provider: str = Field(min_length=1, max_length=80)
    model: str = Field(min_length=1, max_length=120)
    width: int = Field(ge=256, le=2048)
    height: int = Field(ge=256, le=2048)
    duration_ms: int = Field(ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class VideoClipCreateRequest(BaseModel):
    prompt_override: str | None = Field(default=None, max_length=1500)
    reference_image_id: UUID | None = None
    negative_prompt: str = Field(
        default="blurry, flicker, distorted anatomy, text, watermark",
        max_length=1000,
    )

    @field_validator("prompt_override", "negative_prompt")
    @classmethod
    def strip_video_prompt(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class VideoClipGenerationRequest(BaseModel):
    episode_id: UUID
    shot_list_id: UUID
    shot_index: int = Field(ge=1)
    duration_seconds: int = Field(ge=1, le=120)
    prompt: str = Field(min_length=1, max_length=2000)
    negative_prompt: str = Field(min_length=1, max_length=1000)
    asset_refs: list[ShotAssetReference] = Field(default_factory=list, max_length=20)
    keyframe_bytes: bytes | None = None
    keyframe_mime_type: str | None = Field(default=None, max_length=100)
    generation_attempt: int = Field(default=1, ge=1, le=100)


class VideoClipGenerationResult(BaseModel):
    output_uri: str | None = Field(default=None, max_length=2000)
    video_base64: str | None = None
    mime_type: str = Field(default="video/mp4", min_length=1, max_length=100)
    provider: str = Field(min_length=1, max_length=80)
    model: str = Field(min_length=1, max_length=120)
    duration_seconds: int = Field(ge=1, le=120)
    duration_ms: int = Field(ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AudioNarrationCreateRequest(BaseModel):
    text: str = Field(min_length=1, max_length=5000)
    voice: str | None = Field(default=None, max_length=120)
    rate: str | None = Field(default=None, max_length=20)
    volume: str | None = Field(default=None, max_length=20)

    @field_validator("text", "voice", "rate", "volume")
    @classmethod
    def strip_audio_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class AudioBGMCreateRequest(BaseModel):
    """Create a BGM Artifact from a configured/licensed local audio file."""

    source_path: str | None = Field(default=None, max_length=500)
    label: str = Field(default="licensed-local-bgm", min_length=1, max_length=120)
    rights_status: RightsStatus = RightsStatus.UNKNOWN
    rights_holder: str | None = Field(default=None, max_length=200)
    rights_reference: str | None = Field(default=None, max_length=500)

    @field_validator("source_path", "label", "rights_holder", "rights_reference")
    @classmethod
    def strip_bgm_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class SubtitleCueRequest(BaseModel):
    start_seconds: float = Field(ge=0, le=3600)
    end_seconds: float = Field(ge=0, le=3600)
    text: str = Field(min_length=1, max_length=1000)

    @field_validator("text")
    @classmethod
    def strip_subtitle_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @model_validator(mode="after")
    def validate_interval(self) -> "SubtitleCueRequest":
        if self.end_seconds <= self.start_seconds:
            raise ValueError("end_seconds must be greater than start_seconds")
        return self


class SubtitleCreateRequest(BaseModel):
    language: str = Field(default="zh-CN", min_length=2, max_length=20)
    cues: list[SubtitleCueRequest] = Field(min_length=1, max_length=500)

    @field_validator("language")
    @classmethod
    def strip_subtitle_language(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @model_validator(mode="after")
    def validate_timeline(self) -> "SubtitleCreateRequest":
        previous_end = 0.0
        for cue in self.cues:
            if cue.start_seconds < previous_end:
                raise ValueError("subtitle cues must be ordered and must not overlap")
            previous_end = cue.end_seconds
        return self


class SubtitleAlignmentCreateRequest(BaseModel):
    """Request for generating subtitle cues from text and an audio duration."""

    text: str = Field(min_length=1, max_length=5000)
    language: str = Field(default="zh-CN", min_length=2, max_length=20)
    audio_duration_seconds: float = Field(gt=0, le=3600)

    @field_validator("text", "language")
    @classmethod
    def strip_alignment_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


class SubtitleAlignmentGenerationResult(BaseModel):
    """Provider output before it is serialized to a subtitle Artifact."""

    cues: list[SubtitleCueRequest] = Field(min_length=1, max_length=500)
    provider: str = Field(min_length=1, max_length=80)
    model: str = Field(min_length=1, max_length=120)
    precision: str = Field(default="sentence_estimate", min_length=1, max_length=80)
    duration_ms: int = Field(ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SubtitleASRCreateRequest(BaseModel):
    """Request for transcribing an existing narration Artifact."""

    audio_artifact_id: UUID
    language: str = Field(default="zh-CN", min_length=2, max_length=20)
    reference_text: str | None = Field(default=None, max_length=5000)
    provider_profile_id: str | None = Field(default=None, min_length=1, max_length=120)

    @field_validator("language", "reference_text", "provider_profile_id")
    @classmethod
    def strip_asr_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class SubtitleASRGenerationRequest(BaseModel):
    """Audio bytes and context passed across the ASR Provider boundary."""

    audio_bytes: bytes = Field(min_length=1)
    mime_type: str = Field(min_length=1, max_length=100)
    language: str = Field(min_length=2, max_length=20)
    audio_duration_seconds: float = Field(gt=0, le=3600)
    reference_text: str | None = Field(default=None, max_length=5000)


class SubtitleASRGenerationResult(BaseModel):
    """ASR Provider output before it is serialized to a subtitle Artifact."""

    cues: list[SubtitleCueRequest] = Field(min_length=1, max_length=500)
    provider: str = Field(min_length=1, max_length=80)
    model: str = Field(min_length=1, max_length=120)
    precision: str = Field(default="segment_asr", min_length=1, max_length=80)
    duration_ms: int = Field(ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProviderProfileSummary(BaseModel):
    """Safe Provider metadata suitable for a frontend selection control."""

    profile_id: str
    capability: Literal["asr"] = "asr"
    label: str
    provider: str
    model: str
    base_url: str
    api_key_env: str | None = None
    configured: bool
    default: bool


class TTSGenerationRequest(BaseModel):
    text: str = Field(min_length=1, max_length=5000)
    voice: str = Field(min_length=1, max_length=120)
    rate: str = Field(min_length=1, max_length=20)
    volume: str = Field(min_length=1, max_length=20)


class TTSGenerationResult(BaseModel):
    audio_base64: str
    mime_type: str = Field(default="audio/mpeg", min_length=1, max_length=100)
    provider: str = Field(min_length=1, max_length=80)
    model: str = Field(min_length=1, max_length=120)
    duration_seconds: float = Field(default=0, ge=0)
    duration_ms: int = Field(ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class BGMGenerationRequest(BaseModel):
    source_path: str | None = Field(default=None, max_length=500)
    label: str = Field(default="licensed-local-bgm", min_length=1, max_length=120)
    rights_status: RightsStatus = RightsStatus.UNKNOWN
    rights_holder: str | None = Field(default=None, max_length=200)
    rights_reference: str | None = Field(default=None, max_length=500)


class BGMGenerationResult(BaseModel):
    audio_base64: str
    mime_type: str = Field(default="audio/mpeg", min_length=1, max_length=100)
    provider: str = Field(min_length=1, max_length=80)
    model: str = Field(min_length=1, max_length=120)
    duration_seconds: float = Field(default=0, ge=0)
    duration_ms: int = Field(ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AudioTrackRequest(BaseModel):
    """One audio artifact placed on the final video timeline."""

    artifact_id: UUID
    track_type: Literal["narration", "bgm"] = "narration"
    start_seconds: float = Field(default=0, ge=0, le=3600)
    volume: float = Field(default=1.0, ge=0, le=2.0)
    loop: bool = False
    fade_in_seconds: float = Field(default=0, ge=0, le=120)
    fade_out_seconds: float = Field(default=0, ge=0, le=120)


class VideoAssemblyCreateRequest(BaseModel):
    # A single clip is valid for a hardware smoke and for short-form drafts;
    # multi-shot production remains supported up to the existing batch limit.
    clip_task_ids: list[UUID] = Field(min_length=1, max_length=500)
    # A short drama may place one measured narration Artifact per shot. Keep
    # enough room for a 10-shot portfolio sample plus optional BGM tracks.
    audio_tracks: list[AudioTrackRequest] = Field(default_factory=list, max_length=32)
    subtitle_artifact_id: UUID | None = None
    output_format: Literal["mp4"] = "mp4"

    @field_validator("clip_task_ids")
    @classmethod
    def require_unique_clip_task_ids(cls, value: list[UUID]) -> list[UUID]:
        if len(set(value)) != len(value):
            raise ValueError("clip_task_ids must not contain duplicates")
        return value

    @field_validator("audio_tracks")
    @classmethod
    def require_unique_audio_artifacts(
        cls,
        value: list[AudioTrackRequest],
    ) -> list[AudioTrackRequest]:
        artifact_ids = [track.artifact_id for track in value]
        if len(set(artifact_ids)) != len(artifact_ids):
            raise ValueError("audio_tracks must not contain duplicate artifact_id values")
        return value


class SceneContent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scene_index: int = Field(ge=1)
    duration_seconds: int = Field(ge=1, le=180)
    voiceover: str = Field(min_length=1, max_length=1000)
    caption: str = Field(min_length=1, max_length=80)
    visual_keywords: list[str] = Field(min_length=1, max_length=8)
    transition: Literal["cut", "fade", "dissolve"]
    risk_notes: list[str] = Field(default_factory=list, max_length=8)

    @field_validator("voiceover", "caption")
    @classmethod
    def strip_content_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


class ScriptContent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=120)
    hook: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=300)
    duration_seconds: int = Field(ge=15, le=180)
    scenes: list[SceneContent] = Field(min_length=1, max_length=24)
    cta: str = Field(min_length=1, max_length=200)
    risk_notes: list[str] = Field(default_factory=list, max_length=12)

    @field_validator("title", "hook", "summary", "cta")
    @classmethod
    def strip_script_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @model_validator(mode="after")
    def validate_scene_timeline(self) -> "ScriptContent":
        expected_indexes = list(range(1, len(self.scenes) + 1))
        actual_indexes = [scene.scene_index for scene in self.scenes]
        if actual_indexes != expected_indexes:
            raise ValueError("scene_index values must be sequential starting at 1")

        total_duration = sum(scene.duration_seconds for scene in self.scenes)
        allowed_delta = max(2, round(self.duration_seconds * 0.1))
        if abs(total_duration - self.duration_seconds) > allowed_delta:
            raise ValueError("scene durations must be close to duration_seconds")
        return self


class StageRun(BaseModel):
    stage: StageName
    status: TaskStatus
    attempt: int = 1
    progress: int = Field(default=0, ge=0, le=100)
    error_code: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class TaskError(BaseModel):
    code: str
    message: str


class ReferenceImageRecord(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    task_id: UUID
    project_id: UUID
    asset_id: UUID
    asset_key: UUID
    asset_type: AssetType
    asset_version: int = Field(ge=1)
    prompt: str = Field(min_length=1, max_length=2000)
    negative_prompt: str = Field(min_length=1, max_length=1000)
    provider: str = Field(default="pending", min_length=1, max_length=80)
    model: str = Field(default="pending", min_length=1, max_length=120)
    status: ReferenceImageStatus = ReferenceImageStatus.CREATED
    output_uri: str | None = Field(default=None, max_length=2000)
    width: int = Field(ge=256, le=2048)
    height: int = Field(ge=256, le=2048)
    duration_ms: int = Field(default=0, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)
    error: TaskError | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ArtifactSummary(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    type: Literal[
        "script_json",
        "story_bible_json",
        "episode_outline_json",
        "episode_script_json",
        "shot_list_json",
        "reference_image",
        "video_clip",
        "rendered_video",
        "audio_narration",
        "audio_bgm",
        "subtitle_srt",
    ]
    status: Literal["ready"] = "ready"
    provider: str
    created_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)
    preview: dict[str, Any] | None = None


class ArtifactRecord(BaseModel):
    """A queryable Artifact Registry record linked to its generation task."""

    id: UUID
    task_id: UUID
    project_id: UUID
    type: Literal[
        "script_json",
        "story_bible_json",
        "episode_outline_json",
        "episode_script_json",
        "shot_list_json",
        "reference_image",
        "video_clip",
        "rendered_video",
        "audio_narration",
        "audio_bgm",
        "subtitle_srt",
    ]
    status: Literal["ready"] = "ready"
    provider: str
    created_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)
    preview: dict[str, Any] | None = None
    download_url: str | None = None


class GenerationTaskRecord(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    project_id: UUID
    kind: GenerationTaskKind = GenerationTaskKind.INFO_SCRIPT
    input_data: dict[str, Any] = Field(default_factory=dict)
    status: TaskStatus = TaskStatus.CREATED
    current_stage: StageName | None = StageName.SCRIPT
    progress: int = Field(default=0, ge=0, le=100)
    error: TaskError | None = None
    stages: list[StageRun] = Field(default_factory=list)
    artifacts: list[ArtifactSummary] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class TaskBatchStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"


class TaskBatchRecord(BaseModel):
    """Durable grouping of tasks for batch progress and resumable retries."""

    id: UUID = Field(default_factory=uuid4)
    project_id: UUID
    task_ids: list[UUID] = Field(min_length=1, max_length=100)
    label: str = Field(default="未命名批次", min_length=1, max_length=120)
    status: TaskBatchStatus = TaskBatchStatus.CREATED
    total_count: int = Field(default=0, ge=0)
    succeeded_count: int = Field(default=0, ge=0)
    failed_count: int = Field(default=0, ge=0)
    active_count: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_counts(self) -> "TaskBatchRecord":
        if self.total_count and self.total_count != len(self.task_ids):
            raise ValueError("total_count must match task_ids")
        if len(set(self.task_ids)) != len(self.task_ids):
            raise ValueError("task_ids must be unique")
        return self


class TaskBatchCreateRequest(BaseModel):
    project_id: UUID
    task_ids: list[UUID] = Field(min_length=1, max_length=100)
    label: str = Field(default="未命名批次", min_length=1, max_length=120)

    @field_validator("label")
    @classmethod
    def strip_batch_label(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("label must not be blank")
        return value

    @field_validator("task_ids")
    @classmethod
    def validate_batch_task_ids(cls, value: list[UUID]) -> list[UUID]:
        if len(set(value)) != len(value):
            raise ValueError("task_ids must be unique")
        return value


class TaskBatchResumeResponse(BaseModel):
    batch: TaskBatchRecord
    retried_task_ids: list[UUID] = Field(default_factory=list)
    skipped_task_ids: list[UUID] = Field(default_factory=list)


class EpisodeTaskPlanAction(StrEnum):
    CREATED = "created"
    REUSED = "reused"
    SKIPPED = "skipped"
    BLOCKED = "blocked"


class EpisodeTaskPlanCreateRequest(BaseModel):
    """Create the next dependency-ready task for each selected episode.

    ``production_mode`` keeps the original content-only planner behavior as the
    backwards-compatible default. When enabled, the planner continues through
    reviewed assets, narration, subtitles, optional BGM, shot clips and final
    assembly. It still creates only the next ready stage because later stages
    depend on asynchronous Artifacts produced by earlier stages.
    """

    episode_ids: list[UUID] | None = Field(default=None, min_length=1, max_length=100)
    label: str = Field(default="分集生产计划", min_length=1, max_length=120)
    production_mode: bool = False
    include_reference_images: bool = True
    include_narration: bool = True
    include_subtitles: bool = True
    include_bgm: bool = False
    include_video: bool = True
    include_assembly: bool = True
    subtitle_mode: Literal["align", "asr"] = "align"
    provider_profile_id: str | None = Field(default=None, min_length=1, max_length=120)
    bgm_source_path: str | None = Field(default=None, max_length=500)
    bgm_label: str = Field(default="licensed-local-bgm", min_length=1, max_length=120)
    bgm_rights_status: RightsStatus = RightsStatus.UNKNOWN
    bgm_rights_holder: str | None = Field(default=None, max_length=200)
    bgm_rights_reference: str | None = Field(default=None, max_length=500)

    @field_validator("label")
    @classmethod
    def strip_plan_label(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("label must not be blank")
        return value

    @field_validator("episode_ids")
    @classmethod
    def validate_plan_episode_ids(cls, value: list[UUID] | None) -> list[UUID] | None:
        if value is not None and len(set(value)) != len(value):
            raise ValueError("episode_ids must be unique")
        return value

    @model_validator(mode="after")
    def validate_production_options(self) -> "EpisodeTaskPlanCreateRequest":
        if self.subtitle_mode == "asr" and not self.include_subtitles:
            raise ValueError("subtitle_mode cannot be set when subtitles are disabled")
        if self.provider_profile_id is not None and self.subtitle_mode != "asr":
            raise ValueError("provider_profile_id is only valid for ASR subtitles")
        return self


class EpisodeTaskPlanItem(BaseModel):
    episode_id: UUID
    episode_number: int = Field(ge=1)
    stage: Literal[
        "novel_episode_script",
        "novel_shot_list",
        "asset_reference_image",
        "audio_narration",
        "subtitle_align",
        "subtitle_asr",
        "audio_bgm",
        "video_clip",
        "video_assembly",
    ] | None = None
    task_id: UUID | None = None
    task_ids: list[UUID] = Field(default_factory=list, max_length=100)
    action: EpisodeTaskPlanAction
    reason: str = Field(min_length=1, max_length=300)
    blocked_reasons: list[str] = Field(default_factory=list, max_length=20)


class EpisodeTaskPlanResponse(BaseModel):
    project_id: UUID
    label: str
    batch: TaskBatchRecord | None = None
    batches: list[TaskBatchRecord] = Field(default_factory=list, max_length=100)
    items: list[EpisodeTaskPlanItem] = Field(min_length=1, max_length=100)
    created_count: int = Field(default=0, ge=0)
    reused_count: int = Field(default=0, ge=0)
    skipped_count: int = Field(default=0, ge=0)
    blocked_count: int = Field(default=0, ge=0)


class ScriptGenerationRequest(BaseModel):
    topic: str
    language: str
    target_duration_seconds: int
    aspect_ratio: str
    tone: str
    required_points: list[str] = Field(default_factory=list, max_length=12)


class ScriptGenerationResult(BaseModel):
    content: ScriptContent
    provider: str
    model: str
    duration_ms: int
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
