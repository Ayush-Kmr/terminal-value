"""Trending-topic discovery.

Pulls what people are searching for and reading right now in each priority
region, then has the model choose the one story worth a video. Regions and
their weights live in config.yaml under `trending`.

Sources (all keyless):
  - Google Trends daily search trends, per country
  - Google News top stories, per country
  - Google News search, for regions Trends does not cover (China)
"""
from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from typing import Any

import requests

from . import llm, state
from .config import Config

log = logging.getLogger(__name__)
UA = "Mozilla/5.0 (compatible; TerminalValue/1.0)"
HT = "{https://trends.google.com/trending/rss}"


def _get(url: str, params: dict | None = None) -> ET.Element | None:
    try:
        r = requests.get(url, params=params, headers={"User-Agent": UA}, timeout=25)
        r.raise_for_status()
        return ET.fromstring(r.content)
    except Exception as exc:  # noqa: BLE001
        log.warning("Feed failed %s: %s", url, exc)
        return None


def _traffic(text: str) -> int:
    """'20,000+' -> 20000"""
    digits = "".join(c for c in (text or "") if c.isdigit())
    return int(digits) if digits else 0


def search_trends(geo: str, limit: int = 10) -> list[dict[str, Any]]:
    root = _get("https://trends.google.com/trending/rss", {"geo": geo})
    out: list[dict[str, Any]] = []
    if root is None:
        return out
    for item in root.iter("item"):
        news = [
            f"{n.findtext(HT + 'news_item_title', '')} ({n.findtext(HT + 'news_item_source', '')})"
            for n in item.findall(HT + "news_item")
        ]
        out.append({
            "term": item.findtext("title", "").strip(),
            "searches": _traffic(item.findtext(HT + "approx_traffic", "")),
            "headlines": news[:3],
        })
    out.sort(key=lambda t: t["searches"], reverse=True)
    return out[:limit]


def news_headlines(hl: str, gl: str, query: str | None = None,
                   limit: int = 12) -> list[dict[str, str]]:
    """Top stories for a country, or a search when `query` is given."""
    ceid = f"{gl}:{hl.split('-')[0]}"
    params = {"hl": hl, "gl": gl, "ceid": ceid}
    if query:
        url = "https://news.google.com/rss/search"
        params["q"] = query
    else:
        url = "https://news.google.com/rss"
    root = _get(url, params)
    out: list[dict[str, str]] = []
    if root is None:
        return out
    for item in root.iter("item"):
        title = item.findtext("title", "").strip()
        source = item.findtext("source", "").strip()
        if source and title.endswith(f" - {source}"):
            title = title[: -len(source) - 3]
        out.append({"title": title, "source": source,
                    "date": item.findtext("pubDate", ""),
                    "url": item.findtext("link", "")})
        if len(out) >= limit:
            break
    return out


def gather(cfg: Config) -> list[dict[str, Any]]:
    """One block of raw signals per configured region."""
    blocks = []
    for region in cfg.get_path("trending.regions", []):
        code = region["code"]
        block: dict[str, Any] = {
            "region": code, "name": region.get("name", code),
            "weight": region.get("weight", 0.5),
            "language": region.get("language", "en"),
            "searches": [], "headlines": [],
        }
        if region.get("trends_geo"):
            block["searches"] = search_trends(region["trends_geo"])
        news = region.get("news") or {}
        if news:
            block["headlines"] = news_headlines(
                news.get("hl", "en-US"), news.get("gl", "US"), news.get("query"))
        log.info("Trends %s: %d searches, %d headlines",
                 code, len(block["searches"]), len(block["headlines"]))
        blocks.append(block)
    return blocks


PICK_PROMPT = """You are the commissioning editor of a YouTube explainer channel
whose audience is in the United States. Today is {today}. Choose the {n} best stories
for today's video from the live signals below.

How to rank:
- Choose American business stories: US companies, markets, jobs, prices, the
  Fed, tariffs, big tech and AI deals. Region weight: {weights}. A foreign
  story qualifies only if it clearly moves American money, jobs or prices.
- Prefer everyday American money over company results: prices, rent, housing, cars,
  groceries, jobs, debt, the things a family feels. Frame a company story around
  what it changes for ordinary people. A big open question beats a recap.
- Prefer stories that are BOTH searched and covered by several outlets today.
- A VIRAL line is a video already doing big numbers on YouTube this week. If one
  with 1,000,000 views or more is about an American business story and breaks none
  of the rules below, its subject goes FIRST, then the next biggest. We cover the
  subject from our own reporting and never reuse the video, its script or its title.
- Prefer stories with a "why" worth explaining for {minutes} minutes: a
  decision, a number, a conflict of interests, a consequence. A bare sports
  score, a film trailer, a celebrity birthday or a single-word search with no
  story behind it is not a video.
- The story must be explainable from what reputable outlets have already
  reported. Nothing that is still only rumour.

Never choose (these are unsafe to cover without a human editor):
{avoid}

What this channel's own viewers watched most (same kinds of subject do well;
never repeat the story itself):
{own}

Already covered recently, do not repeat:
{covered}

LIVE SIGNALS
{signals}

Return a JSON array of {n} objects, best first:
  topic         - short English name of the story, as a headline writer would label it
  region        - one of: {codes}
  angle         - the one question the video answers, in one sentence
  why_trending  - what happened in the last 24-48 hours
  search_query  - 3-6 English words that find news coverage of exactly this story
  wiki_title    - English Wikipedia article title for the main person/place/organisation, or ""
  keywords      - 6-10 search keywords people would type for this story
  score         - 0-100"""


