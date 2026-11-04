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

XFADE = 0.4       # seconds each shot overlaps the next
WIPE = 0.3        # News Package band wipe between sections
# Cycled so no two neighbouring cuts match; a new section gets its own move.
CUTS_WIDE = ("fade", "slideleft", "smoothleft", "circleopen", "wipeleft", "fade",
             "slideright", "zoomin")
CUTS_TALL = ("fade", "slideup", "smoothup", "circleopen", "wipeup", "fade",
             "slidedown", "zoomin")
# (zoom from, zoom to, x from, x to, y from, y to), x and y as shares of the free
# margin. Six moves, used in turn, so a run of photographs never drifts one way.
MOVES = ((1.02, 1.18, .5, .5, .5, .5),     # push in
         (1.18, 1.02, .5, .5, .5, .5),     # pull out
         (1.16, 1.16, 0, 1, .5, .5),       # pan right
         (1.16, 1.16, 1, 0, .5, .5),       # pan left
         (1.04, 1.18, .5, .5, .15, .15),   # push in on the upper part
         (1.16, 1.16, .5, .5, 1, 0))       # tilt up


def _run(cmd: list[str]) -> None:
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"ffmpeg failed:\n{' '.join(cmd[:12])}...\n{res.stderr[-2500:]}")


def _size(cfg: Config) -> tuple[int, int, int]:
    w, h = cfg.get_path("visuals.resolution", [1920, 1080])
    return int(w), int(h), int(cfg.get_path("visuals.fps", 30))


def _style(cfg: Config) -> str:
    return str(cfg.get_path("visuals.style", "classic"))


def _packaged(cfg: Config) -> bool:
    """Long videos in the News Package style carry the strap, bug and tabs."""
    w, h, _ = _size(cfg)
    return _style(cfg) == motion.NEWS and w > h


# Photographs and clips are graded to the style. News Package: one duotone
# (ink to paper) for every plate with a little grain, so stock, archive and AI
# pictures read as one set; Caption News: a light, slightly warm grade.
GRADES = {
    motion.NEWS: ("format=gray,eq=contrast=1.22:gamma=1.05:brightness=-0.02,format=rgb24,"
                  "lutrgb=r='14+val*222/255':g='16+val*215/255':b='19+val*197/255',"
                  "vignette=PI/4.6,noise=alls=7:allf=t"),
    motion.CAPTION: ("eq=contrast=1.07:saturation=0.92:brightness=-0.02,"
                     "colorbalance=rs=0.04:gs=0.01:bs=-0.03,vignette=PI/6"),
}


def _grade(cfg: Config) -> str:
    return GRADES.get(_style(cfg), "eq=contrast=1.05:saturation=1.05,vignette=PI/6")


def _labels(cfg: Config) -> dict[str, str]:
    """Beat id -> its label, from every spine in the config (for the strap tab)."""
    out: dict[str, str] = {}
    for key in ("structure.beats", "structure.history_beats", "structure.biography_beats"):
        for b in cfg.get_path(key, []) or []:
            out.setdefault(b.get("id"), b.get("label", ""))
    return out


def _package(shot: dict, prev: dict | None, cfg: Config, dest: Path, headline: str) -> Path | None:
    """The News Package layer for one shot, as a frame sequence."""
    try:
        spec = shot.get("visual_spec") or {}
        picture = shot.get("visual_type") in {"photo", "ai_still"} or \
            str(shot.get("asset", "")).lower().endswith(".mp4")
        line = (spec.get("headline") or "") if picture else ""
        prev_spec = (prev or {}).get("visual_spec") or {}
        prev_line = (prev_spec.get("headline") or "") if prev and (
            prev.get("visual_type") in {"photo", "ai_still"}
            or str(prev.get("asset", "")).lower().endswith(".mp4")) else ""
        labels = _labels(cfg)
        vis = {**cfg.get("visuals", {}), "wordmark": cfg.get_path("visuals.labels.wordmark")
               or cfg.get_path("channel.name", "")}
        png = motion.package(
            vis, dest.with_name(dest.stem + "_pkg.png"),
            headline=headline, tab=labels.get(shot.get("beat"), ""), line=line,
            source=str(spec.get("caption") or ""),
            tag="Illustration · AI-generated" if shot.get("visual_type") == "ai_still" else "",
            enter=prev is None, new_tab=bool(prev) and prev.get("beat") != shot.get("beat"),
            new_line=line != prev_line)
        return motion.frames_dir(png)
    except Exception as exc:  # noqa: BLE001 - a missing strap must not lose the shot
        log.warning("Package layer failed for shot %s: %s", shot.get("index"), exc)
        return None


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
    # In the News Package the strap carries the line instead.
    if not text or h > w or _packaged(cfg) or not cfg.get_path("visuals.lower_thirds", True):
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

    z0, z1, x0, x1, y0, y1 = MOVES[seed % len(MOVES)]
    # Eased, so the camera settles instead of moving like a machine.
    p = f"(1-pow(1-on/{frames},2))"
    z = f"{z0}+({z1 - z0:.3f})*{p}"
    x = f"(iw-iw/zoom)*({x0}+({x1 - x0})*{p})"
    y = f"(ih-ih/zoom)*({y0}+({y1 - y0})*{p})"

    graph = (
        f"[0:v]scale={w*2}:{h*2}:force_original_aspect_ratio=increase,"
        f"crop={w*2}:{h*2},"
        f"zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s={w}x{h}:fps={fps},"
        f"{_grade(cfg)}[pic]"
    )
    inputs, graph = _with_plate(["-loop", "1", "-i", str(src)], graph, plate, fps)
    return _encode(inputs, graph, seconds, fps, dest)


