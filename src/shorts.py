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


def _headline_plate(cfg: Config, title: str, out: Path) -> Path:
    """Transparent 1080x1920 overlay: the headline, held at the top for the
    whole Short, below where the app draws its own controls. Drawn with
    Pillow so it wraps and uses the brand font."""
    from PIL import Image, ImageDraw

    from .visuals.motion import _fit, _font

    vcfg = cfg.get("visuals", {})
    pal = vcfg.get("palette", {})
    img = Image.new("RGBA", (1080, 1920), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    font, lines, size = _fit(d, vcfg, "display", title.upper(), 900, 84, 52, max_lines=2)
    line_h = int(size * 1.14)
    y = 285 - len(lines) * line_h / 2
    for line in lines:
        d.text((540, y), line, font=font, fill=pal.get("accent", "#E8B931"), anchor="ma",
               stroke_width=8, stroke_fill="#0B0E13")
        y += line_h
    d.text((540, 1920 - 272), cfg.get_path("channel.name", "").upper(),
           font=_font(vcfg, "mono", 30, "Bold"), fill=pal.get("muted", "#8B949E"), anchor="ma")
    img.save(out)
    return out


def finish(cfg: Config, video: Path, out: Path, title: str, max_seconds: float = 58.0,
           shots: list[dict] | None = None) -> Path:
    """Lay the headline and the word-by-word captions over a vertical cut and
    cap it at Shorts length."""
    plate = _headline_plate(cfg, title, out.with_suffix(".png"))
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
               headline: str, title: str, language: str = "en") -> dict:
    """A Short that is its own story: the video was already rendered 9:16, so
    it only needs its headline."""
    out = finish(cfg, video, workdir / "short.mp4", latin_only(headline) or "EXPLAINED",
                 shots=shots)
    return {
        "path": str(out),
        "title": f"{title.strip()[:90]} #shorts",
        "description": f"{cfg.get_path('channel.disclosure', '').strip()}\n\n#shorts",
    }
