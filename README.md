<p align="center"><img src="assets/voiceflow.svg" width="96" alt="VoiceFlow icon"></p>

# VoiceFlow

A floating dictation button for Ubuntu (GNOME on Wayland) — a [Wispr Flow](https://wisprflow.ai)-style
tool that runs fully offline. Click the button, speak, click again: the text is typed
where your cursor is, in any application.

- Local speech recognition with [faster-whisper](https://github.com/SYSTRAN/faster-whisper) (GPU or CPU)
- Always-on-top round button that never steals focus
- Works in any text field; your previous clipboard contents are restored afterwards
- Russian and English UI, any Whisper language for dictation

> Status: early alpha. Tested on Ubuntu 26.04, GNOME Shell 50, Wayland, NVIDIA GPU.

## How it works
| Step | Mechanism |
| --- | --- |
| Window | GTK 3 running through XWayland, so it can stay on top without taking focus |
| Recording | `pw-record` (16 kHz mono) |
| Transcription | `worker.py` in its own venv; the model stays loaded between dictations |
| Insertion | `wl-copy` + a virtual keyboard on `/dev/uinput` pressing Ctrl+V |

## Requirements
- Ubuntu with GNOME on Wayland (other compositors are untested)
- `python3-gi`, `gir1.2-gtk-3.0`, `pipewire` (`pw-record`), `wl-clipboard`, [`uv`](https://docs.astral.sh/uv/)
- Write access to `/dev/uinput` for the desktop user. If it is missing, install the udev rule:
  ```
  sudo cp contrib/70-voiceflow-uinput.rules /etc/udev/rules.d/
  sudo udevadm control --reload && sudo udevadm trigger /dev/uinput
  ```
  Without it the text is only copied to the clipboard.

## Install
```
git clone https://github.com/<you>/voiceflow && cd voiceflow
./install.sh            # CPU, or GPU if cuBLAS 12 + cuDNN 9 are already on the system
./install.sh --cuda     # alternatively: also download CUDA libraries from pip (~1 GB)
./run.sh
./install-desktop.sh    # optional: add a launcher and icon to the application menu
```
The first start downloads the selected model from Hugging Face
(`large-v3-turbo` is about 1.6 GB; pick a smaller one in the menu if you prefer).

## Usage
- **Left click** — start recording: the button expands into a pill with a live waveform. **✓** transcribes and pastes, **✕** discards the recording. **Drag** — move it.
- **Right click** — model, dictation language, paste shortcut (use *Ctrl+Shift+V* in terminals), quit.
- Settings are stored in `~/.config/voiceflow/config.json`.
- Set `VOICEFLOW_DEBUG=1` to print what is pasted (off by default for privacy).

### Optional environment (`~/.config/voiceflow/env`, sourced by `run.sh`)
```
export HF_ENDPOINT=https://hf-mirror.com                       # Hugging Face mirror
export VOICEFLOW_CUDA_LIBS=/path/to/venv/lib/python3.X/site-packages   # reuse cuBLAS/cuDNN from another venv
VOICEFLOW_NO_PROXY=1                                            # ignore proxy variables
```

## Troubleshooting
- **First word is cut off** — some sound cards produce a loud click when the mic wakes up from suspend.
  VoiceFlow trims it, but speak half a second after the button turns red, or disable source suspend in WirePlumber.
- **Recording is distorted** — lower the microphone level (`pactl set-source-volume @DEFAULT_SOURCE@ 20%`).
- **Nothing is pasted** — check the udev rule above; use *Ctrl+Shift+V* in terminals.
- **No global hotkey** — Wayland has no portable way to register one. Bind a GNOME custom shortcut instead (planned).

## License
MIT

---

## По-русски
Плавающая кнопка диктовки для Ubuntu (GNOME/Wayland), аналог Wispr Flow, полностью офлайн.
Клик — запись, ещё клик — распознавание локальной моделью whisper и вставка текста туда, где стоит курсор.

Установка: `./install.sh`, затем `./run.sh` (ярлык в меню — `./install-desktop.sh`).
Для вставки нужен доступ к `/dev/uinput` (правило udev в `contrib/`). Правая кнопка мыши — модель, язык, режим вставки.
Интерфейс автоматически русский, если системная локаль русская.
