"""The heart of Vocalm: the bar on your screen, the breathe card, and the loop that ties the microphone, the tracker
and the history together.

Everything Tk lives on the main thread. The dashboard (server.py), the tray icon and the hotkeys run on their own
threads and never touch Tk: they put commands on `Core.commands`, and read `Core.live`, a snapshot dict that is
replaced (never edited) every frame.
"""
import queue
import random
import statistics
import time
import tkinter as tk
from dataclasses import asdict

from . import calibrate, settings, winapi
from .audio import LevelMeter
from .tracker import Tracker

TICK_MS = 33
MAX_STEP_S = 0.25            # a frame longer than this (the PC slept) isn't counted as talking time
SAVE_MINUTES_EVERY = 10.0
SAVE_SESSION_EVERY = 30.0
TOPMOST_EVERY = 2.0
CHECK_APP_EVERY = 1.0
MIC_RETRY_EVERY = 5.0

TOPMOST_ON_TASKBAR_EVERY = 0.5  # the taskbar comes to the front whenever it's clicked; win it back quickly

TRANSPARENT = "#010203"      # keyed out by Windows; never used in the drawing itself
COLORS = {
    "track": "#1a2120", "edge": "#2c3836", "tick": "#8a9b97",
    "calm": "#3ddc84", "close": "#ffb020", "loud": "#ff4d4d",
    "paused": "#8a9b97", "move": "#3fbfad", "nomic": "#ff4d4d", "peak": "#eef5f3",
    "card": "#101615", "card_text": "#eef5f3", "card_sub": "#9fb2ad", "quiet": "#5d6e6a",
}
FONT = "Bahnschrift"         # ships with Windows 10 and 11; Tk falls back to the default font if it's missing
LABEL_GAP = 6                # between the bar and its readout
PILL_PAD_L, PILL_DOT, PILL_GAP, PILL_PAD_R = 9, 6, 6, 10
BAR_HEADROOM_DB = 15.0       # how far past your line the bar's scale reaches
SWITCH_SECONDS = 45.0       # talking needed to work out how far to move an older line to perceived loudness
NUDGES = ["Take a breath.", "Ease off a little.", "Drop it a notch.", "Breathe, lower the volume.", "Soften up.",
          "You're spiking. Settle."]
CALIBRATION_SECONDS = {"normal": 10.0, "loud": 5.0}


def rounded_rect(canvas, x1, y1, x2, y2, r, **kw):
    """Tk has no rounded rectangle; a smoothed polygon is the usual stand-in."""
    r = max(0.0, min(r, (x2 - x1) / 2.0, (y2 - y1) / 2.0))
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2, x1, y2,
           x1, y2 - r, x1, y1 + r, x1, y1]
    return canvas.create_polygon(pts, smooth=True, **kw)


