"""The history: writing, the old script's import, and what the dashboard is told."""
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import closing
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vocalm import store  # noqa: E402
from vocalm.tracker import Bucket  # noqa: E402

TODAY = date(2026, 10, 15)


def session(start, **kw):
    r = {"start": start, "end": start, "duration_s": 0, "speaking_s": 0, "over_s": 0, "loud_events": 0,
         "peak_dbfs": None, "longest_calm_s": 0, "threshold_dbfs": -24.1}
    r.update(kw)
    return r


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="vocalm-test-")
        self.db_path = os.path.join(self.dir, "vocalm.db")
        self.store = store.Store(self.db_path)

    def tearDown(self):
        self.store.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def db(self):
        return closing(store.connect(self.db_path))

    def test_minutes_add_up_across_flushes(self):
        self.store.add_minutes([("2026-10-15T20:01", "VALORANT", Bucket(30, 6, -900, -12, 1))], -24.1)
        self.store.add_minutes([("2026-10-15T20:01", "VALORANT", Bucket(20, 4, -600, -8, 1))], -24.1)
        self.store.flush()
        with self.db() as db:
            row = db.execute("SELECT * FROM minutes").fetchone()
        self.assertEqual((row["speaking_s"], row["over_s"], row["peak_dbfs"], row["loud_events"]), (50, 10, -8, 2))

    def test_session_is_saved_as_it_goes(self):
        sid = self.store.start_session(session("2026-10-15T10:00:00"))
        self.store.save_session(sid, session("2026-10-15T10:00:00", end="2026-10-15T10:30:00", duration_s=1800,
                                             speaking_s=300, loud_events=3))
        self.store.flush()
        with self.db() as db:
            row = db.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
        self.assertEqual((row["end"], row["loud_events"]), ("2026-10-15T10:30:00", 3))

    def test_overview_numbers(self):
        sid = self.store.start_session(session("2026-10-15T19:00:00"))
        for ts, peak, th in (("2026-10-13T21:10:00", -20.0, -34.8), ("2026-10-14T21:10:00", -15.0, -24.1),
                             ("2026-10-15T20:05:00", -9.0, -24.1), ("2026-10-15T20:30:00", -18.0, -24.1)):
            self.store.add_event(sid, {"ts": ts, "peak_dbfs": peak, "duration_s": 2, "threshold_dbfs": th,
                                       "app": "VALORANT"})
        # 30 minutes of talking on the 15th, 2 loud moments: 4 per hour of talking
        self.store.add_minutes([(f"2026-10-15T20:{m:02d}", "VALORANT", Bucket(60, 9, -1800, -9, 1 if m in (5, 25) else 0))
                                for m in range(0, 30)], -24.1)
        self.store.flush()
        with self.db() as db:
            o = store.overview(db, 7, today=TODAY)
            t = store.today_summary(db, today=TODAY)
        self.assertEqual(o["range"], {"start": "2026-10-09", "end": "2026-10-15", "days": 7, "first": "2026-10-13"})
        self.assertEqual(o["totals"]["loud"], 4)
        self.assertEqual(o["totals"]["rate"], 4.0)
        self.assertEqual(o["totals"]["share_loud"], 15.0)
        self.assertEqual(o["totals"]["peak"], -9.0)
        self.assertEqual([d["loud"] for d in o["daily"]], [0, 0, 0, 0, 1, 1, 2])
        self.assertEqual(o["changes"], [{"date": "2026-10-14", "from": -34.8, "to": -24.1}])
        self.assertEqual(o["heatmap"][3][20], 2)      # Thursday the 15th, 8 pm (Monday-first rows)
        self.assertEqual(sum(map(sum, o["heatmap"])), 4)
        self.assertEqual(o["apps"][0]["app"], "VALORANT")
        self.assertEqual(o["loudest"][0]["peak_dbfs"], -9.0)
        self.assertTrue(o["hasMinutes"])
        self.assertEqual((t["totals"]["loud"], len(t["recent"]), t["apps"][0]["talk_s"]), (2, 2, 1800))

    def test_rate_needs_five_minutes_of_talking(self):
        self.store.add_minutes([("2026-10-15T20:00", "X", Bucket(120, 60, -3600, -5, 2))], -24.1)
        self.store.flush()
        with self.db() as db:
            self.assertIsNone(store.overview(db, 1, today=TODAY)["totals"]["rate"])

    def test_empty_history(self):
        with self.db() as db:
            o = store.overview(db, 0, today=TODAY)
        self.assertEqual(o["totals"]["loud"], 0)
        self.assertEqual(o["range"]["days"], 1)
        self.assertFalse(o["hasMinutes"])

    def test_csv_export(self):
        self.store.add_event(1, {"ts": "2026-10-15T20:05:00", "peak_dbfs": -9.0, "duration_s": 2,
                                 "threshold_dbfs": -24.1, "app": "Apex Legends"})
        self.store.flush()
        with self.db() as db:
            text = store.events_csv(db)
        self.assertIn("2026-10-15T20:05:00,-9.0,2.0,-24.1,Apex Legends,live", text)


