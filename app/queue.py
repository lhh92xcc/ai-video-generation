"""Task queue adapters for local development and Redis Worker execution."""

from __future__ import annotations

import asyncio
import os
import socket
from contextlib import asynccontextmanager
from collections.abc import Awaitable, Callable, Iterable
from datetime import datetime, timezone
from typing import Protocol
from uuid import UUID, uuid4

from redis.asyncio import Redis


class TaskQueue(Protocol):
    async def enqueue(self, task_id: UUID) -> None:
        ...

    async def close(self) -> None:
        ...

    async def ack(self, task_id: UUID) -> None:
        ...

    async def heartbeat(self, payload: dict[str, object]) -> None:
        ...

    async def worker_status(self) -> dict[str, object] | None:
        ...


class InProcessTaskQueue:
    """Run a task in the API process for tests and dependency-free local work."""

    def __init__(self) -> None:
        self._handler: Callable[[UUID], Awaitable[None]] | None = None
        self._running_tasks: set[asyncio.Task[None]] = set()
        self._enqueued_task_ids: set[UUID] = set()

    def set_handler(self, handler: Callable[[UUID], Awaitable[None]]) -> None:
        self._handler = handler

    async def enqueue(self, task_id: UUID) -> None:
        if self._handler is None:
            raise RuntimeError("InProcessTaskQueue handler has not been configured")
        if task_id in self._enqueued_task_ids:
            return
        self._enqueued_task_ids.add(task_id)
        running_task = asyncio.create_task(self._handler(task_id))
        self._running_tasks.add(running_task)
        running_task.add_done_callback(self._running_tasks.discard)
        running_task.add_done_callback(lambda _task: self._enqueued_task_ids.discard(task_id))

    async def close(self) -> None:
        if self._running_tasks:
            await asyncio.gather(*self._running_tasks, return_exceptions=True)

    async def ack(self, task_id: UUID) -> None:
        return None

    async def heartbeat(self, payload: dict[str, object]) -> None:
        return None

    async def worker_status(self) -> dict[str, object] | None:
        return None


