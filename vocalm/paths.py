"""Where Vocalm keeps its settings and history.

Everything lives in one data folder: %LOCALAPPDATA%\\Vocalm on Windows. Set VOCALM_DATA or pass --data-dir to
use another one (the tests and the packaging smoke test do).
"""
import os
import sys

# The script this app grew out of kept its config and logs next to itself. They're imported once, on first start.
LEGACY_DIRS = [
    os.path.join(os.path.expanduser("~"), "Desktop", "App Projects", "Voice-monitoring", "v3"),
]


def default_data_dir():
    env = os.environ.get("VOCALM_DATA")
    if env:
        return os.path.abspath(env)
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
        return os.path.join(base, "Vocalm")
    return os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"), "vocalm")


def configure(data_dir=None):
    global DATA, SETTINGS, DB, LOG, INSTANCE
    DATA = os.path.abspath(data_dir) if data_dir else default_data_dir()
    SETTINGS = os.path.join(DATA, "settings.json")
    DB = os.path.join(DATA, "vocalm.db")  # loud moments, minute-by-minute talking time, sessions
    LOG = os.path.join(DATA, "vocalm.log")  # used when there's no console (the .exe, pythonw, --background)
    INSTANCE = os.path.join(DATA, "instance.json")  # port and token of the running copy, for --quit


def legacy_dir():
    """The old script's folder, if it's still around."""
    for folder in LEGACY_DIRS:
        if os.path.isfile(os.path.join(folder, "config.json")) or os.path.isdir(os.path.join(folder, "logs")):
            return folder
    return None


configure()
