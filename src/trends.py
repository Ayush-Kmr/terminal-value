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
                news.get("hl", "en-IN"), news.get("gl", "IN"), news.get("query"))
        log.info("Trends %s: %d searches, %d headlines",
                 code, len(block["searches"]), len(block["headlines"]))
        blocks.append(block)
    return blocks


PICK_PROMPT = """You are the commissioning editor of a YouTube explainer channel
whose main audience is in India. Today is {today}. Choose the {n} best stories
for today's video from the live signals below.

How to rank:
- Region weight matters: {weights}. An India story beats an equally strong
  story elsewhere. A foreign story ranks high only if Indians will care
  (money, jobs, visas, tech, geopolitics touching India, a global first).
- Prefer stories that are BOTH searched and covered by several outlets today.
- Prefer stories with a "why" worth explaining for {minutes} minutes: a
  decision, a number, a conflict of interests, a consequence. A bare sports
  score, a film trailer, a celebrity birthday or a single-word search with no
  story behind it is not a video.
- The story must be explainable from what reputable outlets have already
  reported. Nothing that is still only rumour.

Never choose (these are unsafe to cover without a human editor):
{avoid}

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


def pick(cfg: Config, n: int = 4) -> list[dict[str, Any]]:
    """Ranked candidate stories, each tagged with region and narration language."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    blocks = gather(cfg)
    if not any(b["searches"] or b["headlines"] for b in blocks):
        raise RuntimeError("No trend feeds responded")

    lines: list[str] = []
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
