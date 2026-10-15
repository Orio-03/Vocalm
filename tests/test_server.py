"""The dashboard server, against a stand-in for the overlay core: the page, the API, and who's allowed in."""
import http.client
import json
import os
import queue
import shutil
import sys
import tempfile
import threading
import unittest
from dataclasses import asdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vocalm import paths, settings, store  # noqa: E402
from vocalm.server import Server  # noqa: E402


class FakeMeter:
    label = "Test mic (WASAPI, 48000 Hz)"


class FakeCore:
    """Answers commands the way the real one does on the main thread."""

    def __init__(self):
        self.cfg = settings.Settings()
        self.commands = queue.Queue()
        self.quit_hooks = []
        self.quitting = False
        self.port = 0
        self.meter = FakeMeter()
        self.handled = []
        self.live = {"t": 1, "db": -30.0, "zone": "calm"}
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while True:
            cmd = self.commands.get()
            self.handled.append(cmd[:2] if cmd[0] != "settings" else ("settings",))
            if cmd[0] == "settings":
                self.cfg = settings.normalize(cmd[1], base=self.cfg)
                cmd[2].put(asdict(self.cfg))

    def settings_dict(self):
        return asdict(self.cfg)


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp(prefix="vocalm-server-")
        paths.configure(cls.dir)
        store.init(paths.DB)
        cls.core = FakeCore()
        cls.server = Server(cls.core, 48791)
        cls.port = cls.server.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()
        paths.configure()
        shutil.rmtree(cls.dir, ignore_errors=True)

    def request(self, method, path, body=None, token=True, host=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = {"Host": host or f"127.0.0.1:{self.port}"}
        if token:
            headers["X-Vocalm-Token"] = self.server.token
        data = None
        if body is not None:
            data = json.dumps(body)
            headers["Content-Type"] = "application/json"
        conn.request(method, path, body=data, headers=headers)
        r = conn.getresponse()
        payload = r.read()
        conn.close()
        return r.status, payload

    def test_ping_needs_no_token(self):
        status, body = self.request("GET", "/api/ping", token=False)
        self.assertEqual((status, json.loads(body)["app"]), (200, "vocalm"))

    def test_page_has_its_data_and_token(self):
        status, body = self.request("GET", "/", token=False)
        page = body.decode()
        self.assertEqual(status, 200)
        self.assertIn("<title>Vocalm</title>", page)
        self.assertNotIn("__VOCALM_BOOT__", page)
        self.assertIn(self.server.token, page)
        for asset in ("/app.js", "/app.css", "/favicon.svg"):
            self.assertEqual(self.request("GET", asset, token=False)[0], 200, asset)

    def test_other_websites_are_turned_away(self):
        self.assertEqual(self.request("GET", "/api/overview", token=False)[0], 403)            # no token
        self.assertEqual(self.request("GET", "/", host="evil.example:48791")[0], 403)           # DNS rebinding
        self.assertEqual(self.request("POST", "/api/action", {"action": "quit"}, token=False)[0], 403)
        self.assertNotIn(("quit",), self.core.handled)

    def test_overview_and_today(self):
        status, body = self.request("GET", "/api/overview?days=30")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["range"]["days"], 30)
        self.assertEqual(self.request("GET", "/api/today")[0], 200)

    def test_settings_are_merged_and_checked(self):
        status, body = self.request("POST", "/api/settings", {"threshold_dbfs": -20, "bar_width": 9999})
        s = json.loads(body)["settings"]
        self.assertEqual((status, s["threshold_dbfs"], s["bar_width"]), (200, -20.0, 600))

    def test_actions(self):
        self.assertEqual(self.request("POST", "/api/action", {"action": "pause"})[0], 200)
        self.assertEqual(self.request("POST", "/api/action", {"action": "format_c"})[0], 400)
        self.assertEqual(self.request("POST", "/api/calibrate", {"action": "normal"})[0], 200)
        self.assertEqual(self.request("POST", "/api/calibrate", {"action": "explode"})[0], 400)

    def test_bad_json(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("POST", "/api/settings", body="{nope", headers={
            "Host": f"127.0.0.1:{self.port}", "X-Vocalm-Token": self.server.token, "Content-Type": "application/json"})
        self.assertEqual(conn.getresponse().status, 400)
        conn.close()

    def test_live_stream_sends_the_snapshot(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("GET", f"/api/live?t={self.server.token}", headers={"Host": f"127.0.0.1:{self.port}"})
        r = conn.getresponse()
        self.assertEqual(r.getheader("Content-Type"), "text/event-stream")
        line = r.fp.readline().decode()
        self.assertTrue(line.startswith("data: "))
        self.assertEqual(json.loads(line[6:])["db"], -30.0)
        conn.close()


if __name__ == "__main__":
    unittest.main()
