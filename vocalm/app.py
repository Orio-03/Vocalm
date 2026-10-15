"""Starting Vocalm: Vocalm.exe, or python -m vocalm, or double-click Vocalm.pyw."""
import argparse
import hashlib
import json
import os
import sys
import threading
import time
import traceback
import urllib.request
import webbrowser

from . import APP_NAME, __version__, paths


def _log_to_file():
    """No console (the .exe, pythonw, start with Windows): keep a small log in the data folder instead."""
    os.makedirs(paths.DATA, exist_ok=True)
    try:
        if os.path.getsize(paths.LOG) > 1 << 20:
            os.remove(paths.LOG)
    except OSError:
        pass
    sys.stdout = sys.stderr = open(paths.LOG, "a", encoding="utf-8", buffering=1, errors="replace")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="vocalm", description=f"{APP_NAME} {__version__}: keeps your voice in check "
                                 "while you game. Its dashboard is at http://127.0.0.1:48680")
    ap.add_argument("--background", action="store_true",
                    help="don't open the dashboard (what the start-with-Windows shortcut uses)")
    ap.add_argument("--no-open", action="store_true", help="don't open the dashboard in the browser")
    ap.add_argument("--port", type=int, help="dashboard port instead of the one in settings (48680)")
    ap.add_argument("--data-dir", help="keep settings and history here instead of %%LOCALAPPDATA%%\\Vocalm")
    ap.add_argument("--list-devices", action="store_true", help="list microphones and exit")
    ap.add_argument("--quit", action="store_true", help="ask the running Vocalm to save and quit, then exit")
    ap.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    args = ap.parse_args(argv)
    if args.data_dir:
        paths.configure(args.data_dir)

    if args.quit:
        sys.exit(0 if quit_running() else 1)

    if args.list_devices:
        from .audio import input_devices
        for index, api, dev in input_devices():
            print(f"[{index:3}] {api:<22} {dev['name'].strip()}")
        return

    if sys.stdout is None or args.background:
        _log_to_file()
    print(f"--- {APP_NAME} {__version__}, {time.strftime('%Y-%m-%d %H:%M:%S')}")

    from . import settings, store, winapi
    from .server import running
    cfg, first_start = settings.load()
    port = args.port or cfg.port
    url = f"http://127.0.0.1:{port}/"

    # A copy with its own data folder (a test, a smoke test) gets its own lock, so it can run beside yours
    lock = "Local\\Vocalm-singleton" if not args.data_dir else "Local\\Vocalm-" + hashlib.sha1(
        paths.DATA.lower().encode()).hexdigest()[:12]
    if not winapi.claim_single_instance(lock):
        # Already running: double-clicking again should just bring up the dashboard
        if running(port):
            print(f"{APP_NAME} is already running" + ("." if args.background else ", opening it."))
            if not (args.background or args.no_open):
                webbrowser.open(url)
        else:
            winapi.message_box(APP_NAME, f"{APP_NAME} is already running, but its dashboard isn't answering on "
                               f"port {port}. Quit it from the tray icon, or end Vocalm in Task Manager.")
        return

    legacy = paths.legacy_dir()
    if first_start and legacy:
        carried = settings.from_legacy(legacy)
        if carried is not None:
            cfg = carried
            print(f"Carried your settings over from {legacy}")

    history = store.Store(paths.DB)
    try:
        if not cfg.legacy_imported:
            if legacy:
                events, sessions = store.import_legacy(paths.DB, legacy)
                print(f"Imported {events} loud moments and {sessions} sessions from {legacy}")
            cfg.legacy_imported = True
        settings.save(cfg)

        from .overlay import Core
        from .server import Server
        from .tray import Tray
        core = Core(cfg, history, port)
        server = Server(core, port)
        port = server.start()
        Tray(core).start()
        if not (args.background or args.no_open):
            threading.Timer(0.6, webbrowser.open, args=(f"http://127.0.0.1:{port}/",)).start()
        core.run()
        print(f"{APP_NAME} stopped")
    finally:
        history.close()


def quit_running(timeout=15):
    """Ask the copy that's running to save and quit, and wait for it to go. True if none is left running."""
    try:
        with open(paths.INSTANCE, encoding="utf-8") as f:
            inst = json.load(f)
    except (OSError, ValueError):
        print(f"{APP_NAME} isn't running")
        return True
    port = int(inst["port"])
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/action", data=json.dumps({"action": "quit"}).encode(), method="POST",
        headers={"Content-Type": "application/json", "X-Vocalm-Token": inst["token"]})
    try:
        urllib.request.urlopen(request, timeout=5).read()
    except OSError:
        print(f"{APP_NAME} isn't answering")
        return False
    from .server import running
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not running(port, timeout=0.5) and not os.path.exists(paths.INSTANCE):
            time.sleep(0.5)  # let the process finish exiting after its server stops
            print(f"{APP_NAME} quit")
            return True
        time.sleep(0.3)
    print(f"{APP_NAME} didn't quit in time")
    return False


def run():
    """Entry point for the .exe and Vocalm.pyw: problems end up in the log and a dialog, not a vanished window."""
    try:
        main()
    except SystemExit:
        raise
    except BaseException:
        details = traceback.format_exc()
        try:
            os.makedirs(paths.DATA, exist_ok=True)
            with open(paths.LOG, "a", encoding="utf-8") as f:
                f.write(details)
        except OSError:
            pass
        from .winapi import message_box
        message_box(f"{APP_NAME} couldn't start", details.strip().splitlines()[-1] + f"\n\nDetails: {paths.LOG}")
        sys.exit(1)
