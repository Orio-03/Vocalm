"""Install (or upgrade) Vocalm for the current user, no admin rights needed.

    python packaging/install_local.py [--autostart] [--desktop-shortcut] [--from-source]

  * stops a running Vocalm cleanly (it saves first), then copies dist/Vocalm to %LOCALAPPDATA%\\Programs\\Vocalm
  * adds Vocalm to the Start menu (and the desktop with --desktop-shortcut)
  * --autostart: starts it with Windows, and removes the old Loud_Mic.bat from the Startup folder
  * --from-source: point the shortcuts at this source checkout, run by your own Python, instead of the built
    Vocalm.exe. For PCs with Smart App Control on: it blocks unsigned programs like a locally built Vocalm.exe
    outright, but allows Python (signed by the Python Software Foundation) and the libraries Vocalm uses.

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


def shortcut(link, target, args="", workdir=None, icon=None,
             description="Vocalm: keeps your voice in check while you game"):
    env = dict(os.environ, VL_LINK=link, VL_TARGET=target, VL_ARGS=args, VL_DIR=workdir or os.path.dirname(target),
               VL_DESC=description, VL_ICON=(icon or target) + ",0")
    script = ("$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:VL_LINK); $s.TargetPath = $env:VL_TARGET; "
              "$s.Arguments = $env:VL_ARGS; $s.WorkingDirectory = $env:VL_DIR; $s.Description = $env:VL_DESC; "
              "$s.IconLocation = $env:VL_ICON; $s.Save()")
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], env=env, check=True)


def pythonw():
    """The windowless twin of the Python running this script."""
    exe = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.isfile(exe):
        sys.exit(f"Couldn't find pythonw.exe next to {sys.executable}")
    return exe


def main():
    ap = argparse.ArgumentParser(description="Install Vocalm for this user")
    ap.add_argument("--autostart", action="store_true", help="start Vocalm when you sign in")
    ap.add_argument("--desktop-shortcut", action="store_true", help="also put a shortcut on the desktop")
    ap.add_argument("--from-source", action="store_true",
                    help="run this source checkout with your Python instead of the built Vocalm.exe "
                         "(for when Smart App Control blocks the unsigned exe)")
    args = ap.parse_args()

    if args.from_source:
        # pythonw "...\Vocalm.pyw": no console window, and nothing unsigned for Smart App Control to stop
        program, script = pythonw(), os.path.join(ROOT, "Vocalm.pyw")
        launch = {"target": program, "args": f'"{script}"', "workdir": ROOT,
                  "icon": os.path.join(ROOT, "assets", "vocalm.ico")}
        print(f"Shortcuts will run {script} with {program}")
    else:
        if not os.path.isfile(os.path.join(SOURCE, "Vocalm.exe")):
            sys.exit("Build it first: python packaging/build_windows.py")
        if os.path.isfile(EXE):
            print("Stopping the running Vocalm (it saves first)...")
            subprocess.run([EXE, "--quit"], timeout=60)
        if os.path.isdir(TARGET):
            shutil.rmtree(TARGET)
        shutil.copytree(SOURCE, TARGET)
        print(f"Installed to {TARGET}")
        launch = {"target": EXE, "args": ""}

    shortcut(os.path.join(PROGRAMS, "Vocalm.lnk"), **launch)
    print("Added to the Start menu")
    if args.desktop_shortcut:
        shortcut(os.path.join(DESKTOP, "Vocalm.lnk"), **launch)
        print("Added a desktop shortcut")
    if args.autostart:
        startup = os.path.join(PROGRAMS, "Startup")
        shortcut(os.path.join(startup, "Vocalm.lnk"), **dict(launch, args=(launch["args"] + " --background").strip()),
                 description="Vocalm: keeps your voice in check, in the background")
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
