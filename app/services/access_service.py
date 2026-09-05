"""Project membership and role-based access policy."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta
from uuid import UUID

from app.auth.identity import ActorIdentity
from app.domain.models import (
    AuditAction,
    AuditEntityType,
    AuditLogRecord,
    NovelProjectRecord,
    ProjectInvitationAcceptRequest,
    ProjectInvitationCreateRequest,
    ProjectInvitationRecord,
    ProjectInvitationStatus,
    ProjectAccessRecord,
    ProjectMemberRecord,
    ProjectRole,
    ProjectPermission,
    ProjectMemberUpsertRequest,
    utc_now,
)
from app.repositories.protocol import NovelStore
from app.services.audit_service import AuditService


class ProjectAccessError(Exception):
    """Base class for project membership failures."""


class ProjectAccessNotFoundError(ProjectAccessError):
    """Raised when the project or member does not exist."""


class ProjectMemberNotFoundError(ProjectAccessError):
    """Raised when a requested project member does not exist."""


class ProjectPermissionDeniedError(ProjectAccessError):
    """Raised when an actor lacks a required project permission."""


class ProjectOwnerRequiredError(ProjectAccessError):
    """Raised when an operation would leave a project without an owner."""


class ProjectMemberInputError(ProjectAccessError):
    """Raised when a member identifier is invalid."""


class ProjectInvitationNotFoundError(ProjectAccessError):
    """Raised when an invitation does not exist in the requested project."""


class ProjectInvitationConflictError(ProjectAccessError):
    """Raised when a pending invitation or membership already exists."""


class ProjectInvitationInvalidError(ProjectAccessError):
    """Raised when an invitation token is invalid or used by another actor."""


class ProjectInvitationExpiredError(ProjectAccessError):
    """Raised when an invitation is past its expiry time."""


class ProjectInvitationStateError(ProjectAccessError):
    """Raised when an invitation is no longer pending."""


ROLE_PERMISSIONS: dict[ProjectRole, frozenset[ProjectPermission]] = {
    ProjectRole.VIEWER: frozenset({ProjectPermission.READ}),
    ProjectRole.EDITOR: frozenset(
        {
            ProjectPermission.READ,
            ProjectPermission.MANAGE_TASKS,
            ProjectPermission.EDIT_SCRIPT,
            ProjectPermission.EDIT_ASSET,
        }
    ),
    ProjectRole.REVIEWER: frozenset({ProjectPermission.READ, ProjectPermission.REVIEW_ASSET}),
    ProjectRole.OWNER: frozenset(ProjectPermission),
}


class ProjectAccessService:
    def __init__(
        self,
        store: NovelStore,
        *,
        auth_mode: str,
        audit_service: AuditService | None = None,
    ) -> None:
        self._store = store
        self._auth_mode = auth_mode
        self._audit_service = audit_service

    async def provision_owner(self, project: NovelProjectRecord, identity: ActorIdentity) -> ProjectMemberRecord:
        return await self._store.upsert_novel_project_member(
            ProjectMemberRecord(
                project_id=project.id,
                actor_id=identity.actor_id,
                actor_name=identity.actor_name,
                role=ProjectRole.OWNER,
            )
        )

    async def get_access(self, project_id: UUID, identity: ActorIdentity) -> ProjectAccessRecord:
        project = await self._store.get_novel_project(project_id)
        if project is None:
            raise ProjectAccessNotFoundError
        member = await self._store.get_novel_project_member(project_id, identity.actor_id)
        if member is None:
            # Local mode is an explicit single-machine development escape hatch.
            # Signed-header mode requires an explicit membership row.
            if self._auth_mode == "local":
                member = ProjectMemberRecord(
                    project_id=project_id,
                    actor_id=identity.actor_id,
                    actor_name=identity.actor_name,
                    role=ProjectRole.OWNER,
                )
            else:
                raise ProjectPermissionDeniedError
        return ProjectAccessRecord(
            project_id=project_id,
            actor_id=identity.actor_id,
            actor_name=member.actor_name,
            role=member.role,
            permissions=sorted(ROLE_PERMISSIONS[member.role], key=lambda item: item.value),
        )

    async def require(
        self,
        project_id: UUID,
        identity: ActorIdentity,
        permission: ProjectPermission,
    ) -> ProjectAccessRecord:
        access = await self.get_access(project_id, identity)
        if permission not in access.permissions:
            raise ProjectPermissionDeniedError
        return access

    async def list_accessible_projects(
        self,
        projects: list[NovelProjectRecord],
        identity: ActorIdentity,
    ) -> list[NovelProjectRecord]:
        if self._auth_mode == "local":
            return projects
        accessible: list[NovelProjectRecord] = []
        for project in projects:
            if await self._store.get_novel_project_member(project.id, identity.actor_id) is not None:
                accessible.append(project)
        return accessible

    async def list_members(self, project_id: UUID, identity: ActorIdentity) -> list[ProjectMemberRecord]:
        await self.require(project_id, identity, ProjectPermission.READ)
        return await self._store.list_novel_project_members(project_id)

    async def upsert_member(
        self,
        project_id: UUID,
        actor_id: str,
        payload: ProjectMemberUpsertRequest,
        identity: ActorIdentity,
    ) -> ProjectMemberRecord:
        await self.require(project_id, identity, ProjectPermission.MANAGE_MEMBERS)
        normalized_actor_id = actor_id.strip()
        if not normalized_actor_id or len(normalized_actor_id) > 120:
            raise ProjectMemberInputError
        current = await self._store.get_novel_project_member(project_id, normalized_actor_id)
        if (
            current is not None
            and current.role == ProjectRole.OWNER
            and payload.role != ProjectRole.OWNER
            and await self._owner_count(project_id) <= 1
        ):
            raise ProjectOwnerRequiredError
        if current is not None and current.actor_name == payload.actor_name.strip() and current.role == payload.role:
            return current
        saved = await self._store.upsert_novel_project_member(
            ProjectMemberRecord(
                project_id=project_id,
                actor_id=normalized_actor_id,
                actor_name=payload.actor_name.strip(),
                role=payload.role,
                updated_at=utc_now(),
            )
        )
        if self._audit_service is not None:
            if current is None:
                action = AuditAction.PROJECT_MEMBER_ADDED
            elif current.role != saved.role:
                action = AuditAction.PROJECT_MEMBER_ROLE_CHANGED
            else:
                action = AuditAction.PROJECT_MEMBER_UPDATED
            metadata = {
                "member_actor_id": saved.actor_id,
                "member_name": saved.actor_name,
                "role": saved.role.value,
            }
            if current is not None:
                metadata.update(
                    {
                        "previous_name": current.actor_name,
                        "previous_role": current.role.value,
                    }
                )
            await self._audit_service.record(
                AuditLogRecord(
                    project_id=project_id,
                    entity_type=AuditEntityType.PROJECT_MEMBER,
                    entity_id=saved.id,
                    action=action,
                    actor_id=identity.actor_id,
                    actor_name=identity.actor_name,
                    metadata=metadata,
                )
            )
        return saved

    async def remove_member(self, project_id: UUID, actor_id: str, identity: ActorIdentity) -> None:
        await self.require(project_id, identity, ProjectPermission.MANAGE_MEMBERS)
        normalized_actor_id = actor_id.strip()
        if not normalized_actor_id or len(normalized_actor_id) > 120:
            raise ProjectMemberInputError
        member = await self._store.get_novel_project_member(project_id, normalized_actor_id)
        if member is None:
            raise ProjectMemberNotFoundError
        if member.role == ProjectRole.OWNER and await self._owner_count(project_id) <= 1:
            raise ProjectOwnerRequiredError
        await self._store.delete_novel_project_member(project_id, normalized_actor_id)
        if self._audit_service is not None:
            await self._audit_service.record(
                AuditLogRecord(
                    project_id=project_id,
                    entity_type=AuditEntityType.PROJECT_MEMBER,
                    entity_id=member.id,
                    action=AuditAction.PROJECT_MEMBER_REMOVED,
                    actor_id=identity.actor_id,
                    actor_name=identity.actor_name,
                    metadata={
                        "member_actor_id": member.actor_id,
                        "member_name": member.actor_name,
                        "role": member.role.value,
                    },
                )
            )

    async def create_invitation(
        self,
        project_id: UUID,
        payload: ProjectInvitationCreateRequest,
        identity: ActorIdentity,
    ) -> tuple[ProjectInvitationRecord, str]:
        await self.require(project_id, identity, ProjectPermission.MANAGE_MEMBERS)
        existing_member = await self._store.get_novel_project_member(project_id, payload.invitee_actor_id)
        if existing_member is not None:
            raise ProjectInvitationConflictError
        now = utc_now()
        for invitation in await self._store.list_novel_project_invitations(project_id):
            if invitation.invitee_actor_id != payload.invitee_actor_id:
                continue
            invitation = await self._expire_if_needed(invitation, now=now)
            if invitation.status == ProjectInvitationStatus.PENDING:
                raise ProjectInvitationConflictError
        token = secrets.token_urlsafe(32)
        invitation = await self._store.create_novel_project_invitation(
            ProjectInvitationRecord(
                project_id=project_id,
                invitee_actor_id=payload.invitee_actor_id,
                invitee_name=payload.invitee_name,
                role=payload.role,
                token_hash=self._hash_invitation_token(token),
                expires_at=now + timedelta(seconds=payload.expires_in_seconds),
                invited_by_actor_id=identity.actor_id,
                invited_by_name=identity.actor_name,
                created_at=now,
                updated_at=now,
            )
        )
        await self._record_audit(
            project_id=project_id,
            entity_id=invitation.id,
            entity_type=AuditEntityType.PROJECT_INVITATION,
            action=AuditAction.PROJECT_INVITATION_CREATED,
            identity=identity,
            metadata={
                "invitee_actor_id": invitation.invitee_actor_id,
                "invitee_name": invitation.invitee_name,
                "role": invitation.role.value,
                "expires_at": invitation.expires_at.isoformat(),
            },
        )
        return invitation, token

    async def list_invitations(
        self,
        project_id: UUID,
        identity: ActorIdentity,
    ) -> list[ProjectInvitationRecord]:
        await self.require(project_id, identity, ProjectPermission.MANAGE_MEMBERS)
        now = utc_now()
        invitations: list[ProjectInvitationRecord] = []
        for invitation in await self._store.list_novel_project_invitations(project_id):
            invitations.append(await self._expire_if_needed(invitation, now=now))
        return invitations

    async def revoke_invitation(
        self,
        project_id: UUID,
        invitation_id: UUID,
        identity: ActorIdentity,
    ) -> ProjectInvitationRecord:
        await self.require(project_id, identity, ProjectPermission.MANAGE_MEMBERS)
        invitation = await self._store.get_novel_project_invitation(invitation_id)
        if invitation is None or invitation.project_id != project_id:
            raise ProjectInvitationNotFoundError
        invitation = await self._expire_if_needed(invitation)
        if invitation.status != ProjectInvitationStatus.PENDING:
            raise ProjectInvitationStateError
        now = utc_now()
        revoked = invitation.model_copy(
            update={
                "status": ProjectInvitationStatus.REVOKED,
                "revoked_at": now,
                "updated_at": now,
            }
        )
        saved = await self._store.update_novel_project_invitation(revoked)
        await self._record_audit(
            project_id=project_id,
            entity_id=saved.id,
            entity_type=AuditEntityType.PROJECT_INVITATION,
            action=AuditAction.PROJECT_INVITATION_REVOKED,
            identity=identity,
            metadata={
                "invitee_actor_id": saved.invitee_actor_id,
                "role": saved.role.value,
            },
        )
        return saved

    async def accept_invitation(
        self,
        invitation_id: UUID,
        payload: ProjectInvitationAcceptRequest,
        identity: ActorIdentity,
    ) -> tuple[ProjectInvitationRecord, ProjectMemberRecord]:
        invitation = await self._store.get_novel_project_invitation(invitation_id)
        if invitation is None:
            raise ProjectInvitationNotFoundError
        invitation = await self._expire_if_needed(invitation)
        if invitation.status == ProjectInvitationStatus.EXPIRED:
            raise ProjectInvitationExpiredError
        if invitation.status != ProjectInvitationStatus.PENDING:
            raise ProjectInvitationStateError
        if not hmac.compare_digest(invitation.token_hash, self._hash_invitation_token(payload.token)):
            raise ProjectInvitationInvalidError
        if invitation.invitee_actor_id != identity.actor_id:
            raise ProjectInvitationInvalidError
        if await self._store.get_novel_project_member(invitation.project_id, identity.actor_id) is not None:
            raise ProjectInvitationConflictError
        accepted_result = await self._store.accept_novel_project_invitation(
            invitation_id,
            self._hash_invitation_token(payload.token),
            identity.actor_id,
            utc_now(),
        )
        if accepted_result is None:
            current = await self._store.get_novel_project_invitation(invitation_id)
            if current is None:
                raise ProjectInvitationNotFoundError
            current = await self._expire_if_needed(current)
            if current.status == ProjectInvitationStatus.EXPIRED:
                raise ProjectInvitationExpiredError
            if current.status != ProjectInvitationStatus.PENDING:
                raise ProjectInvitationStateError
            if await self._store.get_novel_project_member(
                current.project_id, identity.actor_id
            ) is not None:
                raise ProjectInvitationConflictError
            raise ProjectInvitationConflictError
        saved_invitation, member = accepted_result
        await self._record_audit(
            project_id=invitation.project_id,
            entity_id=saved_invitation.id,
            entity_type=AuditEntityType.PROJECT_INVITATION,
            action=AuditAction.PROJECT_INVITATION_ACCEPTED,
            identity=identity,
            metadata={
                "invitee_actor_id": saved_invitation.invitee_actor_id,
                "role": saved_invitation.role.value,
            },
        )
        await self._record_audit(
            project_id=invitation.project_id,
            entity_id=member.id,
            entity_type=AuditEntityType.PROJECT_MEMBER,
            action=AuditAction.PROJECT_MEMBER_ADDED,
            identity=identity,
            metadata={
                "member_actor_id": member.actor_id,
                "member_name": member.actor_name,
                "role": member.role.value,
                "invitation_id": str(saved_invitation.id),
            },
        )
        return saved_invitation, member

    async def _expire_if_needed(
        self,
        invitation: ProjectInvitationRecord,
        *,
        now: datetime | None = None,
    ) -> ProjectInvitationRecord:
        now = now or utc_now()
        if invitation.status != ProjectInvitationStatus.PENDING or invitation.expires_at > now:
            return invitation
        expired = invitation.model_copy(
            update={"status": ProjectInvitationStatus.EXPIRED, "updated_at": now}
        )
        return await self._store.update_novel_project_invitation(expired)

    @staticmethod
    def _hash_invitation_token(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    async def _record_audit(
        self,
        *,
        project_id: UUID,
        entity_id: UUID,
        entity_type: AuditEntityType,
        action: AuditAction,
        identity: ActorIdentity,
        metadata: dict[str, object],
    ) -> None:
        if self._audit_service is None:
            return
        await self._audit_service.record(
            AuditLogRecord(
                project_id=project_id,
                entity_type=entity_type,
                entity_id=entity_id,
                action=action,
                actor_id=identity.actor_id,
                actor_name=identity.actor_name,
                metadata=metadata,
            )
        )

    async def _owner_count(self, project_id: UUID) -> int:
        return sum(
            member.role == ProjectRole.OWNER
            for member in await self._store.list_novel_project_members(project_id)
        )
