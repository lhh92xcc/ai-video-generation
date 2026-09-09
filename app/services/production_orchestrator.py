"""Durable, event-driven advancement of the novel production DAG.

The episode planner remains the single place that knows media dependencies.
This service owns the lifecycle around it: creating a run from an uploaded
novel, persisting the run marker on every task, advancing after completion and
reconciling the same state after an API/Worker restart.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from app.domain.models import (
    EpisodeTaskPlanCreateRequest,
    EpisodeTaskPlanResponse,
    GenerationTaskKind,
    GenerationTaskRecord,
    ProductionRunCreateRequest,
    ProductionRunResponse,
    StageRun,
    TaskError,
    TaskStatus,
    utc_now,
)
from app.domain.production_run import (
    AUTO_RUN_CONTROL_REVISION,
    AUTO_RUN_ENABLED,
    AUTO_RUN_ID,
    AUTO_RUN_PLAN,
    AUTO_RUN_STATUS,
    control_revision,
    run_status_from_tasks,
)
from app.queue import TaskQueue
from app.repositories.protocol import ProjectTaskStore
from app.services.episode_task_plan_service import EpisodeTaskPlanService
from app.services.novel_service import NovelProjectNotFoundError, NovelSourceNotFoundError


_RUN_STOPPED_STATUSES = {"paused", "canceled"}


class ProductionRunNotFoundError(Exception):
    """Raised when a requested auto-production Run has no task marker."""


class ProductionRunControlError(Exception):
    """Raised when a Run cannot perform the requested lifecycle transition."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


