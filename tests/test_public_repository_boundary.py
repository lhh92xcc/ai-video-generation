from __future__ import annotations

import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_private_collaboration_and_runtime_files_are_not_tracked() -> None:
    completed = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr.decode("utf-8", errors="replace")
    tracked = {
        item.decode("utf-8")
        for item in completed.stdout.split(b"\0")
        if item
    }
    forbidden = {
        path
        for path in tracked
        if (
            path == ".env"
            or path.startswith(".env.")
            or path == "AGENTS.md"
            or path.startswith(".ai/")
            or path.startswith("docs/")
            or path == "config/config.local.toml"
            or path.startswith(".tmp/")
            or path.startswith(".models/")
            or path.startswith("local-runtimes/")
        )
    }

    assert not forbidden, "private or generated paths are tracked: " + ", ".join(sorted(forbidden))


def test_public_runtime_guidance_is_not_bound_to_one_gpu_model() -> None:
    public_guidance = (
        PROJECT_ROOT / "README.md",
        PROJECT_ROOT / "config/config.windows_gpu.toml",
        PROJECT_ROOT / "scripts/start-windows-gpu.ps1",
        PROJECT_ROOT / "scripts/check-windows-gpu.ps1",
    )

    for path in public_guidance:
        content = path.read_text(encoding="utf-8").lower()
        assert "4060" not in content, f"固定显卡型号文案出现在 {path}"
