"""Versioned HTTP routes for the first backend phase."""

from __future__ import annotations

import asyncio
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, File, Form, Header, Query, Request, UploadFile, status
from pydantic import BaseModel, Field

from app.api.errors import ApiError
from app.domain.models import (
    AssetCreateRequest,
    AssetBatchReviewRequest,
    AssetRecord,
    AssetReviewRecord,
    AssetReviewRequest,
    AssetReviewResult,
    AssetType,
    ArtifactRecord,
    AssetVersionCreateRequest,
    AuditAction,
    AuditEntityType,
    AuditLogRecord,
    AudioNarrationCreateRequest,
    AudioBGMCreateRequest,
    CleanupResponse,
    IdentityCalibrationRequest,
    IdentityCalibrationResponse,
    IdentityRetryRequest,
    IdentityRetryResponse,
    LipSyncCreateRequest,
    ChapterRecord,
    EpisodeRecord,
    EpisodeTaskPlanCreateRequest,
    EpisodeTaskPlanResponse,
    EpisodeScriptDraftMergePreviewRequest,
    EpisodeScriptDraftMergePreviewResponse,
    EpisodeScriptDraftRecord,
    EpisodeScriptDraftUpsertRequest,
    EpisodeScriptImpactReport,
    EpisodeScriptRecord,
    EpisodeScriptVersionCreateRequest,
    GenerationTaskRecord,
    GenerationTaskKind,
    NovelProjectCreateRequest,
    NovelProjectRecord,
    NovelSourceSummary,
    OperationalHealthResponse,
    ProductionQueueSnapshot,
    ProductionRunCreateRequest,
    ProductionRunResponse,
    ProjectCreateRequest,
    ProjectAccessRecord,
    ProjectInvitationAcceptRequest,
    ProjectInvitationCreateRequest,
    ProjectInvitationSummary,
    ProjectMemberRecord,
    ProjectMemberUpsertRequest,
    ProjectPermission,
    ProjectRole,
    ProjectRecord,
    ProviderProfileSummary,
    ReferenceImageCreateRequest,
    ReferenceImageRecord,
    RightsStatus,
    ShotListRecord,
    SubtitleASRCreateRequest,
    StoryBibleRecord,
    SubtitleAlignmentCreateRequest,
    SubtitleCreateRequest,
    TaskBatchCreateRequest,
    TaskBatchRecord,
    TaskBatchResumeResponse,
    TaskStatus,
    VideoClipCreateRequest,
    VideoAssemblyCreateRequest,
    VoiceAssetCreateRequest,
    VoiceAssetRecord,
)
from app.services.asset_service import (
    AssetInputError,
    AssetNotFoundError,
    AssetReviewError,
    AssetService,
    AssetVersionConflictError,
)
from app.services.artifact_service import ArtifactNotFoundError, ArtifactService
from app.services.audit_service import AuditService
from app.services.access_service import (
    ProjectAccessNotFoundError,
    ProjectAccessService,
    ProjectMemberInputError,
    ProjectMemberNotFoundError,
    ProjectInvitationConflictError,
    ProjectInvitationExpiredError,
    ProjectInvitationInvalidError,
    ProjectInvitationNotFoundError,
    ProjectInvitationStateError,
    ProjectOwnerRequiredError,
    ProjectPermissionDeniedError,
)
from app.auth.identity import ActorIdentity
from app.services.reference_image_service import (
    AssetNotReadyError,
    ReferenceImageNotFoundError,
    ReferenceImageTaskService,
)
from app.services.video_clip_service import (
    ShotNotFoundError,
    VideoClipAssetGateError,
    VideoClipTaskService,
)
from app.services.video_assembly_service import (
    VideoAssemblyInputError,
    VideoAssemblyTaskService,
)
from app.services.tts_service import TTSInputError, TTSTaskService
from app.services.voice_asset_service import (
    VoiceAssetInputError,
    VoiceAssetNotFoundError,
    VoiceAssetService,
)
from app.services.identity_retry_service import (
    IdentityAuditRetryService,
    IdentityRetryInputError,
)
from app.media.identity_calibration import IdentityCalibrationService
from app.services.lip_sync_service import LipSyncInputError, LipSyncTaskService
from app.services.bgm_service import BGMTaskService
from app.services.subtitle_service import SubtitleTaskService
from app.providers.profiles import ASRProviderProfileError
from app.services.novel_service import (
    EpisodeNotFoundError,
    EpisodeScriptNotFoundError,
    EpisodeScriptDraftConflictError,
    EpisodeScriptDraftNotFoundError,
    EpisodeScriptDraftStaleError,
    EpisodeScriptVersionConflictError,
    NovelInputError,
    NovelProjectNotFoundError,
    NovelService,
    NovelSourceNotFoundError,
    ShotListNotFoundError,
    StoryBibleNotFoundError,
)
from app.services.task_service import (
    ProjectNotFoundError,
    TaskNotFoundError,
    TaskNotRetryableError,
    TaskService,
)
from app.services.task_batch_service import (
    BatchTaskNotFoundError,
    BatchTaskProjectMismatchError,
    TaskBatchNotFoundError,
    TaskBatchService,
)
from app.services.episode_task_plan_service import (
    EpisodeTaskPlanEmptyError,
    EpisodeTaskPlanEpisodeMismatchError,
    EpisodeTaskPlanService,
)
from app.services.production_orchestrator import ProductionOrchestrator
from app.storage.protocol import StorageError

router = APIRouter()


class HealthResponse(BaseModel):
    status: str
    version: str


class ProjectListResponse(BaseModel):
    items: list[ProjectRecord]
    total: int = Field(ge=0)


class EpisodePlanRequest(BaseModel):
    target_episode_count: int | None = Field(default=None, ge=1, le=100)


class ProviderProfileListResponse(BaseModel):
    items: list[ProviderProfileSummary]
    total: int = Field(ge=0)
    default_profile_id: str


class GenerationTaskListResponse(BaseModel):
    items: list[GenerationTaskRecord]
    total: int = Field(ge=0)


class TaskBatchListResponse(BaseModel):
    items: list[TaskBatchRecord]
    total: int = Field(ge=0)


class ArtifactListResponse(BaseModel):
    items: list[ArtifactRecord]
    total: int = Field(ge=0)


class AuditLogListResponse(BaseModel):
    items: list[AuditLogRecord]
    total: int = Field(ge=0)


class CurrentActorResponse(BaseModel):
    actor_id: str
    actor_name: str
    source: str
    auth_mode: str


class ProjectMemberListResponse(BaseModel):
    items: list[ProjectMemberRecord]
    total: int = Field(ge=0)


class ProjectInvitationListResponse(BaseModel):
    items: list[ProjectInvitationSummary]
    total: int = Field(ge=0)


class ProjectInvitationCreateResponse(BaseModel):
    invitation: ProjectInvitationSummary
    accept_token: str


class ProjectInvitationAcceptResponse(BaseModel):
    invitation: ProjectInvitationSummary
    member: ProjectMemberRecord


def _service(request: Request) -> TaskService:
    return request.app.state.task_service


def _task_batch_service(request: Request) -> TaskBatchService:
    return request.app.state.task_batch_service


def _episode_task_plan_service(request: Request) -> EpisodeTaskPlanService:
    return request.app.state.episode_task_plan_service


def _production_orchestrator(request: Request) -> ProductionOrchestrator:
    return request.app.state.production_orchestrator


def _novel_service(request: Request) -> NovelService:
    return request.app.state.novel_service


def _novel_task_service(request: Request):
    return request.app.state.novel_task_service


def _asset_service(request: Request) -> AssetService:
    return request.app.state.asset_service


def _reference_image_task_service(request: Request) -> ReferenceImageTaskService:
    return request.app.state.reference_image_task_service


def _video_clip_task_service(request: Request) -> VideoClipTaskService:
    return request.app.state.video_clip_task_service


def _video_assembly_task_service(request: Request) -> VideoAssemblyTaskService:
    return request.app.state.video_assembly_task_service


def _tts_task_service(request: Request) -> TTSTaskService:
    return request.app.state.tts_task_service


def _voice_asset_service(request: Request) -> VoiceAssetService:
    return request.app.state.voice_asset_service


def _identity_calibration_service(request: Request) -> IdentityCalibrationService:
    return request.app.state.identity_calibration_service


def _identity_retry_service(request: Request) -> IdentityAuditRetryService:
    return request.app.state.identity_retry_service


def _lip_sync_task_service(request: Request) -> LipSyncTaskService:
    return request.app.state.lip_sync_task_service


def _bgm_task_service(request: Request) -> BGMTaskService:
    return request.app.state.bgm_task_service


def _subtitle_task_service(request: Request) -> SubtitleTaskService:
    return request.app.state.subtitle_task_service


def _artifact_service(request: Request) -> ArtifactService:
    return request.app.state.artifact_service


def _audit_service(request: Request) -> AuditService:
    return request.app.state.audit_service


def _access_service(request: Request) -> ProjectAccessService:
    return request.app.state.access_service


def _runtime_health_service(request: Request):
    return request.app.state.runtime_health_service


def _cleanup_service(request: Request):
    return request.app.state.cleanup_service


def _identity(request: Request) -> ActorIdentity:
    identity = getattr(request.state, "identity", None)
    if identity is None:
        raise ApiError(401, "AUTHENTICATION_REQUIRED", "A trusted identity is required")
    return identity


