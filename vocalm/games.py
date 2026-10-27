"""Your voice per game, with ClipTrove's clips.

ClipTrove (github.com/Orio-03/Cliptrove) keeps a list of your clips: which game, and when you saved each one. Vocalm
copies that list (game and time only, never titles or files) into its own history, so it stays even when ClipTrove
is closed or a clip is deleted, and uses it two ways:

  which game   The app in front already names most games ("Wardogs", "League of Legends Client"). The clips name the
               rest: an app that's always in front when you save, say, Counter-Strike 2 clips ("cs2") is that game,
               and a loud moment with no app (the old script never noted one), or in Discord or a browser, counts
               for a game when it's in the middle of a run of that game's clips.
  each clip    Were you loud in the minute and a half before you saved it, and how far over your line.

Then, per game and overall: are you getting calmer, and by how much you go over when you don't.
"""
import bisect
import json
import os
import unicodedata
from datetime import date, datetime, timedelta

CLIP_BEFORE_S = 90       # a clip is the stretch just before you save it; GG's are usually 30 s to 2 min
CLIP_AFTER_S = 5
RUN_GAP_S = 45 * 60      # clips of one game this close together are one evening of playing it
RUN_LEAD_S = 20 * 60     # you were playing a while before the first clip of a run
RUN_TAIL_S = 2 * 60
LEARN_MIN_HITS = 3
LEARN_SHARE = 0.6
LISTEN_TALK_S = 10 * 60   # a clip counts as heard if you talked this close before it (or a loud moment was near)
LISTEN_EVENT_S = 30 * 60
RECENT_DAYS = 14         # "now" for the improvement verdict, against the BEFORE_DAYS before it
BEFORE_DAYS = 28

# Never a game, whatever the clips say: you talk on Discord while playing, but Discord isn't what you played
NOT_GAMES = {"desktop", "discord", "googlechrome", "chrome", "microsoftedge", "firefox", "opera", "brave",
             "windowsexplorer", "explorer", "steam", "steamclientwebhelper", "steelseriesgg", "steelseriesggclient",
             "steelseriessonar", "amdsoftwarehost", "amdsoftware", "claude", "whatsapp", "whatsapproot", "spotify",
             "microsoftphonelink", "vocalm", "cliptrove", "unknowngame", "unsorted"}


def cliptrove_dir():
    env = os.environ.get("CLIPTROVE_DATA")
    if env:
        return os.path.abspath(env)
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
    return os.path.join(base, "Cliptrove")


def norm(name):
    """'Rocket League®', 'ROCKET LEAGUE' and 'RocketLeague' all become 'rocketleague'."""
    text = unicodedata.normalize("NFKD", name or "").lower()
    return "".join(ch for ch in text if ch.isalnum() and ord(ch) < 0x2000)


def display(name):
    """A game's name without trademark signs (or the stand-ins for them that some names arrive with)."""
    cleaned = "".join(ch for ch in name if ch not in "™®©�").strip()
    return " ".join(cleaned.split()) or name


# ---------- copying ClipTrove's clip list ----------

def read_cliptrove(folder=None):
    """[(key, game, saved)] from ClipTrove's library.json, saved as local time 'YYYY-MM-DDTHH:MM:SS'.
    None if ClipTrove isn't on this PC or its file can't be read."""
    path = os.path.join(folder or cliptrove_dir(), "library.json")
    try:
        with open(path, encoding="utf-8") as f:
            snapshot = json.load(f).get("snapshot") or {}
    except (OSError, ValueError, AttributeError):
        return None
    clips = []
    for key, c in snapshot.items():
        try:
            saved = datetime.fromisoformat(c["recorded"])
        except (KeyError, TypeError, ValueError):
            continue
        if saved.tzinfo is not None:
            saved = saved.astimezone().replace(tzinfo=None)
        clips.append((str(key), display(str(c.get("game") or "")), saved.isoformat(timespec="seconds")))
    return clips


