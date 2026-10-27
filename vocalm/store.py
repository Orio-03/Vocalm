"""Your history, in vocalm.db (SQLite) in the data folder.

  events    one row per loud moment: when, how loud, how long, the line at the time, the app in front
  minutes   per minute and app: talking time, time over the line, average and peak level, loud moments
  sessions  one row per run of Vocalm, saved every 30 s so a shutdown or crash loses almost nothing

Writes go through one background thread, so a slow disk can never make the bar stutter. The dashboard reads
with its own short-lived connections; WAL mode lets both happen at once.
"""
import csv
import io
import json
import os
import queue
import sqlite3
import threading
import time
from contextlib import closing
from datetime import date, timedelta

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS sessions(
    id INTEGER PRIMARY KEY, start TEXT NOT NULL, end TEXT, duration_s REAL DEFAULT 0, speaking_s REAL DEFAULT 0,
    over_s REAL DEFAULT 0, loud_events INTEGER DEFAULT 0, peak_dbfs REAL, longest_calm_s REAL DEFAULT 0,
    threshold_dbfs REAL, source TEXT DEFAULT 'live');
CREATE TABLE IF NOT EXISTS events(
    id INTEGER PRIMARY KEY, session_id INTEGER, ts TEXT NOT NULL, peak_dbfs REAL, duration_s REAL,
    threshold_dbfs REAL, app TEXT, source TEXT DEFAULT 'live');
CREATE INDEX IF NOT EXISTS events_ts ON events(ts);
CREATE TABLE IF NOT EXISTS minutes(
    minute TEXT NOT NULL, app TEXT NOT NULL DEFAULT '', speaking_s REAL DEFAULT 0, over_s REAL DEFAULT 0,
    db_sum REAL DEFAULT 0, peak_dbfs REAL, loud_events INTEGER DEFAULT 0, threshold_dbfs REAL,
    PRIMARY KEY(minute, app));
