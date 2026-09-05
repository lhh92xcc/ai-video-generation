"""Task queue adapters for local development and Redis Worker execution."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Protocol
from uuid import UUID

from redis.asyncio import Redis


class TaskQueue(Protocol):
    async def enqueue(self, task_id: UUID) -> None:
        ...

    async def close(self) -> None:
        ...


class InProcessTaskQueue:
    """Run a task in the API process for tests and dependency-free local work."""

    def __init__(self) -> None:
        self._handler: Callable[[UUID], Awaitable[None]] | None = None
        self._running_tasks: set[asyncio.Task[None]] = set()

    def set_handler(self, handler: Callable[[UUID], Awaitable[None]]) -> None:
        self._handler = handler

    async def enqueue(self, task_id: UUID) -> None:
        if self._handler is None:
            raise RuntimeError("InProcessTaskQueue handler has not been configured")
        running_task = asyncio.create_task(self._handler(task_id))
        self._running_tasks.add(running_task)
        running_task.add_done_callback(self._running_tasks.discard)

    async def close(self) -> None:
        if self._running_tasks:
            await asyncio.gather(*self._running_tasks, return_exceptions=True)


class RedisTaskQueue:
    def __init__(self, redis_url: str, queue_name: str) -> None:
        self.queue_name = queue_name
        self._redis: Redis = Redis.from_url(redis_url, decode_responses=True)

    async def enqueue(self, task_id: UUID) -> None:
        await self._redis.rpush(self.queue_name, str(task_id))

    async def dequeue(self, timeout_seconds: int = 5) -> UUID | None:
        item = await self._redis.blpop(self.queue_name, timeout=timeout_seconds)
        if item is None:
            return None
        _, raw_task_id = item
        return UUID(raw_task_id)

    async def close(self) -> None:
        await self._redis.aclose()
