"""What people actually type into YouTube search about a story.

YouTube's own search suggestions (the autocomplete under the search box) are
the best free signal of demand: each one is a phrase real viewers searched.
Titles and tags are written around them.
"""
from __future__ import annotations

import logging
import re

import requests

log = logging.getLogger(__name__)
URL = "https://suggestqueries.google.com/complete/search"
OTHER_LANGUAGES = ("hindi", "tamil", "telugu", "malayalam", "kannada", "marathi", "bengali", "gujarati")


def suggestions(query: str, hl: str = "en", gl: str = "US") -> list[str]:
    try:
        r = requests.get(URL, params={"client": "firefox", "ds": "yt", "hl": hl, "gl": gl,
                                      "q": query}, timeout=8)
        r.raise_for_status()
        return [str(s) for s in r.json()[1]]
    except Exception as exc:  # noqa: BLE001 - a missing hint never stops a run
        log.info("Search suggestions for %r unavailable: %s", query, exc)
        return []


def _core(subject: str) -> str:
    """The searchable head of a subject: "RBI repo rate decision" -> "rbi repo rate"."""
    # "Verizon's $2 Billion Deal" -> "verizon deal", not "verizon s 2".
    plain = re.sub(r"[’']s\b", "", subject)
    words = [w for w in re.findall(r"[^\W\d][\w.&-]*", plain) if w.lower() not in
             {"decision", "news", "latest", "update", "the", "a", "of", "and", "why", "how",
              "billion", "million", "trillion"}]
    return " ".join(words[:3]).lower()


# Words any search about a story may add. Anything else a suggestion adds must
# appear in the story itself: autocomplete also returns what people type about
# other things with the same name ("bernie madoff movie scene", "gas prices
# starlito", "kalshi dance", "gas cylinder price hindi" on a US petrol story),
# and those went out as tags (review of 2026-11-04 uploads).
GENERIC = set("""
news explained explain explainer today latest live update updates now new this week
why how what when who is are was does do did so the a an in of to for on and vs
stock stocks share shares price prices rate rates market markets money make makes
work works crash fall falls drop rise rises up down going happened happening
india indian us usa america american english full video analysis meaning
documentary story history expensive cheap costly cost costs
""".split())


def _relevant(phrase: str, core: str, context: str) -> bool:
    words = re.findall(r"[a-z0-9]+", phrase.lower())
    ctx = context.lower()
    for w in words:
        if w in GENERIC or w in core.split() or w.isdigit():
            continue
        # Endings vary ("trading"/"trades"), so a longer
        # word counts when its first four letters appear in the story.
        stem = w[:4] if len(w) >= 5 else w      # "trading" ~ "trades"
        if len(stem) < 3 or stem not in ctx:
            return False
    return True


def for_story(subject: str, language: str = "en", limit: int = 8,
              context: str = "") -> list[str]:
    """Real search phrases about this story, best first. With `context` (the
    story's own text), phrases that add words the story never mentions are
    dropped."""
    core = _core(subject)
    if not core:
        return []
    key = core.split()[0]
    queries = [(core, "en"), (f"{core} news", "en"), (f"{core} explained", "en")]
    if " " not in core:                        # a company or ticker on its own
        queries.insert(0, (f"{core} stock", "en"))
    out: list[str] = []
    for q, hl in queries:
        for s in suggestions(q, hl, "US"):
            low = s.lower()
            if not re.search(rf"(?<![\w]){re.escape(key)}(?![\w])", low):
                continue                       # about something else ("irene ...")
            if any(x in low for x in OTHER_LANGUAGES):
                continue
            if context and not _relevant(s, core, f"{subject} {context}"):
                continue
            if s not in out:
                out.append(s)
    log.info("Search phrases for %r (%s): %s", subject, language, "; ".join(out[:limit]))
    return out[:limit]
