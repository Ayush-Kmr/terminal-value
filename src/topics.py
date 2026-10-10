"""Topic selection.

Three inputs, in order of weight:
  1. Our own performance history (what has worked on THIS channel)
  2. Competitor outliers, measured against each competitor's OWN median
  3. A generated candidate pool, filtered against the cooldown list

The outlier rule matters: 400K views on a 2M-sub channel is a flop; 180K on a
40K-sub channel is the signal worth chasing.
"""
from __future__ import annotations

import logging
from typing import Any

from . import llm, state
from .config import Config, read_channel_doc

log = logging.getLogger(__name__)

CANDIDATE_PROMPT = """You are the development producer for a business documentary
channel. Propose {n} companies for a 12-minute rise-and-fall documentary.

Channel voice:
{voice}

Hard requirements for every candidate:
- A real company with a documented, verifiable arc (rise, pivotal bet, decline).
- The story must be resolvable from public record: filings, court documents,
  earnings calls, major press. No company whose collapse is purely rumoured.
- At least five years between founding and the events covered.
- NOT currently in active litigation where the facts are still contested.
- Avoid the five most obvious examples that every business channel has covered
  (Blockbuster, Nokia, Kodak, Enron, Theranos) unless you have a genuinely
  under-reported angle, in which case say what it is.

Already covered or rejected — do not propose these:
{excluded}

For each candidate return:
  company          - the company name
  angle            - the specific argument this episode makes, in one sentence.
                     Not "the story of X" but a claim that could be wrong.
  surprising_fact  - one verifiable detail most retellings omit
  why_now          - why this resonates with a 2026 business audience
  evidence_sources - 3-5 specific source types that would substantiate it
  difficulty       - easy | medium | hard (how hard to research well)

Return a JSON array of {n} objects."""


def propose(cfg: Config, n: int = 6) -> list[dict[str, Any]]:
    st = state.load()
    excluded = sorted(
        state.covered_companies(st, cfg.get_path("topics.cooldown_days", 365))
    )
    prompt = CANDIDATE_PROMPT.format(
        n=n,
        voice=read_channel_doc("voice.md")[:2500],
        excluded="\n".join(f"- {c}" for c in excluded) or "- (nothing yet)",
    )
    candidates = llm.json_call(prompt, temperature=0.9)
    if not isinstance(candidates, list):
        raise RuntimeError("Topic generator did not return a list")

    ranked = rank(cfg, candidates, st)
    log.info("Proposed %d topics", len(ranked))
    return ranked


RANK_PROMPT = """Score each candidate documentary topic from 0-100 on likely
performance for a business documentary channel.

Weight the score:
  40%  Curiosity gap in the angle - does the claim make someone need the answer?
  25%  Breadth - how many people have heard of this company at all?
  20%  Evidence quality - can this be told from primary sources?
  15%  Freshness - how saturated is this story on YouTube already?

Penalise heavily: topics whose angle is just "here is what happened".
Reward: angles that contradict the popular version of events.

Candidates:
{candidates}

Return a JSON array of objects with: company, score (int), reasoning (one
sentence), predicted_title (a title under 60 characters that could actually be
used).  Sort by score descending."""


def rank(cfg: Config, candidates: list[dict], st: dict) -> list[dict[str, Any]]:
    import json as _json

    scored = llm.json_call(
        RANK_PROMPT.format(candidates=_json.dumps(candidates, indent=2)),
        temperature=0.3,
    )
    by_name = {c.get("company", "").lower(): c for c in candidates}
    merged = []
    for s in scored:
        base = by_name.get(s.get("company", "").lower(), {})
        merged.append({**base, **s})
    return merged


def is_outlier(views: int, channel_median: float, threshold: float) -> bool:
    """A video outperforms when it beats its own channel's median by `threshold`x."""
    if not channel_median:
        return False
    return views >= channel_median * threshold
