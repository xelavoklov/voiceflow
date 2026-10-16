"""Transcription process: keeps a faster-whisper model loaded in memory.

Protocol (JSON lines over stdin/stdout):
  -> {"audio": "/tmp/x.wav", "language": "ru" | null}
  <- {"text": "..."}  or  {"error": "..."}
The first line the worker prints is {"ready": true, "device": "cuda|cpu", "model": "..."}.
"""
import ctypes
import glob
import json
import os
import sys


def preload_cuda_libs():
    """pip-installed cublas/cudnn live in site-packages, not in system library paths."""
    import site

    # Extra directories with CUDA libraries: VOICEFLOW_CUDA_LIBS=/path/to/site-packages (":"-separated)
    extra = [p for p in os.environ.get("VOICEFLOW_CUDA_LIBS", "").split(":") if p]
    for base in site.getsitepackages() + extra:
        for pattern in ("nvidia/cublas/lib/libcublas*.so*", "nvidia/cudnn/lib/libcudnn*.so*"):
            for path in sorted(glob.glob(os.path.join(base, pattern))):
                try:
                    ctypes.CDLL(path, mode=ctypes.RTLD_GLOBAL)
                except OSError:
                    pass


def read_wav(path):
    """16-bit PCM wav -> float32 [-1, 1]; recordings are already 16 kHz mono, so PyAV is not needed."""
    import wave

    import numpy as np

    with wave.open(path, "rb") as wav:
        if wav.getsampwidth() != 2 or wav.getframerate() != 16000:
            raise ValueError("expected a 16-bit 16 kHz wav")
        data = np.frombuffer(wav.readframes(wav.getnframes()), dtype=np.int16)
        if wav.getnchannels() > 1:
            data = data.reshape(-1, wav.getnchannels()).mean(axis=1)
    audio = data.astype(np.float32) / 32768.0
    return trim_wake_transient(audio)


def trim_wake_transient(audio, window=320, limit=9600, threshold=0.1):
    """A waking microphone produces a DC step (~0.3 s, signal pinned at full scale) that confuses
    the model, so cut the start until the window mean returns to zero."""
    start = 0
    while start < min(len(audio), limit) and abs(audio[start : start + window].mean()) > threshold:
        start += window
    return audio[start:]


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
            print(f"[worker] {device} unavailable: {exc}", file=sys.stderr, flush=True)
    raise RuntimeError(f"could not load the model: {last}")


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
