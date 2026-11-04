#!/usr/bin/env python3
"""Add every existing upload whose title matches upload.title_series to that
series playlist (new uploads join it on their own).

    python scripts/backfill_series.py
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
    token, n = None, 0
    while True:
        r = yt.playlistItems().list(part="snippet", playlistId=uploads,
                                    maxResults=50, pageToken=token).execute()
        for item in r.get("items", []):
            title = item["snippet"]["title"]
            name = upload.title_series(cfg, title)
            if name:
                upload.add_to_playlist(item["snippet"]["resourceId"]["videoId"], name)
                print(f"{name} <- {title[:70]}")
                n += 1
        token = r.get("nextPageToken")
        if not token:
            break
    print(f"{n} video(s) checked into series playlists")
    return 0


if __name__ == "__main__":
    sys.exit(main())
