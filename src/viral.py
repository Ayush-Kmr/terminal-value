"""What is already proven on YouTube: the demand signal, never the content.

Two uses. `recent` lists the niche's biggest videos of the past week, as one more
signal for the daily story picker. `history_story` finds a past business story
that has already drawn a million views or more, so a long video can tell it.

Only titles and view counts are read. Every video is researched and written from
its own sources: re-uploading or re-narrating someone else's video would be a
copyright strike, and YouTube does not pay for reused content.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from . import llm, state, trends, upload
from .config import Config

log = logging.getLogger(__name__)


def _search(yt, query: str, order: str = "relevance", after: datetime | None = None,
            long_only: bool = False) -> list[dict[str, Any]]:
    """Up to 25 US English results with their view counts."""
    params: dict[str, Any] = {"part": "id", "q": query, "type": "video", "maxResults": 25,
                              "order": order, "regionCode": "US", "relevanceLanguage": "en"}
    if after:
        params["publishedAfter"] = after.strftime("%Y-%m-%dT%H:%M:%SZ")
    if long_only:
        params["videoDuration"] = "long"       # over 20 minutes
    ids = [i["id"]["videoId"] for i in yt.search().list(**params).execute().get("items", [])]
    if not ids:
        return []
    items = yt.videos().list(part="snippet,statistics", id=",".join(ids)).execute()["items"]
    return [{"id": v["id"], "title": v["snippet"]["title"],
             "channel": v["snippet"]["channelTitle"],
             "views": int(v["statistics"].get("viewCount", 0))} for v in items]


def _gather(cfg: Config, queries: list[str], floor: int, **kw) -> list[dict[str, Any]]:
    yt = upload.client()
    found: dict[str, dict[str, Any]] = {}
    for q in queries:
        for v in _search(yt, q, **kw):
            if v["views"] >= floor and v["channel"] != cfg.get_path("channel.name"):
                found[v["id"]] = v
    # English titles only: the audience is American.
    found = {k: v for k, v in found.items() if v["title"].isascii()}
    return sorted(found.values(), key=lambda v: v["views"], reverse=True)


def recent(cfg: Config, limit: int = 12) -> list[dict[str, Any]]:
    """The niche's biggest videos of the past week. Empty on any failure: this
    is a hint for the picker, never a reason to lose a run."""
    days = int(cfg.get_path("viral.news_days", 7))
    try:
        hits = _gather(cfg, cfg.get_path("viral.news_queries", []),
                       int(cfg.get_path("viral.news_min_views", 100000)),
                       order="viewCount",
                       after=datetime.now(timezone.utc) - timedelta(days=days))
    except Exception as exc:  # noqa: BLE001
        log.warning("Viral search unavailable: %s", str(exc)[:160])
        return []
    log.info("Viral this week: %d videos, top %s", len(hits),
             hits[0]["title"][:70] if hits else "none")
    return hits[:limit]


HISTORY_PROMPT = """You choose the next long documentary for a YouTube channel about
American business, for a US audience.

These videos by other channels have each passed {floor:,} views, so the subjects
are proven. Choose the best SUBJECT: the one real company, person or event from the
past that one of these videos is about. We will research and tell it ourselves from primary sources and reporting,
so never reuse a title, a script or a structure from these videos.

{videos}

Choose a story about money: a corporate collapse, a fraud, a market crash, a
rivalry, a bad bet, a rise and fall. It must have ended at least five years ago
and be well documented.

Never choose:
- War, terrorism, a disaster or anything centred on deaths: YouTube limits ads on these
- A living person accused of something no court or regulator has ruled on
{avoid}

Already covered, do not repeat:
{covered}

Return one JSON object:
  topic         - short name of the story, as a viewer would search it
  angle         - the one question the film answers, in one sentence
  search_query  - 3-6 words that find reporting on exactly this story
  wiki_title    - English Wikipedia article title for it
  keywords      - 6-10 search phrases people type about it
  video         - the number of the video above that is about exactly this subject"""


def history_story(cfg: Config) -> dict[str, Any] | None:
    """A past story proven at a million views, or the next from the fallback list."""
    floor = int(cfg.get_path("viral.history_min_views", 1000000))
    done = [str(e.get("history", "")) for e in state.load().get("episodes", [])
            if e.get("history")]
    words = cfg.get_path("trending.block_words", []) or []
    avoid = "\n".join(f"- {a}" for a in cfg.get_path("viral.history_avoid", []))
    try:
        hits = _gather(cfg, cfg.get_path("viral.history_queries", []), floor)
        rejected: list[str] = []
        for _ in range(3 if hits else 0):
            pick = llm.json_call(HISTORY_PROMPT.format(
                floor=floor, avoid=avoid,
                covered="\n".join(f"- {d}" for d in [*done, *rejected]) or "- (nothing yet)",
                videos="\n".join(f"{n}. {v['views']:,} views: {v['title']}"
                                 for n, v in enumerate(hits[:40], 1))),
                temperature=0.5)
            topic = str(pick.get("topic") or "").strip()
            try:
                chosen = hits[:40][int(pick.get("video")) - 1]
            except (TypeError, ValueError, IndexError):
                chosen = None
            proof = _proof(topic, [chosen]) if chosen else None
            if proof and topic.lower() not in {d.lower() for d in done} \
                    and not trends.blocked(pick, words):
                log.info("History pick: %s (%d views: %s)", topic, proof["views"], proof["title"])
                return _story(pick, topic)
            log.info("History pick %r rejected: no video on it above the floor", topic)
            rejected.append(topic)
    except Exception as exc:  # noqa: BLE001
        log.warning("History search failed (%s); using the fallback list", str(exc)[:160])
    for t in cfg.get_path("viral.history_fallback", []):
        if t["topic"].lower() not in {d.lower() for d in done}:
            return _story(t, t["topic"])
    log.warning("No history topic left: add more to viral.history_fallback")
    return None


def _proof(topic: str, hits: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The million-view video that is really about this topic: the model's own
    citation cannot be trusted (it once offered a Madoff film as proof of Lehman)."""
    generic = {"collapse", "scandal", "crash", "fall", "rise", "story", "history", "company",
               "fraud", "crisis", "bankruptcy", "the"}
    names = [w for w in re.findall(r"[a-z0-9]+", topic.lower())
             if len(w) > 2 and w not in generic]
    if not names:
        return None
    return next((v for v in hits if names[0] in v["title"].lower()), None)


def _story(pick: dict[str, Any], topic: str) -> dict[str, Any]:
    return {"topic": topic, "company": topic, "angle": pick.get("angle", ""),
            "region": "US", "language": "en", "history": topic,
            "search_query": pick.get("search_query") or topic,
            "wiki_title": pick.get("wiki_title", ""),
            "keywords": [str(k) for k in pick.get("keywords") or []][:10] or [topic]}
