"""Stock and archival imagery.

Order of preference: Pexels video -> Pixabay -> Wikimedia Commons (public domain
and CC0/CC-BY only). Share-alike and non-commercial licences are filtered out,
because a monetised channel cannot use them.
"""
from __future__ import annotations

import logging
from pathlib import Path

import requests

from ..config import env

log = logging.getLogger(__name__)
UA = "TerminalValue/1.0 (documentary production)"

# Licences that are safe on a monetised channel. Everything else is dropped.
SAFE_LICENCES = {"cc0", "pd", "publicdomain", "cc-by", "cc-by-4.0", "cc-by-3.0", "cc-by-2.0"}


def _download(url: str, dest: Path) -> Path | None:
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=60, stream=True)
        r.raise_for_status()
        with open(dest, "wb") as fh:
            for chunk in r.iter_content(1 << 16):
                fh.write(chunk)
        return dest
    except Exception as exc:  # noqa: BLE001
        log.warning("Download failed %s: %s", url, exc)
        return None


def pexels_video(query: str, dest: Path) -> dict | None:
    key = env("PEXELS_API_KEY")
    if not key:
        return None
    try:
        r = requests.get(
            "https://api.pexels.com/videos/search",
            params={"query": query, "per_page": 5, "orientation": "landscape", "size": "medium"},
            headers={"Authorization": key, "User-Agent": UA}, timeout=30,
        )
        r.raise_for_status()
        for video in r.json().get("videos", []):
            files = [f for f in video.get("video_files", []) if (f.get("width") or 0) >= 1280]
            if not files:
                continue
            best = min(files, key=lambda f: abs((f.get("width") or 0) - 1920))
            out = dest.with_suffix(".mp4")
            if _download(best["link"], out):
                return {"path": str(out), "kind": "video", "credit": video.get("user", {}).get("name", "Pexels"),
                        "licence": "pexels", "url": video.get("url", "")}
    except Exception as exc:  # noqa: BLE001
        log.warning("Pexels search failed for %r: %s", query, exc)
    return None


def pixabay_image(query: str, dest: Path) -> dict | None:
    key = env("PIXABAY_API_KEY")
    if not key:
        return None
    try:
        r = requests.get(
            "https://pixabay.com/api/",
            params={"key": key, "q": query[:100], "image_type": "photo",
                    "orientation": "horizontal", "per_page": 5, "safesearch": "true"},
            timeout=30,
        )
        r.raise_for_status()
        for hit in r.json().get("hits", []):
            out = dest.with_suffix(".jpg")
            if _download(hit.get("largeImageURL", ""), out):
                return {"path": str(out), "kind": "image", "credit": hit.get("user", "Pixabay"),
                        "licence": "pixabay", "url": hit.get("pageURL", "")}
    except Exception as exc:  # noqa: BLE001
        log.warning("Pixabay search failed for %r: %s", query, exc)
    return None


def pixabay_video(query: str, dest: Path) -> dict | None:
    """Moving footage. Same key as Pixabay images."""
    key = env("PIXABAY_API_KEY")
    if not key:
        return None
    try:
        r = requests.get(
            "https://pixabay.com/api/videos/",
            params={"key": key, "q": query[:100], "per_page": 5, "safesearch": "true"},
            timeout=30,
        )
        r.raise_for_status()
        for hit in r.json().get("hits", []):
            files = hit.get("videos", {})
            best = files.get("medium") or files.get("large") or files.get("small") or {}
            if not best.get("url") or (best.get("width") or 0) < (best.get("height") or 0):
                continue  # skip vertical clips
            out = dest.with_suffix(".mp4")
            if _download(best["url"], out):
                return {"path": str(out), "kind": "video", "credit": hit.get("user", "Pixabay"),
                        "licence": "pixabay", "url": hit.get("pageURL", "")}
    except Exception as exc:  # noqa: BLE001
        log.warning("Pixabay video search failed for %r: %s", query, exc)
    return None


def wikimedia_image(query: str, dest: Path) -> dict | None:
    """Archival photographs. Licence-filtered — this is where real historical
    imagery of real companies and people legitimately comes from."""
    try:
        r = requests.get(
            "https://commons.wikimedia.org/w/api.php",
            params={
                "action": "query", "format": "json", "generator": "search",
                "gsrnamespace": 6, "gsrsearch": query, "gsrlimit": 10,
                "prop": "imageinfo", "iiprop": "url|extmetadata", "iiurlwidth": 1920,
            },
            headers={"User-Agent": UA}, timeout=30,
        )
        r.raise_for_status()
        pages = r.json().get("query", {}).get("pages", {})
        for _, page in pages.items():
            info = (page.get("imageinfo") or [{}])[0]
            meta = info.get("extmetadata", {})
            licence = (meta.get("LicenseShortName", {}).get("value", "") or "").lower()
            normalised = licence.replace(" ", "-").replace("public-domain", "pd")
            if not any(safe in normalised for safe in SAFE_LICENCES):
                continue  # drop share-alike / non-commercial
            url = info.get("thumburl") or info.get("url")
            # Commons also holds SVGs, PDFs, audio and video; stills only.
            if not url or Path(url.split("?")[0]).suffix.lower() not in {".jpg", ".jpeg", ".png"}:
                continue
            out = dest.with_suffix(Path(url).suffix or ".jpg")
            if _download(url, out):
                return {
                    "path": str(out), "kind": "image",
                    "credit": meta.get("Artist", {}).get("value", "Wikimedia Commons"),
                    "licence": licence, "url": info.get("descriptionurl", ""),
                    "needs_attribution": "cc-by" in normalised,
                }
    except Exception as exc:  # noqa: BLE001
        log.warning("Wikimedia search failed for %r: %s", query, exc)
    return None


def find(query: str, dest: Path, prefer_archival: bool = False,
         allow_video: bool = True) -> dict | None:
    """Archival first for real people and places, stock first for mood/texture."""
    video = [pexels_video, pixabay_video] if allow_video else []
    chain = ([wikimedia_image, *video, pixabay_image] if prefer_archival
             else [*video, pixabay_image, wikimedia_image])
    for fn in chain:
        result = fn(query, dest)
        if result:
            log.info("Visual for %r via %s", query, fn.__name__)
            return result
    log.warning("No stock found for %r", query)
    return None