def sync(db, folder=None):
    """Bring the clip list up to date from ClipTrove (only when its file changed). Returns the status shown."""
    folder = folder or cliptrove_dir()
    path = os.path.join(folder, "library.json")
    try:
        mtime = str(os.path.getmtime(path))
    except OSError:
        return {"found": False, "folder": folder}
    seen = db.execute("SELECT value FROM meta WHERE key='cliptrove_mtime'").fetchone()
    if not seen or seen[0] != mtime:
        clips = read_cliptrove(folder)
        if clips is None:
            return {"found": True, "readable": False, "folder": folder}
        db.executemany("INSERT INTO clips(key, game, saved) VALUES (?,?,?) ON CONFLICT(key) DO UPDATE SET "
                       "game = excluded.game, saved = excluded.saved", clips)
        db.execute("INSERT OR REPLACE INTO meta VALUES ('cliptrove_mtime', ?)", (mtime,))
        db.execute("INSERT OR REPLACE INTO meta VALUES ('cliptrove_synced', ?)",
                   (datetime.now().isoformat(timespec="seconds"),))
        db.commit()
    synced = db.execute("SELECT value FROM meta WHERE key='cliptrove_synced'").fetchone()
    return {"found": True, "readable": True, "folder": folder, "synced": synced[0] if synced else None}


# ---------- working out the game ----------

def _secs(ts):
    return datetime.fromisoformat(ts).timestamp()


class Games:
    """Which game each moment of your history belongs to."""

    def __init__(self, clips, rows):
        # clips: [(game, saved)]; rows: [(time, app)] from loud moments and talking minutes, used to learn names
        self.names = {}
        for game, _ in clips:
            if norm(game) and norm(game) not in NOT_GAMES:
                self.names.setdefault(norm(game), game)
        self.clips = sorted((_secs(saved), norm(game)) for game, saved in clips if norm(game) in self.names)
        self.clip_times = [t for t, _ in self.clips]
        self.runs = self._runs()
        self.run_starts = [r[0] for r in self.runs]
        self.apps = {}
        self.learned = {}     # app's own name -> game key, for the ones only the clips could name
        self._learn(rows)

    def _runs(self):
        by_game = {}
        for t, g in self.clips:
            by_game.setdefault(g, []).append(t)
        runs = []
        for g, times in by_game.items():
            first = last = times[0]
            for t in times[1:]:
                if t - last > RUN_GAP_S:
                    runs.append((first - RUN_LEAD_S, last + RUN_TAIL_S, g))
                    first = t
                last = t
            runs.append((first - RUN_LEAD_S, last + RUN_TAIL_S, g))
        return sorted(runs)

    def _direct(self, key):
        if key in self.names:
            return key
        best = None
        for g in self.names:  # "League of Legends Client" is League of Legends
            if len(g) >= 4 and (g in key or (len(key) >= 4 and key in g)):
                if best is None or len(g) > len(best):
                    best = g
        return best

    def _learn(self, rows):
        votes, spelled = {}, {}
        for ts, app in rows:
            key = norm(app)
            if not key or key in NOT_GAMES or key in self.apps:
                continue
            found = self._direct(key)
            if found:
                self.apps[key] = found
                continue
            g = self._clip_at(_secs(ts))
            if g:
                spelled.setdefault(key, app)
                votes.setdefault(key, {}).setdefault(g, 0)
                votes[key][g] += 1
        for key, counts in votes.items():
            if key in self.apps:
                continue
            g, n = max(counts.items(), key=lambda kv: kv[1])
            if n >= LEARN_MIN_HITS and n / sum(counts.values()) >= LEARN_SHARE:
                self.apps[key] = g
                self.learned[spelled[key]] = g

    def _clip_at(self, t):
        """The game of a clip that covers time t, if any."""
        i = bisect.bisect_left(self.clip_times, t - CLIP_AFTER_S)
        if i < len(self.clips) and self.clips[i][0] <= t + CLIP_BEFORE_S:
            return self.clips[i][1]
        return None

    def _run_at(self, t):
        """The game you were playing a run of clips of at time t (the nearest clip wins if two overlap)."""
        best, best_d = None, None
        i = bisect.bisect_right(self.run_starts, t)
        for start, end, g in self.runs[max(0, i - 40):i]:
            if start <= t <= end:
                d = min(abs(t - (start + RUN_LEAD_S)), abs(t - (end - RUN_TAIL_S)))
                if best_d is None or d < best_d:
                    best, best_d = g, d
        return best

    def of(self, ts, app):
        """(game key, how it was known: 'app' or 'clips') or (None, None) for time that wasn't gaming."""
        key = norm(app)
        if key in self.apps:
            return self.apps[key], "app"
        found = self._direct(key) if key and key not in NOT_GAMES else None
        if found:
            self.apps[key] = found
            return found, "app"
        g = self._run_at(_secs(ts))
        return (g, "clips") if g else (None, None)


