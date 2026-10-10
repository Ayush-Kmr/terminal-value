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


def generate(cfg: Config, meta: dict, backdrop: Path | None, out: Path) -> Path:
    """Rotate layouts by episode number so the channel page has rhythm."""
    figure = str(meta.get("thumb_figure", "")).strip()
    words = str(meta.get("thumb_words", meta.get("company", "")))[:40]

    if figure:
        return number_layout(cfg, figure, words, backdrop, out)
    return person_layout(cfg, words, backdrop, out)
