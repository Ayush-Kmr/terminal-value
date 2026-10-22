#!/usr/bin/env python3
"""The Telegram bot: both channels' numbers and the pipeline's state, on
demand, from slash commands.

    python scripts/telegram_bot.py --poll 280   answer commands for 280 s
    python scripts/telegram_bot.py --digest     send the daily summary
    python scripts/telegram_bot.py --once /status   print one reply (testing)

There is no server: .github/workflows/telegram.yml runs --poll every few
minutes, each run long-polling Telegram until the next one starts, so replies
come within seconds while a run is live. Only TELEGRAM_CHAT_ID is answered.

Read-only: every command reports; none of them changes anything.
"""
from __future__ import annotations

import argparse
import html
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import upload  # noqa: E402

API = "https://api.telegram.org/bot{token}/{method}"
IST = ZoneInfo("Asia/Kolkata")


def _channels() -> list[dict]:
    """Both channels, from config.yaml and channels/*.yaml, with the secret
    that signs in as each."""
    main = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))["channel"]
    out = [{"key": "english", "name": main["name"], "id": main["channel_id"],
            "token": os.getenv("YT_REFRESH_TOKEN")}]
    for f in sorted((ROOT / "channels").glob("*.yaml")):
        ch = (yaml.safe_load(f.read_text(encoding="utf-8")) or {}).get("channel", {})
        key = {"ta": "tamil"}.get(f.stem, f.stem)
        out.append({"key": key, "name": ch.get("name", f.stem), "id": ch.get("channel_id"),
                    "token": os.getenv(f"YT_REFRESH_TOKEN_{f.stem.upper()}")})
    return [c for c in out if c["id"]]


COMMANDS = [
    ("status", "Both channels at a glance + last run"),
    ("english", "English channel: stats and latest videos"),
    ("tamil", "Tamil channel: stats and latest videos"),
    ("today", "Everything uploaded in the last 24 hours"),
    ("schedule", "Videos waiting to go public, with times"),
    ("week", "Last 7 days: views, watch time, subscribers"),
    ("top", "Best videos so far on each channel"),
    ("runs", "Latest pipeline runs on GitHub"),
    ("next", "When the next videos are made and published"),
    ("help", "All commands"),
]


# ------------------------------------------------------------------ telegram

def tg(method: str, **payload) -> dict:
    r = requests.post(API.format(token=os.environ["TELEGRAM_BOT_TOKEN"], method=method),
                      json=payload, timeout=70)
    r.raise_for_status()
    return r.json()


def send(text: str) -> None:
    # Telegram's limit is 4096 characters; split at line breaks.
    while text:
        cut = len(text) if len(text) <= 4000 else text.rfind("\n", 0, 4000)
        cut = cut if cut > 0 else 4000
        tg("sendMessage", chat_id=os.environ["TELEGRAM_CHAT_ID"], text=text[:cut],
           parse_mode="HTML", disable_web_page_preview=True)
        text = text[cut:].lstrip("\n")


# ------------------------------------------------------------------ youtube

_cache: dict = {}


def yt(ch: dict):
    if ch["key"] not in _cache:
        _cache[ch["key"]] = upload.client(ch["token"])
    return _cache[ch["key"]]


def n(x) -> str:
    x = int(x or 0)
    return f"{x / 1e6:.1f}M" if x >= 1e6 else f"{x / 1e3:.1f}K" if x >= 1e4 else f"{x:,}"


def esc(s) -> str:
    return html.escape(str(s or ""))


def channel_info(ch: dict) -> dict:
    item = yt(ch).channels().list(part="snippet,statistics,contentDetails,status",
                                  id=ch["id"]).execute()["items"][0]
    return item


