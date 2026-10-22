"""Word-by-word captions, timed from what the narrator actually says.

Shorts are watched with the sound off as often as on, and the feed decides in
the first seconds whether to keep showing a Short, so every Short carries big
captions: three words at a time, the word being spoken in the accent colour.

Timings come from the Whisper transcript of each take (voice.check_takes).
The words shown are the script's, not the transcript's, so a mis-heard word
never reaches the screen. Drawn with Pillow and laid over as a transparent
video: no libass needed, so it renders the same on a Mac and on the runner.
"""
from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, features

from .config import Config, latin_only

log = logging.getLogger(__name__)


def _script_words(text: str) -> list[str]:
    return [w for w in re.split(r"\s+", text.strip()) if w]


def timeline(shots: list[dict]) -> list[tuple[str, float, float]]:
    """(word, start, end) in video time for every narrated word."""
    out: list[tuple[str, float, float]] = []
    offset = 0.0
    for shot in shots:
        words = _script_words(shot.get("narration") or "")
        if words and shot.get("audio"):
            heard = shot.get("words") or []
            spoken = float(shot.get("spoken_duration") or shot.get("duration", 5))
            for i, w in enumerate(words):
                if heard:
                    # Script word i takes the timing of the heard word at the
                    # same relative position: robust to splits and mis-hearings.
                    j = min(int(i * len(heard) / len(words)), len(heard) - 1)
                    k = min(int((i + 1) * len(heard) / len(words)), len(heard)) - 1
                    start, end = heard[j]["start"], heard[max(j, k)]["end"]
                else:
                    start, end = spoken * i / len(words), spoken * (i + 1) / len(words)
                out.append((w, offset + start, offset + max(end, start + 0.08)))
        offset += float(shot.get("duration", 5))
    return out


def _groups(words: list[tuple[str, float, float]], size: int = 3,
            max_chars: int = 18) -> list[list]:
    """Up to `size` words and about `max_chars` letters (so the type stays big),
    breaking early at sentence ends and pauses."""
    groups, cur = [], []
    for w in words:
        chars = sum(len(c[0]) + 1 for c in cur) + len(w[0])
        if cur and (len(cur) >= size or chars > max_chars or w[1] - cur[-1][2] > 0.45):
            groups.append(cur)
            cur = []
        cur.append(w)
        if re.search(r"[.?!।:;]$", w[0]):
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)
    return groups


def native_script(cfg: Config) -> bool:
    """Captions can be drawn in the narration's own script (Tamil): the
    channel names a font for it (visuals.fonts.caption) and Pillow has the
    text shaper such scripts need (libraqm, which needs libfribidi)."""
    font = cfg.get_path("visuals.fonts.caption")
    return bool(font) and features.check("raqm")


def _draw(cfg: Config, size: tuple[int, int], words: list[str], active: int,
          y_frac: float) -> Image.Image:
    from .visuals.motion import _font

    w, h = size
    vcfg = cfg.get("visuals", {})
    pal = vcfg.get("palette", {})
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    native = native_script(cfg)
    clean = [x.strip("\"'“”‘’") if native else latin_only(x).upper().strip("\"'“”‘’") or "·"
             for x in words]
    pt = int(w * (0.07 if native else 0.088))
    while True:
        font = _font(vcfg, "caption" if native else "display", pt)
        space = d.textlength(" ", font=font)
        widths = [d.textlength(x, font=font) for x in clean]
        total = sum(widths) + space * (len(clean) - 1)
        if total <= w * 0.86 or pt < 40:
            break
        pt = int(pt * 0.92)
    stroke = max(4, pt // 9)
    x, y = (w - total) / 2, h * y_frac
    for i, (word, ww) in enumerate(zip(clean, widths)):
        colour = pal.get("accent", "#E8B931") if i == active else pal.get("ink", "#F2F4F7")
        d.text((x, y), word, font=font, fill=colour, anchor="lm",
               stroke_width=stroke, stroke_fill="#06080B")
        x += ww + space
    return img


def overlay(cfg: Config, shots: list[dict], size: tuple[int, int], workdir: Path,
            y_frac: float = 0.79) -> Path | None:
    """A transparent caption track the length of the narration."""
    words = timeline(shots)
    if not native_script(cfg) and not any(latin_only(w[0]) for w in words):
        # Narration in a script the fonts cannot draw: no captions rather
        # than a row of placeholder dots.
        log.info("Captions skipped: no font or shaper for the narration's script")
        return None
    if not words:
        return None
    total = sum(float(s.get("duration", 5)) for s in shots)
    frames = workdir / "captions"
    frames.mkdir(parents=True, exist_ok=True)
    blank = frames / "blank.png"
    Image.new("RGBA", size, (0, 0, 0, 0)).save(blank)

    events: list[tuple[Path, float]] = []      # (image, seconds shown)
    t = 0.0
    n = 0
    for group in _groups(words):
        texts = [g[0] for g in group]
        for i, (_, start, end) in enumerate(group):
            if start > t + 0.02:
                events.append((blank, start - t))
                t = start
            stop = group[i + 1][1] if i + 1 < len(group) else end
            png = frames / f"c_{n:04d}.png"
            _draw(cfg, size, texts, i, y_frac).save(png)
            n += 1
            events.append((png, max(stop - t, 0.04)))
            t = max(stop, t + 0.04)
    if total > t:
        events.append((blank, total - t))

    listing = workdir / "captions.txt"
    lines = []
    for png, secs in events:
        lines += [f"file '{png.resolve()}'", f"duration {secs:.3f}"]
    lines.append(f"file '{events[-1][0].resolve()}'")   # concat needs the last twice
    listing.write_text("\n".join(lines), encoding="utf-8")

    out = workdir / "captions.mov"
    res = subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
         "-vf", "fps=30,format=rgba", "-c:v", "png", "-t", f"{total:.3f}", str(out)],
        capture_output=True, text=True)
    if res.returncode != 0:
        log.warning("Caption track failed: %s", res.stderr[-800:])
        return None
    return out


def srt(shots: list[dict], out: Path, per_line: int = 7) -> Path:
    """Upload captions from the same timings, a short line at a time."""
    def stamp(t: float) -> str:
        h, rem = divmod(int(t), 3600)
        m, s = divmod(rem, 60)
        return f"{h:02d}:{m:02d}:{s:02d},{int((t % 1) * 1000):03d}"

    lines = []
    for n, group in enumerate(_groups(timeline(shots), per_line, 60), 1):
        lines += [str(n), f"{stamp(group[0][1])} --> {stamp(group[-1][2])}",
                  " ".join(g[0] for g in group), ""]
    out.write_text("\n".join(lines), encoding="utf-8")
    return out
