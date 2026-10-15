"""Make every screenshot and video in the README and on the website, from Vocalm's demo mode.

    python packaging/media/make_media.py

Starts `python -m vocalm --demo` (a scripted voice and made-up history: nothing from your own Vocalm), films the
dashboard in headless Chrome and the real bar over a made-up game scene, and writes:

  docs/images/*.webp        README pictures; demo.webp and bar.webp are animated
  site/media/*.mp4, *.webp  the website's videos and their posters
  assets/social-preview.png the card shown when the site or repo is shared

Needs Node 22+, Chrome or Edge, and ffmpeg (winget install Gyan.FFmpeg). The bar is filmed on your main monitor,
so leave the screen alone for the half minute it takes.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
IMAGES = os.path.join(ROOT, "docs", "images")
SITE_MEDIA = os.path.join(ROOT, "site", "media")
PORT = 48690
FONT = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", "bahnschrift.ttf")


def run(*cmd, **kw):
    print("$", " ".join(str(c) for c in cmd[:6]), "..." if len(cmd) > 6 else "")
    subprocess.run([str(c) for c in cmd], check=True, **kw)


def ffmpeg(*args):
    run("ffmpeg", "-y", "-loglevel", "error", *args)


def start_demo():
    proc = subprocess.Popen([sys.executable, "-u", "-m", "vocalm", "--demo", "--no-open", "--port", str(PORT)],
                            cwd=ROOT, stdout=subprocess.PIPE, text=True)
    data = None
    for line in proc.stdout:
        print("  demo:", line.rstrip())
        if line.startswith("Demo data in"):
            data = line.split("in", 1)[1].strip()
        if line.startswith("Listening to"):
            break
    import threading  # keep reading its output, or a full pipe would freeze it mid-film
    threading.Thread(target=lambda: [None for _ in proc.stdout], daemon=True).start()
    for _ in range(50):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/ping", timeout=1)
            return proc, data
        except OSError:
            time.sleep(0.2)
    raise SystemExit("The demo didn't start")


def webp(src, dst, width=None, quality=86):
    img = Image.open(src).convert("RGB")
    if width and img.width > width:
        img = img.resize((width, round(img.height * width / img.width)), Image.LANCZOS)
    img.save(dst, "WEBP", quality=quality, method=6)


def dashboard_video(raw):
    """The JPEG frames (taken at irregular moments) at their real timing, as an MP4."""
    with open(os.path.join(raw, "frames.json"), encoding="utf-8") as f:
        frames = json.load(f)
    listing = os.path.join(raw, "frames.txt")
    with open(listing, "w", encoding="utf-8") as f:
        for this, nxt in zip(frames, frames[1:] + [None]):
            f.write(f"file 'frames/{this['name']}'\n")
            f.write(f"duration {((nxt['t'] if nxt else this['t'] + 80) - this['t']) / 1000:.3f}\n")
        f.write(f"file 'frames/{frames[-1]['name']}'\n")
    out = os.path.join(raw, "dashboard.mp4")
    ffmpeg("-f", "concat", "-safe", "0", "-i", listing, "-vf", "fps=30,format=yuv420p", "-c:v", "libx264",
           "-preset", "slow", "-crf", "22", "-movflags", "+faststart", out)
    return out


def social_preview(bar_frame, dst):
    """1280x640: the logo, the name, the promise, and a real frame of the bar going red."""
    W, H = 1280, 640
    card = Image.new("RGB", (W, H), (10, 14, 13))
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse((-300, -380, 700, 420), fill=(63, 191, 173, 60))
    from PIL import ImageFilter
    card = Image.alpha_composite(card.convert("RGBA"), glow.filter(ImageFilter.GaussianBlur(120))).convert("RGB")
    logo = Image.open(os.path.join(ROOT, "assets", "logo-256.png")).convert("RGBA").resize((112, 112), Image.LANCZOS)
    card.paste(logo, (80, 92), logo)
    d = ImageDraw.Draw(card)
    d.text((216, 104), "Vocalm", font=ImageFont.truetype(FONT, 72), fill=(236, 243, 241))
    d.text((82, 250), "Hear yourself before", font=ImageFont.truetype(FONT, 58), fill=(236, 243, 241))
    d.text((82, 318), "your friends do.", font=ImageFont.truetype(FONT, 58), fill=(63, 191, 173))
    for i, line in enumerate(("A calm, click-through loudness bar for", "gaming on Discord, and a dashboard of",
                              "when and where you get loud. Free, for Windows.")):
        d.text((84, 412 + i * 36), line, font=ImageFont.truetype(FONT, 26), fill=(163, 179, 175))
    shot = Image.open(bar_frame).convert("RGB").crop((1280 - 420, 0, 1280, 250)).resize((420, 250), Image.LANCZOS)
    frame = Image.new("RGB", (shot.width + 4, shot.height + 4), (47, 60, 58))
    frame.paste(shot, (2, 2))
    card.paste(frame, (W - frame.width - 64, (H - frame.height) // 2 + 40))
    card.save(dst, optimize=True)


def main():
    os.makedirs(IMAGES, exist_ok=True)
    os.makedirs(SITE_MEDIA, exist_ok=True)
    raw = tempfile.mkdtemp(prefix="vocalm-media-raw-")
    proc, data = start_demo()
    try:
        time.sleep(3)
        run("node", os.path.join(HERE, "dashboard.mjs"), f"http://127.0.0.1:{PORT}", raw)
        run(sys.executable, os.path.join(HERE, "overlay.py"), data, os.path.join(raw, "bar.mp4"), 27)
    finally:
        subprocess.run([sys.executable, "-m", "vocalm", "--quit", "--data-dir", data], cwd=ROOT)
        proc.wait(timeout=20)
        shutil.rmtree(data, ignore_errors=True)

    for name in ("live", "insights", "heatmap", "settings", "settings-bar", "calibrate"):
        webp(os.path.join(raw, f"{name}.png"), os.path.join(IMAGES, f"{name}.webp"))
    if os.path.exists(os.path.join(raw, "download.png")):
        webp(os.path.join(raw, "download.png"), os.path.join(IMAGES, "download.webp"))

    dash = dashboard_video(raw)
    bar = os.path.join(raw, "bar.mp4")
    closeup = "crop=440:250:840:0"
    # The website: H.264 MP4s that autoplay muted, with a still poster each
    shutil.copy(dash, os.path.join(SITE_MEDIA, "dashboard.mp4"))
    ffmpeg("-i", bar, "-c:v", "libx264", "-preset", "slow", "-crf", "20", "-movflags", "+faststart",
           "-pix_fmt", "yuv420p", os.path.join(SITE_MEDIA, "bar.mp4"))
    ffmpeg("-i", bar, "-vf", f"{closeup},scale=880:-2:flags=lanczos", "-c:v", "libx264", "-preset", "slow",
           "-crf", "18", "-movflags", "+faststart", "-pix_fmt", "yuv420p", os.path.join(SITE_MEDIA, "bar-closeup.mp4"))
    ffmpeg("-ss", "11", "-i", bar, "-frames:v", "1", os.path.join(raw, "bar-red.png"))
    webp(os.path.join(raw, "bar-red.png"), os.path.join(SITE_MEDIA, "bar-poster.webp"), quality=80)
    ffmpeg("-ss", "11", "-i", os.path.join(SITE_MEDIA, "bar-closeup.mp4"), "-frames:v", "1",
           os.path.join(raw, "closeup-red.png"))
    webp(os.path.join(raw, "closeup-red.png"), os.path.join(SITE_MEDIA, "bar-closeup-poster.webp"), quality=82)
    ffmpeg("-ss", "6", "-i", dash, "-frames:v", "1", os.path.join(raw, "dash-poster.png"))
    webp(os.path.join(raw, "dash-poster.png"), os.path.join(SITE_MEDIA, "dashboard-poster.webp"), quality=80)
    # The README: animated WebP (GitHub shows those inline, where it won't play a video file from the repository)
    ffmpeg("-i", bar, "-vf", f"{closeup},fps=15,scale=660:-2:flags=lanczos", "-c:v", "libwebp_anim",
           "-loop", "0", "-quality", "72", "-compression_level", "6", os.path.join(IMAGES, "bar.webp"))
    ffmpeg("-i", dash, "-vf", "fps=10,scale=960:-2:flags=lanczos", "-c:v", "libwebp_anim", "-loop", "0",
           "-quality", "60", "-compression_level", "6", os.path.join(IMAGES, "demo.webp"))
    social_preview(os.path.join(raw, "bar-red.png"), os.path.join(ROOT, "assets", "social-preview.png"))
    shutil.rmtree(raw, ignore_errors=True)
    for folder in (IMAGES, SITE_MEDIA):
        for name in sorted(os.listdir(folder)):
            print(f"  {os.path.relpath(os.path.join(folder, name), ROOT):<38} {os.path.getsize(os.path.join(folder, name)) / 1e6:6.2f} MB")


if __name__ == "__main__":
    main()
