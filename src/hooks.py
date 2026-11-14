"""Hook generation and scoring.

The first 15 seconds decide the video. We generate five, score them on a rubric
that approximates what actually drives retention, and only the winner survives.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from . import llm
from .config import language_rules, read_channel_doc

log = logging.getLogger(__name__)

BANNED = [
    "little did they know", "the rest is history", "but there was a catch",
    "this is where things get interesting", "buckle up", "let that sink in",
    "a cautionary tale", "game-changer", "at the end of the day",
    "in today's video", "without further ado", "what if i told you",
    "imagine a world", "have you ever wondered",
]

GEN_PROMPT = """Write 5 completely different cold opens for a {kind}.

Subject: {company}
The question the video answers: {angle}

{language}

Use only the facts listed below. Do not add numbers, names or events from memory.
Strongest surprising facts available:
{facts}

Narrator voice:
{voice}

Each cold open: 30-40 words, spoken aloud in about 15 seconds, in two parts:
  0-5 seconds: ONE short sentence (under 15 words) that names the subject and
  contains the single most surprising number from the facts. No scene-setting.
  5-15 seconds: the payoff, said plainly: the exact question this video answers,
  what the viewer will know by the end, and why it touches their own money,
  job or prices. This promise is required, and must be finished by word 30.
  End on the first thing the video shows next, so there is no pause where a
  viewer could leave.
The video's long videos lose most viewers in their first tenth (audit
2026-11-14), so every word past 40 costs viewers.
No background, history, definitions or "to understand this" before 15 seconds:
viewers of this channel leave in the first minute when the opening explains
before it pays off.

Make the five genuinely different in STRATEGY, not just wording:
  1. Open on the single most absurd verifiable number.
  2. Open mid-scene on a specific day, with a specific person.
  3. Open on the contradiction (what was said vs what the record shows).
  4. Open on the thing the viewer thinks they know, then undercut it.
  5. Open on the ending, then rewind.

{proven}
Never compare a figure to anything unless the facts above make that comparison.
Never begin with a rhetorical question. Never use: {banned}

Return JSON array of objects: {{"strategy": "...", "text": "..."}}"""

PROVEN = """  6. strategy "proven": open with the idea of this line, which already held
     viewers in our own Short on the same story ({views} views): "{line}"
     Keep its number and its punch, then add the payoff the Short had no time for.
Write 6 cold opens in all."""

SCORE_PROMPT = """Score each cold open 0-100 for a YouTube explainer audience.

Rubric:
  25  Payoff by 15 seconds — within the first ~30 words, do they know exactly
      what question is answered and what they will get?
  20  Curiosity gap — does the viewer now need the rest of the video?
  20  Viewer stake — does it say what this means for the viewer's own money or life?
  15  Specificity — a real number in the FIRST sentence; names and dates beat abstractions
  10  Pace — no background or context before the payoff
  10  Credibility — does it sound like someone who read the source material?

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
    # Split on sentence ends, not every dot: "$1.25 trillion" is one sentence.
    first = re.split(r"(?<=[.!?])\s+", text.strip())[0]
    if first.strip().endswith("?"):
        score -= 25
        issues.append("opens on a rhetorical question")
    if not re.search(r"\d", text):
        score -= 15
        issues.append("no concrete number")
    elif not re.search(r"\d", first):
        score -= 10
        issues.append("no number in the first sentence")
    if len(first.split()) > 15:
        score -= 10
        issues.append(f"first sentence too long ({len(first.split())} words)")
    words = len(text.split())
    if words > 45:
        score -= 10
        issues.append(f"too long ({words} words)")
    if words < 25:
        score -= 10
        issues.append(f"too short ({words} words)")
    return score, issues


def generate_and_score(company: str, angle: str, facts: list[dict],
                       language: str = "en", kind: str = "documentary",
                       proven: str = "", proven_views: int = 0) -> dict[str, Any]:
    """`proven`: the opening line of our own Short on this story that already
    held viewers; a cold open built on it gets a small head start."""
    import json as _json

    top = [f for f in facts if f.get("is_surprising")][:6] or facts[:6]
    hooks = llm.json_call(
        GEN_PROMPT.format(
            company=company, angle=angle, kind=kind,
            language=language_rules(language),
            facts="\n".join(f"- {f.get('claim','')} {f.get('value','')}" for f in top),
            voice=read_channel_doc("voice.md")[:1800],
            banned=", ".join(BANNED[:8]),
            proven=PROVEN.format(line=proven.strip(), views=proven_views) if proven else "",
        ),
        temperature=0.95,
    )

    scored = llm.json_call(
        SCORE_PROMPT.format(hooks=_json.dumps(
            [{"index": i, "text": h["text"]} for i, h in enumerate(hooks)], indent=2
        )),
        temperature=0.2,
    )
    hooks = [h for h in hooks if isinstance(h, dict) and h.get("text")]
    if not hooks:
        raise RuntimeError("Hook generator returned nothing usable")
    score_by_idx = {s.get("index"): s for s in scored if isinstance(s, dict)}

    results = []
    for i, h in enumerate(hooks):
        pen, issues = _penalty(h["text"])
        if proven and str(h.get("strategy", "")).lower().startswith("proven"):
            pen += 8                    # already tested on this channel's viewers
            issues.append("built on the Short that held viewers (+8)")
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
