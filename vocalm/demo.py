"""Demo mode, for screenshots and videos: `python -m vocalm --demo`.

A scripted voice instead of a microphone, and two and a half months of made-up history in made-up games, in a
throwaway data folder. Nothing from your own history is ever shown, and the same pictures come out every time.
"""
import math
import os
import random
import tempfile
import threading
import time
from datetime import date, datetime, timedelta

from . import settings, store
from .tracker import Bucket

GAMES = {  # name: (how often you play it, how heated it gets)
    "Ironfall Tactics": (1.0, 1.6),
    "Neon Drift": (0.8, 0.9),
    "Skyreach Arena": (0.7, 1.25),
    "Hollow Pines": (0.45, 0.4),
}
CHAT = "Discord"
LINE, NORMAL = -24.0, -32.0
DAYS = 75


def demo_settings(port):
    return settings.normalize({
        "device_query": "Demo microphone", "threshold_dbfs": LINE, "normal_dbfs": NORMAL, "loudness": "perceived",
        "setup_done": True, "legacy_imported": True, "hotkeys_enabled": False, "track_apps": True, "port": port,
        "nudge_cooldown_s": 20, "bar_info_timeout_s": 300, "bar_label": "peak", "corner": "top-right",
    })


# ---------- the made-up history ----------

def make_history(db_path, today=None, seed=7):
    """Fill a fresh database with believable, made-up history: evenings and weekends, a few games, and a voice that
    slowly gets calmer (the story the Insights page is there to tell)."""
    rng = random.Random(seed)
    today = today or date.today()
    store.init(db_path)
    events, minutes, sessions = [], {}, []
    for back in range(DAYS, -1, -1):
        day = today - timedelta(days=back)
        weekend = day.weekday() >= 5
        if back and rng.random() > (0.88 if weekend else 0.55):
            continue
        progress = 1 - back / DAYS                        # 0 at the start, 1 today
        calmer = 1.0 - 0.55 * progress                    # loud moments fall by about half over the period
        line = -21.0 if back > 52 else LINE               # recalibrated once, early on
        starts = [rng.choice([14, 15, 16]), rng.choice([19, 20, 21])] if weekend else [rng.choice([19, 20, 21])]
        if back == 0:
            starts = [max(9, datetime.now().hour - 2)]
        for start_hour in starts:
            games = rng.sample(list(GAMES), k=rng.choice([1, 1, 2]))
            t = datetime.combine(day, datetime.min.time()) + timedelta(hours=start_hour, minutes=rng.randint(0, 40))
            run_start, talked, loud = t, 0.0, 0
            for game in games:
                weight, heat = GAMES[game]
                length = int(rng.uniform(25, 110) * (0.6 + weight * 0.5)) if back else 35
                for m in range(length):
                    minute = (t + timedelta(minutes=m))
                    if back == 0 and minute > datetime.now() - timedelta(minutes=5):
                        break
                    app = CHAT if rng.random() < 0.12 else game
                    talk = rng.uniform(10, 42)
                    rate = heat * calmer * (1.4 if minute.hour >= 22 else 1.0) * 0.16
                    n_loud = sum(1 for _ in range(3) if rng.random() < rate / 3 * (talk / 30))
                    over = n_loud * rng.uniform(1.6, 3.4) + rng.uniform(0, 2.5) * heat * calmer
                    key = (minute.strftime("%Y-%m-%dT%H:%M"), app)
                    b = minutes.setdefault(key, [Bucket(), line])[0]
                    mean = NORMAL + rng.gauss(0, 1.6) + heat * 1.2
                    b.speaking_s += talk
                    b.over_s += min(over, talk)
                    b.db_sum += mean * talk
                    b.peak_dbfs = max(b.peak_dbfs, line + rng.uniform(-6, 4) + n_loud * rng.uniform(2, 5))
                    b.loud_events += n_loud
                    for _ in range(n_loud):
                        at = minute + timedelta(seconds=rng.uniform(0, 59))
                        peak = line + min(21.0, rng.expovariate(1 / (4.5 + heat * 2.5)) + 1.0)
                        events.append((at.strftime("%Y-%m-%dT%H:%M:%S"), round(peak, 1), round(rng.uniform(1.5, 4.2), 2),
                                       line, app))
                    talked += talk
                    loud += n_loud
                t += timedelta(minutes=length + rng.randint(2, 10))
            sessions.append((run_start, t, talked, loud))

    from contextlib import closing
    with closing(store.connect(db_path)) as db:
        db.executemany("INSERT INTO events(ts, peak_dbfs, duration_s, threshold_dbfs, app) VALUES (?,?,?,?,?)", events)
        db.executemany(
            "INSERT INTO minutes(minute, app, speaking_s, over_s, db_sum, peak_dbfs, loud_events, threshold_dbfs) "
            "VALUES (?,?,?,?,?,?,?,?)",
            [(k[0], k[1], b.speaking_s, b.over_s, b.db_sum, b.peak_dbfs, b.loud_events, line)
             for k, (b, line) in minutes.items()])
        db.executemany(
            "INSERT INTO sessions(start, end, duration_s, speaking_s, over_s, loud_events, longest_calm_s, "
            "threshold_dbfs) VALUES (?,?,?,?,0,?,?,?)",
            [(s.strftime("%Y-%m-%dT%H:%M:%S"), e.strftime("%Y-%m-%dT%H:%M:%S"), (e - s).total_seconds(), talk, n,
              (e - s).total_seconds() / max(1, n + 1), LINE) for s, e, talk, n in sessions])
        db.commit()
    return len(events)


