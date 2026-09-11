from pathlib import Path


LAUNCHER = Path("scripts/run-portfolio-demo.ps1")


def test_portfolio_launcher_is_hardware_agnostic_and_uses_quality_snapshot() -> None:
    source = LAUNCHER.read_text(encoding="utf-8")

    assert "不绑定具体 GPU 型号" in source
    assert 'AI_VIDEO_PROFILE = "windows_gpu"' in source
    assert 'AI_VIDEO_VISUAL_QUALITY_PROFILE = $QualityProfile' in source
    assert '"--quality-profile", $QualityProfile' in source
    assert '"--shots", [string]$Shots' in source
    assert '"--shot-duration", [string]$ShotDuration' in source
    assert '"--output-dir", $OutputDir' in source
    assert "--resume" in source
    assert "--stop-after-shot" in source


def test_portfolio_launcher_keeps_host_runner_separate_from_docker_paths() -> None:
    source = LAUNCHER.read_text(encoding="utf-8")

    assert '$env:AI_VIDEO_STORAGE_PROVIDER = "local"' in source
    assert '$env:AI_VIDEO_STORAGE_URI_PREFIX = "local://"' in source
    assert '$env:AI_VIDEO_IMAGE_BASE_URL = $ComfyUIUrl' in source
    assert '$env:AI_VIDEO_VIDEO_BASE_URL = $ComfyUIUrl' in source
    assert '$env:AI_VIDEO_LLM_BASE_URL = "$OllamaUrl/v1"' in source
    assert "API_KEY" not in source
    assert "SECRET_KEY" not in source
