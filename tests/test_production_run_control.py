from __future__ import annotations

import asyncio
from datetime import timedelta
from uuid import UUID, uuid4

from app.db import create_engine, create_session_factory, init_db
from app.domain.models import (
    GenerationTaskKind,
    GenerationTaskRecord,
    StageName,
    StageRun,
    TaskError,
    TaskStatus,
    utc_now,
)
from app.repositories.in_memory import InMemoryStore
from app.repositories.sqlalchemy import SqlAlchemyStore
from app.services.production_orchestrator import ProductionOrchestrator
from app.services.task_service import TaskNotRetryableError, TaskService


class RecordingQueue:
    def __init__(self) -> None:
        self.enqueued: list[UUID] = []

    async def enqueue(self, task_id: UUID) -> None:
        self.enqueued.append(task_id)

    async def close(self) -> None:
        return None

    async def ack(self, task_id: UUID) -> None:
        del task_id

    async def heartbeat(self, payload: dict[str, object]) -> None:
        del payload

    async def worker_status(self) -> dict[str, object] | None:
        return None


def _task(
    project_id: UUID,
    run_id: UUID,
    *,
    status: TaskStatus,
    run_status: str = "active",
    kind: GenerationTaskKind = GenerationTaskKind.NOVEL_EPISODE_SCRIPT,
    auto_retry_pending: bool = False,
    next_retry_at: str | None = None,
) -> GenerationTaskRecord:
    stage = StageName.EPISODE_SCRIPT
    if kind == GenerationTaskKind.VIDEO_CLIP:
        stage = StageName.VIDEO_CLIP
    if kind == GenerationTaskKind.NOVEL_STORY_BIBLE:
        stage = StageName.STORY_BIBLE
    input_data: dict[str, object] = {
        "auto_run_id": str(run_id),
        "auto_advance": run_status not in {"paused", "canceled", "completed", "failed"},
        "auto_run_status": run_status,
        "auto_run_control_revision": 0,
    }
    if auto_retry_pending:
        input_data["auto_retry_pending"] = True
    if next_retry_at is not None:
        input_data["next_retry_at"] = next_retry_at
    return GenerationTaskRecord(
        project_id=project_id,
        kind=kind,
        input_data=input_data,
        status=status,
        current_stage=stage,
        progress=100 if status == TaskStatus.SUCCEEDED else 0,
        error=(
            TaskError(code="PROVIDER_TIMEOUT", message="retryable")
            if status == TaskStatus.FAILED
            else None
        ),
        stages=[
            StageRun(
                stage=stage,
                status=status,
                progress=100 if status == TaskStatus.SUCCEEDED else 0,
            )
        ],
    )


def test_pause_blocks_dag_advancement_and_queued_provider_execution() -> None:
    async def exercise() -> None:
        store = InMemoryStore()
        queue = RecordingQueue()
        project_id = uuid4()
        run_id = uuid4()
        running = _task(project_id, run_id, status=TaskStatus.RUNNING)
        queued = _task(project_id, run_id, status=TaskStatus.QUEUED)
        await store.create_task(running)
        await store.create_task(queued)

        orchestrator = ProductionOrchestrator(store, object(), task_queue=queue)  # type: ignore[arg-type]
        paused = await orchestrator.pause_run(project_id, run_id)

        assert paused.status == "paused"
        assert paused.auto_advance is False
        saved_queued = await store.get_task(queued.id)
        assert saved_queued is not None
        assert saved_queued.status == TaskStatus.QUEUED
        assert saved_queued.input_data["auto_run_status"] == "paused"

        started: list[UUID] = []

        async def video_runner(task_id: UUID) -> None:
            started.append(task_id)

        service = TaskService(
            store,
            None,  # type: ignore[arg-type]
            queue,  # type: ignore[arg-type]
            video_clip_task_runner=video_runner,
        )
        await service.run_task(queued.id)
        assert started == []

        # The task may finish naturally after the pause, but it cannot advance
        # the next DAG wave because the durable control marker is disabled.
        saved_running = await store.get_task(running.id)
        assert saved_running is not None
        saved_running.status = TaskStatus.SUCCEEDED
        saved_running.progress = 100
        saved_running.current_stage = None
        saved_running.stages[0].status = TaskStatus.SUCCEEDED
        await store.update_task(saved_running)
        assert await orchestrator.on_task_finished(running.id) is None

    asyncio.run(exercise())


