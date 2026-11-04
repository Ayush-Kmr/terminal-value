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
WINDOW = timedelta(hours=26)


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


def latest_run() -> dict:
    rows = json.loads(gh("run", "list", "--workflow", "episode.yml", "--limit", "1", "--json",
                         "databaseId,status,conclusion,createdAt"))
    return rows[0] if rows else {}


def tell(text: str) -> None:
    print(text)
    token, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if token and chat:
        data = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
        urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data, timeout=30)


def main() -> int:
    now = datetime.now(timezone.utc)
    english, tamil = fresh("data/history.json", now), fresh("data/history.ta.json", now)
    run = latest_run()
    if english and tamil:
        print("Both channels uploaded today")
        return 0
    if run.get("status") and run["status"] != "completed":
        print(f"Run {run.get('databaseId')} is still {run['status']}; leaving it to finish")
        return 0
    if not english:
        gh("workflow", "run", "episode.yml", "--ref", "main")
        tell("🛠 Watchdog: no English upload in the last day, so I started the daily run.")
    else:
        started = datetime.fromisoformat(run["createdAt"].replace("Z", "+00:00"))
        if run.get("conclusion") == "failure" and now - started < WINDOW:
            gh("run", "rerun", str(run["databaseId"]), "--failed")
            tell("🛠 Watchdog: the Tamil upload failed, so I restarted it.")
        else:
            # The Tamil job picks its own stories, so it can run on its own.
            gh("workflow", "run", "episode.yml", "--ref", "main", "-f", "tamil_only=true")
            tell("🛠 Watchdog: no Tamil upload in the last day, so I started a Tamil-only run.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
