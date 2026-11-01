"""The weekly newsletter draft: the week's stories, retold for readers.

Written to reports/newsletter.md by the weekly report and announced in Telegram,
ready to paste into a newsletter service. Built only from what the videos said,
so a failure here never touches the channel.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from . import gate, llm, state, upload
from .config import ROOT, Config

log = logging.getLogger(__name__)

PROMPT = """Write this week's issue of "The Story Behind the Headline", a short free
newsletter for ordinary Americans who want business news explained in plain English.

The week's videos, with their summaries:
{stories}

Choose the 3 most useful. For each: a plain headline, 2-3 short sentences on what
happened and why it matters to someone's money or job, then "Watch: <link>".
Use only what is in the summaries. No new figures, no predictions and no advice on
what to buy or sell. Open with one friendly sentence and end with one question for
readers to reply to. Under 250 words, in Markdown."""


def week_stories(days: int = 7) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    eps = [e for e in state.load().get("episodes", [])
           if e.get("video_id") and e.get("created_at")
           and datetime.fromisoformat(e["created_at"]).astimezone(timezone.utc) > cutoff]
    if not eps:
        return []
    ids = ",".join(e["video_id"] for e in eps[-50:])
    public = upload.client().videos().list(part="snippet,status", id=ids).execute()
    return [{"title": v["snippet"]["title"].replace("#shorts", "").strip(),
             "summary": v["snippet"]["description"].split("\n\n")[0][:500],
             "url": f"https://youtu.be/{v['id']}"}
            for v in public.get("items", []) if v["status"]["privacyStatus"] == "public"]


def draft(cfg: Config) -> str:
    """Writes the draft; "" when there is nothing to write or anything fails."""
    if cfg.get_path("channel.language", "en") != "en":
        return ""
    try:
        stories = week_stories()
        if not stories:
            return ""
        text = llm.call(PROMPT.format(stories="\n".join(
            f"- {s['title']}: {s['summary']} ({s['url']})" for s in stories)), temperature=0.5)
        path = ROOT / "reports" / "newsletter.md"
        path.write_text(text.strip() + "\n", encoding="utf-8")
        gate.notify("📰 This week's newsletter draft is in reports/newsletter.md", markdown=False)
        return text
    except Exception as exc:  # noqa: BLE001
        log.warning("Newsletter draft skipped: %s", str(exc)[:200])
        return ""