def test_resume_requeues_queued_work_and_due_auto_retry_once() -> None:
    async def exercise() -> None:
        store = InMemoryStore()
        queue = RecordingQueue()
        project_id = uuid4()
        run_id = uuid4()
        queued = _task(project_id, run_id, status=TaskStatus.QUEUED, run_status="paused")
        due_failed = _task(
            project_id,
            run_id,
            status=TaskStatus.FAILED,
            run_status="paused",
            auto_retry_pending=True,
            next_retry_at=(utc_now() - timedelta(seconds=1)).isoformat(),
        )
        await store.create_task(queued)
        await store.create_task(due_failed)

        task_service = TaskService(store, None, queue)  # type: ignore[arg-type]
        orchestrator = ProductionOrchestrator(
            store,
            object(),  # type: ignore[arg-type]
            task_queue=queue,  # type: ignore[arg-type]
            retry_task=task_service.retry_task,
        )
        resumed = await orchestrator.resume_run(project_id, run_id)

        assert resumed.status == "active"
        assert set(queue.enqueued) == {queued.id, due_failed.id}
        assert len(queue.enqueued) == 2
        saved_queued = await store.get_task(queued.id)
        saved_retry = await store.get_task(due_failed.id)
        assert saved_queued is not None and saved_queued.input_data["auto_run_status"] == "active"
        assert saved_retry is not None
        assert saved_retry.status == TaskStatus.QUEUED
        assert saved_retry.stages[0].attempt == 2
        assert saved_retry.input_data["auto_retry_pending"] is False

    asyncio.run(exercise())


def test_cancel_preserves_running_and_completed_work_and_rejects_retry() -> None:
    async def exercise() -> None:
        store = InMemoryStore()
        queue = RecordingQueue()
        project_id = uuid4()
        run_id = uuid4()
        running = _task(project_id, run_id, status=TaskStatus.RUNNING)
        queued = _task(project_id, run_id, status=TaskStatus.QUEUED)
        failed = _task(
            project_id,
            run_id,
            status=TaskStatus.FAILED,
            auto_retry_pending=True,
            next_retry_at=(utc_now() + timedelta(hours=1)).isoformat(),
        )
        completed = _task(project_id, run_id, status=TaskStatus.SUCCEEDED)
        await store.create_task(running)
        await store.create_task(queued)
        await store.create_task(failed)
        await store.create_task(completed)
        stale_running = await store.get_task(running.id)
        assert stale_running is not None

        orchestrator = ProductionOrchestrator(store, object(), task_queue=queue)  # type: ignore[arg-type]
        canceled = await orchestrator.cancel_run(project_id, run_id)

        assert canceled.status == "canceled"
        assert canceled.auto_advance is False
        saved_running = await store.get_task(running.id)
        saved_queued = await store.get_task(queued.id)
        saved_failed = await store.get_task(failed.id)
        saved_completed = await store.get_task(completed.id)
        assert saved_running is not None and saved_running.status == TaskStatus.RUNNING
        assert saved_queued is not None and saved_queued.status == TaskStatus.CANCELED
        assert saved_failed is not None
        assert saved_failed.status == TaskStatus.FAILED
        assert saved_failed.input_data["auto_retry_pending"] is False
        assert saved_completed is not None and saved_completed.status == TaskStatus.SUCCEEDED

        # Simulate a Worker that held an old snapshot while cancellation landed.
        stale_running.input_data.update(
            {
                "auto_run_status": "active",
                "auto_advance": True,
                "auto_run_control_revision": 0,
                "auto_retry_pending": True,
                "next_retry_at": "2020-01-01T00:00:00+00:00",
            }
        )
        await store.update_task(stale_running)
        merged = await store.get_task(running.id)
        assert merged is not None
        assert merged.input_data["auto_run_status"] == "canceled"
        assert merged.input_data["auto_advance"] is False
        assert merged.input_data["auto_retry_pending"] is False
        assert "next_retry_at" not in merged.input_data

        task_service = TaskService(store, None, queue)  # type: ignore[arg-type]
        with_error = False
        try:
            await task_service.retry_task(failed.id)
        except TaskNotRetryableError:
            with_error = True
        assert with_error

    asyncio.run(exercise())


