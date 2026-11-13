#!/usr/bin/env python3
"""Point the most-watched Shorts at a long video (analysis 2026-11-13: 0.09%
of views became subscribers and 2% of views came from suggested videos; new
Shorts already carry the link, the older winners did not).

    python scripts/link_long.py              # apply
    python scripts/link_long.py --dry-run    # only list what would change
    python scripts/link_long.py --top 15

For each of the channel's top Shorts by views, the public long video whose
title shares the most words with it is chosen (when none share a word, the
long video viewers watch furthest through, from history.json, with at least
10 views), and one line is put at the top of the description.
Shorts that already link a long video are left alone. Each change costs 50
units of the 10,000-a-day YouTube quota.

YouTube's "Related video" (the tappable link in the Shorts player) cannot be
set through the API; the script prints the pairs to set by hand in Studio.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import state, upload  # noqa: E402
from src.config import load  # noqa: E402

SHORT_MAX = 180     # seconds
STOP = {"the", "a", "an", "of", "to", "in", "on", "for", "and", "is", "why", "how", "what",
        "does", "did", "its", "as", "at", "by", "with", "shorts", "news", "latest", "new",
        "price", "prices", "stock", "stocks", "market", "markets", "today", "will", "about",
        "drop", "drops", "falls", "falling", "crash", "hits", "surges", "slide", "jump"}


def seconds(iso: str) -> int:
    h, m, s = (int(x or 0) for x in re.fullmatch(
        r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso).groups())
    return h * 3600 + m * 60 + s


def words(title: str) -> set[str]:
    title = title.lower().replace("artificial intelligence", "ai")
    return {w for w in re.findall(r"[a-z0-9$]+|[ऀ-ॿ]+", title)
            if w not in STOP and len(w) > 1 and not w.strip("$").isdigit()}


def main() -> int:
    dry = "--dry-run" in sys.argv
    top = int(sys.argv[sys.argv.index("--top") + 1]) if "--top" in sys.argv else 10
    cfg = load()
    yt = upload.client()
    upload.check_channel(cfg, yt)
    label = cfg.get_path("labels.deep_dive", "This week's deep dive")
    uploads = yt.channels().list(part="contentDetails", mine=True).execute()["items"][0][
        "contentDetails"]["relatedPlaylists"]["uploads"]
    ids, token = [], None
    while True:
        r = yt.playlistItems().list(part="contentDetails", playlistId=uploads,
                                    maxResults=50, pageToken=token).execute()
        ids += [i["contentDetails"]["videoId"] for i in r.get("items", [])]
        token = r.get("nextPageToken")
        if not token:
            break
    videos = []
    for i in range(0, len(ids), 50):
        videos += yt.videos().list(part="snippet,statistics,status,contentDetails",
                                   id=",".join(ids[i:i + 50])).execute()["items"]
    public = [v for v in videos if v["status"]["privacyStatus"] == "public"]
    views = lambda v: int(v["statistics"].get("viewCount", 0))  # noqa: E731
    longs = sorted((v for v in public if seconds(v["contentDetails"]["duration"]) > SHORT_MAX),
                   key=views, reverse=True)
    shorts = sorted((v for v in public if seconds(v["contentDetails"]["duration"]) <= SHORT_MAX),
                    key=views, reverse=True)[:top]
    if not longs:
        print("No public long video on this channel yet; nothing to link.")
        return 0
    long_ids = {v["id"] for v in longs}
    held = {e["video_id"]: (e.get("stats") or {}).get("avg_pct") or 0
            for e in state.load().get("episodes", [])
            if e.get("kind") == "long" and (e.get("stats") or {}).get("views", 0) >= 10}
    fallback = max(longs, key=lambda v: (held.get(v["id"], 0), views(v)))
    print(f"Fallback: {fallback['snippet']['title'][:60]} "
          f"({held.get(fallback['id'], 0):.0f}% watched)\n")
    changed = 0
    for s in shorts:
        sn = s["snippet"]
        desc = sn.get("description", "")
        if any(lid in desc for lid in long_ids):
            print(f"= {sn['title'][:60]} (already links a long video)")
            continue
        mine = words(sn["title"])
        best = max(longs, key=lambda v: (len(mine & words(v["snippet"]["title"])), views(v)))
        if not mine & words(best["snippet"]["title"]):
            best = fallback
        url = f"https://youtu.be/{best['id']}"
        print(f"{'would link' if dry else 'linked'}: {sn['title'][:55]} ({views(s)} views)"
              f"\n    -> {best['snippet']['title'][:60]} {url}")
        if dry:
            continue
        body = {"id": s["id"], "snippet": {
            "title": sn["title"], "categoryId": sn["categoryId"],
            "description": f"{label}: {url}\n\n{desc}"[:5000],
            **{k: sn[k] for k in ("tags", "defaultLanguage", "defaultAudioLanguage") if k in sn}}}
        yt.videos().update(part="snippet", body=body).execute()
        changed += 1
    print(f"\n{changed} Short(s) updated." if not dry else "\nDry run: nothing changed.")
    print("Set the same pairs as each Short's Related video in Studio "
          "(Content > Short > Related video); the API cannot.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
