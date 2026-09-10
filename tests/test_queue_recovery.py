from __future__ import annotations

import asyncio
from collections import defaultdict
from uuid import UUID, uuid4

from app.domain.models import (
    GenerationTaskKind,
    GenerationTaskRecord,
    StageName,
    StageRun,
    TaskStatus,
)
from app.queue import RedisTaskQueue
from app.repositories.in_memory import InMemoryStore
from app.workers.generation_worker import _requeue_queued_tasks


class FakeRedis:
    """Small in-memory Redis subset for queue restart/reconciliation tests."""

    def __init__(self) -> None:
        self.lists: dict[str, list[str]] = defaultdict(list)
        self.sets: dict[str, set[str]] = defaultdict(set)
        self.hashes: dict[str, dict[str, str]] = defaultdict(dict)

    async def eval(self, script: str, _numkeys: int, *args: str) -> int:
        if "sadd" not in script or "lpush" not in script:
            raise AssertionError("unexpected Redis script in queue test")
        queue_name, marker_name, task_id = args
        if task_id in self.sets[marker_name]:
            return 0
        self.sets[marker_name].add(task_id)
        self.lists[queue_name].insert(0, task_id)
        return 1

    async def hgetall(self, name: str) -> dict[str, str]:
        return dict(self.hashes[name])

    async def hdel(self, name: str, *keys: str) -> int:
        removed = 0
        for key in keys:
            if key in self.hashes[name]:
                del self.hashes[name][key]
                removed += 1
        return removed

    async def lrange(self, name: str, start: int, end: int) -> list[str]:
        values = self.lists[name]
        normalized_end = len(values) - 1 if end == -1 else end
        return list(values[start : normalized_end + 1])

    async def lrem(self, name: str, count: int, value: str) -> int:
        values = self.lists[name]
        if count == 0:
            kept = [item for item in values if item != value]
        elif count > 0:
            removed = 0
            kept = []
            for item in values:
                if item == value and removed < count:
                    removed += 1
                    continue
                kept.append(item)
        else:  # pragma: no cover - queue code only uses 0 and positive counts
            removed = 0
            kept = []
            for item in reversed(values):
                if item == value and removed < abs(count):
                    removed += 1
                    continue
                kept.append(item)
            kept.reverse()
        removed_count = len(values) - len(kept)
        self.lists[name] = kept
        return removed_count

    async def srem(self, name: str, *members: str) -> int:
        removed = 0
        for member in members:
            if member in self.sets[name]:
                self.sets[name].remove(member)
                removed += 1
        return removed

    async def sadd(self, name: str, *members: str) -> int:
        before = len(self.sets[name])
        self.sets[name].update(members)
        return len(self.sets[name]) - before

    async def smembers(self, name: str) -> set[str]:
        return set(self.sets[name])


def make_queue(redis: FakeRedis) -> RedisTaskQueue:
    queue = object.__new__(RedisTaskQueue)
    queue.queue_name = "test:queue"
    queue.processing_name = "test:queue:processing"
    queue.enqueued_name = "test:queue:enqueued"
    queue.claims_name = "test:queue:claims"
    queue.worker_id = "test-worker"
    queue._redis = redis
    return queue


def test_recover_expired_removes_all_duplicate_processing_entries_and_requeues_once() -> None:
    async def exercise() -> None:
        redis = FakeRedis()
        queue = make_queue(redis)
        task_id = uuid4()
        task_key = str(task_id)
        redis.lists[queue.processing_name] = [task_key, task_key, task_key]
        redis.hashes[queue.claims_name][task_key] = "0|stale-worker"
        redis.sets[queue.enqueued_name].add(task_key)

        recovered = await queue.recover_expired(1, requeue=True)

        assert recovered == [task_id]
        assert redis.lists[queue.processing_name] == []
        assert redis.lists[queue.queue_name] == [task_key]
        assert redis.hashes[queue.claims_name] == {}
        assert redis.sets[queue.enqueued_name] == {task_key}

    asyncio.run(exercise())


def test_reconcile_repairs_markers_and_reenqueues_missing_runnable_task() -> None:
    async def exercise() -> None:
        redis = FakeRedis()
        queue = make_queue(redis)
        queued_id = uuid4()
        processing_id = uuid4()
        stale_id = uuid4()
        runnable_id = uuid4()
        redis.lists[queue.queue_name] = [str(queued_id)]
        redis.lists[queue.processing_name] = [str(processing_id)]
        redis.sets[queue.enqueued_name] = {str(queued_id), str(stale_id)}

        result = await queue.reconcile_enqueued_markers([runnable_id])

        assert result == {"removed_markers": 1, "repaired_markers": 1, "enqueued": 1}
        assert redis.sets[queue.enqueued_name] == {
            str(queued_id),
            str(processing_id),
            str(runnable_id),
        }
        assert redis.lists[queue.queue_name] == [str(runnable_id), str(queued_id)]

    asyncio.run(exercise())


def test_reconcile_removes_orphan_marker_before_requeueing_database_task() -> None:
    async def exercise() -> None:
        redis = FakeRedis()
        queue = make_queue(redis)
        runnable_id = UUID("11111111-1111-1111-1111-111111111111")
        redis.sets[queue.enqueued_name].add(str(runnable_id))

        result = await queue.reconcile_enqueued_markers([runnable_id])

        assert result["removed_markers"] == 1
        assert result["enqueued"] == 1
        assert redis.lists[queue.queue_name] == [str(runnable_id)]
        assert redis.sets[queue.enqueued_name] == {str(runnable_id)}

    asyncio.run(exercise())


def test_worker_restart_requeues_topic_run_task_from_durable_marker() -> None:
    async def exercise() -> None:
        store = InMemoryStore()
        redis = FakeRedis()
        queue = make_queue(redis)
        project_id = uuid4()
        active_task = GenerationTaskRecord(
            project_id=project_id,
            kind=GenerationTaskKind.VIDEO_CLIP,
            input_data={
                "topic_pipeline": True,
                "topic_run_id": str(uuid4()),
                "topic_run_status": "active",
                "topic_run_control_revision": 0,
            },
            status=TaskStatus.QUEUED,
            current_stage=StageName.VIDEO_CLIP,
            stages=[StageRun(stage=StageName.VIDEO_CLIP, status=TaskStatus.QUEUED)],
        )
        await store.create_task(active_task)

        # Topic tasks use their own marker namespace; startup recovery should
        # still recognize them as runnable durable work.
        requeued = await _requeue_queued_tasks(store, queue)

        assert requeued == 1
        assert redis.lists[queue.queue_name] == [str(active_task.id)]

    asyncio.run(exercise())
