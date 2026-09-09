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