def videos(ch: dict, limit: int = 50) -> list[dict]:
    """Latest uploads (private and scheduled ones too, since we are the owner)."""
    info = channel_info(ch)
    pl = info["contentDetails"]["relatedPlaylists"]["uploads"]
    from googleapiclient.errors import HttpError
    try:
        ids = [i["contentDetails"]["videoId"] for i in yt(ch).playlistItems().list(
            part="contentDetails", playlistId=pl,
            maxResults=min(limit, 50)).execute().get("items", [])]
    except HttpError as exc:
        if exc.resp.status == 404:      # a new channel has no uploads list yet
            return []
        raise
    if not ids:
        return []
    items = yt(ch).videos().list(part="snippet,statistics,status,contentDetails",
                                 id=",".join(ids)).execute().get("items", [])
    items.sort(key=lambda v: v["snippet"]["publishedAt"], reverse=True)
    return items


def is_short(v: dict) -> bool:
    """Shorts run up to 3 minutes; the long videos are ~9."""
    import re
    m = re.fullmatch(r"P(?:\d+D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?",
                     v["contentDetails"].get("duration", ""))
    if not m:
        return False
    h, mi, se = (int(x or 0) for x in m.groups())
    return h * 3600 + mi * 60 + se <= 180


def when(iso: str) -> str:
    t = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(IST)
    return t.strftime("%d %b %I:%M %p")


def video_line(v: dict) -> str:
    s, st = v.get("statistics", {}), v["status"]
    kind = "🎬" if not is_short(v) else "📱"
    title = esc(v["snippet"]["title"][:70])
    url = f"https://youtu.be/{v['id']}"
    if st.get("publishAt") and st["privacyStatus"] != "public":
        state = f"⏰ goes public {when(st['publishAt'])}"
    elif st["privacyStatus"] != "public":
        state = f"🔒 {st['privacyStatus']}"
    else:
        state = (f"👁 {n(s.get('viewCount'))} · 👍 {n(s.get('likeCount'))} · "
                 f"💬 {n(s.get('commentCount'))}")
    return f"{kind} <a href=\"{url}\">{title}</a>\n     {state} · {when(v['snippet']['publishedAt'])}"


def flag(ch: dict) -> str:
    return "🇮🇳 Tamil" if ch["key"] == "tamil" else "🇬🇧 English"


def summary(ch: dict) -> str:
    info = channel_info(ch)
    s = info["statistics"]
    subs = "hidden" if s.get("hiddenSubscriberCount") else n(s.get("subscriberCount"))
    handle = info["snippet"].get("customUrl", "")
    return (f"<b>{esc(info['snippet']['title'])}</b> ({flag(ch)}) {esc(handle)}\n"
            f"👥 {subs} subscribers · 👁 {n(s.get('viewCount'))} views · "
            f"🎞 {n(s.get('videoCount'))} videos")


def analytics(ch: dict, days: int = 7) -> str:
    from googleapiclient.discovery import build
    if not ch["token"]:
        # Without the channel's own token the call would read the other channel.
        return "(analytics unavailable: no sign-in token for this channel)"
    creds = upload.credentials(ch["token"])
    ya = build("youtubeAnalytics", "v2", credentials=creds, cache_discovery=False)
    end = date.today()
    start = end - timedelta(days=days)
    try:
        r = ya.reports().query(
            ids="channel==MINE", startDate=start.isoformat(), endDate=end.isoformat(),
            metrics="views,estimatedMinutesWatched,averageViewDuration,"
                    "subscribersGained,subscribersLost,likes,comments,shares").execute()
    except Exception as exc:  # noqa: BLE001
        return f"(analytics unavailable: {esc(str(exc)[:120])})"
    row = (r.get("rows") or [[0] * 8])[0]
    views, mins, avg, gained, lost, likes, comments, shares = row
    return (f"👁 {n(views)} views · ⏱ {mins / 60:.1f} hours watched · "
            f"avg {int(avg) // 60}:{int(avg) % 60:02d} per view\n"
            f"👥 +{n(gained)} / -{n(lost)} subscribers · 👍 {n(likes)} · 💬 {n(comments)} · "
            f"🔁 {n(shares)}\n<i>YouTube Analytics lags 1-3 days.</i>")


