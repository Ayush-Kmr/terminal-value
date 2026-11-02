#!/usr/bin/env python3
"""Publish every video waiting for its slot, on the channel of the current token.

    python scripts/publish_now.py
    CHANNEL=ta python scripts/publish_now.py

Only touches scheduled videos: a private video with no publish time was held on
purpose and stays private.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import upload  # noqa: E402
from src.config import load  # noqa: E402


def main() -> int:
    cfg = load()
    yt = upload.client()
    upload.check_channel(cfg, yt)
    uploads = yt.channels().list(part="contentDetails", mine=True).execute()["items"][0][
        "contentDetails"]["relatedPlaylists"]["uploads"]
    ids = [i["contentDetails"]["videoId"] for i in yt.playlistItems().list(
        part="contentDetails", playlistId=uploads, maxResults=20).execute().get("items", [])]
    done = 0
    for v in yt.videos().list(part="snippet,status", id=",".join(ids)).execute().get("items", []):
        if v["status"].get("publishAt") and v["status"]["privacyStatus"] != "public":
            upload.set_privacy(v["id"], "public")
            print(f"Published {v['id']}: {v['snippet']['title'][:70]}")
            done += 1
    print(f"{done} video(s) published")
    language = cfg.get_path("channel.language", "en")
    for v in yt.videos().list(part="snippet", id=",".join(ids[:6])).execute().get("items", []):
        s = v["snippet"]
        upload.add_to_playlists(cfg, v["id"], language, s["title"], " ".join(s.get("tags", [])))
    print("Playlists checked for the 6 newest videos")
    return 0


if __name__ == "__main__":
    sys.exit(main())
