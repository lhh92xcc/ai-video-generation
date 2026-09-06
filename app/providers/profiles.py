"""Safe, configuration-backed Provider Profiles for runtime selection."""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field, replace

from app.config import Settings
from app.providers.asr import SubtitleASRProvider
from app.providers.image_generation import ImageGenerationProvider
from app.providers.video_generation import VideoGenerationProvider


class ProviderProfileError(Exception):
    """A user-selectable runtime Provider Profile cannot be resolved or used."""

    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class ASRProviderProfileError(ProviderProfileError):
    """A user-selectable ASR profile cannot be resolved or used."""


class VisualProviderProfileError(ProviderProfileError):
    """A user-selectable image or video profile cannot be resolved or used."""


@dataclass(frozen=True, slots=True)
class ASRProviderProfile:
    """Internal profile metadata; the API never exposes ``api_key``."""

    profile_id: str
    label: str
    provider: str
    model: str
    base_url: str
    api_key_env: str | None
    configured: bool
    default: bool
    api_key: str | None = field(default=None, repr=False, compare=False)

    def as_public_dict(self) -> dict[str, object]:
        """Return the allow-listed fields used by the frontend dropdown."""

        return {
            "profile_id": self.profile_id,
            "capability": "asr",
            "label": self.label,
            "provider": self.provider,
            "model": self.model,
            "base_url": self.base_url,
            "api_key_env": self.api_key_env,
            "configured": self.configured,
            "default": self.default,
        }


