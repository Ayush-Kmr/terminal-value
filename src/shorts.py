"""Shorts: vertical videos, built natively at 9:16.

Shorts exist to buy subscribers; longs buy watch hours. A Short cut from a
long video reuses its narration and pictures but has its cards drawn again
for the tall frame, so nothing is shrunk into a letterbox.
"""
from __future__ import annotations

import copy
import logging
import subprocess
from pathlib import Path

from . import assets, captions, llm, render
from .config import Config, latin_only, portrait

log = logging.getLogger(__name__)

PICK_PROMPT = """Choose the {n} strongest standalone moments from this video to cut
as YouTube Shorts. They must not overlap.

A good Short moment:
- Makes sense with zero context from the rest of the video
- Contains one surprising, specific fact
- Runs {lo}-{hi} seconds (add up the shot durations)
- Ends on something that makes the viewer want the full video

Shots (index, duration, narration):
{shots}

Return a JSON array of objects:
  start_index, end_index  - first and last shot index, inclusive
  hook_text  - a 2-5 word on-screen headline, ALWAYS in English Latin letters
  title      - the Short's YouTube title, under 70 characters, in the same
               language as the narration
  why        - one sentence"""


def pick_moments(script_shots: list[dict], n: int = 2, target: int = 30) -> list[dict]:
    lines = "\n".join(
        f"{s['index']}\t{s.get('duration',5):.1f}s\t{(s.get('narration') or '')[:140]}"
        for s in script_shots
    )
    return llm.json_call(
        PICK_PROMPT.format(n=n, shots=lines, lo=max(target - 8, 12), hi=target + 6),
        temperature=0.6)


def _offset(shots: list[dict], index: int) -> float:
    return sum(float(s.get("duration", 5)) for s in shots if s["index"] < index)


def _headline_plate(cfg: Config, title: str, out: Path, series: str = "") -> Path:
    """Transparent 1080x1920 overlay: the headline, held at the top for the
    whole Short, below where the app draws its own controls. Drawn with
    Pillow so it wraps and uses the brand font."""
    from PIL import Image, ImageDraw

    from .visuals.motion import _fit, _font

    vcfg = cfg.get("visuals", {})
    pal = vcfg.get("palette", {})
    if vcfg.get("style") == "caption_news":
        return _caption_news_plate(cfg, title, out, series)
    img = Image.new("RGBA", (1080, 1920), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    font, lines, size = _fit(d, vcfg, "display", title.upper(), 900, 84, 52, max_lines=3)
    line_h = int(size * 1.14)
    y = 285 - len(lines) * line_h / 2
    for line in lines:
        d.text((540, y), line, font=font, fill=pal.get("accent", "#E8B931"), anchor="ma",
               stroke_width=8, stroke_fill="#0B0E13")
        y += line_h
    if series:      # names the series so a viewer knows there is a next one
        small = _font(vcfg, "mono", 32, "Bold")
        for n, text in enumerate((f"SERIES: {series.upper()}", "FOLLOW FOR THE NEXT ONE")):
            d.text((540, y + 18 + n * 44), text, font=small, fill="#FFFFFF", anchor="ma",
                   stroke_width=5, stroke_fill="#0B0E13")
    d.text((540, 1920 - 272), (cfg.get_path("visuals.labels.wordmark")
                               or cfg.get_path("channel.name", "")).upper(),
           font=_font(vcfg, "mono", 30, "Bold"), fill=pal.get("muted", "#8B949E"), anchor="ma")
    img.save(out)
    return out


def _caption_news_plate(cfg: Config, title: str, out: Path, series: str = "") -> Path:
    """Caption News, 9:16: the channel bug (a honey block) in the top safe area,
    the headline under it in heavy ivory type, a dark shade at the foot so the
    captions read on any picture."""
    import numpy as np
    from PIL import Image, ImageDraw, ImageFilter

    from .visuals.motion import _fit, _font

    vcfg = cfg.get("visuals", {})
    pal = vcfg.get("palette", {})
    ink, honey = pal.get("ink", "#F7F5EF"), pal.get("accent", "#FFC21A")
    img = Image.new("RGBA", (1080, 1920), (0, 0, 0, 0))
    a = np.zeros((1920, 1080), dtype="float32")
    a[:560] = (np.linspace(1, 0, 560) ** 1.2 * 0.62)[:, None]  # under the bug and headline
    a[1300:] = (np.linspace(0, 1, 620) ** 1.3 * 0.78)[:, None]  # under the captions
    shade = np.zeros((1920, 1080, 4), dtype="uint8")
    shade[..., 3] = (a * 255).astype("uint8")
    img.alpha_composite(Image.fromarray(shade))
    d = ImageDraw.Draw(img)

    name = (cfg.get_path("visuals.labels.wordmark") or cfg.get_path("channel.name", "")).upper()
    fb = _font(vcfg, "display", 30)
    bw = d.textlength(name, font=fb) + 36
    d.rectangle([84, 128, 84 + bw, 174], fill=honey)
    d.text((84 + 18, 151), name, font=fb, fill="#0E0E0C", anchor="lm")

    font, lines, size = _fit(d, vcfg, "display", title.upper(), 912, 78, 50, max_lines=3)
    line_h = int(size * 1.08)
    text = Image.new("RGBA", img.size, (0, 0, 0, 0))
    td = ImageDraw.Draw(text)
    y = 200
    for line in lines:
        td.text((84, y), line, font=font, fill=ink)
        y += line_h
    if series:
        small = _font(vcfg, "mono", 28)
        for n, row in enumerate((f"SERIES: {series.upper()}", "FOLLOW FOR THE NEXT ONE")):
            td.text((84, y + 14 + n * 40), row, font=small, fill=honey if n == 0 else ink)
    shadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    shadow.putalpha(text.getchannel("A").point(lambda v: int(v * 0.8)))
    img.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(10)))
    img.alpha_composite(text)
    img.save(out)
    return out


