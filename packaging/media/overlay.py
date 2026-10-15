"""Film the real bar over a made-up game scene, from a Vocalm running in demo mode.

    python packaging/media/overlay.py <demo data folder> <out.mp4> [seconds]

Draws a game-like scene (sky, hills and a HUD, all made up) in a window, moves the demo's bar into its top-right
corner, and records that part of the screen. The bar, its red-peak readout and the breathe card are the real
thing, drawn by Vocalm itself.
"""
import ctypes
import json
import os
import random
import subprocess
import sys
import time
import tkinter as tk
import urllib.request
from ctypes import wintypes

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageTk

W, H = 1280, 720
FONT = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", "bahnschrift.ttf")
u32, gdi = ctypes.windll.user32, ctypes.windll.gdi32
u32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))


def scene(seed=4):
    """A dusk landscape with a shooter-style HUD. Entirely drawn here; no real game in it."""
    rng = random.Random(seed)
    sky = Image.new("RGB", (1, H))                                  # a one-pixel column, stretched: the sky
    top, mid, low = (9, 20, 32), (34, 52, 70), (196, 120, 82)
    for y in range(H):
        t = y / H
        k = t / 0.55 if t < 0.55 else min(1, (t - 0.55) / 0.2) * 0.55
        a, b = (top, mid) if t < 0.55 else (mid, low)
        sky.putpixel((0, y), tuple(int(a[i] + (b[i] - a[i]) * k) for i in range(3)))
    img = sky.resize((W, H))
    d = ImageDraw.Draw(img)
    for _ in range(90):  # stars
        x, y = rng.randrange(W), rng.randrange(int(H * 0.4))
        v = rng.randint(120, 220)
        d.point((x, y), fill=(v, v, v + 10))
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))                  # the sun going down behind the hills
    ImageDraw.Draw(glow).ellipse((W * 0.55, H * 0.5, W * 0.95, H * 0.88), fill=(240, 150, 90, 120))
    img = Image.alpha_composite(img.convert("RGBA"), glow.filter(ImageFilter.GaussianBlur(70))).convert("RGB")
    for layer, (colour, base, rough) in enumerate([((30, 44, 58), 0.62, 70), ((20, 31, 41), 0.70, 55),
                                                   ((12, 19, 26), 0.80, 40)]):
        pts, x = [(0, H)], 0
        y = H * base
        while x <= W + 40:
            pts.append((x, y))
            x += rng.randint(30, 90)
            y = max(H * (base - 0.18), min(H * (base + 0.06), y + rng.randint(-rough, rough)))
        pts += [(W + 40, H)]
        layer_img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(layer_img).polygon(pts, fill=colour + (255,))
        if layer == 0:
            layer_img = layer_img.filter(ImageFilter.GaussianBlur(2))
        img = Image.alpha_composite(img.convert("RGBA"), layer_img).convert("RGB")

    d = ImageDraw.Draw(img, "RGBA")
    big, small = ImageFont.truetype(FONT, 40), ImageFont.truetype(FONT, 16)
    cx, cy = W // 2, H // 2 + 20                                   # crosshair
    for x1, y1, x2, y2 in [(cx - 18, cy, cx - 6, cy), (cx + 6, cy, cx + 18, cy), (cx, cy - 18, cx, cy - 6),
                           (cx, cy + 6, cx, cy + 18)]:
        d.line((x1, y1, x2, y2), fill=(235, 240, 238, 230), width=2)
    d.ellipse((34, 30, 194, 190), fill=(8, 14, 18, 170), outline=(220, 230, 228, 90), width=2)   # minimap
    for _ in range(6):
        x, y = rng.randint(70, 160), rng.randint(66, 156)
        d.ellipse((x - 3, y - 3, x + 3, y + 3), fill=(230, 90, 80, 220))
    d.polygon([(114, 102), (108, 118), (114, 113), (120, 118)], fill=(240, 245, 243, 255))
    d.rounded_rectangle((34, H - 70, 314, H - 52), 6, fill=(8, 14, 18, 160))                        # health
    d.rounded_rectangle((38, H - 66, 250, H - 56), 4, fill=(232, 238, 236, 230))
    d.text((34, H - 104), "100", font=small, fill=(232, 238, 236, 220))
    d.text((W - 190, H - 96), "24", font=big, fill=(240, 245, 243, 240))                           # ammo
    d.text((W - 136, H - 82), "/ 120", font=small, fill=(200, 210, 208, 200))
    d.text((W // 2 - 60, 26), "ROUND 7  ·  4 - 3", font=small, fill=(232, 238, 236, 210))
    vignette = Image.new("L", (W, H), 0)
    ImageDraw.Draw(vignette).ellipse((-W * 0.2, -H * 0.3, W * 1.2, H * 1.3), fill=255)
    vignette = vignette.filter(ImageFilter.GaussianBlur(120))
    return Image.composite(img, Image.new("RGB", (W, H), (0, 0, 0)), vignette)


def grab(x, y, w, h):
    hdc = u32.GetDC(0)
    mem = gdi.CreateCompatibleDC(hdc)
    bmp = gdi.CreateCompatibleBitmap(hdc, w, h)
    gdi.SelectObject(mem, bmp)
    gdi.BitBlt(mem, 0, 0, w, h, hdc, x, y, 0x00CC0020 | 0x40000000)  # CAPTUREBLT: include layered windows

    class BMIH(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                    ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("a", wintypes.LONG), ("b", wintypes.LONG),
                    ("c", wintypes.DWORD), ("d", wintypes.DWORD)]
    bmi = BMIH(ctypes.sizeof(BMIH), w, -h, 1, 32, 0)
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bmi), 0)
    gdi.DeleteObject(bmp)
    gdi.DeleteDC(mem)
    u32.ReleaseDC(0, hdc)
    return buf.raw


