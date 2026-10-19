"""Thumbnail generation: three fixed layouts, rotated.

Deliberately not AI-generated. A thumbnail is typography and one image; models
still cannot set type, and legible type is most of the click-through rate.
"""
from __future__ import annotations

import logging
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

from .config import Config, PALETTE_DEFAULT
from .visuals.motion import _font  # shared font resolution

log = logging.getLogger(__name__)
TW, TH = 1280, 720


def _pal(cfg: Config) -> dict:
    return {**PALETTE_DEFAULT, **(cfg.get_path("visuals.palette") or {})}


def _backdrop(image: Path | None, pal: dict) -> Image.Image:
    if image and Path(image).exists():
        img = Image.open(image).convert("RGB").resize((TW, TH), Image.LANCZOS)
        img = ImageEnhance.Color(img).enhance(0.35)
        img = ImageEnhance.Brightness(img).enhance(0.55)
        return img.filter(ImageFilter.GaussianBlur(1.2))
    return Image.new("RGB", (TW, TH), pal["bg"])


def number_layout(cfg: Config, figure: str, label: str, backdrop: Path | None, out: Path) -> Path:
    """Layout 1: one enormous figure. Highest CTR of the three in this niche."""
    pal = _pal(cfg)
    img = _backdrop(backdrop, pal)
    d = ImageDraw.Draw(img)
    vcfg = cfg.get("visuals", {})

    colour = pal["danger"] if figure.strip().startswith("-") else pal["accent"]
    f = _font(vcfg, "display", 300 if len(figure) <= 5 else 210)
    bb = d.textbbox((0, 0), figure, font=f)
    x = (TW - (bb[2] - bb[0])) / 2
    y = TH / 2 - (bb[3] - bb[1]) / 2 - 70
    # Heavy drop shadow so it survives a dark or busy backdrop.
    d.text((x + 7, y + 7), figure, font=f, fill="#000000")
    d.text((x, y), figure, font=f, fill=colour)

    fl = _font(vcfg, "display", 78)
    lines = textwrap.wrap(label.upper(), 24)[:2]
    ly = y + (bb[3] - bb[1]) + 60
    for line in lines:
        lb = d.textbbox((0, 0), line, font=fl)
        lx = (TW - (lb[2] - lb[0])) / 2
        d.text((lx + 4, ly + 4), line, font=fl, fill="#000000")
        d.text((lx, ly), line, font=fl, fill=pal["ink"])
        ly += 88

    img.save(out, quality=92)
    return out


def person_layout(cfg: Config, words: str, portrait: Path | None, out: Path) -> Path:
    """Layout 3: archival portrait left, max four words right."""
    pal = _pal(cfg)
    img = _backdrop(portrait, pal)
    d = ImageDraw.Draw(img)
    vcfg = cfg.get("visuals", {})

    d.rectangle([TW * 0.52, 0, TW, TH], fill=pal["bg"])
    d.rectangle([TW * 0.52 - 8, 0, TW * 0.52, TH], fill=pal["accent"])

    f = _font(vcfg, "display", 92)
    lines = textwrap.wrap(words.upper(), 12)[:4]
    y = (TH - len(lines) * 104) / 2
    for line in lines:
        d.text((TW * 0.56, y), line, font=f, fill=pal["ink"])
        y += 104

    img.save(out, quality=92)
    return out


def headline_layout(cfg: Config, words: str, figure: str, backdrop: Path | None,
                    out: Path) -> Path:
    """The channel's thumbnail: a bright picture of the subject on the right,
    a dark fade on the left carrying 2-4 huge words that name the story, and
    the key figure in an accent badge. Words + picture + number read as one
    idea at phone size; the old full-dark, figure-only layout did not say
    what the video was about."""
    from .visuals.motion import _fit

    pal = _pal(cfg)
    vcfg = cfg.get("visuals", {})
    if backdrop and Path(backdrop).exists():
        img = Image.open(backdrop).convert("RGB")
        # cover-crop to 16:9
        r = max(TW / img.width, TH / img.height)
        img = img.resize((int(img.width * r) + 1, int(img.height * r) + 1), Image.LANCZOS)
        left, top = (img.width - TW) // 2, (img.height - TH) // 2
        img = img.crop((left, top, left + TW, top + TH))
        img = ImageEnhance.Contrast(ImageEnhance.Color(img).enhance(1.15)).enhance(1.08)
    else:
        img = Image.new("RGB", (TW, TH), pal["surface"])

    # Left-to-right fade so the type always sits on dark.
    fade = Image.new("L", (TW, 1))
    for x in range(TW):
        fade.putpixel((x, 0), int(235 * max(0.0, 1 - x / (TW * 0.68)) ** 0.9))
    img.paste(Image.new("RGB", (TW, TH), "#06080B"), (0, 0), fade.resize((TW, TH)))
    d = ImageDraw.Draw(img)

    x0 = 56
    y = 70
    if figure:
        fb = _font(vcfg, "display", 120)
        bw = d.textlength(figure, font=fb)
        colour = pal["danger"] if figure.startswith("-") else pal["accent"]
        d.rectangle([x0 - 18, y - 6, x0 + bw + 22, y + 140], fill=colour)
        d.text((x0 + 2, y + 66), figure, font=fb, fill="#0B0E13", anchor="lm")
        y += 178

    start = 170
    while True:
        font, lines, size = _fit(d, vcfg, "display", words.upper(), TW * 0.6,
                                 start, 64, max_lines=3)
        line_h = int(size * 1.08)
        if y + line_h * len(lines) <= TH - 50 or size <= 64:
            break
        start = int(size * 0.92)
    if not figure:
        y = (TH - line_h * len(lines)) / 2
    for i, line in enumerate(lines):
        colour = pal["accent"] if (i == len(lines) - 1 and not figure) else pal["ink"]
        d.text((x0, y), line, font=font, fill=colour,
               stroke_width=max(3, size // 22), stroke_fill="#000000")
        y += line_h

    img.save(out, quality=92)
    return out


def generate(cfg: Config, meta: dict, backdrop: Path | None, out: Path) -> Path:
    figure = str(meta.get("thumb_figure", "")).strip()
    words = str(meta.get("thumb_words", meta.get("company", "")))[:40]
    # A figure the words already contain would be said twice.
    if figure and figure.lower().rstrip("m%") in words.lower():
        figure = ""
    return headline_layout(cfg, words, figure, backdrop, out)
