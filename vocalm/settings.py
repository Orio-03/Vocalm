"""Your settings, in settings.json in the data folder.

Every value is checked and clamped on the way in (from the file, from the dashboard, from an old config), so the
rest of the app never sees a threshold of +40 dB or a 0-pixel bar.
"""
import json
import os
from dataclasses import asdict, dataclass, fields
from typing import Optional

from . import DEFAULT_PORT, paths

CORNERS = ("top-right", "top-left", "top-center", "bottom-right", "bottom-left")
BAR_LABELS = ("peak", "streak", "off")


@dataclass
class Settings:
    # --- what to listen to ---
    device_query: str = "Sonar - Microphone"  # part of the microphone's name; empty = Windows' default mic
    samplerate: Optional[int] = None          # None = the device's own rate
    blocksize: int = 512                      # ~11 ms at 48 kHz

    # --- levels (dBFS) ---
    threshold_dbfs: float = -24.0             # the "too loud for my friends" line
    normal_dbfs: float = -31.0                # your calm speaking level (set by calibration)
    gate_dbfs: float = -60.0                  # below this you're not talking
    hysteresis_db: float = 2.0                # drop this far under the line to count as calm again

    # --- how it reacts ---
    attack_ms: float = 50.0
    release_ms: float = 400.0
    sustain_seconds: float = 1.5              # loud for this long = a loud moment

    # --- the bar ---
    overlay_visible: bool = True
    pos_x: Optional[int] = None               # absolute desktop position, set with Move bar; None = use corner
    pos_y: Optional[int] = None
    corner: str = "top-right"
    margin_px: int = 24
    bar_width: int = 180
    bar_height: int = 14
    bar_grow_px: int = 6
    opacity: float = 0.85
    bar_label: str = "peak"                   # next to the bar: peak (your last peak) | streak (calm streak) | off
    bar_peak_marker: bool = True              # a small mark on the bar where your last peak reached

    # --- the breathe card ---
    nudge_enabled: bool = True
    nudge_cooldown_s: float = 30.0
    nudge_duration_s: float = 4.0

    # --- app ---
    hotkeys_enabled: bool = True
    track_apps: bool = True                   # note which app or game was in front when you talked
    port: int = DEFAULT_PORT
    setup_done: bool = False                  # the dashboard shows the welcome until this is true
    legacy_imported: bool = False


# (min, max) for numbers; anything outside is clamped
RANGES = {
    "threshold_dbfs": (-80.0, 0.0), "normal_dbfs": (-90.0, 0.0), "gate_dbfs": (-90.0, -20.0),
    "hysteresis_db": (0.0, 10.0), "attack_ms": (5.0, 500.0), "release_ms": (50.0, 3000.0),
    "sustain_seconds": (0.3, 10.0), "margin_px": (0, 400), "bar_width": (60, 600), "bar_height": (6, 60),
    "bar_grow_px": (0, 30), "opacity": (0.2, 1.0), "nudge_cooldown_s": (5.0, 600.0),
    "nudge_duration_s": (1.0, 20.0), "port": (1024, 65535), "pos_x": (-100000, 100000),
    "pos_y": (-100000, 100000), "samplerate": (8000, 384000),
}
BLOCKSIZES = (128, 256, 512, 1024, 2048)
TYPES = {f.name: f.type for f in fields(Settings)}
NULLABLE = {"samplerate", "pos_x", "pos_y"}  # None is a real value for these ("use the default")
INTEGERS = {"samplerate", "pos_x", "pos_y", "blocksize", "margin_px", "bar_width", "bar_height", "bar_grow_px", "port"}


def _coerce(name, value, default):
    kind = TYPES[name]
    if value is None:
        return None if name in NULLABLE else default
    try:
        if kind is bool:
            if isinstance(value, str):
                return value.strip().lower() in ("1", "true", "yes", "on")
            return bool(value)
        if kind is str:
            return str(value).strip()[:200]
        if name in INTEGERS:
            value = int(round(float(value)))
        else:
            value = float(value)
        if value != value or value in (float("inf"), float("-inf")):  # NaN / infinity
            return default
    except (TypeError, ValueError, OverflowError):
        return default
    lo, hi = RANGES.get(name, (None, None))
    if lo is not None:
        value = max(lo, min(hi, value))
    return value


def normalize(data, base=None):
    """A Settings built from `data` (a dict), falling back to `base` (or the defaults) for anything missing or bad."""
    out = Settings() if base is None else Settings(**asdict(base))
    if not isinstance(data, dict):
        return out
    for f in fields(Settings):
        if f.name in data:
            setattr(out, f.name, _coerce(f.name, data[f.name], getattr(out, f.name)))
    if out.corner not in CORNERS:
        out.corner = "top-right"
    if out.bar_label not in BAR_LABELS:
        out.bar_label = "peak"
    if out.blocksize not in BLOCKSIZES:
        out.blocksize = 512
    if out.threshold_dbfs <= out.gate_dbfs:
        out.threshold_dbfs = out.gate_dbfs + 10.0
    if (out.pos_x is None) != (out.pos_y is None):
        out.pos_x = out.pos_y = None
    return out


def load():
    """(settings, is_new). is_new is True the very first time, before settings.json exists."""
    try:
        with open(paths.SETTINGS, encoding="utf-8") as f:
            return normalize(json.load(f)), False
    except FileNotFoundError:
        return Settings(), True
    except (OSError, ValueError) as e:
        print(f"Couldn't read settings.json ({e}); using defaults")
        return Settings(), False


def save(cfg):
    os.makedirs(paths.DATA, exist_ok=True)
    tmp = paths.SETTINGS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(asdict(cfg), f, indent=2)
    os.replace(tmp, paths.SETTINGS)  # never leave a half-written file behind


def from_legacy(folder):
    """Settings carried over from the old script's config.json, or None."""
    try:
        with open(os.path.join(folder, "config.json"), encoding="utf-8") as f:
            old = json.load(f)
    except (OSError, ValueError):
        return None
    # Same names in both, apart from the app-level ones that didn't exist before
    return normalize({k: v for k, v in old.items() if k in TYPES})