def _card_clip(frames: Path, dest: Path, seconds: float, cfg: Config,
               plate: Path | None = None) -> Path:
    """Play a card's entrance, then hold the finished frame with a slow push
    so it never sits completely still under the narration."""
    w, h, fps = _size(cfg)
    graph = (
        f"[0:v]tpad=stop_mode=clone:stop_duration={seconds + 1:.2f},"
        f"scale={w*2}:{h*2}:flags=bilinear,"
        f"zoompan=z='min(1+0.0006*on,1.08)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
        f":d=1:s={w}x{h}:fps={fps}[pic]"
    )
    inputs, graph = _with_plate(["-framerate", str(fps), "-i", str(frames / "f_%03d.png")],
                                graph, plate, fps)
    return _encode(inputs, graph, seconds, fps, dest)


def _still_clip(src: Path, dest: Path, seconds: float, cfg: Config,
                plate: Path | None = None) -> Path:
    """A card with no entrance sequence: near-imperceptible push only."""
    w, h, fps = _size(cfg)
    frames = max(int(seconds * fps), 1)
    graph = (
        f"[0:v]scale={w}:{h},zoompan=z='min(1.0+0.0006*on,1.025)':"
        f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s={w}x{h}:fps={fps}[pic]"
    )
    inputs, graph = _with_plate(["-loop", "1", "-i", str(src)], graph, plate, fps)
    return _encode(inputs, graph, seconds, fps, dest)


def _trim_video(src: Path, dest: Path, seconds: float, cfg: Config,
                plate: Path | None = None) -> Path:
    """Stock video: trim, conform, grade slightly to match the palette."""
    w, h, fps = _size(cfg)
    graph = (
        f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
        f"fps={fps},{_grade(cfg)}[pic]"
    )
    inputs, graph = _with_plate(["-stream_loop", "-1", "-i", str(src)], graph, plate, fps)
    return _encode(inputs, graph, seconds, fps, dest)


def build_shot_clips(cfg: Config, shots: list[dict], workdir: Path,
                     headline: str = "") -> list[Path]:
    """One clip per shot. Each runs XFADE longer than the shot so it can
    dissolve into the next; shots that produce no clip are marked so the
    join stays in step with the narration. `headline` goes on the News
    Package strap (long videos)."""
    clips_dir = workdir / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)
    clips: list[Path] = []
    packaged = _packaged(cfg)
    prev: dict | None = None

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
        plate = _package(shot, prev, cfg, dest, headline) if packaged else _plate(shot, cfg, dest)
        if asset.suffix.lower() == ".mp4":
            _trim_video(asset, dest, seconds, cfg, plate)
        elif shot.get("visual_type") in {"photo", "ai_still"} and not frames:
            _ken_burns(asset, dest, seconds, cfg, seed=shot["index"], plate=plate)
        elif frames:
            _card_clip(frames, dest, seconds, cfg, plate if packaged else None)
        else:
            _still_clip(asset, dest, seconds, cfg, plate if packaged else None)

        shot["clip"] = str(dest)
        clips.append(dest)
        prev = shot
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


def music_credit(cfg: Config, seed: str) -> str:
    """The attribution the track's Creative Commons licence requires."""
    track = pick_music(cfg, seed)
    title = (cfg.get_path("audio.credits", {}) or {}).get(track.stem) if track else None
    if not title:
        return ""
    return (f'Music: "{title}" by Kevin MacLeod (incompetech.com), mixed under the narration.\n'
            "Licensed under Creative Commons: By Attribution 4.0\n"
            "https://creativecommons.org/licenses/by/4.0/")


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
    cuts = CUTS_TALL if h > w else CUTS_WIDE
    move = "smoothup" if h > w else "smoothleft"
    style = _style(cfg)
    # News Package: hard cuts inside a section, a band wipe between sections.
    # Caption News: hard cuts only. Classic: dissolves and slides.
    hard = 1.0 / fps
    wipes: list[float] = []
    last = "c0"
    for i in range(1, len(used)):
        new_beat = used[i].get("beat") != used[i - 1].get("beat")
        at = starts[used[i]["index"]]
        if style == motion.NEWS and w > h:
            kind, dur = ("wipeleft", WIPE) if new_beat else ("fade", hard)
            if new_beat:
                wipes.append(at)
        elif style == motion.CAPTION:
            kind, dur = "fade", hard
        else:
            kind, dur = (move if new_beat else cuts[i % len(cuts)]), XFADE
        parts.append(f"[{last}][c{i}]xfade=transition={kind}:duration={dur:.3f}"
                     f":offset={at:.3f}[x{i}]")
        last = f"x{i}"
    if wipes:
        # The band rides the wipe's edge, which moves right to left.
        band = motion.band(cfg.get("visuals", {}), workdir / "band.png")
        n = len(used)
        inputs += ["-loop", "1", "-i", str(band)]
        parts.append(f"[{n}:v]split={len(wipes)}" + "".join(f"[b{j}]" for j in range(len(wipes))))
        lead = int(14 * h / 1080)
        for j, at in enumerate(wipes):
            parts.append(
                f"[{last}][b{j}]overlay=x='{w}*(1-(t-{at:.3f})/{WIPE})-{lead}':y=0:"
                f"enable='between(t,{at:.3f},{at + WIPE:.3f})':eof_action=pass[w{j}]")
            last = f"w{j}"
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
