"""ffmpeg assembly: shot assets + narration + music -> finished video.

Cards arrive with their entrance already drawn as a frame sequence
(visuals/motion.py); here they are played, held with a slow push, and joined
to the neighbouring shots with a short dissolve. Photographs and stock clips
get a slow move and a caption plate. Narration is laid over, with the music
bed ducked underneath it.
"""
from __future__ import annotations

import hashlib
import logging
import random
import subprocess
from pathlib import Path

from .config import ROOT, Config
from .visuals import motion

log = logging.getLogger(__name__)

XFADE = 0.35      # seconds each shot overlaps the next


def _run(cmd: list[str]) -> None:
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"ffmpeg failed:\n{' '.join(cmd[:12])}...\n{res.stderr[-2500:]}")


def _size(cfg: Config) -> tuple[int, int, int]:
    w, h = cfg.get_path("visuals.resolution", [1920, 1080])
    return int(w), int(h), int(cfg.get_path("visuals.fps", 30))


def _encode(inputs: list[str], graph: str, seconds: float, fps: int, dest: Path) -> Path:
    _run(["ffmpeg", "-y", *inputs, "-filter_complex", graph, "-map", "[v]",
          "-t", f"{seconds:.3f}", "-r", str(fps), "-an", "-c:v", "libx264",
          "-preset", "medium", "-crf", "18", str(dest)])
    return dest


def _plate(shot: dict, cfg: Config, dest: Path) -> Path | None:
    """Caption plate for a photograph or clip, drawn from the shot's headline."""
    text = (shot.get("visual_spec") or {}).get("headline", "")
    w, h, _ = _size(cfg)
    # Shorts carry word captions in the same band; two texts there collide.
    if not text or h > w or not cfg.get_path("visuals.lower_thirds", True):
        return None
    try:
        vis = {**cfg.get("visuals", {})}
        png = motion.lower_third(text, vis, dest.with_name(dest.stem + "_plate.png"))
        return motion.frames_dir(png) if png else None
    except Exception as exc:  # noqa: BLE001 - a missing label must not lose the shot
        log.warning("Caption plate failed for shot %s: %s", shot.get("index"), exc)
        return None


def _with_plate(inputs: list[str], graph: str, plate: Path | None, fps: int) -> tuple[list[str], str]:
    """Finish a graph that ends in [pic], with or without a caption plate."""
    if not plate:
        return inputs, graph + ";[pic]format=yuv420p[v]"
    inputs = inputs + ["-framerate", str(fps), "-i", str(plate / "f_%03d.png")]
    return inputs, graph + ";[pic][1:v]overlay=eof_action=repeat:format=auto,format=yuv420p[v]"


def _ken_burns(src: Path, dest: Path, seconds: float, cfg: Config, seed: int,
               plate: Path | None = None) -> Path:
    """Slow zoom/pan. Direction alternates so consecutive shots don't drift the
    same way, which is what makes a sequence feel mechanical."""
    w, h, fps = _size(cfg)
    frames = max(int(seconds * fps), 1)

    rng = random.Random(seed)
    # Max 8% movement - more than that looks cheap on archival material.
    rate = 0.08 / frames
    z = (f"min(1.001+{rate:.6f}*on,1.08)" if rng.random() < 0.6
         else f"max(1.08-{rate:.6f}*on,1.001)")
    x = {0: "iw/2-(iw/zoom/2)", 1: "0", 2: "iw-(iw/zoom)"}[rng.randint(0, 2)]
    y = {0: "ih/2-(ih/zoom/2)", 1: "0"}[rng.randint(0, 1)]

    graph = (
        f"[0:v]scale={w*2}:{h*2}:force_original_aspect_ratio=increase,"
        f"crop={w*2}:{h*2},"
        f"zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s={w}x{h}:fps={fps},"
        f"eq=contrast=1.05:saturation=0.92,vignette=PI/5[pic]"
    )
    inputs, graph = _with_plate(["-loop", "1", "-i", str(src)], graph, plate, fps)
    return _encode(inputs, graph, seconds, fps, dest)


