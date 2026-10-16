#!/usr/bin/python3
"""VoiceFlow: floating dictation button for Ubuntu (GNOME/Wayland).

Click to start recording, click again to stop: speech is transcribed by a local
whisper model and pasted where the cursor is. The window never takes focus, so
the text lands in the application you were typing in.
"""
import fcntl
import json
import math
import os
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import threading
import time

os.environ.setdefault("GDK_BACKEND", "x11")  # XWayland: keep-above and no-focus work in mutter

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import GLib, Gdk, Gtk  # noqa: E402

BASE = os.path.dirname(os.path.abspath(__file__))
WORKER = os.path.join(BASE, "worker.py")
VENV_PY = os.path.join(BASE, ".venv", "bin", "python")
CONFIG_PATH = os.path.expanduser("~/.config/voiceflow/config.json")

MODELS = ["large-v3-turbo", "medium", "small", "base"]
LANGUAGES = [("Auto", None), ("Русский", "ru"), ("English", "en")]
PASTE_MODES = [("Ctrl+V", ("ctrl", "v")), ("Ctrl+Shift+V", ("ctrl", "shift", "v"))]
SIZE = 64

STRINGS = {
    "en": {
        "auto": "Auto", "model": "Model", "language": "Language", "paste": "Paste", "quit": "Quit",
        "terminals": "Ctrl+Shift+V (terminals)", "loading": "Loading model {model}…",
        "ready": "{model} ({device}): click to record", "error": "Error: {text}",
        "pasted": "Pasted: {text}", "nothing": "No speech recognised", "recording": "Recording… click to stop",
        "too_short": "Too short", "busy": "Transcribing…", "worker_died": "transcription process failed",
        "no_venv": "No .venv found: run ./install.sh",
        "no_uinput": "uinput unavailable ({exc}): text will only be copied to the clipboard "
                     "(see contrib/70-voiceflow-uinput.rules)",
        "missing": "Missing required tools: {tools}",
    },
    "ru": {
        "auto": "Авто", "model": "Модель", "language": "Язык", "paste": "Вставка", "quit": "Выход",
        "terminals": "Ctrl+Shift+V (терминалы)", "loading": "Загрузка модели {model}…",
        "ready": "{model} ({device}): клик — запись", "error": "Ошибка: {text}",
        "pasted": "Вставлено: {text}", "nothing": "Речь не распознана", "recording": "Запись… клик — остановить",
        "too_short": "Слишком коротко", "busy": "Распознаю…", "worker_died": "процесс распознавания завершился с ошибкой",
        "no_venv": "Нет .venv: выполните ./install.sh",
        "no_uinput": "uinput недоступен ({exc}): текст будет только в буфере обмена "
                     "(см. contrib/70-voiceflow-uinput.rules)",
        "missing": "Не найдены нужные программы: {tools}",
    },
}
_lang = (os.environ.get("LC_ALL") or os.environ.get("LC_MESSAGES") or os.environ.get("LANG") or "en")[:2]
T = STRINGS.get(_lang, STRINGS["en"])
DEBUG = bool(os.environ.get("VOICEFLOW_DEBUG"))

DEFAULTS = {"model": "large-v3-turbo", "language": "ru", "paste": 0, "x": None, "y": None}


def load_config():
    try:
        with open(CONFIG_PATH, encoding="utf-8") as fh:
            return {**DEFAULTS, **json.load(fh)}
    except (OSError, ValueError):
        return dict(DEFAULTS)


def save_config(cfg):
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=2)


