"""Games: naming the game from the app or from ClipTrove's clips, checking each clip, and the improvement verdict."""
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import closing
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vocalm import games, store  # noqa: E402

TODAY = date(2026, 10, 27)


def local(ts):
    """A ClipTrove 'recorded' value (with its UTC offset) for a local time."""
    return datetime.fromisoformat(ts).astimezone().isoformat(timespec="seconds")


class NamesTest(unittest.TestCase):
    def test_norm_and_display(self):
        self.assertEqual(games.norm("Rocket League�"), "rocketleague")
        self.assertEqual(games.norm("ROCKET LEAGUE®"), "rocketleague")
        self.assertEqual(games.display("Battlefield� 6"), "Battlefield 6")
        self.assertEqual(games.display("Rocket League®"), "Rocket League")

    def test_app_names_a_game(self):
        g = games.Games([("League of Legends", "2026-10-20T20:00:00"), ("WARDOGS", "2026-10-21T20:00:00")], [])
        self.assertEqual(g.of("2026-10-01T10:00:00", "League of Legends Client"), ("leagueoflegends", "app"))
        self.assertEqual(g.of("2026-10-01T10:00:00", "Wardogs"), ("wardogs", "app"))
        self.assertEqual(g.of("2026-10-01T10:00:00", "Discord"), (None, None))  # not near any clip

    def test_clips_place_talk_in_chat_apps_and_old_moments(self):
        g = games.Games([("Apex Legends", "2026-10-20T21:00:00"), ("Apex Legends", "2026-10-20T21:30:00")], [])
        self.assertEqual(g.of("2026-10-20T20:45:00", "Discord"), ("apexlegends", "clips"))   # lead-in before clip 1
        self.assertEqual(g.of("2026-10-20T21:15:00", ""), ("apexlegends", "clips"))          # between the clips
        self.assertEqual(g.of("2026-10-20T19:00:00", ""), (None, None))                      # well before the run
        self.assertEqual(g.of("2026-10-20T23:00:00", ""), (None, None))                      # well after it

    def test_learns_an_exe_name_from_clips(self):
        clips = [("Counter-Strike 2", f"2026-10-2{d}T21:00:00") for d in range(4)]
        rows = [(f"2026-10-2{d}T20:59:30", "cs2") for d in range(4)]
        g = games.Games(clips, rows)
        self.assertEqual(g.learned, {"cs2": "counterstrike2"})
        self.assertEqual(g.of("2026-09-01T12:00:00", "cs2"), ("counterstrike2", "app"))

    def test_never_learns_a_chat_app(self):
        clips = [("Counter-Strike 2", f"2026-10-2{d}T21:00:00") for d in range(4)]
        g = games.Games(clips, [(f"2026-10-2{d}T20:59:30", "Discord") for d in range(4)])
        self.assertEqual(g.learned, {})


class ReportTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="vocalm-games-")
        self.db_path = os.path.join(self.dir, "vocalm.db")
        self.ct = os.path.join(self.dir, "Cliptrove")
        os.makedirs(self.ct)
        store.init(self.db_path)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def cliptrove(self, clips):
        with open(os.path.join(self.ct, "library.json"), "w", encoding="utf-8") as f:
            json.dump({"snapshot": {f"k{i}": {"game": g, "recorded": local(t), "title": "secret title", "file": "x"}
                                    for i, (g, t) in enumerate(clips)}}, f)

    def history(self, events=(), minutes=()):
        with closing(store.connect(self.db_path)) as db:
            db.executemany("INSERT INTO events(ts, peak_dbfs, duration_s, threshold_dbfs, app) VALUES (?,?,?,?,?)",
                           events)
            db.executemany("INSERT INTO minutes(minute, app, speaking_s, over_s, loud_events) VALUES (?,?,?,?,?)",
                           minutes)
            db.commit()

    def report(self, days=0):
        with closing(store.connect(self.db_path)) as db:
            return games.report(db, days, today=TODAY, cliptrove=self.ct)

    def test_no_cliptrove(self):
        shutil.rmtree(self.ct)
        r = self.report()
        self.assertFalse(r["cliptrove"]["found"])
        self.assertEqual(r["games"], [])
        self.assertIsNone(r["verdict"])

    def test_copies_game_and_time_only_and_keeps_them(self):
        self.cliptrove([("WARDOGS", "2026-10-26T23:04:32")])
        r = self.report()
        self.assertEqual(r["cliptrove"]["clips"], 1)
        with closing(store.connect(self.db_path)) as db:
            row = dict(db.execute("SELECT * FROM clips").fetchone())
        self.assertEqual(row, {"key": "k0", "game": "WARDOGS", "saved": "2026-10-26T23:04:32"})
        os.remove(os.path.join(self.ct, "library.json"))  # deleted in ClipTrove, or ClipTrove uninstalled
        self.assertEqual(self.report()["cliptrove"]["clips"], 1)

    def test_each_clip_is_checked(self):
        self.cliptrove([("WARDOGS", "2026-10-26T23:04:32"), ("WARDOGS", "2026-10-26T23:33:14"),
                        ("WARDOGS", "2026-10-25T12:00:00")])
        self.history(events=[("2026-10-26T23:03:40", -12.0, 2.0, -29.2, "Wardogs")],
                     minutes=[("2026-10-26T23:03", "Wardogs", 40, 4, 1), ("2026-10-26T23:32", "Wardogs", 30, 0, 0)])
        r = self.report()
        by_time = {c["saved"]: c for c in r["clips"]}
        self.assertTrue(by_time["2026-10-26T23:04:32"]["loud"])
        self.assertEqual(by_time["2026-10-26T23:04:32"]["overDb"], 17.2)
        self.assertFalse(by_time["2026-10-26T23:33:14"]["loud"])
        self.assertNotIn("2026-10-25T12:00:00", by_time)  # Vocalm wasn't listening then: not counted either way
        self.assertEqual((r["totals"]["clipsHeard"], r["totals"]["clipsLoud"]), (2, 1))
        wardogs = r["games"][0]
        self.assertEqual((wardogs["game"], wardogs["loud"], wardogs["overDb"]), ("WARDOGS", 1, 17.2))

    def test_getting_calmer(self):
        clips, events, minutes = [], [], []
        for back in range(1, 42):
            d = TODAY - timedelta(days=back)
            clips.append(("Apex Legends", f"{d}T21:00:00"))
            loud = 1 if back <= 14 else 4           # four loud moments a night before, one lately
            for i in range(loud):
                events.append((f"{d}T20:{30 + i}:00", -14.0, 2.0, -29.2, "Apex Legends"))
            for i in range(20):
                minutes.append((f"{d}T20:{30 + i:02}", "Apex Legends", 45.0, 0.0, 1 if i < loud else 0))
        self.cliptrove(clips)
        self.history(events, minutes)
        r = self.report()
        self.assertEqual(r["verdict"]["metric"], "rate")
        self.assertEqual(r["verdict"]["trend"], "better")
        self.assertEqual(r["games"][0]["verdict"]["trend"], "better")
        self.assertIsNone(r["projection"])  # a sudden drop: a straight line through it would go below zero
        self.assertEqual(self.report(30)["range"]["start"], "2026-09-28")


if __name__ == "__main__":
    unittest.main()
