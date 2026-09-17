from __future__ import annotations

import pytest

from app.config import load_settings
from app.providers.profiles import ProviderProfileError, VisualProviderProfileRegistry


def test_builtin_quality_profiles_use_exact_9_16_canvases() -> None:
    registry = VisualProviderProfileRegistry(load_settings("config/config.example.toml"))

    for profile in registry.list_quality_profiles():
        assert profile.aspect_ratio == "9:16"
        assert profile.image_width * 16 == profile.image_height * 9
        assert profile.video_width * 16 == profile.video_height * 9
        assert profile.image_width % 8 == 0
        assert profile.image_height % 8 == 0
        assert profile.video_width % 8 == 0
        assert profile.video_height % 8 == 0


def test_unknown_quality_snapshot_maps_to_stable_provider_error() -> None:
    registry = VisualProviderProfileRegistry(load_settings("config/config.example.toml"))
    snapshot = registry.quality_snapshot("local_safe")
    snapshot["profile_id"] = "removed_profile"

    with pytest.raises(ProviderProfileError) as caught:
        registry.settings_for_quality(
            quality_profile_id="removed_profile",
            quality_snapshot=snapshot,
        )

    assert caught.value.code == "VISUAL_QUALITY_SNAPSHOT_INVALID"


def test_quality_settings_keep_the_selected_profile_id() -> None:
    registry = VisualProviderProfileRegistry(load_settings("config/config.windows_gpu.toml"))

    selected = registry.settings_for_quality("local_safe")

    assert selected.visual_quality_profile == "local_safe"
    assert (selected.image_width, selected.image_height) == (432, 768)
    assert (selected.video_output_width, selected.video_output_height) == (288, 512)


def test_local_visual_profiles_use_the_runtime_comfyui_endpoint() -> None:
    example_registry = VisualProviderProfileRegistry(
        load_settings("config/config.example.toml")
    )
    assert (
        example_registry.resolve("image", "image.comfyui").base_url
        == "http://127.0.0.1:8188"
    )
    assert (
        example_registry.resolve("video", "video.comfyui_wan_i2v").base_url
        == "http://127.0.0.1:8188"
    )

    windows_registry = VisualProviderProfileRegistry(
        load_settings("config/config.windows_gpu.toml")
    )
    assert (
        windows_registry.resolve("image", "image.comfyui").base_url
        == "http://host.docker.internal:8188"
    )
    assert (
        windows_registry.resolve("video", "video.comfyui_wan_i2v").base_url
        == "http://host.docker.internal:8188"
    )


def test_jimeng_profile_stays_disabled_until_protocol_is_explicitly_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AI_VIDEO_JIMENG_API_KEY", raising=False)
    monkeypatch.delenv("AI_VIDEO_JIMENG_PROTOCOL_READY", raising=False)
    registry = VisualProviderProfileRegistry(load_settings("config/config.example.toml"))

    profile = registry.resolve("video", "video.jimeng")

    assert profile.configured is False
    assert profile.label == "即梦视频（适配器预留）"


def test_jimeng_unconfigured_error_explains_all_enablement_requirements(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "AI_VIDEO_JIMENG_API_KEY",
        "AI_VIDEO_JIMENG_BASE_URL",
        "AI_VIDEO_JIMENG_MODEL",
        "AI_VIDEO_JIMENG_PROTOCOL_READY",
    ):
        monkeypatch.delenv(name, raising=False)
    registry = VisualProviderProfileRegistry(load_settings("config/config.example.toml"))

    with pytest.raises(ProviderProfileError) as caught:
        registry.ensure_configured(registry.resolve("video", "video.jimeng"))

    assert caught.value.code == "PROVIDER_PROFILE_NOT_CONFIGURED"
    assert "AI_VIDEO_JIMENG_PROTOCOL_READY" in caught.value.message
    assert "AI_VIDEO_JIMENG_BASE_URL" in caught.value.message


def test_jimeng_profile_becomes_configured_only_with_key_and_protocol_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AI_VIDEO_JIMENG_API_KEY", "test-key")
    monkeypatch.setenv("AI_VIDEO_JIMENG_BASE_URL", "https://jimeng.example")
    monkeypatch.setenv("AI_VIDEO_JIMENG_MODEL", "official-model")
    monkeypatch.setenv("AI_VIDEO_JIMENG_PROTOCOL_READY", "1")
    registry = VisualProviderProfileRegistry(load_settings("config/config.example.toml"))

    profile = registry.resolve("video", "video.jimeng")

    assert profile.configured is True
    assert profile.base_url == "https://jimeng.example"
    assert profile.model == "official-model"
    assert profile.as_public_dict()["api_key_env"] == "AI_VIDEO_JIMENG_API_KEY"
    assert "api_key" not in profile.as_public_dict()


def test_jimeng_profile_stays_disabled_if_protocol_is_enabled_without_endpoint_or_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AI_VIDEO_JIMENG_API_KEY", "test-key")
    monkeypatch.setenv("AI_VIDEO_JIMENG_PROTOCOL_READY", "1")
    monkeypatch.delenv("AI_VIDEO_JIMENG_BASE_URL", raising=False)
    monkeypatch.delenv("AI_VIDEO_JIMENG_MODEL", raising=False)
    registry = VisualProviderProfileRegistry(load_settings("config/config.example.toml"))

    assert registry.resolve("video", "video.jimeng").configured is False


def test_jimeng_profile_does_not_reuse_generic_video_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_VIDEO_VIDEO_PROVIDER", "jimeng")
    monkeypatch.setenv("AI_VIDEO_VIDEO_BASE_URL", "https://jimeng.example")
    monkeypatch.setenv("AI_VIDEO_VIDEO_MODEL", "official-model")
    monkeypatch.setenv("AI_VIDEO_VIDEO_API_KEY", "generic-key")
    monkeypatch.setenv("AI_VIDEO_JIMENG_PROTOCOL_READY", "1")
    monkeypatch.delenv("AI_VIDEO_JIMENG_API_KEY", raising=False)
    monkeypatch.delenv("AI_VIDEO_JIMENG_BASE_URL", raising=False)
    monkeypatch.delenv("AI_VIDEO_JIMENG_MODEL", raising=False)

    registry = VisualProviderProfileRegistry(load_settings("config/config.example.toml"))

    assert registry.resolve("video", "video.jimeng").configured is False


def test_distilled_quality_selects_matched_workflow_and_snapshot() -> None:
    registry = VisualProviderProfileRegistry(load_settings("config/config.example.toml"))
    snapshot = registry.quality_snapshot("local_distilled")
    selected = registry.settings_for_quality("local_distilled", snapshot)
    assert selected.video_workflow_path == "config/comfyui/wan2.1-i2v-lightx2v-api.json"
    assert (selected.video_steps, selected.video_cfg) == (4, 1.0)
    assert (selected.video_output_width, selected.video_output_height, selected.video_fps) == (288, 512, 12)
    assert registry.settings_for_quality("local_safe").video_workflow_path.endswith("wan2.1-i2v-api.json")
