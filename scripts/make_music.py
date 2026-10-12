"""Generate the background music beds in assets/music/.

The beds are synthesised here rather than downloaded so the channel owns them
outright: no licence to track, no attribution line, and nothing a third party
can file a Content ID claim against. Each one is a seamless loop.

    python scripts/make_music.py            # writes assets/music/bed_*.mp3

To use different music, delete these and drop any .mp3 files you have the
rights to into assets/music/ — the pipeline picks from whatever is there.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import numpy as np

SR = 44100
ROOT = Path(__file__).resolve().parent.parent
NOTES = {"C": 0, "C#": 1, "D": 2, "Eb": 3, "E": 4, "F": 5, "F#": 6, "G": 7,
         "Ab": 8, "A": 9, "Bb": 10, "B": 11}

# name -> tempo, one chord per bar (root, minor?), and how busy the top line is
TRACKS = {
    "bed_pulse":   {"bpm": 96,  "chords": ["Dm", "Bb", "F", "C"],  "arp": 1.0, "hat": 1.0},
    "bed_tension": {"bpm": 84,  "chords": ["Am", "Am", "F", "E"],  "arp": 0.6, "hat": 0.0},
    "bed_lift":    {"bpm": 104, "chords": ["C", "G", "Am", "F"],   "arp": 0.9, "hat": 0.7},
}


def hz(semitones_from_c: int, octave: int) -> float:
    return 261.6256 * 2 ** ((semitones_from_c + 12 * (octave - 4)) / 12)


def triad(name: str) -> list[int]:
    minor = name.endswith("m")
    root = NOTES[name[:-1] if minor else name]
    return [root, root + (3 if minor else 4), root + 7]


def add(buf: np.ndarray, start: float, sound: np.ndarray, pan: float = 0.0) -> None:
    """Mix a mono sound into the stereo loop at `start` seconds, wrapping at
    the end so tails carry across the loop point."""
    n = buf.shape[0]
    idx = (int(start * SR) + np.arange(len(sound))) % n
    np.add.at(buf[:, 0], idx, sound * (1 - max(pan, 0)))
    np.add.at(buf[:, 1], idx, sound * (1 + min(pan, 0)))


def env(n: int, attack: float, release: float) -> np.ndarray:
    e = np.ones(n)
    a, r = min(int(attack * SR), n // 2), min(int(release * SR), n // 2)
    e[:a] = np.sin(np.linspace(0, np.pi / 2, a)) ** 2
    e[n - r:] = np.cos(np.linspace(0, np.pi / 2, r)) ** 2
    return e


def pad(freq: float, seconds: float, detune: float) -> np.ndarray:
    """Soft string-like pad: a few harmonics, two slightly detuned voices."""
    t = np.arange(int(seconds * SR)) / SR
    out = np.zeros_like(t)
    for d in (-detune, detune):
        f = freq * (1 + d)
        for h in range(1, 7):
            out += np.sin(2 * np.pi * f * h * t + h * 1.3) / h ** 2.2
    return out * env(len(t), 0.9, 1.4)


def pluck(freq: float, seconds: float = 0.5) -> np.ndarray:
    t = np.arange(int(seconds * SR)) / SR
    tone = np.sin(2 * np.pi * freq * t) + 0.35 * np.sin(4 * np.pi * freq * t) * np.exp(-t * 14)
    return tone * np.exp(-t * 7.5) * np.minimum(t / 0.004, 1)


def kick() -> np.ndarray:
    t = np.arange(int(0.32 * SR)) / SR
    phase = 2 * np.pi * (46 * t + (95 - 46) * (1 - np.exp(-t * 30)) / 30)
    return np.sin(phase) * np.exp(-t * 13)


def hat(rng: np.random.Generator) -> np.ndarray:
    n = int(0.06 * SR)
    noise = np.diff(rng.standard_normal(n + 2), 2)      # crude high-pass
    return noise * np.exp(-np.arange(n) / SR * 90)


def build(spec: dict, bars: int = 32) -> np.ndarray:
    beat = 60 / spec["bpm"]
    bar = 4 * beat
    buf = np.zeros((int(round(bars * bar * SR)), 2))
    rng = np.random.default_rng(7)
    pattern = [0, 2, 1, 2, 0, 2, 1, 3]                  # eighth-note arpeggio

    for b in range(bars):
        t0 = b * bar
        notes = triad(spec["chords"][b % len(spec["chords"])])
        section = (b // 8) % 4                          # 8-bar sections: build, full, thin, full
        for i, n in enumerate(notes):
            add(buf, t0 - 0.3, 0.085 * pad(hz(n, 3), bar + 1.6, 0.0022), pan=(i - 1) * 0.35)
        add(buf, t0 - 0.3, 0.05 * pad(hz(notes[0], 4), bar + 1.6, 0.003))

        # Bass: a sine that swells on every eighth, which is what gives a
        # bed forward motion without drums getting in the narrator's way.
        t = np.arange(int(bar * SR)) / SR
        swell = 0.62 + 0.38 * np.sin(2 * np.pi * t / (beat / 2) - np.pi / 2)
        bass = np.sin(2 * np.pi * hz(notes[0], 2) * t) * swell * env(len(t), 0.02, 0.08)
        add(buf, t0, 0.20 * bass)

        if section != 0:
            for k in (0, 2):
                add(buf, t0 + k * beat, 0.34 * kick())
        if spec["hat"] and section in (1, 3):
            for k in range(4):
                add(buf, t0 + (k + 0.5) * beat, 0.018 * spec["hat"] * hat(rng), pan=0.25)

        if section != 2:
            tones = notes + [notes[0] + 12]
            for k, step in enumerate(pattern):
                if rng.random() > spec["arp"]:
                    continue
                p = 0.085 * pluck(hz(tones[step], 5))
                when = t0 + k * beat / 2
                add(buf, when, p, pan=-0.3)
                # Dotted-eighth echoes, alternating sides.
                for e in range(1, 4):
                    add(buf, when + e * 0.75 * beat, p * 0.42 ** e, pan=0.45 * (-1) ** e)

    buf = np.tanh(buf * 1.4) / 1.4
    return buf / np.abs(buf).max() * 0.7


def main() -> int:
    out_dir = ROOT / "assets" / "music"
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, spec in TRACKS.items():
        pcm = (build(spec) * 32767).astype("<i2").tobytes()
        out = out_dir / f"{name}.mp3"
        # Level every bed the same so the mix in render.py behaves the same
        # whichever one is picked. Roll off the top: it sits under a voice.
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "s16le", "-ar", str(SR), "-ac", "2",
             "-i", "-", "-af", "lowpass=f=9000,loudnorm=I=-20:TP=-3:LRA=7",
             "-ar", str(SR), "-b:a", "160k", str(out)],
            input=pcm, check=True)
        print(f"{out.relative_to(ROOT)}  {len(pcm) / 4 / SR:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
