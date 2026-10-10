"""Hook generation and scoring.

The first 15 seconds decide the video. We generate five, score them on a rubric
that approximates what actually drives retention, and only the winner survives.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from . import llm
from .config import read_channel_doc

log = logging.getLogger(__name__)

BANNED = [
    "little did they know", "the rest is history", "but there was a catch",
    "this is where things get interesting", "buckle up", "let that sink in",
    "a cautionary tale", "game-changer", "at the end of the day",
    "in today's video", "without further ado", "what if i told you",
    "imagine a world", "have you ever wondered",
]

GEN_PROMPT = """Write 5 completely different cold opens for a business documentary.

Company: {company}
Angle: {angle}
Strongest surprising facts available:
{facts}

Narrator voice:
{voice}

Each cold open: 35-55 words, spoken aloud in about 15-20 seconds.

Make the five genuinely different in STRATEGY, not just wording:
  1. Open on the single most absurd verifiable number.
  2. Open mid-scene on a specific day, with a specific person.
  3. Open on the contradiction (what they said vs what the filing showed).
  4. Open on the thing the viewer thinks they know, then undercut it.
  5. Open on the ending, then rewind.

Never begin with a rhetorical question. Never use: {banned}

Return JSON array of objects: {{"strategy": "...", "text": "..."}}"""

SCORE_PROMPT = """Score each cold open 0-100 for a business documentary audience.

Rubric:
  35  Curiosity gap — does the viewer now need the rest of the video?
  25  Specificity — real numbers, names, dates beat abstractions every time
  20  Pace — does it get to the point inside the first sentence?
  15  Credibility — does it sound like someone who read the filings?
   5  Freshness — does it avoid documentary cliché?

Deduct 25 points for any rhetorical question. Deduct 20 for stacked adjectives.
Deduct 15 if the first six words are generic scene-setting.

Cold opens:
{hooks}

Return JSON array: {{"index": 0, "score": int, "weakness": "<one sentence>"}}"""


def _penalty(text: str) -> tuple[int, list[str]]:
    """Deterministic checks the model shouldn't be trusted to apply itself."""
    issues: list[str] = []
    score = 0
    low = text.lower()
    for phrase in BANNED:
        if phrase in low:
            score -= 30
            issues.append(f"banned phrase: {phrase!r}")
    first = text.split(".")[0]
    if first.strip().endswith("?"):
        score -= 25
        issues.append("opens on a rhetorical question")
    if not re.search(r"\d", text):
        score -= 15
        issues.append("no concrete number")
    words = len(text.split())
    if words > 65:
        score -= 10
        issues.append(f"too long ({words} words)")
    if words < 25:
        score -= 10
        issues.append(f"too short ({words} words)")
    return score, issues


def generate_and_score(company: str, angle: str, facts: list[dict]) -> dict[str, Any]:
    import json as _json

    top = [f for f in facts if f.get("is_surprising")][:6] or facts[:6]
    hooks = llm.json_call(
        GEN_PROMPT.format(
            company=company, angle=angle,
            facts="\n".join(f"- {f.get('claim','')} {f.get('value','')}" for f in top),
            voice=read_channel_doc("voice.md")[:1800],
            banned=", ".join(BANNED[:8]),
        ),
        temperature=0.95,
    )

    scored = llm.json_call(
        SCORE_PROMPT.format(hooks=_json.dumps(
            [{"index": i, "text": h["text"]} for i, h in enumerate(hooks)], indent=2
        )),
        temperature=0.2,
    )
    score_by_idx = {s["index"]: s for s in scored}

    results = []
    for i, h in enumerate(hooks):
        pen, issues = _penalty(h["text"])
        model = score_by_idx.get(i, {})
        results.append({
            **h,
            "model_score": model.get("score", 50),
            "penalty": pen,
            "final_score": model.get("score", 50) + pen,
            "weakness": model.get("weakness", ""),
            "issues": issues,
        })

    results.sort(key=lambda r: r["final_score"], reverse=True)
    log.info("Best hook scored %d (%s)", results[0]["final_score"], results[0]["strategy"])
    return {"winner": results[0], "all": results}