class Core:
    def __init__(self, cfg, store, port):
        self.cfg = cfg
        self.store = store
        self.port = port
        self.commands = queue.Queue()
        self.listeners = []           # f(state) for calm/loud/paused changes (the tray icon)
        self.quit_hooks = []          # run on the main thread while shutting down
        self.hotkey_failures = []

        now = time.time()
        self.meter = LevelMeter(cfg)
        self.watcher = winapi.ForegroundWatcher()
        self.app_name = ""
        self.tracker = Tracker(cfg, now)
        self.switch_result = None     # when an older line was moved over to perceived loudness, by how much
        self._arm_switch()
        self.session_id = store.start_session(self.tracker.session_record(now))
        self.paused = False
        self.move_mode = False
        self.quitting = False
        self.event_seq = 0
        self.calib = {"phase": None, "until": 0.0, "seconds": 0.0, "samples": {}, "result": None, "error": None}

        self._last = {"tick": now, "minutes": now, "session": now, "topmost": 0.0, "app": 0.0, "mic": 0.0,
                      "nudge": 0.0}
        self._drawn = None
        self._move_pos = None
        self._on_taskbar = False
        self._last_cursor = None
        self._state = None
        self.live = {}

        winapi.set_dpi_awareness()
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title("Vocalm")
        self._build_windows()
        if cfg.hotkeys_enabled:
            winapi.start_hotkeys(lambda name: self.commands.put(("hotkey", name)),
                                 lambda failed: self.commands.put(("hotkeys_failed", failed)))

    # ---------- windows ----------

    def _overlay_window(self, width, height, alpha):
        win = tk.Toplevel(self.root)
        win.overrideredirect(True)
        win.configure(bg=TRANSPARENT)
        win.geometry(f"{width}x{height}+0+0")
        try:
            win.attributes("-alpha", alpha)
            win.wm_attributes("-transparentcolor", TRANSPARENT)
        except tk.TclError:
            pass
        canvas = tk.Canvas(win, width=width, height=height, bg=TRANSPARENT, highlightthickness=0, bd=0)
        canvas.pack()
        win.update_idletasks()
        winapi.make_overlay(win)
        winapi.raise_topmost(win)
        return win, canvas

    def _build_windows(self):
        w, h = self._bar_size()
        self.bar, self.canvas = self._overlay_window(w, h, float(self.cfg.opacity))
        self._place_bar(*self._home_position(), save=False)
        self.card_w, self.card_h = 268, 92
        self.nudge, self.nudge_canvas = self._overlay_window(self.card_w, self.card_h, 0.95)
        self.nudge.withdraw()
        self.hint_w, self.hint_h = 330, 74
        self.hint, self.hint_canvas = self._overlay_window(self.hint_w, self.hint_h, 0.96)
        self.hint.withdraw()
        if not self.cfg.overlay_visible:
            self.bar.withdraw()

    def _fonts(self):
        if not hasattr(self, "_label_fonts"):
            from tkinter import font as tkfont
            self._label_fonts = (tkfont.Font(family=FONT, size=10, weight="bold"), tkfont.Font(family=FONT, size=8))
        return self._label_fonts

    def _pill_width(self, text, small):
        """The readout's pill hugs its text: padding, dot, gap, text, padding."""
        bold, light = self._fonts()
        return PILL_PAD_L + PILL_DOT + PILL_GAP + bold.measure(text) + light.measure(small) + PILL_PAD_R

    def _label_width(self):
        """Room kept beside the bar for the widest readout, so the window never has to resize as it changes."""
        if self.cfg.bar_label == "off":
            return 0
        if self.cfg.bar_label == "peak":
            return self._pill_width("-88.8", " dB")
        return max(self._pill_width(t, "calm ") for t in ("23h 59m", "99d 23h", "<1m"))

    def _bar_size(self):
        g = self.cfg.bar_grow_px
        label = self._label_width()
        return self.cfg.bar_width + g * 2 + (LABEL_GAP + label if label else 0), self.cfg.bar_height + g * 2

    def _clamp(self, x, y):
        """Anywhere on a monitor, the taskbar included; just never off the edge of the screen."""
        w, h = self._bar_size()
        left, top, right, bottom = winapi.monitor_near(x, y, w, h)
        return max(left, min(int(x), right - w)), max(top, min(int(y), bottom - h))

    def _over_taskbar(self):
        if not self._move_pos:
            return False
        w, h = self._bar_size()
        left, top, right, bottom = winapi.work_area_near(*self._move_pos, w, h)
        x, y = self._move_pos
        return x < left or y < top or x + w > right or y + h > bottom

    def _home_position(self):
        """Where the bar belongs: where you dragged it, or the chosen corner of the main monitor."""
        if self.cfg.pos_x is not None and self.cfg.pos_y is not None:
            return self._clamp(self.cfg.pos_x, self.cfg.pos_y)
        w, h = self._bar_size()
        left, top, right, bottom = winapi.primary_work_area()
        m, corner = self.cfg.margin_px, self.cfg.corner
        x = {"top-left": left + m, "bottom-left": left + m, "top-center": (left + right - w) // 2}.get(
            corner, right - w - m)
        y = bottom - h - m if corner.startswith("bottom") else top + m
        return x, y

    def _place_bar(self, x, y, save=False):
        x, y = self._clamp(x, y)
        self._move_pos = (x, y)  # winfo_x() lags until Tk catches up; this doesn't
        self._on_taskbar = self._over_taskbar()
        self.bar.geometry(f"+{x}+{y}")
        if self.move_mode:
            self._place_hint()
        if save:
            self.cfg.pos_x, self.cfg.pos_y = x, y
            settings.save(self.cfg)

    def _resize_bar(self):
        w, h = self._bar_size()
        self.bar.geometry(f"{w}x{h}")
        self.canvas.configure(width=w, height=h)
        try:
            self.bar.attributes("-alpha", float(self.cfg.opacity))
        except tk.TclError:
            pass
        self._place_bar(*self._home_position())
        self._drawn = None

    # ---------- drawing ----------

    def _fraction(self, level):
        """Where a level sits on the bar: from where talking starts to 15 dB past your line. The room past the line
        is what lets the red-peak needle show how far over you went, instead of every peak piling up at the end."""
        lo = self.tracker.floor()
        hi = self.cfg.threshold_dbfs + BAR_HEADROOM_DB
        return 0.0 if hi <= lo else max(0.0, min(1.0, (level - lo) / (hi - lo)))

    def _peak_showing(self, now):
        return self.tracker.red_peak_showing(now, self.cfg.bar_info_timeout_s)

    def _label(self, now):
        """(text, small text, dot colour) for the readout beside the bar, or None for no readout."""
        if self.cfg.bar_label == "streak":
            # Whole minutes only: seconds ticking next to your game would pull your eye every second
            return compact_duration(self.tracker.calm_streak(now)), "calm ", COLORS["calm"]
        if not self._peak_showing(now):
            return None  # nothing went red lately: nothing to say, so nothing takes up room
        # A plain hyphen: Bahnschrift's minus sign is as wide as a dash and reads as one at this size
        return f"{self.tracker.red_peak:.1f}", " dB", COLORS["loud"]

    def _draw_bar(self, level, zone, now):
        mode = ("move" if self.move_mode else "nomic" if not self.meter.running else
                "paused" if self.paused or self.calib["phase"] else zone)
        live = mode in ("quiet", "calm", "close", "loud")
        fraction = round(self._fraction(level), 3) if live else 0
        peak = self.tracker.red_peak if live and self.cfg.bar_peak_marker and self._peak_showing(now) else None
        peak_at = round(self._fraction(peak), 3) if peak is not None else None
        label = self._label(now) if self.cfg.bar_label != "off" else None
        key = (fraction, mode, peak_at, label)
        if key == self._drawn:
            return
        self._drawn = key

        g = self.cfg.bar_grow_px
        area_w = self.cfg.bar_width + g * 2           # the bar's part of the window; the readout sits to its right
        win_h = self.cfg.bar_height + g * 2
        grow = g if mode == "loud" else 0
        w, h = self.cfg.bar_width + grow * 2, self.cfg.bar_height + grow
        x1, y1 = (area_w - w) / 2.0, (win_h - h) / 2.0
        x2, y2 = x1 + w, y1 + h
        c = self.canvas
        c.delete("all")
        edge = COLORS["move"] if mode == "move" else COLORS["nomic"] if mode == "nomic" else COLORS["edge"]
        rounded_rect(c, x1, y1, x2, y2, h / 2.0, fill=COLORS["track"], outline=edge)
        cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
        if mode == "move":  # a grip, so it's obvious the bar is being moved
            for dx in (-10, -4, 2, 8):
                c.create_rectangle(cx + dx, cy - 4, cx + dx + 2, cy + 4, fill=COLORS["move"], outline=COLORS["move"])
        elif mode == "paused":  # an empty bar would look like silence, so paused gets a pause sign
            for dx in (-4, 2):
                c.create_rectangle(cx + dx, cy - h * .42, cx + dx + 2, cy + h * .42, fill=COLORS["paused"],
                                   outline=COLORS["paused"])
        elif live:
            if fraction > 0.004:
                fill = COLORS["calm" if mode == "quiet" else mode]
                rounded_rect(c, x1, y1, x1 + max(h, w * fraction), y2, h / 2.0, fill=fill, outline=fill)
            tick = x1 + w * self._fraction(self.cfg.threshold_dbfs)  # where "too loud" starts
            c.create_line(tick, y1 + 2, tick, y2 - 2, fill=COLORS["tick"], width=1)
            if peak_at is not None and peak_at > 0.004:
                # The needle: how far past the line you went last time. Pokes out above and below the bar so it
                # reads differently from the line's tick.
                px = min(x2 - 2, max(x1 + 2, x1 + w * peak_at))
                c.create_rectangle(px - 1, max(0, y1 - 3), px + 1, min(win_h, y2 + 3), fill=COLORS["peak"],
                                   outline=COLORS["peak"])
        if label:
            self._draw_label(c, area_w + LABEL_GAP, win_h, label, mode)

    def _draw_label(self, c, x, win_h, label, mode):
        text, small, dot = label
        if mode in ("move", "paused", "nomic"):
            dot = COLORS["quiet"]
        bold, light = self._fonts()
        pill_h = min(win_h, max(18, self.cfg.bar_height + 6))
        top = (win_h - pill_h) / 2.0
        cy = win_h / 2.0
        rounded_rect(c, x, top, x + self._pill_width(text, small), top + pill_h, pill_h / 2.0, fill=COLORS["card"],
                     outline=COLORS["edge"])
        c.create_oval(x + PILL_PAD_L, cy - PILL_DOT / 2, x + PILL_PAD_L + PILL_DOT, cy + PILL_DOT / 2, fill=dot,
                      outline=dot)
        # Both sizes sit on one baseline (centring each on its own would leave the small text floating), with the
        # big text's line box centred in the pill
        baseline = cy - (bold.metrics("ascent") + bold.metrics("descent")) / 2.0 + bold.metrics("ascent")
        parts = [(small, light, COLORS["card_sub"]), (text, bold, COLORS["card_text"])]
        if self.cfg.bar_label != "streak":
            parts.reverse()  # "-12.4 dB": number first; "calm 12m": word first
        tx = x + PILL_PAD_L + PILL_DOT + PILL_GAP
        for content, font, colour in parts:
            if content:
                c.create_text(tx, baseline - font.metrics("ascent"), anchor="nw", text=content, fill=colour, font=font)
                tx += font.measure(content)

    # ---------- the breathe card ----------

    def show_nudge(self, streak, force=False):
        now = time.time()
        if not force and (not self.cfg.nudge_enabled or not self.cfg.overlay_visible
                          or now - self._last["nudge"] < self.cfg.nudge_cooldown_s):
            return
        self._last["nudge"] = now
        c = self.nudge_canvas
        c.delete("all")
        rounded_rect(c, 1, 1, self.card_w - 1, self.card_h - 1, 14, fill=COLORS["card"], outline=COLORS["loud"])
        c.create_rectangle(1, 1, 5, self.card_h - 1, fill=COLORS["loud"], outline=COLORS["loud"])
        c.create_text(20, 34, anchor="w", text=random.choice(NUDGES), fill=COLORS["card_text"],
                      font=(FONT, 14, "bold"))
        sub = ("Preview: you'll see this after a loud moment" if force
               else f"calm streak ended at {format_duration(streak)}")
        c.create_text(20, 60, anchor="w", text=sub, fill=COLORS["card_sub"], font=(FONT, 9))

        bx, by = self._move_pos or (self.bar.winfo_x(), self.bar.winfo_y())
        bw, bh = self._bar_size()
        left, top, right, bottom = winapi.work_area_near(bx, by, bw, bh)
        y = by + bh + 10 if by + bh + 10 + self.card_h <= bottom else by - self.card_h - 10
        left_side = bx + bw / 2 < (left + right) / 2
        x = bx if left_side else bx + bw - self.card_w
        x = max(left, min(x, right - self.card_w))
        start = x - 40 if left_side else x + 40
        self.nudge.geometry(f"+{start}+{y}")
        self.nudge.deiconify()
        winapi.make_overlay(self.nudge)
        winapi.raise_topmost(self.nudge)
        self._slide(start, x, y, 0)
        if getattr(self, "_nudge_job", None):
            self.root.after_cancel(self._nudge_job)
        self._nudge_job = self.root.after(int(self.cfg.nudge_duration_s * 1000), self.nudge.withdraw)

    def _slide(self, start, end, y, step):
        if step > 8 or self.quitting:
            return
        eased = 1 - (1 - step / 8) ** 3
        self.nudge.geometry(f"+{int(start + (end - start) * eased)}+{y}")
        self.root.after(16, self._slide, start, end, y, step + 1)

    # ---------- moving the bar ----------

    def toggle_move(self):
        if not self.cfg.overlay_visible:
            self.set_overlay(True)
        self.move_mode = not self.move_mode
        self._drawn = None
        if self.move_mode:
            self._last_cursor = winapi.cursor_position()
            c = self.hint_canvas
            c.delete("all")
            rounded_rect(c, 1, 1, self.hint_w - 1, self.hint_h - 1, 12, fill=COLORS["card"], outline=COLORS["move"])
            c.create_text(16, 24, anchor="w", text="The bar is following your mouse", fill=COLORS["card_text"],
                          font=(FONT, 11, "bold"))
            c.create_text(16, 48, anchor="w", text="Ctrl+Alt+arrows to fine-tune  ·  Ctrl+Alt+B to drop it",
                          fill=COLORS["card_sub"], font=(FONT, 9))
            self._place_hint()
            self.hint.deiconify()
            winapi.make_overlay(self.hint)
            winapi.raise_topmost(self.hint)
            winapi.raise_topmost(self.bar)
        else:
            self.hint.withdraw()
            self._place_bar(*self._move_pos, save=True)

    def _place_hint(self):
        bx, by = self._move_pos
        bw, bh = self._bar_size()
        left, top, right, bottom = winapi.work_area_near(bx, by, bw, bh)
        y = by + bh + 10 if by + bh + 10 + self.hint_h <= bottom else by - self.hint_h - 10
        x = max(left, min(bx, right - self.hint_w))
        self.hint.geometry(f"+{x}+{y}")

    def _follow_cursor(self):
        """The bar follows the pointer: a click-through window always loses the hit-test to whatever is under it,
        so it could never be dragged. Only moves when the pointer does, so arrow nudges stick."""
        pos = winapi.cursor_position()
        if pos is None or pos == self._last_cursor:
            return
        self._last_cursor = pos
        w, h = self._bar_size()
        self._place_bar(pos[0] - w // 2, pos[1] - h // 2)

    def set_overlay(self, visible):
        self.cfg.overlay_visible = bool(visible)
        settings.save(self.cfg)
        if visible:
            self.bar.deiconify()
            winapi.make_overlay(self.bar)
            winapi.raise_topmost(self.bar)
        else:
            if self.move_mode:
                self.toggle_move()
            self.bar.withdraw()
            self.nudge.withdraw()
        self._drawn = None

    # ---------- commands from the dashboard, tray and hotkeys ----------

    def _handle(self, command):
        kind, *args = command
        if kind == "hotkey":
            name = args[0]
            if name == "quit":
                self.quit()
            elif name == "pause":
                self.set_paused(not self.paused)
            elif name == "move":
                self.toggle_move()
            elif self.move_mode:
                dx, dy = {"left": (-1, 0), "right": (1, 0), "up": (0, -1), "down": (0, 1)}[name]
                self._place_bar(self._move_pos[0] + dx, self._move_pos[1] + dy)
        elif kind == "pause":
            self.set_paused(args[0])
        elif kind == "move":
            self.toggle_move()
        elif kind == "overlay":
            self.set_overlay(args[0])
        elif kind == "test_nudge":
            self.show_nudge(self.tracker.calm_streak(time.time()), force=True)
        elif kind == "reset_position":
            self.cfg.pos_x = self.cfg.pos_y = None
            settings.save(self.cfg)
            self._place_bar(*self._home_position())
        elif kind == "settings":
            self.apply_settings(*args)
        elif kind == "calibrate":
            self.calibration(args[0])
        elif kind == "hotkeys_failed":
            self.hotkey_failures = list(args[0])
            print("Hotkeys taken by another program:", ", ".join(self.hotkey_failures))
        elif kind == "quit":
            self.quit()

    def set_paused(self, paused):
        if paused and not self.paused:
            self._record(self.tracker.interrupt(time.time()))
        self.paused = bool(paused)
        self._drawn = None

    def apply_settings(self, patch, reply=None):
        """Merge a partial update from the dashboard. Done here on the main thread, against the live settings, so
        it can't undo something that changed a moment earlier (like the bar you just moved)."""
        old = self.cfg
        new = settings.normalize(patch, base=old)
        restart = any(getattr(old, k) != getattr(new, k) for k in
                      ("device_query", "samplerate", "blocksize", "attack_ms", "release_ms"))
        self.cfg = self.tracker.cfg = self.meter.cfg = new
        settings.save(new)
        if any(getattr(old, k) != getattr(new, k) for k in
               ("bar_width", "bar_height", "bar_grow_px", "opacity", "corner", "margin_px", "pos_x", "pos_y",
                "bar_label")):
            self._resize_bar()
        if old.overlay_visible != new.overlay_visible:
            self.set_overlay(new.overlay_visible)
        if restart:
            self.open_mic()
        self._drawn = None
        self._arm_switch()
        if reply is not None:
            reply.put(asdict(new))

    # ---------- switching an older line over to perceived loudness ----------

    def _arm_switch(self):
        """While measuring flat, have the meter collect how perceived loudness differs during your talking."""
        self.meter.switch_floor = self.tracker.floor() if self.cfg.loudness == "flat" else None

    def _switch_needed(self):
        return SWITCH_SECONDS * self.meter.blocks_per_second()

    def _check_switch(self, now):
        """Once there's enough talking to go on, move the line (and normal voice) by the typical difference between
        the two measures. Your normal voice then sits exactly as far under the line as before; what changes is that
        sharp, high-pitched sounds now count as loud as they sound."""
        if self.cfg.loudness != "flat" or len(self.meter.switch_diffs) < self._switch_needed():
            return
        offset = round(statistics.median(self.meter.switch_diffs), 1)
        self.meter.switch_diffs = []
        before = self.cfg.threshold_dbfs
        self._record(self.tracker.interrupt(now))  # a loud moment mustn't straddle two ways of measuring
        self.apply_settings({"threshold_dbfs": before + offset, "normal_dbfs": self.cfg.normal_dbfs + offset,
                             "loudness": "perceived"})
        self.switch_result = {"from": before, "to": self.cfg.threshold_dbfs, "offset": offset, "at": now}
        print(f"Switched to perceived loudness: your line moved {before:+.1f} -> {self.cfg.threshold_dbfs:+.1f} dB "
              f"({offset:+.1f} dB, the typical difference while you talked)")

    def calibration(self, action):
        c = self.calib
        if action in ("normal", "loud") and not c["phase"]:
            self._record(self.tracker.interrupt(time.time()))  # the loud pass mustn't count as loud moments
            c.update(phase=action, seconds=CALIBRATION_SECONDS[action],
                     until=time.time() + CALIBRATION_SECONDS[action], error=None)
            if action == "normal":
                c.update(samples={}, result=None)
            self.meter.begin_capture()
        elif action == "cancel":
            self.meter.end_capture()
            c.update(phase=None, samples={}, result=None, error=None)
        elif action == "reset":
            c.update(phase=None, samples={}, result=None, error=None)

    def _finish_calibration_phase(self):
        c = self.calib
        c["samples"][c["phase"]] = self.meter.end_capture()
        c["phase"] = None
        if "normal" in c["samples"] and "loud" in c["samples"]:
            try:
                c["result"] = calibrate.compute(c["samples"]["normal"], c["samples"]["loud"], self.cfg.gate_dbfs)
            except ValueError as e:
                c.update(error=str(e), samples={})
        elif "normal" in c["samples"]:
            level, count = calibrate.level_of(c["samples"]["normal"], self.cfg.gate_dbfs)
            if count < calibrate.MIN_SAMPLES["normal"]:
                c.update(error="Vocalm barely heard you. Check your mic is unmuted, then try again.", samples={})

    # ---------- the loop ----------

    def open_mic(self):
        previous = self.meter.error
        try:
            self.meter.start()
            print("Listening to", self.meter.label)
        except RuntimeError as e:
            self.meter.error = str(e)
            if self.meter.error != previous:  # it retries every few seconds; say it once
                print(e)
        self._drawn = None

    def _record(self, signals):
        for kind, payload in signals:
            if kind == "loud":
                self.show_nudge(payload)
            elif kind == "event":
                self.store.add_event(self.session_id, payload)
                self.event_seq += 1

    def tick(self):
        if self.quitting:
            return
        try:
            self._tick()
        except Exception as e:  # never let one bad frame stop the loop
            print("Tick failed:", repr(e))
        self.root.after(TICK_MS, self.tick)

    def _tick(self):
        now = time.time()
        step = min(max(now - self._last["tick"], 0.0), MAX_STEP_S)
        self._last["tick"] = now

        while True:
            try:
                self._handle(self.commands.get_nowait())
            except queue.Empty:
                break
            if self.quitting:
                return

        if self.move_mode:
            self._follow_cursor()
        if self.cfg.track_apps and now - self._last["app"] >= CHECK_APP_EVERY:
            self._last["app"] = now
            self.app_name = self.watcher.current() or self.app_name

        level = self.meter.level if self.meter.running else -120.0
        if self.calib["phase"]:
            if now >= self.calib["until"]:
                self._finish_calibration_phase()
        elif not self.paused and self.meter.running:
            self._record(self.tracker.update(level, step, now, self.app_name if self.cfg.track_apps else ""))
        zone = self.tracker.zone(level)

        if self.cfg.overlay_visible:
            self._draw_bar(level, zone, now)
            every = TOPMOST_ON_TASKBAR_EVERY if self._on_taskbar else TOPMOST_EVERY
            if now - self._last["topmost"] >= every:
                self._last["topmost"] = now
                winapi.raise_topmost(self.bar)

        if now - self._last["minutes"] >= SAVE_MINUTES_EVERY:
            self._last["minutes"] = now
            self.store.add_minutes(self.tracker.take_buckets(now), self.cfg.threshold_dbfs)
        if now - self._last["session"] >= SAVE_SESSION_EVERY:
            self._last["session"] = now
            self.store.save_session(self.session_id, self.tracker.session_record(now))
        self._check_switch(now)
        if now - self._last["mic"] >= MIC_RETRY_EVERY:
            self._last["mic"] = now
            if self.meter.stalled() and not self.calib["phase"]:
                print("The microphone stopped; reopening it")
                self.open_mic()

        state = "paused" if self.paused else ("loud" if zone == "loud" else "calm")
        if state != self._state:
            self._state = state
            for listener in self.listeners:
                try:
                    listener(state)
                except Exception as e:
                    print("Listener failed:", repr(e))
        self._publish(now, level, zone)

    def _publish(self, now, level, zone):
        c = self.calib
        t = self.tracker
        self.live = {
            "t": now, "db": round(level, 1), "zone": zone, "paused": self.paused,
            "redPeak": round(t.red_peak, 1) if t.red_peak is not None else None,
            "redPeakAgo": round(now - t.red_peak_at) if t.red_peak_at is not None else None,
            "redPeakShowing": self._peak_showing(now), "infoTimeout": self.cfg.bar_info_timeout_s,
            "onTaskbar": self._on_taskbar,
            "loudness": {"mode": self.cfg.loudness, "switched": self.switch_result,
                         "progress": round(min(1.0, len(self.meter.switch_diffs) / self._switch_needed()), 3)
                         if self.cfg.loudness == "flat" else None},
            "streak": round(t.calm_streak(now)), "app": self.app_name if self.cfg.track_apps else "",
            "eventSeq": self.event_seq, "moveMode": self.move_mode, "overlay": self.cfg.overlay_visible,
            "threshold": self.cfg.threshold_dbfs, "normal": self.cfg.normal_dbfs, "gate": self.cfg.gate_dbfs,
            "floor": round(t.floor(), 1),
            "mic": {"ok": self.meter.running, "label": self.meter.label, "error": self.meter.error},
            "session": {"started": t.started, "talk_s": round(t.speaking_s, 1), "over_s": round(t.over_s, 1),
                        "loud": t.loud_events, "peak": round(t.peak_dbfs, 1) if t.peak_dbfs > -120 else None},
            "calib": {"phase": c["phase"], "remaining": round(max(0.0, c["until"] - now), 1) if c["phase"] else 0,
                      "seconds": c["seconds"], "done": sorted(c["samples"]), "result": c["result"],
                      "error": c["error"]},
            "hotkeyFailures": self.hotkey_failures,
        }

    def run(self):
        self.open_mic()
        self.root.after(TICK_MS, self.tick)
        self.root.mainloop()

    def quit(self):
        if self.quitting:
            return
        self.quitting = True
        now = time.time()
        if self.move_mode:
            self._place_bar(*self._move_pos, save=True)
        self._record(self.tracker.interrupt(now))
        self.store.add_minutes(self.tracker.take_buckets(now, everything=True), self.cfg.threshold_dbfs)
        self.store.save_session(self.session_id, self.tracker.session_record(now))
        self.meter.stop()
        for hook in self.quit_hooks:
            try:
                hook()
            except Exception as e:
                print("Shutdown step failed:", repr(e))
        self.root.destroy()

    def settings_dict(self):
        return asdict(self.cfg)


def compact_duration(seconds):
    """For the bar's readout: "<1m", "12m", "1h 05m", "2d 4h". Changes at most once a minute."""
    minutes = int(max(seconds, 0) // 60)
    if minutes < 1:
        return "<1m"
    h, m = divmod(minutes, 60)
    if h >= 24:
        return f"{h // 24}d {h % 24}h"
    return f"{h}h {m:02d}m" if h else f"{m}m"


def format_duration(seconds):
    seconds = int(max(seconds, 0))
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    if h:
        return f"{h}h {m}m"
    return f"{m}m {s}s" if m else f"{s}s"
