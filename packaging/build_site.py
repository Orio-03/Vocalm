"""Put the website together in _site/: site/ plus the README's pictures, the logo and the sharing card.

    python packaging/build_site.py

GitHub Pages publishes exactly this folder (.github/workflows/pages.yml), so opening _site/index.html (or serving
it with `python -m http.server -d _site`) shows what will go live.
"""
import glob
import os
import shutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "_site")


def main():
    shutil.rmtree(OUT, ignore_errors=True)
    shutil.copytree(os.path.join(ROOT, "site"), OUT)
    os.makedirs(os.path.join(OUT, "images"), exist_ok=True)
    for path in glob.glob(os.path.join(ROOT, "docs", "images", "*.webp")):
        shutil.copy2(path, os.path.join(OUT, "images"))
    for name in ("logo.svg", "logo-256.png", "social-preview.png"):
        shutil.copy2(os.path.join(ROOT, "assets", name), OUT)
    with open(os.path.join(OUT, ".nojekyll"), "w"):
        pass
    size = sum(os.path.getsize(p) for p in glob.glob(os.path.join(OUT, "**", "*"), recursive=True) if os.path.isfile(p))
    print(f"Built _site/ ({size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
