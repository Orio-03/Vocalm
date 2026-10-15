"""Build the Windows app: dist/Vocalm/Vocalm.exe, zipped as dist/Vocalm-<version>-windows.zip.

    python -m pip install -r requirements.txt -r packaging/requirements-build.txt
    python packaging/build_windows.py

Then try it with packaging/smoke_test.py, and install it with packaging/install_local.py.
"""
import os
import shutil
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from vocalm import APP_NAME, DEFAULT_PORT, __version__  # noqa: E402

QUICK_START = f"""{APP_NAME} {__version__}
{'=' * (len(APP_NAME) + len(__version__) + 1)}

Keeps your voice in check while you game.

1. Double-click Vocalm.exe. A small bar appears on your screen, a tray icon appears by the clock,
   and your browser opens the dashboard at http://127.0.0.1:{DEFAULT_PORT}
   (Windows may say it "protected your PC" the first time: click "More info", then "Run anyway").
2. In the dashboard, open Settings and calibrate your voice (15 seconds).
3. Optional: turn on "Start with Windows" in Settings or in the tray menu.

Hotkeys: Ctrl+Alt+M pause, Ctrl+Alt+B move the bar, Ctrl+Alt+Q quit.
Games must run in borderless or windowed mode for the bar to show over them.

Keep all the files in this folder together. Your settings and history live in %LOCALAPPDATA%\\Vocalm.
"""

VERSION_INFO = """VSVersionInfo(
  ffi=FixedFileInfo(filevers=({v}, 0), prodvers=({v}, 0), mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1,
                    subtype=0x0, date=(0, 0)),
  kids=[StringFileInfo([StringTable('040904B0', [
          StringStruct('FileDescription', '{name}'), StringStruct('ProductName', '{name}'),
          StringStruct('FileVersion', '{version}'), StringStruct('ProductVersion', '{version}'),
          StringStruct('OriginalFilename', '{name}.exe'), StringStruct('InternalName', '{name}')])]),
        VarFileInfo([VarStruct('Translation', [1033, 1200])])])
"""


def main():
    dist, build = os.path.join(ROOT, "dist"), os.path.join(ROOT, "build")
    shutil.rmtree(dist, ignore_errors=True)
    shutil.rmtree(build, ignore_errors=True)
    os.makedirs(build)
    version_file = os.path.join(build, "version_info.txt")
    numbers = ", ".join(__version__.split("."))
    with open(version_file, "w", encoding="utf-8") as f:
        f.write(VERSION_INFO.format(v=numbers, name=APP_NAME, version=__version__))

    subprocess.run([
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed", "--name", APP_NAME,
        "--icon", os.path.join(ROOT, "assets", "vocalm.ico"),
        "--version-file", version_file,
        "--add-data", os.path.join(ROOT, "vocalm", "web") + os.pathsep + "vocalm/web",
        "--hidden-import", "pystray._win32",  # pystray picks its backend at runtime, so PyInstaller can't see it
        "--distpath", dist, "--workpath", build, "--specpath", build,
        os.path.join(ROOT, "Vocalm.pyw"),
    ], check=True)

    app = os.path.join(dist, APP_NAME)
    for name in ("README.md", "CHANGELOG.md", "PRIVACY.md", "LICENSE.md"):
        shutil.copy2(os.path.join(ROOT, name), app)
    with open(os.path.join(app, "Read me.txt"), "w", encoding="utf-8", newline="\r\n") as f:
        f.write(QUICK_START)

    out = os.path.join(dist, f"{APP_NAME}-{__version__}-windows.zip")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for folder, _, files in os.walk(app):
            for name in files:
                path = os.path.join(folder, name)
                z.write(path, os.path.join(APP_NAME, os.path.relpath(path, app)))
    print(f"Built {out} ({os.path.getsize(out) / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
