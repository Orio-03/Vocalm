"""Install (or upgrade) the built app for the current user, no admin rights needed.

    python packaging/install_local.py [--autostart] [--desktop-shortcut]

  * stops a running Vocalm cleanly (it saves first), then copies dist/Vocalm to %LOCALAPPDATA%\\Programs\\Vocalm
  * adds Vocalm to the Start menu (and the desktop with --desktop-shortcut)
  * --autostart: starts it with Windows, and removes the old Loud_Mic.bat from the Startup folder

Your settings and history aren't touched: they live in %LOCALAPPDATA%\\Vocalm, not in the program folder.
"""
import argparse
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(ROOT, "dist", "Vocalm")
TARGET = os.path.join(os.environ["LOCALAPPDATA"], "Programs", "Vocalm")
EXE = os.path.join(TARGET, "Vocalm.exe")
PROGRAMS = os.path.join(os.environ["APPDATA"], "Microsoft", "Windows", "Start Menu", "Programs")
DESKTOP = os.path.join(os.path.expanduser("~"), "Desktop")


def shortcut(link, target, args="", description="Vocalm: keeps your voice in check while you game"):
    env = dict(os.environ, VL_LINK=link, VL_TARGET=target, VL_ARGS=args, VL_DIR=os.path.dirname(target),
               VL_DESC=description)
    script = ("$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:VL_LINK); $s.TargetPath = $env:VL_TARGET; "
              "$s.Arguments = $env:VL_ARGS; $s.WorkingDirectory = $env:VL_DIR; $s.Description = $env:VL_DESC; "
              "$s.IconLocation = $env:VL_TARGET + ',0'; $s.Save()")
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], env=env, check=True)


def main():
    ap = argparse.ArgumentParser(description="Install the built Vocalm for this user")
    ap.add_argument("--autostart", action="store_true", help="start Vocalm when you sign in")
    ap.add_argument("--desktop-shortcut", action="store_true", help="also put a shortcut on the desktop")
    args = ap.parse_args()
    if not os.path.isfile(os.path.join(SOURCE, "Vocalm.exe")):
        sys.exit("Build it first: python packaging/build_windows.py")

    if os.path.isfile(EXE):
        print("Stopping the running Vocalm (it saves first)...")
        subprocess.run([EXE, "--quit"], timeout=60)
    if os.path.isdir(TARGET):
        shutil.rmtree(TARGET)
    shutil.copytree(SOURCE, TARGET)
    print(f"Installed to {TARGET}")

    shortcut(os.path.join(PROGRAMS, "Vocalm.lnk"), EXE)
    print("Added to the Start menu")
    if args.desktop_shortcut:
        shortcut(os.path.join(DESKTOP, "Vocalm.lnk"), EXE)
        print("Added a desktop shortcut")
    if args.autostart:
        startup = os.path.join(PROGRAMS, "Startup")
        shortcut(os.path.join(startup, "Vocalm.lnk"), EXE, "--background",
                 "Vocalm: keeps your voice in check, in the background")
        old = os.path.join(startup, "Loud_Mic.bat")
        if os.path.isfile(old):
            with open(old, encoding="utf-8", errors="replace") as f:
                is_old_script = "calm_mic.py" in f.read()
            if is_old_script:  # removed after closing it: Windows won't delete a file that's still open
                os.remove(old)
                print("Removed the old Loud_Mic.bat from the Startup folder")
        print("Vocalm will start with Windows")


if __name__ == "__main__":
    main()
