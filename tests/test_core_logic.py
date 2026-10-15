"""The parts with no UI: the tracker, calibration maths and settings."""
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402

from vocalm import audio, calibrate, settings  # noqa: E402
from vocalm.tracker import Tracker, minute_key  # noqa: E402

STEP = 0.25


def run(tracker, level, seconds, start, app="Game"):
    """Feed `level` for `seconds`, starting at time `start`. Returns (signals, end time)."""
    out, t = [], start
    for _ in range(int(round(seconds / STEP))):
        t += STEP
        out += tracker.update(level, STEP, t, app)
    return out, t


class TrackerTest(unittest.TestCase):
    def setUp(self):
        self.cfg = settings.Settings(threshold_dbfs=-12.0, gate_dbfs=-60.0, sustain_seconds=1.5, hysteresis_db=2.0)
        self.t0 = 1_760_000_000.0
        self.tr = Tracker(self.cfg, self.t0)

    def test_silence_is_not_talking(self):
        run(self.tr, -80, 10, self.t0)
        self.assertEqual(self.tr.speaking_s, 0)
        self.assertEqual(self.tr.loud_events, 0)

    def test_keyboard_and_breathing_are_not_talking(self):
        # normal voice -31 dB: talking starts 18 dB under it, at -49, even though the gate is at -60
        self.assertEqual(self.tr.floor(), -49.0)
        run(self.tr, -55, 10, self.t0)
        self.assertEqual((self.tr.speaking_s, self.tr.zone(-55)), (0, "quiet"))
        run(self.tr, -45, 1, self.t0 + 10)
        self.assertEqual(self.tr.speaking_s, 1.0)

    def test_a_laugh_is_not_a_loud_moment_but_shouting_is(self):
        sig, t = run(self.tr, -6, 1.0, self.t0)          # short spike
        self.assertEqual(self.tr.loud_events, 0)
        sig, t = run(self.tr, -30, 2.0, t)
        sig, t = run(self.tr, -5, 4.0, t)                # sustained
        self.assertEqual(self.tr.loud_events, 1)
        self.assertEqual([k for k, _ in sig], ["loud"])
        sig, t = run(self.tr, -30, 1.0, t)
        events = [p for k, p in sig if k == "event"]
        self.assertEqual(len(events), 1)
        self.assertAlmostEqual(events[0]["duration_s"], 4.0, places=2)  # time over the line, not wall clock
        self.assertEqual(events[0]["peak_dbfs"], -5.0)
        self.assertEqual(events[0]["app"], "Game")

    def test_hysteresis_stops_flicker_at_the_line(self):
        self.tr.update(-12.0, STEP, self.t0 + 1)
        self.assertTrue(self.tr.hot)
        self.tr.update(-13.0, STEP, self.t0 + 2)      # inside the 2 dB band: still loud
        self.assertTrue(self.tr.hot)
        self.tr.update(-15.0, STEP, self.t0 + 3)
        self.assertFalse(self.tr.hot)

    def test_minutes_split_by_minute_and_app(self):
        start = (self.t0 // 60) * 60 + 50                 # 10 s before a minute boundary
        run(self.tr, -30, 20, start, app="VALORANT")
        run(self.tr, -30, 4, start + 20, app="Discord")
        rows = self.tr.take_buckets(start + 200, everything=False)
        keys = {(m, a): round(b.speaking_s, 2) for m, a, b in rows}
        self.assertEqual(keys[(minute_key(start + 1), "VALORANT")], 9.75)
        self.assertEqual(keys[(minute_key(start + 15), "VALORANT")], 10.25)
        self.assertEqual(keys[(minute_key(start + 21), "Discord")], 4.0)
        self.assertEqual(self.tr.buckets, {})
        mean = sum(b.db_sum for _, _, b in rows) / sum(b.speaking_s for _, _, b in rows)
        self.assertAlmostEqual(mean, -30.0)

    def test_unfinished_minute_stays_until_asked(self):
        run(self.tr, -30, 2, self.t0)
        self.assertEqual(self.tr.take_buckets(self.t0 + 2), [])
        self.assertEqual(len(self.tr.take_buckets(self.t0 + 2, everything=True)), 1)

    def test_pause_finishes_a_loud_moment(self):
        _, t = run(self.tr, -5, 3, self.t0)
        sig = self.tr.interrupt(t)
        self.assertEqual([k for k, _ in sig], ["event"])
        self.assertFalse(self.tr.hot)

    def test_only_red_peaks_are_kept(self):
        _, t = run(self.tr, -30, 2, self.t0)       # calm (green)
        _, t = run(self.tr, -15, 2, t)             # close to the line (amber): still nothing
        self.assertIsNone(self.tr.red_peak)
        _, t = run(self.tr, -11, 0.5, t)           # over the line: red
        _, t = run(self.tr, -6, 0.25, t)           # its top
        _, t = run(self.tr, -11, 0.25, t)
        self.assertEqual(self.tr.red_peak, -6)

    def test_red_moments_close_together_are_one_peak(self):
        _, t = run(self.tr, -6, 0.5, self.t0)
        _, t = run(self.tr, -30, 1.5, t)           # a breath, then red again
        _, t = run(self.tr, -10, 0.5, t)
        self.assertEqual(self.tr.red_peak, -6)     # still the shout's top, not the lower second burst
        _, t = run(self.tr, -30, 10, t)            # a real gap...
        _, t = run(self.tr, -10, 0.5, t)           # ...and the next time over the line is its own peak
        self.assertEqual(self.tr.red_peak, -10)

    def test_red_peak_goes_away_after_the_timer(self):
        _, t = run(self.tr, -6, 0.5, self.t0)
        self.assertTrue(self.tr.red_peak_showing(t + 299, 300))
        self.assertFalse(self.tr.red_peak_showing(t + 301, 300))
        self.assertTrue(self.tr.red_peak_showing(t + 10 ** 6, 0))   # 0 = never hide
        self.assertFalse(Tracker(self.cfg, self.t0).red_peak_showing(self.t0, 0))

    def test_calm_streak(self):
        self.assertEqual(self.tr.calm_streak(self.t0 + 100), 100)
        _, t = run(self.tr, -5, 2, self.t0 + 100)
        self.assertLess(self.tr.calm_streak(t), 1)
        self.assertEqual(self.tr.longest_calm_s, 101.5)


class PerceivedLoudnessTest(unittest.TestCase):
    """A-weighting checked against its published values, with pure tones through the real code path."""

    RATE, N = 48000.0, 512

    def level(self, freq, amplitude=0.3):
        t = np.arange(self.N) / self.RATE
        block = (amplitude * np.sin(2 * np.pi * freq * t + 0.3)).astype(np.float32)
        return audio.Perceived().level(block[:, None], self.RATE), audio.dbfs(block)

    def test_1_khz_is_unchanged(self):
        perceived, flat = self.level(1000)
        self.assertAlmostEqual(perceived, flat, delta=0.4)

    def test_high_pitch_counts_more_and_low_rumble_less(self):
        # IEC 61672: +1.2 dB at 2.5 kHz, -8.6 dB at 250 Hz. A tone's FFT smears a little, so allow some slack.
        p, f = self.level(2500)
        self.assertAlmostEqual(p - f, 1.3, delta=0.6)
        p, f = self.level(250)
        self.assertAlmostEqual(p - f, -8.6, delta=1.5)
        # A squeal at 3 kHz and a deep 150 Hz voice with the same raw energy: the squeal now reads far louder
        self.assertGreater(self.level(3000)[0] - self.level(150)[0], 12)

    def test_curve_shape(self):
        w = 10 * np.log10(audio.a_weight_power([100, 1000, 4000, 10000]))
        np.testing.assert_allclose(w, [-19.1, 0.0, 1.0, -2.5], atol=0.15)

    def test_silence(self):
        self.assertLess(audio.Perceived().level(np.zeros((512, 2), np.float32), 48000.0), -200)


class CompactDurationTest(unittest.TestCase):
    def test_readout_never_ticks_in_seconds(self):
        from vocalm.overlay import compact_duration
        self.assertEqual([compact_duration(s) for s in (0, 59, 60, 754, 3600 + 300, 2 * 86400 + 4 * 3600)],
                         ["<1m", "<1m", "1m", "12m", "1h 05m", "2d 4h"])


class CalibrateTest(unittest.TestCase):
    def test_line_goes_45_percent_of_the_way(self):
        r = calibrate.compute([-30.0] * 100, [-10.0] * 50, -60)
        self.assertEqual(r["threshold"], -21.0)
        self.assertEqual(r["note"], "")

    def test_never_closer_than_6_db(self):
        r = calibrate.compute([-40.8] * 100, [-30.2] * 50, -60)  # the real numbers from August
        self.assertEqual(r["threshold"], -34.8)
        self.assertIn("6 dB", r["note"])

    def test_same_both_times(self):
        self.assertEqual(calibrate.compute([-30.0] * 100, [-29.0] * 50, -60)["threshold"], -24.0)

    def test_silence_is_refused(self):
        with self.assertRaises(ValueError):
            calibrate.compute([-80.0] * 500, [-10.0] * 50, -60)
        with self.assertRaises(ValueError):
            calibrate.compute([-30.0] * 100, [-90.0] * 50, -60)


class SettingsTest(unittest.TestCase):
    def test_clamps_and_rejects_nonsense(self):
        cfg = settings.normalize({"threshold_dbfs": 40, "bar_width": "wide", "opacity": float("nan"),
                                  "corner": "middle", "port": 99, "blocksize": 300, "sustain_seconds": "inf"})
        self.assertEqual(cfg.threshold_dbfs, 0.0)
        self.assertEqual(cfg.bar_width, 180)
        self.assertEqual(cfg.opacity, 0.85)
        self.assertEqual(cfg.corner, "top-right")
        self.assertEqual(cfg.port, 1024)
        self.assertEqual(cfg.blocksize, 512)
        self.assertEqual(cfg.sustain_seconds, 1.5)

    def test_patch_keeps_the_rest(self):
        base = settings.normalize({"threshold_dbfs": -24.1, "pos_x": -295, "pos_y": 1778})
        new = settings.normalize({"sustain_seconds": 2}, base=base)
        self.assertEqual((new.threshold_dbfs, new.pos_x, new.pos_y, new.sustain_seconds), (-24.1, -295, 1778, 2.0))

    def test_null_position_means_back_to_the_corner(self):
        base = settings.normalize({"pos_x": -295, "pos_y": 1778})
        new = settings.normalize({"pos_x": None, "pos_y": None}, base=base)
        self.assertIsNone(new.pos_x)
        self.assertIsNone(new.pos_y)

    def test_half_a_position_is_dropped(self):
        self.assertIsNone(settings.normalize({"pos_x": 5}).pos_x)

    def test_peak_timer(self):
        self.assertEqual(settings.normalize({}).bar_info_timeout_s, 300)
        self.assertEqual(settings.normalize({"bar_info_timeout_s": 0}).bar_info_timeout_s, 0)      # never
        self.assertEqual(settings.normalize({"bar_info_timeout_s": 3}).bar_info_timeout_s, 10)     # too short
        self.assertEqual(settings.normalize({"bar_info_timeout_s": -5}).bar_info_timeout_s, 0)
        self.assertEqual(settings.normalize({"bar_info_timeout_s": 1e9}).bar_info_timeout_s, 86400)

    def test_older_setups_measure_flat_until_switched(self):
        import json
        import tempfile
        from vocalm import paths
        folder = tempfile.mkdtemp(prefix="vocalm-settings-")
        try:
            paths.configure(folder)
            with open(paths.SETTINGS, "w", encoding="utf-8") as f:
                json.dump({"threshold_dbfs": -24.1}, f)                 # saved by 0.1.1: no "loudness" yet
            self.assertEqual(settings.load()[0].loudness, "flat")
            settings.save(settings.normalize({"loudness": "perceived"}))
            self.assertEqual(settings.load()[0].loudness, "perceived")
            self.assertEqual(settings.Settings().loudness, "perceived")  # a brand-new setup starts perceived
        finally:
            paths.configure()

    def test_bar_label_choices(self):
        self.assertEqual(settings.normalize({"bar_label": "streak"}).bar_label, "streak")
        self.assertEqual(settings.normalize({"bar_label": "fireworks"}).bar_label, "peak")

    def test_bools_from_strings(self):
        self.assertFalse(settings.normalize({"nudge_enabled": "false"}).nudge_enabled)
        self.assertTrue(settings.normalize({"nudge_enabled": "on"}).nudge_enabled)

    def test_line_stays_above_the_gate(self):
        cfg = settings.normalize({"threshold_dbfs": -70, "gate_dbfs": -60})
        self.assertGreater(cfg.threshold_dbfs, cfg.gate_dbfs)


if __name__ == "__main__":
    unittest.main()
