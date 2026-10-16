#!/bin/bash
# Creates the transcription environment (faster-whisper) in ./.venv
#   ./install.sh          CPU, or GPU if cuBLAS 12 + cuDNN 9 are already available (see README)
#   ./install.sh --cuda   also installs the CUDA libraries from pip (~1 GB)
set -e
cd "$(dirname "$0")"
command -v uv >/dev/null || { echo "uv is required: https://docs.astral.sh/uv/"; exit 1; }
uv venv --clear --python 3.12 .venv
uv pip install --python .venv/bin/python faster-whisper
if [ "$1" = "--cuda" ]; then
    uv pip install --python .venv/bin/python nvidia-cublas-cu12 nvidia-cudnn-cu12
fi
