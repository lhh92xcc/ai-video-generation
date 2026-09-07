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