def _card_clip(frames: Path, dest: Path, seconds: float, cfg: Config) -> Path:
    """Play a card's entrance, then hold the finished frame with a slow push
    so it never sits completely still under the narration."""
    w, h, fps = _size(cfg)
    graph = (
        f"[0:v]tpad=stop_mode=clone:stop_duration={seconds + 1:.2f},"
        f"scale={w*2}:{h*2}:flags=bilinear,"
        f"zoompan=z='min(1+0.00028*on,1.035)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
        f":d=1:s={w}x{h}:fps={fps},format=yuv420p[v]"
    )
    return _encode(["-framerate", str(fps), "-i", str(frames / "f_%03d.png")],
                   graph, seconds, fps, dest)


def _still_clip(src: Path, dest: Path, seconds: float, cfg: Config) -> Path:
    """A card with no entrance sequence: near-imperceptible push only."""
    w, h, fps = _size(cfg)
    frames = max(int(seconds * fps), 1)
    graph = (
        f"[0:v]scale={w}:{h},zoompan=z='min(1.0+0.0006*on,1.025)':"
        f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s={w}x{h}:fps={fps},"
        f"format=yuv420p[v]"
    )
    return _encode(["-loop", "1", "-i", str(src)], graph, seconds, fps, dest)


def _trim_video(src: Path, dest: Path, seconds: float, cfg: Config,
                plate: Path | None = None) -> Path:
    """Stock video: trim, conform, grade slightly to match the palette."""
    w, h, fps = _size(cfg)
    graph = (
        f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
        f"fps={fps},eq=saturation=0.8:contrast=1.06,vignette=PI/5[pic]"
    )
    inputs, graph = _with_plate(["-stream_loop", "-1", "-i", str(src)], graph, plate, fps)
    return _encode(inputs, graph, seconds, fps, dest)


def build_shot_clips(cfg: Config, shots: list[dict], workdir: Path) -> list[Path]:
    """One clip per shot. Each runs XFADE longer than the shot so it can
    dissolve into the next; shots that produce no clip are marked so the
    join stays in step with the narration."""
    clips_dir = workdir / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)
    clips: list[Path] = []

    for shot in shots:
        dest = clips_dir / f"clip_{shot['index']:03d}.mp4"
        asset = Path(shot["asset"]) if shot.get("asset") else None
        seconds = float(shot.get("duration", 5.0)) + XFADE

        if dest.exists():
            shot["clip"] = str(dest)
            clips.append(dest)
            continue
        if asset is None or not asset.exists():
            log.warning("Shot %03d has no asset; skipping", shot["index"])
            continue

        frames = motion.frames_dir(asset)
        if asset.suffix.lower() == ".mp4":
            _trim_video(asset, dest, seconds, cfg, _plate(shot, cfg, dest))
        elif shot.get("visual_type") in {"photo", "ai_still"} and not frames:
            _ken_burns(asset, dest, seconds, cfg, seed=shot["index"],
                       plate=_plate(shot, cfg, dest))
        elif frames:
            _card_clip(frames, dest, seconds, cfg)
        else:
            _still_clip(asset, dest, seconds, cfg)

        shot["clip"] = str(dest)
        clips.append(dest)
    return clips


def pick_music(cfg: Config, seed: str) -> Path | None:
    """One of the beds in assets/music, chosen by the story so the same
    video always gets the same track and different videos vary."""
    if not cfg.get_path("audio.music", True):
        return None
    tracks = sorted((ROOT / "assets" / "music").glob("*.mp3"))
    if not tracks:
        return None
    return tracks[int(hashlib.sha1(seed.encode("utf-8")).hexdigest(), 16) % len(tracks)]


