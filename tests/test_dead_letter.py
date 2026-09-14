from __future__ import annotations

import asyncio
from dataclasses import replace
from uuid import uuid4

from fastapi.testclient import TestClient

from app.auth.identity import sign_identity_headers
from app.config import load_settings
from app.domain.dead_letter import open_dead_letter
from app.domain.models import (
    GenerationTaskKind,
    GenerationTaskRecord,
    ProjectRecord,
    StageName,
    StageRun,
    TaskError,
    TaskStatus,
)
from app.main import create_app
from app.queue import InProcessTaskQueue
from app.repositories.in_memory import InMemoryStore
from app.services.task_service import TaskService
from app.workers.generation_worker import _maybe_auto_retry, _open_dead_letter


def _failed_task(project_id, *, error_code: str = "VIDEO_PROVIDER_TIMEOUT") -> GenerationTaskRecord:
    return GenerationTaskRecord(
        project_id=project_id,
        kind=GenerationTaskKind.VIDEO_CLIP,
        status=TaskStatus.FAILED,
        current_stage=StageName.VIDEO_CLIP,
        error=TaskError(code=error_code, message="视频 Provider 暂时不可用"),
        stages=[StageRun(stage=StageName.VIDEO_CLIP, status=TaskStatus.FAILED)],
    )


def test_worker_opens_dead_letter_after_automatic_retries_are_exhausted() -> None:
    async def exercise() -> None:
        store = InMemoryStore()
        queue = InProcessTaskQueue()
        task = _failed_task(uuid4())
        task.input_data["auto_retry_count"] = 2
        await store.create_task(task)
        service = TaskService(store, None, queue)  # type: ignore[arg-type]
        settings = replace(
            load_settings(),
            worker_auto_retry_enabled=True,
            worker_max_auto_retries=2,
        )

        retried = await _maybe_auto_retry(task, service, settings)
        saved = await store.get_task(task.id)
        await queue.close()

        assert retried is False
        assert saved is not None
        assert saved.input_data["dead_letter"]["status"] == "open"
        assert saved.input_data["dead_letter"]["auto_retry_count"] == 2
        assert saved.input_data["dead_letter"]["max_auto_retries"] == 2

    asyncio.run(exercise())


def test_worker_opens_dead_letter_for_non_retryable_error() -> None:
    async def exercise() -> None:
        store = InMemoryStore()
        queue = InProcessTaskQueue()
        task = _failed_task(uuid4(), error_code="VIDEO_PROVIDER_AUTH_FAILED")
        await store.create_task(task)
        service = TaskService(store, None, queue)  # type: ignore[arg-type]
        settings = replace(load_settings(), worker_auto_retry_enabled=True)

        await _maybe_auto_retry(task, service, settings)
        saved = await store.get_task(task.id)
        await queue.close()

        assert saved is not None
        assert saved.input_data["dead_letter"]["status"] == "open"
        assert saved.input_data["dead_letter"]["error_code"] == "VIDEO_PROVIDER_AUTH_FAILED"
        assert saved.input_data["dead_letter"]["auto_retry_count"] == 0

    asyncio.run(exercise())


def test_dead_letter_requeue_preserves_history_and_resets_retry_budget() -> None:
    async def exercise() -> None:
        store = InMemoryStore()
        queue = InProcessTaskQueue()
        enqueued: list = []

        async def handler(task_id) -> None:
            enqueued.append(task_id)

        queue.set_handler(handler)
        task = _failed_task(uuid4())
        task.input_data["auto_retry_count"] = 3
        open_dead_letter(
            task.input_data,
            error_code=task.error.code,
            error_message=task.error.message,
            auto_retry_count=3,
            max_auto_retries=3,
            now="2026-09-14T00:00:00+00:00",
        )
        await store.create_task(task)
        service = TaskService(store, None, queue)  # type: ignore[arg-type]

        requeued = await service.requeue_dead_letter_task(task.id)
        await queue.close()
        assert requeued.status == TaskStatus.QUEUED
        assert requeued.input_data["dead_letter"]["status"] == "requeued"
        assert requeued.input_data["dead_letter"]["requeue_count"] == 1
        assert requeued.input_data["auto_retry_count"] == 0
        assert enqueued == [task.id]

        failed_again = await store.get_task(task.id)
        assert failed_again is not None
        failed_again.status = TaskStatus.FAILED
        failed_again.error = TaskError(code="VIDEO_PROVIDER_TIMEOUT", message="第二次失败")
        await store.update_task(failed_again)
        settings = replace(load_settings(), worker_max_auto_retries=3)
        await _open_dead_letter(failed_again, service, settings)

        reopened = await store.get_task(task.id)

        assert reopened is not None
        assert reopened.input_data["dead_letter"]["status"] == "open"
        assert reopened.input_data["dead_letter"]["requeue_count"] == 1
        assert reopened.input_data["dead_letter"]["reopen_count"] == 1

    asyncio.run(exercise())


