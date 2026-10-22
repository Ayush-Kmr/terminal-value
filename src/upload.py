"""YouTube Data API v3 upload.

Everything uploads Private. Gate 2 is what flips it. Note that until Google
approves your API audit form, YouTube forces every API upload to Private
regardless of what you request — that is their rule for new projects, not a bug.
"""
from __future__ import annotations

import logging
import re
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


def credentials(refresh_token: str | None = None) -> Credentials:
    """Signed-in credentials for one channel: the token in YT_REFRESH_TOKEN
    unless another channel's token is passed."""
    creds = Credentials(
        token=None,
        refresh_token=refresh_token or env("YT_REFRESH_TOKEN", required=True),
        client_id=env("YT_CLIENT_ID", required=True),
        client_secret=env("YT_CLIENT_SECRET", required=True),
        token_uri="https://oauth2.googleapis.com/token",
        # No scopes: the refresh then returns every scope the token was
        # granted, so a token re-made with the caption scope gets captions,
        # and an older one keeps working for uploads.
    )
    creds.refresh(Request())
    return creds


def client(refresh_token: str | None = None):
    return build("youtube", "v3", credentials=credentials(refresh_token),
                 cache_discovery=False)


def slots(cfg: Config, kind: str = "long", count: int = 1,
          min_lead_minutes: int = 20) -> list[datetime]:
    """The next `count` daily publish slots for this format, in channel time.
    A slot closer than `min_lead_minutes` is skipped: YouTube needs time to
    process the upload before a scheduled release."""
    tz = ZoneInfo(cfg.get_path("channel.timezone", "Asia/Kolkata"))
    now = datetime.now(tz)
    times = cfg.get_path(f"format.{kind}.publish_times") or ["19:00"]
    out: list[datetime] = []
    for ahead in range(0, 8):
        for hhmm in sorted(times):
            hh, mm = hhmm.split(":")[:2]
            cand = (now + timedelta(days=ahead)).replace(
                hour=int(hh), minute=int(mm), second=0, microsecond=0)
            if cand > now + timedelta(minutes=min_lead_minutes):
                out.append(cand)
            if len(out) >= count:
                return out
    return out


def next_slot(cfg: Config, kind: str = "long") -> datetime:
    return slots(cfg, kind, 1)[0]


def _safe(text: str, max_bytes: int) -> str:
    """YouTube rejects < and > in titles, descriptions and tags, and measures
    the description limit in bytes — Devanagari is three bytes a character."""
    text = str(text or "").replace("<", "").replace(">", "")
    return text.encode("utf-8")[:max_bytes].decode("utf-8", "ignore").strip()


_verified: str | None = None


def check_channel(cfg: Config, yt) -> str:
    """The channel the token belongs to, refusing to go on when it is not the
    one config names. Two channels share this repo; a token mix-up must never
    put a Tamil video on the English channel or the other way round."""
    global _verified
    if _verified:
        return _verified
    mine = yt.channels().list(part="id,snippet", mine=True).execute()["items"][0]
    want = cfg.get_path("channel.channel_id")
    if not want:
        raise RuntimeError(
            f"channel.channel_id is not set for {cfg.get_path('channel.name')}. The token "
            f"belongs to {mine['snippet']['title']} ({mine['id']}): if that is right, "
            "put that ID in the channel's config file.")
    if mine["id"] != want:
        raise RuntimeError(
            f"Refusing to upload: the token belongs to {mine['snippet']['title']} "
            f"({mine['id']}), but {cfg.get_path('channel.name')} is {want}.")
    _verified = mine["id"]
    return _verified


def upload(cfg: Config, video: Path, meta: dict[str, Any],
           privacy: str = "private", publish_at: datetime | None = None) -> str:
    yt = client()
    check_channel(cfg, yt)
    language = meta.get("language") or cfg.get_path("channel.language", "en")
    body: dict[str, Any] = {
        "snippet": {
            "title": _safe(meta["title"], 400)[:100],
            "description": _safe(meta.get("description", ""), 4900),
            "tags": [t for t in (_safe(t, 200) for t in meta.get("tags", [])[:60]) if t],
            "categoryId": str(cfg.get_path("upload.category_id", 22)),
            "defaultLanguage": language,
            "defaultAudioLanguage": language,
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
                              "name": "", "isDraft": False}},
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


def topic_playlists(cfg: Config, *texts: str) -> list[str]:
    """The upload.topic_playlists a video belongs in, by whole-word match on
    its subject, title and tags."""
    words = set(re.findall(r"[a-z0-9]+", " ".join(texts).lower()))
    return [name for name, keys in (cfg.get_path("upload.topic_playlists", {}) or {}).items()
            if words & {k.lower() for k in keys}]


def add_to_playlists(cfg: Config, video_id: str, language: str, *texts: str) -> None:
    """The language playlist plus every matching topic playlist."""
    names = [cfg.get_path(f"upload.playlists.{language}")] + topic_playlists(cfg, *texts)
    for name in dict.fromkeys(n for n in names if n):
        add_to_playlist(video_id, name)


def status(video_id: str) -> dict[str, Any]:
    """What YouTube actually did with an upload: privacy, processing state and
    any rejection. New API projects have uploads locked to private until
    Google's API audit is passed, whatever was requested."""
    try:
        items = client().videos().list(part="status", id=video_id).execute().get("items", [])
        return items[0]["status"] if items else {}
    except Exception as exc:  # noqa: BLE001
        log.warning("Status check failed: %s", exc)
        return {}
