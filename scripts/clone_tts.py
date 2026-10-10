"""Speak a batch of lines in the channel owner's cloned voice (Chatterbox).

Runs in its own virtualenv (requirements-voice.txt): the model pins older
torch and numpy builds than the main pipeline uses. src/voice.py calls this
once per video with a jobs file, so the 3 GB model loads a single time.

    python scripts/clone_tts.py jobs.json

jobs.json:
    {"refs": {"hi": "assets/voice/ref_hi.wav", "en": "assets/voice/ref_en.wav"},
     "settings": {"exaggeration": 0.5, "cfg_weight": 0.5, "temperature": 0.7},
     "items": [{"text": "...", "language": "hi", "out": ".../shot_000.wav"}]}
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

import torch
import torchaudio

MAX_CHARS = 240        # the model drifts on longer inputs; split at sentence ends
ATTEMPTS = 3           # a take that is far too short or long is re-spoken
PAUSE_S = 0.22         # between chunks of one line


def pick_device() -> str:
    # Apple's GPU backend measured slower and less steady than CPU for this
    # model, so it is opt-in: VOICE_DEVICE=mps.
    return os.environ.get("VOICE_DEVICE") or ("cuda" if torch.cuda.is_available() else "cpu")


def chunks(text: str) -> list[str]:
    """Sentence-sized pieces no longer than MAX_CHARS."""
    parts = [p.strip() for p in re.split(r"(?<=[.।?!])\s+", text) if p.strip()]
    out: list[str] = []
    for part in parts:
        while len(part) > MAX_CHARS:
            cut = max(part.rfind(c, 0, MAX_CHARS) for c in ",;:—–")
            cut = cut + 1 if cut > 40 else part.rfind(" ", 0, MAX_CHARS)
            cut = cut if cut > 0 else MAX_CHARS
            out.append(part[:cut].strip())
            part = part[cut:].strip()
        if out and len(out[-1]) + len(part) + 1 <= MAX_CHARS:
            out[-1] = f"{out[-1]} {part}"
        else:
            out.append(part)
    return out


def plausible(seconds: float, text: str) -> float:
    """0 when the take's length fits the text, else how far outside it is.
    Measured speaking rate of this voice is 13-18 characters a second."""
    n = len(text)
    low, high = n / 24.0, n / 7.0 + 1.5
    if seconds < low:
        return low - seconds
    if seconds > high:
        return seconds - high
    return 0.0


def speak(model, text: str, language: str, settings: dict) -> torch.Tensor:
    best, best_miss = None, None
    for attempt in range(ATTEMPTS):
        wav = model.generate(text, language_id=language, **settings)
        miss = plausible(wav.shape[-1] / model.sr, text)
        if best is None or miss < best_miss:
            best, best_miss = wav, miss
        if miss == 0:
            break
        print(f"  retake {attempt + 1}: length off by {miss:.1f}s", flush=True)
    return best


def main() -> int:
    jobs = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    device = pick_device()

    from chatterbox.mtl_tts import ChatterboxMultilingualTTS

    # The checkpoints were saved from CUDA; load them onto whatever is here.
    _load = torch.load
    torch.load = lambda *a, **k: _load(*a, **{**k, "map_location": torch.device(device)})
    started = time.time()
    model = ChatterboxMultilingualTTS.from_pretrained(device=device)
    torch.load = _load
    print(f"Voice model ready on {device} in {time.time() - started:.0f}s", flush=True)

    settings = {"exaggeration": 0.5, "cfg_weight": 0.5, "temperature": 0.7,
                **(jobs.get("settings") or {})}
    exaggeration = settings.pop("exaggeration")
    items, refs = jobs["items"], jobs["refs"]
    spoken = 0.0

    for language in sorted({i["language"] for i in items}):
        model.prepare_conditionals(refs.get(language) or refs["en"], exaggeration=exaggeration)
        for n, item in enumerate(i for i in items if i["language"] == language):
            t0 = time.time()
            pieces = [speak(model, c, language, settings) for c in chunks(item["text"])]
            gap = torch.zeros(1, int(PAUSE_S * model.sr))
            wav = pieces[0]
            for piece in pieces[1:]:
                wav = torch.cat([wav, gap, piece], dim=-1)
            torchaudio.save(item["out"], wav, model.sr)
            secs = wav.shape[-1] / model.sr
            spoken += secs
            print(f"[{language} {n + 1}] {secs:.1f}s of speech in {time.time() - t0:.0f}s",
                  flush=True)

    print(f"Done: {spoken / 60:.1f} min of speech in {(time.time() - started) / 60:.1f} min",
          flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
