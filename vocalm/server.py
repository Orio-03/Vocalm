"""The dashboard: a small web server on this PC only, at http://127.0.0.1:48680.

Only the page itself can use it: every API call carries a random token that only the page knows, and the Host
header must name this PC, so other websites open in your browser can neither read your history nor change your
settings. The live meter streams over Server-Sent Events, about 15 updates a second.
"""
import json
import os
import queue
import secrets
import socket
import sqlite3
import threading
import time
import urllib.parse
import urllib.request
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import APP_NAME, __version__, autostart, games, paths, store
from .audio import device_choices

WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
STATIC = {"/app.js": ("app.js", "text/javascript; charset=utf-8"),
          "/app.css": ("app.css", "text/css; charset=utf-8"),
          "/favicon.svg": ("favicon.svg", "image/svg+xml")}
CLIPTROVE = "http://127.0.0.1:48650"
LIVE_FPS = 15
MAX_BODY = 64 * 1024


def running(port, timeout=1.0):
    """The ping reply if Vocalm is already serving on this port, else None."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/ping", timeout=timeout) as r:
            reply = json.loads(r.read())
            return reply if reply.get("app") == "vocalm" else None
    except (OSError, ValueError):
        return None


def cliptrove_status():
    try:
        with urllib.request.urlopen(CLIPTROVE + "/api/ping", timeout=0.4) as r:
            return {"running": json.loads(r.read()).get("app") == "cliptrove", "url": CLIPTROVE + "/"}
    except (OSError, ValueError):
        return {"running": False, "url": CLIPTROVE + "/"}


def failure_message(e):
    if isinstance(e, sqlite3.DatabaseError) and "malformed" in str(e):
        return "Your history file is damaged. Quit Vocalm and start it again: it repairs the file as it starts"
    return "Something went wrong; it's in vocalm.log"


class Server:
    def __init__(self, core, port):
        self.core = core
        self.port = port
        self.token = secrets.token_urlsafe(18)
        self.httpd = None
        self.stopping = False

    def start(self):
        """Serve on the chosen port, or the next free one if something else has it. Returns the port used."""
        handler = make_handler(self)
        for port in [self.port] + list(range(self.port + 1, self.port + 10)):
            try:
                self.httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
                break
            except OSError:
                continue
        else:
            raise RuntimeError(f"Ports {self.port}-{self.port + 9} are all in use")
        self.httpd.daemon_threads = True
        self.port = self.core.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, name="server", daemon=True).start()
        self.core.quit_hooks.append(self.stop)
        self._write_instance()
        print(f"Dashboard: http://127.0.0.1:{self.port}/")
        return self.port

    def _write_instance(self):
        """Where this copy can be reached, for `Vocalm.exe --quit` (the installer uses it to stop Vocalm cleanly
        before an upgrade). It sits in your own data folder, readable only by programs running as you."""
        try:
            with open(paths.INSTANCE, "w", encoding="utf-8") as f:
                json.dump({"port": self.port, "token": self.token, "pid": os.getpid()}, f)
        except OSError as e:
            print("Couldn't write instance.json:", e)

    def stop(self):
        self.stopping = True
        try:
            os.remove(paths.INSTANCE)
        except OSError:
            pass
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()

    # ---------- what the page gets ----------

    def boot(self):
        cfg = self.core.cfg
        with closing(store.connect(paths.DB)) as db:
            legacy = db.execute("SELECT value FROM meta WHERE key='legacy_imported'").fetchone()
        return {
            "token": self.token, "app": APP_NAME, "version": __version__, "port": self.port,
            "settings": self.core.settings_dict(), "firstRun": not cfg.setup_done,
            "legacy": json.loads(legacy[0]) if legacy else None,
            "dataDir": getattr(paths, "SHOWN_DATA", None) or paths.DATA,
            "autostart": {"supported": autostart.supported(), "enabled": autostart.enabled()},
        }

    def page(self):
        with open(os.path.join(WEB, "index.html"), encoding="utf-8") as f:
            html = f.read()
        blob = json.dumps(self.boot(), ensure_ascii=False).replace("</", "<\\/").replace("<!--", "<\\u0021--")
        return html.replace("__VOCALM_BOOT__", blob).replace("__V__", __version__)

    def action(self, name):
        put = self.core.commands.put
        simple = {"pause": ("pause", True), "resume": ("pause", False), "move": ("move",),
                  "overlay_on": ("overlay", True), "overlay_off": ("overlay", False), "test_nudge": ("test_nudge",),
                  "reset_position": ("reset_position",), "quit": ("quit",)}
        if name in simple:
            put(simple[name])
        elif name == "autostart_on":
            autostart.enable()
        elif name == "autostart_off":
            autostart.disable()
        elif name == "finish_setup":
            self.settings({"setup_done": True})
        elif name == "open_data":
            os.startfile(paths.DATA)
        else:
            raise ValueError(f"Unknown action: {name}")
        return {"ok": True, "autostart": {"supported": autostart.supported(), "enabled": autostart.enabled()}}

    def settings(self, patch):
        if not isinstance(patch, dict):
            raise ValueError("Expected an object of settings")
        reply = queue.Queue(maxsize=1)
        self.core.commands.put(("settings", patch, reply))
        try:
            return reply.get(timeout=5)
        except queue.Empty:
            raise RuntimeError("Vocalm didn't respond in time") from None


def make_handler(server):
    core = server.core

    class Handler(BaseHTTPRequestHandler):
        server_version = f"Vocalm/{__version__}"

        def log_message(self, fmt, *args):  # quiet: the log is for problems, not every request
            pass

        # ---------- helpers ----------

        def _host_ok(self):
            host = (self.headers.get("Host") or "").lower()
            return host in (f"127.0.0.1:{server.port}", f"localhost:{server.port}")

        def _token_ok(self, query):
            given = self.headers.get("X-Vocalm-Token") or (query.get("t") or [""])[0]
            return secrets.compare_digest(given.encode(), server.token.encode())

        def _send(self, status, body, kind="application/json; charset=utf-8", extra=None):
            data = body if isinstance(body, bytes) else body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def _json(self, obj, status=200):
            self._send(status, json.dumps(obj, ensure_ascii=False, default=str))

        def _error(self, status, message):
            self._json({"error": message}, status)

        # ---------- GET ----------

        def do_GET(self):
            url = urllib.parse.urlsplit(self.path)
            query = urllib.parse.parse_qs(url.query)
            path = url.path
            if not self._host_ok():
                return self._error(403, "Open Vocalm at http://127.0.0.1")
            if path == "/api/ping":
                return self._json({"app": "vocalm", "version": __version__})
            if path in ("/", "/index.html"):
                return self._send(200, server.page(), "text/html; charset=utf-8",
                                  {"Content-Security-Policy": "default-src 'self'; style-src 'self' 'unsafe-inline'; "
                                   "script-src 'self'; img-src 'self' data:; object-src 'none'; "
                                   "frame-ancestors 'none'"})
            if path in STATIC:
                name, kind = STATIC[path]
                with open(os.path.join(WEB, name), "rb") as f:
                    return self._send(200, f.read(), kind)
            if not path.startswith("/api/"):
                return self._error(404, "Not found")
            if not self._token_ok(query):
                return self._error(403, "Reload the page")
            try:
                if path == "/api/live":
                    return self._live()
                if path == "/api/state":
                    return self._json(server.boot())
                if path == "/api/today":
                    with closing(store.connect(paths.DB)) as db:
                        return self._json(store.today_summary(db))
                if path == "/api/games":
                    days = int((query.get("days") or ["0"])[0])
                    with closing(store.connect(paths.DB)) as db:
                        return self._json(games.report(db, days))
                if path == "/api/overview":
                    days = int((query.get("days") or ["30"])[0])
                    with closing(store.connect(paths.DB)) as db:
                        return self._json(store.overview(db, max(0, min(days, 3650))))
                if path == "/api/devices":
                    return self._json({"devices": device_choices(), "current": core.meter.label})
                if path == "/api/cliptrove":
                    return self._json(cliptrove_status())
                if path == "/api/export.csv":
                    with closing(store.connect(paths.DB)) as db:
                        body = store.events_csv(db)
                    name = f"vocalm-loud-moments-{store.stamp()}.csv"
                    return self._send(200, body, "text/csv; charset=utf-8",
                                      {"Content-Disposition": f'attachment; filename="{name}"'})
            except (ValueError, LookupError) as e:
                return self._error(400, str(e))
            except Exception as e:
                print("Request failed:", path, repr(e))
                return self._error(500, failure_message(e))
            return self._error(404, "Not found")

        def _live(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            last = None
            try:
                while not server.stopping and not core.quitting:
                    live = core.live
                    if live is not last and live:
                        last = live
                        self.wfile.write(b"data: " + json.dumps(live).encode() + b"\n\n")
                        self.wfile.flush()
                    time.sleep(1.0 / LIVE_FPS)
            except (ConnectionError, socket.timeout, OSError):
                pass  # the tab was closed

        # ---------- POST ----------

        def do_POST(self):
            url = urllib.parse.urlsplit(self.path)
            if not self._host_ok():
                return self._error(403, "Open Vocalm at http://127.0.0.1")
            if not self._token_ok({}):
                return self._error(403, "Reload the page")
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                return self._error(413, "Too much data")
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                return self._error(400, "Expected JSON")
            try:
                if url.path == "/api/settings":
                    return self._json({"settings": server.settings(body)})
                if url.path == "/api/action":
                    return self._json(server.action(str(body.get("action", ""))))
                if url.path == "/api/calibrate":
                    step = str(body.get("action", ""))
                    if step not in ("normal", "loud", "cancel", "reset"):
                        raise ValueError("Unknown calibration step")
                    core.commands.put(("calibrate", step))
                    return self._json({"ok": True})
            except (ValueError, RuntimeError) as e:
                return self._error(400, str(e))
            except Exception as e:
                print("Request failed:", url.path, repr(e))
                return self._error(500, "Something went wrong; it's in vocalm.log")
            return self._error(404, "Not found")

    return Handler