# ---------- the report ----------

def _week(d):
    return (d - timedelta(days=d.weekday())).isoformat()


def _rate(loud, talk_s):
    return round(loud / (talk_s / 3600.0), 1) if talk_s >= 900 else None


def _over(events):
    overs = [e["over"] for e in events if e["over"] is not None]
    return round(sum(overs) / len(overs), 1) if overs else None


def _stats(events, minutes, clips):
    talk = sum(m["talk"] for m in minutes)
    loud_m = sum(m["loud"] for m in minutes)
    over_s = sum(m["over_s"] for m in minutes)
    heard = [c for c in clips if c["heard"]]
    loud_clips = [c for c in heard if c["loud"]]
    return {
        "loud": len(events),
        "overDb": _over(events),
        "worstOverDb": round(max((e["over"] for e in events if e["over"] is not None), default=0), 1) or None,
        "talk_s": round(talk, 1),
        "rate": _rate(loud_m, talk),
        "shareLoud": round(100.0 * over_s / talk, 1) if talk >= 600 else None,
        "clips": len(clips), "clipsHeard": len(heard), "clipsLoud": len(loud_clips),
        "clipShare": round(100.0 * len(loud_clips) / len(heard)) if len(heard) >= 3 else None,
    }


def _verdict(now, before):
    """Compare recent with before on the fairest measure both have enough of. None if neither does."""
    for metric, unit, enough in (("rate", "loud moments per hour of talking", lambda s: s["rate"] is not None),
                                 ("clipShare", "% of your clips with a loud moment", lambda s: s["clipsHeard"] >= 5),
                                 ("overDb", "over your line, on average", lambda s: s["loud"] >= 5)):
        if enough(now) and enough(before) and now[metric] is not None and before[metric] is not None:
            a, b = before[metric], now[metric]
            change = round((b - a) / a * 100) if a else None
            if change is None:
                trend = "same"
            elif change <= -15:
                trend = "better"
            elif change >= 15:
                trend = "worse"
            else:
                trend = "same"
            return {"metric": metric, "unit": unit, "before": a, "now": b, "change": change, "trend": trend}
    return None


def _projection(weeks):
    """A straight line through the weekly loud-per-hour figures (weighted by how much you talked), four weeks on."""
    pts = [(i, w["rate"], w["talk_s"]) for i, w in enumerate(weeks) if w["rate"] is not None][-8:]
    if len(pts) < 3:
        return None
    sw = sum(p[2] for p in pts)
    mx = sum(p[0] * p[2] for p in pts) / sw
    my = sum(p[1] * p[2] for p in pts) / sw
    var = sum(p[2] * (p[0] - mx) ** 2 for p in pts)
    if not var:
        return None
    slope = sum(p[2] * (p[0] - mx) * (p[1] - my) for p in pts) / var
    last = len(weeks) - 1
    now, later = my + slope * (last - mx), my + slope * (last + 4 - mx)
    if now <= 0 or not 0.4 * now <= later <= 2.5 * now:
        return None  # a straight line that far out would say something silly (like never loud again)
    return {"perWeek": round(slope, 2), "now": round(now, 1), "inFourWeeks": round(later, 1)}


