from __future__ import annotations

import asyncio
from uuid import uuid4

from app.domain.models import (
    GenerationTaskKind,
    GenerationTaskRecord,
    StageName,
    StageRun,
    TaskStatus,
)
from app.queue import InProcessTaskQueue
from app.repositories.in_memory import InMemoryStore
from app.services.task_service import TaskService


def test_retrying_video_task_increments_generation_attempt() -> None:
    async def exercise() -> None:
        store = InMemoryStore()
        queue = InProcessTaskQueue()
        enqueued: list = []

        async def handler(task_id) -> None:
            enqueued.append(task_id)

        queue.set_handler(handler)
        task = GenerationTaskRecord(
            project_id=uuid4(),
            kind=GenerationTaskKind.VIDEO_CLIP,
            input_data={"generation_attempt": 1},
            status=TaskStatus.FAILED,
            current_stage=StageName.VIDEO_CLIP,
            stages=[
                StageRun(
                    stage=StageName.VIDEO_CLIP,
                    status=TaskStatus.FAILED,
                    attempt=1,
                )
            ],
        )
        await store.create_task(task)
        service = TaskService(store, None, queue)  # type: ignore[arg-type]

        retried = await service.retry_task(task.id)
        await queue.close()

        assert retried.input_data["generation_attempt"] == 2
        assert retried.stages[0].attempt == 2
        assert retried.status == TaskStatus.QUEUED
        assert enqueued == [task.id]

    asyncio.run(exercise())
