"""Build the configured text provider."""

from __future__ import annotations

from app.config import Settings
from app.providers.mock_text import MockTextProvider
from app.providers.image_generation import ImageGenerationProvider
from app.providers.mock_image import MockImageGenerationProvider
from app.providers.mock_video import MockVideoGenerationProvider
from app.providers.local_fixture_video import LocalFixtureVideoGenerationProvider
from app.providers.mock_tts import MockTTSProvider
from app.providers.edge_tts import EdgeTTSProvider
from app.providers.openai_compatible_image import OpenAICompatibleImageGenerationProvider
from app.providers.openai_compatible_video import OpenAICompatibleVideoGenerationProvider
from app.providers.siliconflow_image import SiliconFlowImageGenerationProvider
from app.providers.siliconflow_video import SiliconFlowVideoGenerationProvider
from app.providers.novel_pipeline import MockNovelDramaProvider, NovelDramaGenerationProvider
from app.providers.openai_compatible_novel import (
    OpenAICompatibleNovelDramaProvider,
    OpenAICompatibleStoryBibleProvider,
)
from app.providers.openai_compatible import OpenAICompatibleTextProvider
from app.providers.protocol import TextProvider
from app.providers.story_bible import MockStoryBibleProvider, StoryBibleGenerationProvider
from app.providers.video_generation import VideoGenerationProvider
from app.providers.tts import TTSProvider
from app.providers.bgm import BGMProvider
from app.providers.asr import SubtitleASRProvider
from app.providers.local_bgm import LocalFileBGMProvider
from app.providers.mock_bgm import MockBGMProvider
from app.providers.mock_asr import MockASRProvider
from app.providers.openai_compatible_asr import OpenAICompatibleASRProvider
from app.providers.siliconflow_asr import SiliconFlowASRProvider
from app.providers.aliyun_dashscope_asr import AliyunDashScopeASRProvider
from app.providers.mock_subtitle_alignment import MockSentenceSubtitleAlignmentProvider
from app.providers.subtitle_alignment import SubtitleAlignmentProvider


def create_text_provider(settings: Settings) -> TextProvider:
    if settings.llm_provider in {"openai_compatible", "ollama"}:
        return OpenAICompatibleTextProvider(
            base_url=(settings.llm_base_url or "http://127.0.0.1:11434/v1")
            if settings.llm_provider == "ollama"
            else settings.llm_base_url,
            api_key=settings.llm_api_key or ("ollama" if settings.llm_provider == "ollama" else None),
            model=settings.llm_model,
            temperature=settings.llm_temperature,
            timeout_seconds=settings.llm_timeout_seconds,
        )
    return MockTextProvider()


def create_story_bible_provider(settings: Settings) -> StoryBibleGenerationProvider:
    if settings.llm_provider in {"openai_compatible", "ollama"}:
        return OpenAICompatibleStoryBibleProvider(
            base_url=(settings.llm_base_url or "http://127.0.0.1:11434/v1")
            if settings.llm_provider == "ollama"
            else settings.llm_base_url,
            api_key=settings.llm_api_key or ("ollama" if settings.llm_provider == "ollama" else None),
            model=settings.llm_model,
            temperature=settings.llm_temperature,
            timeout_seconds=settings.llm_timeout_seconds,
        )
    return MockStoryBibleProvider()


def create_novel_pipeline_provider(settings: Settings) -> NovelDramaGenerationProvider:
    if settings.llm_provider in {"openai_compatible", "ollama"}:
        return OpenAICompatibleNovelDramaProvider(
            base_url=(settings.llm_base_url or "http://127.0.0.1:11434/v1")
            if settings.llm_provider == "ollama"
            else settings.llm_base_url,
            api_key=settings.llm_api_key or ("ollama" if settings.llm_provider == "ollama" else None),
            model=settings.llm_model,
            temperature=settings.llm_temperature,
            timeout_seconds=settings.llm_timeout_seconds,
        )
    return MockNovelDramaProvider()


