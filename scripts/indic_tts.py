"""Speak a batch of lines in an Indian language with AI4Bharat's Indic
Parler-TTS (Apache-2.0). Used for narration languages the cloned voice
cannot speak, such as Tamil.

Runs in its own virtualenv (requirements-indic.txt): the model pins an older
transformers than the main pipeline uses. src/voice.py calls this once per
video with a jobs file, so the model loads a single time. The model is gated
on Hugging Face: accept its terms once and set HF_TOKEN.

    python scripts/indic_tts.py jobs.json

jobs.json:
    {"speaker": "Jaya",
     "description": "Jaya speaks at a moderate pace ...",
     "items": [{"text": "...", "out": ".../shot_000.wav"}]}
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf
import torch

MODEL = "ai4bharat/indic-parler-tts"
MAX_CHARS = 220        # trained on clips under ~30 s; split longer lines
BATCH = 6              # chunks generated together; sorted by length to limit padding
PAUSE_S = 0.2          # between chunks of one line


def chunks(text: str) -> list[str]:
    """Sentence-sized pieces no longer than MAX_CHARS."""
    parts = [p.strip() for p in re.split(r"(?<=[.?!।])\s+", text) if p.strip()]
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


def main() -> int:
    jobs = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    from parler_tts import ParlerTTSForConditionalGeneration
    from transformers import AutoTokenizer

    device = os.environ.get("VOICE_DEVICE") or ("cuda" if torch.cuda.is_available() else "cpu")
    torch.manual_seed(int(jobs.get("seed", 7)))     # same voice colour every run
    started = time.time()
    model = ParlerTTSForConditionalGeneration.from_pretrained(MODEL).to(device)
    tok = AutoTokenizer.from_pretrained(MODEL)
    desc_tok = AutoTokenizer.from_pretrained(model.config.text_encoder._name_or_path)
    sr = model.config.sampling_rate
    print(f"Indic voice ready on {device} in {time.time() - started:.0f}s", flush=True)

    # Every chunk of every line, generated in length-sorted batches.
    pieces = [(i, j, c) for i, item in enumerate(jobs["items"])
              for j, c in enumerate(chunks(item["text"]))]
    order = sorted(range(len(pieces)), key=lambda k: len(pieces[k][2]))
    audio: dict[tuple[int, int], np.ndarray] = {}
    desc = jobs["description"]
    for b in range(0, len(order), BATCH):
        batch = [pieces[k] for k in order[b:b + BATCH]]
        t0 = time.time()
        d = desc_tok([desc] * len(batch), return_tensors="pt", padding=True).to(device)
        p = tok([c for _, _, c in batch], return_tensors="pt", padding=True).to(device)
        with torch.no_grad():
            gen = model.generate(input_ids=d.input_ids, attention_mask=d.attention_mask,
                                 prompt_input_ids=p.input_ids,
                                 prompt_attention_mask=p.attention_mask,
                                 do_sample=True, temperature=1.0,
                                 return_dict_in_generate=True)
        for row, (i, j, _) in enumerate(batch):
            n = int(gen.audios_length[row])
            audio[(i, j)] = gen.sequences[row, :n].float().cpu().numpy()
        secs = sum(len(audio[(i, j)]) for i, j, _ in batch) / sr
        print(f"[batch {b // BATCH + 1}/{-(-len(order) // BATCH)}] {secs:.1f}s of speech "
              f"in {time.time() - t0:.0f}s", flush=True)

    gap = np.zeros(int(PAUSE_S * sr), dtype=np.float32)
    total = 0.0
    for i, item in enumerate(jobs["items"]):
        parts = [audio[(i, j)] for j in range(len(chunks(item["text"])))]
        wav = parts[0]
        for part in parts[1:]:
            wav = np.concatenate([wav, gap, part])
        sf.write(item["out"], wav, sr)
        total += len(wav) / sr
    print(f"Done: {total / 60:.1f} min of speech in {(time.time() - started) / 60:.1f} min",
          flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
