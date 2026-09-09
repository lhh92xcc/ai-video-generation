from pathlib import Path


LAUNCHER = Path("scripts/run-novel-production.ps1")


def test_windows_novel_launcher_forwards_new_and_resume_modes() -> None:
    source = LAUNCHER.read_text(encoding="utf-8")

    assert 'param(' in source
    assert 'scripts\\run-novel-production.py' in source
    assert '请提供 -Novel（新建项目）或 -ProjectId（恢复已有项目）中的一个' in source
    assert '"--novel"' in source
    assert '"--project-id"' in source
    assert '"--run-id"' in source
    assert '"--control"' in source
    assert '使用 -Control 时必须同时提供 -ProjectId 和 -RunId' in source
    assert '"--quality-profile"' in source
    assert '"--shot-keyframe-mode"' in source
    assert '"--no-wait"' in source
    assert '$env:AI_VIDEO_PROFILE = "windows_gpu"' in source
    assert '$env:AI_VIDEO_CONFIG = "config/config.windows_gpu.toml"' in source


def test_windows_novel_launcher_does_not_contain_provider_secrets() -> None:
    source = LAUNCHER.read_text(encoding="utf-8")

    assert "API_KEY" not in source
    assert "SECRET_KEY" not in source
