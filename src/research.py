"""Research: assemble a sourced fact base before any script is written.

The rule that keeps this channel monetisable is simple — every claim in the
finished video traces to something a viewer could go and check. Wikipedia may
*start* the research. It may never finish it.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field, asdict
from typing import Any

import requests

from . import llm
from .config import Config

log = logging.getLogger(__name__)

UA = "Chapter11Research/1.0 (documentary research; contact via channel)"


@dataclass
class Source:
    title: str
    url: str
    kind: str          # sec_filing | court | transcript | press | book | wikipedia
    date: str | None = None
    note: str = ""


@dataclass
class FactBase:
    company: str
    angle: str
    summary: str = ""
    facts: list[dict[str, Any]] = field(default_factory=list)
    timeline: list[dict[str, Any]] = field(default_factory=list)
    financials: list[dict[str, Any]] = field(default_factory=list)
    quotes: list[dict[str, Any]] = field(default_factory=list)
    people: list[dict[str, Any]] = field(default_factory=list)
    sources: list[Source] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["sources"] = [asdict(s) if not isinstance(s, dict) else s for s in self.sources]
        return d


# ----------------------------------------------------------------- wikipedia

def wikipedia_summary(company: str) -> tuple[str, Source | None]:
    """Starting point only. Gives us names, dates and spelling to search on."""
    try:
        r = requests.get(
            "https://en.wikipedia.org/w/api.php",
            params={
                "action": "query", "format": "json", "prop": "extracts",
                "exintro": 0, "explaintext": 1, "redirects": 1, "titles": company,
            },
            headers={"User-Agent": UA}, timeout=20,
        )
        r.raise_for_status()
        pages = r.json().get("query", {}).get("pages", {})
        for _, page in pages.items():
            text = page.get("extract", "")
            if text:
                url = "https://en.wikipedia.org/wiki/" + page["title"].replace(" ", "_")
                return text[:20000], Source(page["title"], url, "wikipedia")
    except Exception as exc:  # noqa: BLE001
        log.warning("Wikipedia lookup failed for %s: %s", company, exc)
    return "", None


# ----------------------------------------------------------------- SEC EDGAR

def sec_filings(company: str, limit: int = 8) -> list[Source]:
    """EDGAR full-text search. Free, no key, and the single highest-value
    source for any US public company's collapse."""
    out: list[Source] = []
    try:
        r = requests.get(
            "https://efts.sec.gov/LATEST/search-index",
            params={"q": f'"{company}"', "forms": "10-K,10-Q,8-K"},
            headers={"User-Agent": UA}, timeout=25,
        )
        if r.status_code != 200:
            r = requests.get(
                "https://efts.sec.gov/LATEST/search-index?q=" + requests.utils.quote(f'"{company}"'),
                headers={"User-Agent": UA}, timeout=25,
            )
        data = r.json()
        for hit in data.get("hits", {}).get("hits", [])[:limit]:
            src = hit.get("_source", {})
            adsh = src.get("adsh", "").replace("-", "")
            cik = (src.get("ciks") or [""])[0]
            url = (
                f"https://www.sec.gov/Archives/edgar/data/{cik.lstrip('0')}/{adsh}/"
                if cik and adsh else "https://www.sec.gov/edgar/search/"
            )
            out.append(
                Source(
                    title=f"{src.get('form', 'Filing')} — {src.get('display_names', [company])[0]}",
                    url=url, kind="sec_filing", date=src.get("file_date"),
                )
            )
    except Exception as exc:  # noqa: BLE001
        log.info("EDGAR search unavailable for %s (%s) — continuing", company, exc)
    return out


# ----------------------------------------------------------------- synthesis

EXTRACT_PROMPT = """You are a documentary researcher building a fact base.

Company: {company}
Episode angle (the argument this film makes): {angle}

Source material:
---
{material}
---

Extract ONLY what the source material supports. Where you know something
relevant that is NOT in the material, you may include it but you MUST set
"needs_verification": true on that item. Never invent a number, a date or a quote.

Return JSON:
{{
  "summary": "<3 sentences: what happened and why the angle holds>",
  "facts": [
    {{"claim": "...", "value": "<the number/date, if any>", "source_hint": "<where to verify>", "needs_verification": false, "beat": "<cold_open|the_rise|the_bet|the_crack|the_fall|the_lesson>"}}
  ],
  "timeline": [{{"year": "1998", "event": "...", "significance": "..."}}],
  "financials": [{{"metric": "revenue", "year": "2008", "value": 4200000000, "unit": "USD", "source_hint": "..."}}],
  "quotes": [{{"text": "<verbatim, under 25 words>", "speaker": "...", "role": "...", "date": "...", "source_hint": "..."}}],
  "people": [{{"name": "...", "role": "...", "why_they_matter": "..."}}],
  "contrarian_claim": "<the one thing most retellings get wrong, and the evidence>",
  "surprising_facts": ["<facts a well-informed viewer would not know>"]
}}

Aim for 20-30 facts, 10-15 timeline entries, and enough financials to draw at
least two honest charts. Quotes must be verbatim and under 25 words each."""


