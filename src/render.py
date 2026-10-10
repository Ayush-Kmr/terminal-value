"""ffmpeg assembly: shot stills + narration -> finished video.

Movement is added here, not baked into the PNGs. Every shot gets a slow push or
drift (max 6% over its duration) because a perfectly static frame under
narration is the texture that reads as a slideshow.
"""
from __future__ import annotations

import logging
import random
import subprocess
from pathlib import Path

from .config import Config

log = logging.getLogger(__name__)


def _run(cmd: list[str]) -> None:
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"ffmpeg failed:\n{' '.join(cmd[:12])}...\n{res.stderr[-2500:]}")


def _ken_burns(src: Path, dest: Path, seconds: float, cfg: Config, seed: int) -> Path:
    """Slow zoom/pan. Direction alternates so consecutive shots don't drift the
    same way, which is what makes a sequence feel mechanical."""
    w, h = cfg.get_path("visuals.resolution", [1920, 1080])
    fps = cfg.get_path("visuals.fps", 30)
    frames = max(int(seconds * fps), 1)

    rng = random.Random(seed)
    zoom_in = rng.random() < 0.6
    # Max 6% movement - more than that looks cheap on archival material.
    if zoom_in:
        z = f"min(1.001+0.00{max(int(60/frames),1)}*on,1.06)"
    else:
        z = f"max(1.06-0.00{max(int(60/frames),1)}*on,1.001)"

    x = {0: "iw/2-(iw/zoom/2)", 1: "0", 2: "iw-(iw/zoom)"}[rng.randint(0, 2)]
    y = {0: "ih/2-(ih/zoom/2)", 1: "0"}[rng.randint(0, 1)]

    vf = (
        f"scale={w*2}:{h*2}:force_original_aspect_ratio=increase,"
        f"crop={w*2}:{h*2},"
        f"zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s={w}x{h}:fps={fps},"
        f"format=yuv420p"
    )
    _run(["ffmpeg", "-y", "-loop", "1", "-i", str(src), "-vf", vf,
          "-t", f"{seconds:.3f}", "-r", str(fps), "-c:v", "libx264",
          "-preset", "medium", "-crf", "18", str(dest)])
    return dest


def _still_clip(src: Path, dest: Path, seconds: float, cfg: Config) -> Path:
    """Motion-graphic cards get a near-imperceptible push rather than a pan —
    text that drifts is harder to read."""
    w, h = cfg.get_path("visuals.resolution", [1920, 1080])
    fps = cfg.get_path("visuals.fps", 30)
    frames = max(int(seconds * fps), 1)
    vf = (
        f"scale={w}:{h},zoompan=z='min(1.0+0.0006*on,1.025)':"
        f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s={w}x{h}:fps={fps},"
        f"format=yuv420p"
    )
    _run(["ffmpeg", "-y", "-loop", "1", "-i", str(src), "-vf", vf,
          "-t", f"{seconds:.3f}", "-r", str(fps), "-c:v", "libx264",
          "-preset", "medium", "-crf", "18", str(dest)])
    return dest


def _trim_video(src: Path, dest: Path, seconds: float, cfg: Config) -> Path:
    """Stock video: trim, conform, desaturate slightly to match the palette."""
    w, h = cfg.get_path("visuals.resolution", [1920, 1080])
    fps = cfg.get_path("visuals.fps", 30)
    vf = (
        f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
        f"eq=saturation=0.72:contrast=1.06,format=yuv420p"
    )
    _run(["ffmpeg", "-y", "-stream_loop", "-1", "-i", str(src), "-t", f"{seconds:.3f}",
          "-vf", vf, "-r", str(fps), "-an", "-c:v", "libx264",
          "-preset", "medium", "-crf", "18", str(dest)])
    return dest


def build_shot_clips(cfg: Config, shots: list[dict], workdir: Path) -> list[Path]:
    clips_dir = workdir / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)
    clips: list[Path] = []

    for shot in shots:
        dest = clips_dir / f"clip_{shot['index']:03d}.mp4"
        asset = Path(shot["asset"]) if shot.get("asset") else None
        seconds = float(shot.get("duration", 5.0))

        if dest.exists():
            clips.append(dest)
            continue
        if asset is None or not asset.exists():
            log.warning("Shot %03d has no asset; skipping", shot["index"])
            continue

        if asset.suffix.lower() == ".mp4":
            _trim_video(asset, dest, seconds, cfg)
        elif shot.get("visual_type") in {"photo", "ai_still"}:
            _ken_burns(asset, dest, seconds, cfg, seed=shot["index"])
        else:
            _still_clip(asset, dest, seconds, cfg)

        clips.append(dest)
    return clips


def concat_with_audio(
    cfg: Config, clips: list[Path], shots: list[dict], workdir: Path,
    music: Path | None = None,
) -> Path:
    """Crossfade-free hard cuts (correct for documentary), narration laid over."""
    list_file = workdir / "clips.txt"
    list_file.write_text("".join(f"file '{c.resolve()}'\n" for c in clips), encoding="utf-8")

    silent = workdir / "silent.mp4"
    _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
          "-c", "copy", str(silent)])

    # Build the narration track: each shot's audio at its cumulative offset.
    audio_inputs: list[str] = []
    filters: list[str] = []
    offset = 0.0
    idx = 0
    for shot in shots:
        if shot.get("audio"):
            audio_inputs += ["-i", str(shot["audio"])]
            filters.append(f"[{idx+1}:a]adelay={int(offset*1000)}|{int(offset*1000)}[a{idx}]")
            idx += 1
        offset += float(shot.get("duration", 5.0))

    out = workdir / "final.mp4"
    if not filters:
        log.warning("No narration audio; writing silent cut")
        silent.replace(out)
        return out

    mix = "".join(f"[a{i}]" for i in range(idx))
    chain = ";".join(filters) + f";{mix}amix=inputs={idx}:dropout_transition=0:normalize=0[narr]"

    cmd = ["ffmpeg", "-y", "-i", str(silent), *audio_inputs]
    if music and music.exists():
        cmd += ["-stream_loop", "-1", "-i", str(music)]
        chain += (
            f";[{idx+1}:a]volume=0.07,afade=t=in:d=3[bed]"
            f";[narr][bed]amix=inputs=2:duration=first:normalize=0[aout]"
        )
        amap = "[aout]"
    else:
        amap = "[narr]"

    chain += f";{amap}loudnorm=I=-14:TP=-1.5:LRA=11[final]"
    cmd += ["-filter_complex", chain, "-map", "0:v", "-map", "[final]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", str(out)]
    _run(cmd)
    log.info("Rendered %s", out)
    return out
