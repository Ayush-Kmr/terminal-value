"""Packaging: titles, description, tags, chapters.

Titles are generated as a set, linted against hard rules, then scored — the
same approach as hooks, because packaging decides whether good research ever
gets watched.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from . import llm
from .config import Config, latin_only

log = logging.getLogger(__name__)

TITLE_MAX = 70          # beyond this it truncates on mobile search
CLICKBAIT = [
    "you won't believe", "shocking", "insane", "destroyed", "exposed",
    "the truth about", "gone wrong", "!!!",
]

TITLE_LANGUAGE = {
    "hi": ("Write the titles in Hindi (Devanagari script). Keep names of people, "
           "companies, countries and products in English/Latin letters when that is "
           "how Indians type them into search (e.g. RBI, Tesla, H-1B)."),
    "en": "Write the titles in English.",
}

TITLE_PROMPT = """Write 8 title options for a YouTube {kind}.

Subject: {company}
The question the video answers: {angle}
Strongest fact in the video: {fact}

{language}

Rules:
- Under {maxlen} characters. Shorter is better.
- The main thing people are searching (the name of the person, company, place
  or event) must appear near the start.
- State or imply a specific claim or question. Promise only what the video delivers.
- No ALL CAPS words. No exclamation marks. No "shocking", "insane", "exposed".
- A number in the title is good when it is a real number from the research.
- The title and thumbnail must combine into one idea, so let the thumbnail
  carry the number if the title carries the claim.

Vary the strategy across the eight: the claim, the specific number, the open
question, the contradiction, the consequence for an ordinary viewer.

Return a JSON array of objects:
  title         - the title
  strategy      - which strategy
  thumb_text    - 2-4 words for the thumbnail, ALWAYS in English capitals-friendly
                  Latin letters, not repeating the title
  thumb_figure  - one real figure from the research for the thumbnail, at most
                  6 characters (e.g. "-94%", "$4B", "37"), or "" if none fits
  summary       - two plain sentences describing the video, in the title's language"""


def _lint(title: str) -> tuple[int, list[str]]:
    issues: list[str] = []
    score = 0
    low = title.lower()
    if len(title) > TITLE_MAX:
        score -= 20
        issues.append(f"{len(title)} chars (max {TITLE_MAX})")
    if len(title) < 12:
        score -= 30
        issues.append("too short")
    for phrase in CLICKBAIT:
        if phrase in low:
            score -= 25
            issues.append(f"clickbait: {phrase!r}")
    if re.search(r"\b[A-Z]{5,}\b", title):
        score -= 15
        issues.append("ALL CAPS word")
    if "!" in title:
        score -= 15
        issues.append("exclamation mark")
    if re.search(r"\d", title):
        score += 8
    return score, issues


def titles(cfg: Config, company: str, angle: str, best_fact: str,
           language: str = "en") -> list[dict[str, Any]]:
    options = llm.json_call(
        TITLE_PROMPT.format(
            company=company, angle=angle, fact=best_fact, maxlen=TITLE_MAX,
            kind=cfg.get_path("format.kind", "documentary"),
            language=TITLE_LANGUAGE.get(language, TITLE_LANGUAGE["en"])),
        temperature=0.9,
    )
    out = []
    for o in options:
        if not isinstance(o, dict) or not o.get("title"):
            continue
        adj, issues = _lint(o["title"])
        o["thumb_text"] = latin_only(o.get("thumb_text", "")) or latin_only(company)
        o["thumb_figure"] = latin_only(str(o.get("thumb_figure", "")))[:6]
        out.append({**o, "lint_score": adj, "issues": issues})
    if not out:
        raise RuntimeError("Title generator returned nothing usable")
    out.sort(key=lambda t: (t["lint_score"], -len(t["title"])), reverse=True)
    return out


def chapters(cfg: Config, shots: list[dict]) -> str:
    """Chapter marks at beat boundaries. YouTube needs 00:00 as the first."""
    labels = {b["id"]: b.get("label", b["id"].replace("_", " ").capitalize())
              for b in cfg.get_path("structure.beats", [])}
    lines, t, seen = [], 0.0, set()
    for shot in shots:
        beat = shot.get("beat")
        if beat and beat not in seen:
            seen.add(beat)
            lines.append(f"{int(t)//60:02d}:{int(t)%60:02d} {labels.get(beat, beat)}")
        t += float(shot.get("duration", 5))
    # YouTube ignores chapters unless there are at least three, 10s+ apart.
    return "\n".join(lines) if len(lines) >= 3 else ""


def hashtags(keywords: list[str], language: str) -> str:
    tags = []
    for k in keywords[:3]:
        word = re.sub(r"[^0-9A-Za-zऀ-ॿ]", "", k.title())
        if word and f"#{word}" not in tags:
            tags.append(f"#{word}")
    tags.append("#hindinews" if language == "hi" else "#explained")
    return " ".join(tags)


def description(cfg: Config, summary: str, chapter_text: str, sources: list[dict],
                keywords: list[str], language: str = "en") -> str:
    """Built from parts rather than generated: the sources and the disclosure
    must be exact, and a model asked to copy them verbatim sometimes does not."""
    parts = [summary.strip()]
    if chapter_text:
        parts.append("CHAPTERS\n" + chapter_text)

    lines = []
    for s in sources[:12]:
        title, url = s.get("title", "Source"), s.get("url", "")
        # News-aggregator redirect links run to hundreds of characters.
        lines.append(f"- {title}" + (f" — {url}" if url and len(url) < 120 else ""))
    if lines:
        parts.append("SOURCES\n" + "\n".join(lines))

    parts.append(cfg.get_path("channel.disclosure", "").strip())
    parts.append(hashtags(keywords, language))
    return "\n\n".join(p for p in parts if p)[:4900]


def tags(company: str, keywords: list[str], language: str = "en") -> list[str]:
    base = [company, *keywords, f"{company} explained", f"{company} news",
            "explained", "news explained", "current affairs"]
    if language == "hi":
        base += ["hindi news", "news in hindi", f"{company} hindi", "samachar"]
    # YouTube caps total tag length at 500 characters.
    out, total, seen = [], 0, set()
    for t in base:
        t = str(t).strip()[:60]
        if not t or t.lower() in seen:
            continue
        if total + len(t) + 1 > 480:
            break
        seen.add(t.lower())
        out.append(t)
        total += len(t) + 1
    return out
