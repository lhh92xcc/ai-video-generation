"""FastAPI application factory."""

from __future__ import annotations

from contextlib import asynccontextmanager
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.auth.identity import AuthenticationError, create_identity_provider
from app.api.errors import ApiError
from app.api.routes import router
from app.config import Settings, load_settings
from app.domain.models import TaskStatus
from app.db import create_engine as create_database_engine, create_session_factory, init_db
from app.media.audio_validation import FFprobeAudioValidator
from app.media.audio_normalization import FFmpegAudioNormalizer
from app.media.video_validation import FFprobeVideoValidator
from app.media.identity_audit import IdentityConsistencyAuditor
from app.media.identity_calibration import IdentityCalibrationService
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
from app.providers.errors import ImageProviderError, TextProviderError
from app.providers.errors_audio import TTSProviderError
from app.providers.errors_video import VideoProviderError
from app.storage.factory import create_artifact_storage
from app.queue import InProcessTaskQueue, RedisTaskQueue
from app.repositories.in_memory import InMemoryStore
from app.repositories.sqlalchemy import SqlAlchemyStore
from app.services.task_service import TaskService
from app.services.task_batch_service import TaskBatchService
from app.services.episode_task_plan_service import EpisodeTaskPlanService
from app.services.novel_service import NovelService
from app.services.novel_task_service import NovelGenerationTaskService
from app.services.asset_service import AssetService
from app.services.reference_image_service import ReferenceImageTaskService
from app.services.video_clip_service import VideoClipTaskService
from app.services.video_assembly_service import VideoAssemblyTaskService
from app.services.tts_service import TTSTaskService
from app.services.bgm_service import BGMTaskService
from app.services.subtitle_service import SubtitleTaskService
from app.services.identity_retry_service import IdentityAuditRetryService
from app.services.lip_sync_service import LipSyncTaskService
from app.services.voice_asset_service import VoiceAssetService
from app.services.artifact_service import ArtifactService
from app.services.audit_service import AuditService
from app.services.access_service import ProjectAccessService
from app.services.runtime_health_service import RuntimeHealthService
from app.services.temp_cleanup_service import TemporaryDirectoryCleanupService
from app.services.production_orchestrator import ProductionOrchestrator
from app.rendering.ffmpeg_renderer import FFmpegVideoRenderer
from app.providers.profiles import ASRProviderProfileRegistry, VisualProviderProfileRegistry


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or load_settings()
    identity_provider = create_identity_provider(app_settings)

    database_engine = None
    if app_settings.persistence_backend == "postgres":
        database_engine = create_database_engine(app_settings.database_url)
        session_factory = create_session_factory(database_engine)
        store = SqlAlchemyStore(session_factory)
    else:
        store = InMemoryStore()

    if app_settings.queue_backend == "redis":
        task_queue = RedisTaskQueue(app_settings.redis_url, app_settings.queue_name)
    else:
        task_queue = InProcessTaskQueue()

    text_provider = create_text_provider(app_settings)
    story_bible_provider = create_story_bible_provider(app_settings)
    novel_pipeline_provider = create_novel_pipeline_provider(app_settings)
    image_provider = create_image_generation_provider(app_settings)
    identity_image_provider = create_identity_image_generation_provider(app_settings)
    video_provider = create_video_generation_provider(app_settings)
    tts_provider = create_tts_provider(app_settings)
    lip_sync_provider = create_lip_sync_provider(app_settings)
    bgm_provider = create_bgm_provider(app_settings)
    subtitle_alignment_provider = create_subtitle_alignment_provider(app_settings)
    asr_profile_registry = ASRProviderProfileRegistry(app_settings)
    visual_profile_registry = VisualProviderProfileRegistry(app_settings)
    artifact_storage = create_artifact_storage(app_settings)
    novel_service = NovelService(
        store,
        story_bible_provider,
        novel_pipeline_provider,
    )
    novel_task_service = NovelGenerationTaskService(store, novel_service, task_queue)
    asset_service = AssetService(store, novel_service)
    voice_asset_service = VoiceAssetService(store)
    reference_image_task_service = ReferenceImageTaskService(
        store,
        task_queue,
        image_provider,
        artifact_storage,
        identity_provider=identity_image_provider,
        default_width=app_settings.image_width,
        default_height=app_settings.image_height,
        default_style=app_settings.image_default_style,
        default_negative_prompt=app_settings.image_default_negative_prompt,
        provider_registry=visual_profile_registry,
    )
    video_clip_task_service = VideoClipTaskService(
        store,
        task_queue,
        video_provider,
        artifact_storage,
        video_validator=FFprobeVideoValidator(
            timeout_seconds=app_settings.video_probe_timeout_seconds
        ),
        identity_auditor=(
            IdentityConsistencyAuditor(
                python_path=app_settings.identity_audit_python_path,
                script_path=app_settings.identity_audit_script_path,
                model_root=app_settings.identity_audit_model_root,
                threshold=app_settings.identity_audit_threshold,
                frame_count=app_settings.identity_audit_frame_count,
                timeout_seconds=app_settings.identity_audit_timeout_seconds,
            )
            if app_settings.identity_audit_enabled
            else None
        ),
        default_negative_prompt=app_settings.video_default_negative_prompt,
        prompt_suffix=app_settings.video_prompt_suffix,
        provider_registry=visual_profile_registry,
    )
    video_assembly_task_service = VideoAssemblyTaskService(
        store,
        task_queue,
        artifact_storage,
        renderer=FFmpegVideoRenderer(
            binary=app_settings.video_binary,
            timeout_seconds=app_settings.task_timeout_seconds,
            render_preset=app_settings.video_render_preset,
            render_crf=app_settings.video_render_crf,
            render_tune=app_settings.video_render_tune,
        ),
        audio_validator=FFprobeAudioValidator(
            timeout_seconds=app_settings.tts_probe_timeout_seconds
        ),
    )
    lip_sync_task_service = LipSyncTaskService(
        store,
        task_queue,
        lip_sync_provider,
        artifact_storage,
        video_validator=FFprobeVideoValidator(
            timeout_seconds=app_settings.video_probe_timeout_seconds
        ),
    )
    tts_task_service = TTSTaskService(
        store,
        task_queue,
        tts_provider,
        artifact_storage,
        audio_validator=FFprobeAudioValidator(
            timeout_seconds=app_settings.tts_probe_timeout_seconds
        ),
        default_voice=app_settings.tts_voice,
        default_rate=app_settings.tts_rate,
        default_volume=app_settings.tts_volume,
        max_text_characters=app_settings.tts_max_text_characters,
        pronunciation_dictionary=load_pronunciation_dictionary(
            app_settings.tts_pronunciation_dictionary_path
        ),
        voice_asset_service=voice_asset_service,
        configured_provider=app_settings.tts_provider,
        multi_voice_ffmpeg_binary=app_settings.tts_ffmpeg_binary,
        multi_voice_timeout_seconds=app_settings.tts_multi_voice_timeout_seconds,
    )
    bgm_task_service = BGMTaskService(
        store,
        task_queue,
        bgm_provider,
        artifact_storage,
        audio_validator=FFprobeAudioValidator(
            timeout_seconds=app_settings.tts_probe_timeout_seconds
        ),
        audio_normalizer=(
            FFmpegAudioNormalizer(timeout_seconds=app_settings.bgm_normalization_timeout_seconds)
            if app_settings.bgm_normalize_audio
            else None
        ),
    )
    subtitle_task_service = SubtitleTaskService(
        store,
        task_queue,
        artifact_storage,
        alignment_provider=subtitle_alignment_provider,
        asr_profile_registry=asr_profile_registry,
    )
    artifact_service = ArtifactService(store, artifact_storage)
    audit_service = AuditService(store)
    access_service = ProjectAccessService(
        store,
        auth_mode=app_settings.auth_mode,
        audit_service=audit_service,
    )
    task_service = TaskService(
        store,
        text_provider,
        task_queue,
        novel_task_runner=novel_task_service.run_task,
        reference_image_task_runner=reference_image_task_service.run_task,
        video_clip_task_runner=video_clip_task_service.run_task,
        video_assembly_task_runner=video_assembly_task_service.run_task,
        tts_task_runner=tts_task_service.run_task,
        bgm_task_runner=bgm_task_service.run_task,
        subtitle_task_runner=subtitle_task_service.run_task,
        lip_sync_task_runner=lip_sync_task_service.run_task,
    )
    task_batch_service = TaskBatchService(store, task_service.retry_task)
    identity_calibration_service = IdentityCalibrationService(store)
    identity_retry_service = IdentityAuditRetryService(store, task_queue, task_batch_service)
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
    cleanup_service = TemporaryDirectoryCleanupService(
        app_settings.worker_cleanup_roots,
        storage_base_path=app_settings.storage_base_path,
        max_age_hours=app_settings.worker_cleanup_max_age_hours,
        max_files_per_run=app_settings.worker_cleanup_max_files_per_run,
        min_free_gb=app_settings.worker_cleanup_min_free_gb,
    )
    runtime_health_service = RuntimeHealthService(
        app_settings,
        task_queue,
        store,
        visual_profile_registry=visual_profile_registry,
    )
    production_orchestrator = ProductionOrchestrator(store, episode_task_plan_service)

    if isinstance(task_queue, InProcessTaskQueue):
        async def run_in_process_task(task_id: UUID) -> None:
            await task_service.run_task(task_id)
            finished = await store.get_task(task_id)
            if finished is not None and finished.status == TaskStatus.SUCCEEDED:
                await production_orchestrator.on_task_finished(task_id)
            elif finished is not None and finished.status == TaskStatus.FAILED:
                await production_orchestrator.on_task_failed(task_id)

        task_queue.set_handler(run_in_process_task)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if database_engine is not None:
            await init_db(database_engine)
        yield
        await task_queue.close()
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
        await visual_profile_registry.close()
        close_storage = getattr(artifact_storage, "close", None)
        if close_storage is not None:
            await close_storage()
        if database_engine is not None:
            await database_engine.dispose()

    app = FastAPI(
        title="AI Video Generation API",
        version=app_settings.app_version,
        description="API for projects and schema-validated script-generation tasks.",
        lifespan=lifespan,
    )
    app.state.settings = app_settings
    app.state.store = store
    app.state.task_queue = task_queue
    app.state.task_service = task_service
    app.state.task_batch_service = task_batch_service
    app.state.episode_task_plan_service = episode_task_plan_service
    app.state.novel_service = novel_service
    app.state.novel_task_service = novel_task_service
    app.state.asset_service = asset_service
    app.state.reference_image_task_service = reference_image_task_service
    app.state.video_clip_task_service = video_clip_task_service
    app.state.video_assembly_task_service = video_assembly_task_service
    app.state.tts_task_service = tts_task_service
    app.state.lip_sync_task_service = lip_sync_task_service
    app.state.voice_asset_service = voice_asset_service
    app.state.bgm_task_service = bgm_task_service
    app.state.subtitle_task_service = subtitle_task_service
    app.state.artifact_service = artifact_service
    app.state.audit_service = audit_service
    app.state.artifact_storage = artifact_storage
    app.state.asr_profile_registry = asr_profile_registry
    app.state.visual_profile_registry = visual_profile_registry
    app.state.identity_provider = identity_provider
    app.state.access_service = access_service
    app.state.identity_calibration_service = identity_calibration_service
    app.state.identity_retry_service = identity_retry_service
    app.state.cleanup_service = cleanup_service
    app.state.runtime_health_service = runtime_health_service
    app.state.production_orchestrator = production_orchestrator

    @app.middleware("http")
    async def add_request_id(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or f"req_{uuid4().hex}"
        request.state.request_id = request_id
        if request.url.path.startswith("/api/"):
            try:
                request.state.identity = identity_provider.resolve(request)
            except AuthenticationError as exc:
                response = JSONResponse(
                    status_code=401,
                    content={
                        "error": {
                            "code": exc.code,
                            "message": exc.message,
                            "request_id": request_id,
                            "details": {},
                        }
                    },
                )
                response.headers["X-Request-ID"] = request_id
                return response
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    @app.exception_handler(ApiError)
    async def handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "request_id": request.state.request_id,
                    "details": exc.details,
                }
            },
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        details = [
            {"loc": list(error["loc"]), "message": error["msg"], "type": error["type"]}
            for error in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "INVALID_REQUEST",
                    "message": "Request validation failed",
                    "request_id": request.state.request_id,
                    "details": details,
                }
            },
        )

    @app.exception_handler(HTTPException)
    async def handle_http_error(request: Request, exc: HTTPException) -> JSONResponse:
        message = exc.detail if isinstance(exc.detail, str) else "HTTP request failed"
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": "HTTP_ERROR",
                    "message": message,
                    "request_id": request.state.request_id,
                    "details": exc.detail if isinstance(exc.detail, dict) else {},
                }
            },
        )

    @app.exception_handler(TextProviderError)
    async def handle_provider_error(request: Request, exc: TextProviderError) -> JSONResponse:
        return JSONResponse(
            status_code=502,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "request_id": request.state.request_id,
                    "details": {},
                }
            },
        )

    @app.exception_handler(ImageProviderError)
    async def handle_image_provider_error(request: Request, exc: ImageProviderError) -> JSONResponse:
        return JSONResponse(
            status_code=502,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "request_id": request.state.request_id,
                    "details": {},
                }
            },
        )

    @app.exception_handler(VideoProviderError)
    async def handle_video_provider_error(request: Request, exc: VideoProviderError) -> JSONResponse:
        return JSONResponse(
            status_code=502,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "request_id": request.state.request_id,
                    "details": {},
                }
            },
        )

    @app.exception_handler(TTSProviderError)
    async def handle_tts_provider_error(request: Request, exc: TTSProviderError) -> JSONResponse:
        return JSONResponse(
            status_code=502,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "request_id": request.state.request_id,
                    "details": {},
                }
            },
        )

    app.include_router(router)
    return app


app = create_app()
