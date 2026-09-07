from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from app.media.visual_quality_profiles import (
    recommend_visual_quality_profile,
    visual_quality_recommendation_reason,
)


def test_quality_profile_recommendation_uses_memory_bands_not_gpu_names() -> None:
    assert recommend_visual_quality_profile(None) == "local_safe"
    assert recommend_visual_quality_profile(8.0) == "local_safe"
    assert recommend_visual_quality_profile(12.0) == "local_balanced"
    assert recommend_visual_quality_profile(16.0) == "high_quality"


def test_quality_profile_recommendation_rejects_invalid_memory() -> None:
    with pytest.raises(ValueError):
        recommend_visual_quality_profile(0)
    with pytest.raises(ValueError):
        recommend_visual_quality_profile(float("nan"))


def test_quality_profile_recommendation_has_smoke_boundary() -> None:
    assert "3 秒 smoke" in visual_quality_recommendation_reason(None)
    assert "local_balanced" in visual_quality_recommendation_reason(12)


def test_quality_profile_cli_supports_explicit_memory_and_json() -> None:
    script_path = Path(__file__).parents[1] / "scripts" / "recommend-quality-profile.py"
    spec = importlib.util.spec_from_file_location("recommend_quality_profile", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.main(["--vram-gb", "12", "--json"]) == 0
