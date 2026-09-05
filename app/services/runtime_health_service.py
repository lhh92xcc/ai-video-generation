"""Read-only operational health checks for the remote production console."""

from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from typing import Any

import httpx

from app.config import Settings
from app.domain.models import (
    OperationalComponentHealth,
    OperationalHealthResponse,
    ProductionQueueSnapshot,
    TaskStatus,
)
from app.queue import RedisTaskQueue
from app.repositories.protocol import ProjectTaskStore


class RuntimeHealthService:
    def __init__(
        self,
        settings: Settings,
        queue: object,
        store: ProjectTaskStore | None = None,
    ) -> None:
        self.settings = settings
        self.queue = queue
        self.store = store

    async def check(self) -> OperationalHealthResponse:
        components: list[OperationalComponentHealth] = []
        components.append(await self._check_redis())
        components.append(await self._check_http_provider(
            "ollama",
            self._ollama_url(),
            configured=self.settings.llm_provider == "ollama",
        ))
        components.append(await self._check_http_provider(
            "comfyui",
            self._comfyui_url(),
            configured=self.settings.image_provider == "comfyui"
            or self.settings.video_provider == "comfyui_wan_i2v",
            path="/system_stats",
        ))
        components.append(await self._check_http_provider(
            "musetalk",
            self._build_url(self.settings.lip_sync_base_url, self.settings.lip_sync_health_path),
            configured=self.settings.lip_sync_provider in {"musetalk_http", "http"},
        ))

        disk = shutil.disk_usage(Path.cwd())
        free_gb = disk.free / 1024**3
        worker = await self._worker_status()
        lock_busy = await self._lock_busy()
        degraded = any(
            item.status in {"degraded", "unavailable"}
            for item in components
        ) or (
            self.settings.queue_backend == "redis"
            and worker.get("status") not in {"online", "starting"}
        )
        return OperationalHealthResponse(
            status="degraded" if degraded else "ok",
            profile=self.settings.runtime_profile,
            checked_at=datetime.now(timezone.utc),
            disk_free_gb=round(max(0.0, free_gb), 2),
            gpu_lock_enabled=self.settings.worker_gpu_lock_enabled,
            gpu_lock_busy=lock_busy,
            worker=worker,
            components=components,
        )

    async def queue_snapshot(
        self,
        limit: int = 100,
        project_ids: set | None = None,
    ) -> ProductionQueueSnapshot:
        """Return the data needed by the remote production queue console."""

        health = await self.check()
        tasks = []
        if self.store is not None:
            tasks = await self.store.list_tasks(limit=5000)
        if project_ids is not None:
            tasks = [task for task in tasks if task.project_id in project_ids]
        tasks.sort(key=lambda item: item.updated_at, reverse=True)

        counts = {status.value: 0 for status in TaskStatus}
        for task in tasks:
            counts[task.status.value] = counts.get(task.status.value, 0) + 1
        counts["total"] = len(tasks)

        runs: dict[str, list] = {}
        for task in tasks:
            raw_run_id = task.input_data.get("auto_run_id")
            if raw_run_id:
                runs.setdefault(str(raw_run_id), []).append(task)

        auto_runs: list[dict[str, object]] = []
        for run_id, run_tasks in runs.items():
            active = sum(
                task.status in {TaskStatus.CREATED, TaskStatus.QUEUED, TaskStatus.RUNNING}
                for task in run_tasks
            )
            succeeded = sum(task.status == TaskStatus.SUCCEEDED for task in run_tasks)
            failed = sum(task.status == TaskStatus.FAILED for task in run_tasks)
            progress = round(sum(task.progress for task in run_tasks) / len(run_tasks))
            marker_status = next(
                (
                    str(task.input_data.get("auto_run_status"))
                    for task in run_tasks
                    if task.input_data.get("auto_run_status")
                ),
                "active",
            )
            if failed and marker_status == "active":
                marker_status = "blocked"
            episode_ids = sorted(
                {
                    str(task.input_data["episode_id"])
                    for task in run_tasks
                    if task.input_data.get("episode_id")
                }
            )
            latest = max(task.updated_at for task in run_tasks)
            auto_runs.append(
                {
                    "id": run_id,
                    "status": marker_status,
                    "task_count": len(run_tasks),
                    "active_count": active,
                    "succeeded_count": succeeded,
                    "failed_count": failed,
                    "progress": progress,
                    "episode_ids": episode_ids,
                    "updated_at": latest.isoformat(),
                }
            )
        auto_runs.sort(key=lambda item: str(item.get("updated_at", "")), reverse=True)

        return ProductionQueueSnapshot(
            profile=health.profile,
            refreshed_at=health.checked_at,
            counts=counts,
            gpu_lock_enabled=health.gpu_lock_enabled,
            gpu_lock_busy=health.gpu_lock_busy,
            worker=health.worker,
            auto_runs=auto_runs,
            tasks=tasks[: max(1, min(200, limit))],
        )

    async def _check_redis(self) -> OperationalComponentHealth:
        if not isinstance(self.queue, RedisTaskQueue):
            return OperationalComponentHealth(
                name="redis",
                status="not_configured",
                message="当前 API 使用进程内队列",
            )
        started = monotonic()
        try:
            await self.queue.ping()
        except Exception as exc:  # pragma: no cover - depends on external runtime
            return OperationalComponentHealth(
                name="redis",
                status="unavailable",
                message=f"Redis 不可用：{str(exc)[:160]}",
                latency_ms=round((monotonic() - started) * 1000),
            )
        return OperationalComponentHealth(
            name="redis",
            status="ok",
            message="队列连接正常",
            latency_ms=round((monotonic() - started) * 1000),
        )

    async def _check_http_provider(
        self,
        name: str,
        url: str,
        *,
        configured: bool,
        path: str | None = None,
    ) -> OperationalComponentHealth:
        if not configured:
            return OperationalComponentHealth(
                name=name,
                status="not_configured",
                message="当前运行档案未启用",
            )
        if not url:
            return OperationalComponentHealth(
                name=name,
                status="unavailable",
                message="服务地址未配置",
            )
        endpoint = self._build_url(url, path) if path else url
        started = monotonic()
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                response = await client.get(endpoint)
                response.raise_for_status()
        except httpx.TimeoutException:
            return OperationalComponentHealth(
                name=name,
                status="unavailable",
                message="连接超时",
                latency_ms=round((monotonic() - started) * 1000),
            )
        except httpx.HTTPStatusError as exc:
            return OperationalComponentHealth(
                name=name,
                status="degraded",
                message=f"HTTP {exc.response.status_code}",
                latency_ms=round((monotonic() - started) * 1000),
            )
        except httpx.HTTPError as exc:
            return OperationalComponentHealth(
                name=name,
                status="unavailable",
                message=f"连接失败：{str(exc)[:120]}",
                latency_ms=round((monotonic() - started) * 1000),
            )
        return OperationalComponentHealth(
            name=name,
            status="ok",
            message="服务响应正常",
            latency_ms=round((monotonic() - started) * 1000),
        )

    async def _worker_status(self) -> dict[str, Any]:
        if not isinstance(self.queue, RedisTaskQueue):
            return {"status": "in_process", "message": "进程内任务执行器"}
        try:
            payload = await self.queue.worker_status()
            if not payload:
                return {"status": "offline", "message": "尚未收到 Worker 心跳"}
            result: dict[str, Any] = dict(payload)
            updated_at = result.get("updated_at")
            if isinstance(updated_at, str):
                try:
                    age = (
                        datetime.now(timezone.utc)
                        - datetime.fromisoformat(updated_at)
                    ).total_seconds()
                    result["age_seconds"] = round(max(0.0, age), 1)
                    if age > 90:
                        result["status"] = "offline"
                except ValueError:
                    pass
            result.setdefault("status", "online")
            result["pending_count"] = await self.queue.pending_count()
            result["processing_count"] = await self.queue.processing_count()
            return result
        except Exception as exc:  # pragma: no cover - depends on external runtime
            return {"status": "unavailable", "message": str(exc)[:160]}

    async def _lock_busy(self) -> bool:
        if not self.settings.worker_gpu_lock_enabled:
            return False
        if not isinstance(self.queue, RedisTaskQueue):
            return False
        try:
            return await self.queue.lock_busy(self.settings.worker_gpu_lock_key)
        except Exception:  # pragma: no cover - depends on external runtime
            return False

    def _ollama_url(self) -> str:
        base = self.settings.llm_base_url.rstrip("/")
        if base.endswith("/v1"):
            base = base[:-3]
        return f"{base}/api/tags"

    def _comfyui_url(self) -> str:
        base = self.settings.image_base_url or self.settings.video_base_url
        return base

    @staticmethod
    def _build_url(base: str, path: str | None) -> str:
        if not base:
            return ""
        if not path:
            return base
        return f"{base.rstrip('/')}/{path.lstrip('/')}"