"""


def connect(path):
    db = sqlite3.connect(path, timeout=15)
    db.row_factory = sqlite3.Row
    return db


def healthy(path):
    """True unless SQLite finds damage in the file (a missing file is fine: it gets created)."""
    if not os.path.exists(path):
        return True
    try:
        with closing(sqlite3.connect(path, timeout=15)) as db:
            return db.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    except sqlite3.DatabaseError:
        return False


def repair(path):
    """Move a damaged history aside and copy everything still readable into a fresh one.

    Returns (where the damaged file went, rows saved per table). The damaged file is kept, never deleted."""
    base, ext = os.path.splitext(path)
    damaged = f"{base}.damaged-{time.strftime('%Y%m%d-%H%M%S')}{ext}"
    for suffix in ("", "-wal", "-shm"):  # together, so the moved file still reads its own write-ahead log
        if os.path.exists(path + suffix):
            os.replace(path + suffix, damaged + suffix)
    init(path)
    saved = {}
    with closing(sqlite3.connect(damaged, timeout=15)) as old, closing(connect(path)) as new:
        for table in ("meta", "sessions", "events", "minutes", "clips"):
            try:
                have = {r[1] for r in old.execute(f"PRAGMA table_info({table})")}
            except sqlite3.DatabaseError:
                have = set()
            cols = [r[1] for r in new.execute(f"PRAGMA table_info({table})") if r[1] in have]
            if not cols:
                continue
            rows = _salvage(old, table, cols)
            new.executemany(f"INSERT OR IGNORE INTO {table}({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                            rows)
            saved[table] = len(rows)
        new.commit()
    return damaged, saved


def _salvage(db, table, cols):
    """Every row that can still be read, found by rowid ranges: a range that hits a damaged page is split in half
    until only the unreadable rows are left out."""
    query = f"SELECT {','.join(cols)} FROM {table} WHERE rowid BETWEEN ? AND ?"
    try:
        top = min(db.execute(f"SELECT MAX(rowid) FROM {table}").fetchone()[0] or 0, 1 << 24)
    except sqlite3.DatabaseError:
        top = 1 << 24
    rows = []

    def take(lo, hi):
        try:
            rows.extend(tuple(r) for r in db.execute(query, (lo, hi)).fetchall())
        except sqlite3.DatabaseError:
            if lo < hi:
                mid = (lo + hi) // 2
                take(lo, mid)
                take(mid + 1, hi)

    for lo in range(1, top + 1, 4096):
        take(lo, min(top, lo + 4095))
    return rows


def init(path):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with closing(connect(path)) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript(SCHEMA)
        db.execute("INSERT OR IGNORE INTO meta VALUES ('schema', '1')")
        db.commit()


class Store:
    """The writer. Every method queues its work and returns at once (except start_session, which needs the id)."""

    def __init__(self, path):
        self.path = path
        if not healthy(path):
            damaged, saved = repair(path)
            print(f"The history file was damaged. Rebuilt it from everything still readable "
                  f"({', '.join(f'{n} {t}' for t, n in saved.items())}); the damaged file is kept as {damaged}")
        init(path)
        self._jobs = queue.Queue()
        self._thread = threading.Thread(target=self._run, name="store", daemon=True)
        self._thread.start()

    def _run(self):
        db = connect(self.path)
        while True:
            job = self._jobs.get()
            if job is None:
                break
            fn, args, done = job
            try:
                result = fn(db, *args)
                db.commit()
            except Exception as e:  # keep going: one bad write mustn't stop the history
                print(f"Couldn't save to the history: {e!r}")
                db.rollback()
                result = None
            if done is not None:
                done.put(result)
        db.close()

    def _submit(self, fn, *args, wait=False):
        done = queue.Queue(maxsize=1) if wait else None
        self._jobs.put((fn, args, done))
        return done.get(timeout=30) if wait else None

    def start_session(self, record):
        return self._submit(_insert_session, record, wait=True)

    def save_session(self, sid, record):
        self._submit(_update_session, sid, record)

    def add_event(self, sid, record):
        self._submit(_insert_event, sid, record)

    def add_minutes(self, rows, threshold):
        if rows:
            self._submit(_upsert_minutes, rows, threshold)

    def flush(self):
        """Wait until everything queued so far is on disk."""
        self._submit(lambda db: None, wait=True)

    def close(self):
        self._jobs.put(None)
        self._thread.join(timeout=10)


def _insert_session(db, r):
    cur = db.execute("INSERT INTO sessions(start, end, duration_s, speaking_s, over_s, loud_events, peak_dbfs, "
                     "longest_calm_s, threshold_dbfs) VALUES (?,?,?,?,?,?,?,?,?)",
                     (r["start"], r["end"], r["duration_s"], r["speaking_s"], r["over_s"], r["loud_events"],
                      r["peak_dbfs"], r["longest_calm_s"], r["threshold_dbfs"]))
    return cur.lastrowid


def _update_session(db, sid, r):
    db.execute("UPDATE sessions SET end=?, duration_s=?, speaking_s=?, over_s=?, loud_events=?, peak_dbfs=?, "
               "longest_calm_s=?, threshold_dbfs=? WHERE id=?",
               (r["end"], r["duration_s"], r["speaking_s"], r["over_s"], r["loud_events"], r["peak_dbfs"],
                r["longest_calm_s"], r["threshold_dbfs"], sid))


def _insert_event(db, sid, r):
    db.execute("INSERT INTO events(session_id, ts, peak_dbfs, duration_s, threshold_dbfs, app) VALUES (?,?,?,?,?,?)",
               (sid, r["ts"], r["peak_dbfs"], r["duration_s"], r["threshold_dbfs"], r.get("app") or None))


def _upsert_minutes(db, rows, threshold):
    db.executemany(
        "INSERT INTO minutes(minute, app, speaking_s, over_s, db_sum, peak_dbfs, loud_events, threshold_dbfs) "
        "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(minute, app) DO UPDATE SET "
        "speaking_s = speaking_s + excluded.speaking_s, over_s = over_s + excluded.over_s, "
        "db_sum = db_sum + excluded.db_sum, peak_dbfs = MAX(COALESCE(peak_dbfs, -120), excluded.peak_dbfs), "
        "loud_events = loud_events + excluded.loud_events, threshold_dbfs = excluded.threshold_dbfs",
        [(m, app, b.speaking_s, b.over_s, b.db_sum, b.peak_dbfs, b.loud_events, threshold) for m, app, b in rows])


# ---------- the old script's logs ----------

def import_legacy(path, folder):
    """Copy the old script's logs/events.jsonl and logs/sessions.jsonl in, once. Returns (events, sessions)."""
    with closing(connect(path)) as db:
        if db.execute("SELECT 1 FROM meta WHERE key='legacy_imported'").fetchone():
            return 0, 0
        counts = []
        for name, insert in (("events.jsonl", _legacy_event), ("sessions.jsonl", _legacy_session)):
            rows = 0
            try:
                # utf-8-sig: a file saved by PowerShell starts with a byte-order mark that would spoil line one
                with open(os.path.join(folder, "logs", name), encoding="utf-8-sig") as f:
                    for line in f:
                        try:
                            record = json.loads(line)
                        except ValueError:
                            continue
                        if isinstance(record, dict) and insert(db, record):
                            rows += 1
            except OSError:
                pass
            counts.append(rows)
        db.execute("INSERT OR REPLACE INTO meta VALUES ('legacy_imported', ?)",
                   (json.dumps({"from": folder, "events": counts[0], "sessions": counts[1]}),))
        db.commit()
        return counts[0], counts[1]


