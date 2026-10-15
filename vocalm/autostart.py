"""Starting Vocalm when you sign in to Windows: a shortcut in your Startup folder that runs Vocalm.exe --background
(or Vocalm.pyw --background with pythonw, when running from the source code). No window, no browser tab.
"""
import os
import shutil
import subprocess
import sys

FROZEN = getattr(sys, "frozen", False)  # running as Vocalm.exe
HERE = os.path.dirname(sys.executable) if FROZEN else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STARTUP = os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu", "Programs", "Startup")
LINK = os.path.join(STARTUP, "Vocalm.lnk")
NO_WINDOW = 0x08000000


def supported():
    return sys.platform == "win32" and os.path.isdir(STARTUP)


def enabled():
    return supported() and os.path.exists(LINK)


def launcher():
    """(program, arguments) that start Vocalm without a window."""
    if FROZEN:
        return sys.executable, "--background"
    script = os.path.join(HERE, "Vocalm.pyw")
    exe = sys.executable or ""
    if os.path.basename(exe).lower() == "python.exe":
        exe = os.path.join(os.path.dirname(exe), "pythonw.exe")
    if os.path.basename(exe).lower() == "pythonw.exe" and os.path.exists(exe):
        return exe, f'"{script}" --background'
    pyw = shutil.which("pyw")
    if pyw:
        return pyw, f'-3 "{script}" --background'
    raise RuntimeError("Couldn't find pythonw.exe to start Vocalm with")


def enable():
    if not supported():
        raise RuntimeError("Starting with Windows only works on Windows")
    exe, args = launcher()
    env = dict(os.environ, VC_LINK=LINK, VC_TARGET=exe, VC_ARGS=args, VC_DIR=os.path.dirname(exe))
    script = ("$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:VC_LINK); "
              "$s.TargetPath = $env:VC_TARGET; $s.Arguments = $env:VC_ARGS; $s.WorkingDirectory = $env:VC_DIR; "
              "$s.Description = 'Vocalm: keeps your voice in check, in the background'; $s.WindowStyle = 7; $s.Save()")
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], env=env,
                       capture_output=True, text=True, errors="replace", creationflags=NO_WINDOW, timeout=60)
    if r.returncode or not os.path.exists(LINK):
        raise RuntimeError((r.stderr.strip().splitlines() or ["Couldn't create the startup shortcut"])[-1])
    remove_legacy()


def remove_legacy():
    """The old script was started by a copy of its .bat in the Startup folder. Left there, it would start a second
    overlay next to Vocalm. Only a .bat that runs the old script is removed."""
    removed = []
    for name in ("Loud_Mic.bat",):
        path = os.path.join(STARTUP, name)
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                is_old_script = "calm_mic.py" in f.read()
            if is_old_script:  # removed after closing it: Windows won't delete a file that's still open
                os.remove(path)
                removed.append(path)
        except OSError:
            pass
    return removed


def disable():
    if os.path.exists(LINK):
        os.remove(LINK)
