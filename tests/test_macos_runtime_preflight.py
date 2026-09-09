from __future__ import annotations

from pathlib import Path

from scripts.mac_runtime_preflight import MacRuntimePreflight, resolve_config_path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _write_minimal_local_config(project_root: Path) -> Path:
    config_path = project_root / "config/config.local.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        """
[image_generation]
provider = "comfyui"
workflow_path = "config/comfyui/flux-schnell-t2i-api.json"
identity_workflow_path = "config/comfyui/flux-schnell-faceid-reference-api.json"

[video_generation]
provider = "comfyui_wan_i2v"
workflow_path = "config/comfyui/wan2.1-i2v-api.json"
""".lstrip(),
        encoding="utf-8",
    )
    return config_path


def test_mac_preflight_resolves_local_profile_config(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    local_config = _write_minimal_local_config(project_root)
    monkeypatch.setenv("AI_VIDEO_PROFILE", "local_mac_16gb")
    monkeypatch.delenv("AI_VIDEO_CONFIG", raising=False)

    path = resolve_config_path(project_root)

    assert path == local_config


def test_mac_preflight_validates_repository_workflows(tmp_path: Path) -> None:
    config_path = _write_minimal_local_config(tmp_path)
    checker = MacRuntimePreflight(
        project_root=PROJECT_ROOT,
        config_path=config_path,
        comfyui_root=PROJECT_ROOT / ".tmp/nonexistent-comfyui",
        validate_models=False,
    )

    checker._load_config()
    checker._check_project_workflows()

    assert checker.results
    assert all(result.ok for result in checker.results)


def test_mac_preflight_reports_missing_model_root(tmp_path: Path) -> None:
    config_path = _write_minimal_local_config(tmp_path)
    checker = MacRuntimePreflight(
        project_root=PROJECT_ROOT,
        config_path=config_path,
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
