#!/bin/bash
# Запуск VoiceFlow. Переменные можно переопределить снаружи.
cd "$(dirname "$0")"
# Модели качаются с зеркала и напрямую: через прокси HuggingFace очень медленный.
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY all_proxy
export HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}"
export HF_HUB_DISABLE_XET=1
# cublas 12 + cudnn 9 для GPU: берём из другого venv, если они там есть.
if [ -z "$VOICEFLOW_CUDA_LIBS" ] && [ -d "$HOME/ComfyUI/.venv/lib/python3.10/site-packages/nvidia" ]; then
    export VOICEFLOW_CUDA_LIBS="$HOME/ComfyUI/.venv/lib/python3.10/site-packages"
fi
exec /usr/bin/python3 app.py "$@"
