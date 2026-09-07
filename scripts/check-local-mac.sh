#!/usr/bin/env bash

set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# A Mac check without an explicit profile must target the real local profile;
# otherwise the public example config intentionally falls back to Mock.
export AI_VIDEO_PROFILE="${AI_VIDEO_PROFILE:-local_mac_16gb}"
export AI_VIDEO_CONFIG="${AI_VIDEO_CONFIG:-config/config.local.toml}"
args=("--project-root" "${project_root}")

# The default command is intentionally strict about local model files.  Set
# CHECK_LOCAL_MAC_VALIDATE_MODELS=0 for a fast service-only check.
if [[ "${CHECK_LOCAL_MAC_VALIDATE_MODELS:-1}" == "1" ]]; then
  args+=("--validate-models")
fi
if [[ "${CHECK_LOCAL_MAC_REQUIRE_COMPOSE:-0}" == "1" ]]; then
  args+=("--require-compose")
fi
if [[ "${COMFYUI_REQUIRE_MPS:-0}" == "1" ]]; then
  args+=("--require-mps")
fi

if command -v python3 >/dev/null 2>&1; then
  exec python3 "${project_root}/scripts/mac_runtime_preflight.py" "${args[@]}" "$@"
fi
if command -v uv >/dev/null 2>&1; then
  exec uv run --no-project python "${project_root}/scripts/mac_runtime_preflight.py" "${args[@]}" "$@"
fi

echo "找不到 python3 或 uv，无法执行 Mac 本地前置检查。" >&2
exit 1
