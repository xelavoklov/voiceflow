#!/usr/bin/python3
"""VoiceFlow: плавающая кнопка диктовки для Ubuntu (GNOME/Wayland).

Клик по кнопке начинает запись, второй клик останавливает, распознаёт речь
локальной моделью whisper и вставляет текст туда, где стоит курсор.
Окно не забирает фокус, поэтому вставка попадает в нужное приложение.
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

os.environ.setdefault("GDK_BACKEND", "x11")  # XWayland: keep-above и no-focus работают в mutter

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import GLib, Gdk, Gtk  # noqa: E402

BASE = os.path.dirname(os.path.abspath(__file__))
WORKER = os.path.join(BASE, "worker.py")
VENV_PY = os.path.join(BASE, ".venv", "bin", "python")
CONFIG_PATH = os.path.expanduser("~/.config/voiceflow/config.json")

MODELS = ["large-v3-turbo", "medium", "small", "base"]
LANGUAGES = [("Авто", None), ("Русский", "ru"), ("English", "en")]
PASTE_MODES = [("Ctrl+V", ("ctrl", "v")), ("Ctrl+Shift+V (терминалы)", ("ctrl", "shift", "v"))]
SIZE = 64

DEFAULTS = {"model": "large-v3-turbo", "language": None, "paste": 0, "x": None, "y": None}


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
    """Виртуальная клавиатура через /dev/uinput (на Wayland другого способа нет)."""

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
        time.sleep(0.3)  # композитор должен успеть увидеть устройство

    def _emit(self, etype, code, value):
        sec, usec = divmod(time.time(), 1)
        os.write(self.fd, struct.pack("llHHi", int(sec), int(usec * 1e6), etype, code, value))

    def chord(self, keys):
        codes = [self.KEYS[k] for k in keys]
        for code in codes:
            self._emit(1, code, 1)
        self._emit(0, 0, 0)
        time.sleep(0.03)
        for code in reversed(codes):
            self._emit(1, code, 0)
        self._emit(0, 0, 0)


class Worker:
    """Обёртка над процессом worker.py (модель живёт в отдельном venv)."""

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
            GLib.idle_add(self.on_error, "процесс распознавания завершился с ошибкой")

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
        """Возвращает (путь, длительность)."""
        duration = time.time() - self.started
        self.proc.send_signal(signal.SIGINT)  # pw-record дописывает wav-заголовок по SIGINT
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
            print(f"[voiceflow] uinput недоступен ({exc}): текст будет только в буфере обмена", file=sys.stderr)

        self.show_all()
        if self.cfg["x"] is not None:
            self.move(self.cfg["x"], self.cfg["y"])
        self.start_worker()
        GLib.timeout_add(40, self.tick)

    # --- жизненный цикл ---------------------------------------------------
    def start_worker(self):
        if self.worker:
            self.worker.stop()
        self.set_state(self.LOADING, f"Загрузка модели {self.cfg['model']}…")
        self.worker = Worker(self.cfg["model"], self.on_ready, self.on_result, self.on_error)

    def on_ready(self, msg):
        self.set_state(self.IDLE, f"{msg['model']} ({msg['device']}): клик — запись")

    def on_error(self, text):
        print(f"[voiceflow] ошибка: {text}", file=sys.stderr)
        self.set_state(self.IDLE, f"Ошибка: {text}")
        if shutil.which("notify-send"):
            subprocess.Popen(["notify-send", "VoiceFlow", text])

    def on_result(self, text):
        self.set_state(self.IDLE, f"Вставлено: {text[:60]}" if text else "Речь не распознана")
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
            self.set_state(self.RECORDING, "Запись… клик — остановить")
        elif self.state == self.RECORDING:
            path, duration = self.recorder.stop()
            if duration < 0.4:
                os.unlink(path)
                self.set_state(self.IDLE, "Слишком коротко")
                return
            self.set_state(self.BUSY, "Распознаю…")
            self.worker.transcribe(path, self.cfg["language"])
            GLib.timeout_add_seconds(30, lambda: os.path.exists(path) and os.unlink(path) and False)

    def paste(self, text):
        old = None
        if shutil.which("wl-paste"):
            types = subprocess.run(["wl-paste", "--list-types"], capture_output=True, text=True).stdout
            if "text/plain" in types:
                old = subprocess.run(["wl-paste", "--no-newline"], capture_output=True).stdout
        subprocess.run(["wl-copy"], input=text.encode())
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

        radio_group("Модель", [(m, m) for m in MODELS], MODELS.index(self.cfg["model"]), self.set_model)
        langs = [l for _, l in LANGUAGES]
        radio_group("Язык", LANGUAGES, langs.index(self.cfg["language"]), self.set_language)
        radio_group(
            "Вставка", [(n, i) for i, (n, _) in enumerate(PASTE_MODES)], self.cfg["paste"], self.set_paste
        )
        quit_item = Gtk.MenuItem(label="Выход")
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
        cr.set_source_rgba(0, 0, 0, 0.35)  # тень
        cr.arc(cx, cy + 1.5, r, 0, 2 * math.pi)
        cr.fill()
        cr.set_source_rgb(red, green, blue)
        cr.arc(cx, cy, r, 0, 2 * math.pi)
        cr.fill()

        cr.set_source_rgb(1, 1, 1)
        if self.state in (self.BUSY, self.LOADING):  # крутилка
            cr.set_line_width(4)
            cr.set_line_cap(1)
            cr.arc(cx, cy, 11, self.phase, self.phase + 4.2)
            cr.stroke()
        else:  # микрофон
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
    if not os.path.exists(VENV_PY):
        sys.exit("Нет .venv: выполните ./install.sh")
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    Button()
    Gtk.main()


if __name__ == "__main__":
    main()
