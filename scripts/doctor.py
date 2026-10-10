#!/usr/bin/env python3
"""Check every credential the pipeline needs, with the smallest real call each
service allows. Prints a table; never prints a secret.

    python scripts/doctor.py

Exits non-zero if anything the pipeline cannot run without is broken.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import llm  # noqa: E402
from src.config import env, load  # noqa: E402

rows: list[tuple[str, str, str]] = []
failed_required = False


def record(name: str, ok: bool | None, detail: str, required: bool = True) -> None:
    global failed_required
    mark = "OK" if ok else ("SKIP" if ok is None else "FAIL")
    if ok is False and required:
        failed_required = True
    rows.append((name, mark, detail.replace("\n", " ")[:200]))
    print(f"{mark:5} {name:22} {detail}", flush=True)


def check(name: str, required: bool = True):
    def wrap(fn):
        try:
            result = fn()
            if result is None:
                record(name, None, "not configured", required)
            else:
                record(name, True, result, required)
        except Exception as exc:  # noqa: BLE001
            detail = str(exc)
            for key in ("GEMINI_API_KEY", "TELEGRAM_BOT_TOKEN", "PIXABAY_API_KEY",
                        "CF_API_TOKEN", "ELEVENLABS_API_KEY"):
                secret = os.getenv(key)
                if secret:
                    detail = detail.replace(secret, "***")
            record(name, False, f"{type(exc).__name__}: {detail}", required)
        return fn
    return wrap


@check("Gemini (models)")
def _gemini_models():
    r = requests.get("https://generativelanguage.googleapis.com/v1beta/models",
                     headers={"x-goog-api-key": env("GEMINI_API_KEY", required=True)},
                     params={"pageSize": 200}, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:160]}")
    names = [m["name"].split("/")[-1] for m in r.json().get("models", [])
             if "generateContent" in m.get("supportedGenerationMethods", [])]
    flash = sorted(n for n in names if "flash" in n and "tts" not in n
                   and "image" not in n and "live" not in n)
    has = llm.GEMINI_MODEL in names
    return (f"configured model {llm.GEMINI_MODEL} "
            f"{'available' if has else 'NOT AVAILABLE'}; flash models: {', '.join(flash)}")


@check("Gemini (generate)")
def _gemini():
    return "replied: " + llm._gemini("Reply with the single word: ready", None, 0).strip()[:40]


@check("Groq (generate)")
def _groq():
    reply = llm._groq("Reply with the single word: ready", None, 0).strip()[:40]
    return f"model {llm._groq_model} replied: {reply}"


@check("Cloudflare FLUX")
def _cf():
    from src.visuals import ai_images
    if not (env("CF_ACCOUNT_ID") and env("CF_API_TOKEN")):
        return None
    dest = Path(tempfile.mkdtemp()) / "t.png"
    account, token = env("CF_ACCOUNT_ID"), env("CF_API_TOKEN")
    r = requests.post(
        f"https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{ai_images.MODEL}",
        headers={"Authorization": f"Bearer {token}"},
        json={"prompt": "an empty boardroom at night, amber light"}, timeout=90)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
    import base64
    dest.write_bytes(base64.b64decode(r.json()["result"]["image"]))
    from PIL import Image
    return f"image generated, {Image.open(dest).size[0]}x{Image.open(dest).size[1]}"


@check("Pixabay", required=False)
def _pixabay():
    key = env("PIXABAY_API_KEY")
    if not key:
        return None
    r = requests.get("https://pixabay.com/api/",
                     params={"key": key, "q": "office building", "per_page": 3}, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:120]}")
    v = requests.get("https://pixabay.com/api/videos/",
                     params={"key": key, "q": "stock market", "per_page": 3}, timeout=30)
    return (f"{r.json().get('totalHits', 0)} image hits; "
            f"video endpoint HTTP {v.status_code}, {v.json().get('totalHits', 0) if v.ok else 0} hits")


@check("ElevenLabs (plan)", required=False)
def _eleven_plan():
    r = requests.get("https://api.elevenlabs.io/v1/user/subscription",
                     headers={"xi-api-key": env("ELEVENLABS_API_KEY", required=True)}, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:160]}")
    s = r.json()
    left = s.get("character_limit", 0) - s.get("character_count", 0)
    return (f"tier={s.get('tier')} characters left this cycle: {left:,} "
            f"of {s.get('character_limit', 0):,} (one 12-min episode needs ~11,000)")


@check("ElevenLabs (voice)")
def _eleven_voice():
    vid = env("ELEVENLABS_VOICE_ID", required=True)
    r = requests.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{vid}",
        headers={"xi-api-key": env("ELEVENLABS_API_KEY", required=True)},
        json={"text": "Ready.", "model_id": load().get_path("voice.model")}, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
    return f"synthesised a 1-word test clip ({len(r.content) // 1024} KB)"


@check("Telegram")
def _telegram():
    token = env("TELEGRAM_BOT_TOKEN", required=True)
    base = f"https://api.telegram.org/bot{token}"
    me = requests.get(f"{base}/getMe", timeout=30).json()
    if not me.get("ok"):
        raise RuntimeError(f"getMe: {me.get('description')}")
    hook = requests.get(f"{base}/getWebhookInfo", timeout=30).json().get("result", {})
    sent = requests.post(f"{base}/sendMessage", timeout=30, json={
        "chat_id": env("TELEGRAM_CHAT_ID", required=True),
        "text": "Terminal Value: connection test. Approval buttons will arrive in this chat.",
    }).json()
    if not sent.get("ok"):
        raise RuntimeError(f"sendMessage: {sent.get('description')}")
    note = " (WARNING: a webhook is set; button polling will not work)" if hook.get("url") else ""
    return f"bot @{me['result'].get('username')} delivered a test message{note}"


@check("YouTube")
def _youtube():
    from src import upload
    yt = upload.client()
    resp = yt.channels().list(part="snippet,status,statistics", mine=True).execute()
    items = resp.get("items", [])
    if not items:
        raise RuntimeError("token is valid but the signed-in account has no channel")
    ch = items[0]
    want = load().get_path("channel.channel_id")
    match = "matches config" if ch["id"] == want else f"DOES NOT MATCH config ({want})"
    return (f"channel '{ch['snippet']['title']}' {ch['id']} {match}; "
            f"long uploads: {ch['status'].get('longUploadsStatus')}; "
            f"videos: {ch['statistics'].get('videoCount')}")


def main() -> int:
    summary = os.getenv("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write("| Check | Result | Detail |\n|---|---|---|\n")
            for name, mark, detail in rows:
                fh.write(f"| {name} | {mark} | {detail.replace('|', '/')} |\n")
    return 1 if failed_required else 0


if __name__ == "__main__":
    sys.exit(main())
