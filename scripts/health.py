#!/usr/bin/env python3
"""The evening report in Telegram: what each channel uploaded today, any
warnings the pipeline raised, and every GitHub job that failed in the last day.

Sent every evening, fine or not, so silence never has to be read as good news
(Ayush, 2026-11-04: "keep monitoring everything, also updating on Telegram").
Standard library and the gh CLI only, like the watchdog, so it still works when
the pipeline itself is broken. Run by .github/workflows/watchdog.yml.
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
DAY = timedelta(hours=24)
IST = timezone(timedelta(hours=5, minutes=30))
CHANNELS = (("English", "data/history.json", 2), ("Bhojpuri", "data/history.bh.json", 2))
# Workflows that run on a schedule; their failures matter. One-off buttons
# (Clean tags, Brand channel...) are reported too when they fail.
QUIET = {"Telegram bot"}      # restarts itself every hour; one failure is noise


def gh(*args: str) -> str:
    return subprocess.run(["gh", *args], capture_output=True, text=True, check=True).stdout


def uploads(path: str, now: datetime) -> list[dict]:
    file = ROOT / path
    if not file.exists():
        return []
    out = []
    for ep in json.loads(file.read_text(encoding="utf-8")).get("episodes", []):
        when = datetime.fromisoformat(ep.get("created_at", "2000-01-01T00:00:00+00:00"))
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        if now - when < DAY:
            out.append(ep)
    return out


def failed_runs(now: datetime) -> list[str]:
    rows = json.loads(gh("run", "list", "--limit", "60", "--json",
                         "workflowName,conclusion,createdAt,url,displayTitle,event"))
    out = []
    for r in rows:
        when = datetime.fromisoformat(r["createdAt"].replace("Z", "+00:00"))
        if now - when > DAY or r["workflowName"] in QUIET:
            continue
        if r.get("conclusion") == "failure":
            out.append(f"❌ {r['workflowName']} ({when.astimezone(IST):%H:%M} IST): {r['url']}")
    return out


def todays_run(now: datetime) -> str:
    rows = json.loads(gh("run", "list", "--workflow", "episode.yml", "--event", "schedule",
                         "--limit", "1", "--json", "databaseId,status,conclusion,createdAt"))
    if not rows:
        return "⚠️ No scheduled daily run found"
    r = rows[0]
    when = datetime.fromisoformat(r["createdAt"].replace("Z", "+00:00"))
    if now - when > DAY:
        return "⚠️ The daily run did not start today"
    jobs = json.loads(gh("run", "view", str(r["databaseId"]), "--json", "jobs"))["jobs"]
    names = {"produce": "English", "bhojpuri": "Bhojpuri"}
    icon = {"success": "✅", "failure": "❌", "skipped": "⏭", "cancelled": "⛔"}
    parts = [f"{names.get(j['name'], j['name'])} {icon.get(j.get('conclusion'), '⏳')}"
             for j in jobs]
    return f"Daily run ({when.astimezone(IST):%H:%M} IST): " + " · ".join(parts)


def report(now: datetime) -> str:
    lines = [f"📋 Evening report, {now.astimezone(IST):%a %d %b}", ""]
    try:
        lines.append(todays_run(now))
    except Exception as exc:  # noqa: BLE001
        lines.append(f"⚠️ Could not read today's run: {exc}")
    lines.append("")
    for name, path, shorts_expected in CHANNELS:
        eps = uploads(path, now)
        shorts = [e for e in eps if e.get("kind") == "short"]
        longs = [e for e in eps if e.get("kind") == "long"]
        mark = "✅" if len(shorts) >= shorts_expected else "⚠️"
        lines.append(f"{mark} {name}: {len(shorts)} Short(s), {len(longs)} long "
                     f"(expected {shorts_expected} Short(s) a day)")
        for e in eps:
            kind = "Long" if e.get("kind") == "long" else "Short"
            runtime = f" · {e['runtime']}" if e.get("runtime") else ""
            lines.append(f"  • {kind}{runtime}: {e.get('title', '')[:70]}")
            lines.append(f"    https://youtu.be/{e.get('video_id', '')}")
            for w in (e.get("warnings") or [])[:3]:
                lines.append(f"    ⚠️ {str(w)[:110]}")
        lines.append("")
    try:
        failed = failed_runs(now)
    except Exception as exc:  # noqa: BLE001
        failed = [f"⚠️ Could not list runs: {exc}"]
    lines += (["Failed in the last 24 h:", *failed] if failed
              else ["✅ No GitHub job failed in the last 24 h"])
    lines += ["", "Videos go public at their slots: /schedule shows the times."]
    return "\n".join(lines)


def tell(text: str) -> None:
    print(text)
    token, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if token and chat:
        data = urllib.parse.urlencode({"chat_id": chat, "text": text,
                                       "disable_web_page_preview": "true"}).encode()
        urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data,
                               timeout=30)


if __name__ == "__main__":
    tell(report(datetime.now(timezone.utc)))
