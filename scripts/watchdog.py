#!/usr/bin/env python3
"""Did both channels get videos today? If not, restart what is missing.

Judged by data/history*.json, which records real uploads, so test builds and
half-finished runs do not count. Standard library only: it must work when
everything else is broken. Run by .github/workflows/watchdog.yml.
"""
from __future__ import annotations

import json
import os
import subprocess
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WINDOW = timedelta(hours=20)    # daily uploads; Sunday's long must not count for Monday


def fresh(path: str, now: datetime) -> bool:
    """Has this channel recorded an upload in the last day?"""
    file = ROOT / path
    if not file.exists():
        return False
    for ep in json.loads(file.read_text(encoding="utf-8")).get("episodes", []):
        when = datetime.fromisoformat(ep.get("created_at", "2000-01-01T00:00:00+00:00"))
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        if now - when < WINDOW:
            return True
    return False


def gh(*args: str) -> str:
    return subprocess.run(["gh", *args], capture_output=True, text=True, check=True).stdout


def latest_run(workflow: str) -> dict:
    rows = json.loads(gh("run", "list", "--workflow", workflow, "--limit", "1", "--json",
                         "databaseId,status,conclusion,createdAt"))
    return rows[0] if rows else {}


def tell(text: str) -> None:
    print(text)
    token, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if token and chat:
        data = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
        urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data, timeout=30)


# Each channel has its own workflow (split 2026-11-14), so each is checked and
# restarted on its own.
CHANNELS = (("English", "data/history.json", "episode.yml"),
            ("Bhojpuri", "data/history.bh.json", "bhojpuri.yml"))


def main() -> int:
    now = datetime.now(timezone.utc)
    for name, history, workflow in CHANNELS:
        if fresh(history, now):
            print(f"{name}: uploaded today")
            continue
        run = latest_run(workflow)
        if run.get("status") and run["status"] != "completed":
            print(f"{name}: run {run.get('databaseId')} is still {run['status']}; leaving it")
            continue
        started = datetime.fromisoformat(run["createdAt"].replace("Z", "+00:00")) \
            if run.get("createdAt") else None
        if run.get("conclusion") == "failure" and started and now - started < WINDOW:
            gh("run", "rerun", str(run["databaseId"]), "--failed")
            tell(f"🛠 Watchdog: the {name} run failed, so I restarted it.")
        else:
            gh("workflow", "run", workflow, "--ref", "main")
            tell(f"🛠 Watchdog: no {name} upload in the last day, so I started its run.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