class VirtualKeyboard:
    """Virtual keyboard through /dev/uinput (the only generic way to type on Wayland)."""

    KEYS = {"ctrl": 29, "shift": 42, "v": 47}
    UI_SET_EVBIT = 0x40045564
    UI_SET_KEYBIT = 0x40045565
    UI_DEV_CREATE = 0x5501

    def __init__(self):
        self.fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
        fcntl.ioctl(self.fd, self.UI_SET_EVBIT, 1)  # EV_KEY
        fcntl.ioctl(self.fd, self.UI_SET_EVBIT, 0)  # EV_SYN
        for code in self.KEYS.values():
            fcntl.ioctl(self.fd, self.UI_SET_KEYBIT, code)
        name = b"voiceflow-keyboard".ljust(80, b"\0")
        dev = struct.pack("80sHHHHI", name, 0x03, 0x1234, 0x5678, 1, 0) + b"\0" * (4 * 64 * 4)
        os.write(self.fd, dev)
        fcntl.ioctl(self.fd, self.UI_DEV_CREATE)
        time.sleep(0.3)  # the compositor needs a moment to notice the device

    def _emit(self, etype, code, value):
        sec, usec = divmod(time.time(), 1)
        os.write(self.fd, struct.pack("llHHi", int(sec), int(usec * 1e6), etype, code, value))

    def chord(self, keys):
        codes = [self.KEYS[k] for k in keys]
        for code in codes:
            self._emit(1, code, 1)
            self._emit(0, 0, 0)
            time.sleep(0.02)
        time.sleep(0.05)
        for code in reversed(codes):
            self._emit(1, code, 0)
            self._emit(0, 0, 0)
            time.sleep(0.02)


