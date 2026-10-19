#!/usr/bin/env python3
"""One-time OAuth: produces the YT_REFRESH_TOKEN the pipeline needs.

    python scripts/get_youtube_token.py ~/Downloads/client_secret_XXXX.json

Sign in with the Google account that OWNS the Terminal Value brand account.
Google will warn that the app is unverified — that is expected, it is your own
app. Click Advanced, then Continue.
"""
import sys
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/youtube.force-ssl",   # caption uploads
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    secrets = Path(sys.argv[1]).expanduser()
    if not secrets.exists():
        print(f"No such file: {secrets}")
        return 1

    flow = InstalledAppFlow.from_client_secrets_file(str(secrets), SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")

    print("\n" + "=" * 62)
    print("Add these three to .env AND to your GitHub Actions secrets:\n")
    print(f"YT_CLIENT_ID={flow.client_config['client_id']}")
    print(f"YT_CLIENT_SECRET={flow.client_config['client_secret']}")
    print(f"YT_REFRESH_TOKEN={creds.refresh_token}")
    print("=" * 62)
    if not creds.refresh_token:
        print("\n!! No refresh token returned. Revoke the app at "
              "https://myaccount.google.com/permissions and run this again.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
