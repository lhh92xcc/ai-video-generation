"""Dependency-aware orchestration for multi-episode novel production.

The planner is intentionally a small, durable DAG coordinator. It never waits
for a provider inside the HTTP request and it never bundles multiple expensive
steps into one task. Each call creates the next stage whose inputs are already
available; a later call advances the same episode after the previous Artifact
has completed. Existing active/failed tasks are reused so a failed plan can be
resumed through the task batch API without creating duplicates.
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from app.domain.models import (
    AssetRecord,
    AssetStatus,
    AssetType,
    AudioBGMCreateRequest,
    AudioNarrationCreateRequest,
    EpisodeTaskPlanAction,
    EpisodeTaskPlanCreateRequest,
    EpisodeTaskPlanItem,
    EpisodeTaskPlanResponse,
    EpisodeRecord,
    GenerationTaskKind,
    GenerationTaskRecord,
    ReferenceImageCreateRequest,
    ReferenceImageRecord,
    ReferenceImageStatus,
    ShotContent,
    SubtitleASRCreateRequest,
    SubtitleAlignmentCreateRequest,
    TaskBatchCreateRequest,
    TaskStatus,
    VideoAssemblyCreateRequest,
    VideoClipCreateRequest,
)
from app.providers.profiles import ASRProviderProfileError
from app.media.narration_text import build_episode_narration_text
from app.repositories.protocol import NovelStore, ProjectTaskStore
from app.services.bgm_service import BGMTaskService
from app.services.novel_service import (
    NovelService,
)
from app.services.novel_task_service import NovelGenerationTaskService
from app.services.reference_image_service import ReferenceImageTaskService
from app.services.subtitle_service import SubtitleTaskService
from app.services.task_batch_service import TaskBatchService
from app.services.tts_service import TTSTaskService
from app.services.video_assembly_service import VideoAssemblyTaskService
from app.services.video_clip_service import VideoClipTaskService


class EpisodeTaskPlanStore(ProjectTaskStore, NovelStore, Protocol):
    """Combined repository surface needed by the planner."""


class EpisodeTaskPlanEpisodeMismatchError(Exception):
    def __init__(self, episode_id: UUID) -> None:
        self.episode_id = episode_id


class EpisodeTaskPlanEmptyError(Exception):
    pass


class EpisodeTaskPlanService:
    """Advance each selected episode by one dependency-ready production stage."""

    _TASK_QUERY_LIMIT = 5000

    def __init__(
        self,
        store: EpisodeTaskPlanStore,
        novel_service: NovelService,
        novel_task_service: NovelGenerationTaskService,
        task_batch_service: TaskBatchService,
        reference_image_task_service: ReferenceImageTaskService,
        tts_task_service: TTSTaskService,
        subtitle_task_service: SubtitleTaskService,
        bgm_task_service: BGMTaskService,
        video_clip_task_service: VideoClipTaskService,
        video_assembly_task_service: VideoAssemblyTaskService,
    ) -> None:
        self._store = store
        self._novel_service = novel_service
        self._novel_task_service = novel_task_service
        self._task_batch_service = task_batch_service
        self._reference_image_task_service = reference_image_task_service
        self._tts_task_service = tts_task_service
        self._subtitle_task_service = subtitle_task_service
        self._bgm_task_service = bgm_task_service
        self._video_clip_task_service = video_clip_task_service
        self._video_assembly_task_service = video_assembly_task_service

    async def create_story_bible_task(
        self,
        project_id: UUID,
        idempotency_key: str | None = None,
        *,
        task_metadata: dict[str, object] | None = None,
    ) -> tuple[GenerationTaskRecord, bool]:
        """Expose the upstream task boundary to the production orchestrator."""

        return await self._novel_task_service.create_story_bible_task(
            project_id,
            idempotency_key,
            task_metadata=task_metadata,
        )

    async def create_episode_plan_task(
        self,
        project_id: UUID,
        target_episode_count: int | None = None,
        idempotency_key: str | None = None,
        *,
        task_metadata: dict[str, object] | None = None,
    ) -> tuple[GenerationTaskRecord, bool]:
        """Expose episode planning without leaking the nested service field."""

        return await self._novel_task_service.create_episode_plan_task(
            project_id,
            target_episode_count,
            idempotency_key,
            task_metadata=task_metadata,
        )

    async def create(
        self,
        project_id: UUID,
        request: EpisodeTaskPlanCreateRequest,
        idempotency_key: str | None = None,
    ) -> EpisodeTaskPlanResponse:
        await self._novel_service.get_project(project_id)
        await self._novel_service.get_story_bible(project_id)
        episodes = await self._novel_service.list_episodes(project_id)
        selected = self._select_episodes(episodes, request.episode_ids)
        if not selected:
            raise EpisodeTaskPlanEmptyError

        project_tasks = await self._store.list_tasks(
            project_id=project_id,
            limit=self._TASK_QUERY_LIMIT,
        )
        task_ids: list[UUID] = []
        items: list[EpisodeTaskPlanItem] = []
        created_count = 0
        reused_count = 0
        skipped_count = 0
        blocked_count = 0

        for episode in selected:
            item, stage_task_ids = await self._plan_episode(
                episode,
                project_tasks,
                request,
                idempotency_key,
            )
            items.append(item)
            task_ids.extend(stage_task_ids)
            if item.action == EpisodeTaskPlanAction.CREATED:
                created_count += 1
            elif item.action == EpisodeTaskPlanAction.REUSED:
                reused_count += 1
            elif item.action == EpisodeTaskPlanAction.SKIPPED:
                skipped_count += 1
            else:
                blocked_count += 1

        batch = None
        batches = []
        if task_ids:
            for part, start in enumerate(range(0, len(task_ids), 100), start=1):
                chunk = task_ids[start : start + 100]
                batch_request = TaskBatchCreateRequest(
                    project_id=project_id,
                    task_ids=chunk,
                    label=request.label,
                )
                batch_key = (
                    f"episode-task-plan:{idempotency_key}:part:{part}"
                    if idempotency_key
                    else None
                )
                created_batch, _ = await self._task_batch_service.create(batch_request, batch_key)
                batches.append(created_batch)
            batch = batches[0]

        return EpisodeTaskPlanResponse(
            project_id=project_id,
            label=request.label,
            auto_advance=request.auto_advance,
            batch=batch,
            batches=batches,
            items=items,
            created_count=created_count,
            reused_count=reused_count,
            skipped_count=skipped_count,
            blocked_count=blocked_count,
        )

    async def advance_after_task(
        self,
        task: GenerationTaskRecord,
        *,
        include_bgm: bool = False,
    ) -> EpisodeTaskPlanResponse | GenerationTaskRecord | None:
        """Advance the durable novel DAG after a successful task.

        This method is intentionally event-driven: the Worker calls it after
        ACKing a successful task, and the same idempotent planner is also safe
        to call during startup reconciliation.  It never waits for the next
        Provider and therefore remains safe to call from the Worker loop.
        """

        if task.status != TaskStatus.SUCCEEDED:
            return None
        if task.kind == GenerationTaskKind.NOVEL_STORY_BIBLE:
            next_task, _ = await self._novel_task_service.create_episode_plan_task(
                task.project_id,
                idempotency_key=f"auto-dag:episode-plan:{task.project_id}",
            )
            return next_task
        if task.kind == GenerationTaskKind.NOVEL_EPISODE_PLAN:
            return await self.create(
                task.project_id,
                EpisodeTaskPlanCreateRequest(
                    label="自动生产 DAG",
                    production_mode=True,
                    include_reference_images=True,
                    include_narration=True,
                    include_subtitles=True,
                    include_bgm=include_bgm,
                    include_video=True,
                    include_assembly=True,
                    auto_advance=True,
                ),
                idempotency_key=f"auto-dag:episodes:{task.project_id}",
            )

        if task.kind not in {
            GenerationTaskKind.NOVEL_EPISODE_SCRIPT,
            GenerationTaskKind.NOVEL_SHOT_LIST,
            GenerationTaskKind.ASSET_REFERENCE_IMAGE,
            GenerationTaskKind.AUDIO_NARRATION,
            GenerationTaskKind.SUBTITLE_ALIGN,
            GenerationTaskKind.SUBTITLE_ASR,
            GenerationTaskKind.AUDIO_BGM,
            GenerationTaskKind.VIDEO_CLIP,
            GenerationTaskKind.VIDEO_ASSEMBLY,
        }:
            return None
        raw_episode_id = task.input_data.get("episode_id")
        if raw_episode_id is None:
            return None
        try:
            episode_id = UUID(str(raw_episode_id))
        except (TypeError, ValueError):
            return None
        return await self.create(
            task.project_id,
            EpisodeTaskPlanCreateRequest(
                episode_ids=[episode_id],
                label="自动生产 DAG",
                production_mode=True,
                include_reference_images=True,
                include_narration=True,
                include_subtitles=True,
                include_bgm=include_bgm,
                include_video=True,
                include_assembly=True,
                auto_advance=True,
            ),
            idempotency_key=f"auto-dag:episode:{episode_id}",
        )

    @staticmethod
    def _select_episodes(
        episodes: list[EpisodeRecord],
        requested_ids: list[UUID] | None,
    ) -> list[EpisodeRecord]:
        ordered = sorted(episodes, key=lambda item: item.episode_number)
        if requested_ids is None:
            return ordered[:100]
        by_id = {episode.id: episode for episode in ordered}
        selected: list[EpisodeRecord] = []
        for episode_id in requested_ids:
            episode = by_id.get(episode_id)
            if episode is None:
                raise EpisodeTaskPlanEpisodeMismatchError(episode_id)
            selected.append(episode)
        return sorted(selected, key=lambda item: item.episode_number)

    async def _plan_episode(
        self,
        episode: EpisodeRecord,
        project_tasks: list[GenerationTaskRecord],
        request: EpisodeTaskPlanCreateRequest,
        idempotency_key: str | None,
    ) -> tuple[EpisodeTaskPlanItem, list[UUID]]:
        planned_task = self._latest_planned_task(project_tasks, episode.id, idempotency_key)
        if planned_task is not None:
            return self._item(
                episode,
                planned_task.kind.value,
                EpisodeTaskPlanAction.REUSED,
                "复用同一幂等键创建的任务计划",
                [planned_task.id],
            ), [planned_task.id]
        script_task = self._latest_task(project_tasks, GenerationTaskKind.NOVEL_EPISODE_SCRIPT, episode.id)
        script = await self._store.get_latest_episode_script(episode.id)
        if script_task is not None and script_task.status != TaskStatus.SUCCEEDED:
            return self._item(episode, GenerationTaskKind.NOVEL_EPISODE_SCRIPT.value, EpisodeTaskPlanAction.REUSED, f"已有分场剧本任务处于 {script_task.status.value} 状态", [script_task.id]), [script_task.id]
        if script is None:
            if script_task is not None:
                return self._item(episode, GenerationTaskKind.NOVEL_EPISODE_SCRIPT.value, EpisodeTaskPlanAction.BLOCKED, "分场剧本任务已成功但没有找到剧本 Artifact，需人工检查任务记录", [], ["EPISODE_SCRIPT_ARTIFACT_MISSING"]), []
            task, reused = await self._novel_task_service.create_episode_script_task(
                episode.id,
                self._task_key(idempotency_key, "script", episode.id),
                idempotency_key,
            )
            return self._item(episode, GenerationTaskKind.NOVEL_EPISODE_SCRIPT.value, EpisodeTaskPlanAction.REUSED if reused else EpisodeTaskPlanAction.CREATED, "复用已有分场剧本任务" if reused else "已创建分场剧本任务", [task.id]), [task.id]

        shot_task = self._latest_task(project_tasks, GenerationTaskKind.NOVEL_SHOT_LIST, episode.id)
        shot_list = await self._store.get_latest_shot_list(episode.id)
        if shot_task is not None and shot_task.status != TaskStatus.SUCCEEDED:
            return self._item(episode, GenerationTaskKind.NOVEL_SHOT_LIST.value, EpisodeTaskPlanAction.REUSED, f"已有分镜任务处于 {shot_task.status.value} 状态", [shot_task.id]), [shot_task.id]
        if shot_list is None:
            if shot_task is not None:
                return self._item(episode, GenerationTaskKind.NOVEL_SHOT_LIST.value, EpisodeTaskPlanAction.BLOCKED, "分镜任务已成功但没有找到分镜 Artifact，需人工检查任务记录", [], ["SHOT_LIST_ARTIFACT_MISSING"]), []
            task, reused = await self._novel_task_service.create_shot_list_task(
                episode.id,
                self._task_key(idempotency_key, "shots", episode.id),
                idempotency_key,
            )
            return self._item(episode, GenerationTaskKind.NOVEL_SHOT_LIST.value, EpisodeTaskPlanAction.REUSED if reused else EpisodeTaskPlanAction.CREATED, "复用已有分镜任务" if reused else "已创建分镜任务", [task.id]), [task.id]

        # Backwards-compatible content-only behavior.
        if not request.production_mode:
            return self._item(episode, None, EpisodeTaskPlanAction.SKIPPED, "分场剧本和分镜均已生成；production_mode 未开启", []), []

        # Keep the last completed production stage in the response when an
        # automatic DAG tick already finished the requested work between two
        # planner calls. A skipped response should still tell the UI what was
        # actually completed instead of returning a misleading null stage.
        completed_stage: str | None = None

        if request.include_reference_images:
            reference_item, reference_task_ids, ready_references, blocked = await self._plan_reference_images(
                episode, shot_list.shots, project_tasks, idempotency_key,
            )
            if reference_item is not None:
                return reference_item, reference_task_ids
        else:
            ready_references, blocked = {}, []
        if blocked:
            return self._item(episode, GenerationTaskKind.ASSET_REFERENCE_IMAGE.value, EpisodeTaskPlanAction.BLOCKED, "参考图阶段被阻塞，请先处理资产或参考图 Artifact", [], blocked), []
        if ready_references:
            completed_stage = GenerationTaskKind.ASSET_REFERENCE_IMAGE.value

        if request.include_narration:
            narration_item, narration_task_ids, narration_artifact, blocked = await self._plan_narration(
                episode, script, project_tasks, idempotency_key,
            )
            if narration_item is not None:
                return narration_item, narration_task_ids
            if blocked:
                return self._item(episode, GenerationTaskKind.AUDIO_NARRATION.value, EpisodeTaskPlanAction.BLOCKED, "旁白阶段被阻塞，请检查音频任务和 Artifact", [], blocked), []
            if narration_artifact is not None:
                completed_stage = GenerationTaskKind.AUDIO_NARRATION.value
        else:
            narration_artifact = None

        subtitle_artifact = None
        if request.include_subtitles:
            if narration_artifact is None:
                return self._item(episode, self._subtitle_kind(request), EpisodeTaskPlanAction.BLOCKED, "字幕依赖已完成的旁白音频", [], ["AUDIO_NARRATION_REQUIRED"]), []
            subtitle_item, subtitle_task_ids, subtitle_artifact, blocked = await self._plan_subtitles(
                episode, request, project_tasks, narration_artifact, script, idempotency_key,
            )
            if subtitle_item is not None:
                return subtitle_item, subtitle_task_ids
            if blocked:
                return self._item(episode, self._subtitle_kind(request), EpisodeTaskPlanAction.BLOCKED, "字幕阶段被阻塞，请检查字幕 Provider 或音频 Artifact", [], blocked), []
            if subtitle_artifact is not None:
                completed_stage = self._subtitle_kind(request)

        bgm_artifact = None
        if request.include_bgm:
            bgm_item, bgm_task_ids, bgm_artifact, blocked = await self._plan_bgm(
                episode, project_tasks, request, idempotency_key,
            )
            if bgm_item is not None:
                return bgm_item, bgm_task_ids
            if blocked:
                return self._item(episode, GenerationTaskKind.AUDIO_BGM.value, EpisodeTaskPlanAction.BLOCKED, "BGM 阶段被阻塞，请检查本地授权音频配置", [], blocked), []
            if bgm_artifact is not None:
                completed_stage = GenerationTaskKind.AUDIO_BGM.value

        video_clip_tasks: list[GenerationTaskRecord] = []
        if request.include_video:
            video_item, video_task_ids, video_clip_tasks, blocked = await self._plan_video_clips(
                episode, shot_list.shots, project_tasks, ready_references, idempotency_key,
            )
            if video_item is not None:
                return video_item, video_task_ids
            if blocked:
                return self._item(episode, GenerationTaskKind.VIDEO_CLIP.value, EpisodeTaskPlanAction.BLOCKED, "视频片段阶段被阻塞，请先完成分镜资产审核", [], blocked), []
            if video_clip_tasks:
                completed_stage = GenerationTaskKind.VIDEO_CLIP.value
        if not request.include_assembly:
            completed_task_ids = self._completed_task_ids(
                completed_stage,
                project_tasks,
                episode.id,
                len(shot_list.shots),
            )
            return self._item(episode, completed_stage, EpisodeTaskPlanAction.SKIPPED, "当前配置已完成，include_assembly 未开启", completed_task_ids), completed_task_ids
        if not request.include_video:
            return self._item(episode, GenerationTaskKind.VIDEO_ASSEMBLY.value, EpisodeTaskPlanAction.BLOCKED, "成片合成依赖视频片段", [], ["VIDEO_CLIPS_REQUIRED"]), []
        if not video_clip_tasks:
            video_clip_tasks = self._successful_video_clip_tasks(project_tasks, episode.id, len(shot_list.shots))
        if len(video_clip_tasks) != len(shot_list.shots):
            return self._item(episode, GenerationTaskKind.VIDEO_ASSEMBLY.value, EpisodeTaskPlanAction.BLOCKED, "成片合成需要所有镜头片段成功", [], ["VIDEO_CLIPS_NOT_READY"]), []

        assembly_item, assembly_task_ids, blocked = await self._plan_assembly(
            episode, project_tasks, request, video_clip_tasks,
            narration_artifact, bgm_artifact, subtitle_artifact, idempotency_key,
        )
        if assembly_item is not None:
            return assembly_item, assembly_task_ids
        if not blocked:
            completed_task_ids = self._completed_task_ids(
                GenerationTaskKind.VIDEO_ASSEMBLY.value,
                project_tasks,
                episode.id,
                len(shot_list.shots),
            )
            return self._item(
                episode,
                GenerationTaskKind.VIDEO_ASSEMBLY.value,
                EpisodeTaskPlanAction.SKIPPED,
                "成片合成已完成，复用现有 Artifact",
                completed_task_ids,
            ), completed_task_ids
        return self._item(episode, GenerationTaskKind.VIDEO_ASSEMBLY.value, EpisodeTaskPlanAction.BLOCKED, "成片合成阶段被阻塞，请检查可选音频、字幕和片段 Artifact", blocked), []

    async def _plan_reference_images(self, episode, shots, project_tasks, idempotency_key):
        assets = await self._store.list_assets(episode.project_id)
        by_key = {(asset.asset_type, asset.asset_key): asset for asset in assets}
        referenced: dict[UUID, AssetRecord] = {}
        blocked: list[str] = []
        for shot in shots:
            if shot.unresolved_asset_requirements or shot.asset_binding_warnings:
                blocked.extend(shot.unresolved_asset_requirements)
                blocked.extend(shot.asset_binding_warnings)
            for reference in shot.asset_refs:
                asset = by_key.get((reference.asset_type, reference.asset_key))
                if asset is None:
                    blocked.append(f"{reference.name}:asset_not_found")
                elif asset.status != AssetStatus.READY:
                    blocked.append(f"{reference.name}:asset_status={asset.status.value}")
                else:
                    referenced[asset.asset_key] = asset
        if blocked:
            return None, [], {}, sorted(set(blocked))

        ready: dict[UUID, ReferenceImageRecord] = {}
        pending: list[UUID] = []
        created = 0
        for asset in referenced.values():
            images = await self._store.list_reference_images(asset.id)
            successful = self._preferred_reference_image(images, asset.asset_type)
            if successful is not None:
                ready[asset.asset_key] = successful
                continue
            task = self._latest_reference_task(project_tasks, asset.id, asset.version)
            if task is not None and task.status != TaskStatus.SUCCEEDED:
                pending.append(task.id)
                continue
            if task is not None:
                blocked.append(f"{asset.name}:reference_image_artifact_missing")
                continue
            created_task, reused = await self._reference_image_task_service.create_task(
                asset.id,
                ReferenceImageCreateRequest(),
                self._task_key(idempotency_key, "reference", asset.id),
            )
            pending.append(created_task.id)
            if not reused:
                created += 1
        if pending:
            action = EpisodeTaskPlanAction.CREATED if created else EpisodeTaskPlanAction.REUSED
            return self._item(episode, GenerationTaskKind.ASSET_REFERENCE_IMAGE.value, action, f"参考图阶段待处理 {len(pending)} 个资产", pending), pending, ready, []
        return None, [], ready, blocked

    async def _plan_narration(self, episode, script, project_tasks, idempotency_key):
        text = self._script_text(script)
        if not text:
            return None, [], None, ["NARRATION_TEXT_EMPTY"]
        task = self._latest_task(project_tasks, GenerationTaskKind.AUDIO_NARRATION, episode.id)
        artifact = self._task_artifact(task, "audio_narration") if task else None
        if task is not None and task.status != TaskStatus.SUCCEEDED:
            return self._item(episode, GenerationTaskKind.AUDIO_NARRATION.value, EpisodeTaskPlanAction.REUSED, f"已有旁白任务处于 {task.status.value} 状态", [task.id]), [task.id], None, []
        if task is not None and artifact is None:
            return None, [], None, ["AUDIO_NARRATION_ARTIFACT_MISSING"]
        if artifact is not None:
            return None, [], artifact, []
        created, reused = await self._tts_task_service.create_task(
            episode.id,
            AudioNarrationCreateRequest(text=text),
            self._task_key(idempotency_key, "narration", episode.id),
        )
        return self._item(episode, GenerationTaskKind.AUDIO_NARRATION.value, EpisodeTaskPlanAction.REUSED if reused else EpisodeTaskPlanAction.CREATED, "复用已有旁白任务" if reused else "已创建本集旁白任务", [created.id]), [created.id], None, []

    async def _plan_subtitles(self, episode, request, project_tasks, narration_artifact, script, idempotency_key):
        kind = GenerationTaskKind.SUBTITLE_ASR if request.subtitle_mode == "asr" else GenerationTaskKind.SUBTITLE_ALIGN
        other_kind = GenerationTaskKind.SUBTITLE_ALIGN if kind == GenerationTaskKind.SUBTITLE_ASR else GenerationTaskKind.SUBTITLE_ASR
        task = self._latest_task(project_tasks, kind, episode.id)
        other_task = self._latest_task(project_tasks, other_kind, episode.id)
        artifact = self._task_artifact(task, "subtitle_srt") if task else None
        if artifact is None and other_task is not None and other_task.status == TaskStatus.SUCCEEDED:
            artifact = self._task_artifact(other_task, "subtitle_srt")
            if artifact is not None:
                return None, [], artifact, []
        if task is not None and task.status != TaskStatus.SUCCEEDED:
            return self._item(episode, kind.value, EpisodeTaskPlanAction.REUSED, f"已有字幕任务处于 {task.status.value} 状态", [task.id]), [task.id], None, []
        if task is not None and artifact is None:
            return None, [], None, ["SUBTITLE_ARTIFACT_MISSING"]
        if artifact is not None:
            return None, [], artifact, []
        text = self._script_text(script)
        if request.subtitle_mode == "asr":
            try:
                created, reused = await self._subtitle_task_service.create_asr_task(
                    episode.id,
                    SubtitleASRCreateRequest(
                        audio_artifact_id=narration_artifact.id,
                        language="zh-CN",
                        reference_text=text,
                        provider_profile_id=request.provider_profile_id,
                    ),
                    self._task_key(idempotency_key, "subtitle-asr", episode.id),
                )
            except ASRProviderProfileError as exc:
                return None, [], None, [exc.code]
        else:
            duration = self._artifact_duration(narration_artifact)
            if duration <= 0:
                return None, [], None, ["AUDIO_DURATION_REQUIRED_FOR_SUBTITLE"]
            created, reused = await self._subtitle_task_service.create_alignment_task(
                episode.id,
                SubtitleAlignmentCreateRequest(text=text, language="zh-CN", audio_duration_seconds=duration),
                self._task_key(idempotency_key, "subtitle-align", episode.id),
            )
        return self._item(episode, kind.value, EpisodeTaskPlanAction.REUSED if reused else EpisodeTaskPlanAction.CREATED, "复用已有字幕任务" if reused else "已创建字幕任务", [created.id]), [created.id], None, []

    async def _plan_bgm(self, episode, project_tasks, request, idempotency_key):
        task = self._latest_task(project_tasks, GenerationTaskKind.AUDIO_BGM, episode.id)
        artifact = self._task_artifact(task, "audio_bgm") if task else None
        if task is not None and task.status != TaskStatus.SUCCEEDED:
            return self._item(episode, GenerationTaskKind.AUDIO_BGM.value, EpisodeTaskPlanAction.REUSED, f"已有 BGM 任务处于 {task.status.value} 状态", [task.id]), [task.id], None, []
        if task is not None and artifact is None:
            return None, [], None, ["AUDIO_BGM_ARTIFACT_MISSING"]
        if artifact is not None:
            return None, [], artifact, []
        created, reused = await self._bgm_task_service.create_task(
            episode.id,
            AudioBGMCreateRequest(
                source_path=request.bgm_source_path,
                label=request.bgm_label,
                rights_status=request.bgm_rights_status,
                rights_holder=request.bgm_rights_holder,
                rights_reference=request.bgm_rights_reference,
            ),
            self._task_key(idempotency_key, "bgm", episode.id),
        )
        return self._item(episode, GenerationTaskKind.AUDIO_BGM.value, EpisodeTaskPlanAction.REUSED if reused else EpisodeTaskPlanAction.CREATED, "复用已有 BGM 任务" if reused else "已创建 BGM 任务", [created.id]), [created.id], None, []

    async def _plan_video_clips(self, episode, shots, project_tasks, references, idempotency_key):
        blocked: list[str] = []
        successful: list[GenerationTaskRecord] = []
        pending: list[UUID] = []
        created = 0
        for shot in shots:
            if not self._shot_ready(shot):
                blocked.append(f"shot_{shot.shot_index}:asset_gate")
                continue
            task = self._latest_video_clip_task(project_tasks, episode.id, shot.shot_index)
            if task is not None and task.status == TaskStatus.SUCCEEDED:
                if self._task_artifact(task, "video_clip") is None:
                    blocked.append(f"shot_{shot.shot_index}:video_artifact_missing")
                else:
                    successful.append(task)
                continue
            if task is not None:
                pending.append(task.id)
                continue
            reference_image_id = self._reference_image_id_for_shot(shot, references)
            created_task, reused = await self._video_clip_task_service.create_task(
                episode.id,
                shot.shot_index,
                VideoClipCreateRequest(reference_image_id=reference_image_id),
                self._task_key(idempotency_key, f"video-{shot.shot_index}", episode.id),
            )
            pending.append(created_task.id)
            if not reused:
                created += 1
        if blocked:
            return None, [], successful, sorted(set(blocked))
        if pending:
            action = EpisodeTaskPlanAction.CREATED if created else EpisodeTaskPlanAction.REUSED
            return self._item(episode, GenerationTaskKind.VIDEO_CLIP.value, action, f"视频片段阶段待处理 {len(pending)} 个镜头", pending), pending, successful, []
        return None, [], successful, []

    async def _plan_assembly(self, episode, project_tasks, request, video_clip_tasks, narration_artifact, bgm_artifact, subtitle_artifact, idempotency_key):
        blocked = []
        if request.include_narration and narration_artifact is None:
            blocked.append("AUDIO_NARRATION_REQUIRED")
        if request.include_bgm and bgm_artifact is None:
            blocked.append("AUDIO_BGM_REQUIRED")
        if request.include_subtitles and subtitle_artifact is None:
            blocked.append("SUBTITLE_REQUIRED")
        if blocked:
            return None, [], blocked
        task = self._latest_task(project_tasks, GenerationTaskKind.VIDEO_ASSEMBLY, episode.id)
        artifact = self._task_artifact(task, "rendered_video") if task else None
        if task is not None and task.status != TaskStatus.SUCCEEDED:
            return self._item(episode, GenerationTaskKind.VIDEO_ASSEMBLY.value, EpisodeTaskPlanAction.REUSED, f"已有成片合成任务处于 {task.status.value} 状态", [task.id]), [task.id], []
        if task is not None and artifact is None:
            return None, [], ["VIDEO_ASSEMBLY_ARTIFACT_MISSING"]
        if artifact is not None:
            return None, [], []
        tracks = []
        if narration_artifact is not None:
            tracks.append({"artifact_id": narration_artifact.id, "track_type": "narration"})
        if bgm_artifact is not None:
            tracks.append({"artifact_id": bgm_artifact.id, "track_type": "bgm", "volume": 0.18, "loop": True, "fade_in_seconds": 1, "fade_out_seconds": 2})
        created, reused = await self._video_assembly_task_service.create_task(
            episode.id,
            VideoAssemblyCreateRequest(
                clip_task_ids=[task.id for task in sorted(video_clip_tasks, key=self._video_clip_sort_key)],
                audio_tracks=tracks,
                subtitle_artifact_id=subtitle_artifact.id if subtitle_artifact is not None else None,
            ),
            self._task_key(idempotency_key, "assembly", episode.id),
        )
        return self._item(episode, GenerationTaskKind.VIDEO_ASSEMBLY.value, EpisodeTaskPlanAction.REUSED if reused else EpisodeTaskPlanAction.CREATED, "复用已有成片任务" if reused else "已创建成片合成任务", [created.id]), [created.id], []

    @staticmethod
    def _item(episode, stage, action, reason, task_ids, blocked_reasons=None):
        return EpisodeTaskPlanItem(
            episode_id=episode.id,
            episode_number=episode.episode_number,
            stage=stage,
            task_id=task_ids[0] if task_ids else None,
            task_ids=task_ids,
            action=action,
            reason=reason,
            blocked_reasons=blocked_reasons or [],
        )

    @staticmethod
    def _task_artifact(task, artifact_type):
        if task is None:
            return None
        return next((artifact for artifact in task.artifacts if artifact.type == artifact_type), None)

    @staticmethod
    def _artifact_duration(artifact):
        try:
            value = float(artifact.metadata.get("duration_seconds"))
        except (TypeError, ValueError):
            return 0.0
        return value if value > 0 else 0.0

    @staticmethod
    def _script_text(script):
        return build_episode_narration_text(script)[:5000]

    @staticmethod
    def _shot_ready(shot: ShotContent) -> bool:
        return not (shot.unresolved_asset_requirements or shot.asset_binding_warnings or any(reference.status != AssetStatus.READY for reference in shot.asset_refs))

    @staticmethod
    def _subtitle_kind(request):
        return GenerationTaskKind.SUBTITLE_ASR.value if request.subtitle_mode == "asr" else GenerationTaskKind.SUBTITLE_ALIGN.value

    @staticmethod
    def _reference_image_id_for_shot(shot, references):
        ordered_refs = sorted(
            shot.asset_refs,
            key=lambda item: 0 if item.asset_type == AssetType.CHARACTER else 1,
        )
        for asset in ordered_refs:
            image = references.get(asset.asset_key)
            if image is not None:
                return image.id
        return None

    @staticmethod
    def _preferred_reference_image(images, asset_type):
        successful = [
            image
            for image in images
            if image.status == ReferenceImageStatus.SUCCEEDED
            and isinstance(image.metadata.get("storage_key"), str)
            and image.metadata.get("storage_key")
        ]
        if not successful:
            return None
        if asset_type == AssetType.CHARACTER:
            anchor = next(
                (image for image in successful if image.metadata.get("identity_anchor") is True),
                None,
            )
            if anchor is not None:
                return anchor
            legacy = sorted(successful, key=lambda image: image.created_at)
            candidate = next(
                (
                    image
                    for image in legacy
                    if image.metadata.get("reference_role") != "identity_locked_variant"
                ),
                None,
            )
            if candidate is not None:
                return candidate
        return successful[0]

    @staticmethod
    def _latest_task(tasks, kind, episode_id):
        matching = [task for task in tasks if task.kind == kind and str(task.input_data.get("episode_id")) == str(episode_id)]
        matching.sort(key=lambda task: task.updated_at, reverse=True)
        return matching[0] if matching else None

    @classmethod
    def _completed_task_ids(
        cls,
        stage: str | None,
        tasks: list[GenerationTaskRecord],
        episode_id: UUID,
        shot_count: int,
    ) -> list[UUID]:
        """Return completed task IDs for a skipped production stage.

        Keeping these IDs in the response makes an automatic completion
        observable to clients that asked for the same stage just after the
        Worker advanced it. The generated batch is informational and does not
        re-enqueue a succeeded task.
        """

        if stage is None:
            return []
        stage_kind = {
            GenerationTaskKind.ASSET_REFERENCE_IMAGE.value: GenerationTaskKind.ASSET_REFERENCE_IMAGE,
            GenerationTaskKind.AUDIO_NARRATION.value: GenerationTaskKind.AUDIO_NARRATION,
            GenerationTaskKind.SUBTITLE_ALIGN.value: GenerationTaskKind.SUBTITLE_ALIGN,
            GenerationTaskKind.SUBTITLE_ASR.value: GenerationTaskKind.SUBTITLE_ASR,
            GenerationTaskKind.AUDIO_BGM.value: GenerationTaskKind.AUDIO_BGM,
            GenerationTaskKind.VIDEO_ASSEMBLY.value: GenerationTaskKind.VIDEO_ASSEMBLY,
        }.get(stage)
        if stage_kind is not None:
            matching = [
                task
                for task in tasks
                if task.kind == stage_kind
                and str(task.input_data.get("episode_id")) == str(episode_id)
                and task.status == TaskStatus.SUCCEEDED
                and task.artifacts
            ]
            matching.sort(key=lambda task: task.updated_at, reverse=True)
            if matching:
                return [matching[0].id]
            return []
        if stage == GenerationTaskKind.VIDEO_CLIP.value:
            matching = [
                task
                for task in tasks
                if task.kind == GenerationTaskKind.VIDEO_CLIP
                and str(task.input_data.get("episode_id")) == str(episode_id)
                and task.status == TaskStatus.SUCCEEDED
                and cls._task_artifact(task, "video_clip") is not None
            ]
            matching.sort(key=cls._video_clip_sort_key)
            return [task.id for task in matching[:shot_count]]
        return []

    @staticmethod
    def _latest_video_clip_task(tasks, episode_id, shot_index):
        matching = [task for task in tasks if task.kind == GenerationTaskKind.VIDEO_CLIP and str(task.input_data.get("episode_id")) == str(episode_id) and int(task.input_data.get("shot_index", 0)) == shot_index]
        matching.sort(key=lambda task: task.updated_at, reverse=True)
        return matching[0] if matching else None

    @staticmethod
    def _latest_reference_task(tasks, asset_id, asset_version):
        matching = [task for task in tasks if task.kind == GenerationTaskKind.ASSET_REFERENCE_IMAGE and str(task.input_data.get("asset_id")) == str(asset_id) and int(task.input_data.get("asset_version", asset_version)) == asset_version]
        matching.sort(key=lambda task: task.updated_at, reverse=True)
        return matching[0] if matching else None

    @staticmethod
    def _latest_planned_task(tasks, episode_id, plan_key):
        if not plan_key:
            return None
        matching = [
            task
            for task in tasks
            if str(task.input_data.get("episode_id")) == str(episode_id)
            and task.input_data.get("episode_task_plan_key") == plan_key
            and task.kind in {
                GenerationTaskKind.NOVEL_EPISODE_SCRIPT,
                GenerationTaskKind.NOVEL_SHOT_LIST,
            }
        ]
        matching.sort(key=lambda task: task.updated_at, reverse=True)
        return matching[0] if matching else None

    @classmethod
    def _successful_video_clip_tasks(cls, tasks, episode_id, shot_count):
        result = []
        for shot_index in range(1, shot_count + 1):
            task = cls._latest_video_clip_task(tasks, episode_id, shot_index)
            if task is not None and task.status == TaskStatus.SUCCEEDED and cls._task_artifact(task, "video_clip") is not None:
                result.append(task)
        return result

    @staticmethod
    def _video_clip_sort_key(task):
        return int(task.input_data.get("shot_index", 0))

    @staticmethod
    def _task_key(idempotency_key, stage, subject_id):
        prefix = idempotency_key.strip() if idempotency_key and idempotency_key.strip() else "stable"
        return f"episode-plan:{prefix}:{stage}:{subject_id}"
