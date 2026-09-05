#!/usr/bin/env bash

set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_root"

export AI_VIDEO_CONFIG="${AI_VIDEO_CONFIG:-config/config.local.toml}"
export AI_VIDEO_PROFILE="${AI_VIDEO_PROFILE:-local_mac_16gb}"

echo "Using local profile: $AI_VIDEO_CONFIG"
echo "API: http://127.0.0.1:8000"
echo "Ollama: http://127.0.0.1:11434"

exec uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
