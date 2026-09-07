#!/usr/bin/env python3
"""Recommend a hardware-neutral visual quality profile for a local host.

The command is read-only. It uses an explicit ``--vram-gb`` when supplied;
otherwise it tries the optional ``nvidia-smi`` telemetry command. Missing
telemetry is not a failure and falls back to the conservative profile.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.media.visual_quality_profiles import (
    recommend_visual_quality_profile,
    visual_quality_recommendation_reason,
)


def _detect_vram_gb() -> float | None:
    command = shutil.which("nvidia-smi")
    if command is None:
        return None
    try:
        completed = subprocess.run(
            [command, "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    values: list[float] = []
    for line in completed.stdout.splitlines():
        try:
            value = float(line.strip())
        except ValueError:
            continue
        if value > 0:
            values.append(value / 1024)
    return min(values) if values else None


def _build_payload(vram_gb: float | None, *, source: str) -> dict[str, Any]:
    return {
        "profile_id": recommend_visual_quality_profile(vram_gb),
        "vram_gb": round(vram_gb, 2) if vram_gb is not None else None,
        "source": source,
        "reason": visual_quality_recommendation_reason(vram_gb),
        "requires_smoke_validation": True,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--vram-gb",
        type=float,
        help="Explicit GPU memory in GiB; omit to try nvidia-smi.",
    )
    parser.add_argument("--json", action="store_true", help="Print a machine-readable JSON object.")
    args = parser.parse_args(argv)

    if args.vram_gb is not None:
        vram_gb = args.vram_gb
        source = "explicit"
    else:
        vram_gb = _detect_vram_gb()
        source = "nvidia-smi" if vram_gb is not None else "fallback"
    payload = _build_payload(vram_gb, source=source)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"建议视觉质量档案：{payload['profile_id']}")
        print(f"显存依据：{payload['vram_gb'] or '未读取到'} GB（来源：{payload['source']}）")
        print(str(payload["reason"]))
        print("请先运行 1 个 3 秒 smoke，再决定是否升档；不要直接按档案并发批量生成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
