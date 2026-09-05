"""Application configuration loaded from TOML and environment variables."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Settings:
    app_name: str
    app_version: str
    runtime_profile: str
    environment: str
    api_host: str
    api_port: int
    frontend_origin: str
    auth_mode: str
    auth_shared_secret: str | None
    auth_identity_ttl_seconds: int
    auth_local_actor_id: str
    auth_local_actor_name: str
    log_level: str
    persistence_backend: str
    database_url: str
    queue_backend: str
    redis_url: str
    queue_name: str
    task_timeout_seconds: int
    storage_provider: str
    storage_base_path: str
    storage_uri_prefix: str
    storage_endpoint: str
    storage_public_endpoint: str | None
    storage_bucket: str
    storage_region: str
    storage_access_key: str | None
    storage_secret_key: str | None
    storage_timeout_seconds: int
    storage_signed_url_expire_seconds: int
    llm_provider: str
    llm_base_url: str
    llm_api_key: str | None
    llm_model: str
    llm_temperature: float
    llm_timeout_seconds: int
    image_provider: str
    image_base_url: str
    image_api_key: str | None
    image_model: str
    image_timeout_seconds: int
    image_width: int
    image_height: int
    image_workflow_path: str
    image_identity_workflow_path: str
    image_poll_interval_seconds: float
    image_max_poll_seconds: int
    video_provider: str
    video_base_url: str
    video_api_key: str | None
    video_model: str
    video_workflow_path: str
    video_image_size: str
    video_timeout_seconds: int
    video_create_path: str
    video_status_path_template: str
    video_poll_interval_seconds: float
    video_max_poll_seconds: int
    video_max_download_bytes: int
    video_probe_timeout_seconds: int
    video_output_width: int
    video_output_height: int
    video_fps: int
    video_motion_zoom: float
    video_binary: str
    tts_provider: str
    tts_voice: str
    tts_rate: str
    tts_volume: str
    tts_timeout_seconds: int
    tts_probe_timeout_seconds: int
    tts_max_text_characters: int
    tts_segment_max_characters: int
    tts_max_tempo_factor: float
    tts_speed_token: str
    tts_max_new_token: int
    tts_chunk_max_characters: int
    tts_crossfade_ms: int
    tts_pacing_enabled: bool
    tts_pacing_target_characters_per_second: float
    tts_pacing_min_tempo_factor: float
    tts_pacing_crossfade_ms: int
    tts_pause_compaction_enabled: bool
    tts_pause_min_gap_seconds: float
    tts_pause_soft_keep_seconds: float
    tts_pause_strong_keep_seconds: float
    tts_pause_neutral_keep_seconds: float
    tts_pause_trailing_keep_seconds: float
    tts_pause_scene_boundary_keep_seconds: float
    tts_binary: str
    tts_ffmpeg_binary: str
    tts_multi_voice_timeout_seconds: int
    tts_runtime_path: str
    tts_script_path: str
    tts_device: str
    tts_pronunciation_dictionary_path: str
    identity_audit_enabled: bool
    identity_audit_python_path: str
    identity_audit_script_path: str
    identity_audit_model_root: str
    identity_audit_threshold: float
    identity_audit_frame_count: int
    identity_audit_timeout_seconds: int
    lip_sync_provider: str
    lip_sync_runtime_path: str
    lip_sync_script_path: str
    lip_sync_model_root: str
    lip_sync_device: str
    lip_sync_model: str
    lip_sync_timeout_seconds: int
    lip_sync_base_url: str
    lip_sync_create_path: str
    lip_sync_health_path: str
    lip_sync_api_key: str | None
    lip_sync_max_download_bytes: int
    bgm_provider: str
    bgm_source_root: str
    bgm_source_path: str | None
    bgm_normalize_audio: bool
    bgm_normalization_timeout_seconds: int
    subtitle_alignment_provider: str
    asr_provider: str
    asr_base_url: str
    asr_api_key: str | None
    asr_model: str
    asr_timeout_seconds: int
    asr_binary: str
    asr_model_path: str
    asr_threads: int
    worker_auto_retry_enabled: bool
    worker_max_auto_retries: int
    worker_retry_backoff_seconds: float
    worker_retry_max_backoff_seconds: float
    worker_stale_task_timeout_seconds: int
    worker_gpu_lock_enabled: bool
    worker_gpu_lock_key: str
    worker_gpu_lock_ttl_seconds: int
    worker_gpu_lock_wait_seconds: int
    worker_cleanup_enabled: bool
    worker_cleanup_interval_seconds: int
    worker_cleanup_max_age_hours: float
    worker_cleanup_roots: tuple[str, ...]
    worker_cleanup_max_files_per_run: int
    worker_cleanup_min_free_gb: float
    worker_scheduler_enabled: bool
    worker_scheduler_interval_seconds: int


def _default_config_path() -> Path:
    configured_path = os.getenv("AI_VIDEO_CONFIG")
    if configured_path:
        return Path(configured_path)

    profile = os.getenv("AI_VIDEO_PROFILE", "")
    local_path = Path("config/config.local.toml")
    if local_path.exists() and profile == "local_mac_16gb":
        return local_path
    windows_path = Path("config/config.windows_4060ti_8gb.toml")
    if windows_path.exists() and profile == "windows_4060ti_8gb":
        return windows_path
    return Path("config/config.example.toml")


def load_settings(path: str | Path | None = None) -> Settings:
    """Load the small application settings subset needed by the first phase."""

    config_path = Path(path) if path else _default_config_path()
    with config_path.open("rb") as config_file:
        raw_config = tomllib.load(config_file)

    app_config = raw_config.get("app", {})
    database_config = raw_config.get("database", {})
    persistence_config = raw_config.get("persistence", {})
    redis_config = raw_config.get("redis", {})
    queue_config = raw_config.get("queue", {})
    auth_config = raw_config.get("auth", {})
    storage_config = raw_config.get("storage", {})
    llm_config = raw_config.get("llm", {})
    image_config = raw_config.get("image_generation", {})
    video_config = raw_config.get("video_generation", {})
    tts_config = raw_config.get("tts", {})
    lip_sync_config = raw_config.get("lip_sync", {})
    bgm_config = raw_config.get("bgm", {})
    subtitle_alignment_config = raw_config.get("subtitle_alignment", {})
    asr_config = raw_config.get("asr", {})
    worker_config = raw_config.get("worker", {})

    def setting(section: dict[str, object], key: str, env_name: str, default: object) -> object:
        environment_value = os.getenv(env_name)
        if environment_value is not None and environment_value.strip() != "":
            return environment_value
        return str(section.get(key, default))

    def bool_setting(section: dict[str, object], key: str, env_name: str, default: bool) -> bool:
        return str(setting(section, key, env_name, default)).lower() in {
            "1",
            "true",
            "yes",
            "on",
        }

    def list_setting(
        section: dict[str, object],
        key: str,
        env_name: str,
        default: tuple[str, ...],
    ) -> tuple[str, ...]:
        raw_value = os.getenv(env_name)
        if raw_value is None:
            raw_value = section.get(key, default)
        if isinstance(raw_value, str):
            values = raw_value.split(",")
        elif isinstance(raw_value, (list, tuple)):
            values = raw_value
        else:
            values = default
        return tuple(str(value).strip() for value in values if str(value).strip())

    return Settings(
        app_name=str(app_config.get("name", "ai-video-generation")),
        app_version="0.1.0",
        runtime_profile=str(
            setting(app_config, "profile", "AI_VIDEO_PROFILE", "default")
        ),
        environment=str(app_config.get("environment", "local")),
        api_host=str(app_config.get("api_host", "0.0.0.0")),
        api_port=int(app_config.get("api_port", 8000)),
        frontend_origin=str(app_config.get("frontend_origin", "http://localhost:5173")),
        auth_mode=str(setting(auth_config, "mode", "AI_VIDEO_AUTH_MODE", "local")),
        auth_shared_secret=os.getenv("AI_VIDEO_AUTH_SHARED_SECRET") or None,
        auth_identity_ttl_seconds=int(
            setting(auth_config, "identity_ttl_seconds", "AI_VIDEO_AUTH_IDENTITY_TTL_SECONDS", 60)
        ),
        auth_local_actor_id=str(
            setting(auth_config, "local_actor_id", "AI_VIDEO_AUTH_LOCAL_ACTOR_ID", "local-operator")
        ),
        auth_local_actor_name=str(
            setting(auth_config, "local_actor_name", "AI_VIDEO_AUTH_LOCAL_ACTOR_NAME", "本地操作员")
        ),
        log_level=str(app_config.get("log_level", "INFO")),
        persistence_backend=str(
            setting(persistence_config, "backend", "AI_VIDEO_PERSISTENCE_BACKEND", "memory")
        ),
        database_url=str(
            setting(
                database_config,
                "url",
                "AI_VIDEO_DATABASE_URL",
                "postgresql+asyncpg://postgres:postgres@localhost:5432/ai_video",
            )
        ),
        queue_backend=str(setting(queue_config, "backend", "AI_VIDEO_QUEUE_BACKEND", "in_process")),
        redis_url=str(
            setting(redis_config, "url", "AI_VIDEO_REDIS_URL", "redis://localhost:6379/0")
        ),
        queue_name=str(
            setting(queue_config, "name", "AI_VIDEO_QUEUE_NAME", "ai-video-generation")
        ),
        task_timeout_seconds=int(
            setting(queue_config, "task_timeout_seconds", "AI_VIDEO_TASK_TIMEOUT_SECONDS", 1800)
        ),
        storage_provider=str(
            setting(storage_config, "provider", "AI_VIDEO_STORAGE_PROVIDER", "local")
        ),
        storage_base_path=str(
            setting(storage_config, "base_path", "AI_VIDEO_STORAGE_BASE_PATH", ".tmp/artifacts")
        ),
        storage_uri_prefix=str(
            setting(storage_config, "uri_prefix", "AI_VIDEO_STORAGE_URI_PREFIX", "local://")
        ),
        storage_endpoint=str(
            setting(
                storage_config,
                "endpoint",
                "AI_VIDEO_STORAGE_ENDPOINT",
                "http://localhost:9000",
            )
        ),
        storage_public_endpoint=(
            os.getenv("AI_VIDEO_STORAGE_PUBLIC_ENDPOINT")
            or str(storage_config.get("public_endpoint", "")).strip()
            or None
        ),
        storage_bucket=str(
            setting(storage_config, "bucket", "AI_VIDEO_STORAGE_BUCKET", "ai-video-artifacts")
        ),
        storage_region=str(
            setting(storage_config, "region", "AI_VIDEO_STORAGE_REGION", "us-east-1")
        ),
        storage_access_key=os.getenv("AI_VIDEO_STORAGE_ACCESS_KEY"),
        storage_secret_key=os.getenv("AI_VIDEO_STORAGE_SECRET_KEY"),
        storage_timeout_seconds=int(
            setting(
                storage_config,
                "timeout_seconds",
                "AI_VIDEO_STORAGE_TIMEOUT_SECONDS",
                120,
            )
        ),
        storage_signed_url_expire_seconds=int(
            setting(
                storage_config,
                "signed_url_expire_seconds",
                "AI_VIDEO_STORAGE_SIGNED_URL_EXPIRE_SECONDS",
                900,
            )
        ),
        llm_provider=str(setting(llm_config, "provider", "AI_VIDEO_LLM_PROVIDER", "mock")),
        llm_base_url=str(
            setting(llm_config, "base_url", "AI_VIDEO_LLM_BASE_URL", "https://api.deepseek.com")
        ),
        llm_api_key=os.getenv("AI_VIDEO_LLM_API_KEY"),
        llm_model=str(setting(llm_config, "model", "AI_VIDEO_LLM_MODEL", "deepseek-chat")),
        llm_temperature=float(setting(llm_config, "temperature", "AI_VIDEO_LLM_TEMPERATURE", 0.7)),
        llm_timeout_seconds=int(
            setting(llm_config, "request_timeout_seconds", "AI_VIDEO_LLM_TIMEOUT_SECONDS", 90)
        ),
        image_provider=str(
            setting(image_config, "provider", "AI_VIDEO_IMAGE_PROVIDER", "mock")
        ),
        image_base_url=str(
            setting(image_config, "base_url", "AI_VIDEO_IMAGE_BASE_URL", "")
        ),
        image_api_key=os.getenv("AI_VIDEO_IMAGE_API_KEY"),
        image_model=str(
            setting(image_config, "model", "AI_VIDEO_IMAGE_MODEL", "mock-reference-v1")
        ),
        image_timeout_seconds=int(
            setting(
                image_config,
                "request_timeout_seconds",
                "AI_VIDEO_IMAGE_TIMEOUT_SECONDS",
                120,
            )
        ),
        image_width=int(setting(image_config, "width", "AI_VIDEO_IMAGE_WIDTH", 1024)),
        image_height=int(setting(image_config, "height", "AI_VIDEO_IMAGE_HEIGHT", 1024)),
        image_workflow_path=str(
            setting(
                image_config,
                "workflow_path",
                "AI_VIDEO_IMAGE_WORKFLOW_PATH",
                "config/comfyui/flux-schnell-t2i-api.json",
            )
        ),
        image_identity_workflow_path=str(
            setting(
                image_config,
                "identity_workflow_path",
                "AI_VIDEO_IMAGE_IDENTITY_WORKFLOW_PATH",
                "config/comfyui/flux-schnell-faceid-reference-api.json",
            )
        ),
        image_poll_interval_seconds=float(
            setting(
                image_config,
                "poll_interval_seconds",
                "AI_VIDEO_IMAGE_POLL_INTERVAL_SECONDS",
                1.0,
            )
        ),
        image_max_poll_seconds=int(
            setting(
                image_config,
                "max_poll_seconds",
                "AI_VIDEO_IMAGE_MAX_POLL_SECONDS",
                600,
            )
        ),
        video_provider=str(
            setting(video_config, "provider", "AI_VIDEO_VIDEO_PROVIDER", "mock")
        ),
        video_base_url=str(
            setting(video_config, "base_url", "AI_VIDEO_VIDEO_BASE_URL", "")
        ),
        video_api_key=os.getenv("AI_VIDEO_VIDEO_API_KEY"),
        video_model=str(
            setting(video_config, "model", "AI_VIDEO_VIDEO_MODEL", "mock-video-v1")
        ),
        video_workflow_path=str(
            setting(
                video_config,
                "workflow_path",
                "AI_VIDEO_VIDEO_WORKFLOW_PATH",
                "config/comfyui/wan2.1-i2v-api.json",
            )
        ),
        video_image_size=str(
            setting(video_config, "image_size", "AI_VIDEO_VIDEO_IMAGE_SIZE", "720x1280")
        ),
        video_timeout_seconds=int(
            setting(
                video_config,
                "request_timeout_seconds",
                "AI_VIDEO_VIDEO_TIMEOUT_SECONDS",
                300,
            )
        ),
        video_create_path=str(
            setting(
                video_config,
                "create_path",
                "AI_VIDEO_VIDEO_CREATE_PATH",
                "/videos/generations",
            )
        ),
        video_status_path_template=str(
            setting(
                video_config,
                "status_path_template",
                "AI_VIDEO_VIDEO_STATUS_PATH_TEMPLATE",
                "/videos/generations/{task_id}",
            )
        ),
        video_poll_interval_seconds=float(
            setting(
                video_config,
                "poll_interval_seconds",
                "AI_VIDEO_VIDEO_POLL_INTERVAL_SECONDS",
                2,
            )
        ),
        video_max_poll_seconds=int(
            setting(
                video_config,
                "max_poll_seconds",
                "AI_VIDEO_VIDEO_MAX_POLL_SECONDS",
                900,
            )
        ),
        video_max_download_bytes=int(
            setting(
                video_config,
                "max_download_bytes",
                "AI_VIDEO_VIDEO_MAX_DOWNLOAD_BYTES",
                524288000,
            )
        ),
        video_probe_timeout_seconds=int(
            setting(
                video_config,
                "probe_timeout_seconds",
                "AI_VIDEO_VIDEO_PROBE_TIMEOUT_SECONDS",
                30,
            )
        ),
        video_output_width=int(
            setting(video_config, "output_width", "AI_VIDEO_VIDEO_OUTPUT_WIDTH", 576)
        ),
        video_output_height=int(
            setting(video_config, "output_height", "AI_VIDEO_VIDEO_OUTPUT_HEIGHT", 1024)
        ),
        video_fps=int(setting(video_config, "fps", "AI_VIDEO_VIDEO_FPS", 24)),
        video_motion_zoom=float(
            setting(video_config, "motion_zoom", "AI_VIDEO_VIDEO_MOTION_ZOOM", 1.12)
        ),
        video_binary=str(
            setting(video_config, "binary", "AI_VIDEO_VIDEO_BINARY", "ffmpeg")
        ),
        tts_provider=str(setting(tts_config, "provider", "AI_VIDEO_TTS_PROVIDER", "edge_tts")),
        tts_voice=str(
            setting(
                tts_config,
                "voice",
                "AI_VIDEO_TTS_VOICE",
                "zh-CN-YunyangNeural",
            )
        ),
        tts_rate=str(setting(tts_config, "rate", "AI_VIDEO_TTS_RATE", "-35%")),
        tts_volume=str(setting(tts_config, "volume", "AI_VIDEO_TTS_VOLUME", "+0%")),
        tts_timeout_seconds=int(
            setting(tts_config, "request_timeout_seconds", "AI_VIDEO_TTS_TIMEOUT_SECONDS", 90)
        ),
        tts_probe_timeout_seconds=int(
            setting(tts_config, "probe_timeout_seconds", "AI_VIDEO_TTS_PROBE_TIMEOUT_SECONDS", 30)
        ),
        tts_max_text_characters=int(
            setting(tts_config, "max_text_characters", "AI_VIDEO_TTS_MAX_TEXT_CHARACTERS", 5000)
        ),
        tts_segment_max_characters=int(
            setting(
                tts_config,
                "segment_max_characters",
                "AI_VIDEO_TTS_SEGMENT_MAX_CHARACTERS",
                22,
            )
        ),
        tts_max_tempo_factor=float(
            setting(
                tts_config,
                "max_tempo_factor",
                "AI_VIDEO_TTS_MAX_TEMPO_FACTOR",
                1.08,
            )
        ),
        tts_speed_token=str(
            setting(tts_config, "speed_token", "AI_VIDEO_TTS_SPEED_TOKEN", "[speed_1]")
        ),
        tts_max_new_token=int(
            setting(tts_config, "max_new_token", "AI_VIDEO_TTS_MAX_NEW_TOKEN", 3600)
        ),
        tts_chunk_max_characters=int(
            setting(
                tts_config,
                "chunk_max_characters",
                "AI_VIDEO_TTS_CHUNK_MAX_CHARACTERS",
                120,
            )
        ),
        tts_crossfade_ms=int(
            setting(tts_config, "crossfade_ms", "AI_VIDEO_TTS_CROSSFADE_MS", 120)
        ),
        tts_pacing_enabled=bool_setting(
            tts_config,
            "pacing_enabled",
            "AI_VIDEO_TTS_PACING_ENABLED",
            False,
        ),
        tts_pacing_target_characters_per_second=float(
            setting(
                tts_config,
                "pacing_target_characters_per_second",
                "AI_VIDEO_TTS_PACING_TARGET_CHARACTERS_PER_SECOND",
                3.8,
            )
        ),
        tts_pacing_min_tempo_factor=float(
            setting(
                tts_config,
                "pacing_min_tempo_factor",
                "AI_VIDEO_TTS_PACING_MIN_TEMPO_FACTOR",
                0.82,
            )
        ),
        tts_pacing_crossfade_ms=int(
            setting(
                tts_config,
                "pacing_crossfade_ms",
                "AI_VIDEO_TTS_PACING_CROSSFADE_MS",
                70,
            )
        ),
        tts_pause_compaction_enabled=bool_setting(
            tts_config,
            "pause_compaction_enabled",
            "AI_VIDEO_TTS_PAUSE_COMPACTION_ENABLED",
            False,
        ),
        tts_pause_min_gap_seconds=float(
            setting(
                tts_config,
                "pause_min_gap_seconds",
                "AI_VIDEO_TTS_PAUSE_MIN_GAP_SECONDS",
                0.20,
            )
        ),
        tts_pause_soft_keep_seconds=float(
            setting(
                tts_config,
                "pause_soft_keep_seconds",
                "AI_VIDEO_TTS_PAUSE_SOFT_KEEP_SECONDS",
                0.18,
            )
        ),
        tts_pause_strong_keep_seconds=float(
            setting(
                tts_config,
                "pause_strong_keep_seconds",
                "AI_VIDEO_TTS_PAUSE_STRONG_KEEP_SECONDS",
                0.22,
            )
        ),
        tts_pause_neutral_keep_seconds=float(
            setting(
                tts_config,
                "pause_neutral_keep_seconds",
                "AI_VIDEO_TTS_PAUSE_NEUTRAL_KEEP_SECONDS",
                0.10,
            )
        ),
        tts_pause_trailing_keep_seconds=float(
            setting(
                tts_config,
                "pause_trailing_keep_seconds",
                "AI_VIDEO_TTS_PAUSE_TRAILING_KEEP_SECONDS",
                0.18,
            )
        ),
        tts_pause_scene_boundary_keep_seconds=float(
            setting(
                tts_config,
                "pause_scene_boundary_keep_seconds",
                "AI_VIDEO_TTS_PAUSE_SCENE_BOUNDARY_KEEP_SECONDS",
                0.20,
            )
        ),
        tts_binary=str(setting(tts_config, "binary", "AI_VIDEO_TTS_BINARY", "say")),
        tts_ffmpeg_binary=str(
            setting(tts_config, "ffmpeg_binary", "AI_VIDEO_TTS_FFMPEG_BINARY", "ffmpeg")
        ),
        tts_multi_voice_timeout_seconds=int(
            setting(
                tts_config,
                "multi_voice_timeout_seconds",
                "AI_VIDEO_TTS_MULTI_VOICE_TIMEOUT_SECONDS",
                120,
            )
        ),
        tts_runtime_path=str(
            setting(
                tts_config,
                "runtime_path",
                "AI_VIDEO_TTS_RUNTIME_PATH",
                "local-runtimes/chattts/.venv/bin/python",
            )
        ),
        tts_script_path=str(
            setting(
                tts_config,
                "script_path",
                "AI_VIDEO_TTS_SCRIPT_PATH",
                "scripts/chattts_generate.py",
            )
        ),
        tts_device=str(setting(tts_config, "device", "AI_VIDEO_TTS_DEVICE", "mps")),
        tts_pronunciation_dictionary_path=str(
            setting(
                tts_config,
                "pronunciation_dictionary_path",
                "AI_VIDEO_TTS_PRONUNCIATION_DICTIONARY_PATH",
                "config/pronunciation.toml",
            )
        ),
        identity_audit_enabled=bool_setting(
            video_config,
            "identity_audit_enabled",
            "AI_VIDEO_IDENTITY_AUDIT_ENABLED",
            False,
        ),
        identity_audit_python_path=str(
            setting(
                video_config,
                "identity_audit_python_path",
                "AI_VIDEO_IDENTITY_AUDIT_PYTHON_PATH",
                "",
            )
        ),
        identity_audit_script_path=str(
            setting(
                video_config,
                "identity_audit_script_path",
                "AI_VIDEO_IDENTITY_AUDIT_SCRIPT_PATH",
                "scripts/face_identity_audit.py",
            )
        ),
        identity_audit_model_root=str(
            setting(
                video_config,
                "identity_audit_model_root",
                "AI_VIDEO_IDENTITY_AUDIT_MODEL_ROOT",
                "",
            )
        ),
        identity_audit_threshold=float(
            setting(
                video_config,
                "identity_audit_threshold",
                "AI_VIDEO_IDENTITY_AUDIT_THRESHOLD",
                0.4,
            )
        ),
        identity_audit_frame_count=int(
            setting(
                video_config,
                "identity_audit_frame_count",
                "AI_VIDEO_IDENTITY_AUDIT_FRAME_COUNT",
                8,
            )
        ),
        identity_audit_timeout_seconds=int(
            setting(
                video_config,
                "identity_audit_timeout_seconds",
                "AI_VIDEO_IDENTITY_AUDIT_TIMEOUT_SECONDS",
                120,
            )
        ),
        lip_sync_provider=str(
            setting(lip_sync_config, "provider", "AI_VIDEO_LIP_SYNC_PROVIDER", "mock")
        ),
        lip_sync_runtime_path=str(
            setting(
                lip_sync_config,
                "runtime_path",
                "AI_VIDEO_LIP_SYNC_RUNTIME_PATH",
                "",
            )
        ),
        lip_sync_script_path=str(
            setting(
                lip_sync_config,
                "script_path",
                "AI_VIDEO_LIP_SYNC_SCRIPT_PATH",
                "scripts/musetalk_infer.py",
            )
        ),
        lip_sync_model_root=str(
            setting(
                lip_sync_config,
                "model_root",
                "AI_VIDEO_LIP_SYNC_MODEL_ROOT",
                "",
            )
        ),
        lip_sync_device=str(
            setting(lip_sync_config, "device", "AI_VIDEO_LIP_SYNC_DEVICE", "cpu")
        ),
        lip_sync_model=str(
            setting(
                lip_sync_config,
                "model",
                "AI_VIDEO_LIP_SYNC_MODEL",
                "MuseTalk-local",
            )
        ),
        lip_sync_timeout_seconds=int(
            setting(
                lip_sync_config,
                "timeout_seconds",
                "AI_VIDEO_LIP_SYNC_TIMEOUT_SECONDS",
                900,
            )
        ),
        lip_sync_base_url=str(
            setting(
                lip_sync_config,
                "base_url",
                "AI_VIDEO_LIP_SYNC_BASE_URL",
                "http://127.0.0.1:8090",
            )
        ),
        lip_sync_create_path=str(
            setting(
                lip_sync_config,
                "create_path",
                "AI_VIDEO_LIP_SYNC_CREATE_PATH",
                "/v1/lip-sync",
            )
        ),
        lip_sync_health_path=str(
            setting(
                lip_sync_config,
                "health_path",
                "AI_VIDEO_LIP_SYNC_HEALTH_PATH",
                "/healthz",
            )
        ),
        lip_sync_api_key=os.getenv("AI_VIDEO_LIP_SYNC_API_KEY") or None,
        lip_sync_max_download_bytes=int(
            setting(
                lip_sync_config,
                "max_download_bytes",
                "AI_VIDEO_LIP_SYNC_MAX_DOWNLOAD_BYTES",
                524288000,
            )
        ),
        bgm_provider=str(setting(bgm_config, "provider", "AI_VIDEO_BGM_PROVIDER", "mock")),
        bgm_source_root=str(
            setting(bgm_config, "source_root", "AI_VIDEO_BGM_SOURCE_ROOT", ".tmp/bgm")
        ),
        bgm_source_path=(
            str(setting(bgm_config, "source_path", "AI_VIDEO_BGM_SOURCE_PATH", "")) or None
        ),
        bgm_normalize_audio=bool_setting(
            bgm_config,
            "normalize_audio",
            "AI_VIDEO_BGM_NORMALIZE_AUDIO",
            True,
        ),
        bgm_normalization_timeout_seconds=int(
            setting(
                bgm_config,
                "normalization_timeout_seconds",
                "AI_VIDEO_BGM_NORMALIZATION_TIMEOUT_SECONDS",
                120,
            )
        ),
        subtitle_alignment_provider=str(
            setting(
                subtitle_alignment_config,
                "provider",
                "AI_VIDEO_SUBTITLE_ALIGNMENT_PROVIDER",
                "mock_sentence",
            )
        ),
        asr_provider=str(setting(asr_config, "provider", "AI_VIDEO_ASR_PROVIDER", "mock")),
        asr_base_url=str(setting(asr_config, "base_url", "AI_VIDEO_ASR_BASE_URL", "")),
        asr_api_key=os.getenv("AI_VIDEO_ASR_API_KEY"),
        asr_model=str(setting(asr_config, "model", "AI_VIDEO_ASR_MODEL", "whisper-1")),
        asr_timeout_seconds=int(
            setting(asr_config, "request_timeout_seconds", "AI_VIDEO_ASR_TIMEOUT_SECONDS", 180)
        ),
        asr_binary=str(setting(asr_config, "binary", "AI_VIDEO_ASR_BINARY", "whisper-cli")),
        asr_model_path=str(
            setting(asr_config, "model_path", "AI_VIDEO_ASR_MODEL_PATH", "")
        ),
        asr_threads=int(setting(asr_config, "threads", "AI_VIDEO_ASR_THREADS", 4)),
        worker_auto_retry_enabled=bool_setting(
            worker_config,
            "auto_retry_enabled",
            "AI_VIDEO_WORKER_AUTO_RETRY_ENABLED",
            True,
        ),
        worker_max_auto_retries=int(
            setting(worker_config, "max_auto_retries", "AI_VIDEO_WORKER_MAX_AUTO_RETRIES", 2)
        ),
        worker_retry_backoff_seconds=float(
            setting(
                worker_config,
                "retry_backoff_seconds",
                "AI_VIDEO_WORKER_RETRY_BACKOFF_SECONDS",
                5,
            )
        ),
        worker_retry_max_backoff_seconds=float(
            setting(
                worker_config,
                "retry_max_backoff_seconds",
                "AI_VIDEO_WORKER_RETRY_MAX_BACKOFF_SECONDS",
                120,
            )
        ),
        worker_stale_task_timeout_seconds=int(
            setting(
                worker_config,
                "stale_task_timeout_seconds",
                "AI_VIDEO_WORKER_STALE_TASK_TIMEOUT_SECONDS",
                3600,
            )
        ),
        worker_gpu_lock_enabled=bool_setting(
            worker_config,
            "gpu_lock_enabled",
            "AI_VIDEO_WORKER_GPU_LOCK_ENABLED",
            False,
        ),
        worker_gpu_lock_key=str(
            setting(
                worker_config,
                "gpu_lock_key",
                "AI_VIDEO_WORKER_GPU_LOCK_KEY",
                "ai-video:gpus:default",
            )
        ),
        worker_gpu_lock_ttl_seconds=int(
            setting(
                worker_config,
                "gpu_lock_ttl_seconds",
                "AI_VIDEO_WORKER_GPU_LOCK_TTL_SECONDS",
                7200,
            )
        ),
        worker_gpu_lock_wait_seconds=int(
            setting(
                worker_config,
                "gpu_lock_wait_seconds",
                "AI_VIDEO_WORKER_GPU_LOCK_WAIT_SECONDS",
                8,
            )
        ),
        worker_cleanup_enabled=bool_setting(
            worker_config,
            "cleanup_enabled",
            "AI_VIDEO_WORKER_CLEANUP_ENABLED",
            True,
        ),
        worker_cleanup_interval_seconds=int(
            setting(
                worker_config,
                "cleanup_interval_seconds",
                "AI_VIDEO_WORKER_CLEANUP_INTERVAL_SECONDS",
                900,
            )
        ),
        worker_cleanup_max_age_hours=float(
            setting(
                worker_config,
                "cleanup_max_age_hours",
                "AI_VIDEO_WORKER_CLEANUP_MAX_AGE_HOURS",
                24,
            )
        ),
        worker_cleanup_roots=list_setting(
            worker_config,
            "cleanup_roots",
            "AI_VIDEO_WORKER_CLEANUP_ROOTS",
            (".tmp/work", ".tmp/renders", ".tmp/musetalk"),
        ),
        worker_cleanup_max_files_per_run=int(
            setting(
                worker_config,
                "cleanup_max_files_per_run",
                "AI_VIDEO_WORKER_CLEANUP_MAX_FILES_PER_RUN",
                500,
            )
        ),
        worker_cleanup_min_free_gb=float(
            setting(
                worker_config,
                "cleanup_min_free_gb",
                "AI_VIDEO_WORKER_CLEANUP_MIN_FREE_GB",
                10,
            )
        ),
        worker_scheduler_enabled=bool_setting(
            worker_config,
            "scheduler_enabled",
            "AI_VIDEO_WORKER_SCHEDULER_ENABLED",
            True,
        ),
        worker_scheduler_interval_seconds=int(
            setting(
                worker_config,
                "scheduler_interval_seconds",
                "AI_VIDEO_WORKER_SCHEDULER_INTERVAL_SECONDS",
                15,
            )
        ),
    )
