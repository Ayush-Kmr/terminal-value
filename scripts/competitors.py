#!/usr/bin/env python3
"""Who wins in our Shorts niches, from public YouTube data (/youtube competitor).

    python scripts/competitors.py [out.json]

For each niche: search the biggest Shorts of the last 120 days, rank channels
by the views they won there, then for the top 3 channels read their last 50
uploads (views, length, dates) and the top comments on their 3 most-watched
recent videos. Only public data. About 650 units of the 10,000-a-day quota
(6 searches at 100 each), so run it once, not on a schedule.
"""
from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import upload  # noqa: E402

OURS = {"UC5QnQWBbS5AVKvgxsZl4V3w"}          # Bhojpuri AI Comedy; Terminal Value added below
NICHES = {
    "english_finance_shorts": dict(
        queries=["how does it make money", "stock market crash explained", "why did the stock fall"],
        region="US", language="en"),
    "bhojpuri_comedy_shorts": dict(
        queries=["bhojpuri comedy", "bhojpuri comedy video shorts", "bhojpuri funny cartoon"],
        region="IN", language="hi"),
}


def seconds(iso: str) -> int:
    m = re.fullmatch(r"P(?:\d+D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    return sum(int(x or 0) * k for x, k in zip(m.groups(), (3600, 60, 1))) if m else 0


def videos(yt, ids: list[str]) -> list[dict]:
    out = []
    for i in range(0, len(ids), 50):
        for v in yt.videos().list(part="snippet,statistics,contentDetails",
                                  id=",".join(ids[i:i + 50])).execute().get("items", []):
            out.append({"id": v["id"], "title": v["snippet"]["title"],
                        "channel_id": v["snippet"]["channelId"],
                        "channel": v["snippet"]["channelTitle"],
                        "published": v["snippet"]["publishedAt"][:10],
                        "seconds": seconds(v["contentDetails"].get("duration", "")),
                        "views": int(v["statistics"].get("viewCount", 0)),
                        "likes": int(v["statistics"].get("likeCount", 0)),
                        "comments": int(v["statistics"].get("commentCount", 0))})
    return out


def comments(yt, video_id: str, n: int = 30) -> list[dict]:
    try:
        r = yt.commentThreads().list(part="snippet", videoId=video_id, maxResults=n,
                                     order="relevance", textFormat="plainText").execute()
    except Exception as exc:  # noqa: BLE001 - comments off
        return [{"error": str(exc)[:80]}]
    return [{"text": c["snippet"]["topLevelComment"]["snippet"]["textDisplay"][:300],
             "likes": c["snippet"]["topLevelComment"]["snippet"].get("likeCount", 0)}
            for c in r.get("items", [])]


def main() -> int:
    out_path = Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / "output" / "competitors.json")
    yt = upload.client()
    OURS.update(i["id"] for i in yt.channels().list(part="id", mine=True).execute()["items"])
    after = (datetime.now(timezone.utc) - timedelta(days=120)).strftime("%Y-%m-%dT%H:%M:%SZ")
    report = {"generated": datetime.now(timezone.utc).isoformat(), "niches": {}}
    for niche, spec in NICHES.items():
        hits: dict[str, dict] = {}
        for q in spec["queries"]:
            r = yt.search().list(part="id", q=q, type="video", videoDuration="short",
                                 order="viewCount", publishedAfter=after, maxResults=50,
                                 regionCode=spec["region"],
                                 relevanceLanguage=spec["language"]).execute()
            for v in videos(yt, [i["id"]["videoId"] for i in r.get("items", [])]):
                if v["channel_id"] not in OURS:
                    hits[v["id"]] = {**v, "query": q}
        by_channel: dict[str, list] = defaultdict(list)
        for v in hits.values():
            by_channel[v["channel_id"]].append(v)
        ranked = sorted(by_channel.items(), key=lambda kv: sum(v["views"] for v in kv[1]),
                        reverse=True)
        channels = []
        for cid, found in ranked[:3]:
            ch = yt.channels().list(part="snippet,statistics,contentDetails",
                                    id=cid).execute()["items"][0]
            pl = ch["contentDetails"]["relatedPlaylists"]["uploads"]
            ids = [i["contentDetails"]["videoId"] for i in yt.playlistItems().list(
                part="contentDetails", playlistId=pl, maxResults=50).execute().get("items", [])]
            recent = sorted(videos(yt, ids), key=lambda v: v["published"], reverse=True)
            dates = [datetime.fromisoformat(v["published"]) for v in recent]
            span = max((dates[0] - dates[-1]).days, 1) if len(dates) > 1 else 1
            top = sorted(recent, key=lambda v: v["views"], reverse=True)
            channels.append({
                "id": cid, "name": ch["snippet"]["title"],
                "subscribers": int(ch["statistics"].get("subscriberCount", 0)),
                "total_views": int(ch["statistics"].get("viewCount", 0)),
                "video_count": int(ch["statistics"].get("videoCount", 0)),
                "uploads_per_week_recent": round(len(recent) / span * 7, 1),
                "median_seconds_recent": sorted(v["seconds"] for v in recent)[len(recent) // 2]
                if recent else 0,
                "search_hits": sorted(found, key=lambda v: v["views"], reverse=True)[:10],
                "top_recent": top[:10],
                "all_recent": recent,
                "comments": {v["id"]: comments(yt, v["id"]) for v in top[:3]},
            })
        report["niches"][niche] = {
            "queries": spec["queries"],
            "top_search_hits": sorted(hits.values(), key=lambda v: v["views"], reverse=True)[:25],
            "channels": channels}
        print(f"{niche}: " + "; ".join(f"{c['name']} ({c['subscribers']:,} subs)" for c in channels))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
