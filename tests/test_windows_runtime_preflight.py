from __future__ import annotations

import json
from pathlib import Path

from scripts.windows_runtime_preflight import WindowsRuntimePreflight


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_windows_preflight_validates_repository_workflows() -> None:
    checker = WindowsRuntimePreflight(
        project_root=PROJECT_ROOT,
        comfyui_root=None,
        require_comfyui=False,
        validate_comfyui_assets=False,
        require_ollama_model=False,
        require_musetalk=False,
    )

    checker._load_config()
    checker._check_project_workflows()

    assert checker.results
    assert all(result.ok for result in checker.results)


def test_windows_preflight_writes_redacted_machine_readable_report(
    tmp_path: Path,
    monkeypatch,
) -> None:
    report_path = tmp_path / "preflight" / "windows-media-preflight.json"
    monkeypatch.setenv("AI_VIDEO_LLM_API_KEY", "do-not-write-this-secret")
    checker = WindowsRuntimePreflight(
        project_root=PROJECT_ROOT,
        comfyui_root=None,
        config_path=PROJECT_ROOT / "config" / "config.windows_gpu.toml",
        require_comfyui=False,
        validate_comfyui_assets=False,
        require_ollama_model=False,
        require_musetalk=False,
        comfyui_url="http://user:password@127.0.0.1:8188?token=secret",
        report_path=report_path,
    )

    assert checker.run() == 0

    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["status"] == "passed"
    assert payload["profile"] == "windows_gpu"
    assert payload["providers"]["image"] == "comfyui"
    assert payload["providers"]["video"] == "comfyui_wan_i2v"
    assert payload["summary"]["total"] == len(payload["checks"])
    assert payload["endpoints"]["comfyui"] == "http://127.0.0.1:8188"
    assert "do-not-write-this-secret" not in report_path.read_text(encoding="utf-8")
    assert "password" not in report_path.read_text(encoding="utf-8")
    assert "token=secret" not in report_path.read_text(encoding="utf-8")


def test_windows_preflight_accepts_an_explicit_config_path(tmp_path: Path) -> None:
    config_path = tmp_path / "custom.toml"
    config_path.write_text(
        '[app]\nprofile = "custom-windows"\n'
        '[llm]\nmodel = "custom-llm"\n'
        '[image_generation]\nprovider = "custom-image"\nmodel = "custom-image-model"\n'
        '[video_generation]\nprovider = "custom-video"\nmodel = "custom-video-model"\n',
        encoding="utf-8",
    )
    checker = WindowsRuntimePreflight(
        project_root=PROJECT_ROOT,
        comfyui_root=None,
        config_path=config_path,
        require_comfyui=False,
        validate_comfyui_assets=False,
        require_ollama_model=False,
        require_musetalk=False,
        report_path=tmp_path / "custom-report.json",
    )

    checker._load_config()
    checker._write_report(0)

    payload = json.loads((tmp_path / "custom-report.json").read_text(encoding="utf-8"))
    assert checker.config["app"]["profile"] == "custom-windows"
    assert payload["profile"] == "custom-windows"
    assert payload["models"] == {
        "llm": "custom-llm",
        "image": "custom-image-model",
        "video": "custom-video-model",
    }
    assert payload["providers"] == {
        "llm": "ollama",
        "image": "custom-image",
        "video": "custom-video",
    }


def test_windows_preflight_requires_comfyui_root_for_model_validation() -> None:
    checker = WindowsRuntimePreflight(
        project_root=PROJECT_ROOT,
        comfyui_root=None,
        require_comfyui=False,
        validate_comfyui_assets=True,
        require_ollama_model=False,
        require_musetalk=False,
    )

    checker._load_config()
    checker._check_comfyui_assets()

    result = next(item for item in checker.results if item.name == "ComfyUI 模型目录")
    assert result.ok is False
    assert result.required is True


def test_windows_launcher_forwards_strict_media_checks() -> None:
    launcher = (PROJECT_ROOT / "scripts" / "start-windows-gpu.ps1").read_text(encoding="utf-8")

    assert "-RequireOllamaModel" in launcher
    assert "-RequireComfyUI" in launcher
    assert "-ValidateComfyUIAssets" in launcher
    assert "COMFYUI_ROOT" in launcher
    assert "-ReportPath" in launcher


def test_windows_health_script_can_save_preflight_report() -> None:
    checker = (PROJECT_ROOT / "scripts" / "check-windows-gpu.ps1").read_text(encoding="utf-8")

    assert "[string]$ReportPath" in checker
    assert "[string]$ConfigPath" in checker
    assert "--config-path" in checker
    assert "--report-path" in checker
    assert "IsPathRooted" in checker


def test_windows_health_check_requires_container_media_runtime_from_launcher() -> None:
    launcher = (PROJECT_ROOT / "scripts" / "start-windows-gpu.ps1").read_text(encoding="utf-8")
    checker = (PROJECT_ROOT / "scripts" / "check-windows-gpu.ps1").read_text(encoding="utf-8")

    assert "-RequireComposeMedia" in launcher
    assert "subtitles/libass" in checker
    assert "docker compose exec -T api ffprobe" in checker
