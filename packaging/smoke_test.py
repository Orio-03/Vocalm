"""Start a built Vocalm.exe on a spare port with an empty data folder, check the dashboard works, then ask it to quit.

    python packaging/smoke_test.py dist/Vocalm/Vocalm.exe

It runs beside your own Vocalm: a copy with its own data folder gets its own one-copy-only lock.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

PORT = 48711


def get(path, token=None, timeout=10):
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", headers={"X-Vocalm-Token": token} if token else {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read()


def main(exe):
    data = tempfile.mkdtemp(prefix="vocalm-smoke-")
    proc = subprocess.Popen([exe, "--background", "--port", str(PORT), "--data-dir", data])
    try:
        for _ in range(60):
            try:
                ping = json.loads(get("/api/ping", timeout=2)[1])
                break
            except OSError:
                time.sleep(1)
        else:
            raise SystemExit("Vocalm didn't start")
        assert ping["app"] == "vocalm", ping
        status, html = get("/")
        page = html.decode("utf-8")
        assert status == 200 and "<title>Vocalm</title>" in page and "__VOCALM_BOOT__" not in page, page[:300]
        for asset in ("/app.js", "/app.css", "/favicon.svg"):
            assert get(asset)[0] == 200, asset
        with open(os.path.join(data, "instance.json"), encoding="utf-8") as f:
            token = json.load(f)["token"]
        overview = json.loads(get("/api/overview?days=30", token)[1])
        assert overview["range"]["days"] == 30, overview["range"]
        devices = json.loads(get("/api/devices", token)[1])["devices"]
        internal = os.path.join(os.path.dirname(exe), "_internal")
        for needed in ("_tkinter.pyd", "_sounddevice_data"):
            assert os.path.exists(os.path.join(internal, needed)), f"{needed} wasn't bundled"

        quit_ = subprocess.run([exe, "--quit", "--data-dir", data], timeout=30)
        assert quit_.returncode == 0, "--quit failed"
        proc.wait(timeout=15)
        with open(os.path.join(data, "vocalm.log"), encoding="utf-8") as f:
            log = f.read()
        assert "Vocalm stopped" in log, log
        print(f"Smoke test passed: {ping}, {len(devices)} microphones seen, quit cleanly")
    finally:
        if proc.poll() is None:
            proc.kill()
        shutil.rmtree(data, ignore_errors=True)


if __name__ == "__main__":
    main(os.path.abspath(sys.argv[1]))
