"""Turning a stream of levels into loud moments, calm streaks and minute-by-minute stats. No UI and no I/O, so
it can be tested by feeding it made-up levels.

A loud moment is being over your line for `sustain_seconds` in a row. A laugh or one surprised shout goes red on
the bar but isn't counted: staying loud is what bothers the people you're talking to.

Talking time, time over the line and level are also added up per minute and per app. That's what lets the
dashboard say how much of your talking was loud, and in which games, and it's the shape ClipTrove's clips can be
matched against later (a clip has a time; the minute it was recorded has your level).
"""
import time
from dataclasses import dataclass

BURST_GAP_S = 1.2  # this much silence ends a burst of talking; the next word starts a new "last peak"
# Talking starts this far under your calibrated normal voice. Below it is keyboard, breathing and room noise, which
# the gate alone (set for silence) lets through, and which would otherwise count as talking time.
SPEECH_BELOW_NORMAL_DB = 18.0


def minute_key(ts):
    return time.strftime("%Y-%m-%dT%H:%M", time.localtime(ts))


def iso(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ts))


@dataclass
class Bucket:
    speaking_s: float = 0.0
    over_s: float = 0.0
    db_sum: float = 0.0        # level x seconds while talking: db_sum / speaking_s is the average level
    peak_dbfs: float = -120.0
    loud_events: int = 0


class Tracker:
    def __init__(self, cfg, now):
        self.cfg = cfg
        self.started = now
        self.speaking_s = 0.0
        self.over_s = 0.0
        self.loud_events = 0
        self.peak_dbfs = -120.0
        self.longest_calm_s = 0.0
        self.last_event_at = None
        self.hot = False
        self.over_duration = 0.0   # seconds over the line, in a row
        self.event_open = False
        self.event_peak = -120.0
        self.event_app = ""
        self.buckets = {}          # (minute, app) -> Bucket
        # The highest point of your latest burst of talking. It stays put once you stop, so the bar can show where
        # your last sentence peaked rather than a number that keeps sliding around while you play.
        self.last_peak = None
        self.quiet_for = BURST_GAP_S

    # ---------- reading ----------

    def floor(self):
        """The quietest level that counts as talking."""
        return max(self.cfg.gate_dbfs, self.cfg.normal_dbfs - SPEECH_BELOW_NORMAL_DB)

    def zone(self, level):
        """quiet | calm | close | loud: what the bar and the dashboard show."""
        if level <= self.floor():
            return "quiet"
        if self.hot:
            return "loud"
        if level >= self.cfg.threshold_dbfs - 6.0:
            return "close"
        return "calm"

    def calm_streak(self, now):
        return max(0.0, now - (self.last_event_at or self.started))

    def zone_of(self, level):
        """Which zone a level falls in, ignoring whether you're over the line right now (for the last peak)."""
        if level is None or level <= self.floor():
            return "quiet"
        if level >= self.cfg.threshold_dbfs:
            return "loud"
        return "close" if level >= self.cfg.threshold_dbfs - 6.0 else "calm"

    # ---------- advancing ----------

    def _bucket(self, now, app):
        key = (minute_key(now), app or "")
        bucket = self.buckets.get(key)
        if bucket is None:
            bucket = self.buckets[key] = Bucket()
        return bucket

    def update(self, level, elapsed, now, app=""):
        """Advance by `elapsed` seconds at `level` dBFS. Returns a list of signals:
        ("loud", calm_streak_that_ended) when a loud moment starts, ("event", record) when one ends."""
        cfg = self.cfg
        signals = []
        speaking = level > self.floor()
        # Cross the line to go loud; drop clearly back under it to go calm, so the bar doesn't flicker at the edge
        if level >= cfg.threshold_dbfs:
            self.hot = True
        elif level < cfg.threshold_dbfs - cfg.hysteresis_db:
            self.hot = False

        if speaking:
            if self.quiet_for >= BURST_GAP_S or self.last_peak is None:
                self.last_peak = level  # a new burst
            else:
                self.last_peak = max(self.last_peak, level)
            self.quiet_for = 0.0
        else:
            self.quiet_for += elapsed

        if speaking:
            self.speaking_s += elapsed
            self.peak_dbfs = max(self.peak_dbfs, level)
            bucket = self._bucket(now, app)
            bucket.speaking_s += elapsed
            bucket.db_sum += level * elapsed
            bucket.peak_dbfs = max(bucket.peak_dbfs, level)

        if self.hot and speaking:
            self.over_s += elapsed
            bucket.over_s += elapsed
            if self.over_duration == 0.0:
                self.event_peak = level
            self.over_duration += elapsed
            self.event_peak = max(self.event_peak, level)
            if not self.event_open and self.over_duration >= cfg.sustain_seconds:
                self.event_open = True
                self.event_app = app or ""
                streak = self.calm_streak(now)
                self.longest_calm_s = max(self.longest_calm_s, streak)
                self.loud_events += 1
                self.last_event_at = now
                bucket.loud_events += 1
                signals.append(("loud", streak))
        else:
            signals += self._close_event(now)
        return signals

    def interrupt(self, now):
        """Pausing or calibrating: finish any loud moment in progress and forget being over the line."""
        self.hot = False
        return self._close_event(now)

    def _close_event(self, now):
        signals = []
        if self.event_open:
            signals.append(("event", {
                "ts": iso(now - self.over_duration),
                "peak_dbfs": round(self.event_peak, 1),
                "duration_s": round(self.over_duration, 2),  # time actually spent over the line
                "threshold_dbfs": self.cfg.threshold_dbfs,
                "app": self.event_app,
            }))
            self.event_open = False
            self.last_event_at = now
        self.over_duration = 0.0
        return signals

    # ---------- handing data over ----------

    def take_buckets(self, now, everything=False):
        """Finished minutes (or all of them), removed from memory: [(minute, app, Bucket)]."""
        current = minute_key(now)
        done = [k for k in self.buckets if everything or k[0] < current]
        return [(minute, app, self.buckets.pop((minute, app))) for minute, app in sorted(done)]

    def session_record(self, now):
        return {
            "start": iso(self.started),
            "end": iso(now),
            "duration_s": round(now - self.started, 1),
            "speaking_s": round(self.speaking_s, 1),
            "over_s": round(self.over_s, 1),
            "loud_events": self.loud_events,
            "peak_dbfs": round(self.peak_dbfs, 1) if self.peak_dbfs > -120 else None,
            "longest_calm_s": round(max(self.longest_calm_s, self.calm_streak(now)), 1),
            "threshold_dbfs": self.cfg.threshold_dbfs,
        }
