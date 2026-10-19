"""Narration, rendered per shot so audio and visuals stay locked.

Two providers (voice.provider in config.yaml): "clone" speaks in the channel
owner's own cloned voice with an open model that runs on the build machine and
costs nothing per character; "elevenlabs" calls the ElevenLabs API.

Per-shot rendering (rather than one long file) means a re-write of one sentence
costs one API call, not a full re-render — and shot durations come straight from
the audio rather than being guessed.
"""
from __future__ import annotations

import difflib
import json
import logging
import os
import re
import shutil
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
    """Trim the silence the model leaves at either end. No compressor: it
    lifts the quiet tail after every word, which is heard as room echo."""
    trim = "silenceremove=start_periods=1:start_threshold=-48dB:start_silence=0.05"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(raw), "-af",
         f"{trim},areverse,{trim},areverse,highpass=f=75,"
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
    # TQDM_DISABLE: the model draws a progress bar per sentence, which buried
    # everything else in the run log.
    res = subprocess.run([python, str(ROOT / "scripts" / "clone_tts.py"), str(jobs)],
                         stdout=sys.stderr, stderr=subprocess.STDOUT,
                         env={**os.environ, "TQDM_DISABLE": "1"})
    if res.returncode != 0:
        raise VoiceError(f"Cloned voice run failed with exit code {res.returncode}")
    for (_, path), raw in zip(todo, raws):
        _polish(raw, path)
        raw.unlink()


WHISPER = "https://api.groq.com/openai/v1/audio/transcriptions"
MATCH_OK = 0.82        # transcript vs script word match; below this, re-speak


def transcribe(path: Path, language: str | None) -> dict | None:
    """What the take actually says, with word timings (Groq's hosted Whisper,
    free tier). None when no key is set or the call fails: the check is a
    safety net, never a reason to lose a run."""
    key = env("GROQ_API_KEY")
    if not key:
        return None
    for attempt in range(4):
        try:
            with open(path, "rb") as f:
                r = requests.post(
                    WHISPER, headers={"Authorization": f"Bearer {key}"},
                    files={"file": (path.name, f)},
                    data={"model": "whisper-large-v3-turbo", "response_format": "verbose_json",
                          "timestamp_granularities[]": "word",
                          "language": language if language in ("hi", "en") else "en"},
                    timeout=60)
            if r.status_code == 429:          # ~20 requests a minute on the free tier
                import time
                time.sleep(float(r.headers.get("retry-after", 6)) + 1)
                continue
            r.raise_for_status()
            return r.json()
        except Exception as exc:  # noqa: BLE001
            log.warning("Transcription of %s failed: %s", path.name, exc)
            return None
    return None


def _words(text: str, language: str | None) -> list[str]:
    return re.findall(r"[a-z0-9']+|[\u0900-\u097F]+",
                      spoken(text, language or "en").lower())


def match(script_text: str, heard: str, language: str | None) -> float:
    """0-1: how much of the script the take really says. Catches the cloned
    voice's failure modes: a clipped ending, a repeated phrase, mumbling."""
    a, b = _words(script_text, language), _words(heard, language)
    if not a:
        return 1.0
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


def check_takes(cfg: Config, todo_text: dict[Path, str], audio_dir: Path,
                language: str | None, rounds: int = 2) -> dict[Path, dict]:
    """Listen to every take; re-speak the ones that do not say their line, and
    keep whichever take matched best. Returns each file's transcript."""
    heard: dict[Path, dict] = {}
    score: dict[Path, float] = {}
    for path, text in todo_text.items():
        t = transcribe(path, language)
        if t is None:
            return heard            # no transcriber: nothing to check against
        heard[path], score[path] = t, match(text, t.get("text", ""), language)

    for rnd in range(rounds):
        bad = [p for p, sc in score.items() if sc < MATCH_OK]
        if not bad:
            break
        log.info("Voice check round %d: re-speaking %d/%d takes (%s)", rnd + 1, len(bad),
                 len(score), ", ".join(f"{p.stem}={score[p]:.2f}" for p in bad[:8]))
        for p in bad:
            shutil.move(p, p.with_suffix(".prev.mp3"))
        _clone_batch(cfg, [(todo_text[p], p) for p in bad], language, audio_dir)
        for p in bad:
            prev = p.with_suffix(".prev.mp3")
            t = transcribe(p, language)
            sc = match(todo_text[p], (t or {}).get("text", ""), language) if t else 0.0
            if t and sc > score[p]:
                heard[p], score[p] = t, sc
                prev.unlink()
            else:
                shutil.move(prev, p)        # the old take was better
    if score:
        log.info("Voice check: %d takes, lowest match %.2f", len(score), min(score.values()))
    low = [f"{p.stem}={sc:.2f}" for p, sc in score.items() if sc < MATCH_OK]
    if low:
        log.warning("Voice check: %d takes still below %.2f: %s", len(low), MATCH_OK,
                    ", ".join(low[:10]))
    return heard


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
        missing = [(text, path) for text, path in todo if not path.exists()]
        if missing:
            _clone_batch(cfg, missing, language, audio_dir)
        heard = (check_takes(cfg, {path: text for text, path in todo}, audio_dir, language)
                 if cfg.get_path("voice.check", True) else {})
        for shot in shots:
            t = heard.get(audio_dir / f"shot_{shot['index']:03d}.mp3")
            if t and t.get("words"):
                shot["words"] = [{"word": w["word"], "start": float(w["start"]),
                                  "end": float(w["end"])} for w in t["words"]]
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
