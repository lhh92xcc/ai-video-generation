"""SQLAlchemy async engine, tables and database initialization."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, Integer, JSON, String, Text, UniqueConstraint, inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import Uuid


class Base(DeclarativeBase):
    pass


class ProjectRow(Base):
    __tablename__ = "projects"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    title: Mapped[str] = mapped_column(String(120))
    topic: Mapped[str] = mapped_column(String(500))
    language: Mapped[str] = mapped_column(String(20))
    target_duration_seconds: Mapped[int] = mapped_column(Integer)
    aspect_ratio: Mapped[str] = mapped_column(String(5))
    tone: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class GenerationTaskRow(Base):
    __tablename__ = "generation_tasks"
    __table_args__ = (UniqueConstraint("project_id", "idempotency_key"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    project_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    kind: Mapped[str] = mapped_column(String(40), default="info_script")
    input_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(20))
    current_stage: Mapped[str | None] = mapped_column(String(30), nullable=True)
    progress: Mapped[int] = mapped_column(Integer)
    error_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    stages_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    artifacts_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TaskBatchRow(Base):
    __tablename__ = "task_batches"
    __table_args__ = (UniqueConstraint("project_id", "idempotency_key"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    project_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    task_ids_json: Mapped[list[str]] = mapped_column(JSON)
    label: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(20))
    total_count: Mapped[int] = mapped_column(Integer)
    succeeded_count: Mapped[int] = mapped_column(Integer)
    failed_count: Mapped[int] = mapped_column(Integer)
    active_count: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ArtifactRow(Base):
    __tablename__ = "artifacts"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    task_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    project_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    artifact_type: Mapped[str] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(String(20))
    provider: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    preview_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


class NovelProjectRow(Base):
    __tablename__ = "novel_projects"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    title: Mapped[str] = mapped_column(String(120))
    language: Mapped[str] = mapped_column(String(20))
    target_episode_count: Mapped[int] = mapped_column(Integer)
    target_episode_duration_seconds: Mapped[int] = mapped_column(Integer)
    rights_status: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20))
    source_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    story_bible_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class NovelProjectMemberRow(Base):
    __tablename__ = "novel_project_members"
    __table_args__ = (UniqueConstraint("project_id", "actor_id"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    project_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    actor_id: Mapped[str] = mapped_column(String(120), index=True)
    actor_name: Mapped[str] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(30), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class NovelProjectInvitationRow(Base):
    __tablename__ = "novel_project_invitations"
    __table_args__ = (UniqueConstraint("project_id", "token_hash"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    project_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    invitee_actor_id: Mapped[str] = mapped_column(String(120), index=True)
    invitee_name: Mapped[str] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(30), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(30), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    invited_by_actor_id: Mapped[str] = mapped_column(String(120), index=True)
    invited_by_name: Mapped[str] = mapped_column(String(120))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    accepted_by_actor_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class NovelSourceRow(Base):
    __tablename__ = "novel_sources"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    project_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(40))
    size_bytes: Mapped[int] = mapped_column(Integer)
    checksum: Mapped[str] = mapped_column(String(64), index=True)
    content: Mapped[str] = mapped_column(Text)
    rights_status: Mapped[str] = mapped_column(String(20))
    chapter_count: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class NovelChapterRow(Base):
    __tablename__ = "novel_chapters"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    source_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    chapter_number: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(200))
    content: Mapped[str] = mapped_column(Text)
    start_offset: Mapped[int] = mapped_column(Integer)
    end_offset: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class StoryBibleRow(Base):
    __tablename__ = "story_bibles"
    __table_args__ = (UniqueConstraint("project_id", "version"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    project_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    source_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    version: Mapped[int] = mapped_column(Integer)
    content_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    provider: Mapped[str] = mapped_column(String(80))
    model: Mapped[str] = mapped_column(String(120))
    duration_ms: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EpisodeRow(Base):
    __tablename__ = "episodes"
    __table_args__ = (UniqueConstraint("project_id", "episode_number", "version"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    project_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    story_bible_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    episode_number: Mapped[int] = mapped_column(Integer)
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30))
    outline_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    provider: Mapped[str] = mapped_column(String(80))
    model: Mapped[str] = mapped_column(String(120))
    duration_ms: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EpisodeScriptRow(Base):
    __tablename__ = "episode_scripts"
    __table_args__ = (UniqueConstraint("episode_id", "version"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    project_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    episode_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    version: Mapped[int] = mapped_column(Integer)
    content_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    provider: Mapped[str] = mapped_column(String(80))
    model: Mapped[str] = mapped_column(String(120))
    duration_ms: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EpisodeScriptDraftRow(Base):
    __tablename__ = "episode_script_drafts"
    __table_args__ = (UniqueConstraint("episode_id"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    project_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    episode_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    base_script_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    base_script_version: Mapped[int] = mapped_column(Integer)
    revision: Mapped[int] = mapped_column(Integer)
    content_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class ShotListRow(Base):
    __tablename__ = "shot_lists"
    __table_args__ = (UniqueConstraint("episode_id", "version"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    project_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    episode_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    script_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    version: Mapped[int] = mapped_column(Integer)
    shots_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    provider: Mapped[str] = mapped_column(String(80))
    model: Mapped[str] = mapped_column(String(120))
    duration_ms: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AssetRow(Base):
    __tablename__ = "assets"
    __table_args__ = (UniqueConstraint("project_id", "asset_type", "name", "version"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    asset_key: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    project_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    story_bible_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    asset_type: Mapped[str] = mapped_column(String(30), index=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    aliases_json: Mapped[list[str]] = mapped_column(JSON)
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30))
    content_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    source_chapter_numbers_json: Mapped[list[int]] = mapped_column(JSON)
    provider: Mapped[str] = mapped_column(String(80))
    model: Mapped[str] = mapped_column(String(120))
    duration_ms: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AssetReviewRow(Base):
    __tablename__ = "asset_reviews"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    asset_key: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    asset_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    version: Mapped[int] = mapped_column(Integer)
    from_status: Mapped[str] = mapped_column(String(30))
    to_status: Mapped[str] = mapped_column(String(30))
    reviewer: Mapped[str] = mapped_column(String(120))
    comment: Mapped[str] = mapped_column(String(1000))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AuditLogRow(Base):
    __tablename__ = "audit_logs"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    project_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    episode_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True, index=True)
    entity_type: Mapped[str] = mapped_column(String(40), index=True)
    entity_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    action: Mapped[str] = mapped_column(String(60), index=True)
    actor_id: Mapped[str] = mapped_column(String(120), index=True)
    actor_name: Mapped[str] = mapped_column(String(120))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class ReferenceImageRow(Base):
    __tablename__ = "reference_images"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    task_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    project_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    asset_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    asset_key: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    asset_type: Mapped[str] = mapped_column(String(30), index=True)
    asset_version: Mapped[int] = mapped_column(Integer)
    prompt: Mapped[str] = mapped_column(Text)
    negative_prompt: Mapped[str] = mapped_column(Text)
    provider: Mapped[str] = mapped_column(String(80))
    model: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(30), index=True)
    output_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    duration_ms: Mapped[int] = mapped_column(Integer)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    error_json: Mapped[dict[str, str] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


def create_engine(database_url: str) -> AsyncEngine:
    return create_async_engine(database_url, pool_pre_ping=True)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def init_db(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        if connection.dialect.name == "postgresql":
            # API and Worker can start together. Serialize schema creation so
            # concurrent Base.metadata.create_all calls cannot race on tables.
            await connection.execute(
                text(
                    "SELECT pg_advisory_xact_lock("
                    "hashtext('ai_video_generation_schema_init')"
                    ")"
                )
            )
        await connection.run_sync(Base.metadata.create_all)
        await connection.run_sync(_ensure_generation_task_columns)
        await connection.run_sync(_ensure_asset_columns)


def _ensure_generation_task_columns(connection: Any) -> None:
    """Add task routing columns for databases created by an earlier phase."""

    columns = {column["name"] for column in inspect(connection).get_columns("generation_tasks")}
    if "kind" not in columns:
        connection.execute(
            text(
                "ALTER TABLE generation_tasks "
                "ADD COLUMN kind VARCHAR(40) NOT NULL DEFAULT 'info_script'"
            )
        )
    if "input_json" not in columns:
        connection.execute(text("ALTER TABLE generation_tasks ADD COLUMN input_json JSON"))


def _ensure_asset_columns(connection: Any) -> None:
    """Add stable asset identities to databases created before asset references."""

    columns = {column["name"] for column in inspect(connection).get_columns("assets")}
    if "asset_key" not in columns:
        asset_key_type = "UUID" if connection.dialect.name == "postgresql" else "TEXT"
        connection.execute(text(f"ALTER TABLE assets ADD COLUMN asset_key {asset_key_type}"))
        connection.execute(text("UPDATE assets SET asset_key = id WHERE asset_key IS NULL"))
    if "aliases_json" not in columns:
        connection.execute(text("ALTER TABLE assets ADD COLUMN aliases_json JSON"))
        connection.execute(text("UPDATE assets SET aliases_json = '[]' WHERE aliases_json IS NULL"))
