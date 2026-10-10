"""Shorts, cut from the long episode.

Shorts exist to buy subscribers; longs buy watch hours. Cutting from the long
costs nothing extra and keeps the two formats visually identical, which is how
a Short converts into a subscriber who then watches the full video.
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from . import llm
from .config import Config, latin_only

log = logging.getLogger(__name__)

PICK_PROMPT = """Choose the {n} strongest standalone moments from this video to cut
as YouTube Shorts. They must not overlap.

A good Short moment:
- Makes sense with zero context from the rest of the video
- Contains one surprising, specific fact
- Runs 25-55 seconds (add up the shot durations)
- Ends on something that makes the viewer want the full video

Shots (index, duration, narration):
{shots}

Return a JSON array of objects:
  start_index, end_index  - first and last shot index, inclusive
  hook_text  - a 2-5 word on-screen headline, ALWAYS in English Latin letters
  title      - the Short's YouTube title, under 70 characters, in the same
               language as the narration
  why        - one sentence"""


def pick_moments(script_shots: list[dict], n: int = 2) -> list[dict]:
    lines = "\n".join(
        f"{s['index']}\t{s.get('duration',5):.1f}s\t{(s.get('narration') or '')[:140]}"
        for s in script_shots
    )
    return llm.json_call(PICK_PROMPT.format(n=n, shots=lines), temperature=0.6)


def _offset(shots: list[dict], index: int) -> float:
    return sum(float(s.get("duration", 5)) for s in shots if s["index"] < index)


def _headline_plate(cfg: Config, title: str, out: Path) -> Path:
    """Transparent 1080x1920 overlay: headline above the video, channel name
    below. Drawn with Pillow so it wraps and uses the brand font (ffmpeg's
    drawtext is missing from many builds)."""
    import textwrap

    from PIL import Image, ImageDraw

    from .visuals.motion import _font

    vcfg = cfg.get("visuals", {})
    pal = vcfg.get("palette", {})
    img = Image.new("RGBA", (1080, 1920), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    lines = textwrap.wrap(title.upper(), 16)[:3]
    f = _font(vcfg, "display", 86)
    y = 600 - len(lines) * 100          # the video frame starts at y=656
    for line in lines:
        d.text((540, y), line, font=f, fill=pal.get("accent", "#E8B931"), anchor="ma",
               stroke_width=6, stroke_fill="#000000")
        y += 100

    d.text((540, 1330), cfg.get_path("channel.name", "").upper(),
           font=_font(vcfg, "mono", 34), fill=pal.get("ink", "#F2F4F7"), anchor="ma")
    img.save(out)
    return out


def cut(cfg: Config, source: Path, shots: list[dict], moment: dict,
        out: Path, title: str) -> Path:
    """9:16 with the full 16:9 frame in the middle over a blurred fill, so no
    on-screen text is cropped away. Headline above. Hard cap 58 seconds."""
    last = max(s["index"] for s in shots)
    first_i = max(0, min(int(moment["start_index"]), last))
    last_i = max(first_i, min(int(moment["end_index"]), last))
    start = _offset(shots, first_i)
    end = _offset(shots, last_i + 1)
    duration = min(max(end - start, 12.0), 58.0)

    plate = _headline_plate(cfg, title, out.with_suffix(".png"))
    vf = (
        "[0:v]split=2[a][b];"
        "[a]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
        "boxblur=28:4,eq=brightness=-0.32:saturation=0.6[bg];"
        "[b]scale=1080:-2[fg];"
        "[bg][fg]overlay=0:(H-h)/2[base];"
        "[base][1:v]overlay=0:0,format=yuv420p[v]"
    )
    cmd = ["ffmpeg", "-y", "-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", str(source),
           "-i", str(plate), "-filter_complex", vf, "-map", "[v]", "-map", "0:a?",
           "-c:v", "libx264", "-preset", "medium", "-crf", "20",
           "-c:a", "aac", "-b:a", "160k", str(out)]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"Short render failed:\n{res.stderr[-2000:]}")
    log.info("Short cut: %s (%.1fs)", out.name, duration)
    return out


def build_all(cfg: Config, source: Path, shots: list[dict], workdir: Path,
              company: str, n: int = 2, language: str = "en",
              long_url: str = "") -> list[dict]:
    moments = [m for m in pick_moments(shots, n) if isinstance(m, dict)][:n]
    outdir = workdir / "shorts"
    outdir.mkdir(parents=True, exist_ok=True)
    more = long_url or cfg.get_path("channel.url", "")
    built = []
    for i, m in enumerate(moments):
        try:
            headline = latin_only(m.get("hook_text", "")) or latin_only(company) or "EXPLAINED"
            path = cut(cfg, source, shots, m, outdir / f"short_{i+1}.mp4", headline)
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
