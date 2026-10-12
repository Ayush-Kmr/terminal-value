"""Motion-graphics card renderer.

This is the part that makes the channel look like a studio rather than a
slideshow, and it is free. Each card is built as a backdrop plus a stack of
layers; every layer has an entrance (rise, slide, wipe, count-up) and a start
time, so the elements arrive in sequence instead of sitting on a flat frame.

A card is written twice: the finished frame as a PNG (used for thumbnails and
as a fallback), and the entrance as a short frame sequence next to it
(`shot_003.frames/`). render.py plays the sequence and then holds the last
frame with a slow push for the rest of the shot.

Cards are laid out for whatever `resolution` they are given, so the same code
draws 16:9 for long videos and 9:16 for Shorts.
"""
from __future__ import annotations

import logging
import math
import re
import shutil
from pathlib import Path
from typing import Any, Callable

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from ..config import ROOT, PALETTE_DEFAULT

log = logging.getLogger(__name__)

RISE, SLIDE, WIPE, FADE = "rise", "slide", "wipe", "fade"


# ---------------------------------------------------------------- type

def _font(cfg: dict, role: str, size: int, weight: str | None = None) -> ImageFont.FreeTypeFont:
    path = ROOT / cfg.get("fonts", {}).get(role, "")
    if path.exists():
        font = ImageFont.truetype(str(path), max(int(size), 8))
        if weight:
            try:
                font.set_variation_by_name(weight)
            except Exception:  # noqa: BLE001 - a static font has one weight
                pass
        return font
    # Fall back to anything the box has rather than crashing a 3am CI run.
    for candidate in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ):
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, max(int(size), 8))
    return ImageFont.load_default()


def _palette(cfg: dict) -> dict[str, str]:
    return {**PALETTE_DEFAULT, **(cfg.get("palette") or {})}


