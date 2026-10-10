"""Narration, rendered per shot so audio and visuals stay locked.

Two providers (voice.provider in config.yaml): "clone" speaks in the channel
owner's own cloned voice with an open model that runs on the build machine and
costs nothing per character; "elevenlabs" calls the ElevenLabs API.

Per-shot rendering (rather than one long file) means a re-write of one sentence
costs one API call, not a full re-render — and shot durations come straight from
the audio rather than being guessed.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from pathlib import Path

import requests
from tenacity import retry, retry_if_not_exception_type, stop_after_attempt, wait_exponential

from .config import Config, env
from .speech_text import spoken

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent.parent
API = "https://api.elevenlabs.io/v1/text-to-speech"


class VoiceError(RuntimeError):
    """A voice failure that a retry cannot fix."""


def voice_id_for(cfg: Config, region: str | None, language: str | None) -> str:
    """Region voice, else language voice, else the default. config.yaml maps
    each to the NAME of an environment variable; unset ones fall through."""
    voices = cfg.get_path("voice.voices", {}) or {}
    for key in (region, language):
        var = voices.get(key) if key else None
        if var and env(var):
            return env(var)
    return env("ELEVENLABS_VOICE_ID", required=True)


@retry(stop=stop_after_attempt(4), wait=wait_exponential(min=3, max=40),
       retry=retry_if_not_exception_type(VoiceError), reraise=True)
def _synthesise(text: str, out: Path, cfg: Config, voice_id: str) -> None:
    resp = requests.post(
        f"{API}/{voice_id}",
        headers={
            "xi-api-key": env("ELEVENLABS_API_KEY", required=True),
            "Content-Type": "application/json",
        },
        json={
            "text": text,
            "model_id": cfg.get_path("voice.model", "eleven_multilingual_v2"),
            "voice_settings": {
                "stability": cfg.get_path("voice.stability", 0.45),
                "similarity_boost": cfg.get_path("voice.similarity_boost", 0.75),
                "style": cfg.get_path("voice.style", 0.15),
                "use_speaker_boost": True,
            },
        },
        timeout=120,
    )
    if resp.status_code in (401, 402, 403, 422):
        # Out of characters, bad key or bad voice: retrying will not help.
        raise VoiceError(f"ElevenLabs HTTP {resp.status_code}: {resp.text[:300]}")
    resp.raise_for_status()
    out.write_bytes(resp.content)


def _polish(raw: Path, out: Path) -> None:
    """Trim the silence the model leaves at either end and level the take."""
    trim = "silenceremove=start_periods=1:start_threshold=-48dB:start_silence=0.05"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(raw), "-af",
         f"{trim},areverse,{trim},areverse,highpass=f=70,"
         "acompressor=threshold=-22dB:ratio=2:attack=10:release=150,"
         "alimiter=limit=0.89:level=disabled,afade=t=in:d=0.01",
         "-ar", "44100", "-ac", "1", "-b:a", "160k", str(out)],
        check=True,
    )


def _clone_batch(cfg: Config, todo: list[tuple[str, Path]], language: str | None,
                 audio_dir: Path) -> None:
    """Speak every missing shot in one run of scripts/clone_tts.py, which lives
    in its own virtualenv and loads the model once."""
    python = (os.environ.get("VOICE_PYTHON")
              or cfg.get_path("voice.clone.python", "")
              or str(ROOT / ".venv-voice" / "bin" / "python"))
    if not Path(python).exists():
        raise VoiceError(f"Voice environment not found at {python}. See SETUP.md, 'Own voice'.")
    language = language if language in ("hi", "en") else "en"
    refs = {k: str(ROOT / v) for k, v in
            (cfg.get_path("voice.clone.references", {}) or {}).items()}
    if not refs.get("en") or not all(Path(r).exists() for r in refs.values()):
        raise VoiceError("voice.clone.references must point at existing recordings")

    raws = [path.with_suffix(".wav") for _, path in todo]
    jobs = audio_dir / "clone_jobs.json"
    jobs.write_text(json.dumps({
        "refs": refs,
        "settings": cfg.get_path("voice.clone.settings", {}) or {},
        "items": [{"text": spoken(text, language), "language": language, "out": str(raw)}
                  for (text, _), raw in zip(todo, raws)],
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    log.info("Cloned voice: speaking %d shots (%s)", len(todo), language)
    res = subprocess.run([python, str(ROOT / "scripts" / "clone_tts.py"), str(jobs)],
                         stdout=sys.stderr, stderr=subprocess.STDOUT)
    if res.returncode != 0:
        raise VoiceError(f"Cloned voice run failed with exit code {res.returncode}")
    for (_, path), raw in zip(todo, raws):
        _polish(raw, path)
        raw.unlink()


def duration(path: Path) -> float:
    """Exact duration via ffprobe. Shot lengths derive from this."""
    res = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(res.stdout.strip())


def narrate_shots(cfg: Config, shots: list[dict], outdir: Path,
                  region: str | None = None, language: str | None = None) -> list[dict]:
    """Synthesise one audio file per shot; attach real durations."""
    cloned = cfg.get_path("voice.provider", "elevenlabs") == "clone"
    audio_dir = outdir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    if cloned:
        todo = [(text, audio_dir / f"shot_{s['index']:03d}.mp3") for s in shots
                if (text := (s.get("narration") or "").strip())]
        todo = [(text, path) for text, path in todo if not path.exists()]
        if todo:
            _clone_batch(cfg, todo, language, audio_dir)
    else:
        voice_id = voice_id_for(cfg, region, language)

    min_s, max_s = cfg.get_path("visuals.shot_seconds", [4, 9])
    hold_ms = cfg.get_path("voice.pause_after_beat_ms", 650)

    for shot in shots:
        text = (shot.get("narration") or "").strip()
        path = audio_dir / f"shot_{shot['index']:03d}.mp3"
        if not text:
            shot["audio"] = None
            shot["duration"] = float(min_s)
            continue
        if not path.exists():
            _synthesise(text, path, cfg, voice_id)
        spoken = duration(path)

        # Pad so no shot is shorter than the minimum; hold shots get silence after.
        pad = hold_ms / 1000.0 if shot.get("emphasis") == "hold" else 0.25
        shot["audio"] = str(path)
        shot["spoken_duration"] = spoken
        # Never shorter than the narration itself: a clipped shot lets its
        # audio run on underneath the next shot's.
        shot["duration"] = max(float(min_s), spoken + pad)
        log.debug("shot %03d: %.2fs spoken, %.2fs on screen",
                  shot["index"], spoken, shot["duration"])

    total = sum(s["duration"] for s in shots)
    log.info("Narration complete: %d shots, %.1f min", len(shots), total / 60)
    return shots


def estimate_cost(script_text: str) -> float:
    """ElevenLabs bills per character. A 12-minute script is ~11k characters.
    (The cloned voice costs nothing; this applies to voice.provider: elevenlabs.)"""
    chars = len(script_text)
    return round(chars / 1_000_000 * 150, 3)   # ~$150/1M chars on Creator tier
