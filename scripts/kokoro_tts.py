"""Speak a batch of lines with Kokoro-82M, an open text-to-speech model.

Apache-2.0, so the audio can be used commercially. Runs in its own virtualenv
(requirements-kokoro.txt) on the CPU, faster than real time, at no cost per
character. src/voice.py calls this once per video with a jobs file.

    python scripts/kokoro_tts.py jobs.json

jobs.json:
    {"voice": "af_heart", "speed": 1.0, "lexicon": {"Nvidia": "ɛnvˈɪdiə"},
     "items": [{"text": "...", "out": ".../shot_000.wav"}]}
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf
from kokoro import KPipeline

RATE = 24000


def marked(text: str, lexicon: dict[str, str]) -> str:
    """Fixed pronunciations, in the [word](/phonemes/) form the model reads."""
    for word, phonemes in sorted(lexicon.items(), key=lambda kv: -len(kv[0])):
        text = re.sub(rf"(?<![\w\[]){re.escape(word)}(?![\w\]])",
                      lambda m: f"[{m[0]}](/{phonemes}/)", text)
    return text


def main() -> int:
    jobs = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    voice, speed = jobs.get("voice", "af_heart"), float(jobs.get("speed", 1.0))
    lexicon = jobs.get("lexicon") or {}
    started = time.time()
    pipe = KPipeline(lang_code=voice[0], repo_id="hexgrad/Kokoro-82M")
    print(f"Kokoro ready in {time.time() - started:.0f}s ({voice})", flush=True)
    total = 0.0
    for item in jobs["items"]:
        audio = [np.asarray(r.audio) for r in pipe(marked(item["text"], lexicon), voice=voice,
                                                   speed=speed) if r.audio is not None]
        if not audio:
            raise RuntimeError(f"No audio for: {item['text'][:80]}")
        wav = np.concatenate(audio)
        sf.write(item["out"], wav, RATE)
        total += len(wav) / RATE
    took = time.time() - started
    print(f"Done: {total / 60:.1f} min of speech in {took / 60:.1f} min", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