def _rgb(colour: str) -> tuple[int, int, int]:
    colour = colour.lstrip("#")
    return tuple(int(colour[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_w: float, max_lines: int) -> list[str]:
    """Wrap to a pixel width. Character counts misjudge wide display faces."""
    lines: list[str] = []
    line = ""
    for word in str(text).split():
        trial = f"{line} {word}".strip()
        if draw.textlength(trial, font=font) <= max_w or not line:
            line = trial
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip(" ,;:") + "…"
    return lines


def _fit(draw, cfg: dict, role: str, text: str, max_w: float, start: int, floor: int,
         max_lines: int = 1, weight: str | None = None):
    """Largest size at which the text fits in max_lines lines of max_w."""
    size = start
    while True:
        font = _font(cfg, role, size, weight)
        lines = _wrap(draw, text, font, max_w, 99)
        widest = max((draw.textlength(ln, font=font) for ln in lines), default=0)
        if (len(lines) <= max_lines and widest <= max_w) or size <= floor:
            return font, _wrap(draw, text, font, max_w, max_lines), size
        size = int(size * 0.92)


def _ease(p: float) -> float:
    p = min(max(p, 0.0), 1.0)
    return 1 - (1 - p) ** 3


# ---------------------------------------------------------------- scene

_backdrops: dict[tuple, Any] = {}


def _backdrop(w: int, h: int, pal: dict, tint: str) -> Image.Image:
    """Deep gradient with two soft lights and a faint grid. A flat near-black
    fill is what made the old cards read as empty slides."""
    import numpy as np

    key = (w, h, pal["bg"], tint)
    if key not in _backdrops:
        yy, xx = np.mgrid[0:h, 0:w].astype("float32")
        u = min(w, h)
        base = np.array(_rgb(pal["bg"]), dtype="float32")
        lift = np.array(_rgb(pal["surface"]), dtype="float32") * 1.5
        t = (yy / h)[..., None]
        img = lift * (1 - t) ** 1.6 + base * (1 - (1 - t) ** 1.6)

        def light(cx, cy, radius, colour, strength):
            d2 = ((xx - cx * w) ** 2 + (yy - cy * h) ** 2) / (radius * u) ** 2
            return np.exp(-d2)[..., None] * np.array(_rgb(colour), dtype="float32") * strength

        img += light(0.82, 0.16, 0.62, tint, 0.20)
        img += light(0.10, 0.92, 0.70, "#2B5C9E", 0.22)

        step = max(int(u / 11), 8)
        grid = ((xx.astype("int32") % step == 0) | (yy.astype("int32") % step == 0))
        img += grid[..., None] * 5.0

        d = np.sqrt(((xx - w / 2) / (w / 2)) ** 2 + ((yy - h / 2) / (h / 2)) ** 2)
        img *= (1 - 0.30 * np.clip(d - 0.55, 0, 1) ** 1.5)[..., None]
        _backdrops[key] = img
    return _backdrops[key]


class Scene:
    """A backdrop and an ordered stack of animated layers."""

    def __init__(self, cfg: dict, tint: str | None = None, transparent: bool = False):
        self.cfg = cfg
        self.pal = _palette(cfg)
        self.W, self.H = (int(v) for v in cfg.get("resolution", [1920, 1080]))
        self.u = min(self.W, self.H) / 1080
        self.portrait = self.H > self.W
        self.fps = int(cfg.get("fps", 30))
        self.tint = tint or self.pal["accent"]
        self.transparent = transparent
        # Shorts: the app draws its own title, buttons and caption over the
        # top, the right edge and the bottom fifth. Keep content clear of them.
        if self.portrait:
            self.M = self.px(84)
            self.top, self.bottom = int(self.H * 0.275), int(self.H * 0.78)
        else:
            self.M = self.px(130)
            self.top, self.bottom = self.px(170), self.H - self.px(150)
        self.left, self.right = self.M, self.W - self.M
        self.layers: list[dict] = []
        self.static = Image.new("RGBA", (self.W, self.H), (0, 0, 0, 0))
        self.sd = ImageDraw.Draw(self.static)

    # -- helpers
    def px(self, v: float) -> int:
        return int(round(v * self.u))

    def font(self, role: str, size: float, weight: str | None = None):
        return _font(self.cfg, role, self.px(size), weight)

    @property
    def cx(self) -> float:
        return self.W / 2

    @property
    def cy(self) -> float:
        return (self.top + self.bottom) / 2

    def layer(self, at: float = 0.0, move: str = RISE, dur: float = 0.55,
              redraw: Callable[[float], Image.Image] | None = None):
        img = Image.new("RGBA", (self.W, self.H), (0, 0, 0, 0))
        self.layers.append({"img": img, "at": at, "move": move, "dur": dur, "redraw": redraw})
        return img, ImageDraw.Draw(img)

    # -- furniture
    def kicker(self, label: str, colour: str | None = None) -> None:
        """Small label top-left that names what kind of card this is."""
        if not label:
            return
        colour = colour or self.tint
        _, d = self.layer(0.05, SLIDE, 0.45)
        f = self.font("mono", 30, "Bold")
        y = self.top - self.px(86)
        s = self.px(16)
        d.rectangle([self.left, y + self.px(8), self.left + s, y + self.px(8) + s], fill=colour)
        d.text((self.left + s + self.px(18), y), label.upper(), font=f, fill=self.pal["ink"])

    def footer(self, caption: str = "") -> None:
        """Source line bottom-left, channel name bottom-right. Static."""
        d, y = self.sd, self.bottom + self.px(64)
        d.line([(self.left, y - self.px(26)), (self.right, y - self.px(26))],
               fill=(255, 255, 255, 26), width=max(self.px(2), 1))
        f = self.font("mono", 24)
        name = str(self.cfg.get("wordmark", "")).upper()
        if name and not self.portrait:
            d.text((self.right, y), name, font=f, fill=self.pal["muted"], anchor="ra")
        if caption:
            room = self.right - self.left - (d.textlength(name, font=f) + self.px(60)
                                             if name and not self.portrait else 0)
            text = _wrap(d, f"SOURCE · {caption}".upper(), f, room, 1)[0]
            d.text((self.left, y), text, font=f, fill=self.pal["muted"])

    def glow(self, layer: Image.Image, radius: float, strength: float) -> Image.Image:
        """A soft halo of the layer's own colour, to sit behind it."""
        blur = layer.filter(ImageFilter.GaussianBlur(self.px(radius)))
        blur.putalpha(blur.getchannel("A").point(lambda a: int(a * strength)))
        return blur

    # -- output
    def _base(self) -> Image.Image:
        if self.transparent:
            return Image.alpha_composite(
                Image.new("RGBA", (self.W, self.H), (0, 0, 0, 0)), self.static)
        import numpy as np

        arr = _backdrop(self.W, self.H, self.pal, self.tint)
        strength = float(self.cfg.get("grain", 0.035))
        if strength > 0:
            noise = np.random.default_rng().normal(0, strength * 160, arr.shape[:2])
            arr = arr + noise[..., None]
        base = Image.fromarray(arr.clip(0, 255).astype("uint8")).convert("RGBA")
        return Image.alpha_composite(base, self.static)

    def _frame(self, base: Image.Image, t: float) -> Image.Image:
        frame = base.copy()
        for ly in self.layers:
            p = _ease((t - ly["at"]) / ly["dur"])
            if p <= 0:
                continue
            img, box = ly["crop"], ly["box"]
            if ly["redraw"] and p < 1:
                full = ly["redraw"](p)
                box = full.getbbox()
                if not box:
                    continue
                img = full.crop(box)
            x, y = box[0], box[1]
            if ly["move"] == WIPE:
                if p < 1:
                    img = img.crop((0, 0, max(int(img.width * p), 1), img.height))
            else:
                if ly["move"] == RISE:
                    y += int((1 - p) * self.px(46))
                elif ly["move"] == SLIDE:
                    x -= int((1 - p) * self.px(70))
                if p < 1 and not ly["redraw"]:
                    img = img.copy()
                    img.putalpha(img.getchannel("A").point(lambda a, p=p: int(a * p)))
            frame.alpha_composite(img, (max(x, 0), max(y, 0)))
        return frame

    def save(self, out: Path) -> Path:
        """Write the finished frame to `out` and the entrance frames beside it."""
        for ly in self.layers:
            ly["box"] = ly["img"].getbbox() or (0, 0, 1, 1)
            ly["crop"] = ly["img"].crop(ly["box"])
        base = self._base()
        final = self._frame(base, 99.0)
        (final if self.transparent else final.convert("RGB")).save(out)

        frames = out.with_suffix(".frames")
        shutil.rmtree(frames, ignore_errors=True)
        frames.mkdir(parents=True)
        length = max((ly["at"] + ly["dur"] for ly in self.layers), default=0.0)
        n = min(int(math.ceil(length * self.fps)) + 1, self.fps * 3)
        for i in range(n):
            frame = final if i == n - 1 else self._frame(base, i / self.fps)
            (frame if self.transparent else frame.convert("RGB")).save(
                frames / f"f_{i:03d}.png", compress_level=1)
        return out


def frames_dir(asset: Path) -> Path | None:
    """The entrance sequence for a card, if it has one."""
    d = Path(asset).with_suffix(".frames")
    return d if d.is_dir() and any(d.iterdir()) else None


# ---------------------------------------------------------------- cards

_NUMBER = re.compile(r"^(\D*?)(\d[\d,]*(?:\.\d+)?)(.*)$", re.S)


def _counter(value: str) -> Callable[[float], str] | None:
    """A function giving the figure as it counts up to `value`, or None when
    the value is not something that should count (a year, a name)."""
    m = _NUMBER.match(value)
    if not m:
        return None
    prefix, digits, suffix = m.groups()
    plain = digits.replace(",", "")
    target = float(plain)
    if target == 0 or (not prefix.strip() and not suffix.strip() and 1800 <= target <= 2100
                       and "." not in plain):
        return None
    decimals = len(plain.split(".")[1]) if "." in plain else 0
    commas = "," in digits

    def at(p: float) -> str:
        n = target * p
        body = f"{n:,.{decimals}f}" if commas else f"{n:.{decimals}f}"
        return f"{prefix}{body}{suffix}"
    return at


def stat_reveal(spec: dict, cfg: dict, out: Path) -> Path:
    """One number, enormous, counting up. The most effective card in the set."""
    value = str(spec.get("value", "")).strip() or "—"
    pal = _palette(cfg)
    falling = value.startswith("-") or "−" in value
    colour = pal["danger"] if falling else pal["accent"]
    s = Scene(cfg, tint=colour)
    s.kicker("The number", colour)
    s.footer(spec.get("caption", ""))

    max_w = s.right - s.left
    font, lines, size = _fit(s.sd, cfg, "display", value, max_w,
                             s.px(300 if s.portrait else 400), s.px(96),
                             max_lines=2 if s.portrait else 1)
    line_h = int(size * 1.02)
    head = str(spec.get("headline", "")).strip()
    fh = s.font("body", 62 if s.portrait else 58, "SemiBold")
    head_lines = _wrap(s.sd, head, fh, max_w * 0.92, 3) if head else []
    head_h = len(head_lines) * s.px(78)
    block = len(lines) * line_h + (s.px(96) + head_h if head_lines else 0)
    y0 = s.cy - block / 2

    def draw_value(text_lines: list[str]) -> Image.Image:
        img = Image.new("RGBA", (s.W, s.H), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        for i, ln in enumerate(text_lines):
            d.text((s.cx, y0 + i * line_h + line_h / 2), ln, font=font, fill=colour, anchor="mm")
        return img

    crisp = draw_value(lines)
    halo, _ = s.layer(0.75, FADE, 0.7)
    halo.alpha_composite(s.glow(crisp, 38, 0.55))

    count = _counter(value) if len(lines) == 1 else None
    num, _ = s.layer(0.15, FADE, 1.0 if count else 0.5,
                     redraw=(lambda p: draw_value([count(p)])) if count else None)
    num.alpha_composite(crisp)

    y = y0 + len(lines) * line_h + s.px(48)
    _, d = s.layer(0.7, WIPE, 0.45)
    d.rectangle([s.cx - s.px(90), y, s.cx + s.px(90), y + s.px(8)], fill=colour)
    y += s.px(62)
    for i, ln in enumerate(head_lines):
        _, d = s.layer(0.85 + 0.1 * i, RISE)
        d.text((s.cx, y), ln, font=fh, fill=pal["ink"], anchor="ma")
        y += s.px(78)
    return s.save(out)


def quote_card(spec: dict, cfg: dict, out: Path) -> Path:
    """A real quote, with attribution on screen. Never used for invented text."""
    s = Scene(cfg)
    pal = s.pal
    s.kicker("On the record")
    s.footer()
    text = str(spec.get("headline") or spec.get("value") or "").strip().strip('"“”')
    max_w = (s.right - s.left) * (1.0 if s.portrait else 0.86)
    font, lines, size = _fit(s.sd, cfg, "body", text, max_w, s.px(92), s.px(50),
                             max_lines=7 if s.portrait else 4, weight="Bold")
    line_h = int(size * 1.28)
    attr = str(spec.get("caption", "")).strip()
    block = len(lines) * line_h + (s.px(96) if attr else 0)
    y = s.cy - block / 2 + s.px(40)
    x = s.left + (0 if s.portrait else s.px(40))

    mark, d = s.layer(0.1, FADE, 0.6)
    d.text((x - s.px(8), y - s.px(250)), "“", font=s.font("display", 330), fill=pal["accent"])
    for i, ln in enumerate(lines):
        _, d = s.layer(0.3 + 0.13 * i, RISE)
        d.text((x, y), ln, font=font, fill=pal["ink"])
        y += line_h
    if attr:
        t = 0.45 + 0.13 * len(lines)
        _, d = s.layer(t, WIPE, 0.4)
        d.rectangle([x, y + s.px(46), x + s.px(64), y + s.px(52)], fill=pal["accent"])
        _, d = s.layer(t + 0.1, SLIDE)
        fa = s.font("mono", 32, "Bold")
        d.text((x + s.px(86), y + s.px(28)), _wrap(d, attr.upper(), fa, max_w - s.px(90), 1)[0],
               font=fa, fill=pal["accent"])
    return s.save(out)


def timeline(spec: dict, cfg: dict, out: Path) -> Path:
    """Events in order along a rail. Amber marks the pivotal one."""
    events = [e for e in (spec.get("events") or []) if isinstance(e, dict)][:6]
    if not events:
        return stat_reveal(spec, cfg, out)
    s = Scene(cfg)
    pal = s.pal
    s.kicker("Timeline")
    s.footer(spec.get("caption", ""))
    fy = s.font("display", 54)
    fe = s.font("body", 38 if s.portrait else 34, "Medium")

    if s.portrait:
        # Vertical rail down the left, events stacked beside it.
        x = s.left + s.px(20)
        step = (s.bottom - s.top) / len(events)
        _, d = s.layer(0.1, FADE, 0.5)
        d.line([(x, s.top + step * 0.2), (x, s.bottom - step * 0.45)],
               fill=pal["muted"], width=s.px(4))
        for i, ev in enumerate(events):
            y = s.top + step * (i + 0.2)
            hot = bool(ev.get("pivotal"))
            colour = pal["accent"] if hot else pal["ink"]
            r = s.px(20 if hot else 12)
            _, d = s.layer(0.25 + 0.16 * i, SLIDE)
            d.ellipse([x - r, y - r, x + r, y + r], fill=colour)
            d.text((x + s.px(54), y), str(ev.get("year", "")), font=fy, fill=colour, anchor="lm")
            ty = y + s.px(40)
            for ln in _wrap(d, str(ev.get("event", "")), fe, s.right - x - s.px(54), 2):
                d.text((x + s.px(54), ty), ln, font=fe, fill=pal["muted"])
                ty += s.px(48)
        return s.save(out)

    y = int(s.cy)
    inset = s.left + s.px(130)
    span = (s.W - 2 * inset) / max(len(events) - 1, 1)
    _, d = s.layer(0.1, WIPE, 0.9)
    d.line([(inset, y), (s.W - inset, y)], fill=pal["muted"], width=s.px(4))
    col_w = min(span, s.px(420)) - s.px(30)
    for i, ev in enumerate(events):
        x = inset + i * span
        hot = bool(ev.get("pivotal"))
        colour = pal["accent"] if hot else pal["ink"]
        r = s.px(20 if hot else 11)
        _, d = s.layer(0.25 + 0.16 * i, RISE)
        d.ellipse([x - r, y - r, x + r, y + r], fill=colour)
        lines = _wrap(d, str(ev.get("event", "")), fe, col_w, 3)
        if i % 2 == 0:                # alternate sides, year and text together
            d.text((x, y - s.px(84)), str(ev.get("year", "")), font=fy, fill=colour, anchor="mm")
            ty = y - s.px(84) - s.px(56) - len(lines) * s.px(44)
        else:
            d.text((x, y + s.px(84)), str(ev.get("year", "")), font=fy, fill=colour, anchor="mm")
            ty = y + s.px(84) + s.px(44)
        for ln in lines:
            d.text((x, ty), ln, font=fe, fill=pal["muted"], anchor="ma")
            ty += s.px(44)
    return s.save(out)


def chart(spec: dict, cfg: dict, out: Path) -> Path:
    """Real data only. Always captioned with its source. Drawn left to right."""
    import io

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    data = spec.get("chart") or {}
    xs, ys = data.get("x", []), data.get("y", [])
    if not xs or not ys or len(xs) != len(ys):
        log.warning("Chart spec incomplete; falling back to stat card")
        return stat_reveal(spec, cfg, out)

    pal = _palette(cfg)
    declining = len(ys) > 1 and ys[-1] < ys[0]
    colour = pal["danger"] if declining else pal["accent"]
    s = Scene(cfg, tint=colour)
    s.kicker("The data", colour)
    s.footer(spec.get("caption", ""))

    title = str(data.get("title", "")).strip()
    ft = s.font("body", 56, "Bold")
    y = s.top
    for i, ln in enumerate(_wrap(s.sd, title, ft, s.right - s.left, 2)):
        _, d = s.layer(0.15 + 0.1 * i, RISE)
        d.text((s.left, y), ln, font=ft, fill=pal["ink"])
        y += s.px(72)
    y += s.px(20)

    w, h = s.right - s.left, s.bottom - y
    k = s.u
    fig, ax = plt.subplots(figsize=(w / 100, h / 100), dpi=100)
    fig.patch.set_alpha(0)
    ax.set_facecolor("none")
    if data.get("kind") == "bar":
        ax.bar([str(x) for x in xs], ys, color=colour, width=0.62)
    else:
        ax.plot(range(len(xs)), ys, color=colour, linewidth=6 * k, marker="o",
                markersize=13 * k, solid_capstyle="round")
        ax.fill_between(range(len(xs)), ys, min(ys), color=colour, alpha=0.14)
        ax.set_xticks(range(len(xs)))
        ax.set_xticklabels([str(x) for x in xs])
    ax.set_ylabel(data.get("y_label", ""), color=pal["muted"], fontsize=22 * k)
    ax.tick_params(colors=pal["muted"], labelsize=21 * k, length=0, pad=10 * k)
    if s.portrait and len(xs) > 5:
        plt.setp(ax.get_xticklabels(), rotation=45, ha="right")
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(pal["muted"])
    ax.grid(axis="y", color="#FFFFFF", alpha=0.08, linewidth=1.4 * k)
    ax.set_axisbelow(True)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", transparent=True)
    plt.close(fig)

    plot = Image.open(buf).convert("RGBA")
    layer, _ = s.layer(0.45, WIPE, 1.3)
    layer.alpha_composite(plot, (s.left, int(y)))
    return s.save(out)


def document_card(spec: dict, cfg: dict, out: Path) -> Path:
    """A filing excerpt with the one line that matters picked out in amber."""
    s = Scene(cfg)
    pal = s.pal
    s.kicker("From the record")
    s.footer(spec.get("caption", ""))
    body = str(spec.get("headline", "")).strip()
    highlight = str(spec.get("value", "")).strip()
    box = [s.left, s.top, s.right, s.bottom]
    pad = s.px(64)

    panel, d = s.layer(0.0, FADE, 0.4)
    d.rounded_rectangle(box, radius=s.px(26), fill=_rgb(pal["surface"]) + (235,),
                        outline=(255, 255, 255, 30), width=max(s.px(2), 1))
    d.rounded_rectangle([box[0], box[1], box[0] + s.px(10), box[3]], radius=s.px(5),
                        fill=pal["accent"])
    inner_w = box[2] - box[0] - 2 * pad

    if len(body) < 90:
        # A short excerpt is set large; a few words of small type in the
        # corner of an empty panel reads as a rendering fault.
        font, lines, size = _fit(s.sd, cfg, "display", body.upper(), inner_w,
                                 s.px(104), s.px(54), max_lines=5 if s.portrait else 3)
        line_h = int(size * 1.16)
        extra = highlight and highlight.lower() not in body.lower()
        y = (box[1] + box[3]) / 2 - (len(lines) * line_h + (s.px(90) if extra else 0)) / 2
        for i, ln in enumerate(lines):
            _, d = s.layer(0.25 + 0.13 * i, RISE)
            d.text((box[0] + pad, y), ln, font=font, fill=pal["ink"])
            y += line_h
        if extra:
            _, d = s.layer(0.4 + 0.13 * len(lines), SLIDE)
            fm = s.font("mono", 46, "Bold")
            d.text((box[0] + pad, y + s.px(26)), _wrap(d, highlight, fm, inner_w, 1)[0],
                   font=fm, fill=pal["accent"])
        return s.save(out)

    font, lines, size = _fit(s.sd, cfg, "mono", body, inner_w, s.px(54), s.px(26),
                             max_lines=16 if s.portrait else 11)
    line_h = int(size * 1.5)
    y = (box[1] + box[3]) / 2 - len(lines) * line_h / 2
    text, td = s.layer(0.25, FADE, 0.6)
    hit_done = False
    for ln in lines:
        is_hit = bool(highlight) and not hit_done and (
            highlight.lower() in ln.lower() or ln.lower().strip(" …") in highlight.lower())
        if is_hit and len(ln) > 8:
            bb = td.textbbox((box[0] + pad, y), ln, font=font)
            _, hd = s.layer(0.9, WIPE, 0.5)
            hd.rectangle([bb[0] - s.px(10), bb[1] - s.px(8), bb[2] + s.px(10), bb[3] + s.px(8)],
                         fill=pal["accent"])
            hd.text((box[0] + pad, y), ln, font=font, fill=pal["bg"])
            hit_done = True
        td.text((box[0] + pad, y), ln, font=font, fill=pal["ink"])
        y += line_h
    return s.save(out)


def title_card(title: str, subtitle: str, cfg: dict, out: Path) -> Path:
    s = Scene(cfg)
    pal = s.pal
    s.footer()
    font, lines, size = _fit(s.sd, cfg, "display", str(title).upper(), s.right - s.left,
                             s.px(150), s.px(64), max_lines=5 if s.portrait else 3)
    line_h = int(size * 1.12)
    y = s.cy - (len(lines) * line_h + (s.px(90) if subtitle else 0)) / 2
    for i, ln in enumerate(lines):
        _, d = s.layer(0.1 + 0.13 * i, RISE)
        d.text((s.cx, y), ln, font=font, fill=pal["ink"], anchor="ma")
        y += line_h
    _, d = s.layer(0.25 + 0.13 * len(lines), WIPE, 0.45)
    d.rectangle([s.cx - s.px(90), y + s.px(22), s.cx + s.px(90), y + s.px(30)], fill=pal["accent"])
    if subtitle:
        _, d = s.layer(0.4 + 0.13 * len(lines), RISE)
        d.text((s.cx, y + s.px(58)), str(subtitle).upper(), font=s.font("mono", 36, "Bold"),
               fill=pal["accent"], anchor="ma")
    return s.save(out)


def lower_third(text: str, cfg: dict, out: Path) -> Path | None:
    """A caption plate that slides in over a photograph or clip, so footage
    carries a label instead of running bare. Transparent; composited in render."""
    text = str(text or "").strip()
    if not text:
        return None
    s = Scene(cfg, transparent=True)
    pal = s.pal
    font, lines, size = _fit(s.sd, cfg, "body", text, (s.right - s.left) * 0.8 - s.px(60),
                             s.px(60), s.px(40), max_lines=2, weight="Bold")
    line_h = int(size * 1.25)
    h = len(lines) * line_h + s.px(44)
    w = int(max(s.sd.textlength(ln, font=font) for ln in lines)) + s.px(84)
    y1 = s.bottom if s.portrait else s.H - s.px(120)
    x0, y0 = s.left, y1 - h

    # Darken the foot of the picture so the plate reads on any footage.
    import numpy as np
    ramp = np.zeros((s.H, s.W, 4), dtype="uint8")
    span = s.H - (y0 - s.px(260))
    ramp[y0 - s.px(260):, :, 3] = (np.linspace(0, 1, span) ** 1.4 * 190)[:, None]
    shade, _ = s.layer(0.0, FADE, 0.5)
    shade.alpha_composite(Image.fromarray(ramp))

    _, d = s.layer(0.2, WIPE, 0.45)
    d.rectangle([x0, y0, x0 + w, y1], fill=_rgb(pal["bg"]) + (225,))
    d.rectangle([x0, y0, x0 + s.px(10), y1], fill=pal["accent"])
    for i, ln in enumerate(lines):
        _, d = s.layer(0.4 + 0.1 * i, SLIDE)
        d.text((x0 + s.px(44), y0 + s.px(18) + i * line_h), ln, font=font, fill=pal["ink"])
    return s.save(out)


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
