"""Batch grouping and resumable retry orchestration for generation tasks."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from uuid import UUID

from app.domain.models import (
    TaskBatchCreateRequest,
    TaskBatchRecord,
    TaskBatchResumeResponse,
    TaskBatchStatus,
    TaskStatus,
    utc_now,
)
from app.repositories.protocol import ProjectTaskStore


class TaskBatchNotFoundError(Exception):
    pass


class BatchTaskNotFoundError(Exception):
    def __init__(self, task_id: UUID) -> None:
        self.task_id = task_id


class BatchTaskProjectMismatchError(Exception):
    pass


class TaskBatchResumeError(Exception):
    pass


class TaskBatchService:
    def __init__(
        self,
        store: ProjectTaskStore,
        retry_task: Callable[[UUID], Awaitable[object]],
    ) -> None:
        self._store = store
        self._retry_task = retry_task

    async def create(
        self,
        request: TaskBatchCreateRequest,
        idempotency_key: str | None = None,
    ) -> tuple[TaskBatchRecord, bool]:
        for task_id in request.task_ids:
            task = await self._store.get_task(task_id)
            if task is None:
                raise BatchTaskNotFoundError(task_id)
            if task.project_id != request.project_id:
                raise BatchTaskProjectMismatchError
        batch = TaskBatchRecord(
            project_id=request.project_id,
            task_ids=request.task_ids,
            label=request.label,
            total_count=len(request.task_ids),
            updated_at=utc_now(),
        )
        stored, reused = await self._store.create_task_batch(batch, idempotency_key)
        return await self._refresh(stored), reused

    async def get(self, batch_id: UUID) -> TaskBatchRecord:
        batch = await self._store.get_task_batch(batch_id)
        if batch is None:
            raise TaskBatchNotFoundError
        return await self._refresh(batch)

    async def list(self, project_id: UUID, limit: int = 50) -> list[TaskBatchRecord]:
        batches = await self._store.list_task_batches(project_id, limit)
        return [await self._refresh(batch) for batch in batches]

    async def resume(self, batch_id: UUID) -> TaskBatchResumeResponse:
        batch = await self.get(batch_id)
        retried: list[UUID] = []
        skipped: list[UUID] = []
        for task_id in batch.task_ids:
            task = await self._store.get_task(task_id)
            if task is None:
                skipped.append(task_id)
                continue
            if task.status != TaskStatus.FAILED:
                skipped.append(task_id)
                continue
            try:
                await self._retry_task(task_id)
            except Exception:
                skipped.append(task_id)
                continue
            retried.append(task_id)
        refreshed = await self.get(batch_id)
        return TaskBatchResumeResponse(
            batch=refreshed,
            retried_task_ids=retried,
            skipped_task_ids=skipped,
        )

    async def _refresh(self, batch: TaskBatchRecord) -> TaskBatchRecord:
        tasks = [await self._store.get_task(task_id) for task_id in batch.task_ids]
        existing = [task for task in tasks if task is not None]
        succeeded = sum(task.status == TaskStatus.SUCCEEDED for task in existing)
        failed = sum(task.status == TaskStatus.FAILED for task in existing)
        active = sum(
            task.status in {
                TaskStatus.CREATED,
                TaskStatus.QUEUED,
                TaskStatus.RUNNING,
            }
            for task in existing
        )
        if succeeded == len(batch.task_ids) and batch.task_ids:
            status = TaskBatchStatus.SUCCEEDED
        elif active:
            status = TaskBatchStatus.RUNNING
        elif failed and succeeded:
            status = TaskBatchStatus.PARTIAL
        elif failed:
            status = TaskBatchStatus.FAILED
        else:
            status = TaskBatchStatus.CREATED
        refreshed = batch.model_copy(
            update={
                "status": status,
                "total_count": len(batch.task_ids),
                "succeeded_count": succeeded,
                "failed_count": failed,
                "active_count": active,
                "updated_at": utc_now(),
            }
        )
        return refreshed
