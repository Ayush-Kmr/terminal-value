#!/usr/bin/env python3
"""Synthesise the comedy sound effects the Bhojpuri skits use (src/skit.py).

    python scripts/make_sfx.py

Writes assets/sfx/*.wav. Made from sine waves and noise here, so they belong
to the channel: no licence, no Content ID claim.
"""
from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

SR = 48000
OUT = Path(__file__).resolve().parent.parent / "assets" / "sfx"
rng = np.random.default_rng(3)


def t(seconds: float) -> np.ndarray:
    return np.arange(int(SR * seconds)) / SR


def sweep(f0: float, f1: float, seconds: float) -> np.ndarray:
    """A sine gliding from f0 to f1 (exponential)."""
    x = t(seconds)
    f = f0 * (f1 / f0) ** (x / seconds)
    return np.sin(2 * np.pi * np.cumsum(f) / SR)


def lowpass(x: np.ndarray, a: float) -> np.ndarray:
    y = np.empty_like(x)
    acc = 0.0
    for i, v in enumerate(x):
        acc += a * (v - acc)
        y[i] = acc
    return y


def whoosh() -> np.ndarray:
    n = rng.normal(0, 1, int(SR * 0.42))
    x = t(0.42)
    env = np.sin(np.pi * x / 0.42) ** 2
    # Brighter in the middle: a band that opens and closes as it passes.
    out = lowpass(n, 0.04) * (1 - env) + (n - lowpass(n, 0.25)) * env * 0.6
    return out * env * 0.9


def pop() -> np.ndarray:
    return sweep(900, 180, 0.09) * np.exp(-t(0.09) * 40)


def boing() -> np.ndarray:
    x = t(0.75)
    f = 160 + 90 * np.exp(-x * 4) * np.sin(2 * np.pi * 14 * x) + 60 * x
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-x * 4.5)


def ding() -> np.ndarray:
    x = t(1.1)
    tone = sum(a * np.sin(2 * np.pi * f * x) for f, a in ((1320, 1), (2640, .35), (3960, .15)))
    return tone * np.exp(-x * 4.2) * 0.6


def tom(f: float, seconds: float = 0.28) -> np.ndarray:
    x = t(seconds)
    return (sweep(f * 1.6, f, seconds) + 0.3 * rng.normal(0, 1, len(x)) * np.exp(-x * 60)) \
        * np.exp(-x * 13)


def cymbal(seconds: float = 0.9) -> np.ndarray:
    n = rng.normal(0, 1, int(SR * seconds))
    hi = n - lowpass(n, 0.5)
    return hi * np.exp(-t(seconds) * 5) * 0.8


def rimshot() -> np.ndarray:
    """Ba-dum-tss."""
    out = np.zeros(int(SR * 1.45))
    for at, part in ((0.0, tom(210)), (0.17, tom(150)), (0.40, cymbal())):
        i = int(at * SR)
        out[i:i + len(part)] += part[: len(out) - i]
    return out


def gasp() -> np.ndarray:
    """A quick dramatic sting: two falling notes."""
    a = sweep(660, 620, 0.16) * np.exp(-t(0.16) * 6)
    b = sweep(520, 300, 0.5) * np.exp(-t(0.5) * 4)
    return np.concatenate([a, b]) * 0.7


def save(name: str, x: np.ndarray) -> None:
    x = x / (np.abs(x).max() or 1) * 0.8
    fade = min(len(x) // 10, int(SR * 0.01))
    x[-fade:] *= np.linspace(1, 0, fade)
    with wave.open(str(OUT / f"{name}.wav"), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((x * 32767).astype("<i2").tobytes())
    print(f"{name}.wav  {len(x) / SR:.2f}s")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, fn in (("whoosh", whoosh), ("pop", pop), ("boing", boing), ("ding", ding),
                     ("rimshot", rimshot), ("gasp", gasp)):
        save(name, fn())


if __name__ == "__main__":
    main()
