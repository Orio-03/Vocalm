"""Demo mode: its made-up history fills every chart, and its scripted voice walks through every state."""
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import closing
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vocalm import audio, demo, games, settings, store  # noqa: E402


class DemoTest(unittest.TestCase):
    def test_history_fills_the_insights_page(self):
        folder = tempfile.mkdtemp(prefix="vocalm-demo-test-")
        try:
            path = os.path.join(folder, "vocalm.db")
            self.assertGreater(demo.make_history(path, today=date(2026, 10, 15)), 100)
            with closing(store.connect(path)) as db:
                o = store.overview(db, 30, today=date(2026, 10, 15))
            self.assertGreater(o["totals"]["loud"], 50)
            self.assertIsNotNone(o["totals"]["rate"])
            self.assertTrue(set(demo.GAMES) & {a["app"] for a in o["apps"]})
            self.assertGreater(sum(map(sum, o["heatmap"])), 50)
            self.assertNotIn("", {a["app"] for a in o["apps"]})
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    def test_history_fills_the_games_page_from_its_own_clips(self):
        folder = tempfile.mkdtemp(prefix="vocalm-demo-test-")
        try:
            path, clips = os.path.join(folder, "vocalm.db"), os.path.join(folder, "Cliptrove")
            demo.make_history(path, today=date(2026, 10, 15), cliptrove=clips)
            with closing(store.connect(path)) as db:
                g = games.report(db, 0, today=date(2026, 10, 15), cliptrove=clips)
            self.assertEqual({x["game"] for x in g["games"]}, set(demo.GAMES))
            self.assertGreater(g["totals"]["clipsHeard"], 20)
            self.assertIsNotNone(g["verdict"])
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    def test_script_goes_calm_close_loud_and_quiet(self):
        cfg = demo.demo_settings(48690)
        levels = [demo.scripted_level(t / 10) for t in range(int(demo.PERIOD * 10))]
        self.assertTrue(any(v < cfg.normal_dbfs - 18 for v in levels))                       # pauses
        self.assertTrue(any(cfg.threshold_dbfs - 6 <= v < cfg.threshold_dbfs for v in levels))  # amber
        self.assertGreater(sum(v >= cfg.threshold_dbfs for v in levels), 15)                # long enough to count

    def test_scripted_meter_stands_in_for_the_real_one(self):
        """The core only uses what LevelMeter offers; the scripted one must offer all of it."""
        real = audio.LevelMeter(settings.Settings())
        fake = demo.ScriptedMeter(settings.Settings())
        wanted = {"level", "label", "error", "switch_floor", "switch_diffs", "start", "stop", "running", "stalled",
                  "blocks_per_second", "begin_capture", "end_capture"}
        for name in wanted:
            self.assertTrue(hasattr(real, name), name)
            self.assertTrue(hasattr(fake, name), name)


if __name__ == "__main__":
    unittest.main()