def _listening(t, day, minute_times, minute_days, event_times):
    """Was Vocalm hearing you around time t? Since v0.1 it notes every minute you talk, so: did you talk within ten
    minutes of it. Before that (the old script's loud moments only): a loud moment within half an hour."""
    i = bisect.bisect_left(minute_times, t - LISTEN_TALK_S)
    if i < len(minute_times) and minute_times[i] <= t + 60:
        return True
    if day in minute_days:
        return False
    i = bisect.bisect_left(event_times, t - LISTEN_EVENT_S)
    return i < len(event_times) and event_times[i] <= t + LISTEN_EVENT_S


def report(db, days=0, today=None, cliptrove=None):
    """Everything the Games page shows. days=0 means all of your history."""
    today = today or date.today()
    status = sync(db, cliptrove)
    clip_rows = [(r["game"], r["saved"]) for r in db.execute("SELECT game, saved FROM clips ORDER BY saved")]
    events = [dict(r) for r in db.execute(
        "SELECT ts, peak_dbfs, threshold_dbfs, duration_s, COALESCE(app, '') app FROM events ORDER BY ts")]
    minutes = [dict(r) for r in db.execute(
        "SELECT minute, app, speaking_s talk, over_s, loud_events loud FROM minutes ORDER BY minute")]
    games = Games(clip_rows, [(e["ts"], e["app"]) for e in events if e["app"]] +
                  [(m["minute"] + ":30", m["app"]) for m in minutes if m["app"]])

    start = (today - timedelta(days=days - 1)).isoformat() if days and days > 0 else "0000"
    end = today.isoformat() + "T99"
    minute_times = [_secs(m["minute"] + ":30") for m in minutes]
    minute_days = {m["minute"][:10] for m in minutes}

    for e in events:
        e["game"], e["how"] = games.of(e["ts"], e["app"])
        e["over"] = round(e["peak_dbfs"] - e["threshold_dbfs"], 1) \
            if e["peak_dbfs"] is not None and e["threshold_dbfs"] is not None else None
    for m in minutes:
        m["game"], m["how"] = games.of(m["minute"] + ":30", m["app"])
    event_times = [_secs(e["ts"]) for e in events]
    clips = []
    for game, saved in clip_rows:
        key = norm(game)
        if key not in games.names:
            continue
        t = _secs(saved)
        lo = bisect.bisect_left(event_times, t - CLIP_BEFORE_S)
        hi = bisect.bisect_right(event_times, t + CLIP_AFTER_S)
        near = [events[i] for i in range(lo, hi)]
        heard = bool(near) or _listening(t, saved[:10], minute_times, minute_days, event_times)
        overs = [e["over"] for e in near if e["over"] is not None]
        clips.append({"game": key, "saved": saved, "heard": heard, "loud": bool(near),
                      "overDb": round(max(overs), 1) if overs else None,
                      "peak": max((e["peak_dbfs"] for e in near if e["peak_dbfs"] is not None), default=None)})

    def within(rows, field, lo, hi):
        return [r for r in rows if lo <= r[field] < hi]

    ev_g = [e for e in events if e["game"]]
    mi_g = [m for m in minutes if m["game"]]
    in_range = (within(ev_g, "ts", start, end), within(mi_g, "minute", start, end), within(clips, "saved", start, end))
    recent_lo = (today - timedelta(days=RECENT_DAYS - 1)).isoformat()
    before_lo = (today - timedelta(days=RECENT_DAYS + BEFORE_DAYS - 1)).isoformat()

    def periods(ev, mi, cl):
        now = _stats(within(ev, "ts", recent_lo, end), within(mi, "minute", recent_lo, end),
                     within(cl, "saved", recent_lo, end))
        before = _stats(within(ev, "ts", before_lo, recent_lo), within(mi, "minute", before_lo, recent_lo),
                        within(cl, "saved", before_lo, recent_lo))
        return now, before

    def weekly(ev, mi, cl):
        weeks = {}

        def slot(ts):
            return weeks.setdefault(_week(date.fromisoformat(ts[:10])), {"ev": [], "mi": [], "cl": []})
        for e in within(ev, "ts", start, end):
            slot(e["ts"])["ev"].append(e)
        for m in within(mi, "minute", start, end):
            slot(m["minute"])["mi"].append(m)
        for c in within(cl, "saved", start, end):
            slot(c["saved"])["cl"].append(c)
        listened = [k for k, w in weeks.items() if w["ev"] or w["mi"]]
        if not listened:
            return []
        first = date.fromisoformat(min(listened))  # clips from before Vocalm was listening say nothing
        out, d = [], first
        while d <= today:
            w = weeks.get(d.isoformat(), {"ev": [], "mi": [], "cl": []})
            out.append(dict(_stats(w["ev"], w["mi"], w["cl"]), week=d.isoformat()))
            d += timedelta(days=7)
        return out

    per_game = []
    for key, name in games.names.items():
        ev = [e for e in in_range[0] if e["game"] == key]
        mi = [m for m in in_range[1] if m["game"] == key]
        cl = [c for c in in_range[2] if c["game"] == key]
        if not ev and not mi and not cl:
            continue
        stats = _stats(ev, mi, cl)
        if not stats["loud"] and stats["talk_s"] < 60 and not stats["clipsHeard"]:
            continue
        now, before = periods([e for e in ev_g if e["game"] == key], [m for m in mi_g if m["game"] == key],
                              [c for c in clips if c["game"] == key])
        apps = sorted({e["app"] for e in ev if e["how"] == "app"} | {m["app"] for m in mi if m["how"] == "app"})
        per_game.append(dict(stats, game=name, key=key, apps=apps, verdict=_verdict(now, before),
                             byClips=sum(1 for e in ev if e["how"] == "clips"),
                             lastPlayed=max([c["saved"] for c in cl] + [e["ts"] for e in ev] +
                                            [m["minute"] for m in mi])[:10]))
    per_game.sort(key=lambda g: (-(g["loud"]), -g["talk_s"]))

    now, before = periods(ev_g, mi_g, clips)
    weeks = weekly(ev_g, mi_g, clips)
    names = games.names
    recent_clips = [dict(c, game=names[c["game"]]) for c in sorted(in_range[2], key=lambda c: c["saved"],
                                                                     reverse=True) if c["heard"]][:12]
    loudest = sorted((e for e in in_range[0] if e["over"] is not None), key=lambda e: -e["over"])[:8]
    return {
        "cliptrove": dict(status, clips=len(clip_rows)),
        "range": {"days": days, "start": None if start == "0000" else start, "end": today.isoformat()},
        "totals": _stats(*in_range),
        "verdict": _verdict(now, before),
        "recent": now, "before": before,
        "periods": {"recentDays": RECENT_DAYS, "beforeDays": BEFORE_DAYS},
        "weeks": weeks,
        "projection": _projection(weeks),
        "games": per_game,
        "learned": [{"app": a, "game": names[g]} for a, g in sorted(games.learned.items())],
        "clips": recent_clips,
        "loudest": [{"ts": e["ts"], "game": names[e["game"]], "overDb": e["over"], "peak": e["peak_dbfs"],
                     "how": e["how"], "app": e["app"]} for e in loudest],
        "unplaced": sum(1 for e in events if not e["game"] and start <= e["ts"] < end),
    }
