"""AI illustration via Cloudflare Workers AI (free tier: 10,000 neurons/day).

Stills only — never generated video. A 12-minute video of generated clips costs
$30-65 at current API rates and still can't show a real logo, a real founder or
a real balance sheet. Stills are for mood, era and abstraction; everything
factual comes from motion graphics or archival material.
"""
from __future__ import annotations

import base64
import logging
from pathlib import Path

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from ..config import env

log = logging.getLogger(__name__)

MODEL = "@cf/black-forest-labs/flux-1-schnell"
POLLINATIONS = "https://image.pollinations.ai/prompt/"

# Appended to every prompt. These are credibility rules, not style preferences —
# see channel/visual-identity.md.
STYLE = (
    "cinematic documentary still, deep shadow, muted desaturated palette, "
    "single warm amber light source, 35mm film grain, shallow depth of field, "
    "no text, no letters, no logos, no watermarks, no recognisable real people, "
    "no faces in focus, editorial photography"
)
NEGATIVE = (
    "text, letters, words, logos, brand marks, watermark, signature, "
    "celebrity, recognisable face, cartoon, 3d render, oversaturated, "
    "stock photo smile, distorted hands"
)


def _sanitise(prompt: str) -> str:
    """Strip anything that invites a logo, a real face or on-screen text."""
    banned = ["logo", "brand mark", "sign reading", "text saying", "headline",
              "newspaper front page", "portrait of", "photo of CEO"]
    cleaned = prompt
    for term in banned:
        cleaned = cleaned.replace(term, "scene")
    return cleaned


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=20))
def _cloudflare(prompt: str, dest: Path) -> Path | None:
    account, token = env("CF_ACCOUNT_ID"), env("CF_API_TOKEN")
    if not (account and token):
        return None
    r = requests.post(
        f"https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{MODEL}",
        headers={"Authorization": f"Bearer {token}"},
        json={"prompt": f"{prompt}. {STYLE}", "negative_prompt": NEGATIVE},
        timeout=90,
    )
    r.raise_for_status()
    payload = r.json()
    image_b64 = payload.get("result", {}).get("image")
    if not image_b64:
        return None
    dest.write_bytes(base64.b64decode(image_b64))
    return dest


def _pollinations(prompt: str, dest: Path) -> Path | None:
    """Keyless backup so a Cloudflare quota exhaustion never stalls a run."""
    try:
        import urllib.parse

        url = POLLINATIONS + urllib.parse.quote(f"{prompt}. {STYLE}")
        r = requests.get(url, params={"width": 1920, "height": 1080, "nologo": "true"},
                         timeout=120)
        r.raise_for_status()
        dest.write_bytes(r.content)
        return dest
    except Exception as exc:  # noqa: BLE001
        log.warning("Pollinations fallback failed: %s", exc)
        return None


def generate(prompt: str, dest: Path) -> Path | None:
    clean = _sanitise(prompt)
    for fn in (_cloudflare, _pollinations):
        try:
            result = fn(clean, dest)
            if result and dest.exists() and dest.stat().st_size > 10_000:
                return result
        except Exception as exc:  # noqa: BLE001
            log.warning("%s failed: %s", fn.__name__, exc)
    log.error("All image generators failed for prompt: %s", clean[:90])
    return None
