"""Draw the logo as PNG and ICO files from the same geometry as assets/logo.svg.

    python packaging/make_icons.py

Writes assets/vocalm.ico (16-256 px, for the .exe and shortcuts) and assets/logo-256.png. The tray icon is drawn
at runtime by vocalm.icons, which uses the same drawing code.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from vocalm.icons import logo_image  # noqa: E402


def main():
    assets = os.path.join(ROOT, "assets")
    big = logo_image(256)
    big.save(os.path.join(assets, "logo-256.png"))
    sizes = [16, 20, 24, 32, 40, 48, 64, 128, 256]
    # Each size is drawn on its own rather than scaled down from 256, so small sizes stay crisp
    frames = [logo_image(s) for s in sizes]
    frames[-1].save(os.path.join(assets, "vocalm.ico"), format="ICO", sizes=[(s, s) for s in sizes],
                    append_images=frames[:-1])
    print("Wrote assets/vocalm.ico and assets/logo-256.png")


if __name__ == "__main__":
    main()
