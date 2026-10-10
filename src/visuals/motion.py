"""Motion-graphics card renderer.

This is the part that makes the channel look like a studio rather than a
slideshow, and it is free. Every card is drawn to a PNG here; render.py adds
the movement with ffmpeg.

Design constraints come from channel/visual-identity.md: near-black ground,
one accent colour, red reserved for decline, grain over everything.
"""
from __future__ import annotations

import logging
import textwrap
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from ..config import ROOT, PALETTE_DEFAULT

log = logging.getLogger(__name__)
W, H = 1920, 1080
MARGIN = 140


# ---------------------------------------------------------------- type

def _font(cfg: dict, role: str, size: int) -> ImageFont.FreeTypeFont:
    path = ROOT / cfg.get("fonts", {}).get(role, "")
    if path.exists():
        return ImageFont.truetype(str(path), size)
    # Fall back to anything the box has rather than crashing a 3am CI run.
    for candidate in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ):
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default()


def _palette(cfg: dict) -> dict[str, str]:
    return {**PALETTE_DEFAULT, **(cfg.get("palette") or {})}


def _canvas(pal: dict) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGB", (W, H), pal["bg"])
    return img, ImageDraw.Draw(img)


def _caption(draw, pal, cfg, text: str) -> None:
    """Source caption, bottom left. Non-negotiable on charts and quotes."""
    if not text:
        return
    f = _font(cfg, "mono", 26)
    draw.text((MARGIN, H - 80), text.upper(), font=f, fill=pal["muted"])


def _grain(img: Image.Image, strength: float) -> Image.Image:
    if strength <= 0:
        return img
    import numpy as np

    arr = np.asarray(img).astype("int16")
    noise = np.random.default_rng().normal(0, strength * 255, arr.shape[:2])
    arr = arr + noise[..., None]
    return Image.fromarray(arr.clip(0, 255).astype("uint8"))


# ---------------------------------------------------------------- cards

def stat_reveal(spec: dict, cfg: dict, out: Path) -> Path:
    """One number, enormous, alone. The most effective card in the set."""
    pal = _palette(cfg)
    img, d = _canvas(pal)

    value = str(spec.get("value", "")).strip() or "—"
    # Red for decline, amber otherwise.
    colour = pal["danger"] if value.lstrip().startswith("-") or "−" in value else pal["accent"]

    # Shrink to fit rather than trusting a length heuristic — a long figure
    # silently running off the frame is the kind of bug nobody notices until
    # it is published.
    max_w = W - 2 * MARGIN
    size = 420
    f = _font(cfg, "display", size)
    while size > 90:
        bb = d.textbbox((0, 0), value, font=f)
        if (bb[2] - bb[0]) <= max_w:
            break
        size -= 20
        f = _font(cfg, "display", size)

    d.text((W / 2, H / 2 - 50), value, font=f, fill=colour, anchor="mm")
    num_h = d.textbbox((0, 0), value, font=f)[3]

    head = spec.get("headline", "")
    if head:
        fh = _font(cfg, "body", 46)
        y = H / 2 - 50 + num_h / 2 + 70
        for line in textwrap.wrap(head, 46)[:2]:
            d.text((W / 2, y), line, font=fh, fill=pal["ink"], anchor="mm")
            y += 58

    _caption(d, pal, cfg, spec.get("caption", ""))
    img = _grain(img, cfg.get("grain", 0.035))
    img.save(out)
    return out


def quote_card(spec: dict, cfg: dict, out: Path) -> Path:
    """A real quote, with attribution on screen. Never used for invented text."""
    pal = _palette(cfg)
    img, d = _canvas(pal)

    text = spec.get("headline") or spec.get("value") or ""
    f = _font(cfg, "body", 58)
    lines = textwrap.wrap(f'"{text}"', width=44)
    y = (H - len(lines) * 78) / 2 - 40
    d.rectangle([MARGIN - 40, y - 30, MARGIN - 32, y + len(lines) * 78], fill=pal["accent"])
    for line in lines:
        d.text((MARGIN, y), line, font=f, fill=pal["ink"])
        y += 78

    attr = spec.get("caption", "")
    if attr:
        fa = _font(cfg, "mono", 30)
        d.text((MARGIN, y + 40), f"— {attr}", font=fa, fill=pal["muted"])

    img = _grain(img, cfg.get("grain", 0.035))
    img.save(out)
    return out


def timeline(spec: dict, cfg: dict, out: Path) -> Path:
    """Horizontal compression of years. Amber marks the pivotal one."""
    pal = _palette(cfg)
    img, d = _canvas(pal)
    events = spec.get("events", [])[:6]
    if not events:
        return stat_reveal(spec, cfg, out)

    y = H // 2
    # Inset the rail so the first and last labels have room to centre on
    # their markers instead of running off the frame.
    inset = MARGIN + 120
    d.line([(inset, y), (W - inset, y)], fill=pal["muted"], width=3)
    step = (W - 2 * inset) / max(len(events) - 1, 1)
    fy = _font(cfg, "display", 42)
    fe = _font(cfg, "body", 30)

    for i, ev in enumerate(events):
        x = inset + i * step
        above = i % 2 == 0            # alternate sides, year and text together
        pivotal = bool(ev.get("pivotal"))
        colour = pal["accent"] if pivotal else pal["ink"]
        r = 16 if pivotal else 9
        d.ellipse([x - r, y - r, x + r, y + r], fill=colour)

        lines = textwrap.wrap(str(ev.get("event", "")), 20)[:3]
        if above:
            d.text((x, y - 78), str(ev.get("year", "")), font=fy, fill=colour, anchor="mm")
            ty = y - 78 - 46 - len(lines) * 36
        else:
            d.text((x, y + 78), str(ev.get("year", "")), font=fy, fill=colour, anchor="mm")
            ty = y + 78 + 40
        for line in lines:
            d.text((x, ty), line, font=fe, fill=pal["muted"], anchor="mm")
            ty += 36

    _caption(d, pal, cfg, spec.get("caption", ""))
    img = _grain(img, cfg.get("grain", 0.035))
    img.save(out)
    return out


