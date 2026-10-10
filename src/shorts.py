"""Shorts, cut from the long episode.

Shorts exist to buy subscribers; longs buy watch hours. Cutting from the long
costs nothing extra and keeps the two formats visually identical, which is how
a Short converts into a subscriber who then watches a 12-minute film.
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from . import llm
from .config import Config

log = logging.getLogger(__name__)

PICK_PROMPT = """Choose the {n} strongest standalone moments from this documentary
to cut as YouTube Shorts.

A good Short moment:
- Makes sense with zero context from the rest of the film
- Contains one surprising, specific fact
- Resolves in under 50 seconds
- Ends on something that makes the viewer want the full episode

Shots (index, duration, narration):
{shots}

Return JSON array: {{"start_index": int, "end_index": int, "hook_text": "<a
3-6 word on-screen title for the Short>", "why": "<one sentence>"}}"""


def pick_moments(script_shots: list[dict], n: int = 3) -> list[dict]:
    lines = "\n".join(
        f"{s['index']}\t{s.get('duration',5):.1f}s\t{(s.get('narration') or '')[:140]}"
        for s in script_shots
    )
    return llm.json_call(PICK_PROMPT.format(n=n, shots=lines), temperature=0.6)


def _offset(shots: list[dict], index: int) -> float:
    return sum(float(s.get("duration", 5)) for s in shots if s["index"] < index)


def cut(cfg: Config, source: Path, shots: list[dict], moment: dict,
        out: Path, title: str) -> Path:
    """Crop to 9:16, burn a title card at the top, hard-cap at 58 seconds."""
    start = _offset(shots, moment["start_index"])
    end = _offset(shots, moment["end_index"] + 1)
    duration = min(max(end - start, 12.0), 58.0)

    pal = cfg.get_path("visuals.palette", {})
    accent = pal.get("accent", "#E8B931").lstrip("#")
    safe_title = title.replace(":", r"\:").replace("'", "")

    vf = (
        "scale=1080:-2,"
        "crop=1080:1920:0:(ih-1920)/2,"
        f"drawbox=x=0:y=180:w=iw:h=260:color=0x0E1116@0.85:t=fill,"
        f"drawtext=text='{safe_title}':fontcolor=0x{accent}:fontsize=76:"
        "x=(w-text_w)/2:y=250:line_spacing=14,"
        "format=yuv420p"
    )
    cmd = ["ffmpeg", "-y", "-ss", f"{start:.3f}", "-i", str(source),
           "-t", f"{duration:.3f}", "-vf", vf,
           "-c:v", "libx264", "-preset", "medium", "-crf", "20",
           "-c:a", "aac", "-b:a", "160k", str(out)]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"Short render failed:\n{res.stderr[-2000:]}")
    log.info("Short cut: %s (%.1fs)", out.name, duration)
    return out


def build_all(cfg: Config, source: Path, shots: list[dict], workdir: Path,
              company: str, n: int = 3) -> list[dict]:
    moments = pick_moments(shots, n)
    outdir = workdir / "shorts"
    outdir.mkdir(parents=True, exist_ok=True)
    built = []
    for i, m in enumerate(moments):
        try:
            path = cut(cfg, source, shots, m, outdir / f"short_{i+1}.mp4",
                       m.get("hook_text", company))
            built.append({
                "path": str(path),
                "title": f"{m.get('hook_text', company)} #{company.replace(' ', '')}",
                "description": (
                    f"From the full documentary on {company}. "
                    f"Watch it here: {cfg.get_path('channel.url','')}\n\n#shorts #business"
                ),
                "why": m.get("why", ""),
            })
        except Exception as exc:  # noqa: BLE001
            log.warning("Short %d failed: %s", i + 1, exc)
    return built
