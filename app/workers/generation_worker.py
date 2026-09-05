"""Redis-backed generation Worker.

Run with:

    AI_VIDEO_PERSISTENCE_BACKEND=postgres \
    AI_VIDEO_QUEUE_BACKEND=redis \
    uv run python -m app.workers.generation_worker
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID

from app.config import load_settings
from app.db import create_engine, create_session_factory, init_db
from app.domain.models import GenerationTaskKind, TaskStatus
from app.media.audio_validation import FFprobeAudioValidator
from app.media.audio_normalization import FFmpegAudioNormalizer
from app.media.video_validation import FFprobeVideoValidator
from app.media.identity_audit import IdentityConsistencyAuditor
from app.media.pronunciation import load_pronunciation_dictionary
from app.providers.factory import (
    create_image_generation_provider,
    create_identity_image_generation_provider,
    create_lip_sync_provider,
    create_bgm_provider,
    create_novel_pipeline_provider,
    create_story_bible_provider,
    create_subtitle_alignment_provider,
    create_tts_provider,
    create_text_provider,
    create_video_generation_provider,
)
from app.queue import RedisLeaseLock, RedisTaskQueue, maybe_gpu_lease
from app.repositories.sqlalchemy import SqlAlchemyStore
from app.services.novel_service import NovelService
from app.services.novel_task_service import NovelGenerationTaskService
from app.services.task_service import TaskService
from app.services.task_batch_service import TaskBatchService
from app.services.reference_image_service import ReferenceImageTaskService
from app.services.video_clip_service import VideoClipTaskService
from app.services.video_assembly_service import VideoAssemblyTaskService
from app.services.tts_service import TTSTaskService
from app.services.bgm_service import BGMTaskService
from app.services.subtitle_service import SubtitleTaskService
from app.services.lip_sync_service import LipSyncTaskService
from app.services.voice_asset_service import VoiceAssetService
from app.services.episode_task_plan_service import EpisodeTaskPlanService
from app.services.production_orchestrator import ProductionOrchestrator
from app.services.temp_cleanup_service import TemporaryDirectoryCleanupService
from app.providers.profiles import ASRProviderProfileRegistry
from app.storage.factory import create_artifact_storage
from app.rendering.ffmpeg_renderer import FFmpegVideoRenderer

logger = logging.getLogger(__name__)


async def run_worker() -> None:
    settings = load_settings()
    if settings.persistence_backend != "postgres":
        raise RuntimeError("Redis Worker requires AI_VIDEO_PERSISTENCE_BACKEND=postgres")
    if settings.queue_backend != "redis":
        raise RuntimeError("Redis Worker requires AI_VIDEO_QUEUE_BACKEND=redis")

    engine = create_engine(settings.database_url)
    session_factory = create_session_factory(engine)
    store = SqlAlchemyStore(session_factory)
    queue = RedisTaskQueue(settings.redis_url, settings.queue_name)
    gpu_lock = (
        RedisLeaseLock(
            settings.redis_url,
            settings.worker_gpu_lock_key,
            ttl_seconds=settings.worker_gpu_lock_ttl_seconds,
            wait_seconds=settings.worker_gpu_lock_wait_seconds,
        )
        if settings.worker_gpu_lock_enabled
        else None
    )
    text_provider = create_text_provider(settings)
    story_bible_provider = create_story_bible_provider(settings)
    novel_pipeline_provider = create_novel_pipeline_provider(settings)
    image_provider = create_image_generation_provider(settings)
    identity_image_provider = create_identity_image_generation_provider(settings)
    video_provider = create_video_generation_provider(settings)
    tts_provider = create_tts_provider(settings)
    lip_sync_provider = create_lip_sync_provider(settings)
    bgm_provider = create_bgm_provider(settings)
    subtitle_alignment_provider = create_subtitle_alignment_provider(settings)
    asr_profile_registry = ASRProviderProfileRegistry(settings)
    artifact_storage = create_artifact_storage(settings)
    novel_service = NovelService(
        store,
        story_bible_provider,
        novel_pipeline_provider,
    )
    novel_task_service = NovelGenerationTaskService(store, novel_service, queue)
    voice_asset_service = VoiceAssetService(store)
    reference_image_task_service = ReferenceImageTaskService(
        store,
        queue,
        image_provider,
        artifact_storage,
        identity_provider=identity_image_provider,
        default_width=settings.image_width,
        default_height=settings.image_height,
    )
    video_clip_task_service = VideoClipTaskService(
        store,
        queue,
        video_provider,
        artifact_storage,
        video_validator=FFprobeVideoValidator(
            timeout_seconds=settings.video_probe_timeout_seconds
        ),
        identity_auditor=(
            IdentityConsistencyAuditor(
                python_path=settings.identity_audit_python_path,
                script_path=settings.identity_audit_script_path,
                model_root=settings.identity_audit_model_root,
                threshold=settings.identity_audit_threshold,
                frame_count=settings.identity_audit_frame_count,
                timeout_seconds=settings.identity_audit_timeout_seconds,
            )
            if settings.identity_audit_enabled
            else None
        ),
    )
    video_assembly_task_service = VideoAssemblyTaskService(
        store,
        queue,
        artifact_storage,
        renderer=FFmpegVideoRenderer(
            binary=settings.video_binary,
            timeout_seconds=settings.task_timeout_seconds,
        ),
        audio_validator=FFprobeAudioValidator(
            timeout_seconds=settings.tts_probe_timeout_seconds
        ),
    )
    lip_sync_task_service = LipSyncTaskService(
        store,
        queue,
        lip_sync_provider,
        artifact_storage,
        video_validator=FFprobeVideoValidator(
            timeout_seconds=settings.video_probe_timeout_seconds
        ),
    )
    tts_task_service = TTSTaskService(
        store,
        queue,
        tts_provider,
        artifact_storage,
        audio_validator=FFprobeAudioValidator(timeout_seconds=settings.tts_probe_timeout_seconds),
        default_voice=settings.tts_voice,
        default_rate=settings.tts_rate,
        default_volume=settings.tts_volume,
        max_text_characters=settings.tts_max_text_characters,
        pronunciation_dictionary=load_pronunciation_dictionary(
            settings.tts_pronunciation_dictionary_path
        ),
        voice_asset_service=voice_asset_service,
        configured_provider=settings.tts_provider,
        multi_voice_ffmpeg_binary=settings.tts_ffmpeg_binary,
        multi_voice_timeout_seconds=settings.tts_multi_voice_timeout_seconds,
    )
    bgm_task_service = BGMTaskService(
        store,
        queue,
        bgm_provider,
        artifact_storage,
        audio_validator=FFprobeAudioValidator(timeout_seconds=settings.tts_probe_timeout_seconds),
        audio_normalizer=(
            FFmpegAudioNormalizer(timeout_seconds=settings.bgm_normalization_timeout_seconds)
            if settings.bgm_normalize_audio
            else None
        ),
    )
    subtitle_task_service = SubtitleTaskService(
        store,
        queue,
        artifact_storage,
        alignment_provider=subtitle_alignment_provider,
        asr_profile_registry=asr_profile_registry,
    )
    service = TaskService(
        store,
        text_provider,
        queue,
        novel_task_runner=novel_task_service.run_task,
        reference_image_task_runner=reference_image_task_service.run_task,
        video_clip_task_runner=video_clip_task_service.run_task,
        video_assembly_task_runner=video_assembly_task_service.run_task,
        tts_task_runner=tts_task_service.run_task,
        bgm_task_runner=bgm_task_service.run_task,
        subtitle_task_runner=subtitle_task_service.run_task,
        lip_sync_task_runner=lip_sync_task_service.run_task,
    )

    task_batch_service = __import__(
        "app.services.task_batch_service",
        fromlist=["TaskBatchService"],
    ).TaskBatchService(store, service.retry_task)
    episode_task_plan_service = EpisodeTaskPlanService(
        store,
        novel_service,
        novel_task_service,
        task_batch_service,
        reference_image_task_service,
        tts_task_service,
        subtitle_task_service,
        bgm_task_service,
        video_clip_task_service,
        video_assembly_task_service,
    )
    production_orchestrator = ProductionOrchestrator(store, episode_task_plan_service)
    cleanup_service = TemporaryDirectoryCleanupService(
        settings.worker_cleanup_roots,
        storage_base_path=settings.storage_base_path,
        max_age_hours=settings.worker_cleanup_max_age_hours,
        max_files_per_run=settings.worker_cleanup_max_files_per_run,
        min_free_gb=settings.worker_cleanup_min_free_gb,
    )

    heartbeat_task: asyncio.Task[None] | None = None
    cleanup_task: asyncio.Task[None] | None = None
    scheduler_task: asyncio.Task[None] | None = None

    try:
        await init_db(engine)
        recovered = await queue.recover_expired(
            settings.worker_stale_task_timeout_seconds,
            requeue=False,
        )
        recovered_stale = await _recover_stale_tasks(
            store,
            service,
            settings.worker_stale_task_timeout_seconds,
        )
        for task_id in recovered_stale:
            stale_task = await store.get_task(task_id)
            if stale_task is not None:
                await _maybe_auto_retry(stale_task, service, settings)
        await _resume_pending_auto_retries(store, service, settings)
        recovered_requeued = await _requeue_recovered_claims(store, queue, recovered)
        requeued = await _requeue_queued_tasks(store, queue)
        logger.info(
            "generation worker started queue=%s recovered_claims=%d recovered_tasks=%d recovered_requeued=%d requeued=%d",
            settings.queue_name,
            len(recovered),
            len(recovered_stale),
            recovered_requeued,
            requeued,
        )
        heartbeat_task = asyncio.create_task(_heartbeat_loop(queue, settings))
        cleanup_task = asyncio.create_task(
            _cleanup_loop(cleanup_service, queue, settings)
        )
        scheduler_task = asyncio.create_task(
            _scheduler_loop(
                production_orchestrator,
                store,
                service,
                queue,
                settings,
            )
        )
        while True:
            task_id = await queue.dequeue()
            if task_id is None:
                continue
            task = await store.get_task(task_id)
            if task is None or task.status not in {TaskStatus.CREATED, TaskStatus.QUEUED}:
                await queue.ack(task_id)
                continue
            logger.info("processing generation task task_id=%s", task_id)
            await queue.heartbeat(
                {"status": "online", "current_task_id": str(task_id), "current_kind": task.kind.value}
            )
            try:
                async with maybe_gpu_lease(
                    gpu_lock if _requires_gpu(task.kind, settings) else None
                ) as acquired:
                    if not acquired:
                        await queue.ack(task_id)
                        await asyncio.sleep(1)
                        await queue.enqueue(task_id)
                        continue
                    await asyncio.wait_for(
                        service.run_task(task_id), timeout=settings.task_timeout_seconds
                    )
            except Exception as exc:
                logger.exception("generation task failed task_id=%s", task_id)
                await service.mark_failed(task_id, _worker_error_code(exc), str(exc))
            finally:
                await queue.ack(task_id)
                await queue.heartbeat({"status": "online", "current_task_id": ""})

            finished = await store.get_task(task_id)
            if finished is None:
                continue
            if finished.status == TaskStatus.FAILED:
                await _maybe_auto_retry(finished, service, settings)
            elif finished.status == TaskStatus.SUCCEEDED and settings.worker_scheduler_enabled:
                try:
                    await production_orchestrator.on_task_finished(task_id)
                except Exception:
                    logger.exception("automatic DAG advancement failed task_id=%s", task_id)
    finally:
        for background_task in (heartbeat_task, cleanup_task, scheduler_task):
            if background_task is not None:
                background_task.cancel()
        for background_task in (heartbeat_task, cleanup_task, scheduler_task):
            if background_task is not None:
                await asyncio.gather(background_task, return_exceptions=True)
        if gpu_lock is not None:
            await gpu_lock.close()
        await queue.close()
        close_provider = getattr(text_provider, "close", None)
        if close_provider is not None:
            await close_provider()
        for provider in (
            story_bible_provider,
            novel_pipeline_provider,
            image_provider,
            identity_image_provider,
            video_provider,
            tts_provider,
            lip_sync_provider,
            bgm_provider,
            subtitle_alignment_provider,
        ):
            close_provider = getattr(provider, "close", None)
            if close_provider is not None:
                await close_provider()
        await asr_profile_registry.close()
        close_storage = getattr(artifact_storage, "close", None)
        if close_storage is not None:
            await close_storage()
        await engine.dispose()


def _requires_gpu(kind: GenerationTaskKind, settings) -> bool:
    gpu_kinds = {
        GenerationTaskKind.ASSET_REFERENCE_IMAGE,
        GenerationTaskKind.VIDEO_CLIP,
        GenerationTaskKind.LIP_SYNC,
    }
    if settings.tts_provider == "chattts":
        gpu_kinds.add(GenerationTaskKind.AUDIO_NARRATION)
    return kind in gpu_kinds


async def _recover_stale_tasks(store, service, timeout_seconds: int) -> list[UUID]:
    tasks = await store.list_tasks(status=TaskStatus.RUNNING.value, limit=5000)
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=max(1, timeout_seconds))
    recovered: list[UUID] = []
    for task in tasks:
        updated_at = task.updated_at
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=timezone.utc)
        if updated_at >= cutoff:
            continue
        await service.mark_failed(
            task.id,
            "WORKER_STALE_TASK_RECOVERED",
            "Worker restarted after the task heartbeat expired; task is retryable",
        )
        recovered.append(task.id)
    return recovered


async def _requeue_recovered_claims(
    store,
    queue: RedisTaskQueue,
    task_ids: list[UUID],
) -> int:
    """Requeue only recovered messages whose durable task is still runnable."""

    requeued = 0
    for task_id in task_ids:
        task = await store.get_task(task_id)
        if task is None or task.status not in {TaskStatus.CREATED, TaskStatus.QUEUED}:
            continue
        await queue.enqueue(task_id)
        requeued += 1
    return requeued


async def _requeue_queued_tasks(store, queue: RedisTaskQueue) -> int:
    tasks = await store.list_tasks(limit=5000)
    queued = [
        task
        for task in tasks
        if task.status in {TaskStatus.CREATED, TaskStatus.QUEUED}
    ]
    reconciliation = await queue.reconcile_enqueued_markers(
        task.id for task in queued
    )
    return reconciliation["enqueued"]


async def _maybe_auto_retry(task, service, settings) -> bool:
    if not settings.worker_auto_retry_enabled:
        return False
    if not _retryable_error(task.error.code if task.error else ""):
        return False
    now = datetime.now(timezone.utc)
    pending = task.input_data.get("auto_retry_pending") is True
    if pending:
        retry_at = _parse_retry_at(task.input_data.get("next_retry_at"))
        if retry_at is not None and retry_at > now:
            return False
    else:
        count = int(task.input_data.get("auto_retry_count", 0))
        if count >= settings.worker_max_auto_retries:
            return False
        delay = min(
            settings.worker_retry_max_backoff_seconds,
            settings.worker_retry_backoff_seconds * (2 ** count),
        )
        task.input_data["auto_retry_count"] = count + 1
        task.input_data["last_auto_retry_at"] = now.isoformat()
        task.input_data["next_retry_at"] = (
            now + timedelta(seconds=max(0.0, delay))
        ).isoformat()
        task.input_data["auto_retry_pending"] = True
        await service._store.update_task(task)
        logger.info(
            "automatic retry scheduled task_id=%s retry_at=%s attempt=%s",
            task.id,
            task.input_data["next_retry_at"],
            task.input_data["auto_retry_count"],
        )
        return False

    try:
        await service.retry_task(task.id)
    except Exception:
        logger.exception("automatic retry enqueue failed task_id=%s", task.id)
        failed_task = await service.get_task(task.id)
        failed_task.input_data["auto_retry_pending"] = False
        failed_task.input_data.pop("next_retry_at", None)
        await service._store.update_task(failed_task)
        return False
    retried_task = await service.get_task(task.id)
    retried_task.input_data["auto_retry_pending"] = False
    retried_task.input_data.pop("next_retry_at", None)
    await service._store.update_task(retried_task)
    return True


async def _resume_pending_auto_retries(store, service, settings) -> int:
    """Resume due retries persisted before a Worker restart.

    The method intentionally does not sleep.  A Worker must remain available
    for other GPU and CPU tasks while exponential backoff is pending.
    """

    if not settings.worker_auto_retry_enabled:
        return 0
    tasks = await store.list_tasks(status=TaskStatus.FAILED.value, limit=5000)
    resumed = 0
    for task in tasks:
        if task.input_data.get("auto_retry_pending") is not True:
            continue
        if await _maybe_auto_retry(task, service, settings):
            resumed += 1
    return resumed


def _parse_retry_at(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _retryable_error(code: str) -> bool:
    if not code:
        return True
    non_retryable_fragments = (
        "AUTH_FAILED",
        "NOT_CONFIGURED",
        "INVALID_CONFIGURATION",
        "INPUT_INVALID",
        "REQUIRED",
        "NOT_FOUND",
        "PROJECT_MISMATCH",
        "ASSET_GATE",
        "ASSET_NOT_READY",
    )
    return not any(fragment in code for fragment in non_retryable_fragments)


def _worker_error_code(exc: Exception) -> str:
    if isinstance(exc, TimeoutError):
        return "WORKER_TASK_TIMEOUT"
    return "WORKER_TASK_FAILED"


async def _heartbeat_loop(queue: RedisTaskQueue, settings) -> None:
    try:
        while True:
            await queue.heartbeat(
                {
                    "status": "online",
                    "profile": settings.runtime_profile,
                    "gpu_lock_enabled": settings.worker_gpu_lock_enabled,
                }
            )
            await asyncio.sleep(20)
    except asyncio.CancelledError:
        raise


async def _cleanup_loop(cleanup_service, queue, settings) -> None:
    try:
        while True:
            await asyncio.sleep(max(30, settings.worker_cleanup_interval_seconds))
            if not settings.worker_cleanup_enabled:
                continue
            report = await asyncio.to_thread(cleanup_service.cleanup)
            logger.info("temporary cleanup completed %s", report.as_dict())
    except asyncio.CancelledError:
        raise


async def _scheduler_loop(orchestrator, store, service, queue, settings) -> None:
    try:
        while True:
            await asyncio.sleep(max(5, settings.worker_scheduler_interval_seconds))
            try:
                retried = await _resume_pending_auto_retries(store, service, settings)
                results = (
                    await orchestrator.tick_all()
                    if settings.worker_scheduler_enabled
                    else []
                )
                if retried:
                    logger.info("automatic retry scheduler requeued %d task(s)", retried)
                if results:
                    logger.info("automatic DAG scheduler advanced %d run(s)", len(results))
            except Exception:
                logger.exception("automatic DAG scheduler tick failed")
    except asyncio.CancelledError:
        raise


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_worker())
