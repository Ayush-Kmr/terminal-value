#!/usr/bin/env python3
"""Apply the channel branding through the YouTube API.

    python scripts/brand_channel.py            apply
    python scripts/brand_channel.py --dry-run  show what would change

Sets the banner, the About description, channel keywords, country, the
made-for-kids status and the video watermark. The profile picture is the one
thing the API cannot set — upload assets/brand/avatar.png in YouTube Studio.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from googleapiclient.http import MediaFileUpload

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import upload  # noqa: E402
from src.config import load  # noqa: E402

BRAND = ROOT / "assets" / "brand"


def keyword_string(keywords: list[str]) -> str:
    """YouTube wants one space-separated string, multi-word phrases quoted."""
    return " ".join(f'"{k}"' if " " in k else k for k in keywords)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cfg = load()
    about = cfg.get_path("channel.about", "").strip()
    keywords = keyword_string(cfg.get_path("channel.keywords", []))

    yt = upload.client()
    ch = yt.channels().list(part="snippet,brandingSettings,status", mine=True).execute()["items"][0]
    want = cfg.get_path("channel.channel_id")
    if ch["id"] != want:
        print(f"Refusing: token belongs to channel {ch['id']}, config says {want}.")
        return 1
    print(f"Channel: {ch['snippet']['title']} ({ch['id']})")

    branding = ch.get("brandingSettings", {})
    channel_block = dict(branding.get("channel", {}))
    channel_block.update({
        "description": about,
        "keywords": keywords,
        "defaultLanguage": cfg.get_path("channel.language", "en"),
        "country": cfg.get_path("channel.country", "US"),
    })

    if args.dry_run:
        print("Would set description:\n" + about)
        print("Would set keywords: " + keywords)
        print(f"Would upload {BRAND / 'banner.png'} and {BRAND / 'watermark.png'}")
        return 0

    failures = 0

    # ---- banner + about + keywords (one update) --------------------------
    try:
        banner = yt.channelBanners().insert(
            media_body=MediaFileUpload(str(BRAND / "banner.png"), mimetype="image/png"),
        ).execute()
        yt.channels().update(part="brandingSettings", body={
            "id": ch["id"],
            "brandingSettings": {
                "channel": channel_block,
                "image": {"bannerExternalUrl": banner["url"]},
            },
        }).execute()
        print("OK   banner, description, keywords, country")
    except Exception as exc:  # noqa: BLE001
        failures += 1
        print(f"FAIL banner/description: {exc}")

    # ---- audience --------------------------------------------------------
    try:
        yt.channels().update(part="status", body={
            "id": ch["id"],
            "status": {"selfDeclaredMadeForKids": False},
        }).execute()
        print("OK   channel set to 'not made for kids'")
    except Exception as exc:  # noqa: BLE001
        failures += 1
        print(f"FAIL made-for-kids status: {exc}")

    # ---- watermark -------------------------------------------------------
    try:
        yt.watermarks().set(
            channelId=ch["id"],
            body={"timing": {"type": "offsetFromStart", "offsetMs": 0},
                  "position": {"type": "corner", "cornerPosition": "bottomRight"}},
            media_body=MediaFileUpload(str(BRAND / "watermark.png"), mimetype="image/png"),
        ).execute()
        print("OK   video watermark (entire video)")
    except Exception as exc:  # noqa: BLE001
        failures += 1
        print(f"FAIL watermark: {exc}")

    print("\nProfile picture: upload assets/brand/avatar.png in YouTube Studio "
          "(Customisation → Profile → Picture). The API has no endpoint for it.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
