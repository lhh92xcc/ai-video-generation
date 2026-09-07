#!/usr/bin/env bash

set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
comfyui_home="${COMFYUI_HOME:-/Users/${USER}/ComfyUI}"
python_bin="${COMFYUI_PYTHON:-}"

if [[ -z "${python_bin}" ]]; then
  if [[ -x /opt/homebrew/opt/python@3.11/bin/python3.11 ]]; then
    python_bin="/opt/homebrew/opt/python@3.11/bin/python3.11"
  else
    python_bin="$(command -v python3)"
  fi
fi

if [[ ! -d "${comfyui_home}/.git" ]]; then
  git clone --depth 1 https://github.com/comfyanonymous/ComfyUI.git "${comfyui_home}"
fi

if [[ ! -x "${comfyui_home}/.venv/bin/python" ]]; then
  "${python_bin}" -m venv "${comfyui_home}/.venv"
fi

comfy_python="${comfyui_home}/.venv/bin/python"
comfy_pip="${comfyui_home}/.venv/bin/pip"
require_mps="${COMFYUI_REQUIRE_MPS:-0}"

"${comfy_pip}" install --upgrade pip
torch_packages=(torch torchvision torchaudio)
if [[ -n "${COMFYUI_TORCH_INDEX_URL:-}" ]]; then
  "${comfy_pip}" install --upgrade "${torch_packages[@]}" --index-url "${COMFYUI_TORCH_INDEX_URL}"
else
  # The CPU-only PyTorch wheel disables MPS on Apple Silicon.  Use the normal
  # PyPI wheels by default; callers can override the index for an approved
  # nightly/staged wheel with COMFYUI_TORCH_INDEX_URL.
  "${comfy_pip}" install --upgrade "${torch_packages[@]}"
fi
"${comfy_pip}" install -r "${comfyui_home}/requirements.txt"

"${comfy_python}" - <<'PY'
import os
import torch

mps_built = bool(torch.backends.mps.is_built())
mps_available = bool(torch.backends.mps.is_available())
if not (mps_built and mps_available):
    if os.environ.get("COMFYUI_REQUIRE_MPS", "0") == "1":
        raise SystemExit("MPS is required but this PyTorch/Mac environment cannot provide it")
    print(
        f"ComfyUI PyTorch ready: {torch.__version__}, device=cpu "
        "(MPS unavailable; set COMFYUI_REQUIRE_MPS=1 to make this a hard failure)"
    )
else:
    print(f"ComfyUI PyTorch ready: {torch.__version__}, device=mps")
PY

target_files=(
  "${comfyui_home}/models/unet/flux1-schnell-Q4_K_S.gguf"
  "${comfyui_home}/models/clip/t5-v1_1-xxl-encoder-Q4_K_S.gguf"
  "${comfyui_home}/models/clip/clip_l.safetensors"
  "${comfyui_home}/models/vae/flux1_vae.safetensors"
  "${comfyui_home}/models/diffusion_models/wan2.1-i2v-14b-480p-Q4_K_S.gguf"
  "${comfyui_home}/models/text_encoders/umt5-xxl-enc-fp8_e4m3fn.safetensors"
  "${comfyui_home}/models/vae/Wan2_1_VAE_bf16.safetensors"
)
missing=0
for target_file in "${target_files[@]}"; do
  if [[ ! -s "${target_file}" ]]; then
    echo "ComfyUI target model is missing: ${target_file}" >&2
    missing=1
  fi
done
if [[ "${missing}" -ne 0 ]]; then
  echo "Run scripts/download-local-target-models-mac.sh, then start ComfyUI again." >&2
fi

echo "ComfyUI home: ${comfyui_home}"
echo "Project root: ${project_root}"
echo "Start command: ${project_root}/scripts/start-comfyui-mac.sh"
echo "Emergency fallback: COMFYUI_DISABLE_SMART_MEMORY=1 COMFYUI_RESERVE_VRAM_GB=4 ${project_root}/scripts/start-comfyui-mac.sh"
