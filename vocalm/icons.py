"""The logo, drawn with Pillow: for the .ico file and for the tray icon, which changes colour with your state.

Geometry is in the same 64x64 units as assets/logo.svg: a rounded tile with a waveform that calms from left to
right. Small sizes use three wider bars, because five 1.5 px bars blur into a grey smudge at 16 px.
"""
import numpy as np
from PIL import Image, ImageDraw

# (x, top, height, opacity) in 64-unit space, bars are 6 wide
BARS = [(9, 13, 38, 1.0), (19, 18, 28, .92), (29, 23, 18, .84), (39, 27, 10, .76), (49, 29, 6, .68)]
BARS_SMALL = [(8, 10, 44, 1.0), (27, 19, 26, .9), (46, 27, 10, .8)]  # bars are 10 wide

GRADIENTS = {
    "calm": ("#8fe0d1", "#1d8577"),
    "loud": ("#ff9a8a", "#d03b3b"),
    "paused": ("#a3a3ad", "#55555f"),
}


def _rgb(hexcode):
    hexcode = hexcode.lstrip("#")
    return [int(hexcode[i:i + 2], 16) for i in (0, 2, 4)]


def _diagonal(n, top_left, bottom_right):
    a, b = np.array(_rgb(top_left), float), np.array(_rgb(bottom_right), float)
    ys, xs = np.mgrid[0:n, 0:n]
    t = ((xs + ys) / (2.0 * (n - 1)))[..., None]
    pixels = (a * (1 - t) + b * t).clip(0, 255).astype(np.uint8)
    return Image.fromarray(pixels, "RGB").convert("RGBA")


def logo_image(size, state="calm"):
    """An RGBA image of the logo, size x size pixels. state: calm | loud | paused."""
    scale = 8 if size <= 64 else 4  # draw big, then shrink: Pillow's shapes aren't anti-aliased
    n = size * scale
    unit = n / 64.0
    tile = _diagonal(n, *GRADIENTS.get(state, GRADIENTS["calm"]))

    mask = Image.new("L", (n, n), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, n - 1, n - 1), radius=16 * unit, fill=255)
    out = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    out.paste(tile, (0, 0), mask)

    bars, width = (BARS_SMALL, 10) if size <= 24 else (BARS, 6)
    layer = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    for x, top, height, opacity in bars:
        draw.rounded_rectangle((x * unit, top * unit, (x + width) * unit, (top + height) * unit),
                               radius=width / 2 * unit, fill=(255, 255, 255, int(255 * opacity)))
    out = Image.alpha_composite(out, layer)
    return out.resize((size, size), Image.LANCZOS)
