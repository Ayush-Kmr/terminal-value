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
from .config import Config

log = logging.getLogger(__name__)

TITLE_MAX = 60          # beyond this it truncates on mobile search
CLICKBAIT = [
    "you won't believe", "shocking", "insane", "destroyed", "exposed",
    "the truth about", "what really happened", "gone wrong", "!!!",
]

TITLE_PROMPT = """Write 8 title options for a business documentary.

Company: {company}
The argument the film makes: {angle}
Strongest fact in the episode: {fact}

Rules:
- Under {maxlen} characters. Shorter is better.
- The company name must appear, because that is what people search.
- State or imply a specific claim. "The Rise and Fall of X" is banned — it is
  the most saturated title format in this niche and it promises nothing.
- No ALL CAPS words. No exclamation marks. No "shocking", "insane", "exposed".
- A number in the title is good when it is a real number from the research.
- The title and thumbnail must combine into one idea, so leave room for the
  thumbnail to carry the number if the title carries the claim.

Vary the strategy across the eight: the claim, the specific number, the
question the filing raises, the contradiction, the single decisive moment.

Return JSON array: {{"title": "...", "strategy": "...", "thumb_pairing": "<what the thumbnail should show alongside it>"}}"""


def _lint(title: str) -> tuple[int, list[str]]:
    issues: list[str] = []
    score = 0
    low = title.lower()
    if len(title) > TITLE_MAX:
        score -= 20
        issues.append(f"{len(title)} chars (max {TITLE_MAX})")
    for phrase in CLICKBAIT:
        if phrase in low:
            score -= 25
            issues.append(f"clickbait: {phrase!r}")
    if "rise and fall" in low:
        score -= 30
        issues.append("saturated 'rise and fall' format")
    if re.search(r"\b[A-Z]{4,}\b", title):
        score -= 15
        issues.append("ALL CAPS word")
    if "!" in title:
        score -= 15
        issues.append("exclamation mark")
    if re.search(r"\d", title):
        score += 8
    return score, issues


def titles(cfg: Config, company: str, angle: str, best_fact: str) -> list[dict[str, Any]]:
    options = llm.json_call(
        TITLE_PROMPT.format(company=company, angle=angle, fact=best_fact, maxlen=TITLE_MAX),
        temperature=0.9,
    )
    out = []
    for o in options:
        adj, issues = _lint(o.get("title", ""))
        out.append({**o, "lint_score": adj, "issues": issues})
    out.sort(key=lambda t: (t["lint_score"], -len(t["title"])), reverse=True)
    return out


DESC_PROMPT = """Write a YouTube description for a business documentary.

Title: {title}
Company: {company}
Argument: {angle}
Summary: {summary}

Structure exactly:
1. Two sentences that restate the argument and the stake. No "in this video".
2. A blank line, then "CHAPTERS" and the timestamps given below, verbatim.
3. A blank line, then "SOURCES" and the source list given below, verbatim.
4. A blank line, then this exact text:
   "Narration in this video is AI-synthesised. Research, script, argument and
    edit are human-directed. Corrections welcome — we pin them."

Chapters:
{chapters}

Sources:
{sources}

Return JSON: {{"description": "..."}}"""


def chapters(shots: list[dict], beats: list[dict]) -> str:
    """Chapter marks at beat boundaries. YouTube needs 00:00 as the first."""
    labels = {
        "cold_open": "Cold open", "the_rise": "The rise", "the_bet": "The bet",
        "the_crack": "The first crack", "the_fall": "The fall",
        "the_lesson": "What it actually teaches",
    }
    lines, t, seen = [], 0.0, set()
    for shot in shots:
        beat = shot.get("beat")
        if beat and beat not in seen:
            seen.add(beat)
            lines.append(f"{int(t)//60:02d}:{int(t)%60:02d} {labels.get(beat, beat)}")
        t += float(shot.get("duration", 5))
    return "\n".join(lines)


def description(cfg: Config, title: str, company: str, angle: str,
                summary: str, chapter_text: str, sources: list[dict]) -> str:
    src_lines = "\n".join(
        f"- {s.get('title','Source')} — {s.get('url','')}" for s in sources[:12]
    ) or "- Sources listed on request"
    result = llm.json_call(
        DESC_PROMPT.format(title=title, company=company, angle=angle,
                           summary=summary, chapters=chapter_text, sources=src_lines),
        temperature=0.5,
    )
    return result.get("description", "")


def tags(company: str, angle: str) -> list[str]:
    base = [
        company, f"{company} documentary", f"{company} history",
        f"what happened to {company}", f"why {company} failed",
        "business documentary", "business case study", "company collapse",
        "corporate strategy", "business history", "business analysis",
        "bankruptcy", "business failure",
    ]
    # YouTube caps total tag length at 500 characters.
    out, total = [], 0
    for t in base:
        if total + len(t) + 1 > 480:
            break
        out.append(t)
        total += len(t) + 1
    return out