def chart(spec: dict, cfg: dict, out: Path) -> Path:
    """Real data only. Always captioned with its source."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    pal = _palette(cfg)
    data = spec.get("chart") or {}
    xs, ys = data.get("x", []), data.get("y", [])
    if not xs or not ys or len(xs) != len(ys):
        log.warning("Chart spec incomplete; falling back to stat card")
        return stat_reveal(spec, cfg, out)

    fig, ax = plt.subplots(figsize=(W / 100, H / 100), dpi=100)
    fig.patch.set_facecolor(pal["bg"])
    ax.set_facecolor(pal["bg"])

    declining = len(ys) > 1 and ys[-1] < ys[0]
    colour = pal["danger"] if declining else pal["accent"]

    if data.get("kind") == "bar":
        ax.bar(xs, ys, color=colour, width=0.62)
    else:
        ax.plot(xs, ys, color=colour, linewidth=4.5, marker="o", markersize=9)
        ax.fill_between(range(len(xs)), ys, min(ys), color=colour, alpha=0.10)

    ax.set_title(data.get("title", ""), color=pal["ink"], fontsize=30, pad=34, loc="left")
    ax.set_ylabel(data.get("y_label", ""), color=pal["muted"], fontsize=20)
    ax.tick_params(colors=pal["muted"], labelsize=19)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("bottom", "left"):
        ax.spines[side].set_color(pal["muted"])
    ax.grid(axis="y", color=pal["surface"], linewidth=1.3)
    ax.set_axisbelow(True)

    if spec.get("caption"):
        fig.text(0.07, 0.035, spec["caption"].upper(), color=pal["muted"],
                 fontsize=14, family="monospace")

    fig.tight_layout(rect=(0.03, 0.06, 0.98, 0.97))
    fig.savefig(out, facecolor=pal["bg"])
    plt.close(fig)

    img = _grain(Image.open(out).convert("RGB"), cfg.get("grain", 0.035))
    img.save(out)
    return out


def document_card(spec: dict, cfg: dict, out: Path) -> Path:
    """A filing excerpt with the one line that matters picked out in amber."""
    pal = _palette(cfg)
    img, d = _canvas(pal)
    d.rectangle([MARGIN, 150, W - MARGIN, H - 190], fill=pal["surface"])

    f = _font(cfg, "mono", 32)
    body = str(spec.get("headline", ""))
    highlight = str(spec.get("value", "")).strip()
    y = 220
    for line in textwrap.wrap(body, 62)[:14]:
        is_hit = highlight and highlight.lower() in line.lower()
        if is_hit:
            bb = d.textbbox((MARGIN + 50, y), line, font=f)
            d.rectangle([bb[0] - 10, bb[1] - 8, bb[2] + 10, bb[3] + 8], fill=pal["accent"])
            d.text((MARGIN + 50, y), line, font=f, fill=pal["bg"])
        else:
            d.text((MARGIN + 50, y), line, font=f, fill=pal["ink"])
        y += 48

    _caption(d, pal, cfg, spec.get("caption", ""))
    img = _grain(img, cfg.get("grain", 0.035))
    img.save(out)
    return out


def title_card(title: str, subtitle: str, cfg: dict, out: Path) -> Path:
    pal = _palette(cfg)
    img, d = _canvas(pal)
    f = _font(cfg, "display", 150)
    lines = textwrap.wrap(title.upper(), 18)
    y = (H - len(lines) * 170) / 2
    for line in lines:
        bb = d.textbbox((0, 0), line, font=f)
        d.text(((W - (bb[2] - bb[0])) / 2, y), line, font=f, fill=pal["ink"])
        y += 170
    if subtitle:
        fs = _font(cfg, "mono", 36)
        sb = d.textbbox((0, 0), subtitle.upper(), font=fs)
        d.text(((W - (sb[2] - sb[0])) / 2, y + 30), subtitle.upper(),
               font=fs, fill=pal["accent"])
    img = _grain(img, cfg.get("grain", 0.035))
    img.save(out)
    return out


RENDERERS = {
    "stat_reveal": stat_reveal,
    "quote_card": quote_card,
    "timeline": timeline,
    "chart": chart,
    "document": document_card,
}


def render(shot: dict, cfg: dict, out: Path) -> Path | None:
    fn = RENDERERS.get(shot.get("visual_type", ""))
    if not fn:
        return None
    return fn(shot.get("visual_spec", {}) or {}, cfg, out)
