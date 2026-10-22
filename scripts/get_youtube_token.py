#!/usr/bin/env python3
"""One-time OAuth: produces the YT_REFRESH_TOKEN the pipeline needs.

    python scripts/get_youtube_token.py ~/Downloads/client_secret_XXXX.json
    python scripts/get_youtube_token.py ~/Downloads/client_secret_XXXX.json --secret YT_REFRESH_TOKEN_TA

Sign in with the Google account that OWNS the channel. When that account has
more than one channel, Google asks which one: pick the channel this token is
for (the English one for YT_REFRESH_TOKEN, the Tamil one for
YT_REFRESH_TOKEN_TA). Google will warn that the app is unverified — that is
expected, it is your own app. Click Advanced, then Continue.

With --secret NAME the token is also saved straight into the repo's GitHub
Actions secret NAME (needs the gh command, logged in).
"""
import subprocess
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
    secret_name = sys.argv[sys.argv.index("--secret") + 1] if "--secret" in sys.argv else ""
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
        return 1

    from googleapiclient.discovery import build
    ch = build("youtube", "v3", credentials=creds, cache_discovery=False) \
        .channels().list(part="snippet", mine=True).execute()["items"][0]
    print(f"\nThis token posts to the channel: {ch['snippet']['title']}  (ID {ch['id']})")

    if secret_name:
        res = subprocess.run(["gh", "secret", "set", secret_name, "-R", "Ayush-Kmr/terminal-value"],
                             input=creds.refresh_token, text=True)
        print(f"Saved as GitHub secret {secret_name}." if res.returncode == 0
              else f"!! Could not save the secret; add {secret_name} by hand.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