class Worker:
    """Wrapper around worker.py (the model lives in a separate venv)."""

    def __init__(self, model, on_ready, on_result, on_error):
        self.model = model
        self.on_ready, self.on_result, self.on_error = on_ready, on_result, on_error
        self.proc = subprocess.Popen(
            [VENV_PY, WORKER, model],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in self.proc.stdout:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if msg.get("ready"):
                GLib.idle_add(self.on_ready, msg)
            elif "error" in msg:
                GLib.idle_add(self.on_error, msg["error"])
            else:
                GLib.idle_add(self.on_result, msg.get("text", ""))
        if self.proc.poll() not in (None, 0, -signal.SIGTERM):
            GLib.idle_add(self.on_error, T["worker_died"])

    def transcribe(self, path, language):
        self.proc.stdin.write(json.dumps({"audio": path, "language": language}) + "\n")
        self.proc.stdin.flush()

    def stop(self):
        try:
            self.proc.terminate()
        except OSError:
            pass


class Recorder:
    def __init__(self):
        self.proc = None
        self.path = None
        self.started = 0.0

    def start(self):
        fd, self.path = tempfile.mkstemp(prefix="voiceflow-", suffix=".wav")
        os.close(fd)
        self.proc = subprocess.Popen(
            ["pw-record", "--rate", "16000", "--channels", "1", "--format", "s16", self.path],
            stderr=subprocess.DEVNULL,
        )
        self.started = time.time()

    def stop(self):
        """Returns (path, duration)."""
        duration = time.time() - self.started
        self.proc.send_signal(signal.SIGINT)  # pw-record finalises the wav header on SIGINT
        try:
            self.proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        path, self.proc = self.path, None
        return path, duration


class Button(Gtk.Window):
    LOADING, IDLE, RECORDING, BUSY = range(4)

    def __init__(self):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.cfg = load_config()
        self.state = self.LOADING
        self.phase = 0.0
        self.recorder = Recorder()
        self.worker = None
        self.press = None
        self.dragging = False

        self.set_title("VoiceFlow")
        self.set_decorated(False)
        self.set_keep_above(True)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_accept_focus(False)
        self.set_focus_on_map(False)
        self.set_resizable(False)
        self.set_default_size(SIZE, SIZE)
        self.stick()
        visual = self.get_screen().get_rgba_visual()
        if visual:
            self.set_visual(visual)
        self.set_app_paintable(True)

        self.area = Gtk.DrawingArea()
        self.area.set_size_request(SIZE, SIZE)
        self.area.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK | Gdk.EventMask.POINTER_MOTION_MASK
        )
        self.area.connect("draw", self.on_draw)
        self.area.connect("button-press-event", self.on_press)
        self.area.connect("button-release-event", self.on_release)
        self.area.connect("motion-notify-event", self.on_motion)
        self.add(self.area)
        self.connect("destroy", self.quit)
        self.connect("configure-event", self.on_configure)

        try:
            self.keyboard = VirtualKeyboard()
        except OSError as exc:
            self.keyboard = None
            print("[voiceflow] " + T["no_uinput"].format(exc=exc), file=sys.stderr)

        self.show_all()
        if self.cfg["x"] is not None:
            self.move(self.cfg["x"], self.cfg["y"])
        self.start_worker()
        GLib.timeout_add(40, self.tick)

    # --- жизненный цикл ---------------------------------------------------
    def start_worker(self):
        if self.worker:
            self.worker.stop()
        self.set_state(self.LOADING, T["loading"].format(model=self.cfg["model"]))
        self.worker = Worker(self.cfg["model"], self.on_ready, self.on_result, self.on_error)

    def on_ready(self, msg):
        self.set_state(self.IDLE, T["ready"].format(model=msg["model"], device=msg["device"]))

    def on_error(self, text):
        print(f"[voiceflow] error: {text}", file=sys.stderr)
        self.set_state(self.IDLE, T["error"].format(text=text))
        if shutil.which("notify-send"):
            subprocess.Popen(["notify-send", "VoiceFlow", text])

    def on_result(self, text):
        self.set_state(self.IDLE, T["pasted"].format(text=text[:60]) if text else T["nothing"])
        if text:
            self.paste(text)

    def set_state(self, state, tip=None):
        self.state = state
        if tip:
            self.set_tooltip_text(tip)
        self.area.queue_draw()

    def tick(self):
        self.phase += 0.12
        if self.state in (self.RECORDING, self.BUSY, self.LOADING):
            self.area.queue_draw()
        return True

    def on_configure(self, _w, _e):
        x, y = self.get_position()
        self.cfg["x"], self.cfg["y"] = x, y
        return False

    def quit(self, *_):
        save_config(self.cfg)
        if self.worker:
            self.worker.stop()
        if self.recorder.proc:
            self.recorder.stop()
        Gtk.main_quit()

    # --- запись и вставка -------------------------------------------------
    def toggle(self):
        if self.state == self.IDLE:
            self.recorder.start()
            self.set_state(self.RECORDING, T["recording"])
        elif self.state == self.RECORDING:
            path, duration = self.recorder.stop()
            if duration < 0.4:
                os.unlink(path)
                self.set_state(self.IDLE, T["too_short"])
                return
            self.set_state(self.BUSY, T["busy"])
            self.worker.transcribe(path, self.cfg["language"])
            GLib.timeout_add_seconds(30, lambda: os.path.exists(path) and os.unlink(path) and False)

    def paste(self, text):
        old = None
        if shutil.which("wl-paste"):
            types = subprocess.run(["wl-paste", "--list-types"], capture_output=True, text=True).stdout
            if "text/plain" in types:
                old = subprocess.run(["wl-paste", "--no-newline"], capture_output=True).stdout
        subprocess.run(["wl-copy"], input=text.encode())
        time.sleep(0.15)  # give the compositor time to accept the new clipboard contents
        if DEBUG:
            print(f"[voiceflow] paste: {text!r}, keyboard={bool(self.keyboard)}", file=sys.stderr, flush=True)
        if self.keyboard:
            self.keyboard.chord(PASTE_MODES[self.cfg["paste"]][1])
        if old is not None:
            GLib.timeout_add(600, lambda: subprocess.run(["wl-copy"], input=old) and False)

    # --- мышь -------------------------------------------------------------
    def on_press(self, _w, ev):
        if ev.button == 3:
            self.show_menu(ev)
            return True
        if ev.button == 1:
            self.press = (ev.x_root, ev.y_root)
            self.dragging = False
        return True

    def on_motion(self, _w, ev):
        if self.press and not self.dragging:
            if math.hypot(ev.x_root - self.press[0], ev.y_root - self.press[1]) > 6:
                self.dragging = True
                self.begin_move_drag(1, int(ev.x_root), int(ev.y_root), ev.time)
        return True

    def on_release(self, _w, ev):
        if ev.button == 1 and self.press and not self.dragging:
            self.toggle()
        self.press = None
        return True

    def show_menu(self, ev):
        menu = Gtk.Menu()

        def radio_group(title, items, current, setter):
            head = Gtk.MenuItem(label=title)
            head.set_sensitive(False)
            menu.append(head)
            group = None
            for i, (label, value) in enumerate(items):
                item = Gtk.RadioMenuItem.new_with_label_from_widget(group, label)
                group = group or item
                item.set_active(i == current)
                item.connect("toggled", lambda it, v=value: it.get_active() and setter(v))
                menu.append(item)
            menu.append(Gtk.SeparatorMenuItem())

        radio_group(T["model"], [(m, m) for m in MODELS], MODELS.index(self.cfg["model"]), self.set_model)
        langs = [l for _, l in LANGUAGES]
        radio_group(T["language"], [(T["auto"] if l is None else n, l) for n, l in LANGUAGES], langs.index(self.cfg["language"]), self.set_language)
        paste_items = [("Ctrl+V", 0), (T["terminals"], 1)]
        radio_group(T["paste"], paste_items, self.cfg["paste"], self.set_paste)
        quit_item = Gtk.MenuItem(label=T["quit"])
        quit_item.connect("activate", self.quit)
        menu.append(quit_item)
        menu.show_all()
        menu.popup_at_pointer(ev)

    def set_model(self, model):
        if model != self.cfg["model"]:
            self.cfg["model"] = model
            save_config(self.cfg)
            self.start_worker()

    def set_language(self, lang):
        self.cfg["language"] = lang
        save_config(self.cfg)

    def set_paste(self, idx):
        self.cfg["paste"] = idx
        save_config(self.cfg)

    # --- рисование --------------------------------------------------------
    def on_draw(self, _w, cr):
        cr.set_operator(0)  # CLEAR
        cr.paint()
        cr.set_operator(2)  # OVER
        cx = cy = SIZE / 2
        r = SIZE / 2 - 4
        colors = {
            self.LOADING: (0.45, 0.47, 0.52),
            self.IDLE: (0.20, 0.45, 0.95),
            self.RECORDING: (0.92, 0.22, 0.22),
            self.BUSY: (0.95, 0.65, 0.15),
        }
        red, green, blue = colors[self.state]
        if self.state == self.RECORDING:
            pulse = (math.sin(self.phase) + 1) / 2
            cr.set_source_rgba(red, green, blue, 0.25 * pulse)
            cr.arc(cx, cy, r + 3 * pulse, 0, 2 * math.pi)
            cr.fill()
        cr.set_source_rgba(0, 0, 0, 0.35)  # shadow
        cr.arc(cx, cy + 1.5, r, 0, 2 * math.pi)
        cr.fill()
        cr.set_source_rgb(red, green, blue)
        cr.arc(cx, cy, r, 0, 2 * math.pi)
        cr.fill()

        cr.set_source_rgb(1, 1, 1)
        if self.state in (self.BUSY, self.LOADING):  # spinner
            cr.set_line_width(4)
            cr.set_line_cap(1)
            cr.arc(cx, cy, 11, self.phase, self.phase + 4.2)
            cr.stroke()
        else:  # microphone
            cr.set_line_width(3)
            cr.set_line_cap(1)
            cr.save()
            cr.translate(cx, cy - 4)
            w, h = 7, 11
            cr.move_to(-w, 0)
            cr.arc(0, 0, w, math.pi, 2 * math.pi)
            cr.line_to(w, h - w)
            cr.arc(0, h - w, w, 0, math.pi)
            cr.close_path()
            cr.fill()
            cr.restore()
            cr.arc(cx, cy - 1, 11, 0.1, math.pi - 0.1)
            cr.stroke()
            cr.move_to(cx, cy + 10)
            cr.line_to(cx, cy + 15)
            cr.stroke()
        return False


def main():
    GLib.set_prgname("voiceflow")
    GLib.set_application_name("VoiceFlow")
    missing = [t for t in ("pw-record", "wl-copy") if not shutil.which(t)]
    if missing:
        sys.exit(T["missing"].format(tools=", ".join(missing)))
    if not os.path.exists(VENV_PY):
        sys.exit(T["no_venv"])
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    Button()
    Gtk.main()


if __name__ == "__main__":
    main()
