#!/usr/bin/env bash

set -euo pipefail

comfyui_home="${COMFYUI_HOME:-/Users/${USER}/ComfyUI}"
hf_base="${HF_ENDPOINT:-https://hf-mirror.com}"

download_file() {
  local url="$1"
  local target="$2"
  mkdir -p "$(dirname "${target}")"
  if [[ -s "${target}" ]]; then
    echo "exists: ${target}"
    return
  fi
  echo "download: ${target}"
  # Hugging Face mirrors occasionally reset long-lived TLS connections. Keep
  # the partial file and retry all transient curl errors so rerunning this
  # script is safe and does not restart a multi-GB download.
  curl -L --fail --retry 10 --retry-all-errors --retry-delay 3 --continue-at - \
    "${url}" -o "${target}"
}

# Flux Schnell Q4 and its text/image dependencies.
download_file "${hf_base}/city96/FLUX.1-schnell-gguf/resolve/main/flux1-schnell-Q4_K_S.gguf?download=true" \
  "${comfyui_home}/models/unet/flux1-schnell-Q4_K_S.gguf"
download_file "${hf_base}/city96/t5-v1_1-xxl-encoder-gguf/resolve/main/t5-v1_1-xxl-encoder-Q4_K_S.gguf?download=true" \
  "${comfyui_home}/models/clip/t5-v1_1-xxl-encoder-Q4_K_S.gguf"
download_file "${hf_base}/comfyanonymous/flux_text_encoders/resolve/main/clip_l.safetensors?download=true" \
  "${comfyui_home}/models/clip/clip_l.safetensors"
# The original BFL repository is gated on some mirrors. This public mirror is
# the same ComfyUI-format FLUX autoencoder file and keeps the local setup
# usable without a Hugging Face account/token.
download_file "${FLUX_VAE_URL:-${hf_base}/foxmail/flux_vae/resolve/main/ae.safetensors?download=true}" \
  "${comfyui_home}/models/vae/flux1_vae.safetensors"

# Flux identity adapter assets. PuLID-Flux is the Flux-compatible FaceID path.
download_file "${hf_base}/makisekurisu-jp/PuLID-Flux/resolve/main/pulid/pulid_flux_v0.9.1.safetensors?download=true" \
  "${comfyui_home}/models/pulid/pulid_flux_v0.9.1.safetensors"
download_file "${hf_base}/makisekurisu-jp/PuLID-Flux/resolve/main/clip/EVA02_CLIP_L_336_psz14_s6B.pt?download=true" \
  "${HOME}/.cache/clip/EVA02_CLIP_L_336_psz14_s6B.pt"
for model in 1k3d68 2d106det genderage glintr100 scrfd_10g_bnkps; do
  download_file "${hf_base}/makisekurisu-jp/PuLID-Flux/resolve/main/insightface/models/antelopev2/${model}.onnx?download=true" \
    "${comfyui_home}/models/insightface/models/antelopev2/${model}.onnx"
done

# Wan2.1 I2V 14B quantized for the low-memory ComfyUI wrapper path.
download_file "${hf_base}/city96/Wan2.1-I2V-14B-480P-gguf/resolve/main/wan2.1-i2v-14b-480p-Q4_K_S.gguf?download=true" \
  "${comfyui_home}/models/diffusion_models/wan2.1-i2v-14b-480p-Q4_K_S.gguf"
download_file "${hf_base}/Kijai/WanVideo_comfy/resolve/main/umt5-xxl-enc-fp8_e4m3fn.safetensors?download=true" \
  "${comfyui_home}/models/text_encoders/umt5-xxl-enc-fp8_e4m3fn.safetensors"
download_file "${hf_base}/Kijai/WanVideo_comfy/resolve/main/Wan2_1_VAE_bf16.safetensors?download=true" \
  "${comfyui_home}/models/vae/Wan2_1_VAE_bf16.safetensors"

echo "Target Flux/Wan/PuLID model files are ready."
echo "ChatTTS and MuseTalk use separate MLX/local runtimes and are installed by their setup scripts."