def build(cfg: Config, company: str, angle: str) -> FactBase:
    log.info("Researching %s", company)

    wiki_text, wiki_src = wikipedia_summary(company)
    filings = sec_filings(company)

    material = wiki_text or f"(No encyclopedia entry retrieved for {company}.)"
    if filings:
        material += "\n\nSEC filings located:\n" + "\n".join(
            f"- {f.title} ({f.date or 'n.d.'}) {f.url}" for f in filings
        )

    data = llm.json_call(
        EXTRACT_PROMPT.format(company=company, angle=angle, material=material[:40000]),
        temperature=0.3,
    )

    fb = FactBase(company=company, angle=angle)
    fb.summary = data.get("summary", "")
    fb.facts = data.get("facts", [])
    fb.timeline = sorted(data.get("timeline", []), key=lambda t: str(t.get("year", "")))
    fb.financials = data.get("financials", [])
    fb.quotes = data.get("quotes", [])
    fb.people = data.get("people", [])
    fb.sources = ([wiki_src] if wiki_src else []) + filings

    # carry the editorial spine forward
    fb.facts.append(
        {"claim": data.get("contrarian_claim", ""), "beat": "the_lesson",
         "is_contrarian": True, "needs_verification": True}
    )
    for s in data.get("surprising_facts", []):
        fb.facts.append({"claim": s, "beat": "cold_open", "is_surprising": True})

    validate(cfg, fb)
    return fb


def validate(cfg: Config, fb: FactBase) -> list[str]:
    """Returns a list of warnings. The pipeline surfaces these at gate 2 rather
    than silently publishing weak research."""
    warnings: list[str] = []
    min_sources = cfg.get_path("research.min_sources", 5)
    banned_sole = set(cfg.get_path("research.banned_sole_source", []))

    kinds = {s.kind if isinstance(s, Source) else s.get("kind") for s in fb.sources}
    if len(fb.sources) < min_sources:
        warnings.append(f"Only {len(fb.sources)} sources (minimum {min_sources}).")
    if kinds and kinds.issubset(banned_sole):
        warnings.append("Wikipedia is the only source. Add primary material before scripting.")
    if cfg.get_path("research.require_primary", True) and not (
        kinds & {"sec_filing", "court", "transcript"}
    ):
        warnings.append("No primary source (filing/court/transcript) located.")

    unverified = [f for f in fb.facts if f.get("needs_verification")]
    if len(unverified) > len(fb.facts) * 0.4:
        warnings.append(
            f"{len(unverified)}/{len(fb.facts)} facts are unverified — research is thin."
        )
    if not fb.financials:
        warnings.append("No financial data — the episode will have no charts.")

    for w in warnings:
        log.warning("RESEARCH: %s", w)
    return warnings


# ================================================================= news mode
# Trending stories have no filings. The fact base comes from what outlets have
# already published: a search-grounded briefing plus the day's headlines.

def grounded_briefing(topic: str, angle: str) -> tuple[str, list[Source]]:
    """Ask Gemini to brief the story using live Google Search. Returns the
    briefing text and the pages it was grounded on. Empty on any failure —
    the caller falls back to headlines."""
    import os

    key = os.getenv("GEMINI_API_KEY")
    if not key:
        return "", []
    prompt = (
        f"Brief me on this news story as of today: {topic}\n"
        f"The question to answer: {angle}\n\n"
        "Search for current reporting. Write a factual briefing of about 500 words: "
        "what happened and when (exact dates), who is involved (full names and roles), "
        "the key numbers, the background needed to understand it, what each side says, "
        "and what happens next. Only include what reputable outlets have reported. "
        "Where reports conflict or something is unconfirmed, say so explicitly."
    )
    try:
        r = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{llm.GEMINI_MODEL}:generateContent",
            headers={"x-goog-api-key": key},
            json={"contents": [{"parts": [{"text": prompt}]}],
                  "tools": [{"google_search": {}}],
                  "generationConfig": {"temperature": 0.2}},
            timeout=120,
        )
        r.raise_for_status()
        cand = r.json()["candidates"][0]
        text = "".join(p.get("text", "") for p in cand.get("content", {}).get("parts", []))
        chunks = cand.get("groundingMetadata", {}).get("groundingChunks", [])
        sources, seen = [], set()
        for c in chunks:
            web = c.get("web") or {}
            if web.get("uri") and web.get("title") not in seen:
                seen.add(web.get("title"))
                sources.append(Source(web.get("title", "Source"), web["uri"], "press"))
        log.info("Grounded briefing: %d chars, %d sources", len(text), len(sources))
        return text, sources
    except Exception as exc:  # noqa: BLE001
        log.warning("Grounded briefing unavailable (%s); using headlines only", exc)
        return "", []