def _legacy_event(db, r):
    if not isinstance(r.get("ts"), str):
        return False
    db.execute("INSERT INTO events(ts, peak_dbfs, duration_s, threshold_dbfs, source) VALUES (?,?,?,?, 'imported')",
               (r["ts"][:19], r.get("peak_dbfs"), r.get("duration_s"), r.get("threshold_dbfs")))
    return True


def _legacy_session(db, r):
    if not isinstance(r.get("start"), str):
        return False
    db.execute("INSERT INTO sessions(start, end, duration_s, speaking_s, over_s, loud_events, peak_dbfs, "
               "longest_calm_s, threshold_dbfs, source) VALUES (?,?,?,?,?,?,?,?,?, 'imported')",
               (r["start"], r.get("end"), r.get("duration_s") or 0, r.get("speaking_s") or 0, r.get("over_s") or 0,
                r.get("loud_events") or 0, r.get("peak_dbfs"), r.get("longest_calm_streak_s") or 0,
                r.get("threshold_dbfs")))
    return True


# ---------- what the dashboard asks for ----------

def _mean(db_sum, seconds):
    return round(db_sum / seconds, 1) if seconds and seconds > 0 else None


def _rate(loud, seconds):
    """Loud moments per hour of talking. Needs 5 minutes of talking to mean anything."""
    return round(loud / (seconds / 3600.0), 1) if seconds and seconds >= 300 else None


def _totals(db, start, end):
    """Totals for dates start..end (inclusive), as 'YYYY-MM-DD' strings."""
    lo, hi = start, end + "T99"
    e = db.execute("SELECT COUNT(*) n, MAX(peak_dbfs) peak FROM events WHERE ts >= ? AND ts < ?", (lo, hi)).fetchone()
    m = db.execute("SELECT SUM(speaking_s) talk, SUM(over_s) over, SUM(db_sum) dbs, SUM(loud_events) loud, "
                   "MAX(peak_dbfs) peak FROM minutes WHERE minute >= ? AND minute < ?", (lo, hi)).fetchone()
    t = db.execute("SELECT MIN(threshold_dbfs) lo, MAX(threshold_dbfs) hi FROM events WHERE ts >= ? AND ts < ?",
                   (lo, hi)).fetchone()
    talk = m["talk"] or 0.0
    return {
        "loud": e["n"],
        "peak": max([p for p in (e["peak"], m["peak"]) if p is not None], default=None),
        "talk_s": round(talk, 1),
        "over_s": round(m["over"] or 0.0, 1),
        "share_loud": round(100.0 * (m["over"] or 0) / talk, 1) if talk >= 60 else None,
        "mean_db": _mean(m["dbs"], talk),
        "rate": _rate(m["loud"] or 0, talk),  # from the minutes table, so loud and talking come from the same days
        "thresholds": [t["lo"], t["hi"]] if t["lo"] is not None else [],
    }


def first_day(db):
    row = db.execute("SELECT MIN(d) FROM (SELECT MIN(substr(ts,1,10)) d FROM events "
                     "UNION ALL SELECT MIN(substr(minute,1,10)) FROM minutes)").fetchone()
    return row[0]