def finish(cfg: Config, video: Path, out: Path, title: str, max_seconds: float = 58.0,
           shots: list[dict] | None = None, series: str = "") -> Path:
    """Lay the headline and the word-by-word captions over a vertical cut and
    cap it at Shorts length."""
    cfg = portrait(cfg)                 # the Shorts style, palette and fonts
    plate = _headline_plate(cfg, title, out.with_suffix(".png"), series)
    track = captions.overlay(cfg, shots, (1080, 1920), out.parent / f"{out.stem}_cc") \
        if shots else None
    inputs = ["-i", str(video), "-i", str(plate)]
    graph = "[0:v][1:v]overlay=0:0"
    if track:
        inputs += ["-i", str(track)]
        graph += "[h];[h][2:v]overlay=0:0:eof_action=pass"
    cmd = ["ffmpeg", "-y", *inputs, "-filter_complex",
           graph + ",format=yuv420p[v]", "-map", "[v]", "-map", "0:a?",
           "-t", f"{max_seconds:.3f}", "-c:v", "libx264", "-preset", "medium", "-crf", "19",
           "-c:a", "copy", str(out)]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"Short render failed:\n{res.stderr[-2000:]}")
    return out


def cut(cfg: Config, shots: list[dict], moment: dict, out: Path, title: str,
        company: str, max_seconds: float = 58.0) -> Path:
    """Rebuild a run of shots from the long video as a vertical Short: same
    narration and pictures, cards redrawn for 9:16."""
    last = max(s["index"] for s in shots)
    first_i = max(0, min(int(moment["start_index"]), last))
    last_i = max(first_i, min(int(moment["end_index"]), last))
    chosen = [copy.deepcopy(s) for s in shots if first_i <= s["index"] <= last_i]
    # Whole shots only: trimming mid-sentence is worse than a shorter Short.
    while len(chosen) > 1 and sum(float(s.get("duration", 5)) for s in chosen) > max_seconds:
        chosen.pop()
    for s in chosen:
        s.pop("clip", None)

    tall = portrait(cfg)
    work = out.with_suffix("")
    work.mkdir(parents=True, exist_ok=True)
    assets.redraw_cards(tall, chosen, work / "assets", company)
    clips = render.build_shot_clips(tall, chosen, work)
    video = render.concat_with_audio(tall, clips, chosen, work,
                                     render.pick_music(cfg, f"{company}-{out.stem}"))
    finish(cfg, video, out, title, max_seconds, chosen)
    log.info("Short cut: %s (%.1fs)", out.name,
             sum(float(s.get("duration", 5)) for s in chosen))
    return out


def build_all(cfg: Config, source: Path, shots: list[dict], workdir: Path,
              company: str, n: int = 2, language: str = "en",
              long_url: str = "") -> list[dict]:
    target = int(cfg.get_path("format.short.target_seconds", 30))
    moments = [m for m in pick_moments(shots, n, target) if isinstance(m, dict)][:n]
    outdir = workdir / "shorts"
    outdir.mkdir(parents=True, exist_ok=True)
    more = long_url or "the full video on the channel"

    built = []
    for i, m in enumerate(moments):
        try:
            headline = latin_only(m.get("hook_text", "")) or latin_only(company) or "EXPLAINED"
            path = cut(cfg, shots, m, outdir / f"short_{i+1}.mp4", headline, company,
                       max_seconds=min(target + 12, 58))
            title = (m.get("title") or headline).strip()[:90]
            built.append({
                "path": str(path),
                "title": f"{title} #shorts",
                "description": (
                    ("पूरा वीडियो यहाँ देखें: " if language == "hi" else "Full video: ")
                    + f"{more}\n\n{cfg.get_path('channel.disclosure', '').strip()}\n\n#shorts"
                ),
                "why": m.get("why", ""),
            })
        except Exception as exc:  # noqa: BLE001
            log.warning("Short %d failed: %s", i + 1, exc)
    return built


def standalone(cfg: Config, video: Path, shots: list[dict], workdir: Path,
               headline: str, title: str, language: str = "en", series: str = "") -> dict:
    """A Short that is its own story: the video was already rendered 9:16, so
    it only needs its headline."""
    # A channel with its own script draws the headline in it; latin_only would
    # hand the Tamil font an English fallback it has no letters for.
    native = cfg.get_path("visuals.fonts.caption")
    out = finish(cfg, video, workdir / "short.mp4",
                 headline if native else latin_only(headline) or "EXPLAINED", shots=shots,
                 series=series)
    return {
        "path": str(out),
        "title": f"{title.strip()[:90]} #shorts",
        "description": f"{cfg.get_path('channel.disclosure', '').strip()}\n\n#shorts",
    }
