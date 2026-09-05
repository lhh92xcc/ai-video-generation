"""Redis-backed generation Worker.

Run with:

    AI_VIDEO_PERSISTENCE_BACKEND=postgres \
    AI_VIDEO_QUEUE_BACKEND=redis \
    uv run python -m app.workers.generation_worker
"""

from __future__ import annotations

import asyncio
import logging

from app.config import load_settings
from app.db import create_engine, create_session_factory, init_db
from app.media.audio_validation import FFprobeAudioValidator
from app.media.audio_normalization import FFmpegAudioNormalizer
from app.media.video_validation import FFprobeVideoValidator
from app.media.identity_audit import IdentityConsistencyAuditor
from app.media.pronunciation import load_pronunciation_dictionary
from app.providers.factory import (
    create_image_generation_provider,
    create_identity_image_generation_provider,
    create_bgm_provider,
    create_novel_pipeline_provider,
    create_story_bible_provider,
    create_subtitle_alignment_provider,
    create_tts_provider,
    create_text_provider,
    create_video_generation_provider,
)
from app.queue import RedisTaskQueue
from app.repositories.sqlalchemy import SqlAlchemyStore
from app.services.novel_service import NovelService
from app.services.novel_task_service import NovelGenerationTaskService
from app.services.task_service import TaskService
from app.services.reference_image_service import ReferenceImageTaskService
from app.services.video_clip_service import VideoClipTaskService
from app.services.video_assembly_service import VideoAssemblyTaskService
from app.services.tts_service import TTSTaskService
from app.services.bgm_service import BGMTaskService
from app.services.subtitle_service import SubtitleTaskService
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
    text_provider = create_text_provider(settings)
    story_bible_provider = create_story_bible_provider(settings)
    novel_pipeline_provider = create_novel_pipeline_provider(settings)
    image_provider = create_image_generation_provider(settings)
    identity_image_provider = create_identity_image_generation_provider(settings)
    video_provider = create_video_generation_provider(settings)
    tts_provider = create_tts_provider(settings)
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
    )

    try:
        await init_db(engine)
        logger.info("generation worker started queue=%s", settings.queue_name)
        while True:
            task_id = await queue.dequeue()
            if task_id is None:
                continue
            logger.info("processing generation task task_id=%s", task_id)
            try:
                await asyncio.wait_for(
                    service.run_task(task_id), timeout=settings.task_timeout_seconds
                )
            except Exception:
                logger.exception("generation task failed task_id=%s", task_id)
    finally:
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


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_worker())
