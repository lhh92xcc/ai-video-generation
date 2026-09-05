#!/usr/bin/env bash

set -euo pipefail

comfyui_home="${COMFYUI_HOME:-/Users/${USER}/ComfyUI}"
python_bin="${COMFYUI_PYTHON:-${comfyui_home}/.venv/bin/python}"
custom_nodes="${comfyui_home}/custom_nodes"

if [[ ! -x "${python_bin}" ]]; then
  echo "ComfyUI Python not found: ${python_bin}" >&2
  echo "Run scripts/setup-comfyui-mac.sh first." >&2
  exit 1
fi

mkdir -p "${custom_nodes}"

clone_node() {
  local repo="$1"
  local directory="$2"
  if [[ ! -d "${custom_nodes}/${directory}" ]]; then
    git clone --depth 1 "https://github.com/${repo}.git" "${custom_nodes}/${directory}"
  fi
}

clone_node "city96/ComfyUI-GGUF" "ComfyUI-GGUF"
clone_node "kijai/ComfyUI-WanVideoWrapper" "ComfyUI-WanVideoWrapper"
clone_node "Kosinkadink/ComfyUI-VideoHelperSuite" "ComfyUI-VideoHelperSuite"
clone_node "sipie800/ComfyUI-PuLID-Flux-Enhanced" "ComfyUI-PuLID-Flux-Enhanced"

"${python_bin}" -m pip install --upgrade pip
"${python_bin}" -m pip install --upgrade gguf
"${python_bin}" -m pip install -r "${custom_nodes}/ComfyUI-WanVideoWrapper/requirements.txt"
"${python_bin}" -m pip install --upgrade imageio-ffmpeg
# The upstream requirements include onnxruntime-gpu unconditionally. CUDA is
# unavailable on Apple Silicon, so install the CPU runtime explicitly.
"${python_bin}" -m pip install --upgrade facexlib insightface onnxruntime ftfy timm

# The PuLID node's EVA-CLIP loader otherwise performs a Hugging Face Hub HEAD
# request even when the model was downloaded into ~/.cache/clip. Apply the
# small offline-cache compatibility patch once so the Mac setup is reproducible
# on networks where Hugging Face TLS is unavailable.
pulid_node="${custom_nodes}/ComfyUI-PuLID-Flux-Enhanced"
pulid_patch="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/config/comfyui/pulid-flux-local-eva.patch"
if [[ -f "${pulid_node}/pulidflux.py" ]] && ! rg -q "local_eva_path" "${pulid_node}/pulidflux.py"; then
  git -C "${pulid_node}" apply --ignore-whitespace --whitespace=nowarn "${pulid_patch}"
fi

echo "Installed target ComfyUI nodes in ${custom_nodes}."
echo "Next: ./scripts/download-local-target-models-mac.sh"
