#!/usr/bin/env python3
"""Unlist every public video in one narration language, and make that
language's playlist private, on the channel of the current token.

    python scripts/unlist_language.py hi            # unlist the Hindi videos
    python scripts/unlist_language.py hi --dry-run  # only list them

Unlisted videos keep their views, comments and links; they leave the channel
page, search and recommendations. Undo in Studio (Visibility -> Public).
Videos are found in data/history.json and, as a check, by script: a title in
Devanagari counts as Hindi.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import upload  # noqa: E402
from src.config import load  # noqa: E402

SCRIPTS = {"hi": r"[ऀ-ॿ]", "ta": r"[஀-௿]"}


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry = "--dry-run" in sys.argv
    lang = args[0] if args else "hi"
    cfg = load()
    yt = upload.client()
    upload.check_channel(cfg, yt)
    eps = json.loads((ROOT / "data" / "history.json").read_text(encoding="utf-8"))["episodes"]
    ids = {e["video_id"] for e in eps if e.get("language") == lang and e.get("video_id")}

    uploads = yt.channels().list(part="contentDetails", mine=True).execute()["items"][0][
        "contentDetails"]["relatedPlaylists"]["uploads"]
    all_ids, token = [], None
    while True:
        r = yt.playlistItems().list(part="contentDetails", playlistId=uploads,
                                    maxResults=50, pageToken=token).execute()
        all_ids += [i["contentDetails"]["videoId"] for i in r.get("items", [])]
        token = r.get("nextPageToken")
        if not token:
            break
    pattern = SCRIPTS.get(lang)
    done = 0
    for i in range(0, len(all_ids), 50):
        for v in yt.videos().list(part="snippet,status", id=",".join(all_ids[i:i + 50])
                                  ).execute().get("items", []):
            title = v["snippet"]["title"]
            if v["id"] not in ids and not (pattern and re.search(pattern, title)):
                continue
            if v["status"]["privacyStatus"] != "public":
                print(f"Already {v['status']['privacyStatus']}: {v['id']} {title[:70]}")
                continue
            print(f"{'Would unlist' if dry else 'Unlisted'} {v['id']}: {title[:70]}")
            if not dry:
                upload.set_privacy(v["id"], "unlisted")
            done += 1
    print(f"{done} video(s) {'to unlist' if dry else 'unlisted'}")

    name = cfg.get_path(f"upload.playlists.{lang}")
    for p in yt.playlists().list(part="snippet,status", mine=True, maxResults=50
                                 ).execute().get("items", []):
        if name and p["snippet"]["title"] == name and p["status"]["privacyStatus"] != "private":
            print(f"{'Would make' if dry else 'Made'} playlist private: {name}")
            if not dry:
                yt.playlists().update(part="snippet,status", body={
                    "id": p["id"], "snippet": {"title": p["snippet"]["title"],
                                               "description": p["snippet"].get("description", "")},
                    "status": {"privacyStatus": "private"}}).execute()
    return 0


if __name__ == "__main__":
    sys.exit(main())
