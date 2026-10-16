"""Процесс распознавания: держит модель faster-whisper в памяти.

Протокол (JSON-строки через stdin/stdout):
  -> {"audio": "/tmp/x.wav", "language": "ru" | null}
  <- {"text": "..."}  или  {"error": "..."}
Первой строкой воркер пишет {"ready": true, "device": "cuda|cpu", "model": "..."}.
"""
import ctypes
import glob
import json
import os
import sys


def preload_cuda_libs():
    """cublas/cudnn из pip-пакетов лежат в site-packages, а не в системных путях."""
    import site

    # Доп. каталоги с CUDA-библиотеками: VOICEFLOW_CUDA_LIBS=/путь/site-packages (через ':')
    extra = [p for p in os.environ.get("VOICEFLOW_CUDA_LIBS", "").split(":") if p]
    for base in site.getsitepackages() + extra:
        for pattern in ("nvidia/cublas/lib/libcublas*.so*", "nvidia/cudnn/lib/libcudnn*.so*"):
            for path in sorted(glob.glob(os.path.join(base, pattern))):
                try:
                    ctypes.CDLL(path, mode=ctypes.RTLD_GLOBAL)
                except OSError:
                    pass


def read_wav(path):
    """16-бит PCM wav -> float32 [-1, 1]; записи уже 16 кГц моно, PyAV не нужен."""
    import wave

    import numpy as np

    with wave.open(path, "rb") as wav:
        if wav.getsampwidth() != 2 or wav.getframerate() != 16000:
            raise ValueError("ожидается 16-бит wav 16 кГц")
        data = np.frombuffer(wav.readframes(wav.getnframes()), dtype=np.int16)
        if wav.getnchannels() > 1:
            data = data.reshape(-1, wav.getnchannels()).mean(axis=1)
    return data.astype(np.float32) / 32768.0


def load_model(name):
    from faster_whisper import WhisperModel

    forced = os.environ.get("VOICEFLOW_DEVICE")
    attempts = [forced] if forced else ["cuda", "cpu"]
    last = None
    for device in attempts:
        try:
            compute = "float16" if device == "cuda" else "int8"
            return WhisperModel(name, device=device, compute_type=compute), device
        except Exception as exc:  # noqa: BLE001
            last = exc
            print(f"[worker] {device} недоступен: {exc}", file=sys.stderr, flush=True)
    raise RuntimeError(f"не удалось загрузить модель: {last}")


def main():
    model_name = sys.argv[1] if len(sys.argv) > 1 else "large-v3-turbo"
    preload_cuda_libs()
    model, device = load_model(model_name)
    print(json.dumps({"ready": True, "device": device, "model": model_name}), flush=True)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            segments, _info = model.transcribe(
                read_wav(req["audio"]),
                language=req.get("language"),
                vad_filter=True,
                beam_size=5,
                condition_on_previous_text=False,
            )
            text = " ".join(s.text.strip() for s in segments).strip()
            print(json.dumps({"text": text}, ensure_ascii=False), flush=True)
        except Exception as exc:  # noqa: BLE001
            print(json.dumps({"error": str(exc)}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