# ------------------------------------------------------------------ github

def gh(path: str) -> dict:
    repo = os.getenv("GITHUB_REPOSITORY", "Ayush-Kmr/terminal-value")
    headers = {"Accept": "application/vnd.github+json"}
    if os.getenv("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
    r = requests.get(f"https://api.github.com/repos/{repo}/{path}", headers=headers, timeout=30)
    r.raise_for_status()
    return r.json()


ICON = {"success": "✅", "failure": "❌", "cancelled": "⛔", "skipped": "⏭", None: "⏳"}


def run_line(run: dict, jobs: bool = True) -> str:
    started = when(run["run_started_at"] or run["created_at"])
    line = (f"{ICON.get(run['conclusion'], '•')} <a href=\"{run['html_url']}\">"
            f"{esc(run['name'])} #{run['run_number']}</a> · {started} · "
            f"{'scheduled' if run['event'] == 'schedule' else 'manual'}")
    if jobs and run["name"] == "Episode":
        js = gh(f"actions/runs/{run['id']}/jobs").get("jobs", [])
        names = {"produce": "English", "tamil": "Tamil"}
        line += "\n     " + " · ".join(f"{names.get(j['name'], j['name'])} "
                                       f"{ICON.get(j['conclusion'], '⏳')}" for j in js)
    return line


def last_episode_run() -> str:
    runs = gh("actions/workflows/episode.yml/runs?per_page=1").get("workflow_runs", [])
    return run_line(runs[0]) if runs else "no runs yet"


# ------------------------------------------------------------------ commands

def cmd_help(_: list[dict]) -> str:
    return ("<b>Terminal Value bot</b>\n\n"
            + "\n".join(f"/{c} — {d}" for c, d in COMMANDS))


def cmd_status(chs: list[dict]) -> str:
    parts = [summary(c) for c in chs]
    parts.append("<b>Last pipeline run</b>\n" + last_episode_run())
    return "📊 <b>STATUS</b>\n\n" + "\n\n".join(parts)


def cmd_channel(ch: dict) -> str:
    vids = videos(ch, 10)
    return (summary(ch) + f"\n🔗 https://www.youtube.com/channel/{ch['id']}\n\n"
            "<b>Latest videos</b>\n" + ("\n".join(video_line(v) for v in vids[:10])
                                        or "none yet"))


def cmd_today(chs: list[dict]) -> str:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    out = ["🗓 <b>LAST 24 HOURS</b>"]
    for ch in chs:
        vids = [v for v in videos(ch, 15)
                if datetime.fromisoformat(v["snippet"]["publishedAt"].replace("Z", "+00:00")) > cutoff]
        out.append(f"\n<b>{esc(ch['name'])}</b> ({len(vids)} uploads)\n"
                   + ("\n".join(video_line(v) for v in vids) or "nothing uploaded"))
    out.append("\n<b>Last pipeline run</b>\n" + last_episode_run())
    return "\n".join(out)


def cmd_schedule(chs: list[dict]) -> str:
    out = ["⏰ <b>SCHEDULED</b>"]
    for ch in chs:
        vids = [v for v in videos(ch, 15)
                if v["status"].get("publishAt") and v["status"]["privacyStatus"] != "public"]
        vids.sort(key=lambda v: v["status"]["publishAt"])
        out.append(f"\n<b>{esc(ch['name'])}</b>\n"
                   + ("\n".join(video_line(v) for v in vids) or "nothing waiting"))
    return "\n".join(out)


def cmd_week(chs: list[dict]) -> str:
    return "📈 <b>LAST 7 DAYS</b>\n\n" + "\n\n".join(
        f"<b>{esc(c['name'])}</b>\n{analytics(c)}" for c in chs)


def cmd_top(chs: list[dict]) -> str:
    out = ["🏆 <b>TOP VIDEOS</b> (latest 50 uploads, by views)"]
    for ch in chs:
        vids = [v for v in videos(ch, 50) if v["status"]["privacyStatus"] == "public"]
        vids.sort(key=lambda v: int(v["statistics"].get("viewCount", 0)), reverse=True)
        out.append(f"\n<b>{esc(ch['name'])}</b>\n"
                   + ("\n".join(video_line(v) for v in vids[:5]) or "no public videos yet"))
    return "\n".join(out)


def cmd_runs(_: list[dict]) -> str:
    runs = gh("actions/runs?per_page=8").get("workflow_runs", [])
    runs = [r for r in runs if r["name"] != "Telegram bot"][:6]
    return "⚙️ <b>PIPELINE RUNS</b>\n\n" + "\n".join(
        run_line(r, jobs=i < 3) for i, r in enumerate(runs))


def cmd_next(_: list[dict]) -> str:
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    now = datetime.now(IST)
    run = now.replace(hour=9, minute=0, second=0, microsecond=0)
    run = run if run > now else run + timedelta(days=1)
    long_times = ", ".join(cfg["format"]["long"]["publish_times"])
    short_times = ", ".join(cfg["format"]["short"]["publish_times"])
    return ("⏭ <b>NEXT</b>\n\n"
            f"🛠 Next daily run starts: {run:%a %d %b, %I:%M %p} IST "
            "(GitHub sometimes starts it late)\n"
            f"🎬 Long video: every {cfg['format']['long']['every_days']} days, "
            f"public at {long_times} IST\n"
            f"📱 Shorts: {cfg['format']['short']['per_day']} a day, public at {short_times} IST\n"
            "🇮🇳 The Tamil channel gets the same videos, in Tamil, at the same times.")


def reply(text: str, chs: list[dict]) -> str:
    word = text.strip().split()[0].split("@")[0].lstrip("/").lower() if text.strip() else ""
    by_key = {c["key"]: c for c in chs}
    handlers = {"start": cmd_help, "help": cmd_help, "status": cmd_status,
                "today": cmd_today, "schedule": cmd_schedule, "week": cmd_week,
                "top": cmd_top, "runs": cmd_runs, "next": cmd_next}
    try:
        if word in by_key:
            return cmd_channel(by_key[word])
        if word in handlers:
            return handlers[word](chs)
        return "Unknown command. " + cmd_help(chs)
    except Exception as exc:  # noqa: BLE001
        return f"⚠️ /{esc(word)} failed: {esc(type(exc).__name__)}: {esc(str(exc)[:300])}"


# ------------------------------------------------------------------ main

def poll(seconds: int) -> None:
    chs = _channels()
    chat = str(os.environ["TELEGRAM_CHAT_ID"])
    tg("setMyCommands", commands=[{"command": c, "description": d} for c, d in COMMANDS])
    deadline = time.time() + seconds
    offset = None
    while time.time() < deadline - 5:
        wait = int(min(50, max(1, deadline - time.time() - 5)))
        updates = tg("getUpdates", offset=offset, timeout=wait,
                     allowed_updates=["message", "channel_post"]).get("result", [])
        for u in updates:
            offset = u["update_id"] + 1
            msg = u.get("message") or u.get("channel_post") or {}
            text = msg.get("text") or ""
            if str(msg.get("chat", {}).get("id")) != chat or not text.startswith("/"):
                continue
            print(f"command: {text.split()[0]}", flush=True)
            send(reply(text, chs))
    if offset:
        tg("getUpdates", offset=offset, timeout=0)     # mark everything as handled


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--poll", type=int, metavar="SECONDS")
    ap.add_argument("--digest", action="store_true")
    ap.add_argument("--once", metavar="COMMAND")
    args = ap.parse_args()
    if args.once:
        print(reply(args.once, _channels()))
    elif args.digest:
        chs = _channels()
        send("🌙 <b>Daily summary</b>\n\n" + cmd_status(chs))
        send(cmd_today(chs))
        send(cmd_schedule(chs))
    elif args.poll:
        poll(args.poll)
    return 0


if __name__ == "__main__":
    sys.exit(main())
