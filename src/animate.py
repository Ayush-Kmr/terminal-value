"""Talking-character animation for the Bhojpuri skits.

Each line's cartoon moves with its own voice: the loudness of the take, frame
by frame, drives a small bounce, push and squash, so the character visibly
"talks" while speaking and settles in the pauses. The speaker's name rides on
a coloured tag. Frames are drawn with Pillow and piped straight into ffmpeg,
so the clip and its audio share one clock: frame k is audio time k / fps.

No lip-sync model: open ones need a GPU and draw cartoon mouths badly.
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

log = logging.getLogger(__name__)

W, H = 1080, 1920


def envelope(audio: Path | None, fps: int, frames: int) -> np.ndarray:
    """0-1 loudness per video frame: fast to rise, slower to fall, like a mouth."""
    out = np.zeros(frames, dtype="float32")
    if not audio or not Path(audio).exists():
        return out
    sr = 16000
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(audio), "-ac", "1", "-ar", str(sr),
                          "-f", "f32le", "-"], capture_output=True).stdout
    pcm = np.frombuffer(raw, dtype="float32")
    hop = sr // fps
    n = min(frames, len(pcm) // hop)
    if n == 0:
        return out
    rms = np.sqrt((pcm[: n * hop].reshape(n, hop) ** 2).mean(axis=1))
    top = np.percentile(rms, 95) or 1.0
    level = np.clip(rms / top, 0, 1)
    acc = 0.0
    for i, v in enumerate(level):
        acc += (0.6 if v > acc else 0.18) * (v - acc)
        out[i] = acc
    return out


def tag(name: str, colour: str, vis: dict) -> Image.Image:
    """The speaker's name on a rounded pill."""
    from .visuals.motion import _font

    font = _font(vis, "display", 56)
    probe = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    w = int(probe.textlength(name, font=font)) + 64
    img = Image.new("RGBA", (w + 8, 96), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([6, 8, w + 6, 92], radius=40, fill="#0B0614")       # shadow
    d.rounded_rectangle([0, 0, w, 84], radius=40, fill=colour)
    d.text((w / 2, 42), name, font=font, fill="#FFFFFF", anchor="mm",
           stroke_width=3, stroke_fill="#0B0614")
    return img


def talking_clip(picture: Path, audio: Path | None, seconds: float, dest: Path,
                 name: str = "", colour: str = "#FF4D6D", vis: dict | None = None,
                 fps: int = 30, seed: int = 0) -> Path:
    """A clip of `seconds` in which the picture talks along with `audio`."""
    frames = max(int(round(seconds * fps)), 1)
    env = envelope(audio, fps, frames)
    base = Image.open(picture).convert("RGB").resize((W, H))
    big = base.resize((int(W * 1.12), int(H * 1.12)), Image.BILINEAR)
    bw, bh = big.size
    label = tag(name, colour, vis or {}) if name else None
    drift = 1 if seed % 2 else -1                 # alternate the slow push sideways
    proc = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
         "-s", f"{W}x{H}", "-r", str(fps), "-i", "-", "-c:v", "libx264", "-preset", "veryfast",
         "-crf", "19", "-pix_fmt", "yuv420p", "-t", f"{seconds:.3f}", str(dest)],
        stdin=subprocess.PIPE)
    try:
        for k in range(frames):
            p, e = k / frames, float(env[k])
            # Slow push across the line, plus the talk: push, lift and squash.
            z = 1.0 + 0.03 * p + 0.035 * e
            sx, sy = z * (1 + 0.012 * e), z * (1 - 0.008 * e)
            cw, ch = bw / 1.12 / sx, bh / 1.12 / sy
            cx = bw / 2 + drift * 18 * p
            cy = bh / 2 - 22 * e + 6 * np.sin(k / fps * 9) * e
            box = (int(cx - cw / 2), int(cy - ch / 2), int(cx + cw / 2), int(cy + ch / 2))
            frame = big.crop(box).resize((W, H), Image.BILINEAR)
            if label is not None:
                # Pops in over the first 6 frames, then holds.
                s = min(1.0, (k + 1) / 6)
                lab = label if s >= 1 else label.resize(
                    (max(int(label.width * s), 1), max(int(label.height * s), 1)))
                frame.paste(lab, (56, 1300 - lab.height // 2), lab)
            proc.stdin.write(frame.tobytes())
        proc.stdin.close()
    except BrokenPipeError:
        pass
    if proc.wait() != 0:
        raise RuntimeError(f"Talking clip failed for {picture.name}")
    return dest
