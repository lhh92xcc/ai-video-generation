"""Batch retry orchestration for shot tasks that failed identity review."""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Iterable
from uuid import UUID, uuid4

from app.domain.models import (
    GenerationTaskKind,
    GenerationTaskRecord,
    IdentityAuditStatus,
    IdentityRetryRequest,
    IdentityRetryResponse,
    IdentityRetrySkippedItem,
    StageName,
    StageRun,
    TaskBatchCreateRequest,
    TaskStatus,
    utc_now,
)
from app.repositories.protocol import ProjectTaskStore
from app.services.task_batch_service import TaskBatchService


class IdentityRetryInputError(Exception):
    """Raised when an identity retry request cannot be scheduled safely."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class IdentityAuditRetryService:
    """Create independent, idempotent video tasks for identity-failed shots."""

    def __init__(
        self,
        store: ProjectTaskStore,
        task_queue,
        task_batch_service: TaskBatchService,
    ) -> None:
        self._store = store
        self._task_queue = task_queue
        self._task_batch_service = task_batch_service

    async def retry(
        self,
        project_id: UUID,
        request: IdentityRetryRequest,
        idempotency_key: str | None = None,
    ) -> IdentityRetryResponse:
        novel_project = await self._store.get_novel_project(project_id)  # type: ignore[attr-defined]
        if novel_project is None:
            raise IdentityRetryInputError(
                "NOVEL_PROJECT_NOT_FOUND",
                "Novel project was not found",
            )
        if request.episode_id is not None:
            episode = await self._store.get_episode(request.episode_id)  # type: ignore[attr-defined]
            if episode is None or episode.project_id != project_id:
                raise IdentityRetryInputError(
                    "EPISODE_PROJECT_MISMATCH",
                    "The selected episode does not belong to this project",
                )

        latest_tasks = await self._latest_tasks(project_id, request)
        candidates: list[tuple[GenerationTaskRecord, str]] = []
        skipped: list[IdentityRetrySkippedItem] = []
        selected_statuses = {status.value for status in request.identity_statuses}
        for task in latest_tasks.values():
            episode_id, shot_index = self._shot_identity(task)
            if episode_id is None or shot_index is None:
                continue
            report = self._identity_report(task)
            status = str(report.get("status", "missing")) if report else "missing"
            reason: str | None = None
            if task.status in {TaskStatus.CREATED, TaskStatus.QUEUED, TaskStatus.RUNNING}:
                reason = "latest_task_is_active"
            elif task.status != TaskStatus.SUCCEEDED:
                reason = "latest_task_did_not_succeed"
            elif status == "passed":
                reason = "identity_audit_passed"
            elif status not in selected_statuses:
                reason = "identity_status_not_selected"
            else:
                candidates.append((task, status))
            if reason is not None:
                skipped.append(
                    IdentityRetrySkippedItem(
                        source_task_id=task.id,
                        episode_id=episode_id,
                        shot_index=shot_index,
                        reason=reason,
                    )
                )

        candidates.sort(
            key=lambda item: (
                str(self._shot_identity(item[0])[0]),
                int(self._shot_identity(item[0])[1] or 0),
            )
        )
        selected_candidates = candidates[: request.max_tasks]
        skipped.extend(
            self._skipped_for_overflow(task, status)
            for task, status in candidates[request.max_tasks :]
        )

        batch_key = self._batch_key(project_id, selected_candidates, idempotency_key)
        existing_batch = await self._store.get_task_batch_by_idempotency_key(
            project_id,
            batch_key,
        )
        if existing_batch is not None:
            refreshed = await self._task_batch_service.get(existing_batch.id)
            existing_ids = list(refreshed.task_ids)
            return IdentityRetryResponse(
                project_id=project_id,
                batch=refreshed,
                inspected_task_count=len(latest_tasks),
                retried_task_ids=existing_ids,
                skipped_task_ids=[item.source_task_id for item in skipped],
                skipped=skipped,
            )

        if not selected_candidates:
            return IdentityRetryResponse(
                project_id=project_id,
                inspected_task_count=len(latest_tasks),
                skipped_task_ids=[item.source_task_id for item in skipped],
                skipped=skipped,
            )

        retried_task_ids: list[UUID] = []
        for source_task, _status in selected_candidates:
            if self._generation_attempt(source_task) >= 100:
                raise IdentityRetryInputError(
                    "IDENTITY_RETRY_ATTEMPT_LIMIT",
                    f"Task {source_task.id} has reached the maximum generation attempt",
                )
        for source_task, status in selected_candidates:
            retry_task, created = await self._create_retry_task(source_task, status)
            retried_task_ids.append(retry_task.id)
            if created:
                await self._task_queue.enqueue(retry_task.id)

        batch, _ = await self._task_batch_service.create(
            TaskBatchCreateRequest(
                project_id=project_id,
                task_ids=retried_task_ids,
                label=request.label,
            ),
            batch_key,
        )
        return IdentityRetryResponse(
            project_id=project_id,
            batch=batch,
            inspected_task_count=len(latest_tasks),
            retried_task_ids=retried_task_ids,
            skipped_task_ids=[item.source_task_id for item in skipped],
            skipped=skipped,
        )

    async def _latest_tasks(
        self,
        project_id: UUID,
        request: IdentityRetryRequest,
    ) -> dict[tuple[UUID, int], GenerationTaskRecord]:
        tasks = await self._store.list_tasks(
            project_id=project_id,
            kind=GenerationTaskKind.VIDEO_CLIP.value,
            limit=5000,
        )
        latest: dict[tuple[UUID, int], GenerationTaskRecord] = {}
        for task in tasks:
            episode_id, shot_index = self._shot_identity(task)
            if episode_id is None or shot_index is None:
                continue
            if request.episode_id is not None and episode_id != request.episode_id:
                continue
            key = (episode_id, shot_index)
            current = latest.get(key)
            if current is None or self._task_order_key(task) > self._task_order_key(current):
                latest[key] = task
        return latest

    @classmethod
    def _task_order_key(
        cls,
        task: GenerationTaskRecord,
    ) -> tuple[int, object, object, str]:
        """Order attempts before timestamps so old retries cannot win later.

        A human review or metadata update can change ``updated_at`` on an older
        task after a newer generation attempt already exists.  The generation
        attempt is the authoritative shot version; timestamps only break ties.
        """

        return (
            cls._generation_attempt(task),
            task.updated_at,
            task.created_at,
            str(task.id),
        )

    async def _create_retry_task(
        self,
        source_task: GenerationTaskRecord,
        identity_status: str,
    ) -> tuple[GenerationTaskRecord, bool]:
        input_data = copy.deepcopy(source_task.input_data)
        current_attempt = self._generation_attempt(source_task)
        if current_attempt >= 100:
            raise IdentityRetryInputError(
                "IDENTITY_RETRY_ATTEMPT_LIMIT",
                f"Task {source_task.id} has reached the maximum generation attempt",
            )
        next_attempt = current_attempt + 1
        input_data.update(
            {
                "generation_attempt": next_attempt,
                "retry_of_task_id": str(source_task.id),
                "retry_reason": f"identity_audit:{identity_status}",
            }
        )
        stage = StageRun(
            stage=StageName.VIDEO_CLIP,
            status=TaskStatus.QUEUED,
            attempt=next_attempt,
        )
        task = GenerationTaskRecord(
            id=uuid4(),
            project_id=source_task.project_id,
            kind=GenerationTaskKind.VIDEO_CLIP,
            input_data=input_data,
            status=TaskStatus.QUEUED,
            current_stage=StageName.VIDEO_CLIP,
            stages=[stage],
            updated_at=utc_now(),
        )
        key = f"video_clip:identity-retry:{source_task.id}:attempt-{next_attempt}"
        stored, reused = await self._store.create_task(task, key)
        return stored, not reused

    @staticmethod
    def _shot_identity(task: GenerationTaskRecord) -> tuple[UUID | None, int | None]:
        try:
            episode_id = UUID(str(task.input_data.get("episode_id", "")))
            shot_index = int(task.input_data.get("shot_index"))
        except (TypeError, ValueError, AttributeError):
            return None, None
        return episode_id, shot_index if shot_index >= 1 else None

    @staticmethod
    def _identity_report(task: GenerationTaskRecord) -> dict[str, object] | None:
        for artifact in reversed(task.artifacts):
            report = artifact.metadata.get("identity_audit")
            if isinstance(report, dict):
                return report
        return None

    @staticmethod
    def _generation_attempt(task: GenerationTaskRecord) -> int:
        try:
            return max(1, int(task.input_data.get("generation_attempt", 1)))
        except (TypeError, ValueError):
            return 1

    @classmethod
    def _batch_key(
        cls,
        project_id: UUID,
        candidates: Iterable[tuple[GenerationTaskRecord, str]],
        explicit_key: str | None,
    ) -> str:
        if explicit_key:
            return f"identity-audit-retry:v1:{explicit_key}"
        payload = [
            {
                "source_task_id": str(task.id),
                "status": status,
                "next_attempt": cls._generation_attempt(task) + 1,
            }
            for task, status in candidates
        ]
        digest = hashlib.sha256(
            json.dumps(
                {"project_id": str(project_id), "candidates": payload},
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()[:32]
        return f"identity-audit-retry:v1:{digest}"

    @staticmethod
    def _skipped_for_overflow(
        task: GenerationTaskRecord,
        _status: str,
    ) -> IdentityRetrySkippedItem:
        episode_id, shot_index = IdentityAuditRetryService._shot_identity(task)
        assert episode_id is not None and shot_index is not None
        return IdentityRetrySkippedItem(
            source_task_id=task.id,
            episode_id=episode_id,
            shot_index=shot_index,
            reason="max_tasks_reached",
        )
