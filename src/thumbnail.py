"""Thumbnail generation: three fixed layouts, rotated.

Deliberately not AI-generated. A thumbnail is typography and one image; models
still cannot set type, and legible type is most of the click-through rate.
"""
from __future__ import annotations

import logging
import re
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


DOWN = re.compile(r"\b(fall|falls|fell|drop|drops|dropped|crash|crashes|plunge|plunges|slump|"
                  r"cut|cuts|slash|slashes|loss|losses|lose|down|bear|sink|sinks|tumble|"
                  r"layoffs?|ban|bans|blocks?)\b", re.I)
UP = re.compile(r"\b(rise|rises|rose|jump|jumps|surge|surges|soar|soars|rally|hike|hikes|"
                r"gain|gains|up|bull|record|boom|doubles?|raises?)\b", re.I)


def direction(*texts: str) -> str:
    """'down', 'up' or '': which way the story's number moved, from the
    figure's sign or the words around it."""
    text = " ".join(t for t in texts if t)
    if re.search(r"(^|\s)[-−]\d", text) or DOWN.search(text):
        return "down"
    if re.search(r"(^|\s)\+\d", text) or UP.search(text):
        return "up"
    return ""


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2}


def _trend(img: Image.Image, way: str, colour: str) -> None:
    """A thick zig-zag market line with an arrowhead across the right half:
    reads as 'fell' or 'rose' at phone size, without a word."""
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    xs = [0.58, 0.66, 0.72, 0.80, 0.86, 0.94]
    ys = [0.22, 0.34, 0.30, 0.52, 0.48, 0.80] if way == "down" else \
         [0.80, 0.66, 0.70, 0.46, 0.50, 0.20]
    pts = [(x * TW, y * TH) for x, y in zip(xs, ys)]
    for width, alpha in ((46, 70), (26, 255)):            # glow, then the line
        d.line(pts, fill=colour + f"{alpha:02x}", width=width, joint="curve")
    (x1, y1), (x2, y2) = pts[-2], pts[-1]
    import math
    ang = math.atan2(y2 - y1, x2 - x1)
    head = [(x2 + 30 * math.cos(ang), y2 + 30 * math.sin(ang)),
            (x2 + 62 * math.cos(ang + 2.5), y2 + 62 * math.sin(ang + 2.5)),
            (x2 + 62 * math.cos(ang - 2.5), y2 + 62 * math.sin(ang - 2.5))]
    d.polygon(head, fill=colour)
    img.paste(layer, (0, 0), layer)