NEWS_EXTRACT_PROMPT = """You are a news researcher building a fact base for an
explainer video. Today is {today}.

Story: {topic}
The question the video answers: {angle}

Source material (a search-grounded briefing, today's headlines, background):
---
{material}
---

Extract ONLY what the source material supports. Never invent a number, a date,
a name or a quote. If the material marks something as unconfirmed or disputed,
set "needs_verification": true on that item. Do not add outside knowledge about
recent events — your own memory of current affairs is out of date.

Return JSON:
{{
  "summary": "<3 sentences: what happened, why it matters, what is still unknown>",
  "facts": [
    {{"claim": "...", "value": "<the number/date, if any>", "source_hint": "<outlet>", "needs_verification": false, "beat": "<{beat_ids}>"}}
  ],
  "timeline": [{{"year": "<date or year>", "event": "...", "significance": "..."}}],
  "financials": [{{"metric": "...", "year": "...", "value": 0, "unit": "...", "source_hint": "..."}}],
  "quotes": [{{"text": "<verbatim from the material, under 25 words>", "speaker": "...", "role": "...", "date": "...", "source_hint": "..."}}],
  "people": [{{"name": "...", "role": "...", "why_they_matter": "..."}}],
  "surprising_facts": ["<specific details from the material most viewers will not know>"]
}}

Aim for 15-25 facts and 5-10 timeline entries. Include "financials" only when
the material gives a real series of numbers that would make an honest chart;
otherwise return an empty list. Include quotes only if they appear verbatim."""


def build_news(cfg: Config, story: dict[str, Any]) -> FactBase:
    from datetime import datetime
    from . import trends

    topic, angle = story["topic"], story.get("angle", "")
    log.info("Researching story: %s", topic)

    briefing, sources = grounded_briefing(topic, angle)

    query = (story.get("search_query") or topic) + " when:3d"
    heads = trends.news_headlines("en-IN", "IN", query, limit=15)
    if story.get("language") == "hi":
        heads += trends.news_headlines("hi", "IN", story.get("search_query") or topic, limit=6)

    material = ""
    if briefing:
        material += "BRIEFING (search-grounded):\n" + briefing
    if story.get("why_trending"):
        material += "\n\nWHY IT IS TRENDING:\n" + story["why_trending"]
    if heads:
        material += "\n\nHEADLINES FROM THE LAST 3 DAYS:\n" + "\n".join(
            f"- {h['title']} ({h['source']}, {h['date'][:16]})" for h in heads)

    wiki_src = None
    if story.get("wiki_title"):
        wiki_text, wiki_src = wikipedia_summary(story["wiki_title"])
        if wiki_text:
            material += ("\n\nBACKGROUND (encyclopedia; may predate this week's events):\n"
                         + wiki_text[:6000])

    if not briefing and len(heads) < 3:
        raise RuntimeError(f"Not enough reporting found on {topic!r} to make a video")

    beat_ids = "|".join(b["id"] for b in cfg.get_path("structure.beats", []))
    data = llm.json_call(
        NEWS_EXTRACT_PROMPT.format(
            today=f"{datetime.now():%d %B %Y}", topic=topic, angle=angle,
            material=material[:40000], beat_ids=beat_ids),
        temperature=0.2,
    )

    fb = FactBase(company=topic, angle=angle)
    fb.summary = data.get("summary", "")
    fb.facts = data.get("facts", [])
    fb.timeline = data.get("timeline", [])
    fb.financials = data.get("financials", [])
    fb.quotes = data.get("quotes", [])
    fb.people = data.get("people", [])
    first_beat = beat_ids.split("|")[0] if beat_ids else "hook"
    for s in data.get("surprising_facts", []):
        fb.facts.append({"claim": s, "beat": first_beat, "is_surprising": True})

    # Publishers actually cited, deduplicated by outlet.
    fb.sources = list(sources)
    seen = {s.title for s in fb.sources}
    for h in heads:
        if h["source"] and h["source"] not in seen and len(fb.sources) < 12:
            seen.add(h["source"])
            fb.sources.append(Source(f"{h['source']}: {h['title']}", h["url"], "press",
                                     h["date"][:16]))
    if wiki_src:
        fb.sources.append(wiki_src)
    return fb


def validate_news(cfg: Config, fb: FactBase) -> list[str]:
    warnings: list[str] = []
    outlets = [s for s in fb.sources
               if (s.kind if isinstance(s, Source) else s.get("kind")) == "press"]
    need = cfg.get_path("research.min_news_sources", 3)
    if len(outlets) < need:
        warnings.append(f"Only {len(outlets)} news sources (minimum {need}).")
    unverified = [f for f in fb.facts if f.get("needs_verification")]
    if fb.facts and len(unverified) > len(fb.facts) * 0.4:
        warnings.append(f"{len(unverified)}/{len(fb.facts)} facts are unconfirmed.")
    if len(fb.facts) < 8:
        warnings.append(f"Thin research: only {len(fb.facts)} facts.")
    for w in warnings:
        log.warning("RESEARCH: %s", w)
    return warnings