def overview(db, days=30, today=None):
    """Everything the Insights page shows for the last `days` days (0 = all of it)."""
    today = today or date.today()
    first = first_day(db)
    if days and days > 0:
        start = today - timedelta(days=days - 1)
    else:
        start = date.fromisoformat(first) if first else today
    end = today
    lo, hi = start.isoformat(), end.isoformat() + "T99"
    span = (end - start).days + 1

    daily = {}
    for d in (start + timedelta(n) for n in range(span)):
        daily[d.isoformat()] = {"date": d.isoformat(), "loud": 0, "talk_s": 0.0, "over_s": 0.0, "mean_db": None,
                                "peak": None, "rate": None, "threshold": None}
    for r in db.execute("SELECT substr(ts,1,10) d, COUNT(*) n, MAX(peak_dbfs) peak, MAX(threshold_dbfs) th "
                        "FROM events WHERE ts >= ? AND ts < ? GROUP BY d", (lo, hi)):
        if r["d"] in daily:
            daily[r["d"]].update(loud=r["n"], peak=r["peak"], threshold=r["th"])
    for r in db.execute("SELECT substr(minute,1,10) d, SUM(speaking_s) talk, SUM(over_s) over, SUM(db_sum) dbs, "
                        "SUM(loud_events) loud, MAX(peak_dbfs) peak FROM minutes WHERE minute >= ? AND minute < ? "
                        "GROUP BY d", (lo, hi)):
        if r["d"] in daily:
            row = daily[r["d"]]
            row.update(talk_s=round(r["talk"] or 0, 1), over_s=round(r["over"] or 0, 1),
                       mean_db=_mean(r["dbs"], r["talk"]), rate=_rate(r["loud"] or 0, r["talk"]))
            if r["peak"] is not None:
                row["peak"] = max(row["peak"] if row["peak"] is not None else -120, r["peak"])

    # When the line moved: counts either side aren't comparable, so the charts mark it
    changes, previous = [], None
    for r in db.execute("SELECT substr(ts,1,10) d, threshold_dbfs th FROM events WHERE threshold_dbfs IS NOT NULL "
                        "ORDER BY ts"):
        if previous is not None and r["th"] != previous and start.isoformat() <= r["d"] <= end.isoformat():
            if not changes or changes[-1]["date"] != r["d"]:
                changes.append({"date": r["d"], "from": previous, "to": r["th"]})
            else:
                changes[-1]["to"] = r["th"]
        previous = r["th"]

    heat = [[0] * 24 for _ in range(7)]  # Monday first
    for r in db.execute("SELECT CAST(strftime('%w', ts) AS INTEGER) wd, CAST(strftime('%H', ts) AS INTEGER) h, "
                        "COUNT(*) n FROM events WHERE ts >= ? AND ts < ? GROUP BY wd, h", (lo, hi)):
        if r["wd"] is not None and r["h"] is not None:
            heat[(r["wd"] + 6) % 7][r["h"]] = r["n"]

    apps = []
    for r in db.execute("SELECT app, SUM(speaking_s) talk, SUM(over_s) over, SUM(db_sum) dbs, MAX(peak_dbfs) peak, "
                        "SUM(loud_events) loud FROM minutes WHERE minute >= ? AND minute < ? AND app != '' "
                        "GROUP BY app HAVING talk >= 60 ORDER BY talk DESC LIMIT 12", (lo, hi)):
        apps.append({"app": r["app"], "talk_s": round(r["talk"], 1), "over_s": round(r["over"] or 0, 1),
                     "share_loud": round(100.0 * (r["over"] or 0) / r["talk"], 1), "mean_db": _mean(r["dbs"], r["talk"]),
                     "peak": r["peak"], "loud": r["loud"] or 0, "rate": _rate(r["loud"] or 0, r["talk"])})

    loudest = [dict(r) for r in db.execute(
        "SELECT ts, peak_dbfs, duration_s, app, threshold_dbfs FROM events WHERE ts >= ? AND ts < ? "
        "ORDER BY peak_dbfs DESC, ts DESC LIMIT 8", (lo, hi))]

    previous_period = _totals(db, (start - timedelta(span)).isoformat(), (start - timedelta(1)).isoformat())
    week = _totals(db, (today - timedelta(6)).isoformat(), today.isoformat())
    last_week = _totals(db, (today - timedelta(13)).isoformat(), (today - timedelta(7)).isoformat())

    sessions = [dict(r) for r in db.execute(
        "SELECT id, start, end, duration_s, speaking_s, loud_events, peak_dbfs, source FROM sessions "
        "WHERE start < ? AND COALESCE(end, start) >= ? ORDER BY start DESC LIMIT 15", (hi, lo))]

    return {
        "range": {"start": start.isoformat(), "end": end.isoformat(), "days": span, "first": first},
        "totals": _totals(db, start.isoformat(), end.isoformat()),
        "previous": previous_period,
        "week": week, "lastWeek": last_week,
        "daily": list(daily.values()),
        "changes": changes,
        "heatmap": heat,
        "apps": apps,
        "loudest": loudest,
        "sessions": sessions,
        "hasMinutes": bool(db.execute("SELECT 1 FROM minutes LIMIT 1").fetchone()),
    }


def today_summary(db, today=None):
    """The Live page's 'today' cards and list."""
    d = (today or date.today()).isoformat()
    totals = _totals(db, d, d)
    apps = [{"app": r["app"], "talk_s": round(r["talk"], 1), "loud": r["loud"] or 0}
            for r in db.execute("SELECT app, SUM(speaking_s) talk, SUM(loud_events) loud FROM minutes "
                                "WHERE minute >= ? AND minute < ? AND app != '' GROUP BY app ORDER BY talk DESC "
                                "LIMIT 5", (d, d + "T99"))]
    recent = [dict(r) for r in db.execute("SELECT ts, peak_dbfs, duration_s, app FROM events WHERE ts >= ? "
                                          "ORDER BY ts DESC LIMIT 12", (d,))]
    return {"date": d, "totals": totals, "apps": apps, "recent": recent}


def events_csv(db):
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["time", "peak_dbfs", "duration_s", "threshold_dbfs", "app", "source"])
    for r in db.execute("SELECT ts, peak_dbfs, duration_s, threshold_dbfs, app, source FROM events ORDER BY ts"):
        w.writerow(list(r))
    return out.getvalue()


def stamp():
    return time.strftime("%Y-%m-%d")
