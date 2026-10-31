"""Check a finished script against its own research before it is voiced.

Every figure and every full name in the narration, and every figure on a card,
must appear in the research. Lines that fail are rewritten once by the model
and checked again; a line that still fails is removed. A script that loses too
much is refused, and the run moves on to the next story.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from . import llm

log = logging.getLogger(__name__)
NUMBER = re.compile(r"\d+(?:\.\d+)?")
NAME = re.compile(r"\b[A-Z][a-z]+(?:\s[A-Z][a-z]+)+\b")
MAX_LOST = 0.25         # share of shots a script may lose and still be used

REWRITE_PROMPT = """These lines from a video script contain a figure or a name that is
not in the research. Rewrite each line so it uses only what the research says.
If the research does not support the point, write a plainer sentence without
that figure or name. Keep each line about the same length, in plain English.

RESEARCH:
{evidence}

LINES:
{lines}

Return JSON: {{"lines": ["<line 1>", ...]}} with exactly {n} strings, in order."""


def evidence(fb: Any) -> str:
    d = fb.to_dict() if hasattr(fb, "to_dict") else fb
    parts = {k: d.get(k) for k in ("company", "angle", "summary", "facts", "timeline",
                                   "financials", "quotes", "people")}
    return json.dumps(parts, ensure_ascii=False) + " " + str(d.get("material", ""))


def _bad_numbers(text: str, plain: str) -> list[str]:
    return [n for n in NUMBER.findall(text.replace(",", ""))
            if not re.search(rf"(?<![\d.]){re.escape(n)}(?!\d|\.\d)", plain)]


COMMON = {"united", "states", "america", "wall", "street", "white", "house", "supreme", "court",
          "federal", "reserve", "new", "york", "big", "tech", "last", "next", "this", "that", "the",
          "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "january",
          "february", "march", "april", "may", "june", "july", "august", "september", "october",
          "november", "december"}


def _bad_names(text: str, low: str) -> list[str]:
    return [name for name in NAME.findall(text)
            if not all(w.lower() in low or w.lower() in COMMON for w in name.split())]


def problems(text: str, source: str) -> list[str]:
    return _bad_numbers(text, source.replace(",", "")) + _bad_names(text, source.lower())


def _clean_card(spec: dict, source: str) -> None:
    """A figure the research lacks comes off the card, whatever the voice says."""
    for key in ("value", "headline"):
        if spec.get(key) and _bad_numbers(str(spec[key]), source.replace(",", "")):
            spec[key] = ""


def check(script: dict, fb: Any) -> tuple[dict, list[str]]:
    source = evidence(fb)
    shots = [s for b in script["beats"] for s in b.get("shots", [])
             if (s.get("narration") or "").strip()]
    total = len(shots) or 1
    for s in shots:
        _clean_card(s.get("visual_spec") or {}, source)
    bad = [s for s in shots if problems(s["narration"], source)]
    notes: list[str] = []
    if bad:
        log.info("Fact check: %d of %d lines need rewriting: %s", len(bad), total,
                 "; ".join(", ".join(problems(s["narration"], source))[:40] for s in bad[:5]))
        got = llm.json_call(REWRITE_PROMPT.format(
            evidence=source[:7000], n=len(bad),
            lines="\n".join(f"{i + 1}. {s['narration']}" for i, s in enumerate(bad))),
            temperature=0.2)
        got = got.get("lines") if isinstance(got, dict) else got
        if isinstance(got, list) and len(got) == len(bad):
            for s, line in zip(bad, got):
                if isinstance(line, str) and line.strip():
                    s["narration"] = line.strip()
    still = [s for s in shots if problems(s["narration"], source)]
    if len(still) > total * MAX_LOST:
        raise RuntimeError(f"Fact check failed: {len(still)} of {total} lines are not "
                           "supported by the research")
    if still:
        drop = {id(s) for s in still}
        for b in script["beats"]:
            b["shots"] = [s for s in b.get("shots", []) if id(s) not in drop]
        notes.append(f"Fact check removed {len(still)} unsupported line(s)")
    for b in script["beats"]:
        b["narration"] = " ".join(s.get("narration", "") for s in b.get("shots", []))
    script["full_narration"] = "\n\n".join(b["narration"] for b in script["beats"])
    script["word_count"] = len(script["full_narration"].split())
    log.info("Fact check passed: %d lines, %d rewritten, %d removed",
             total, len(bad), len(still))
    return script, notes