def test_dead_letter_api_lists_and_requeues_failed_task(client: TestClient) -> None:
    async def seed() -> tuple[str, str]:
        project = await client.app.state.store.create_project(
            ProjectRecord(
                title="死信 API 项目",
                topic="人工恢复",
                language="zh-CN",
                target_duration_seconds=60,
                aspect_ratio="9:16",
                tone="清晰",
            )
        )
        task = _failed_task(project.id)
        open_dead_letter(
            task.input_data,
            error_code="VIDEO_PROVIDER_TIMEOUT",
            error_message="视频服务超时",
            auto_retry_count=2,
            max_auto_retries=2,
        )
        await client.app.state.store.create_task(task)
        return str(project.id), str(task.id)

    project_id, task_id = asyncio.run(seed())
    listed = client.get("/api/v1/system/dead-letters", params={"project_id": project_id})
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert listed.json()["items"][0]["task_id"] == task_id

    requeued = client.post(f"/api/v1/system/dead-letters/{task_id}/requeue")
    assert requeued.status_code == 202
    assert requeued.json()["status"] == "queued"
    assert requeued.json()["input_data"]["dead_letter"]["status"] == "requeued"

    after = client.get("/api/v1/system/dead-letters", params={"project_id": project_id})
    assert after.status_code == 200
    assert after.json()["total"] == 0


def test_dead_letter_api_enforces_project_access() -> None:
    settings = replace(
        load_settings(),
        auth_mode="signed_header",
        auth_shared_secret="dead-letter-access-secret",
    )
    with TestClient(create_app(settings)) as signed_client:
        create_path = "/api/v1/novel-projects"
        owner_headers = sign_identity_headers(
            secret="dead-letter-access-secret",
            method="POST",
            path=create_path,
            actor_id="dead-letter-owner",
            actor_name="Dead Letter Owner",
        )
        created = signed_client.post(
            create_path,
            json={"title": "死信权限项目"},
            headers=owner_headers,
        )
        assert created.status_code == 201
        project_id = created.json()["id"]

        task = _failed_task(project_id)
        open_dead_letter(
            task.input_data,
            error_code="INPUT_INVALID",
            error_message="需要人工确认",
            auto_retry_count=0,
            max_auto_retries=2,
        )
        asyncio.run(signed_client.app.state.store.create_task(task))

        path = "/api/v1/system/dead-letters"
        outsider_headers = sign_identity_headers(
            secret="dead-letter-access-secret",
            method="GET",
            path=path,
            actor_id="dead-letter-outsider",
            actor_name="Dead Letter Outsider",
        )
        denied = signed_client.get(
            path,
            params={"project_id": project_id},
            headers=outsider_headers,
        )
        assert denied.status_code == 403
        assert denied.json()["error"]["code"] == "PROJECT_PERMISSION_DENIED"

        owner_list_headers = sign_identity_headers(
            secret="dead-letter-access-secret",
            method="GET",
            path=path,
            actor_id="dead-letter-owner",
            actor_name="Dead Letter Owner",
        )
        allowed = signed_client.get(
            path,
            params={"project_id": project_id},
            headers=owner_list_headers,
        )
        assert allowed.status_code == 200
        assert allowed.json()["total"] == 1


def test_runtime_queue_snapshot_exposes_dead_letter_count(client: TestClient) -> None:
    async def seed() -> None:
        project = await client.app.state.store.create_project(
            ProjectRecord(
                title="死信队列统计",
                topic="队列快照",
                language="zh-CN",
                target_duration_seconds=60,
                aspect_ratio="9:16",
                tone="清晰",
            )
        )
        task = _failed_task(project.id)
        open_dead_letter(
            task.input_data,
            error_code="VIDEO_PROVIDER_TIMEOUT",
            error_message="队列统计测试",
            auto_retry_count=2,
            max_auto_retries=2,
        )
        await client.app.state.store.create_task(task)

    asyncio.run(seed())
    response = client.get("/api/v1/system/queue")
    assert response.status_code == 200
    assert response.json()["counts"]["dead_letter"] == 1
