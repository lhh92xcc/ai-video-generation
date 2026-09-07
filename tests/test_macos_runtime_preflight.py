from __future__ import annotations

from pathlib import Path

from scripts.mac_runtime_preflight import MacRuntimePreflight, resolve_config_path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_mac_preflight_resolves_local_profile_config(monkeypatch) -> None:
    monkeypatch.setenv("AI_VIDEO_PROFILE", "local_mac_16gb")
    monkeypatch.delenv("AI_VIDEO_CONFIG", raising=False)

    path = resolve_config_path(PROJECT_ROOT)

    assert path.name == "config.local.toml"


def test_mac_preflight_validates_repository_workflows() -> None:
    checker = MacRuntimePreflight(
        project_root=PROJECT_ROOT,
        config_path=PROJECT_ROOT / "config/config.local.toml",
        comfyui_root=PROJECT_ROOT / ".tmp/nonexistent-comfyui",
        validate_models=False,
    )

    checker._load_config()
    checker._check_project_workflows()

    assert checker.results
    assert all(result.ok for result in checker.results)


def test_mac_preflight_reports_missing_model_root() -> None:
    checker = MacRuntimePreflight(
        project_root=PROJECT_ROOT,
        config_path=PROJECT_ROOT / "config/config.local.toml",
        comfyui_root=PROJECT_ROOT / ".tmp/nonexistent-comfyui",
        validate_models=True,
    )

    checker._load_config()
    checker._check_comfyui_assets()

    result = next(item for item in checker.results if item.name == "ComfyUI 模型目录")
    assert result.ok is False
    assert result.required is True


def test_mac_check_wrapper_is_read_only_and_forwards_options() -> None:
    wrapper = (PROJECT_ROOT / "scripts/check-local-mac.sh").read_text(encoding="utf-8")

    assert "mac_runtime_preflight.py" in wrapper
    assert "--validate-models" in wrapper
    assert "CHECK_LOCAL_MAC_REQUIRE_COMPOSE" in wrapper
    assert 'AI_VIDEO_PROFILE="${AI_VIDEO_PROFILE:-local_mac_16gb}"' in wrapper
    assert "docker compose up" not in wrapper