async def _require_project_permission(
    request: Request,
    project_id: UUID,
    permission: ProjectPermission,
) -> ProjectAccessRecord:
    try:
        return await _access_service(request).require(project_id, _identity(request), permission)
    except ProjectAccessNotFoundError as exc:
        raise ApiError(404, "NOVEL_PROJECT_NOT_FOUND", "Novel project was not found") from exc
    except ProjectPermissionDeniedError as exc:
        raise ApiError(403, "PROJECT_PERMISSION_DENIED", "You do not have permission for this project") from exc


async def _require_task_batch_permission(
    request: Request,
    project_id: UUID,
    permission: ProjectPermission,
) -> None:
    """Use novel-project membership, with the documented local legacy fallback."""

    try:
        await _require_project_permission(request, project_id, permission)
    except ApiError as exc:
        if exc.code != "NOVEL_PROJECT_NOT_FOUND":
            raise
        await _require_artifact_read_permission(request, project_id)


async def _require_episode_permission(
    request: Request,
    episode_id: UUID,
    permission: ProjectPermission,
) -> ProjectAccessRecord:
    try:
        episode = await _novel_service(request).get_episode(episode_id)
    except EpisodeNotFoundError as exc:
        raise ApiError(404, "EPISODE_NOT_FOUND", "Episode was not found") from exc
    return await _require_project_permission(request, episode.project_id, permission)


async def _require_asset_permission(
    request: Request,
    asset_id: UUID,
    permission: ProjectPermission,
) -> ProjectAccessRecord:
    try:
        asset = await _asset_service(request).get_asset(asset_id)
    except AssetNotFoundError as exc:
        raise ApiError(404, "ASSET_NOT_FOUND", "Asset was not found") from exc
    return await _require_project_permission(request, asset.project_id, permission)


def _audit_actor(request: Request) -> tuple[str, str]:
    """Use the identity resolved by the configured authentication adapter."""

    identity = _identity(request)
    return identity.actor_id, identity.actor_name


def _invitation_summary(invitation) -> ProjectInvitationSummary:
    return ProjectInvitationSummary.model_validate(invitation.model_dump())


@router.get("/api/v1/auth/me", response_model=CurrentActorResponse, tags=["auth"])
async def get_current_actor(request: Request) -> CurrentActorResponse:
    identity = _identity(request)
    return CurrentActorResponse(
        actor_id=identity.actor_id,
        actor_name=identity.actor_name,
        source=identity.source,
        auth_mode=request.app.state.settings.auth_mode,
    )


@router.get(
    "/api/v1/provider-profiles",
    response_model=ProviderProfileListResponse,
    tags=["provider-profiles"],
)
async def list_provider_profiles(
    request: Request,
    capability: str = "asr",
) -> ProviderProfileListResponse:
    registry = request.app.state.asr_profile_registry
    try:
        profiles = registry.list_profiles(capability)
    except ASRProviderProfileError as exc:
        raise ApiError(exc.status_code, exc.code, exc.message) from exc
    return ProviderProfileListResponse(
        items=[ProviderProfileSummary.model_validate(profile.as_public_dict()) for profile in profiles],
        total=len(profiles),
        default_profile_id=registry.default_profile_id,
    )


@router.get("/healthz", response_model=HealthResponse, tags=["system"])
async def healthz(request: Request) -> HealthResponse:
    settings = request.app.state.settings
    return HealthResponse(status="ok", version=settings.app_version)


@router.get(
    "/api/v1/system/health",
    response_model=OperationalHealthResponse,
    tags=["system"],
)
async def system_health(request: Request) -> OperationalHealthResponse:
    """Run a one-shot dependency check for the remote production console."""

    return await _runtime_health_service(request).check()


@router.get(
    "/api/v1/system/queue",
    response_model=ProductionQueueSnapshot,
    tags=["system"],
)
async def system_queue(
    request: Request,
    project_id: UUID | None = None,
    limit: int = Query(default=100, ge=1, le=200),
) -> ProductionQueueSnapshot:
    """Return queue, Worker, GPU-lock, auto-run and task state."""

    project_ids: set[UUID] | None = None
    if project_id is not None:
        await _require_artifact_read_permission(request, project_id)
        project_ids = {project_id}
    elif request.app.state.settings.auth_mode != "local":
        accessible = await _access_service(request).list_accessible_projects(
            await _novel_service(request).list_projects(),
            _identity(request),
        )
        project_ids = {project.id for project in accessible}
    return await _runtime_health_service(request).queue_snapshot(
        limit=limit,
        project_ids=project_ids,
    )


@router.post(
    "/api/v1/system/cleanup",
    response_model=CleanupResponse,
    tags=["system"],
)
async def system_cleanup(request: Request) -> CleanupResponse:
    """Delete only old files under configured disposable temporary roots."""

    report = await asyncio.to_thread(_cleanup_service(request).cleanup)
    data = report.as_dict()
    data["free_gb_before"] = round(int(data["free_bytes_before"]) / 1024**3, 2)
    data["free_gb_after"] = round(int(data["free_bytes_after"]) / 1024**3, 2)
    return CleanupResponse.model_validate(data)


@router.post(
    "/api/v1/projects",
    response_model=ProjectRecord,
    status_code=status.HTTP_201_CREATED,
    tags=["projects"],
)
async def create_project(request: Request, payload: ProjectCreateRequest) -> ProjectRecord:
    return await _service(request).create_project(payload)


@router.get("/api/v1/projects", response_model=ProjectListResponse, tags=["projects"])
async def list_projects(request: Request) -> ProjectListResponse:
    projects = await _service(request).list_projects()
    return ProjectListResponse(items=projects, total=len(projects))


@router.get("/api/v1/projects/{project_id}", response_model=ProjectRecord, tags=["projects"])
async def get_project(request: Request, project_id: UUID) -> ProjectRecord:
    try:
        return await _service(request).get_project(project_id)
    except ProjectNotFoundError as exc:
        raise ApiError(404, "PROJECT_NOT_FOUND", "Project was not found") from exc


