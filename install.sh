#!/bin/bash
# Ставит окружение для распознавания (faster-whisper).
# CUDA-библиотеки (cublas 12 + cudnn 9) нужны для GPU. Если они уже есть
# в другом venv (например, у PyTorch), укажите его site-packages:
#   VOICEFLOW_CUDA_LIBS=/путь/к/site-packages ./run.sh
# Иначе: ./install.sh --cuda  (скачает ~1 ГБ pip-пакетов)
set -e
cd "$(dirname "$0")"
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python faster-whisper
if [ "$1" = "--cuda" ]; then
    uv pip install --python .venv/bin/python nvidia-cublas-cu12 nvidia-cudnn-cu12
fi