def create_image_generation_provider(settings: Settings) -> ImageGenerationProvider:
    """Build the configured image provider through the stable application contract."""

    if settings.image_provider == "openai_compatible":
        return OpenAICompatibleImageGenerationProvider(
            base_url=settings.image_base_url,
            api_key=settings.image_api_key,
            model=settings.image_model,
            timeout_seconds=settings.image_timeout_seconds,
        )
    if settings.image_provider == "mock":
        return MockImageGenerationProvider()
    if settings.image_provider == "siliconflow":
        return SiliconFlowImageGenerationProvider(
            base_url=settings.image_base_url or "https://api.siliconflow.cn",
            api_key=settings.image_api_key,
            model=settings.image_model,
            timeout_seconds=settings.image_timeout_seconds,
        )
    if settings.image_provider == "comfyui":
        from app.providers.comfyui_image import ComfyUIImageGenerationProvider

        return ComfyUIImageGenerationProvider(
            base_url=settings.image_base_url or "http://127.0.0.1:8188",
            workflow_path=settings.image_workflow_path,
            model=settings.image_model,
            timeout_seconds=settings.image_timeout_seconds,
            poll_interval_seconds=settings.image_poll_interval_seconds,
            max_poll_seconds=settings.image_max_poll_seconds,
        )
    raise ValueError(
        f"Unsupported image provider: {settings.image_provider}. "
        "Use mock, openai_compatible, siliconflow or comfyui."
    )


def create_identity_image_generation_provider(
    settings: Settings,
) -> ImageGenerationProvider | None:
    """Build the optional identity-locked image adapter for character shots."""

    if settings.image_provider != "comfyui":
        return None
    from app.providers.comfyui_image import ComfyUIImageGenerationProvider

    return ComfyUIImageGenerationProvider(
        base_url=settings.image_base_url or "http://127.0.0.1:8188",
        workflow_path=settings.image_identity_workflow_path,
        model=settings.image_model,
        timeout_seconds=settings.image_timeout_seconds,
        poll_interval_seconds=settings.image_poll_interval_seconds,
        max_poll_seconds=settings.image_max_poll_seconds,
    )


def create_video_generation_provider(settings: Settings) -> VideoGenerationProvider:
    """Build the configured video provider through the stable application contract."""

    if settings.video_provider == "mock":
        return MockVideoGenerationProvider(settings.video_model)
    if settings.video_provider == "local_fixture":
        return LocalFixtureVideoGenerationProvider(
            settings.video_model,
            timeout_seconds=settings.video_timeout_seconds,
        )
    if settings.video_provider == "ffmpeg_motion":
        from app.providers.ffmpeg_motion_video import FFmpegMotionVideoGenerationProvider

        return FFmpegMotionVideoGenerationProvider(
            settings.video_model,
            timeout_seconds=settings.video_timeout_seconds,
            binary=settings.video_binary,
            width=settings.video_output_width,
            height=settings.video_output_height,
            fps=settings.video_fps,
            motion_zoom=settings.video_motion_zoom,
        )
    if settings.video_provider == "openai_compatible":
        return OpenAICompatibleVideoGenerationProvider(
            base_url=settings.video_base_url,
            api_key=settings.video_api_key,
            model=settings.video_model,
            timeout_seconds=settings.video_timeout_seconds,
            create_path=settings.video_create_path,
            status_path_template=settings.video_status_path_template,
            poll_interval_seconds=settings.video_poll_interval_seconds,
            max_poll_seconds=settings.video_max_poll_seconds,
            max_download_bytes=settings.video_max_download_bytes,
        )
    if settings.video_provider == "siliconflow":
        return SiliconFlowVideoGenerationProvider(
            base_url=settings.video_base_url or "https://api.siliconflow.cn",
            api_key=settings.video_api_key,
            model=settings.video_model,
            image_size=settings.video_image_size,
            timeout_seconds=settings.video_timeout_seconds,
            poll_interval_seconds=settings.video_poll_interval_seconds,
            max_poll_seconds=settings.video_max_poll_seconds,
            max_download_bytes=settings.video_max_download_bytes,
        )
    if settings.video_provider == "comfyui_wan_i2v":
        from app.providers.comfyui_wan_i2v import ComfyUIWanI2VVideoGenerationProvider

        width, height = settings.video_image_size.lower().split("x", 1)
        return ComfyUIWanI2VVideoGenerationProvider(
            base_url=settings.video_base_url or "http://127.0.0.1:8188",
            workflow_path=settings.video_workflow_path,
            model=settings.video_model,
            timeout_seconds=settings.video_timeout_seconds,
            poll_interval_seconds=settings.video_poll_interval_seconds,
            max_poll_seconds=settings.video_max_poll_seconds,
            output_width=int(width),
            output_height=int(height),
            fps=settings.video_fps,
        )
    raise ValueError(
        f"Unsupported video provider: {settings.video_provider}. "
        "Use mock, local_fixture, ffmpeg_motion, openai_compatible, siliconflow or comfyui_wan_i2v."
    )