def _join(cfg: Config, shots: list[dict], workdir: Path) -> Path:
    """Dissolve the clips into one silent video whose cuts land exactly where
    each shot's narration starts."""
    w, h, fps = _size(cfg)
    used = [s for s in shots if s.get("clip")]
    silent = workdir / "silent.mp4"
    total = sum(float(s.get("duration", 5.0)) for s in shots)
    if len(used) == 1:
        _run(["ffmpeg", "-y", "-i", used[0]["clip"], "-t", f"{total:.3f}", "-c", "copy",
              str(silent)])
        return silent

    inputs: list[str] = []
    parts: list[str] = []
    for i, shot in enumerate(used):
        inputs += ["-i", shot["clip"]]
        parts.append(f"[{i}:v]settb=AVTB,fps={fps},format=yuv420p[c{i}]")
    # A shot that lost its clip leaves its time to the one before it.
    starts, t = {}, 0.0
    for shot in shots:
        starts[shot["index"]] = t
        t += float(shot.get("duration", 5.0))
    move = "smoothup" if h > w else "smoothleft"
    last = "c0"
    for i in range(1, len(used)):
        new_beat = used[i].get("beat") != used[i - 1].get("beat")
        parts.append(
            f"[{last}][c{i}]xfade=transition={move if new_beat else 'fade'}"
            f":duration={XFADE}:offset={starts[used[i]['index']]:.3f}[x{i}]")
        last = f"x{i}"
    _run(["ffmpeg", "-y", *inputs, "-filter_complex", ";".join(parts), "-map", f"[{last}]",
          "-t", f"{total:.3f}", "-r", str(fps), "-c:v", "libx264", "-preset", "medium",
          "-crf", "19", "-pix_fmt", "yuv420p", str(silent)])
    return silent


def concat_with_audio(
    cfg: Config, clips: list[Path], shots: list[dict], workdir: Path,
    music: Path | None = None,
) -> Path:
    """Joined picture, narration laid over, music ducked underneath."""
    silent = _join(cfg, shots, workdir)
    total = sum(float(s.get("duration", 5.0)) for s in shots)

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
    chain = ";".join(filters) + (
        f";{mix}amix=inputs={idx}:dropout_transition=0:normalize=0,"
        f"apad=whole_dur={total:.3f}[narr]")

    cmd = ["ffmpeg", "-y", "-i", str(silent), *audio_inputs]
    if music and music.exists():
        # Start somewhere different in the loop for each video.
        start = random.Random(str(workdir.name)).uniform(0, 40)
        cmd += ["-ss", f"{start:.2f}", "-stream_loop", "-1", "-i", str(music)]
        volume = float(cfg.get_path("audio.music_volume", 0.5))
        # The bed drops whenever the narrator speaks and comes back up in
        # the pauses, so it is heard without ever competing with the voice.
        chain += (
            f";[narr]asplit=2[say][key]"
            f";[{idx+1}:a]aformat=channel_layouts=stereo,volume={volume},afade=t=in:d=1.5[bed]"
            # Measured: threshold 0.02 / ratio 5 left the bed ~20 dB under the
            # voice, inaudible on a phone. This sits it ~14 dB under.
            f";[bed][key]sidechaincompress=threshold=0.05:ratio=3:attack=20:release=400[duck]"
            f";[say][duck]amix=inputs=2:duration=first:normalize=0[aout]"
        )
        amap = "[aout]"
    else:
        amap = "[narr]"

    fade = max(total - 1.2, 0)
    chain += (f";{amap}loudnorm=I=-14:TP=-1.5:LRA=11,"
              f"afade=t=out:st={fade:.3f}:d=1.2[final]")
    cmd += ["-filter_complex", chain, "-map", "0:v", "-map", "[final]", "-t", f"{total:.3f}",
            "-c:v", "copy", "-c:a", "aac", "-ar", "48000", "-b:a", "192k", str(out)]
    _run(cmd)
    log.info("Rendered %s%s", out, f" (music: {music.name})" if music else "")
    return out
