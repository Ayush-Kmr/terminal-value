"""Human approval gates over Telegram.

Two gates, both answerable from a phone in under a minute:
  Gate 1 - approve the company and the angle before research is paid for
  Gate 2 - watch the cut before anything becomes public

The default on timeout is HOLD, never publish. An automated pipeline that
publishes when nobody answered is exactly the failure mode that gets channels
removed from the Partner Programme.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

import requests

from .config import Config, env

log = logging.getLogger(__name__)
API = "https://api.telegram.org/bot{token}/{method}"


def _call(method: str, **payload) -> dict:
    token = env("TELEGRAM_BOT_TOKEN", required=True)
    r = requests.post(API.format(token=token, method=method), json=payload, timeout=60)
    r.raise_for_status()
    return r.json()


def _send(text: str, buttons: list[list[dict]] | None = None) -> int:
    resp = _call(
        "sendMessage",
        chat_id=env("TELEGRAM_CHAT_ID", required=True),
        text=text, parse_mode="Markdown",
        reply_markup={"inline_keyboard": buttons} if buttons else None,
    )
    return resp["result"]["message_id"]


def send_video(path: Path, caption: str) -> int:
    with open(path, "rb") as fh:
        r = requests.post(
            API.format(token=env("TELEGRAM_BOT_TOKEN", required=True), method="sendVideo"),
            data={"chat_id": env("TELEGRAM_CHAT_ID", required=True),
                  "caption": caption[:1024], "parse_mode": "Markdown"},
            files={"video": fh}, timeout=600,
        )
    r.raise_for_status()
    return r.json()["result"]["message_id"]


def _await_choice(valid: set[str], timeout_hours: float, poll: int = 20) -> str | None:
    """Poll for a callback button press. Returns the chosen value, or None on timeout."""
    deadline = time.time() + timeout_hours * 3600
    offset = None
    while time.time() < deadline:
        try:
            resp = _call("getUpdates", offset=offset, timeout=poll)
            for update in resp.get("result", []):
                offset = update["update_id"] + 1
                cq = update.get("callback_query")
                if cq and cq.get("data") in valid:
                    _call("answerCallbackQuery", callback_query_id=cq["id"],
                          text="Got it.")
                    return cq["data"]
                # Allow a plain typed reply too, for when buttons are fiddly.
                msg = (update.get("message") or {}).get("text", "").strip().lower()
                if msg in valid:
                    return msg
        except Exception as exc:  # noqa: BLE001
            log.warning("Telegram poll error: %s", exc)
            time.sleep(poll)
    return None


def topic_gate(cfg: Config, candidates: list[dict]) -> dict[str, Any] | None:
    """Gate 1. Returns the chosen topic dict, or None to abort the run."""
    if not cfg.get_path("gates.topic_approval", True):
        return candidates[0]

    lines = [f"*Pick this week's episode* — {cfg.get_path('channel.name')}", ""]
    buttons = []
    for i, c in enumerate(candidates[:4]):
        lines.append(
            f"*{i+1}. {c.get('company')}* _(score {c.get('score','?')})_\n"
            f"↳ {c.get('angle','')}\n"
            f"↳ Hook: {c.get('surprising_fact','')}\n"
            f"↳ Title idea: {c.get('predicted_title','')}\n"
        )
        buttons.append([{"text": f"{i+1}. {c.get('company')}", "callback_data": f"pick{i}"}])
    buttons.append([{"text": "✗ None of these — regenerate", "callback_data": "reject"}])

    _send("\n".join(lines), buttons)
    valid = {f"pick{i}" for i in range(len(candidates[:4]))} | {"reject"}
    choice = _await_choice(valid, cfg.get_path("gates.timeout_hours", 20))

    if choice is None:
        _send("⏳ No answer — holding. Nothing was produced or published.")
        return None
    if choice == "reject":
        return None
    return candidates[int(choice.replace("pick", ""))]


def review_gate(cfg: Config, video: Path, meta: dict, warnings: list[str]) -> str:
    """Gate 2. Returns 'publish', 'schedule', or 'hold'."""
    if not cfg.get_path("gates.final_review", True):
        return "schedule"

    warn_text = ("\n\n⚠️ *Research warnings:*\n" + "\n".join(f"• {w}" for w in warnings)) if warnings else ""
    caption = (
        f"*{meta.get('title','(untitled)')}*\n"
        f"{meta.get('runtime','?')} · {meta.get('word_count','?')} words\n\n"
        f"_Angle:_ {meta.get('angle','')}"
        f"{warn_text}"
    )
    try:
        send_video(video, caption)
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not send video (%s); sending text only", exc)
        _send(caption + f"\n\n(Video too large to send — check the CI artifact.)")

    _send(
        "Ship it?",
        [
            [{"text": "✓ Publish now", "callback_data": "publish"}],
            [{"text": "🕐 Schedule for the slot", "callback_data": "schedule"}],
            [{"text": "✗ Hold — needs work", "callback_data": "hold"}],
        ],
    )
    choice = _await_choice({"publish", "schedule", "hold"},
                           cfg.get_path("gates.timeout_hours", 20))
    if choice is None:
        _send("⏳ No answer — left private. Nothing published.")
        return "hold"
    return choice


def notify(text: str) -> None:
    """Fire-and-forget status message. Never raises — a broken notifier must
    not take the pipeline down."""
    try:
        _send(text)
    except Exception as exc:  # noqa: BLE001
        log.warning("Notify failed: %s", exc)