def test_sqlalchemy_store_preserves_newer_run_control_marker() -> None:
    async def exercise() -> None:
        engine = create_engine("sqlite+aiosqlite:///:memory:")
        await init_db(engine)
        store = SqlAlchemyStore(create_session_factory(engine))
        project_id = uuid4()
        run_id = uuid4()
        task = _task(project_id, run_id, status=TaskStatus.RUNNING)
        await store.create_task(task)
        controlled = await store.get_task(task.id)
        assert controlled is not None
        controlled.input_data.update(
            {
                "auto_run_status": "canceled",
                "auto_advance": False,
                "auto_run_control_revision": 1,
            }
        )
        await store.update_task(controlled)

        stale = await store.get_task(task.id)
        assert stale is not None
        stale.input_data.update(
            {
                "auto_run_status": "active",
                "auto_advance": True,
                "auto_run_control_revision": 0,
            }
        )
        await store.update_task(stale)
        loaded = await store.get_task(task.id)
        assert loaded is not None
        assert loaded.input_data["auto_run_status"] == "canceled"
        assert loaded.input_data["auto_advance"] is False
        assert loaded.input_data["auto_run_control_revision"] == 1
        await engine.dispose()

    asyncio.run(exercise())


def test_production_run_control_api_is_idempotent(client, monkeypatch) -> None:
    project = client.post(
        "/api/v1/novel-projects",
        json={"title": "Run 控制 API", "target_episode_count": 1},
    )
    assert project.status_code == 201, project.text
    project_id = UUID(project.json()["id"])
    run_id = uuid4()

    async def no_enqueue(task_id: UUID) -> None:
        del task_id

    # The API contract test only exercises durable lifecycle transitions. Keep
    # the in-process test queue from starting a real novel Provider task after
    # the resume request changes the marker back to active.
    monkeypatch.setattr(client.app.state.task_queue, "enqueue", no_enqueue)

    async def seed() -> None:
        task = _task(project_id, run_id, status=TaskStatus.QUEUED)
        await client.app.state.store.create_task(task)

    asyncio.run(seed())

    base = f"/api/v1/novel-projects/{project_id}/production-runs/{run_id}"
    paused = client.post(f"{base}/pause")
    assert paused.status_code == 202, paused.text
    assert paused.json()["status"] == "paused"
    paused_again = client.post(f"{base}/pause")
    assert paused_again.status_code == 202, paused_again.text
    assert paused_again.json()["status"] == "paused"

    resumed = client.post(f"{base}/resume")
    assert resumed.status_code == 202, resumed.text
    assert resumed.json()["status"] == "active"
    canceled = client.post(f"{base}/cancel")
    assert canceled.status_code == 202, canceled.text
    assert canceled.json()["status"] == "canceled"
    canceled_again = client.post(f"{base}/cancel")
    assert canceled_again.status_code == 202, canceled_again.text
    assert canceled_again.json()["status"] == "canceled"

    cannot_resume = client.post(f"{base}/resume")
    assert cannot_resume.status_code == 409, cannot_resume.text
    assert cannot_resume.json()["error"]["code"] == "PRODUCTION_RUN_CANCELED"
    cannot_pause = client.post(f"{base}/pause")
    assert cannot_pause.status_code == 409, cannot_pause.text
    assert cannot_pause.json()["error"]["code"] == "PRODUCTION_RUN_TERMINAL"