def prepare(port):
    """A throwaway data folder with made-up history and demo settings. Returns its path."""
    folder = tempfile.mkdtemp(prefix="vocalm-demo-")
    from . import paths
    paths.configure(folder)
    # The dashboard shows its data folder; demo footage is public, so show a generic one, not your user name
    paths.SHOWN_DATA = r"C:\Users\you\AppData\Local\Vocalm"
    settings.save(demo_settings(port))
    make_history(paths.DB)
    return folder


# ---------- the scripted voice ----------

# (seconds, level at the start, level at the end, wobble): a 26-second loop that walks through every state
SCRIPT = [
    (2.4, -33, -32, 4.0), (0.6, -80, -80, 0), (3.0, -32, -31, 4.0),   # chatting calmly
    (2.5, -30, -26.5, 3.0),                                           # getting into it: amber
    (3.2, -19, -13, 2.5),                                             # shouting: red, long enough to count
    (1.4, -80, -80, 0),                                               # a breath
    (4.5, -33, -33, 4.0), (0.8, -80, -80, 0), (3.2, -32, -32, 4.0),   # calm again
    (4.4, -80, -80, 0),
]
PERIOD = sum(s[0] for s in SCRIPT)


def scripted_level(t, rng=None):
    """The raw level at time t (seconds): the script plus syllable-like wobble."""
    t %= PERIOD
    for length, a, b, wobble in SCRIPT:
        if t < length:
            base = a + (b - a) * (t / length)
            if wobble:
                base += wobble * (0.5 * math.sin(t * 2 * math.pi * 3.7) + 0.5 * math.sin(t * 2 * math.pi * 1.3))
                base += (rng or random).uniform(-1.2, 1.2)
            return base
        t -= length
    return -80.0


class ScriptedMeter:
    """Stands in for audio.LevelMeter: same attributes and methods, levels from SCRIPT instead of a microphone."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.level = -120.0
        self.label = "Demo microphone (scripted)"
        self.error = ""
        self.last_callback = 0.0
        self.switch_floor = None
        self.switch_diffs = []
        self._capture = None
        self._running = False
        self._t0 = time.monotonic()

    def _run(self):
        rng, block = random.Random(1), 512 / 48000.0
        attack = math.exp(-block / (self.cfg.attack_ms / 1000.0))
        release = math.exp(-block / (self.cfg.release_ms / 1000.0))
        while self._running:
            db = scripted_level(time.monotonic() - self._t0, rng)
            coef = attack if db > self.level else release
            self.level = db if self.level <= -119 else coef * self.level + (1 - coef) * db
            self.last_callback = time.monotonic()
            if self._capture is not None:
                self._capture.append(db)
            time.sleep(block)

    def start(self):
        if not self._running:
            self._running = True
            threading.Thread(target=self._run, name="demo-mic", daemon=True).start()

    def stop(self):
        self._running = False

    @property
    def running(self):
        return self._running

    def stalled(self, seconds=3.0):
        return not self._running

    def blocks_per_second(self):
        return 48000.0 / 512

    def begin_capture(self):
        self._capture = []

    def end_capture(self):
        samples, self._capture = self._capture or [], None
        return samples


class FixedApp:
    """Stands in for winapi.ForegroundWatcher, so demo footage never shows which apps you really have open."""

    def __init__(self, name="Ironfall Tactics"):
        self.name = name

    def current(self):
        return self.name


def demo_port():
    return int(os.environ.get("VOCALM_DEMO_PORT", "48690"))
