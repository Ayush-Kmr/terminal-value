#!/usr/bin/env python3
"""Draw the channel art: banner, avatar, video watermark.

    python scripts/brand_assets.py

Writes to assets/brand/. Everything uses the palette and fonts in config.yaml,
so the channel page and the videos are visibly the same system.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.config import PALETTE_DEFAULT, load  # noqa: E402

OUT = ROOT / "assets" / "brand"

# A share price that climbs, peaks and collapses. The channel's whole subject
# in one line; used as the backdrop of the banner and the avatar's mark.
CURVE = [0.10, 0.14, 0.13, 0.22, 0.30, 0.28, 0.41, 0.52, 0.49, 0.63, 0.78, 0.74,
         0.90, 1.00, 0.86, 0.91, 0.66, 0.48, 0.52, 0.31, 0.19, 0.12, 0.05]
PEAK = CURVE.index(max(CURVE))


def font(cfg, role: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(ROOT / cfg["visuals"]["fonts"][role]), size)


def grain(img: Image.Image, strength: float) -> Image.Image:
    arr = np.asarray(img).astype("int16")
    noise = np.random.default_rng(11).normal(0, strength * 255, arr.shape[:2])
    return Image.fromarray((arr + noise[..., None]).clip(0, 255).astype("uint8"))


def tracked(d: ImageDraw.ImageDraw, xy, text: str, f, fill, tracking: float,
            anchor_centre: bool = True) -> None:
    """Letter-spaced text. Pillow has no tracking, so place glyphs by hand."""
    widths = [d.textlength(ch, font=f) for ch in text]
    total = sum(widths) + tracking * (len(text) - 1)
    x = xy[0] - total / 2 if anchor_centre else xy[0]
    for ch, w in zip(text, widths):
        d.text((x, xy[1]), ch, font=f, fill=fill, anchor="ls")
        x += w + tracking


def curve_points(box: tuple[float, float, float, float]) -> list[tuple[float, float]]:
    x0, y0, x1, y1 = box
    step = (x1 - x0) / (len(CURVE) - 1)
    return [(x0 + i * step, y1 - v * (y1 - y0)) for i, v in enumerate(CURVE)]


def banner(cfg, pal) -> Path:
    """2048x1152. TV crops show all of it, desktop a wide strip, mobile only the
    centre 1235x338 — so every word lives inside that centre box."""
    W, H = 2048, 1152
    S = 2                                   # supersample for clean line edges
    img = Image.new("RGB", (W * S, H * S), pal["bg"])
    d = ImageDraw.Draw(img)

    for gx in range(0, W * S, 128 * S):     # faint ledger grid
        d.line([(gx, 0), (gx, H * S)], fill=pal["surface"], width=S)
    for gy in range(0, H * S, 128 * S):
        d.line([(0, gy), (W * S, gy)], fill=pal["surface"], width=S)

    pts = curve_points((-40 * S, 150 * S, (W + 40) * S, 1010 * S))
    d.line(pts[:PEAK + 1], fill="#3A3F2A", width=5 * S, joint="curve")
    d.line(pts[PEAK:], fill="#4A2530", width=5 * S, joint="curve")

    img = img.resize((W, H), Image.LANCZOS)
    d = ImageDraw.Draw(img)

    # Dim the backdrop behind the type so the wordmark never fights the line.
    cx, cy = W // 2, H // 2
    plate = Image.new("L", (W, H), 0)
    ImageDraw.Draw(plate).rectangle([cx - 700, cy - 200, cx + 700, cy + 200], fill=235)
    from PIL import ImageFilter
    plate = plate.filter(ImageFilter.GaussianBlur(60))
    img = Image.composite(Image.new("RGB", (W, H), pal["bg"]), img, plate)
    d = ImageDraw.Draw(img)

    f_word = font(cfg, "display", 114)
    tracked(d, (cx, cy + 8), "TERMINAL VALUE", f_word, pal["ink"], 4)
    d.rectangle([cx - 50, cy + 44, cx + 50, cy + 50], fill=pal["accent"])
    tracked(d, (cx, cy + 104), cfg["channel"]["tagline"].upper(),
            font(cfg, "mono", 34), pal["muted"], 5)
    tracked(d, (cx, cy + 156), cfg["channel"].get("schedule_line", "").upper(),
            font(cfg, "mono", 26), pal["accent"], 4)

    img = grain(img, 0.022)
    out = OUT / "banner.png"
    img.save(out, optimize=True)
    return out


def _mark(size: int, pal, transparent: bool) -> Image.Image:
    """The monogram: TV over the collapsing line. Must survive at 48px, so it
    is two letters and one stroke and nothing else."""
    S = 4
    n = size * S
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0) if transparent else pal["bg"])
    d = ImageDraw.Draw(img)

    cfg = load()
    f = font(cfg, "display", int(n * 0.40))
    bb = d.textbbox((0, 0), "TV", font=f)
    tw, th = bb[2] - bb[0], bb[3] - bb[1]
    tx, ty = (n - tw) / 2 - bb[0], n * 0.45 - th / 2 - bb[1]
    d.text((tx, ty), "TV", font=f, fill=pal["ink"])

    # Underline that falls away: flat, then down and out to the right.
    y = ty + bb[3] + n * 0.075
    x0, x1 = (n - tw) / 2, (n + tw) / 2
    w = max(int(n * 0.038), 2)
    drop = [(x0, y), (x0 + tw * 0.56, y), (x1, y + n * 0.13)]
    d.line(drop[:2], fill=pal["accent"], width=w)
    d.line(drop[1:], fill=pal["danger"], width=w, joint="curve")
    r = w * 0.5
    d.ellipse([drop[1][0] - r, drop[1][1] - r, drop[1][0] + r, drop[1][1] + r],
              fill=pal["accent"])
    return img.resize((size, size), Image.LANCZOS)


def avatar(pal) -> Path:
    out = OUT / "avatar.png"
    _mark(800, pal, transparent=False).convert("RGB").save(out, optimize=True)
    return out


def watermark(pal) -> Path:
    """150x150, transparent. Sits bottom-right of every video as the subscribe button."""
    out = OUT / "watermark.png"
    _mark(150, pal, transparent=True).save(out, optimize=True)
    return out


def main() -> int:
    cfg = load()
    pal = {**PALETTE_DEFAULT, **(cfg.get_path("visuals.palette") or {})}
    OUT.mkdir(parents=True, exist_ok=True)
    for path in (banner(cfg, pal), avatar(pal), watermark(pal)):
        print(f"{path.relative_to(ROOT)}  {path.stat().st_size // 1024} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