def blocked(cand: dict[str, Any], words: list[str]) -> str:
    """The first banned word in a story's text, or "". A hard stop behind the
    model's own judgement, which has let a congressional race through."""
    text = " ".join(str(cand.get(k, "")) for k in
                    ("topic", "angle", "why_trending", "search_query", "keywords")).lower()
    return next((w for w in words if re.search(rf"\b{re.escape(w.lower())}\b", text)), "")


def own_winners(st: dict[str, Any], limit: int = 6) -> str:
    """Best-performing US stories first, from the numbers the weekly report saves."""
    eps = [e for e in st.get("episodes", []) if (e.get("stats") or {}).get("views")
           and e.get("region") in ("US", "MARKETS")]
    eps.sort(key=lambda e: e["stats"]["views"], reverse=True)
    return "\n".join(f"- {e['stats']['views']:,} views: {e.get('title', '')}"
                     for e in eps[:limit]) or "- (no numbers yet)"


def pick(cfg: Config, n: int = 4) -> list[dict[str, Any]]:
    """Ranked candidate stories, each tagged with region and narration language."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    blocks = gather(cfg)
    if not any(b["searches"] or b["headlines"] for b in blocks):
        raise RuntimeError("No trend feeds responded")

    lines: list[str] = []
    from . import viral
    hits = viral.recent(cfg)
    if hits:
        lines.append("## Biggest videos on YouTube this week")
        lines += [f"- VIRAL {v['views']:,} views: {v['title']}" for v in hits]
        lines.append("")
    for b in blocks:
        lines.append(f"## {b['name']} ({b['region']}, weight {b['weight']})")
        for s in b["searches"]:
            heads = "; ".join(h for h in s["headlines"] if h.strip(" ()"))
            lines.append(f"- SEARCHED {s['searches']:,}+: {s['term']} | {heads}")
        for h in b["headlines"]:
            lines.append(f"- NEWS: {h['title']} ({h['source']})")
        lines.append("")

    st = state.load()
    covered = sorted(state.covered_companies(st, cfg.get_path("trending.cooldown_days", 10)))
    regions = {r["code"]: r for r in cfg.get_path("trending.regions", [])}
    tz = ZoneInfo(cfg.get_path("channel.timezone", "Asia/Kolkata"))

    ranked = llm.json_call(
        PICK_PROMPT.format(
            today=f"{datetime.now(tz):%A %d %B %Y}", n=n,
            minutes=cfg.get_path("format.long.target_minutes", 6),
            weights=", ".join(f"{c} {r.get('weight', 0.5)}" for c, r in regions.items()),
            avoid="\n".join(f"- {a}" for a in cfg.get_path("trending.avoid", [])),
            covered="\n".join(f"- {c}" for c in covered) or "- (nothing yet)",
            own=own_winners(st),
            signals="\n".join(lines)[:24000],
            codes=", ".join(regions),
        ),
        temperature=0.4,
    )
    if not isinstance(ranked, list) or not ranked:
        raise RuntimeError("Trend picker returned nothing")

    out = []
    for cand in ranked:
        if not cand.get("topic"):
            continue
        hit = blocked(cand, cfg.get_path("trending.block_words", []) or [])
        if hit:
            log.warning("Dropped %r: mentions %r", cand["topic"], hit)
            continue
        if cand["topic"].strip().lower() in covered:
            continue
        region = cand.get("region") if cand.get("region") in regions else next(iter(regions))
        cand["region"] = region
        cand["language"] = regions[region].get("language", "en")
        cand["company"] = cand["topic"]          # the gate and history use this key
        out.append(cand)
    if not out:
        raise RuntimeError("Every trending candidate was already covered")
    log.info("Trending pick: %s [%s/%s] score %s", out[0]["topic"], out[0]["region"],
             out[0]["language"], out[0].get("score"))
    return out