class ProductionOrchestrator:
    def __init__(
        self,
        store: ProjectTaskStore,
        planner: EpisodeTaskPlanService,
        task_queue: TaskQueue | None = None,
        retry_task: Callable[[UUID], Awaitable[GenerationTaskRecord]] | None = None,
    ) -> None:
        self._store = store
        self._planner = planner
        self._task_queue = task_queue
        self._retry_task = retry_task
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def start(
        self,
        project_id: UUID,
        request: EpisodeTaskPlanCreateRequest,
        response: EpisodeTaskPlanResponse,
        idempotency_key: str | None = None,
    ) -> EpisodeTaskPlanResponse:
        """Mark the first episode-planner wave as an auto-run."""

        run_id = self._run_id(project_id, idempotency_key)
        task_ids = self._task_ids(response)
        if not task_ids:
            # A blocked plan still needs a durable seed so a later asset review
            # can wake the same run.  Existing project tasks are enough; no
            # separate run table is required for this MVP.
            candidates = await self._store.list_tasks(project_id=project_id, limit=5000)
            seed = next(
                (
                    item
                    for item in candidates
                    if item.kind
                    in {
                        GenerationTaskKind.NOVEL_STORY_BIBLE,
                        GenerationTaskKind.NOVEL_EPISODE_PLAN,
                        GenerationTaskKind.NOVEL_EPISODE_SCRIPT,
                        GenerationTaskKind.NOVEL_SHOT_LIST,
                    }
                ),
                None,
            )
            if seed is None:
                return response.model_copy(update={"auto_advance": False})
            task_ids = [seed.id]
        await self._annotate_tasks(task_ids, run_id, self._plan_payload(request))
        await self._reconcile_completed_tasks(task_ids)
        return response.model_copy(update={"auto_run_id": run_id, "auto_advance": True})

    async def start_from_source(
        self,
        project_id: UUID,
        request: ProductionRunCreateRequest,
        idempotency_key: str | None = None,
    ) -> ProductionRunResponse:
        """Start the complete DAG from an uploaded novel.

        The command is safe to repeat with the same idempotency key.  It
        creates only the earliest missing task: StoryBible, episode planning,
        or the normal dependency-aware episode production wave.
        """

        project = await self._store.get_novel_project(project_id)
        if project is None:
            raise NovelProjectNotFoundError
        if project.source_id is None:
            raise NovelSourceNotFoundError

        run_id = self._run_id(project_id, idempotency_key)
        existing = await self._run_tasks(project_id, str(run_id))
        if existing:
            return await self._run_response(project_id, run_id, existing)

        plan = self._plan_payload(request)
        story_bible = await self._store.get_latest_story_bible(project_id)
        if story_bible is None:
            story_task = await self._latest_task(project_id, GenerationTaskKind.NOVEL_STORY_BIBLE)
            if story_task is None:
                story_task, _ = await self._planner.create_story_bible_task(
                    project_id,
                    idempotency_key=f"auto-dag:{run_id}:story-bible",
                    task_metadata=self._markers(run_id, plan),
                )
            else:
                await self._annotate_tasks([story_task.id], run_id, plan)
            return await self._run_response(project_id, run_id, [story_task])

        episodes = await self._store.list_episodes(project_id)
        if not episodes:
            episode_task = await self._latest_task(project_id, GenerationTaskKind.NOVEL_EPISODE_PLAN)
            if episode_task is None:
                target_count = request.target_episode_count or project.target_episode_count
                episode_task, _ = await self._planner.create_episode_plan_task(
                    project_id,
                    target_episode_count=target_count,
                    idempotency_key=f"auto-dag:{run_id}:episode-plan",
                    task_metadata=self._markers(run_id, plan),
                )
            else:
                await self._annotate_tasks([episode_task.id], run_id, plan)
            return await self._run_response(project_id, run_id, [episode_task])

        episode_request = EpisodeTaskPlanCreateRequest.model_validate(
            {key: value for key, value in plan.items() if key != "target_episode_count"}
        )
        response = await self._planner.create(
            project_id,
            episode_request,
            idempotency_key=f"auto-dag:{run_id}:initial-wave",
        )
        started = await self.start(
            project_id,
            episode_request,
            response,
            idempotency_key=idempotency_key,
        )
        public_status = (
            "blocked"
            if started.blocked_count
            else "active"
            if started.auto_advance
            else "completed"
        )
        return ProductionRunResponse(
            project_id=project_id,
            run_id=run_id,
            status=public_status,
            stage=(started.items[0].stage or "production") if started.items else "production",
            task_ids=self._task_ids(started),
            auto_advance=started.auto_advance,
            message="已启动自动生产 Run；后续阶段由 Worker 按依赖自动推进。",
            plan=started,
        )

    async def on_task_finished(self, task_id: UUID) -> EpisodeTaskPlanResponse | None:
        task = await self._store.get_task(task_id)
        if task is None:
            return None
        run_id = task.input_data.get(AUTO_RUN_ID)
        if not run_id or task.input_data.get(AUTO_RUN_ENABLED) is not True:
            return None
        return await self._tick_run(str(run_id), task)

    async def pause_run(self, project_id: UUID, run_id: UUID) -> ProductionRunResponse:
        """Pause DAG advancement without interrupting a running Provider call."""

        return await self._control_run(project_id, run_id, "paused")

    async def resume_run(self, project_id: UUID, run_id: UUID) -> ProductionRunResponse:
        """Resume a paused Run and requeue durable tasks not yet executed."""

        tasks = await self._require_run_tasks(project_id, run_id)
        current_status = run_status_from_tasks(tasks)
        if current_status == "canceled":
            raise ProductionRunControlError(
                "PRODUCTION_RUN_CANCELED",
                "已取消的 Run 不能恢复，请使用新的幂等键重新启动。",
            )
        if current_status in {"completed", "failed"}:
            raise ProductionRunControlError(
                "PRODUCTION_RUN_TERMINAL",
                "已完成或失败的 Run 不能直接恢复，请先处理失败任务或使用新的幂等键。",
            )

        async with self._locks[str(run_id)]:
            tasks = await self._require_run_tasks(project_id, run_id)
            current_status = run_status_from_tasks(tasks)
            if current_status == "canceled":
                raise ProductionRunControlError(
                    "PRODUCTION_RUN_CANCELED",
                    "已取消的 Run 不能恢复，请使用新的幂等键重新启动。",
                )
            if current_status in {"completed", "failed"}:
                raise ProductionRunControlError(
                    "PRODUCTION_RUN_TERMINAL",
                    "已完成或失败的 Run 不能直接恢复，请先处理失败任务或使用新的幂等键。",
                )
            revision = self._next_control_revision(tasks)
            if current_status == "paused":
                await self._set_run_status(
                    tasks,
                    "active",
                    control_revision=revision,
                    force=True,
                )
            retried_task_ids = await self._resume_pending_auto_retries(tasks)
            await self._requeue_runnable_tasks(tasks, skip_task_ids=retried_task_ids)
        # Re-enter the normal locked tick after the control transition.  This
        # advances a Run whose next wave was already satisfied while avoiding
        # a lock re-entry deadlock.
        if tasks:
            await self._tick_run(str(run_id), tasks[0])
        return await self.get_run(project_id, run_id)

    async def cancel_run(self, project_id: UUID, run_id: UUID) -> ProductionRunResponse:
        """Stop a Run, canceling queued work while preserving existing Artifacts."""

        return await self._control_run(project_id, run_id, "canceled")

    async def on_task_failed(self, task_id: UUID) -> None:
        """Reflect a failed automatic task in its durable Run marker.

        The Worker may later schedule a retry. In that case the Run is
        ``blocked`` until the retry is requeued; a terminal failure is marked
        ``failed`` immediately instead of being left looking active.
        """

        task = await self._store.get_task(task_id)
        if task is None:
            return
        if task.status != TaskStatus.FAILED:
            return
        run_id = task.input_data.get(AUTO_RUN_ID)
        if not run_id or task.input_data.get(AUTO_RUN_ENABLED) is not True:
            return
        run_tasks = await self._run_tasks(task.project_id, str(run_id))
        if run_status_from_tasks(run_tasks) in _RUN_STOPPED_STATUSES:
            return
        await self._set_run_status(
            run_tasks,
            "blocked" if task.input_data.get("auto_retry_pending") is True else "failed",
        )

    async def tick_all(self) -> list[EpisodeTaskPlanResponse]:
        tasks = await self._store.list_tasks(limit=5000)
        by_run: dict[str, GenerationTaskRecord] = {}
        for task in tasks:
            run_id = task.input_data.get(AUTO_RUN_ID)
            if run_id and task.input_data.get(AUTO_RUN_ENABLED) is True:
                by_run.setdefault(str(run_id), task)
        results: list[EpisodeTaskPlanResponse] = []
        for run_id, task in by_run.items():
            result = await self._tick_run(run_id, task)
            if result is not None:
                results.append(result)
        return results

    async def tick_project(self, project_id: UUID) -> list[EpisodeTaskPlanResponse]:
        """Reconcile only the automatic Runs belonging to one project.

        This is used after an asset review so a blocked Run can resume in the
        same request cycle, without making the asset endpoint scan unrelated
        projects.
        """

        tasks = await self._store.list_tasks(project_id=project_id, limit=5000)
        by_run: dict[str, GenerationTaskRecord] = {}
        for task in tasks:
            run_id = task.input_data.get(AUTO_RUN_ID)
            if run_id and task.input_data.get(AUTO_RUN_ENABLED) is True:
                by_run.setdefault(str(run_id), task)
        results: list[EpisodeTaskPlanResponse] = []
        for run_id, task in by_run.items():
            result = await self._tick_run(run_id, task)
            if result is not None:
                results.append(result)
        return results

    async def get_latest_run(self, project_id: UUID) -> ProductionRunResponse:
        """Return the most recently updated automatic Run for a project."""

        tasks = await self._store.list_tasks(project_id=project_id, limit=5000)
        groups = self._group_run_tasks(tasks)
        if not groups:
            raise ProductionRunNotFoundError
        run_id, run_tasks = max(
            groups.items(),
            key=lambda item: max(task.updated_at for task in item[1]),
        )
        return await self._run_response(project_id, UUID(run_id), run_tasks)

    async def get_run(self, project_id: UUID, run_id: UUID) -> ProductionRunResponse:
        """Return one persisted Run reconstructed from task markers."""

        tasks = await self._run_tasks(project_id, str(run_id))
        if not tasks:
            raise ProductionRunNotFoundError
        return await self._run_response(project_id, run_id, tasks)

    async def _tick_run(
        self,
        run_id: str,
        seed_task: GenerationTaskRecord,
    ) -> EpisodeTaskPlanResponse | None:
        lock = self._locks[run_id]
        if lock.locked():
            return None
        async with lock:
            task = await self._store.get_task(seed_task.id)
            if task is None:
                return None
            raw_plan = task.input_data.get(AUTO_RUN_PLAN)
            if not isinstance(raw_plan, dict):
                return None
            try:
                request = EpisodeTaskPlanCreateRequest.model_validate(
                    {key: value for key, value in raw_plan.items() if key != "target_episode_count"}
                )
            except ValueError:
                await self._set_run_status(
                    await self._run_tasks(task.project_id, run_id),
                    "failed",
                )
                return None

            run_tasks = await self._run_tasks(task.project_id, run_id)
            if not run_tasks:
                return None
            if run_status_from_tasks(run_tasks) in _RUN_STOPPED_STATUSES:
                return None
            if any(
                item.status in {TaskStatus.CREATED, TaskStatus.QUEUED, TaskStatus.RUNNING}
                for item in run_tasks
            ):
                return None

            terminal_failures = [
                item
                for item in run_tasks
                if item.status == TaskStatus.FAILED
                and item.input_data.get("auto_retry_pending") is not True
            ]
            if terminal_failures:
                await self._set_run_status(run_tasks, "failed")
                return None
            if any(
                item.status == TaskStatus.FAILED
                and item.input_data.get("auto_retry_pending") is True
                for item in run_tasks
            ):
                await self._set_run_status(run_tasks, "blocked")
                return None

            has_episode_plan = any(
                item.kind == GenerationTaskKind.NOVEL_EPISODE_PLAN for item in run_tasks
            )
            if (
                any(item.kind == GenerationTaskKind.NOVEL_STORY_BIBLE for item in run_tasks)
                and not has_episode_plan
            ):
                target_count = raw_plan.get("target_episode_count")
                target_episode_count = (
                    int(target_count)
                    if isinstance(target_count, (int, str)) and str(target_count).strip()
                    else None
                )
                next_task, _ = await self._planner.create_episode_plan_task(
                    task.project_id,
                    target_episode_count=target_episode_count,
                    idempotency_key=f"auto-dag:{run_id}:episode-plan",
                    task_metadata=self._markers(run_id, raw_plan),
                )
                await self._annotate_tasks([next_task.id], run_id, raw_plan)
                return None

            try:
                await self._set_run_status(run_tasks, "active")
                response = await self._planner.create(
                    task.project_id,
                    request,
                    idempotency_key=self._stable_wave_key(run_id, run_tasks),
                )
            except Exception:
                # Keep blocked runs enabled: asset review or an operator fix
                # should allow the next scheduler tick to continue.
                await self._set_run_status(run_tasks, "blocked")
                raise

            # A skipped response may intentionally carry the already-successful
            # task ID so the UI can show what was completed between two planner
            # calls. Those IDs are evidence, not work for the next wave. Only
            # created/reused items should keep the Run active or be annotated
            # as the next dependency-ready tasks.
            next_task_ids = self._actionable_task_ids(response)
            if not next_task_ids and any(item.action.value == "blocked" for item in response.items):
                await self._set_run_status(run_tasks, "blocked")
                return response.model_copy(
                    update={"auto_run_id": UUID(run_id), "auto_advance": True}
                )
            if not next_task_ids and all(item.action.value == "skipped" for item in response.items):
                await self._set_run_status(run_tasks, "completed")
                return response.model_copy(
                    update={"auto_run_id": UUID(run_id), "auto_advance": False}
                )
            if not next_task_ids:
                await self._set_run_status(run_tasks, "blocked")
                return response.model_copy(
                    update={"auto_run_id": UUID(run_id), "auto_advance": True}
                )

            await self._annotate_tasks(next_task_ids, run_id, raw_plan)
            return response.model_copy(
                update={"auto_run_id": UUID(run_id), "auto_advance": True}
            )

    async def _run_tasks(
        self,
        project_id: UUID,
        run_id: str,
    ) -> list[GenerationTaskRecord]:
        return [
            item
            for item in await self._store.list_tasks(project_id=project_id, limit=5000)
            if str(item.input_data.get(AUTO_RUN_ID)) == run_id
        ]

    @staticmethod
    def _group_run_tasks(
        tasks: list[GenerationTaskRecord],
    ) -> dict[str, list[GenerationTaskRecord]]:
        groups: dict[str, list[GenerationTaskRecord]] = defaultdict(list)
        for task in tasks:
            raw_run_id = task.input_data.get(AUTO_RUN_ID)
            if not raw_run_id:
                continue
            try:
                UUID(str(raw_run_id))
            except (TypeError, ValueError):
                continue
            groups[str(raw_run_id)].append(task)
        return dict(groups)

    async def _reconcile_completed_tasks(self, task_ids: list[UUID]) -> None:
        for task_id in task_ids:
            task = await self._store.get_task(task_id)
            if task is not None and task.status == TaskStatus.SUCCEEDED:
                await self.on_task_finished(task_id)

    async def _annotate_tasks(
        self,
        task_ids: list[UUID],
        run_id: UUID | str,
        plan: dict[str, Any],
    ) -> None:
        if not task_ids:
            return
        first_task = await self._store.get_task(task_ids[0])
        if first_task is None:
            return
        existing = await self._run_tasks(first_task.project_id, str(run_id))
        current_status = run_status_from_tasks(existing)
        control_revision = self._current_control_revision(existing)
        for task_id in task_ids:
            task = await self._store.get_task(task_id)
            if task is None:
                continue
            task.input_data.update(
                self._markers(
                    run_id,
                    plan,
                    status=current_status,
                    control_revision=control_revision,
                )
            )
            await self._store.update_task(task)

    @staticmethod
    def _markers(
        run_id: UUID | str,
        plan: dict[str, Any],
        *,
        status: str = "active",
        control_revision: int = 0,
    ) -> dict[str, Any]:
        markers = {
            AUTO_RUN_ID: str(run_id),
            AUTO_RUN_PLAN: plan,
            AUTO_RUN_ENABLED: status not in {"paused", "canceled", "completed", "failed"},
            AUTO_RUN_STATUS: status,
            AUTO_RUN_CONTROL_REVISION: control_revision,
        }
        quality_profile_id = plan.get("visual_quality_profile_id")
        if isinstance(quality_profile_id, str) and quality_profile_id:
            markers["visual_quality_profile_id"] = quality_profile_id
        return markers

    async def _set_run_status(
        self,
        tasks: list[GenerationTaskRecord],
        status: str,
        *,
        control_revision: int | None = None,
        force: bool = False,
    ) -> None:
        if not tasks:
            return
        current_status = run_status_from_tasks(tasks)
        current_revision = self._current_control_revision(tasks)
        if current_status in _RUN_STOPPED_STATUSES and not force:
            return
        revision = current_revision if control_revision is None else control_revision
        for task in tasks:
            if not force:
                latest_tasks = await self._run_tasks(task.project_id, str(task.input_data.get(AUTO_RUN_ID)))
                if self._current_control_revision(latest_tasks) > revision:
                    return
                if run_status_from_tasks(latest_tasks) in _RUN_STOPPED_STATUSES:
                    return
            if task.input_data.get(AUTO_RUN_STATUS) == status:
                continue
            task.input_data[AUTO_RUN_STATUS] = status
            task.input_data[AUTO_RUN_ENABLED] = status not in {
                "completed",
                "failed",
                "paused",
                "canceled",
            }
            task.input_data[AUTO_RUN_CONTROL_REVISION] = revision
            await self._store.update_task(task)

    async def _control_run(
        self,
        project_id: UUID,
        run_id: UUID,
        desired_status: str,
    ) -> ProductionRunResponse:
        tasks = await self._require_run_tasks(project_id, run_id)
        async with self._locks[str(run_id)]:
            tasks = await self._require_run_tasks(project_id, run_id)
            current_status = run_status_from_tasks(tasks)
            if desired_status == "paused" and current_status in {"completed", "failed", "canceled"}:
                raise ProductionRunControlError(
                    "PRODUCTION_RUN_TERMINAL",
                    "已完成、失败或取消的 Run 不能暂停。",
                )
            if desired_status == "canceled" and current_status in {"completed", "failed"}:
                raise ProductionRunControlError(
                    "PRODUCTION_RUN_TERMINAL",
                    "已完成或失败的 Run 不能取消。",
                )
            revision = self._next_control_revision(tasks)
            if current_status != desired_status:
                await self._set_run_status(
                    tasks,
                    desired_status,
                    control_revision=revision,
                    force=True,
                )
            else:
                # An in-flight Worker can create a task between two control
                # writes. Normalize all currently visible tasks even for an
                # idempotent repeated pause/cancel request.
                await self._set_run_status(
                    tasks,
                    desired_status,
                    control_revision=self._current_control_revision(tasks),
                    force=True,
                )
            if desired_status == "canceled":
                tasks = await self._require_run_tasks(project_id, run_id)
                for task in tasks:
                    task.input_data["auto_retry_pending"] = False
                    task.input_data.pop("next_retry_at", None)
                    if task.status in {TaskStatus.CREATED, TaskStatus.QUEUED}:
                        self._mark_task_canceled(task)
                    await self._store.update_task(task)
            return await self.get_run(project_id, run_id)

    async def _require_run_tasks(
        self,
        project_id: UUID,
        run_id: UUID,
    ) -> list[GenerationTaskRecord]:
        tasks = await self._run_tasks(project_id, str(run_id))
        if not tasks:
            raise ProductionRunNotFoundError
        return tasks

    async def _requeue_runnable_tasks(
        self,
        tasks: list[GenerationTaskRecord],
        *,
        skip_task_ids: set[UUID] | None = None,
    ) -> None:
        if self._task_queue is None:
            return
        skipped = skip_task_ids or set()
        for task in tasks:
            if task.id in skipped:
                continue
            if task.status in {TaskStatus.CREATED, TaskStatus.QUEUED}:
                await self._task_queue.enqueue(task.id)

    async def _resume_pending_auto_retries(
        self,
        tasks: list[GenerationTaskRecord],
    ) -> set[UUID]:
        """Requeue due automatic retries when a paused Run is resumed.

        Redis Workers also have a periodic retry scheduler.  The callback is
        optional so the orchestrator remains usable with a minimal queue in
        tests; when present it closes the gap for the in-process queue, which
        has no independent scheduler loop.
        """

        if self._retry_task is None:
            return set()
        now = utc_now()
        retried: set[UUID] = set()
        for task in tasks:
            if task.status != TaskStatus.FAILED:
                continue
            if task.input_data.get("auto_retry_pending") is not True:
                continue
            if not self._retry_is_due(task.input_data.get("next_retry_at"), now):
                continue
            try:
                await self._retry_task(task.id)
            except Exception:
                # Keep the failed task and its retry marker durable.  The
                # Worker scheduler or a later resume can try the enqueue again.
                continue
            retried.add(task.id)
        return retried

    @staticmethod
    def _retry_is_due(value: object, now: datetime) -> bool:
        if not isinstance(value, str) or not value.strip():
            return True
        try:
            retry_at = datetime.fromisoformat(value)
        except ValueError:
            return True
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)
        return retry_at.astimezone(timezone.utc) <= now

    @staticmethod
    def _mark_task_canceled(task: GenerationTaskRecord) -> None:
        now = utc_now()
        task.status = TaskStatus.CANCELED
        task.current_stage = task.current_stage or (
            task.stages[-1].stage if task.stages else None
        )
        task.updated_at = now
        task.error = TaskError(
            code="PRODUCTION_RUN_CANCELED",
            message="The production Run was canceled before this task started.",
        )
        if task.current_stage is None:
            return
        stage_run = next(
            (item for item in task.stages if item.stage == task.current_stage),
            None,
        )
        if stage_run is None:
            stage_run = StageRun(stage=task.current_stage, status=TaskStatus.CANCELED)
            task.stages.append(stage_run)
        else:
            stage_run.status = TaskStatus.CANCELED
            stage_run.error_code = "PRODUCTION_RUN_CANCELED"
            stage_run.finished_at = now

    @staticmethod
    def _current_control_revision(tasks: list[GenerationTaskRecord]) -> int:
        return max((control_revision(task.input_data) for task in tasks), default=0)

    @classmethod
    def _next_control_revision(cls, tasks: list[GenerationTaskRecord]) -> int:
        return cls._current_control_revision(tasks) + 1

    async def _run_response(
        self,
        project_id: UUID,
        run_id: UUID,
        tasks: list[GenerationTaskRecord],
    ) -> ProductionRunResponse:
        status = run_status_from_tasks(tasks)
        latest = max(tasks, key=lambda item: item.updated_at)
        message = {
            "active": "Run 正在由 Worker 按依赖推进。",
            "blocked": "Run 等待资产审核、配置修复或失败任务恢复；条件满足后会继续推进。",
            "paused": "Run 已暂停；正在运行的任务会自然结束，恢复后继续处理未执行任务。",
            "completed": "Run 已完成当前配置启用的全部阶段。",
            "failed": "Run 存在不可自动恢复的失败任务，请在任务中心处理后重新启动。",
            "canceled": "Run 已取消；已生成的 Artifact 保留，未开始的任务不会再执行。",
        }[status]
        return ProductionRunResponse(
            project_id=project_id,
            run_id=run_id,
            visual_quality_profile_id=next(
                (
                    str(task.input_data.get("visual_quality_profile_id"))
                    for task in tasks
                    if task.input_data.get("visual_quality_profile_id")
                ),
                None,
            ),
            status=self._public_status(status),
            stage=latest.kind.value,
            task_ids=[task.id for task in tasks],
            auto_advance=status not in {"paused", "completed", "failed", "canceled"},
            message=message,
        )

    async def _latest_task(
        self,
        project_id: UUID,
        kind: GenerationTaskKind,
    ) -> GenerationTaskRecord | None:
        tasks = await self._store.list_tasks(project_id=project_id, kind=kind.value, limit=5000)
        return max(tasks, key=lambda item: item.updated_at) if tasks else None

    @staticmethod
    def _run_id(project_id: UUID, idempotency_key: str | None) -> UUID:
        if idempotency_key:
            return uuid5(NAMESPACE_URL, f"ai-video:auto-dag:{project_id}:{idempotency_key}")
        return uuid4()

    @staticmethod
    def _plan_payload(request: ProductionRunCreateRequest | EpisodeTaskPlanCreateRequest) -> dict[str, Any]:
        plan = request.model_dump(mode="json")
        plan["auto_advance"] = True
        return plan

    @staticmethod
    def _public_status(status: str) -> str:
        return status if status in {"active", "blocked", "paused", "completed", "failed", "canceled"} else "active"

    @staticmethod
    def _stable_wave_key(run_id: str, tasks: list[GenerationTaskRecord]) -> str:
        signature = "|".join(
            f"{item.id}:{item.kind.value}:{item.status.value}:{item.stages[-1].attempt if item.stages else 1}"
            for item in sorted(tasks, key=lambda value: str(value.id))
        )
        import hashlib

        digest = hashlib.sha256(signature.encode("utf-8")).hexdigest()[:20]
        return f"auto-dag:{run_id}:wave:{digest}"

    @staticmethod
    def _task_ids(response: EpisodeTaskPlanResponse) -> list[UUID]:
        ids: list[UUID] = []
        for item in response.items:
            ids.extend(item.task_ids)
        for batch in response.batches:
            ids.extend(batch.task_ids)
        return list(dict.fromkeys(ids))

    @staticmethod
    def _actionable_task_ids(response: EpisodeTaskPlanResponse) -> list[UUID]:
        """Return only task IDs that represent work for the next DAG wave.

        The planner keeps successful task IDs on skipped items for observability
        and client-side reconciliation. Automatic Run lifecycle decisions must
        exclude those carry-forward IDs, otherwise a completed Run is mistaken
        for a Run with more work to enqueue.
        """

        ids: list[UUID] = []
        for item in response.items:
            if item.action.value not in {"created", "reused"}:
                continue
            ids.extend(item.task_ids)
        return list(dict.fromkeys(ids))
