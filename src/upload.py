"""YouTube Data API v3 upload.

Everything uploads Private. Gate 2 is what flips it. Note that until Google
approves your API audit form, YouTube forces every API upload to Private
regardless of what you request — that is their rule for new projects, not a bug.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from .config import Config, env

log = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]


def client():
    creds = Credentials(
        token=None,
        refresh_token=env("YT_REFRESH_TOKEN", required=True),
        client_id=env("YT_CLIENT_ID", required=True),
        client_secret=env("YT_CLIENT_SECRET", required=True),
        token_uri="https://oauth2.googleapis.com/token",
        scopes=SCOPES,
    )
    creds.refresh(Request())
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def next_slot(cfg: Config, kind: str = "long") -> datetime:
    """The next configured publish slot, in the channel's timezone."""
    tz = ZoneInfo(cfg.get_path("channel.timezone", "Asia/Kolkata"))
    now = datetime.now(tz)
    days = [d.lower() for d in cfg.get_path(f"format.{kind}.publish_days", ["tuesday"])]
    hh, mm = (cfg.get_path(f"format.{kind}.publish_time", "17:30") + ":0").split(":")[:2]
    names = ["monday", "tuesday", "wednesday", "thursday",
             "friday", "saturday", "sunday"]
    for ahead in range(0, 8):
        cand = (now + timedelta(days=ahead)).replace(
            hour=int(hh), minute=int(mm), second=0, microsecond=0)
        if names[cand.weekday()] in days and cand > now:
            return cand
    return now + timedelta(days=1)


def upload(cfg: Config, video: Path, meta: dict[str, Any],
           privacy: str = "private", publish_at: datetime | None = None) -> str:
    yt = client()
    body: dict[str, Any] = {
        "snippet": {
            "title": meta["title"][:100],
            "description": meta.get("description", "")[:5000],
            "tags": meta.get("tags", [])[:60],
            "categoryId": str(cfg.get_path("upload.category_id", 22)),
            "defaultLanguage": cfg.get_path("channel.language", "en"),
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": bool(cfg.get_path("upload.made_for_kids", False)),
            "containsSyntheticMedia": bool(
                cfg.get_path("upload.synthetic_media_disclosure", True)),
        },
    }
    if publish_at is not None:
        body["status"]["privacyStatus"] = "private"
        body["status"]["publishAt"] = publish_at.isoformat()

    media = MediaFileUpload(str(video), chunksize=8 * 1024 * 1024, resumable=True,
                            mimetype="video/mp4")
    request = yt.videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            log.info("Upload %d%%", int(status.progress() * 100))

    video_id = response["id"]
    log.info("Uploaded: https://youtu.be/%s", video_id)
    return video_id


def set_thumbnail(video_id: str, image: Path) -> None:
    try:
        client().thumbnails().set(
            videoId=video_id, media_body=MediaFileUpload(str(image))).execute()
    except Exception as exc:  # noqa: BLE001
        log.warning("Thumbnail upload failed (channel verified?): %s", exc)


def upload_captions(video_id: str, srt: Path, language: str = "en") -> None:
    try:
        client().captions().insert(
            part="snippet",
            body={"snippet": {"videoId": video_id, "language": language,
                              "name": "English", "isDraft": False}},
            media_body=MediaFileUpload(str(srt)),
        ).execute()
    except Exception as exc:  # noqa: BLE001
        log.warning("Caption upload failed: %s", exc)


def set_privacy(video_id: str, privacy: str) -> None:
    yt = client()
    yt.videos().update(
        part="status",
        body={"id": video_id, "status": {"privacyStatus": privacy,
                                         "selfDeclaredMadeForKids": False}},
    ).execute()
    log.info("Video %s set to %s", video_id, privacy)


def add_to_playlist(video_id: str, playlist_title: str) -> None:
    yt = client()
    try:
        existing = yt.playlists().list(part="snippet", mine=True, maxResults=50).execute()
        pid = next((p["id"] for p in existing.get("items", [])
                    if p["snippet"]["title"].lower() == playlist_title.lower()), None)
        if not pid:
            created = yt.playlists().insert(
                part="snippet,status",
                body={"snippet": {"title": playlist_title},
                      "status": {"privacyStatus": "public"}},
            ).execute()
            pid = created["id"]
        yt.playlistItems().insert(
            part="snippet",
            body={"snippet": {"playlistId": pid,
                              "resourceId": {"kind": "youtube#video", "videoId": video_id}}},
        ).execute()
    except Exception as exc:  # noqa: BLE001
        log.warning("Playlist add failed: %s", exc)