class RedisTaskQueue:
    def __init__(self, redis_url: str, queue_name: str, worker_id: str | None = None) -> None:
        self.queue_name = queue_name
        self._redis: Redis = Redis.from_url(redis_url, decode_responses=True)
        self.processing_name = f"{queue_name}:processing"
        self.enqueued_name = f"{queue_name}:enqueued"
        self.claims_name = f"{queue_name}:claims"
        self.heartbeat_name = f"{queue_name}:worker:heartbeat"
        self.worker_id = worker_id or f"{socket.gethostname()}-{os.getpid()}-{uuid4().hex[:8]}"

    _ENQUEUE_SCRIPT = """
    if redis.call('sadd', KEYS[2], ARGV[1]) == 1 then
        return redis.call('lpush', KEYS[1], ARGV[1])
    end
    return 0
    """

    async def enqueue(self, task_id: UUID) -> None:
        # LPUSH + BRPOPLPUSH preserves FIFO order while allowing a claimed
        # message to remain recoverable until the Worker ACKs it.  The set is
        # a durable in-flight marker: API retries, Worker startup
        # reconciliation and DAG races therefore cannot enqueue the same task
        # more than once.
        await self._redis.eval(
            self._ENQUEUE_SCRIPT,
            2,
            self.queue_name,
            self.enqueued_name,
            str(task_id),
        )

    async def dequeue(self, timeout_seconds: int = 5) -> UUID | None:
        # A pre-existing queue (created before the dedupe set was introduced)
        # may contain duplicate list entries.  HSETNX makes the claim atomic
        # from the point of view of multiple Workers; duplicate entries are
        # discarded while the first claim remains recoverable.
        while True:
            raw_task_id = await self._redis.brpoplpush(
                self.queue_name,
                self.processing_name,
                timeout=timeout_seconds,
            )
            if raw_task_id is None:
                return None
            task_key = str(raw_task_id)
            claimed = await self._redis.hsetnx(
                self.claims_name,
                task_key,
                _timestamp_payload(self.worker_id),
            )
            if claimed:
                return UUID(task_key)
            await self._redis.lrem(self.processing_name, 1, task_key)

    async def ack(self, task_id: UUID) -> None:
        task_key = str(task_id)
        # Remove every stale duplicate left by an older Worker version.  The
        # task is considered runnable again only after this ACK, so the marker
        # is removed together with all processing entries.
        await self._redis.lrem(self.processing_name, 0, task_key)
        await self._redis.lrem(self.queue_name, 0, task_key)
        await self._redis.hdel(self.claims_name, task_key)
        await self._redis.srem(self.enqueued_name, task_key)

    async def recover_expired(
        self,
        max_age_seconds: int,
        *,
        requeue: bool = True,
    ) -> list[UUID]:
        """Release claims that outlived their worker heartbeat/lease.

        ``requeue=False`` is used during Worker startup.  The Worker first
        reconciles the durable database state and then decides whether a
        recovered task should be retried or merely put back in the queue.  It
        prevents a stale list entry from racing the database transition to
        ``failed``.
        """

        now = datetime.now(timezone.utc).timestamp()
        recovered: list[UUID] = []
        claims = await self._redis.hgetall(self.claims_name)
        for raw_task_id, raw_claim in claims.items():
            try:
                claimed_at = float(str(raw_claim).split("|", 1)[0])
            except (TypeError, ValueError):
                claimed_at = 0
            if now - claimed_at < max(1, max_age_seconds):
                continue
            # Remove every duplicate processing entry. Older Worker versions
            # could leave more than one copy after a retry race.
            removed = await self._redis.lrem(self.processing_name, 0, raw_task_id)
            await self._redis.hdel(self.claims_name, raw_task_id)
            if removed:
                await self._redis.srem(self.enqueued_name, raw_task_id)
                await self._redis.lrem(self.queue_name, 0, raw_task_id)
                if requeue:
                    await self.enqueue(UUID(raw_task_id))
                recovered.append(UUID(raw_task_id))
        return recovered

    async def reconcile_enqueued_markers(
        self,
        runnable_task_ids: Iterable[UUID] = (),
    ) -> dict[str, int]:
        """Repair enqueue markers after a Worker/API restart.

        The marker set survives a process crash. If a crash happens after
        ``SADD`` but before ``LPUSH``, a queued database task would otherwise
        be considered already enqueued forever. Older list entries without a
        marker are repaired as well; task ownership remains in the claim hash.
        """

        markers = {str(value) for value in await self._redis.smembers(self.enqueued_name)}
        queue_entries = {
            str(value) for value in await self._redis.lrange(self.queue_name, 0, -1)
        }
        processing_entries = {
            str(value)
            for value in await self._redis.lrange(self.processing_name, 0, -1)
        }
        live_entries = queue_entries | processing_entries

        stale_markers = markers - live_entries
        if stale_markers:
            await self._redis.srem(self.enqueued_name, *stale_markers)

        missing_markers = live_entries - markers
        if missing_markers:
            await self._redis.sadd(self.enqueued_name, *missing_markers)

        enqueued = 0
        for task_id in dict.fromkeys(runnable_task_ids):
            task_key = str(task_id)
            if task_key in live_entries:
                continue
            # Remove a stale marker before asking the atomic enqueue script to
            # add the task. This is safe against concurrent reconciliation.
            await self._redis.srem(self.enqueued_name, task_key)
            await self.enqueue(task_id)
            live_entries.add(task_key)
            enqueued += 1

        return {
            "removed_markers": len(stale_markers),
            "repaired_markers": len(missing_markers),
            "enqueued": enqueued,
        }

    async def heartbeat(self, payload: dict[str, object]) -> None:
        data = {
            **payload,
            "worker_id": self.worker_id,
            "queue_name": self.queue_name,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        await self._redis.hset(
            self.heartbeat_name,
            mapping={key: str(value) for key, value in data.items()},
        )
        await self._redis.expire(self.heartbeat_name, 90)

    async def worker_status(self) -> dict[str, object] | None:
        payload = await self._redis.hgetall(self.heartbeat_name)
        if not payload:
            return None
        return payload

    async def pending_count(self) -> int:
        return int(await self._redis.llen(self.queue_name))

    async def processing_count(self) -> int:
        return int(await self._redis.llen(self.processing_name))

    async def ping(self) -> bool:
        return bool(await self._redis.ping())

    async def lock_busy(self, key: str) -> bool:
        return bool(await self._redis.exists(key))

    async def close(self) -> None:
        await self._redis.aclose()


def _timestamp_payload(worker_id: str) -> str:
    return f"{datetime.now(timezone.utc).timestamp()}|{worker_id}"


class RedisLeaseLock:
    """A single-owner Redis lease used to serialize GPU work."""

    _RELEASE_SCRIPT = """
    if redis.call('get', KEYS[1]) == ARGV[1] then
        return redis.call('del', KEYS[1])
    else
        return 0
    end
    """
    _RENEW_SCRIPT = """
    if redis.call('get', KEYS[1]) == ARGV[1] then
        return redis.call('expire', KEYS[1], ARGV[2])
    else
        return 0
    end
    """

    def __init__(
        self,
        redis_url: str,
        key: str,
        ttl_seconds: int = 7200,
        wait_seconds: int = 8,
    ) -> None:
        self.key = key
        self.ttl_seconds = max(1, ttl_seconds)
        self.wait_seconds = max(0, wait_seconds)
        self.token = f"{socket.gethostname()}-{os.getpid()}-{uuid4().hex}"
        self._redis: Redis = Redis.from_url(redis_url, decode_responses=True)
        self._renew_task: asyncio.Task[None] | None = None

    async def acquire(self) -> bool:
        deadline = asyncio.get_running_loop().time() + self.wait_seconds
        while True:
            acquired = await self._redis.set(
                self.key,
                self.token,
                nx=True,
                ex=self.ttl_seconds,
            )
            if acquired:
                self._renew_task = asyncio.create_task(self._renew_loop())
                return True
            if asyncio.get_running_loop().time() >= deadline:
                return False
            await asyncio.sleep(min(1.0, max(0.05, deadline - asyncio.get_running_loop().time())))

    async def release(self) -> None:
        if self._renew_task is not None:
            self._renew_task.cancel()
            await asyncio.gather(self._renew_task, return_exceptions=True)
            self._renew_task = None
        await self._redis.eval(self._RELEASE_SCRIPT, 1, self.key, self.token)

    async def close(self) -> None:
        await self.release()
        await self._redis.aclose()

    async def _renew_loop(self) -> None:
        interval = max(1.0, self.ttl_seconds / 3)
        try:
            while True:
                await asyncio.sleep(interval)
                renewed = await self._redis.eval(
                    self._RENEW_SCRIPT,
                    1,
                    self.key,
                    self.token,
                    str(self.ttl_seconds),
                )
                if not renewed:
                    return
        except asyncio.CancelledError:
            raise


@asynccontextmanager
async def maybe_gpu_lease(
    lock: RedisLeaseLock | None,
):
    if lock is None:
        yield True
        return
    acquired = await lock.acquire()
    try:
        yield acquired
    finally:
        if acquired:
            await lock.release()
