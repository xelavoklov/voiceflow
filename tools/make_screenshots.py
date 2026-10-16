#!/usr/bin/python3
"""Renders README images with the app's own drawing code (no real desktop content involved)."""
import math
import os
import random
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("GDK_BACKEND", "x11")
import cairo  # noqa: E402

import app  # noqa: E402

OUT = os.path.join(os.path.dirname(__file__), "..", "assets")
SCALE = 2
FONT = "DejaVu Sans"


def fake_button(state, levels=None, phase=0.0):
    b = SimpleNamespace(state=state, phase=phase, levels=levels or [0.0] * app.BARS)
    for name in ("LOADING", "IDLE", "RECORDING", "BUSY"):
        setattr(b, name, getattr(app.Button, name))
    b.draw_pill = lambda cr: app._draw_pill(b, cr)
    return b


def draw_widget(cr, x, y, state, **kw):
    """Draw the real widget with its top-left corner at (x, y)."""
    b = fake_button(state, **kw)
    cr.save()
    cr.translate(x, y)
    cr.push_group()  # the widget clears its whole surface first, so draw it in isolation
    app.Button.on_draw(b, None, cr)
    cr.pop_group_to_source()
    cr.paint()
    cr.restore()


def text(cr, x, y, s, size=14, color=(1, 1, 1), bold=False, align="left"):
    cr.select_font_face(FONT, 0, 1 if bold else 0)
    cr.set_font_size(size)
    cr.set_source_rgb(*color)
    w = cr.text_extents(s).x_advance
    cr.move_to(x - (w / 2 if align == "center" else 0), y)
    cr.show_text(s)


def rounded(cr, x, y, w, h, r):
    app._rounded_rect(cr, x, y, w, h, r)


def speech_levels(seed=4):
    random.seed(seed)
    return [min(1, 0.25 + abs(math.sin(i / 2.1)) * random.uniform(0.35, 0.95)) for i in range(app.BARS)]


def canvas(w, h):
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, w * SCALE, h * SCALE)
    cr = cairo.Context(surf)
    cr.scale(SCALE, SCALE)
    return surf, cr


def background(cr, w, h):
    grad = cairo.LinearGradient(0, 0, w, h)
    grad.add_color_stop_rgb(0, 0.10, 0.11, 0.16)
    grad.add_color_stop_rgb(1, 0.17, 0.12, 0.27)
    cr.set_source(grad)
    cr.paint()


def states():
    w, h = 820, 190
    surf, cr = canvas(w, h)
    background(cr, w, h)
    items = [
        ("Loading model", app.Button.LOADING, {"phase": 1.2}, 64),
        ("Ready — click to dictate", app.Button.IDLE, {}, 64),
        ("Recording — ✓ pastes, ✕ cancels", app.Button.RECORDING, {"levels": speech_levels()}, app.PILL_W),
        ("Transcribing", app.Button.BUSY, {"phase": 3.0}, 64),
    ]
    slots = [105, 285, 540, 745]
    for (label, state, kw, width), cx in zip(items, slots):
        height = app.PILL_H if state == app.Button.RECORDING else 64
        draw_widget(cr, cx - width / 2, 62 + (64 - height) / 2, state, **kw)
        text(cr, cx, 160, label, 13, (0.82, 0.84, 0.92), align="center")
    surf.write_to_png(os.path.join(OUT, "states.png"))


def hero():
    w, h = 760, 400
    surf, cr = canvas(w, h)
    background(cr, w, h)
    # fake text editor window
    cr.set_source_rgba(0, 0, 0, 0.4)
    rounded(cr, 62, 46, 640, 310, 14)
    cr.fill()
    cr.set_source_rgb(0.15, 0.16, 0.19)
    rounded(cr, 60, 40, 640, 310, 14)
    cr.fill()
    cr.set_source_rgb(0.20, 0.21, 0.25)
    rounded(cr, 60, 40, 640, 38, 14)
    cr.fill()
    cr.rectangle(60, 64, 640, 14)
    cr.fill()
    for i, c in enumerate(((0.93, 0.36, 0.34), (0.96, 0.75, 0.25), (0.35, 0.78, 0.4))):
        cr.set_source_rgb(*c)
        cr.arc(84 + i * 22, 59, 6, 0, 2 * math.pi)
        cr.fill()
    text(cr, 380, 64, "Notes", 13, (0.7, 0.72, 0.8), align="center")
    lines = [
        "Meeting notes",
        "",
        "• Ship the new dictation button this week.",
        "• Ask Sam about the Flatpak build.",
        "• Remember to record a short demo video",
    ]
    for i, line in enumerate(lines):
        text(cr, 90, 112 + i * 28, line, 16 if i else 20, (0.9, 0.91, 0.95), bold=(i == 0))
    # caret right after the last (dictated) line
    cr.set_source_rgb(0.55, 0.45, 1)
    cr.rectangle(436, 218, 2, 20)
    cr.fill()
    # recording pill floating over the window
    draw_widget(cr, 380 - app.PILL_W / 2, 270, app.Button.RECORDING, levels=speech_levels(9))
    text(cr, 380, 346 + 30, "Click, speak, click ✓ — the text appears at the cursor", 14, (0.82, 0.84, 0.92), align="center")
    surf.write_to_png(os.path.join(OUT, "hero.png"))


if __name__ == "__main__":
    states()
    hero()
    print("written to", os.path.abspath(OUT))