@router.post(
    "/api/v1/projects/{project_id}/generations",
    response_model=GenerationTaskRecord,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["tasks"],
)
async def create_generation(
    request: Request,
    project_id: UUID,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> GenerationTaskRecord:
    try:
        task, _ = await _service(request).create_generation(project_id, idempotency_key)
        return task
    except ProjectNotFoundError as exc:
        raise ApiError(404, "PROJECT_NOT_FOUND", "Project was not found") from exc


@router.get("/api/v1/tasks/{task_id}", response_model=GenerationTaskRecord, tags=["tasks"])
async def get_task(request: Request, task_id: UUID) -> GenerationTaskRecord:
    try:
        return await _service(request).get_task(task_id)
    except TaskNotFoundError as exc:
        raise ApiError(404, "TASK_NOT_FOUND", "Generation task was not found") from exc


@router.get("/api/v1/tasks", response_model=GenerationTaskListResponse, tags=["tasks"])
async def list_tasks(
    request: Request,
    project_id: UUID | None = None,
    kind: GenerationTaskKind | None = None,
    task_status: TaskStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
) -> GenerationTaskListResponse:
    items = await _service(request).list_tasks(
        project_id=project_id,
        kind=kind.value if kind else None,
        status=task_status.value if task_status else None,
        limit=limit,
    )
    return GenerationTaskListResponse(items=items, total=len(items))


@router.post(
    "/api/v1/task-batches",
    response_model=TaskBatchRecord,
    status_code=status.HTTP_201_CREATED,
    tags=["task-batches"],
)
async def create_task_batch(
    request: Request,
    payload: TaskBatchCreateRequest,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> TaskBatchRecord:
    try:
        await _require_artifact_read_permission(request, payload.project_id)
        batch, _ = await _task_batch_service(request).create(payload, idempotency_key)
        return batch
    except BatchTaskNotFoundError as exc:
        raise ApiError(404, "BATCH_TASK_NOT_FOUND", f"Task {exc.task_id} was not found") from exc
    except BatchTaskProjectMismatchError as exc:
        raise ApiError(409, "BATCH_TASK_PROJECT_MISMATCH", "All tasks must belong to the requested project") from exc


@router.get("/api/v1/task-batches", response_model=TaskBatchListResponse, tags=["task-batches"])
async def list_task_batches(
    request: Request,
    project_id: UUID,
    limit: int = Query(default=50, ge=1, le=200),
) -> TaskBatchListResponse:
    await _require_artifact_read_permission(request, project_id)
    items = await _task_batch_service(request).list(project_id, limit)
    return TaskBatchListResponse(items=items, total=len(items))


@router.get(
    "/api/v1/task-batches/{batch_id}",
    response_model=TaskBatchRecord,
    tags=["task-batches"],
)
async def get_task_batch(request: Request, batch_id: UUID) -> TaskBatchRecord:
    try:
        batch = await _task_batch_service(request).get(batch_id)
        await _require_artifact_read_permission(request, batch.project_id)
        return batch
    except TaskBatchNotFoundError as exc:
        raise ApiError(404, "TASK_BATCH_NOT_FOUND", "Task batch was not found") from exc


@router.post(
    "/api/v1/task-batches/{batch_id}/resume",
    response_model=TaskBatchResumeResponse,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["task-batches"],
)
async def resume_task_batch(request: Request, batch_id: UUID) -> TaskBatchResumeResponse:
    try:
        batch = await _task_batch_service(request).get(batch_id)
        await _require_task_batch_permission(request, batch.project_id, ProjectPermission.MANAGE_TASKS)
        return await _task_batch_service(request).resume(batch_id)
    except TaskBatchNotFoundError as exc:
        raise ApiError(404, "TASK_BATCH_NOT_FOUND", "Task batch was not found") from exc


@router.get(
    "/api/v1/artifacts/{artifact_id}",
    response_model=ArtifactRecord,
    tags=["artifacts"],
)
async def get_artifact(
    request: Request,
    artifact_id: UUID,
    expires_in_seconds: int | None = None,
) -> ArtifactRecord:
    try:
        artifact_service = _artifact_service(request)
        artifact = await artifact_service.get_artifact(
            artifact_id,
            include_download_url=False,
        )
        await _require_artifact_read_permission(request, artifact.project_id)
        artifact_service.attach_download_url(artifact, expires_in_seconds)
        return artifact
    except ArtifactNotFoundError as exc:
        raise ApiError(404, "ARTIFACT_NOT_FOUND", "Artifact was not found") from exc
    except StorageError as exc:
        raise ApiError(_storage_error_status(exc.code), exc.code, exc.message) from exc


@router.get("/api/v1/artifacts", response_model=ArtifactListResponse, tags=["artifacts"])
async def list_artifacts(
    request: Request,
    project_id: UUID | None = None,
    artifact_type: str | None = Query(default=None, alias="type"),
    limit: int = Query(default=100, ge=1, le=500),
    expires_in_seconds: int | None = Query(default=None, ge=1, le=604800),
) -> ArtifactListResponse:
    try:
        artifact_service = _artifact_service(request)
        if project_id is not None:
            await _require_artifact_read_permission(request, project_id)
            allowed_project_ids: set[UUID] | None = {project_id}
        else:
            accessible_projects = await _access_service(request).list_accessible_projects(
                await _novel_service(request).list_projects(),
                _identity(request),
            )
            allowed_project_ids = {project.id for project in accessible_projects}
            if request.app.state.settings.auth_mode == "local":
                allowed_project_ids = None

        items = await artifact_service.list_artifacts(
            project_id=project_id,
            artifact_type=artifact_type,
            limit=limit,
            expires_in_seconds=None,
            include_download_url=False,
        )
        if allowed_project_ids is not None:
            items = [item for item in items if item.project_id in allowed_project_ids]
        for item in items:
            artifact_service.attach_download_url(item, expires_in_seconds)
        return ArtifactListResponse(items=items, total=len(items))
    except StorageError as exc:
        raise ApiError(_storage_error_status(exc.code), exc.code, exc.message) from exc


async def _require_artifact_read_permission(request: Request, project_id: UUID) -> None:
    """Authorize Artifact access before exposing a storage-backed URL."""

    try:
        await _require_project_permission(request, project_id, ProjectPermission.READ)
        return
    except ApiError as exc:
        if exc.code != "NOVEL_PROJECT_NOT_FOUND":
            raise

    # Legacy information-video projects predate project membership. They stay
    # available in local development, while signed-header mode fails closed.
    try:
        await _service(request).get_project(project_id)
    except ProjectNotFoundError as exc:
        raise ApiError(404, "ARTIFACT_PROJECT_NOT_FOUND", "Artifact project was not found") from exc
    if request.app.state.settings.auth_mode != "local":
        raise ApiError(403, "PROJECT_PERMISSION_DENIED", "You do not have permission for this project")


def _storage_error_status(code: str) -> int:
    if code == "STORAGE_RATE_LIMITED":
        return 429
    if code == "STORAGE_NOT_FOUND":
        return 404
    if code in {"STORAGE_AUTH_FAILED", "STORAGE_TIMEOUT", "STORAGE_UNAVAILABLE"}:
        return 502
    if code == "STORAGE_INVALID_REQUEST":
        return 400
    return 500


@router.post(
    "/api/v1/tasks/{task_id}/retry",
    response_model=GenerationTaskRecord,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["tasks"],
)
async def retry_task(request: Request, task_id: UUID) -> GenerationTaskRecord:
    try:
        return await _service(request).retry_task(task_id)
    except TaskNotFoundError as exc:
        raise ApiError(404, "TASK_NOT_FOUND", "Generation task was not found") from exc
    except TaskNotRetryableError as exc:
        raise ApiError(409, "TASK_NOT_RETRYABLE", "Task is not in a retryable failed state") from exc


@router.post(
    "/api/v1/novel-projects",
    response_model=NovelProjectRecord,
    status_code=status.HTTP_201_CREATED,
    tags=["novel-projects"],
)
async def create_novel_project(
    request: Request,
    payload: NovelProjectCreateRequest,
) -> NovelProjectRecord:
    project = await _novel_service(request).create_project(payload)
    await _access_service(request).provision_owner(project, _identity(request))
    return project


@router.get(
    "/api/v1/novel-projects",
    response_model=list[NovelProjectRecord],
    tags=["novel-projects"],
)
async def list_novel_projects(request: Request) -> list[NovelProjectRecord]:
    projects = await _novel_service(request).list_projects()
    return await _access_service(request).list_accessible_projects(projects, _identity(request))


@router.get(
    "/api/v1/novel-projects/{project_id}",
    response_model=NovelProjectRecord,
    tags=["novel-projects"],
)
async def get_novel_project(request: Request, project_id: UUID) -> NovelProjectRecord:
    try:
        await _require_project_permission(request, project_id, ProjectPermission.READ)
        return await _novel_service(request).get_project(project_id)
    except NovelProjectNotFoundError as exc:
        raise ApiError(404, "NOVEL_PROJECT_NOT_FOUND", "Novel project was not found") from exc


@router.get(
    "/api/v1/novel-projects/{project_id}/access",
    response_model=ProjectAccessRecord,
    tags=["auth"],
)
async def get_project_access(request: Request, project_id: UUID) -> ProjectAccessRecord:
    return await _require_project_permission(request, project_id, ProjectPermission.READ)


@router.get(
    "/api/v1/novel-projects/{project_id}/members",
    response_model=ProjectMemberListResponse,
    tags=["auth"],
)
async def list_project_members(request: Request, project_id: UUID) -> ProjectMemberListResponse:
    try:
        items = await _access_service(request).list_members(project_id, _identity(request))
        return ProjectMemberListResponse(items=items, total=len(items))
    except ProjectAccessNotFoundError as exc:
        raise ApiError(404, "NOVEL_PROJECT_NOT_FOUND", "Novel project was not found") from exc
    except ProjectPermissionDeniedError as exc:
        raise ApiError(403, "PROJECT_PERMISSION_DENIED", "You do not have permission to view members") from exc


@router.put(
    "/api/v1/novel-projects/{project_id}/members/{actor_id}",
    response_model=ProjectMemberRecord,
    tags=["auth"],
)
async def upsert_project_member(
    request: Request,
    project_id: UUID,
    actor_id: str,
    payload: ProjectMemberUpsertRequest,
) -> ProjectMemberRecord:
    try:
        return await _access_service(request).upsert_member(
            project_id,
            actor_id,
            payload,
            _identity(request),
        )
    except ProjectAccessNotFoundError as exc:
        raise ApiError(404, "NOVEL_PROJECT_NOT_FOUND", "Novel project or member was not found") from exc
    except ProjectPermissionDeniedError as exc:
        raise ApiError(403, "PROJECT_PERMISSION_DENIED", "Only project owners can manage members") from exc
    except ProjectMemberInputError as exc:
        raise ApiError(400, "PROJECT_MEMBER_INPUT_INVALID", "Member actor_id is invalid") from exc
    except ProjectOwnerRequiredError as exc:
        raise ApiError(409, "PROJECT_OWNER_REQUIRED", "A project must keep at least one owner") from exc


@router.delete(
    "/api/v1/novel-projects/{project_id}/members/{actor_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["auth"],
)
async def delete_project_member(request: Request, project_id: UUID, actor_id: str) -> None:
    try:
        await _access_service(request).remove_member(project_id, actor_id, _identity(request))
    except ProjectAccessNotFoundError as exc:
        raise ApiError(404, "NOVEL_PROJECT_NOT_FOUND", "Novel project was not found") from exc
    except ProjectMemberNotFoundError as exc:
        raise ApiError(404, "PROJECT_MEMBER_NOT_FOUND", "Project member was not found") from exc
    except ProjectPermissionDeniedError as exc:
        raise ApiError(403, "PROJECT_PERMISSION_DENIED", "Only project owners can manage members") from exc
    except ProjectMemberInputError as exc:
        raise ApiError(400, "PROJECT_MEMBER_INPUT_INVALID", "Member actor_id is invalid") from exc
    except ProjectOwnerRequiredError as exc:
        raise ApiError(409, "PROJECT_OWNER_REQUIRED", "A project must keep at least one owner") from exc


@router.get(
    "/api/v1/novel-projects/{project_id}/invitations",
    response_model=ProjectInvitationListResponse,
    tags=["auth"],
)
async def list_project_invitations(
    request: Request,
    project_id: UUID,
) -> ProjectInvitationListResponse:
    try:
        invitations = await _access_service(request).list_invitations(project_id, _identity(request))
        items = [_invitation_summary(invitation) for invitation in invitations]
        return ProjectInvitationListResponse(items=items, total=len(items))
    except ProjectAccessNotFoundError as exc:
        raise ApiError(404, "NOVEL_PROJECT_NOT_FOUND", "Novel project was not found") from exc
    except ProjectPermissionDeniedError as exc:
        raise ApiError(403, "PROJECT_PERMISSION_DENIED", "Only project owners can view invitations") from exc


@router.post(
    "/api/v1/novel-projects/{project_id}/invitations",
    response_model=ProjectInvitationCreateResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["auth"],
)
async def create_project_invitation(
    request: Request,
    project_id: UUID,
    payload: ProjectInvitationCreateRequest,
) -> ProjectInvitationCreateResponse:
    try:
        invitation, token = await _access_service(request).create_invitation(
            project_id,
            payload,
            _identity(request),
        )
        return ProjectInvitationCreateResponse(
            invitation=_invitation_summary(invitation),
            accept_token=token,
        )
    except ProjectAccessNotFoundError as exc:
        raise ApiError(404, "NOVEL_PROJECT_NOT_FOUND", "Novel project was not found") from exc
    except ProjectPermissionDeniedError as exc:
        raise ApiError(403, "PROJECT_PERMISSION_DENIED", "Only project owners can create invitations") from exc
    except ProjectInvitationConflictError as exc:
        raise ApiError(409, "PROJECT_INVITATION_CONFLICT", "A member or pending invitation already exists") from exc


@router.delete(
    "/api/v1/novel-projects/{project_id}/invitations/{invitation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["auth"],
)
async def revoke_project_invitation(
    request: Request,
    project_id: UUID,
    invitation_id: UUID,
) -> None:
    try:
        await _access_service(request).revoke_invitation(project_id, invitation_id, _identity(request))
    except ProjectAccessNotFoundError as exc:
        raise ApiError(404, "NOVEL_PROJECT_NOT_FOUND", "Novel project was not found") from exc
    except ProjectInvitationNotFoundError as exc:
        raise ApiError(404, "PROJECT_INVITATION_NOT_FOUND", "Project invitation was not found") from exc
    except ProjectPermissionDeniedError as exc:
        raise ApiError(403, "PROJECT_PERMISSION_DENIED", "Only project owners can revoke invitations") from exc
    except ProjectInvitationStateError as exc:
        raise ApiError(409, "PROJECT_INVITATION_NOT_PENDING", "Only pending invitations can be revoked") from exc


@router.post(
    "/api/v1/project-invitations/{invitation_id}/accept",
    response_model=ProjectInvitationAcceptResponse,
    tags=["auth"],
)
async def accept_project_invitation(
    request: Request,
    invitation_id: UUID,
    payload: ProjectInvitationAcceptRequest,
) -> ProjectInvitationAcceptResponse:
    try:
        invitation, member = await _access_service(request).accept_invitation(
            invitation_id,
            payload,
            _identity(request),
        )
        return ProjectInvitationAcceptResponse(
            invitation=_invitation_summary(invitation),
            member=member,
        )
    except ProjectInvitationNotFoundError as exc:
        raise ApiError(404, "PROJECT_INVITATION_NOT_FOUND", "Project invitation was not found") from exc
    except ProjectInvitationExpiredError as exc:
        raise ApiError(409, "PROJECT_INVITATION_EXPIRED", "Project invitation has expired") from exc
    except ProjectInvitationInvalidError as exc:
        raise ApiError(403, "PROJECT_INVITATION_INVALID", "Invitation token or actor does not match") from exc
    except ProjectInvitationConflictError as exc:
        raise ApiError(409, "PROJECT_INVITATION_CONFLICT", "The actor is already a project member") from exc
    except ProjectInvitationStateError as exc:
        raise ApiError(409, "PROJECT_INVITATION_NOT_PENDING", "Project invitation is no longer pending") from exc


@router.post(
    "/api/v1/novel-projects/{project_id}/sources",
    response_model=NovelSourceSummary,
    status_code=status.HTTP_201_CREATED,
    tags=["novel-projects"],
)
async def upload_novel_source(
    request: Request,
    project_id: UUID,
    file: UploadFile = File(...),
    rights_status: RightsStatus | None = Form(default=None),
) -> NovelSourceSummary:
    try:
        await _require_project_permission(request, project_id, ProjectPermission.EDIT_SCRIPT)
        raw_content = await file.read()
        source, _ = await _novel_service(request).import_source(
            project_id,
            file.filename or "novel.txt",
            raw_content,
            rights_status,
        )
        return NovelSourceSummary.model_validate(source.model_dump(exclude={"content"}))
    except NovelProjectNotFoundError as exc:
        raise ApiError(404, "NOVEL_PROJECT_NOT_FOUND", "Novel project was not found") from exc
    except NovelInputError as exc:
        raise ApiError(400, "NOVEL_INPUT_INVALID", str(exc)) from exc


@router.get(
    "/api/v1/novel-projects/{project_id}/chapters",
    response_model=list[ChapterRecord],
    tags=["novel-projects"],
)
async def list_novel_chapters(request: Request, project_id: UUID) -> list[ChapterRecord]:
    try:
        await _require_project_permission(request, project_id, ProjectPermission.READ)
        return await _novel_service(request).list_chapters(project_id)
    except NovelProjectNotFoundError as exc:
        raise ApiError(404, "NOVEL_PROJECT_NOT_FOUND", "Novel project was not found") from exc
    except NovelSourceNotFoundError as exc:
        raise ApiError(409, "NOVEL_SOURCE_REQUIRED", "Upload a novel source first") from exc


@router.post(
    "/api/v1/novel-projects/{project_id}/story-bible/generate",
    response_model=StoryBibleRecord,
    status_code=status.HTTP_201_CREATED,
    tags=["novel-projects"],
)
async def generate_story_bible(request: Request, project_id: UUID) -> StoryBibleRecord:
    try:
        await _require_project_permission(request, project_id, ProjectPermission.EDIT_SCRIPT)
        return await _novel_service(request).generate_story_bible(project_id)
    except NovelProjectNotFoundError as exc:
        raise ApiError(404, "NOVEL_PROJECT_NOT_FOUND", "Novel project was not found") from exc
    except NovelSourceNotFoundError as exc:
        raise ApiError(409, "NOVEL_SOURCE_REQUIRED", "Upload a novel source first") from exc


@router.post(
    "/api/v1/novel-projects/{project_id}/story-bible/tasks",
    response_model=GenerationTaskRecord,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["novel-tasks"],
)
async def create_story_bible_task(
    request: Request,
    project_id: UUID,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> GenerationTaskRecord:
    try:
        await _require_project_permission(request, project_id, ProjectPermission.EDIT_SCRIPT)
        task, _ = await _novel_task_service(request).create_story_bible_task(
            project_id,
            idempotency_key,
        )
        return task
    except NovelProjectNotFoundError as exc:
        raise ApiError(404, "NOVEL_PROJECT_NOT_FOUND", "Novel project was not found") from exc
    except NovelSourceNotFoundError as exc:
        raise ApiError(409, "NOVEL_SOURCE_REQUIRED", "Upload a novel source first") from exc


@router.get(
    "/api/v1/novel-projects/{project_id}/story-bible",
    response_model=StoryBibleRecord,
    tags=["novel-projects"],
)
async def get_story_bible(request: Request, project_id: UUID) -> StoryBibleRecord:
    try:
        await _require_project_permission(request, project_id, ProjectPermission.READ)
        return await _novel_service(request).get_story_bible(project_id)
    except NovelProjectNotFoundError as exc:
        raise ApiError(404, "NOVEL_PROJECT_NOT_FOUND", "Novel project was not found") from exc
    except StoryBibleNotFoundError as exc:
        raise ApiError(404, "STORY_BIBLE_NOT_FOUND", "StoryBible has not been generated") from exc


@router.post(
    "/api/v1/novel-projects/{project_id}/assets/sync",
    response_model=list[AssetRecord],
    status_code=status.HTTP_201_CREATED,
    tags=["assets"],
)
async def sync_story_bible_assets(request: Request, project_id: UUID) -> list[AssetRecord]:
    try:
        await _require_project_permission(request, project_id, ProjectPermission.EDIT_ASSET)
        return await _asset_service(request).sync_story_bible_assets(project_id)
    except NovelProjectNotFoundError as exc:
        raise ApiError(404, "NOVEL_PROJECT_NOT_FOUND", "Novel project was not found") from exc
    except StoryBibleNotFoundError as exc:
        raise ApiError(409, "STORY_BIBLE_REQUIRED", "Generate StoryBible before syncing assets") from exc


@router.post(
    "/api/v1/novel-projects/{project_id}/assets",
    response_model=AssetRecord,
    status_code=status.HTTP_201_CREATED,
    tags=["assets"],
)
async def create_asset(
    request: Request,
    project_id: UUID,
    payload: AssetCreateRequest,
) -> AssetRecord:
    try:
        await _require_project_permission(request, project_id, ProjectPermission.EDIT_ASSET)
        return await _asset_service(request).create_asset(project_id, payload)
    except NovelProjectNotFoundError as exc:
        raise ApiError(404, "NOVEL_PROJECT_NOT_FOUND", "Novel project was not found") from exc
    except StoryBibleNotFoundError as exc:
        raise ApiError(409, "STORY_BIBLE_REQUIRED", "Generate StoryBible before creating assets") from exc
    except AssetInputError as exc:
        raise ApiError(400, "ASSET_INPUT_INVALID", str(exc)) from exc


@router.get(
    "/api/v1/novel-projects/{project_id}/assets",
    response_model=list[AssetRecord],
    tags=["assets"],
)
async def list_assets(
    request: Request,
    project_id: UUID,
    asset_type: AssetType | None = None,
) -> list[AssetRecord]:
    try:
        await _require_project_permission(request, project_id, ProjectPermission.READ)
        return await _asset_service(request).list_assets(project_id, asset_type)
    except NovelProjectNotFoundError as exc:
        raise ApiError(404, "NOVEL_PROJECT_NOT_FOUND", "Novel project was not found") from exc


@router.post(
    "/api/v1/novel-projects/{project_id}/voice-assets",
    response_model=VoiceAssetRecord,
    status_code=status.HTTP_201_CREATED,
    tags=["voice-assets"],
)
async def create_voice_asset(
    request: Request,
    project_id: UUID,
    payload: VoiceAssetCreateRequest,
) -> VoiceAssetRecord:
    try:
        await _require_project_permission(request, project_id, ProjectPermission.EDIT_ASSET)
        asset = await _voice_asset_service(request).create(project_id, payload)
        actor_id, actor_name = _audit_actor(request)
        await _audit_service(request).record(
            AuditLogRecord(
                project_id=project_id,
                entity_type=AuditEntityType.VOICE_ASSET,
                entity_id=asset.id,
                action=AuditAction.VOICE_ASSET_CREATED,
                actor_id=actor_id,
                actor_name=actor_name,
                metadata={
                    "label": asset.label,
                    "provider": asset.provider,
                    "voice": asset.voice,
                    "character_asset_key": (
                        str(asset.character_asset_key)
                        if asset.character_asset_key is not None
                        else None
                    ),
                },
            )
        )
        return asset
    except NovelProjectNotFoundError as exc:
        raise ApiError(404, "NOVEL_PROJECT_NOT_FOUND", "Novel project was not found") from exc
    except AssetNotFoundError as exc:
        raise ApiError(404, "ASSET_NOT_FOUND", "Character asset was not found") from exc
    except VoiceAssetInputError as exc:
        raise ApiError(409, exc.code, exc.message) from exc


@router.get(
    "/api/v1/novel-projects/{project_id}/voice-assets",
    response_model=list[VoiceAssetRecord],
    tags=["voice-assets"],
)
async def list_voice_assets(request: Request, project_id: UUID) -> list[VoiceAssetRecord]:
    try:
        await _require_project_permission(request, project_id, ProjectPermission.READ)
        return await _voice_asset_service(request).list(project_id)
    except NovelProjectNotFoundError as exc:
        raise ApiError(404, "NOVEL_PROJECT_NOT_FOUND", "Novel project was not found") from exc


@router.post(
    "/api/v1/novel-projects/{project_id}/assets/review-batch",
    response_model=list[AssetReviewResult],
    status_code=status.HTTP_201_CREATED,
    tags=["assets"],
)
async def review_project_assets(
    request: Request,
    project_id: UUID,
    payload: AssetBatchReviewRequest,
) -> list[AssetReviewResult]:
    try:
        await _require_project_permission(request, project_id, ProjectPermission.REVIEW_ASSET)
        results = await _asset_service(request).review_assets(project_id, payload)
        actor_id, actor_name = _audit_actor(request)
        for result in results:
            await _audit_service(request).record(
                AuditLogRecord(
                    project_id=project_id,
                    entity_type=AuditEntityType.ASSET_REVIEW,
                    entity_id=result.review.id,
                    action=AuditAction.ASSET_REVIEW_CREATED,
                    actor_id=actor_id,
                    actor_name=actor_name,
                    metadata={
                        "asset_id": str(result.asset.id),
                        "asset_key": str(result.asset.asset_key),
                        "version": result.review.version,
                        "from_status": result.review.from_status.value,
                        "to_status": result.review.to_status.value,
                        "reviewer": result.review.reviewer,
                        "batch_review": True,
                    },
                )
            )
        return results
    except NovelProjectNotFoundError as exc:
        raise ApiError(404, "NOVEL_PROJECT_NOT_FOUND", "Novel project was not found") from exc
    except AssetNotFoundError as exc:
        raise ApiError(404, "ASSET_NOT_FOUND", "Asset was not found") from exc
    except AssetReviewError as exc:
        raise ApiError(409, "ASSET_REVIEW_INVALID", str(exc)) from exc


@router.get(
    "/api/v1/assets/{asset_id}",
    response_model=AssetRecord,
    tags=["assets"],
)
async def get_asset(request: Request, asset_id: UUID) -> AssetRecord:
    try:
        await _require_asset_permission(request, asset_id, ProjectPermission.READ)
        return await _asset_service(request).get_asset(asset_id)
    except AssetNotFoundError as exc:
        raise ApiError(404, "ASSET_NOT_FOUND", "Asset was not found") from exc


@router.post(
    "/api/v1/assets/{asset_id}/versions",
    response_model=AssetRecord,
    status_code=status.HTTP_201_CREATED,
    tags=["assets"],
)
async def create_asset_version(
    request: Request,
    asset_id: UUID,
    payload: AssetVersionCreateRequest,
) -> AssetRecord:
    try:
        await _require_asset_permission(request, asset_id, ProjectPermission.EDIT_ASSET)
        saved = await _asset_service(request).create_asset_version(asset_id, payload)
        actor_id, actor_name = _audit_actor(request)
        await _audit_service(request).record(
            AuditLogRecord(
                project_id=saved.project_id,
                entity_type=AuditEntityType.ASSET,
                entity_id=saved.id,
                action=AuditAction.ASSET_VERSION_CREATED,
                actor_id=actor_id,
                actor_name=actor_name,
                metadata={
                    "version": saved.version,
                    "status": saved.status.value,
                    "expected_version": payload.expected_version,
                },
            )
        )
        return saved
    except AssetNotFoundError as exc:
        raise ApiError(404, "ASSET_NOT_FOUND", "Asset was not found") from exc
    except AssetInputError as exc:
        raise ApiError(400, "ASSET_INPUT_INVALID", str(exc)) from exc
    except AssetVersionConflictError as exc:
        raise ApiError(409, "ASSET_VERSION_CONFLICT", str(exc)) from exc


@router.post(
    "/api/v1/assets/{asset_id}/reviews",
    response_model=AssetReviewResult,
    status_code=status.HTTP_201_CREATED,
    tags=["assets"],
)
async def review_asset(
    request: Request,
    asset_id: UUID,
    payload: AssetReviewRequest,
) -> AssetReviewResult:
    try:
        await _require_asset_permission(request, asset_id, ProjectPermission.REVIEW_ASSET)
        result = await _asset_service(request).review_asset(asset_id, payload)
        actor_id, actor_name = _audit_actor(request)
        await _audit_service(request).record(
            AuditLogRecord(
                project_id=result.asset.project_id,
                entity_type=AuditEntityType.ASSET_REVIEW,
                entity_id=result.review.id,
                action=AuditAction.ASSET_REVIEW_CREATED,
                actor_id=actor_id,
                actor_name=actor_name,
                metadata={
                    "asset_id": str(result.asset.id),
                    "asset_key": str(result.asset.asset_key),
                    "version": result.review.version,
                    "from_status": result.review.from_status.value,
                    "to_status": result.review.to_status.value,
                    "reviewer": result.review.reviewer,
                },
            )
        )
        return result
    except AssetNotFoundError as exc:
        raise ApiError(404, "ASSET_NOT_FOUND", "Asset was not found") from exc
    except AssetReviewError as exc:
        raise ApiError(409, "ASSET_REVIEW_INVALID", str(exc)) from exc


@router.get(
    "/api/v1/assets/{asset_id}/reviews",
    response_model=list[AssetReviewRecord],
    tags=["assets"],
)
async def list_asset_reviews(
    request: Request,
    asset_id: UUID,
) -> list[AssetReviewRecord]:
    try:
        await _require_asset_permission(request, asset_id, ProjectPermission.READ)
        return await _asset_service(request).list_asset_reviews(asset_id)
    except AssetNotFoundError as exc:
        raise ApiError(404, "ASSET_NOT_FOUND", "Asset was not found") from exc


@router.post(
    "/api/v1/assets/{asset_id}/reference-images",
    response_model=GenerationTaskRecord,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["assets"],
)
async def create_reference_image_task(
    request: Request,
    asset_id: UUID,
    payload: ReferenceImageCreateRequest,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> GenerationTaskRecord:
    try:
        await _require_asset_permission(request, asset_id, ProjectPermission.EDIT_ASSET)
        task, _ = await _reference_image_task_service(request).create_task(
            asset_id,
            payload,
            idempotency_key,
        )
        return task
    except AssetNotFoundError as exc:
        raise ApiError(404, "ASSET_NOT_FOUND", "Asset was not found") from exc
    except AssetNotReadyError as exc:
        raise ApiError(409, "ASSET_NOT_READY", str(exc)) from exc


@router.get(
    "/api/v1/assets/{asset_id}/reference-images",
    response_model=list[ReferenceImageRecord],
    tags=["assets"],
)
async def list_reference_images(request: Request, asset_id: UUID) -> list[ReferenceImageRecord]:
    try:
        await _require_asset_permission(request, asset_id, ProjectPermission.READ)
        return await _reference_image_task_service(request).list_for_asset(asset_id)
    except AssetNotFoundError as exc:
        raise ApiError(404, "ASSET_NOT_FOUND", "Asset was not found") from exc


@router.get(
    "/api/v1/reference-images/{reference_image_id}",
    response_model=ReferenceImageRecord,
    tags=["assets"],
)
async def get_reference_image(
    request: Request,
    reference_image_id: UUID,
) -> ReferenceImageRecord:
    try:
        image = await _reference_image_task_service(request).get(reference_image_id)
        await _require_project_permission(request, image.project_id, ProjectPermission.READ)
        return image
    except ReferenceImageNotFoundError as exc:
        raise ApiError(404, "REFERENCE_IMAGE_NOT_FOUND", "Reference image was not found") from exc


@router.post(
    "/api/v1/novel-projects/{project_id}/episodes/plan",
    response_model=list[EpisodeRecord],
    status_code=status.HTTP_201_CREATED,
    tags=["novel-projects"],
)
async def plan_novel_episodes(
    request: Request,
    project_id: UUID,
    payload: EpisodePlanRequest | None = None,
) -> list[EpisodeRecord]:
    try:
        await _require_project_permission(request, project_id, ProjectPermission.EDIT_SCRIPT)
        return await _novel_service(request).plan_episodes(
            project_id,
            payload.target_episode_count if payload else None,
        )
    except NovelProjectNotFoundError as exc:
        raise ApiError(404, "NOVEL_PROJECT_NOT_FOUND", "Novel project was not found") from exc
    except StoryBibleNotFoundError as exc:
        raise ApiError(409, "STORY_BIBLE_REQUIRED", "Generate StoryBible before planning episodes") from exc


@router.post(
    "/api/v1/novel-projects/{project_id}/episodes/tasks",
    response_model=GenerationTaskRecord,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["novel-tasks"],
)
async def create_episode_plan_task(
    request: Request,
    project_id: UUID,
    payload: EpisodePlanRequest | None = None,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> GenerationTaskRecord:
    try:
        await _require_project_permission(request, project_id, ProjectPermission.EDIT_SCRIPT)
        task, _ = await _novel_task_service(request).create_episode_plan_task(
            project_id,
            payload.target_episode_count if payload else None,
            idempotency_key,
        )
        return task
    except NovelProjectNotFoundError as exc:
        raise ApiError(404, "NOVEL_PROJECT_NOT_FOUND", "Novel project was not found") from exc
    except StoryBibleNotFoundError as exc:
        raise ApiError(409, "STORY_BIBLE_REQUIRED", "Generate StoryBible before planning episodes") from exc


@router.get(
    "/api/v1/novel-projects/{project_id}/episodes",
    response_model=list[EpisodeRecord],
    tags=["novel-projects"],
)
async def list_novel_episodes(request: Request, project_id: UUID) -> list[EpisodeRecord]:
    try:
        await _require_project_permission(request, project_id, ProjectPermission.READ)
        return await _novel_service(request).list_episodes(project_id)
    except NovelProjectNotFoundError as exc:
        raise ApiError(404, "NOVEL_PROJECT_NOT_FOUND", "Novel project was not found") from exc


@router.post(
    "/api/v1/novel-projects/{project_id}/episode-task-plans",
    response_model=EpisodeTaskPlanResponse,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["novel-tasks"],
)
async def create_episode_task_plan(
    request: Request,
    project_id: UUID,
    payload: EpisodeTaskPlanCreateRequest,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> EpisodeTaskPlanResponse:
    try:
        await _require_project_permission(request, project_id, ProjectPermission.MANAGE_TASKS)
        response = await _episode_task_plan_service(request).create(
            project_id,
            payload,
            idempotency_key,
        )
        if payload.production_mode and payload.auto_advance:
            return await _production_orchestrator(request).start(
                project_id,
                payload,
                response,
                idempotency_key,
            )
        return response
    except NovelProjectNotFoundError as exc:
        raise ApiError(404, "NOVEL_PROJECT_NOT_FOUND", "Novel project was not found") from exc
    except StoryBibleNotFoundError as exc:
        raise ApiError(409, "STORY_BIBLE_REQUIRED", "Generate StoryBible before creating episode tasks") from exc
    except EpisodeTaskPlanEpisodeMismatchError as exc:
        raise ApiError(
            409,
            "EPISODE_TASK_PLAN_EPISODE_MISMATCH",
            f"Episode {exc.episode_id} does not belong to this novel project",
        ) from exc
    except EpisodeTaskPlanEmptyError as exc:
        raise ApiError(409, "EPISODE_TASK_PLAN_NO_EPISODES", "No episodes are available for task planning") from exc


@router.post(
    "/api/v1/novel-projects/{project_id}/production-runs",
    response_model=ProductionRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["novel-tasks"],
)
async def start_production_run(
    request: Request,
    project_id: UUID,
    payload: ProductionRunCreateRequest,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ProductionRunResponse:
    """Start or resume the complete novel-to-video production DAG."""

    try:
        await _require_project_permission(request, project_id, ProjectPermission.MANAGE_TASKS)
        return await _production_orchestrator(request).start_from_source(
            project_id,
            payload,
            idempotency_key=idempotency_key,
        )
    except NovelProjectNotFoundError as exc:
        raise ApiError(404, "NOVEL_PROJECT_NOT_FOUND", "Novel project was not found") from exc
    except NovelSourceNotFoundError as exc:
        raise ApiError(409, "NOVEL_SOURCE_REQUIRED", "Upload a novel source before starting production") from exc


@router.get(
    "/api/v1/episodes/{episode_id}",
    response_model=EpisodeRecord,
    tags=["episodes"],
)
async def get_episode(request: Request, episode_id: UUID) -> EpisodeRecord:
    try:
        await _require_episode_permission(request, episode_id, ProjectPermission.READ)
        return await _novel_service(request).get_episode(episode_id)
    except EpisodeNotFoundError as exc:
        raise ApiError(404, "EPISODE_NOT_FOUND", "Episode was not found") from exc


@router.post(
    "/api/v1/episodes/{episode_id}/script/generate",
    response_model=EpisodeScriptRecord,
    status_code=status.HTTP_201_CREATED,
    tags=["episodes"],
)
async def generate_episode_script(request: Request, episode_id: UUID) -> EpisodeScriptRecord:
    try:
        await _require_episode_permission(request, episode_id, ProjectPermission.EDIT_SCRIPT)
        return await _novel_service(request).generate_episode_script(episode_id)
    except EpisodeNotFoundError as exc:
        raise ApiError(404, "EPISODE_NOT_FOUND", "Episode was not found") from exc
    except TTSInputError as exc:
        raise ApiError(400, "TTS_INPUT_INVALID", str(exc)) from exc
    except VoiceAssetInputError as exc:
        raise ApiError(409, exc.code, exc.message) from exc
    except StoryBibleNotFoundError as exc:
        raise ApiError(409, "STORY_BIBLE_REQUIRED", "Generate StoryBible before the episode script") from exc


@router.post(
    "/api/v1/episodes/{episode_id}/script/tasks",
    response_model=GenerationTaskRecord,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["novel-tasks"],
)
async def create_episode_script_task(
    request: Request,
    episode_id: UUID,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> GenerationTaskRecord:
    try:
        await _require_episode_permission(request, episode_id, ProjectPermission.EDIT_SCRIPT)
        task, _ = await _novel_task_service(request).create_episode_script_task(
            episode_id,
            idempotency_key,
        )
        return task
    except EpisodeNotFoundError as exc:
        raise ApiError(404, "EPISODE_NOT_FOUND", "Episode was not found") from exc
    except StoryBibleNotFoundError as exc:
        raise ApiError(409, "STORY_BIBLE_REQUIRED", "Generate StoryBible before the episode script") from exc


@router.get(
    "/api/v1/episodes/{episode_id}/script",
    response_model=EpisodeScriptRecord,
    tags=["episodes"],
)
async def get_episode_script(request: Request, episode_id: UUID) -> EpisodeScriptRecord:
    try:
        await _require_episode_permission(request, episode_id, ProjectPermission.READ)
        return await _novel_service(request).get_episode_script(episode_id)
    except EpisodeNotFoundError as exc:
        raise ApiError(404, "EPISODE_NOT_FOUND", "Episode was not found") from exc
    except EpisodeScriptNotFoundError as exc:
        raise ApiError(404, "EPISODE_SCRIPT_NOT_FOUND", "Episode script has not been generated") from exc


@router.get(
    "/api/v1/episodes/{episode_id}/script/draft",
    response_model=EpisodeScriptDraftRecord,
    tags=["episodes"],
)
async def get_episode_script_draft(
    request: Request,
    episode_id: UUID,
) -> EpisodeScriptDraftRecord:
    try:
        await _require_episode_permission(request, episode_id, ProjectPermission.READ)
        return await _novel_service(request).get_episode_script_draft(episode_id)
    except EpisodeNotFoundError as exc:
        raise ApiError(404, "EPISODE_NOT_FOUND", "Episode was not found") from exc
    except EpisodeScriptDraftNotFoundError as exc:
        raise ApiError(404, "EPISODE_SCRIPT_DRAFT_NOT_FOUND", "Episode script draft was not found") from exc


@router.put(
    "/api/v1/episodes/{episode_id}/script/draft",
    response_model=EpisodeScriptDraftRecord,
    tags=["episodes"],
)
async def save_episode_script_draft(
    request: Request,
    episode_id: UUID,
    payload: EpisodeScriptDraftUpsertRequest,
) -> EpisodeScriptDraftRecord:
    try:
        await _require_episode_permission(request, episode_id, ProjectPermission.EDIT_SCRIPT)
        saved = await _novel_service(request).save_episode_script_draft(episode_id, payload)
        actor_id, actor_name = _audit_actor(request)
        await _audit_service(request).record(
            AuditLogRecord(
                project_id=saved.project_id,
                episode_id=saved.episode_id,
                entity_type=AuditEntityType.EPISODE_SCRIPT_DRAFT,
                entity_id=saved.id,
                action=AuditAction.SCRIPT_DRAFT_SAVED,
                actor_id=actor_id,
                actor_name=actor_name,
                metadata={
                    "revision": saved.revision,
                    "base_script_version": saved.base_script_version,
                },
            )
        )
        return saved
    except EpisodeNotFoundError as exc:
        raise ApiError(404, "EPISODE_NOT_FOUND", "Episode was not found") from exc
    except EpisodeScriptNotFoundError as exc:
        raise ApiError(404, "EPISODE_SCRIPT_NOT_FOUND", "Episode script has not been generated") from exc
    except EpisodeScriptDraftConflictError as exc:
        raise ApiError(
            409,
            "EPISODE_SCRIPT_DRAFT_CONFLICT",
            str(exc),
            details=exc.details,
        ) from exc
    except EpisodeScriptDraftStaleError as exc:
        raise ApiError(409, "EPISODE_SCRIPT_DRAFT_STALE", str(exc)) from exc
    except ValueError as exc:
        raise ApiError(400, "EPISODE_SCRIPT_DRAFT_INPUT_INVALID", str(exc)) from exc


@router.post(
    "/api/v1/episodes/{episode_id}/script/draft/merge-preview",
    response_model=EpisodeScriptDraftMergePreviewResponse,
    tags=["episodes"],
)
async def preview_episode_script_draft_merge(
    request: Request,
    episode_id: UUID,
    payload: EpisodeScriptDraftMergePreviewRequest,
) -> EpisodeScriptDraftMergePreviewResponse:
    try:
        await _require_episode_permission(request, episode_id, ProjectPermission.EDIT_SCRIPT)
        return await _novel_service(request).preview_episode_script_draft_merge(
            episode_id,
            payload,
        )
    except EpisodeNotFoundError as exc:
        raise ApiError(404, "EPISODE_NOT_FOUND", "Episode was not found") from exc
    except EpisodeScriptNotFoundError as exc:
        raise ApiError(404, "EPISODE_SCRIPT_NOT_FOUND", "Episode script has not been generated") from exc
    except EpisodeScriptDraftStaleError as exc:
        raise ApiError(409, "EPISODE_SCRIPT_DRAFT_STALE", str(exc)) from exc
    except ValueError as exc:
        raise ApiError(400, "EPISODE_SCRIPT_DRAFT_INPUT_INVALID", str(exc)) from exc


@router.delete(
    "/api/v1/episodes/{episode_id}/script/draft",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["episodes"],
)
async def delete_episode_script_draft(request: Request, episode_id: UUID) -> None:
    try:
        await _require_episode_permission(request, episode_id, ProjectPermission.EDIT_SCRIPT)
        existing = None
        try:
            existing = await _novel_service(request).get_episode_script_draft(episode_id)
        except EpisodeScriptDraftNotFoundError:
            pass
        await _novel_service(request).delete_episode_script_draft(episode_id)
        if existing is not None:
            actor_id, actor_name = _audit_actor(request)
            await _audit_service(request).record(
                AuditLogRecord(
                    project_id=existing.project_id,
                    episode_id=existing.episode_id,
                    entity_type=AuditEntityType.EPISODE_SCRIPT_DRAFT,
                    entity_id=existing.id,
                    action=AuditAction.SCRIPT_DRAFT_DELETED,
                    actor_id=actor_id,
                    actor_name=actor_name,
                    metadata={"revision": existing.revision},
                )
            )
    except EpisodeNotFoundError as exc:
        raise ApiError(404, "EPISODE_NOT_FOUND", "Episode was not found") from exc


@router.post(
    "/api/v1/episodes/{episode_id}/script/versions",
    response_model=EpisodeScriptRecord,
    status_code=status.HTTP_201_CREATED,
    tags=["episodes"],
)
async def create_episode_script_version(
    request: Request,
    episode_id: UUID,
    payload: EpisodeScriptVersionCreateRequest,
) -> EpisodeScriptRecord:
    try:
        await _require_episode_permission(request, episode_id, ProjectPermission.EDIT_SCRIPT)
        saved = await _novel_service(request).save_episode_script_version(
            episode_id,
            payload,
        )
        actor_id, actor_name = _audit_actor(request)
        await _audit_service(request).record(
            AuditLogRecord(
                project_id=saved.project_id,
                episode_id=saved.episode_id,
                entity_type=AuditEntityType.EPISODE_SCRIPT,
                entity_id=saved.id,
                action=AuditAction.SCRIPT_VERSION_PUBLISHED,
                actor_id=actor_id,
                actor_name=actor_name,
                metadata={
                    "version": saved.version,
                    "expected_version": payload.expected_version,
                },
            )
        )
        return saved
    except EpisodeNotFoundError as exc:
        raise ApiError(404, "EPISODE_NOT_FOUND", "Episode was not found") from exc
    except EpisodeScriptNotFoundError as exc:
        raise ApiError(404, "EPISODE_SCRIPT_NOT_FOUND", "Episode script has not been generated") from exc
    except EpisodeScriptVersionConflictError as exc:
        raise ApiError(409, "EPISODE_SCRIPT_VERSION_CONFLICT", str(exc)) from exc
    except ValueError as exc:
        raise ApiError(400, "EPISODE_SCRIPT_INPUT_INVALID", str(exc)) from exc


@router.get(
    "/api/v1/novel-projects/{project_id}/audit-logs",
    response_model=AuditLogListResponse,
    tags=["audit"],
)
async def list_audit_logs(
    request: Request,
    project_id: UUID,
    entity_type: AuditEntityType | None = Query(default=None),
    entity_id: UUID | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=200),
) -> AuditLogListResponse:
    try:
        await _require_project_permission(request, project_id, ProjectPermission.READ)
    except NovelProjectNotFoundError as exc:
        raise ApiError(404, "NOVEL_PROJECT_NOT_FOUND", "Novel project was not found") from exc
    items = await _audit_service(request).list_logs(
        project_id,
        entity_type=entity_type,
        entity_id=entity_id,
        limit=limit,
    )
    return AuditLogListResponse(items=items, total=len(items))


@router.get(
    "/api/v1/episodes/{episode_id}/script/impact",
    response_model=EpisodeScriptImpactReport,
    tags=["episodes"],
)
async def get_episode_script_impact(
    request: Request,
    episode_id: UUID,
) -> EpisodeScriptImpactReport:
    try:
        await _require_episode_permission(request, episode_id, ProjectPermission.READ)
        return await _novel_service(request).get_episode_script_impact(episode_id)
    except EpisodeNotFoundError as exc:
        raise ApiError(404, "EPISODE_NOT_FOUND", "Episode was not found") from exc
    except EpisodeScriptNotFoundError as exc:
        raise ApiError(404, "EPISODE_SCRIPT_NOT_FOUND", "Episode script has not been generated") from exc


@router.post(
    "/api/v1/episodes/{episode_id}/shots/generate",
    response_model=ShotListRecord,
    status_code=status.HTTP_201_CREATED,
    tags=["episodes"],
)
async def generate_episode_shots(request: Request, episode_id: UUID) -> ShotListRecord:
    try:
        await _require_episode_permission(request, episode_id, ProjectPermission.EDIT_SCRIPT)
        return await _novel_service(request).generate_shot_list(episode_id)
    except EpisodeNotFoundError as exc:
        raise ApiError(404, "EPISODE_NOT_FOUND", "Episode was not found") from exc
    except EpisodeScriptNotFoundError as exc:
        raise ApiError(409, "EPISODE_SCRIPT_REQUIRED", "Generate the episode script first") from exc
    except StoryBibleNotFoundError as exc:
        raise ApiError(409, "STORY_BIBLE_REQUIRED", "Generate StoryBible before the shot list") from exc


@router.post(
    "/api/v1/episodes/{episode_id}/shots/tasks",
    response_model=GenerationTaskRecord,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["novel-tasks"],
)
async def create_episode_shot_task(
    request: Request,
    episode_id: UUID,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> GenerationTaskRecord:
    try:
        await _require_episode_permission(request, episode_id, ProjectPermission.EDIT_SCRIPT)
        task, _ = await _novel_task_service(request).create_shot_list_task(
            episode_id,
            idempotency_key,
        )
        return task
    except EpisodeNotFoundError as exc:
        raise ApiError(404, "EPISODE_NOT_FOUND", "Episode was not found") from exc
    except EpisodeScriptNotFoundError as exc:
        raise ApiError(409, "EPISODE_SCRIPT_REQUIRED", "Generate the episode script first") from exc
    except StoryBibleNotFoundError as exc:
        raise ApiError(409, "STORY_BIBLE_REQUIRED", "Generate StoryBible before the shot list") from exc


@router.get(
    "/api/v1/episodes/{episode_id}/shots",
    response_model=ShotListRecord,
    tags=["episodes"],
)
async def get_episode_shots(request: Request, episode_id: UUID) -> ShotListRecord:
    try:
        await _require_episode_permission(request, episode_id, ProjectPermission.READ)
        return await _novel_service(request).get_shot_list(episode_id)
    except EpisodeNotFoundError as exc:
        raise ApiError(404, "EPISODE_NOT_FOUND", "Episode was not found") from exc
    except ShotListNotFoundError as exc:
        raise ApiError(404, "SHOT_LIST_NOT_FOUND", "Shot list has not been generated") from exc


@router.post(
    "/api/v1/novel-projects/{project_id}/identity-audit/calibrate",
    response_model=IdentityCalibrationResponse,
    tags=["identity-audit"],
)
async def calibrate_identity_thresholds(
    request: Request,
    project_id: UUID,
    payload: IdentityCalibrationRequest,
) -> IdentityCalibrationResponse:
    try:
        await _require_project_permission(request, project_id, ProjectPermission.READ)
        return await _identity_calibration_service(request).calibrate(
            project_id,
            payload,
            request.app.state.settings.identity_audit_threshold,
        )
    except NovelProjectNotFoundError as exc:
        raise ApiError(404, "NOVEL_PROJECT_NOT_FOUND", "Novel project was not found") from exc


@router.post(
    "/api/v1/novel-projects/{project_id}/video-clips/retry-failed",
    response_model=IdentityRetryResponse,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["identity-audit"],
)
async def retry_identity_failed_video_clips(
    request: Request,
    project_id: UUID,
    payload: IdentityRetryRequest,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> IdentityRetryResponse:
    try:
        await _require_project_permission(request, project_id, ProjectPermission.MANAGE_TASKS)
        return await _identity_retry_service(request).retry(
            project_id,
            payload,
            idempotency_key,
        )
    except IdentityRetryInputError as exc:
        code = exc.code
        status_code = 404 if code == "NOVEL_PROJECT_NOT_FOUND" else 409
        raise ApiError(status_code, code, exc.message) from exc


@router.post(
    "/api/v1/episodes/{episode_id}/lip-sync",
    response_model=GenerationTaskRecord,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["lip-sync"],
)
async def create_lip_sync_task(
    request: Request,
    episode_id: UUID,
    payload: LipSyncCreateRequest,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> GenerationTaskRecord:
    try:
        await _require_episode_permission(request, episode_id, ProjectPermission.EDIT_ASSET)
        task, _ = await _lip_sync_task_service(request).create_task(
            episode_id,
            payload,
            idempotency_key,
        )
        return task
    except EpisodeNotFoundError as exc:
        raise ApiError(404, "EPISODE_NOT_FOUND", "Episode was not found") from exc
    except LipSyncInputError as exc:
        status_code = 404 if exc.code.endswith("_NOT_FOUND") else 409
        raise ApiError(status_code, exc.code, exc.message) from exc


@router.post(
    "/api/v1/episodes/{episode_id}/audio",
    response_model=GenerationTaskRecord,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["audio"],
)
async def create_audio_narration_task(
    request: Request,
    episode_id: UUID,
    payload: AudioNarrationCreateRequest,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> GenerationTaskRecord:
    try:
        await _require_episode_permission(request, episode_id, ProjectPermission.EDIT_SCRIPT)
        task, _ = await _tts_task_service(request).create_task(
            episode_id,
            payload,
            idempotency_key,
        )
        return task
    except EpisodeNotFoundError as exc:
        raise ApiError(404, "EPISODE_NOT_FOUND", "Episode was not found") from exc
    except TTSInputError as exc:
        raise ApiError(400, "TTS_INPUT_INVALID", str(exc)) from exc
    except VoiceAssetInputError as exc:
        raise ApiError(409, exc.code, exc.message) from exc


@router.post(
    "/api/v1/episodes/{episode_id}/bgm",
    response_model=GenerationTaskRecord,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["audio"],
)
async def create_audio_bgm_task(
    request: Request,
    episode_id: UUID,
    payload: AudioBGMCreateRequest,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> GenerationTaskRecord:
    try:
        await _require_episode_permission(request, episode_id, ProjectPermission.EDIT_SCRIPT)
        task, _ = await _bgm_task_service(request).create_task(
            episode_id,
            payload,
            idempotency_key,
        )
        return task
    except EpisodeNotFoundError as exc:
        raise ApiError(404, "EPISODE_NOT_FOUND", "Episode was not found") from exc


@router.post(
    "/api/v1/episodes/{episode_id}/subtitles",
    response_model=GenerationTaskRecord,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["subtitles"],
)
async def create_subtitle_task(
    request: Request,
    episode_id: UUID,
    payload: SubtitleCreateRequest,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> GenerationTaskRecord:
    try:
        await _require_episode_permission(request, episode_id, ProjectPermission.EDIT_SCRIPT)
        task, _ = await _subtitle_task_service(request).create_task(
            episode_id,
            payload,
            idempotency_key,
        )
        return task
    except EpisodeNotFoundError as exc:
        raise ApiError(404, "EPISODE_NOT_FOUND", "Episode was not found") from exc


@router.post(
    "/api/v1/episodes/{episode_id}/subtitles/align",
    response_model=GenerationTaskRecord,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["subtitles"],
)
async def create_subtitle_alignment_task(
    request: Request,
    episode_id: UUID,
    payload: SubtitleAlignmentCreateRequest,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> GenerationTaskRecord:
    try:
        await _require_episode_permission(request, episode_id, ProjectPermission.EDIT_SCRIPT)
        task, _ = await _subtitle_task_service(request).create_alignment_task(
            episode_id,
            payload,
            idempotency_key,
        )
        return task
    except EpisodeNotFoundError as exc:
        raise ApiError(404, "EPISODE_NOT_FOUND", "Episode was not found") from exc


@router.post(
    "/api/v1/episodes/{episode_id}/subtitles/asr",
    response_model=GenerationTaskRecord,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["subtitles"],
)
async def create_subtitle_asr_task(
    request: Request,
    episode_id: UUID,
    payload: SubtitleASRCreateRequest,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> GenerationTaskRecord:
    try:
        await _require_episode_permission(request, episode_id, ProjectPermission.EDIT_SCRIPT)
        task, _ = await _subtitle_task_service(request).create_asr_task(
            episode_id,
            payload,
            idempotency_key,
        )
        return task
    except EpisodeNotFoundError as exc:
        raise ApiError(404, "EPISODE_NOT_FOUND", "Episode was not found") from exc
    except ASRProviderProfileError as exc:
        raise ApiError(exc.status_code, exc.code, exc.message) from exc


@router.post(
    "/api/v1/episodes/{episode_id}/shots/{shot_index}/video-clips",
    response_model=GenerationTaskRecord,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["video-clips"],
)
async def create_video_clip_task(
    request: Request,
    episode_id: UUID,
    shot_index: int,
    payload: VideoClipCreateRequest | None = None,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> GenerationTaskRecord:
    try:
        await _require_episode_permission(request, episode_id, ProjectPermission.EDIT_ASSET)
        task, _ = await _video_clip_task_service(request).create_task(
            episode_id,
            shot_index,
            payload or VideoClipCreateRequest(),
            idempotency_key,
        )
        return task
    except EpisodeNotFoundError as exc:
        raise ApiError(404, "EPISODE_NOT_FOUND", "Episode was not found") from exc
    except ShotListNotFoundError as exc:
        raise ApiError(409, "SHOT_LIST_REQUIRED", "Generate the shot list first") from exc
    except ShotNotFoundError as exc:
        raise ApiError(404, "SHOT_NOT_FOUND", "Shot was not found") from exc
    except VideoClipAssetGateError as exc:
        raise ApiError(409, "SHOT_ASSETS_NOT_READY", str(exc)) from exc


@router.post(
    "/api/v1/episodes/{episode_id}/video-renders",
    response_model=GenerationTaskRecord,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["video-renders"],
)
async def create_video_assembly_task(
    request: Request,
    episode_id: UUID,
    payload: VideoAssemblyCreateRequest,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> GenerationTaskRecord:
    try:
        await _require_episode_permission(request, episode_id, ProjectPermission.EDIT_ASSET)
        task, _ = await _video_assembly_task_service(request).create_task(
            episode_id,
            payload,
            idempotency_key,
        )
        return task
    except EpisodeNotFoundError as exc:
        raise ApiError(404, "EPISODE_NOT_FOUND", "Episode was not found") from exc
    except VideoAssemblyInputError as exc:
        status_code = 404 if exc.code.endswith("_NOT_FOUND") else 409
        raise ApiError(status_code, exc.code, exc.message) from exc
