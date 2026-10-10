"""Performance feedback loop.

Pulls stats for every video, writes a report, and feeds the numbers back into
history.json so the topic picker learns. Also flags claims and underperformers.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from pathlib import Path

from . import state, upload
from .config import ROOT, Config

log = logging.getLogger(__name__)


def fetch_stats(cfg: Config) -> list[dict]:
    yt = upload.client()
    channel_id = cfg.get_path("channel.channel_id")
    out: list[dict] = []

    uploads_playlist = None
    ch = yt.channels().list(part="contentDetails", id=channel_id).execute()
    for item in ch.get("items", []):
        uploads_playlist = item["contentDetails"]["relatedPlaylists"]["uploads"]
    if not uploads_playlist:
        log.warning("Could not resolve uploads playlist for %s", channel_id)
        return out

    video_ids, token = [], None
    while True:
        resp = yt.playlistItems().list(
            part="contentDetails", playlistId=uploads_playlist,
            maxResults=50, pageToken=token).execute()
        video_ids += [i["contentDetails"]["videoId"] for i in resp.get("items", [])]
        token = resp.get("nextPageToken")
        if not token:
            break

    for i in range(0, len(video_ids), 50):
        batch = yt.videos().list(
            part="snippet,statistics,status,contentDetails",
            id=",".join(video_ids[i:i + 50])).execute()
        for v in batch.get("items", []):
            stats = v.get("statistics", {})
            out.append({
                "id": v["id"],
                "title": v["snippet"]["title"],
                "published": v["snippet"]["publishedAt"],
                "privacy": v["status"]["privacyStatus"],
                "views": int(stats.get("viewCount", 0)),
                "likes": int(stats.get("likeCount", 0)),
                "comments": int(stats.get("commentCount", 0)),
                "duration": v["contentDetails"]["duration"],
                "claimed": v["status"].get("license") == "youtube"
                           and bool(v["status"].get("embeddable") is False),
            })
    return out


def retention(cfg: Config, days: int = 28) -> dict[str, dict]:
    """Average view duration per video from the Analytics API. Requires the
    analytics scope on your refresh token."""
    try:
        from googleapiclient.discovery import build
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from .config import env

        creds = Credentials(
            token=None, refresh_token=env("YT_REFRESH_TOKEN", required=True),
            client_id=env("YT_CLIENT_ID"), client_secret=env("YT_CLIENT_SECRET"),
            token_uri="https://oauth2.googleapis.com/token", scopes=upload.SCOPES)
        creds.refresh(Request())
        ya = build("youtubeAnalytics", "v2", credentials=creds, cache_discovery=False)

        resp = ya.reports().query(
            ids=f"channel=={cfg.get_path('channel.channel_id')}",
            startDate=str(date.today() - timedelta(days=days)),
            endDate=str(date.today()),
            metrics="averageViewDuration,averageViewPercentage,estimatedMinutesWatched",
            dimensions="video", sort="-estimatedMinutesWatched", maxResults=200,
        ).execute()
        return {
            row[0]: {"avg_duration_s": row[1], "avg_pct": row[2], "minutes": row[3]}
            for row in resp.get("rows", [])
        }
    except Exception as exc:  # noqa: BLE001
        log.info("Analytics unavailable (%s) — report will omit retention", exc)
        return {}


def report(cfg: Config) -> Path:
    videos = fetch_stats(cfg)
    ret = retention(cfg)
    st = state.load()

    for ep in st.get("episodes", []):
        match = next((v for v in videos if v["id"] == ep.get("video_id")), None)
        if match:
            ep["stats"] = {"views": match["views"], "likes": match["likes"],
                           "comments": match["comments"],
                           **ret.get(match["id"], {})}
    st["channel_median_views"] = state.own_median_views(st)
    state.save(st)

    median = st["channel_median_views"] or 0
    threshold = cfg.get_path("topics.outlier_threshold", 2.5)

    total_minutes = sum(r.get("minutes", 0) for r in ret.values())
    hours = total_minutes / 60
    subs_needed = 1000

    lines = [
        f"# Reach report — {date.today()}", "",
        f"**Videos:** {len(videos)}   ",
        f"**Median views:** {median:,.0f}   ",
        f"**Watch hours (28d):** {hours:,.0f} / 4,000", "",
        "## Videos", "",
        "| Video | Views | Avg % | Status |", "|---|---|---|---|",
    ]
    for v in sorted(videos, key=lambda x: x["views"], reverse=True):
        r = ret.get(v["id"], {})
        pct = f"{r.get('avg_pct', 0):.0f}%" if r else "—"
        flag = ""
        if median and v["views"] >= median * threshold:
            flag = "🔥 outlier"
        elif median and v["views"] < median * 0.4:
            flag = "⚠️ retitle candidate"
        if v["privacy"] != "public":
            flag = f"🔒 {v['privacy']}"
        lines.append(f"| {v['title'][:60]} | {v['views']:,} | {pct} | {flag} |")

    lines += ["", "## What to do next", ""]
    weak = [v for v in videos if median and v["views"] < median * 0.4
            and v["privacy"] == "public"]
    if weak:
        lines.append(
            f"- {len(weak)} video(s) under 40% of median. Packaging is the usual "
            "cause — try a new thumbnail first, then a new title. Change one at a time.")
    low_ret = [vid for vid, r in ret.items() if r.get("avg_pct", 100) < 30]
    if low_ret:
        lines.append(
            f"- {len(low_ret)} video(s) under 30% average view percentage. That is a "
            "cold-open problem, not a topic problem.")
    if hours < 4000:
        lines.append(
            f"- {4000 - hours:,.0f} watch hours still needed for the Partner Programme.")

    out = ROOT / "reports" / "reach-report.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    log.info("Reach report written to %s", out)
    return out