def story_layout(cfg: Config, words: str, figure: str, entity: str, way: str,
                 backdrop: Path | None, out: Path) -> Path:
    """The channel's thumbnail, one idea at phone size:
    - who/where in a small accent tag (the context),
    - ONE focal element on the left: a huge figure, or 2-3 huge words,
    - the subject's picture behind, darkened, with a red falling or green
      rising market line over the right half when the story has a direction,
    - lots of plain dark space, and an accent bar along the bottom (brand).
    Text in Tamil (or another non-Latin script) is drawn with the channel's
    caption font when Pillow can shape it."""
    from .captions import native_script
    from .visuals.motion import _fit

    pal = _pal(cfg)
    vcfg = cfg.get("visuals", {})
    up_colour, down_colour = "#2FBF71", pal["danger"]
    if backdrop and Path(backdrop).exists():
        img = Image.open(backdrop).convert("RGB")
        r = max(TW / img.width, TH / img.height)
        img = img.resize((int(img.width * r) + 1, int(img.height * r) + 1), Image.LANCZOS)
        left, top = (img.width - TW) // 2, (img.height - TH) // 2
        img = img.crop((left, top, left + TW, top + TH))
        # Muted and dark: context, not a second focal point.
        img = ImageEnhance.Brightness(ImageEnhance.Color(img).enhance(0.6)).enhance(
            0.55 if way else 0.8).filter(ImageFilter.GaussianBlur(3))
    else:
        img = Image.new("RGB", (TW, TH), pal["surface"])
    fade = Image.new("L", (TW, 1))
    for x in range(TW):
        fade.putpixel((x, 0), int(245 * max(0.0, 1 - x / (TW * 0.62)) ** 0.8))
    img.paste(Image.new("RGB", (TW, TH), "#06080B"), (0, 0), fade.resize((TW, TH)))
    if way:
        _trend(img, way, down_colour if way == "down" else up_colour)
    d = ImageDraw.Draw(img)

    def font_for(text: str, size: int):
        native = any(ord(c) > 0x024F for c in text) and native_script(cfg)
        return _font(vcfg, "caption" if native else "display", size), native

    def mixed(text: str, size: int, width: float, height: float, max_lines: int = 3):
        """Lines of (word, font) for text mixing scripts ("உங்கள் EMI உயரும்"):
        each word in the font that has its letters, shrunk until it fits."""
        while True:
            lines, cur, cur_w = [], [], 0.0
            for w in text.split():
                f = _font(vcfg, "caption" if any(ord(c) > 0x024F for c in w) else "display",
                          size)
                ww = d.textlength(w, font=f)
                gap = d.textlength(" ", font=f) if cur else 0
                if cur and cur_w + gap + ww > width:
                    lines.append(cur)
                    cur, cur_w, gap = [], 0.0, 0
                cur.append((w, f, ww))
                cur_w += gap + ww
            if cur:
                lines.append(cur)
            fits = len(lines) <= max_lines and len(lines) * size * 1.3 <= height \
                and all(sum(x[2] for x in ln) <= width for ln in lines)
            if fits or size <= 60:
                return lines, size
            size = int(size * 0.9)

    x0, y = 60, 64
    if entity:
        f, _ = font_for(entity, 58)
        tw = d.textlength(entity.upper(), font=f)
        d.rectangle([x0 - 16, y - 8, x0 + tw + 18, y + 74], fill=pal["accent"])
        d.text((x0 + 1, y + 33), entity.upper(), font=f, fill="#0B0E13", anchor="lm")
        y += 118

    focal = pal["accent"] if not way else (down_colour if way == "down" else up_colour)
    if figure:
        f = _font(vcfg, "display", 280 if len(figure) <= 4 else 210)
        d.text((x0 - 6, y - 10), figure, font=f, fill=focal,
               stroke_width=10, stroke_fill="#000000")
        y += int(f.size * 1.02)
        words = " ".join(words.split()[:3])
        f2, native = font_for(words, 86)
        d.text((x0, y), words if native else words.upper(), font=f2, fill=pal["ink"],
               stroke_width=5, stroke_fill="#000000")
    elif any(ord(c) > 0x024F for c in words) and native_script(cfg):
        lines, size = mixed(words, 150, TW * 0.56, TH - y - 40)
        line_h = int(size * 1.3)
        for i, line in enumerate(lines):
            colour = focal if i == len(lines) - 1 else pal["ink"]
            x = x0
            for w, f, ww in line:
                d.text((x, y), w if any(ord(c) > 0x024F for c in w) else w.upper(), font=f,
                       fill=colour, stroke_width=max(4, size // 20), stroke_fill="#000000")
                x += ww + d.textlength(" ", font=f)
            y += line_h
    else:
        f0, native = font_for(words, 10)
        text = words if native else words.upper()
        role = "caption" if native else "display"
        start = 165
        while True:
            font, lines, size = _fit(d, vcfg, role, text, TW * 0.56, start, 70, max_lines=3)
            line_h = int(size * (1.25 if native else 1.06))
            if y + line_h * len(lines) <= TH - 60 or size <= 70:
                break
            start = int(size * 0.92)
        for i, line in enumerate(lines):
            colour = focal if i == len(lines) - 1 else pal["ink"]
            d.text((x0, y), line, font=font, fill=colour,
                   stroke_width=max(4, size // 20), stroke_fill="#000000")
            y += line_h

    d.rectangle([0, TH - 14, TW, TH], fill=pal["accent"])
    img.save(out, quality=92)
    return out


def generate(cfg: Config, meta: dict, backdrop: Path | None, out: Path) -> Path:
    """Thumbnail and title are two different persuasion surfaces: nothing the
    title already says (its figure, most of its words) goes on the image."""
    figure = str(meta.get("thumb_figure", "")).strip()
    words = str(meta.get("thumb_words", meta.get("company", "")))[:40]
    title = str(meta.get("title", ""))
    entity = str(meta.get("thumb_entity", "")).strip()[:16]
    if figure and re.sub(r"[^0-9.]", "", figure) and \
            re.sub(r"[^0-9.]", "", figure) in title.replace(",", ""):
        figure = ""                                   # the title carries it
    # A figure the words already contain would be said twice.
    if figure and figure.lower().rstrip("m%") in words.lower():
        figure = ""
    if entity and entity.lower() in words.lower():
        entity = ""
    way = meta.get("direction") or direction(figure, words, title)
    return story_layout(cfg, words, figure, entity, way, backdrop, out)
