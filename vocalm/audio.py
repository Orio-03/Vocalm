"""Listening to the microphone: finding it, opening it, and turning blocks of samples into a smooth level.

Two Windows quirks shape this file:
  * WASAPI devices refuse any sample rate but their own, so the device's default rate is always tried first.
  * PortAudio's WASAPI backend needs COM set up on the thread that starts the stream, so streams are opened on
    the main thread. PortAudio calls us back on its own thread anyway; no worker thread of ours is needed.
"""
import math
import sys
import threading
import time

import numpy as np
import sounddevice as sd

# WDM-KS is left out on purpose: it can take the microphone in exclusive mode and knock Discord off it.
PREFERRED_HOSTAPIS = ["Windows WASAPI", "Windows DirectSound", "MME"]


def dbfs(block):
    """RMS level of a block of float samples, in dBFS (0 = full scale)."""
    rms = float(np.sqrt(np.mean(np.square(block, dtype=np.float64))))
    return 20.0 * math.log10(max(rms, 1e-12))


def input_devices():
    """[(index, host api name, device info)] for everything that can record."""
    apis = {i: h["name"] for i, h in enumerate(sd.query_hostapis())}
    return [(i, apis.get(d["hostapi"], "?"), d) for i, d in enumerate(sd.query_devices())
            if d.get("max_input_channels", 0) > 0]


def _by_preference(entries):
    ordered = []
    for api in PREFERRED_HOSTAPIS:
        ordered += [e for e in entries if e[1] == api]
    return ordered + [e for e in entries if e[1] not in PREFERRED_HOSTAPIS and "WDM-KS" not in e[1]]


def rank_devices(query):
    """Devices to try, best first: name matches (WASAPI first), then Windows' default mic, then the rest."""
    devices = input_devices()
    ranked = []
    if query:
        needle = query.lower()
        ranked = _by_preference([e for e in devices if needle in e[2]["name"].lower()])
    try:
        default = sd.default.device[0]
    except Exception:
        default = None
    ranked += [e for e in devices if e[0] == default and e not in ranked]
    ranked += [e for e in _by_preference(devices) if e not in ranked]
    return ranked


def device_choices():
    """Microphone names for the settings page, one per physical device (Windows lists each up to four times)."""
    seen, out = set(), []
    for _, api, dev in _by_preference(input_devices()):
        name = dev["name"].strip()
        key = name.lower()[:31]  # MME cuts names at 31 characters; match those to their full-length twins
        if key in seen or "sound mapper" in key or "primary sound capture" in key:
            continue
        seen.add(key)
        out.append(name)
    return out


def open_stream(cfg, callback):
    """Open and start a capture stream, working down the candidates until one opens. Returns (stream, label, rate).
    Raises RuntimeError listing what was tried."""
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.ole32.CoInitializeEx(None, 0x2)  # harmless if COM is already set up
        except OSError:
            pass
    tried = []
    for index, api, dev in rank_devices(cfg.device_query)[:6]:
        rates = []
        for rate in (cfg.samplerate, dev["default_samplerate"], 48000, 44100):
            if rate and float(rate) not in rates:
                rates.append(float(rate))
        for channels in dict.fromkeys([min(2, int(dev["max_input_channels"])), 1]):
            for rate in rates:
                try:
                    stream = sd.InputStream(device=index, channels=channels, samplerate=rate,
                                            blocksize=cfg.blocksize, dtype="float32", callback=callback)
                    stream.start()
                    return stream, f"{dev['name'].strip()} ({api}, {int(rate)} Hz)", rate
                except Exception as e:
                    tried.append(f"{dev['name'].strip()} [{api}, {channels} ch, {int(rate)} Hz]: "
                                 f"{str(e).splitlines()[0]}")
    raise RuntimeError("Couldn't open a microphone." + ("\n\nTried:\n" + "\n".join(tried[:6]) if tried else
                                                         "\n\nNo recording devices were found."))


class LevelMeter:
    """The microphone level, smoothed the way a VU meter is: it jumps up almost instantly (so a shout shows the
    moment it happens) and falls back slowly (so the bar glides instead of flickering)."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.level = -120.0          # the smoothed level, read by the UI thread
        self.label = ""              # which device, for the dashboard
        self.error = ""
        self.last_callback = 0.0
        self._stream = None
        self._attack = self._release = 0.0
        self._capture = None         # calibration: raw block levels while capturing
        self._lock = threading.Lock()

    def _callback(self, indata, frames, time_info, status):
        db = dbfs(indata)
        coef = self._attack if db > self.level else self._release
        self.level = db if self.level <= -119.0 else coef * self.level + (1.0 - coef) * db
        self.last_callback = time.monotonic()
        if self._capture is not None:
            with self._lock:
                if self._capture is not None:
                    self._capture.append(db)

    def start(self):
        """Open the microphone. Call on the main thread. Raises RuntimeError if nothing opens."""
        self.stop()
        self._stream, self.label, rate = open_stream(self.cfg, self._callback)
        block = self.cfg.blocksize / rate
        self._attack = math.exp(-block / max(self.cfg.attack_ms / 1000.0, 1e-4))
        self._release = math.exp(-block / max(self.cfg.release_ms / 1000.0, 1e-4))
        self.last_callback = time.monotonic()
        self.error = ""

    def stop(self):
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass
        self.level = -120.0

    @property
    def running(self):
        return self._stream is not None

    def stalled(self, seconds=3.0):
        """True when the device has stopped delivering audio, e.g. a headset was unplugged."""
        return self._stream is None or time.monotonic() - self.last_callback > seconds

    def begin_capture(self):
        with self._lock:
            self._capture = []

    def end_capture(self):
        with self._lock:
            samples, self._capture = self._capture or [], None
        return samples
