"""Safe, configuration-backed Provider Profiles for runtime selection."""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field, replace

from app.config import Settings
from app.providers.asr import SubtitleASRProvider


class ASRProviderProfileError(Exception):
    """A user-selectable ASR profile cannot be resolved or used."""

    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


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
