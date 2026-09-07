#!/usr/bin/env bash

set -euo pipefail

comfyui_home="${COMFYUI_HOME:-/Users/${USER}/ComfyUI}"
comfy_python="${COMFYUI_PYTHON:-${comfyui_home}/.venv/bin/python}"
comfyui_host="${COMFYUI_HOST:-127.0.0.1}"
comfyui_port="${COMFYUI_PORT:-8188}"
reserve_vram_gb="${COMFYUI_RESERVE_VRAM_GB:-1}"

if [[ ! -x "${comfy_python}" ]]; then
  echo "找不到 ComfyUI Python：${comfy_python}" >&2
  echo "先运行 ./scripts/setup-comfyui-mac.sh，或设置 COMFYUI_PYTHON。" >&2
  exit 1
fi
if [[ ! -f "${comfyui_home}/main.py" ]]; then
  echo "找不到 ComfyUI：${comfyui_home}/main.py" >&2
  echo "请设置 COMFYUI_HOME 指向 ComfyUI 安装目录。" >&2
  exit 1
fi

args=(
  "${comfyui_home}/main.py"
  --listen "${comfyui_host}"
  --port "${comfyui_port}"
  --cpu-vae
  --cache-none
  --reserve-vram "${reserve_vram_gb}"
)

# On Apple Silicon, --disable-smart-memory forces aggressive CPU offload and
# can turn a shared-memory MPS run into an unexpectedly slow CPU run. Keep it
# opt-in for emergency memory pressure only.
if [[ "${COMFYUI_DISABLE_SMART_MEMORY:-0}" == "1" ]]; then
  args+=(--disable-smart-memory)
fi

echo "ComfyUI: ${comfyui_home}"
echo "Endpoint: http://${comfyui_host}:${comfyui_port}"
echo "MPS-first mode: reserve ${reserve_vram_gb} GiB; CPU VAE; smart memory enabled"
if [[ "${COMFYUI_DISABLE_SMART_MEMORY:-0}" == "1" ]]; then
  echo "警告：已启用 COMFYUI_DISABLE_SMART_MEMORY=1，可能显著降低 MPS 推理速度。" >&2
fi

exec "${comfy_python}" "${args[@]}"
