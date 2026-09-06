from __future__ import annotations

import pytest

from app.config import load_settings
from app.providers.profiles import ProviderProfileError, VisualProviderProfileRegistry


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