def api(inst, path, body=None):
    req = urllib.request.Request(f"http://127.0.0.1:{inst['port']}{path}", headers={"X-Vocalm-Token": inst["token"],
                                 "Content-Type": "application/json"}, data=json.dumps(body).encode() if body else None)
    return json.loads(urllib.request.urlopen(req, timeout=10).read())


def main(data_dir, out, seconds=27.0, fps=25):
    with open(os.path.join(data_dir, "instance.json"), encoding="utf-8") as f:
        inst = json.load(f)
    left, top = 380, 220                                 # where the scene sits on the main monitor
    root = tk.Tk()
    root.overrideredirect(True)
    root.geometry(f"{W}x{H}+{left}+{top}")
    root.attributes("-topmost", True)
    photo = ImageTk.PhotoImage(scene())
    tk.Label(root, image=photo, bd=0).pack()
    root.update()
    # Put the bar in the scene's top-right corner, the way you'd place it over a game
    # The shortest timer, so the clip shows the red peak appearing and then clearing away by itself
    settings = api(inst, "/api/settings", {"pos_x": left + W - 330, "pos_y": top + 28, "bar_label": "peak",
                                           "bar_peak_marker": True, "overlay_visible": True,
                                           "bar_info_timeout_s": 10})["settings"]
    print("bar at", settings["pos_x"], settings["pos_y"])
    for _ in range(80):   # Vocalm re-asserts always-on-top every 2 s; let it win over the scene window
        root.update()
        time.sleep(0.03)
    # Start filming as the script's long pause ends, so the clip opens with calm talking and builds up
    stream = urllib.request.urlopen(f"http://127.0.0.1:{inst['port']}/api/live?t={inst['token']}", timeout=60)
    quiet_since = None
    while True:
        line = stream.readline()
        if not line.startswith(b"data: "):
            continue
        zone = json.loads(line[6:])["zone"]
        now = time.monotonic()
        if zone == "quiet":
            quiet_since = quiet_since or now
        elif quiet_since and now - quiet_since > 3.0:
            break
        else:
            quiet_since = None
        root.update()
    stream.close()
    proc = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgra",
                             "-s", f"{W}x{H}", "-r", str(fps), "-i", "-", "-c:v", "libx264", "-preset", "slow",
                             "-crf", "18", "-pix_fmt", "yuv420p", out], stdin=subprocess.PIPE)
    start, frame = time.perf_counter(), 0
    while frame < seconds * fps:
        root.update()
        proc.stdin.write(grab(left, top, W, H))
        frame += 1
        wait = start + frame / fps - time.perf_counter()
        if wait > 0:
            time.sleep(wait)
    proc.stdin.close()
    proc.wait()
    root.destroy()
    print(f"wrote {out} ({frame} frames)")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], float(sys.argv[3]) if len(sys.argv) > 3 else 27.0)
