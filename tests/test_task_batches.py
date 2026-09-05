from __future__ import annotations

import asyncio
import time
from uuid import uuid4

from fastapi.testclient import TestClient

from app.domain.models import (
    GenerationTaskRecord,
    ProjectRecord,
    StageName,
    StageRun,
    TaskBatchCreateRequest,
    TaskStatus,
)
from app.services.task_batch_service import TaskBatchService
from app.repositories.in_memory import InMemoryStore


def _task(project_id, status: TaskStatus) -> GenerationTaskRecord:
    return GenerationTaskRecord(
        project_id=project_id,
        status=status,
        current_stage=StageName.SCRIPT if status != TaskStatus.SUCCEEDED else None,
        progress=100 if status == TaskStatus.SUCCEEDED else 0,
        stages=[StageRun(stage=StageName.SCRIPT, status=status, progress=100 if status == TaskStatus.SUCCEEDED else 0)],
    )


def test_task_batch_aggregates_status_and_resumes_failed_tasks() -> None:
    async def exercise() -> None:
        store = InMemoryStore()
        project = await store.create_project(
            ProjectRecord(title="批次项目", topic="批次测试", language="zh-CN", target_duration_seconds=60, aspect_ratio="9:16", tone="清晰")
        )
        succeeded = _task(project.id, TaskStatus.SUCCEEDED)
        failed = _task(project.id, TaskStatus.FAILED)
        await store.create_task(succeeded)
        await store.create_task(failed)

        async def retry(task_id):
            task = await store.get_task(task_id)
            assert task is not None
            task.status = TaskStatus.QUEUED
            task.current_stage = StageName.SCRIPT
            await store.update_task(task)

        service = TaskBatchService(store, retry)
        request = TaskBatchCreateRequest(
            project_id=project.id,
            task_ids=[succeeded.id, failed.id],
            label="第一批",
        )
        batch, reused = await service.create(request, "batch-key")
        assert reused is False
        assert batch.status.value == "partial"
        assert batch.succeeded_count == 1
        assert batch.failed_count == 1

        same_batch, reused = await service.create(request, "batch-key")
        assert reused is True
        assert same_batch.id == batch.id

        resumed = await service.resume(batch.id)
        assert resumed.retried_task_ids == [failed.id]
        assert resumed.skipped_task_ids == [succeeded.id]
        assert resumed.batch.status.value == "running"

    asyncio.run(exercise())


def test_task_batch_api_is_idempotent_and_queryable(client: TestClient) -> None:
    project_response = client.post(
        "/api/v1/projects",
        json={"title": "批次 API", "topic": "批量任务状态", "target_duration_seconds": 60},
    )
    assert project_response.status_code == 201
    project_id = project_response.json()["id"]

    task_ids: list[str] = []
    for index in range(2):
        response = client.post(f"/api/v1/projects/{project_id}/generations")
        assert response.status_code == 202
        task_ids.append(response.json()["id"])

    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        statuses = [client.get(f"/api/v1/tasks/{task_id}").json()["status"] for task_id in task_ids]
        if all(status in {"succeeded", "failed"} for status in statuses):
            break
        time.sleep(0.02)

    payload = {"project_id": project_id, "task_ids": task_ids, "label": "脚本批次"}
    headers = {"Idempotency-Key": "batch-api-key"}
    first = client.post("/api/v1/task-batches", json=payload, headers=headers)
    second = client.post("/api/v1/task-batches", json=payload, headers=headers)
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    assert first.json()["total_count"] == 2

    batch_id = first.json()["id"]
    detail = client.get(f"/api/v1/task-batches/{batch_id}")
    assert detail.status_code == 200
    assert detail.json()["project_id"] == project_id

    listed = client.get("/api/v1/task-batches", params={"project_id": project_id})
    assert listed.status_code == 200
    assert listed.json()["total"] == 1


def test_task_batch_rejects_tasks_from_another_project(client: TestClient) -> None:
    first = client.post(
        "/api/v1/projects",
        json={"title": "批次项目一", "topic": "一", "target_duration_seconds": 60},
    ).json()["id"]
    second = client.post(
        "/api/v1/projects",
        json={"title": "批次项目二", "topic": "二", "target_duration_seconds": 60},
    ).json()["id"]
    task = client.post(f"/api/v1/projects/{second}/generations").json()["id"]

    response = client.post(
        "/api/v1/task-batches",
        json={"project_id": first, "task_ids": [task]},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "BATCH_TASK_PROJECT_MISMATCH"
