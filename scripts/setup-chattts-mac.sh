#!/usr/bin/env bash

set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
runtime_root="${project_root}/local-runtimes/chattts"
python_bin="${CHAT_TTS_PYTHON:-/opt/homebrew/opt/python@3.11/bin/python3.11}"

if [[ ! -x "${python_bin}" ]]; then
  python_bin="$(command -v python3)"
fi

mkdir -p "${runtime_root}"
if [[ ! -x "${runtime_root}/.venv/bin/python" ]]; then
  "${python_bin}" -m venv "${runtime_root}/.venv"
fi

chat_python="${runtime_root}/.venv/bin/python"
"${chat_python}" -m pip install --upgrade pip
"${chat_python}" -m pip install --upgrade ChatTTS soundfile scipy requests

echo "ChatTTS runtime ready: ${chat_python}"
echo "The first generation downloads ChatTTS weights into ${runtime_root}/assets."
