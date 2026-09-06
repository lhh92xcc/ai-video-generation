from __future__ import annotations

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


def test_windows_health_check_requires_container_media_runtime_from_launcher() -> None:
    launcher = (PROJECT_ROOT / "scripts" / "start-windows-gpu.ps1").read_text(encoding="utf-8")
    checker = (PROJECT_ROOT / "scripts" / "check-windows-gpu.ps1").read_text(encoding="utf-8")

    assert "-RequireComposeMedia" in launcher
    assert "subtitles/libass" in checker
    assert "docker compose exec -T api ffprobe" in checker
