#!/usr/bin/env python3
"""Remove off-topic tags from videos already on the channel of the current
token (review of 2026-11-04: "bernie madoff movie scene", "kalshi dance",
"gas cylinder price tamil" on a US petrol story).

    python scripts/clean_tags.py            # apply
    python scripts/clean_tags.py --dry-run  # only list what would change

A tag stays when every word it adds is in the video's own title or
description, or is a generic search word (src/keywords.py). The channel's
standard tags and keywords always stay, and a video keeps at least 3 tags.
Each change costs 50 units of the 10,000-a-day YouTube quota.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import upload  # noqa: E402
from src.config import load  # noqa: E402
from src.keywords import _relevant  # noqa: E402

STANDARD = {"news explained", "tamil news", "business news tamil", "தமிழ் செய்திகள்",
            "hindi news"}
LIMIT = 60          # newest uploads checked


def main() -> int:
    dry = "--dry-run" in sys.argv
    cfg = load()
    yt = upload.client()
    upload.check_channel(cfg, yt)
    keep_always = STANDARD | {str(k).lower() for k in cfg.get_path("channel.keywords", []) or []}
    uploads = yt.channels().list(part="contentDetails", mine=True).execute()["items"][0][
        "contentDetails"]["relatedPlaylists"]["uploads"]
    ids, token = [], None
    while len(ids) < LIMIT:
        r = yt.playlistItems().list(part="contentDetails", playlistId=uploads,
                                    maxResults=50, pageToken=token).execute()
        ids += [i["contentDetails"]["videoId"] for i in r.get("items", [])]
        token = r.get("nextPageToken")
        if not token:
            break
    changed = 0
    for i in range(0, len(ids[:LIMIT]), 50):
        for v in yt.videos().list(part="snippet", id=",".join(ids[i:i + 50])).execute()["items"]:
            sn = v["snippet"]
            tags = sn.get("tags") or []
            context = f"{sn['title']} {sn.get('description', '')}"
            keep = [t for t in tags if t.lower() in keep_always or _relevant(t, "", context)]
            if len(keep) < 3:
                keep = keep + [t for t in tags if t not in keep][:3 - len(keep)]
            drop = [t for t in tags if t not in keep]
            if not drop:
                continue
            changed += 1
            print(f"{'Would clean' if dry else 'Cleaned'} {v['id']} {sn['title'][:60]}")
            print(f"    dropped: {', '.join(drop)}")
            if dry:
                continue
            body = {k: sn[k] for k in ("title", "description", "categoryId",
                                       "defaultLanguage", "defaultAudioLanguage") if k in sn}
            body["tags"] = keep
            yt.videos().update(part="snippet", body={"id": v["id"], "snippet": body}).execute()
    print(f"{changed} video(s) {'would change' if dry else 'changed'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