class ASRProviderProfileRegistry:
    """Resolve built-in ASR profiles without storing secrets in the database."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._profiles = self._build_profiles(settings)
        self._providers: dict[str, SubtitleASRProvider] = {}
        self._lock = asyncio.Lock()
        self._default_profile_id = self._resolve_default_profile_id(settings.asr_provider)

    @property
    def default_profile_id(self) -> str:
        return self._default_profile_id

    def list_profiles(self, capability: str = "asr") -> list[ASRProviderProfile]:
        if capability != "asr":
            raise ASRProviderProfileError(
                "PROVIDER_CAPABILITY_NOT_SUPPORTED",
                f"Provider profile capability {capability!r} is not supported",
            )
        return list(self._profiles.values())

    def resolve(self, profile_id: str | None) -> ASRProviderProfile:
        selected_id = profile_id or self._default_profile_id
        profile = self._profiles.get(selected_id)
        if profile is None:
            raise ASRProviderProfileError(
                "PROVIDER_PROFILE_NOT_FOUND",
                f"Provider profile {selected_id!r} was not found",
            )
        return profile

    def ensure_configured(self, profile: ASRProviderProfile) -> None:
        if profile.configured:
            return
        raise ASRProviderProfileError(
            "PROVIDER_PROFILE_NOT_CONFIGURED",
            f"Provider profile {profile.profile_id!r} is not configured; set {profile.api_key_env}",
            status_code=409,
        )

    async def get_provider(self, profile_id: str | None) -> SubtitleASRProvider:
        profile = self.resolve(profile_id)
        self.ensure_configured(profile)
        cached = self._providers.get(profile.profile_id)
        if cached is not None:
            return cached

        async with self._lock:
            cached = self._providers.get(profile.profile_id)
            if cached is not None:
                return cached
            from app.providers.factory import create_asr_provider

            provider_settings = replace(
                self._settings,
                asr_provider=profile.provider,
                asr_base_url=profile.base_url,
                asr_api_key=profile.api_key,
                asr_model=profile.model,
            )
            provider = create_asr_provider(provider_settings)
            self._providers[profile.profile_id] = provider
            return provider

    async def close(self) -> None:
        providers = list(self._providers.values())
        self._providers.clear()
        for provider in providers:
            close_provider = getattr(provider, "close", None)
            if close_provider is not None:
                await close_provider()

    def _resolve_default_profile_id(self, provider: str) -> str:
        profile_id = f"asr.{provider}"
        if profile_id in self._profiles:
            return profile_id
        return "asr.mock"

    @classmethod
    def _build_profiles(cls, settings: Settings) -> dict[str, ASRProviderProfile]:
        definitions = (
            (
                "asr.mock",
                "本地 Mock ASR",
                "mock",
                "mock-asr-v1",
                "",
                None,
            ),
            (
                "asr.aliyun_dashscope",
                "阿里云百炼 Paraformer-v2",
                "aliyun_dashscope",
                "paraformer-v2",
                "https://dashscope.aliyuncs.com",
                "AI_VIDEO_ASR_ALIYUN_API_KEY",
            ),
            (
                "asr.siliconflow",
                "SiliconFlow SenseVoiceSmall",
                "siliconflow",
                "FunAudioLLM/SenseVoiceSmall",
                "https://api.siliconflow.cn/v1",
                "AI_VIDEO_ASR_SILICONFLOW_API_KEY",
            ),
            (
                "asr.openai_compatible",
                "OpenAI-compatible ASR",
                "openai_compatible",
                "whisper-1",
                "https://api.openai.com/v1",
                "AI_VIDEO_ASR_OPENAI_COMPATIBLE_API_KEY",
            ),
        )
        profiles: dict[str, ASRProviderProfile] = {}
        for profile_id, label, provider, default_model, default_url, api_key_env in definitions:
            is_current = settings.asr_provider == provider
            model = settings.asr_model if is_current and settings.asr_model else default_model
            base_url = settings.asr_base_url if is_current and settings.asr_base_url else default_url
            api_key = cls._resolve_api_key(settings, provider, api_key_env)
            profiles[profile_id] = ASRProviderProfile(
                profile_id=profile_id,
                label=label,
                provider=provider,
                model=model,
                base_url=base_url,
                api_key_env=api_key_env,
                configured=provider == "mock" or bool(api_key),
                default=is_current,
                api_key=api_key,
            )
        return profiles

    @staticmethod
    def _resolve_api_key(
        settings: Settings,
        provider: str,
        provider_key_env: str | None,
    ) -> str | None:
        if provider == "mock":
            return None
        provider_key = os.getenv(provider_key_env) if provider_key_env else None
        if provider_key:
            return provider_key
        # Backward-compatible fallback for the existing single-profile .env.
        if settings.asr_provider == provider:
            return settings.asr_api_key
        return None


@dataclass(frozen=True, slots=True)
class RuntimeProviderProfile:
    """Safe metadata and resolved credentials for a visual Provider Profile."""

    profile_id: str
    capability: str
    label: str
    provider: str
    model: str
    base_url: str
    api_key_env: str | None
    configured: bool
    default: bool
    api_key: str | None = field(default=None, repr=False, compare=False)

    def as_public_dict(self) -> dict[str, object]:
        """Return only fields that are safe for the frontend and operator UI."""

        return {
            "profile_id": self.profile_id,
            "capability": self.capability,
            "label": self.label,
            "provider": self.provider,
            "model": self.model,
            "base_url": self.base_url,
            "api_key_env": self.api_key_env,
            "configured": self.configured,
            "default": self.default,
        }


class VisualProviderProfileRegistry:
    """Resolve image/video profiles without persisting secrets or config files.

    Profiles are intentionally built in. A profile selects an existing Provider
    adapter and a safe model/endpoint preset; the actual API key is resolved from
    an environment variable at runtime and never leaves this process.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._profiles = self._build_profiles(settings)
        self._image_providers: dict[str, ImageGenerationProvider] = {}
        self._video_providers: dict[str, VideoGenerationProvider] = {}
        self._identity_providers: dict[str, ImageGenerationProvider] = {}
        self._lock = asyncio.Lock()

    def list_profiles(self, capability: str) -> list[RuntimeProviderProfile]:
        if capability not in {"image", "video"}:
            raise VisualProviderProfileError(
                "PROVIDER_CAPABILITY_NOT_SUPPORTED",
                f"Provider profile capability {capability!r} is not supported",
            )
        return [
            profile
            for profile in self._profiles.values()
            if profile.capability == capability
        ]

    def default_profile_id(self, capability: str) -> str:
        profiles = self.list_profiles(capability)
        selected = next((profile for profile in profiles if profile.default), None)
        if selected is not None:
            return selected.profile_id
        return profiles[0].profile_id

    def resolve(self, capability: str, profile_id: str | None) -> RuntimeProviderProfile:
        selected_id = profile_id or self.default_profile_id(capability)
        profile = self._profiles.get(selected_id)
        if profile is None or profile.capability != capability:
            raise VisualProviderProfileError(
                "PROVIDER_PROFILE_NOT_FOUND",
                f"Provider profile {selected_id!r} was not found for {capability}",
            )
        return profile

    def ensure_configured(self, profile: RuntimeProviderProfile) -> None:
        if profile.configured:
            return
        raise VisualProviderProfileError(
            "PROVIDER_PROFILE_NOT_CONFIGURED",
            f"Provider profile {profile.profile_id!r} is not configured; set {profile.api_key_env}",
            status_code=409,
        )

    async def get_image_provider(self, profile_id: str | None) -> ImageGenerationProvider:
        profile = self.resolve("image", profile_id)
        self.ensure_configured(profile)
        cached = self._image_providers.get(profile.profile_id)
        if cached is not None:
            return cached

        async with self._lock:
            cached = self._image_providers.get(profile.profile_id)
            if cached is not None:
                return cached
            from app.providers.factory import create_image_generation_provider

            provider_settings = replace(
                self._settings,
                image_provider=profile.provider,
                image_base_url=profile.base_url,
                image_api_key=profile.api_key,
                image_model=profile.model,
            )
            provider = create_image_generation_provider(provider_settings)
            self._image_providers[profile.profile_id] = provider
            return provider

    async def get_identity_image_provider(
        self,
        profile_id: str | None,
    ) -> ImageGenerationProvider | None:
        profile = self.resolve("image", profile_id)
        self.ensure_configured(profile)
        if profile.provider != "comfyui":
            return None
        cache_key = f"identity:{profile.profile_id}"
        cached = self._identity_providers.get(cache_key)
        if cached is not None:
            return cached

        async with self._lock:
            cached = self._identity_providers.get(cache_key)
            if cached is not None:
                return cached
            from app.providers.factory import create_identity_image_generation_provider

            provider_settings = replace(
                self._settings,
                image_provider=profile.provider,
                image_base_url=profile.base_url,
                image_api_key=profile.api_key,
                image_model=profile.model,
            )
            provider = create_identity_image_generation_provider(provider_settings)
            if provider is not None:
                self._identity_providers[cache_key] = provider
            return provider

    async def get_video_provider(self, profile_id: str | None) -> VideoGenerationProvider:
        profile = self.resolve("video", profile_id)
        self.ensure_configured(profile)
        cached = self._video_providers.get(profile.profile_id)
        if cached is not None:
            return cached

        async with self._lock:
            cached = self._video_providers.get(profile.profile_id)
            if cached is not None:
                return cached
            from app.providers.factory import create_video_generation_provider

            provider_settings = replace(
                self._settings,
                video_provider=profile.provider,
                video_base_url=profile.base_url,
                video_api_key=profile.api_key,
                video_model=profile.model,
            )
            provider = create_video_generation_provider(provider_settings)
            self._video_providers[profile.profile_id] = provider
            return provider

    async def close(self) -> None:
        providers = [
            *self._image_providers.values(),
            *self._video_providers.values(),
            *self._identity_providers.values(),
        ]
        self._image_providers.clear()
        self._video_providers.clear()
        self._identity_providers.clear()
        closed: set[int] = set()
        for provider in providers:
            provider_id = id(provider)
            if provider_id in closed:
                continue
            closed.add(provider_id)
            close_provider = getattr(provider, "close", None)
            if close_provider is not None:
                await close_provider()

    @classmethod
    def _build_profiles(cls, settings: Settings) -> dict[str, RuntimeProviderProfile]:
        definitions = (
            ("image.mock", "本地 Mock 参考图", "image", "mock", "mock-reference-v1", "", None),
            (
                "image.comfyui",
                "ComfyUI Flux Schnell（本地）",
                "image",
                "comfyui",
                "flux1-schnell-Q4_K_S.gguf",
                "http://127.0.0.1:8188",
                None,
            ),
            (
                "image.siliconflow",
                "SiliconFlow Kolors",
                "image",
                "siliconflow",
                "Kwai-Kolors/Kolors",
                "https://api.siliconflow.cn",
                "AI_VIDEO_IMAGE_SILICONFLOW_API_KEY",
            ),
            (
                "image.openai_compatible",
                "OpenAI-compatible 图片 Provider",
                "image",
                "openai_compatible",
                "dall-e-3",
                "https://api.openai.com/v1",
                "AI_VIDEO_IMAGE_OPENAI_COMPATIBLE_API_KEY",
            ),
            ("video.mock", "本地 Mock 视频", "video", "mock", "mock-video-v1", "", None),
            (
                "video.local_fixture",
                "FFmpeg 可播放 Fixture（本地）",
                "video",
                "local_fixture",
                "local-fixture-v1",
                "",
                None,
            ),
            (
                "video.ffmpeg_motion",
                "FFmpeg Ken-Burns（本地回退）",
                "video",
                "ffmpeg_motion",
                "ffmpeg-motion-v1",
                "",
                None,
            ),
            (
                "video.comfyui_wan_i2v",
                "ComfyUI Wan2.1 I2V（本地）",
                "video",
                "comfyui_wan_i2v",
                "wan2.1-i2v",
                "http://127.0.0.1:8188",
                None,
            ),
            (
                "video.siliconflow",
                "SiliconFlow Wan",
                "video",
                "siliconflow",
                "Wan-AI/Wan2.2-T2V-A14B",
                "https://api.siliconflow.cn",
                "AI_VIDEO_VIDEO_SILICONFLOW_API_KEY",
            ),
            (
                "video.openai_compatible",
                "OpenAI-compatible 视频 Provider",
                "video",
                "openai_compatible",
                "video-model",
                "https://api.openai.com/v1",
                "AI_VIDEO_VIDEO_OPENAI_COMPATIBLE_API_KEY",
            ),
        )
        profiles: dict[str, RuntimeProviderProfile] = {}
        for profile_id, label, capability, provider, default_model, default_url, api_key_env in definitions:
            if capability == "image":
                current_provider = settings.image_provider
                current_model = settings.image_model
                current_url = settings.image_base_url
                current_key = settings.image_api_key
            else:
                current_provider = settings.video_provider
                current_model = settings.video_model
                current_url = settings.video_base_url
                current_key = settings.video_api_key
            is_current = current_provider == provider
            model = current_model if is_current and current_model else default_model
            base_url = current_url if is_current and current_url else default_url
            api_key = cls._resolve_api_key(
                current_provider,
                current_key,
                provider,
                api_key_env,
            )
            profiles[profile_id] = RuntimeProviderProfile(
                profile_id=profile_id,
                capability=capability,
                label=label,
                provider=provider,
                model=model,
                base_url=base_url,
                api_key_env=api_key_env,
                configured=api_key_env is None or bool(api_key),
                default=is_current,
                api_key=api_key,
            )
        return profiles

    @staticmethod
    def _resolve_api_key(
        current_provider: str,
        current_key: str | None,
        provider: str,
        provider_key_env: str | None,
    ) -> str | None:
        if provider_key_env:
            provider_key = os.getenv(provider_key_env)
            if provider_key:
                return provider_key
        if current_provider == provider:
            return current_key
        return None