class RepairTest(unittest.TestCase):
    """A damaged vocalm.db must not leave the dashboard with "Couldn't load your history" for good."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="vocalm-repair-")
        self.db_path = os.path.join(self.dir, "vocalm.db")
        store.init(self.db_path)
        with closing(store.connect(self.db_path)) as db:
            db.executemany("INSERT INTO events(ts, peak_dbfs, duration_s, threshold_dbfs, app) VALUES (?,?,?,?,?)",
                           [(f"2026-10-{1 + i // 200:02}T20:{i % 60:02}:00", -10.0, 2.0, -24.1, "Apex Legends")
                            for i in range(3000)])
            db.executemany("INSERT INTO minutes(minute, app, speaking_s) VALUES (?,?,?)",
                           [(f"2026-10-{1 + i // 1440:02}T{i // 60 % 24:02}:{i % 60:02}", "Apex Legends", 30.0)
                            for i in range(3000)])
            db.commit()
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        with open(self.db_path, "r+b") as f:  # scribble over a few pages in the middle
            f.seek(4096 * 12)
            f.write(b"\xff" * 4096 * 3)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_rebuilds_from_what_is_readable_and_keeps_the_damaged_file(self):
        self.assertFalse(store.healthy(self.db_path))
        history = store.Store(self.db_path)
        history.close()
        self.assertTrue(store.healthy(self.db_path))
        with closing(store.connect(self.db_path)) as db:
            events = db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            minutes = db.execute("SELECT COUNT(*) FROM minutes").fetchone()[0]
            store.overview(db, 0, today=date(2026, 10, 20))  # what failed before
        self.assertGreater(events + minutes, 5000)
        self.assertLess(events + minutes, 6000)
        self.assertTrue(any(".damaged-" in name for name in os.listdir(self.dir)))

    def test_a_healthy_file_is_left_alone(self):
        os.remove(self.db_path)
        store.init(self.db_path)
        store.Store(self.db_path).close()
        self.assertEqual([n for n in os.listdir(self.dir) if ".damaged-" in n], [])


class LegacyImportTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="vocalm-legacy-")
        os.makedirs(os.path.join(self.dir, "old", "logs"))
        with open(os.path.join(self.dir, "old", "logs", "events.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"ts": "2026-08-18T02:07:24", "peak_dbfs": -25.4, "duration_s": 1.76,
                                "threshold_dbfs": -34.8}) + "\n")
            f.write("not json\n\n")
            f.write(json.dumps({"ts": "2026-10-14T23:58:22", "peak_dbfs": -15.5, "duration_s": 1.94,
                                "threshold_dbfs": -24.1}) + "\n")
        # The real file was rewritten by PowerShell at one point, which put a byte-order mark in front of line one
        with open(os.path.join(self.dir, "old", "logs", "sessions.jsonl"), "w", encoding="utf-8-sig") as f:
            f.write(json.dumps({"start": "2026-08-17T14:48:27", "end": "2026-08-17T14:53:23", "duration_s": 296.7,
                                "speaking_s": 20.8, "over_s": 4.9, "loud_events": 0, "longest_calm_streak_s": 296.7,
                                "peak_dbfs": -24.4, "threshold_dbfs": -34.8}) + "\n")
        self.db_path = os.path.join(self.dir, "vocalm.db")
        store.init(self.db_path)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_imports_once_and_skips_junk(self):
        self.assertEqual(store.import_legacy(self.db_path, os.path.join(self.dir, "old")), (2, 1))
        self.assertEqual(store.import_legacy(self.db_path, os.path.join(self.dir, "old")), (0, 0))
        with closing(store.connect(self.db_path)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM events WHERE source='imported'").fetchone()[0], 2)
            row = db.execute("SELECT longest_calm_s FROM sessions").fetchone()
        self.assertEqual(row[0], 296.7)

    def test_missing_folder_is_fine(self):
        self.assertEqual(store.import_legacy(self.db_path, os.path.join(self.dir, "nope")), (0, 0))


if __name__ == "__main__":
    unittest.main()
