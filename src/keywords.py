"""What people actually type into YouTube search about a story.

YouTube's own search suggestions (the autocomplete under the search box) are
the best free signal of demand: each one is a phrase real viewers searched.
Titles and tags are written around them. Tamil viewers mostly search in
English with "tamil" added ("gold rate tamil", "share market news today in
tamil"), so the Tamil channel asks for both forms.
"""
from __future__ import annotations

import logging
import re

import requests

log = logging.getLogger(__name__)
URL = "https://suggestqueries.google.com/complete/search"
OTHER_LANGUAGES = ("hindi", "telugu", "malayalam", "kannada", "marathi", "bengali", "gujarati")


def suggestions(query: str, hl: str = "en") -> list[str]:
    try:
        r = requests.get(URL, params={"client": "firefox", "ds": "yt", "hl": hl, "gl": "IN",
                                      "q": query}, timeout=8)
        r.raise_for_status()
        return [str(s) for s in r.json()[1]]
    except Exception as exc:  # noqa: BLE001 - a missing hint never stops a run
        log.info("Search suggestions for %r unavailable: %s", query, exc)
        return []


def _core(subject: str) -> str:
    """The searchable head of a subject: "RBI repo rate decision" -> "rbi repo rate"."""
    words = [w for w in re.findall(r"[\w.&-]+", subject) if w.lower() not in
             {"decision", "news", "latest", "update", "the", "a", "of", "and", "why", "how"}]
    return " ".join(words[:3]).lower()


def for_story(subject: str, language: str = "en", limit: int = 8) -> list[str]:
    """Real search phrases about this story, best first."""
    core = _core(subject)
    if not core:
        return []
    key = core.split()[0]
    if language == "ta":
        queries = [(f"{core} tamil", "en"), (f"{core} news tamil", "en"), (core, "ta")]
    else:
        queries = [(core, "en"), (f"{core} news", "en"), (f"{core} explained", "en")]
        if " " not in core:                    # a company or ticker on its own
            queries.insert(0, (f"{core} stock", "en"))
    out: list[str] = []
    for q, hl in queries:
        for s in suggestions(q, hl):
            low = s.lower()
            if not re.search(rf"(?<![\w]){re.escape(key)}(?![\w])", low):
                continue                       # about something else ("irene ...")
            if language != "ta" and any(x in low for x in OTHER_LANGUAGES + ("tamil",)):
                continue
            if language == "ta" and any(x in low for x in OTHER_LANGUAGES):
                continue
            if s not in out:
                out.append(s)
    log.info("Search phrases for %r (%s): %s", subject, language, "; ".join(out[:limit]))
    return out[:limit]
