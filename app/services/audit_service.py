"""Audit recording for user-visible content editing operations."""

from __future__ import annotations

from uuid import UUID

from app.domain.models import AuditEntityType, AuditLogRecord
from app.repositories.protocol import NovelStore


class AuditService:
    """Persist immutable editing events without implementing authentication."""

    def __init__(self, store: NovelStore) -> None:
        self._store = store

    async def record(self, log: AuditLogRecord) -> AuditLogRecord:
        return await self._store.save_audit_log(log)

    async def list_logs(
        self,
        project_id: UUID,
        entity_type: AuditEntityType | None = None,
        entity_id: UUID | None = None,
        limit: int = 100,
    ) -> list[AuditLogRecord]:
        return await self._store.list_audit_logs(
            project_id,
            entity_type=entity_type,
            entity_id=entity_id,
            limit=limit,
        )
