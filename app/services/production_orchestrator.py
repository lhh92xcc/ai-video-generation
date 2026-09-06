"""Durable, event-driven advancement of the novel production DAG.

The episode planner remains the single place that knows media dependencies.
This service owns the lifecycle around it: creating a run from an uploaded
novel, persisting the run marker on every task, advancing after completion and
reconciling the same state after an API/Worker restart.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from app.domain.models import (
    EpisodeTaskPlanCreateRequest,
    EpisodeTaskPlanResponse,
    GenerationTaskKind,
    GenerationTaskRecord,
    ProductionRunCreateRequest,
    ProductionRunResponse,
    TaskStatus,
)
from app.repositories.protocol import ProjectTaskStore
from app.services.episode_task_plan_service import EpisodeTaskPlanService
from app.services.novel_service import NovelProjectNotFoundError, NovelSourceNotFoundError


AUTO_RUN_ID = "auto_run_id"
AUTO_RUN_PLAN = "auto_run_plan"
AUTO_RUN_ENABLED = "auto_advance"
AUTO_RUN_STATUS = "auto_run_status"


class ProductionOrchestrator:
    def __init__(
        self,
        store: ProjectTaskStore,
        planner: EpisodeTaskPlanService,
    ) -> None:
        self._store = store
        self._planner = planner
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
        return ProductionRunResponse(
            project_id=project_id,
            run_id=run_id,
            status="blocked" if started.blocked_count else "active",
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

            next_task_ids = self._task_ids(response)
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
        for task_id in task_ids:
            task = await self._store.get_task(task_id)
            if task is None:
                continue
            task.input_data.update(self._markers(run_id, plan))
            await self._store.update_task(task)

    @staticmethod
    def _markers(run_id: UUID | str, plan: dict[str, Any]) -> dict[str, Any]:
        markers = {
            AUTO_RUN_ID: str(run_id),
            AUTO_RUN_PLAN: plan,
            AUTO_RUN_ENABLED: True,
            AUTO_RUN_STATUS: "active",
        }
        quality_profile_id = plan.get("visual_quality_profile_id")
        if isinstance(quality_profile_id, str) and quality_profile_id:
            markers["visual_quality_profile_id"] = quality_profile_id
        return markers

    async def _set_run_status(
        self,
        tasks: list[GenerationTaskRecord],
        status: str,
    ) -> None:
        for task in tasks:
            if task.input_data.get(AUTO_RUN_STATUS) == status:
                continue
            task.input_data[AUTO_RUN_STATUS] = status
            task.input_data[AUTO_RUN_ENABLED] = status not in {"completed", "failed"}
            await self._store.update_task(task)

    async def _run_response(
        self,
        project_id: UUID,
        run_id: UUID,
        tasks: list[GenerationTaskRecord],
    ) -> ProductionRunResponse:
        status = next(
            (
                str(task.input_data.get(AUTO_RUN_STATUS))
                for task in tasks
                if task.input_data.get(AUTO_RUN_STATUS)
            ),
            "active",
        )
        latest = max(tasks, key=lambda item: item.updated_at)
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
            auto_advance=status not in {"completed", "failed"},
            message="已复用同一幂等键对应的生产 Run。",
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
        return status if status in {"active", "blocked", "completed", "failed"} else "active"

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