def create_tts_provider(settings: Settings) -> TTSProvider:
    """Build the configured TTS provider through the stable application contract."""

    if settings.tts_provider == "mock":
        return MockTTSProvider()
    if settings.tts_provider == "edge_tts":
        return EdgeTTSProvider(timeout_seconds=settings.tts_timeout_seconds)
    if settings.tts_provider == "macos_say":
        from app.providers.macos_say_tts import MacOSSayTTSProvider

        return MacOSSayTTSProvider(
            timeout_seconds=settings.tts_timeout_seconds,
            say_binary=settings.tts_binary,
            ffmpeg_binary=settings.tts_ffmpeg_binary,
        )
    if settings.tts_provider == "chattts":
        from app.providers.chattts import ChatTTSProvider

        return ChatTTSProvider(
            runtime_path=settings.tts_runtime_path,
            script_path=settings.tts_script_path,
            device=settings.tts_device,
            timeout_seconds=settings.tts_timeout_seconds,
            segment_max_characters=settings.tts_segment_max_characters,
            speed_token=settings.tts_speed_token,
            max_new_token=settings.tts_max_new_token,
            chunk_max_characters=settings.tts_chunk_max_characters,
            crossfade_ms=settings.tts_crossfade_ms,
        )
    raise ValueError(
        f"Unsupported TTS provider: {settings.tts_provider}. Use mock, edge_tts, macos_say or chattts."
    )


def create_bgm_provider(settings: Settings) -> BGMProvider:
    """Build a BGM Provider without coupling task code to file or fixture details."""

    if settings.bgm_provider == "mock":
        return MockBGMProvider()
    if settings.bgm_provider == "local_file":
        return LocalFileBGMProvider(
            source_root=settings.bgm_source_root,
            default_source_path=settings.bgm_source_path,
        )
    raise ValueError(f"Unsupported BGM provider: {settings.bgm_provider}. Use mock or local_file.")


def create_subtitle_alignment_provider(settings: Settings) -> SubtitleAlignmentProvider:
    """Build the configured subtitle alignment Provider."""

    if settings.subtitle_alignment_provider in {"mock", "mock_sentence"}:
        return MockSentenceSubtitleAlignmentProvider()
    raise ValueError(
        f"Unsupported subtitle alignment provider: {settings.subtitle_alignment_provider}. "
        "Use mock_sentence."
    )


def create_asr_provider(settings: Settings) -> SubtitleASRProvider:
    """Build the configured audio transcription Provider."""

    if settings.asr_provider == "mock":
        return MockASRProvider()
    if settings.asr_provider == "openai_compatible":
        return OpenAICompatibleASRProvider(
            base_url=settings.asr_base_url,
            api_key=settings.asr_api_key,
            model=settings.asr_model,
            timeout_seconds=settings.asr_timeout_seconds,
        )
    if settings.asr_provider == "siliconflow":
        return SiliconFlowASRProvider(
            base_url=settings.asr_base_url or "https://api.siliconflow.cn/v1",
            api_key=settings.asr_api_key,
            model=settings.asr_model,
            timeout_seconds=settings.asr_timeout_seconds,
        )
    if settings.asr_provider == "aliyun_dashscope":
        return AliyunDashScopeASRProvider(
            base_url=settings.asr_base_url or AliyunDashScopeASRProvider.DEFAULT_BASE_URL,
            api_key=settings.asr_api_key,
            model=settings.asr_model,
            timeout_seconds=settings.asr_timeout_seconds,
        )
    if settings.asr_provider == "whisper_cpp":
        from app.providers.whisper_cpp_asr import WhisperCppASRProvider

        return WhisperCppASRProvider(
            binary=settings.asr_binary,
            model_path=settings.asr_model_path,
            timeout_seconds=settings.asr_timeout_seconds,
            threads=settings.asr_threads,
        )
    raise ValueError(
        f"Unsupported ASR provider: {settings.asr_provider}. "
        "Use mock, openai_compatible, siliconflow, aliyun_dashscope or whisper_cpp."
    )
