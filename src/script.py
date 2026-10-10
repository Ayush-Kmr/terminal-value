"""Script writing: beat by beat, with a shot list attached to every sentence.

Written one beat at a time rather than in a single pass. A 12-minute script
generated in one shot drifts into uniform sentence rhythm, which is exactly the
texture that reads as machine-made.
"""
from __future__ import annotations

import logging
from typing import Any

from . import llm
from .config import Config, read_channel_doc

log = logging.getLogger(__name__)

BEAT_PROMPT = """Write beat "{beat_id}" of a business documentary.

COMPANY: {company}
EPISODE ARGUMENT: {angle}
THIS BEAT'S JOB: {intent}
TARGET LENGTH: {words} words of narration (about {seconds} seconds spoken)

NARRATOR VOICE (follow this precisely):
{voice}

VISUAL SYSTEM (every shot must be one of these types):
{visuals}

FACTS AVAILABLE FOR THIS BEAT:
{facts}

FINANCIAL DATA AVAILABLE (use for charts):
{financials}

QUOTES AVAILABLE (verbatim only — never alter, never invent):
{quotes}

WHAT HAS ALREADY BEEN SAID (do not repeat any of it):
{previous}

Write the narration, then break it into shots. Each shot pairs 1-3 sentences of
narration with one visual. Shots run 4-9 seconds. Never cut faster than 4s.

Return JSON:
{{
  "narration": "<the full beat, as continuous prose, for the voice actor>",
  "shots": [
    {{
      "narration": "<the sentences spoken over this shot>",
      "visual_type": "<stat_reveal|chart|timeline|quote_card|photo|ai_still|document>",
      "visual_spec": {{
         "headline": "<text on screen, if any — max 6 words>",
         "value": "<the number for stat_reveal>",
         "caption": "<source caption, shown small>",
         "chart": {{"kind":"line|bar","title":"...","x":[...],"y":[...],"y_label":"..."}},
         "search_terms": "<for photo: what archival/stock image to find>",
         "prompt": "<for ai_still: image prompt. NO logos, NO text, NO real faces>"
      }},
      "emphasis": "<normal|hold>"
    }}
  ]
}}

Rules:
- Vary sentence length deliberately. Three short sentences in a row, then a long one.
- Every number spoken aloud should appear on screen in the same shot.
- Use "hold" emphasis on at most two shots: a beat of silence after a number lands.
- If a fact is marked needs_verification, either omit it or phrase it as attributed
  ("according to the 2009 annual report") rather than stated as bare fact."""


def write(cfg: Config, factbase: Any, hook_text: str) -> dict[str, Any]:
    import json as _json

    fb = factbase.to_dict() if hasattr(factbase, "to_dict") else factbase
    target_min = cfg.get_path("format.long.target_minutes", 12)
    wpm = cfg.get_path("format.long.words_per_minute", 150)
    total_words = int(target_min * wpm)

    voice_doc = read_channel_doc("voice.md")
    visual_doc = read_channel_doc("visual-identity.md")

    beats_cfg = cfg.get_path("structure.beats", [])
    out_beats: list[dict[str, Any]] = []
    said_so_far: list[str] = []

    for beat in beats_cfg:
        bid = beat["id"]
        words = int(total_words * beat["share"])
        seconds = int(words / wpm * 60)

        # The cold open is already written and scored — don't regenerate it.
        if bid == "cold_open" and hook_text:
            out_beats.append({
                "id": bid,
                "narration": hook_text,
                "shots": _shots_for_hook(hook_text, fb),
            })
            said_so_far.append(hook_text)
            continue

        relevant = [f for f in fb.get("facts", []) if f.get("beat") == bid] or fb.get("facts", [])[:10]

        result = llm.json_call(
            BEAT_PROMPT.format(
                beat_id=bid, company=fb["company"], angle=fb["angle"],
                intent=beat["intent"], words=words, seconds=seconds,
                voice=voice_doc[:2500], visuals=visual_doc[:2000],
                facts=_json.dumps(relevant[:18], indent=2),
                financials=_json.dumps(fb.get("financials", []), indent=2),
                quotes=_json.dumps(fb.get("quotes", []), indent=2),
                previous=" ".join(said_so_far)[-2500:] or "(nothing yet)",
            ),
            temperature=0.8,
        )
        result["id"] = bid
        out_beats.append(result)
        said_so_far.append(result.get("narration", ""))
        log.info("Beat %s written (%d shots)", bid, len(result.get("shots", [])))

    script = {
        "company": fb["company"],
        "angle": fb["angle"],
        "beats": out_beats,
        "full_narration": "\n\n".join(b.get("narration", "") for b in out_beats),
        "sources": fb.get("sources", []),
    }
    script["word_count"] = len(script["full_narration"].split())
    script["estimated_minutes"] = round(script["word_count"] / wpm, 1)
    log.info("Script: %d words, ~%s min", script["word_count"], script["estimated_minutes"])
    return script


def _shots_for_hook(hook: str, fb: dict) -> list[dict[str, Any]]:
    """The cold open gets a fixed, deliberately stark treatment."""
    sentences = [s.strip() for s in hook.replace("\n", " ").split(".") if s.strip()]
    shots = []
    for i, s in enumerate(sentences):
        shots.append({
            "narration": s + ".",
            "visual_type": "stat_reveal" if any(c.isdigit() for c in s) else "ai_still",
            "visual_spec": {
                "headline": "",
                "value": "".join(c for c in s if c.isdigit() or c in ".,$%") or "",
                "caption": "",
                "prompt": (
                    f"Dark, grainy, cinematic still evoking {fb.get('company','a company')}'s "
                    "world. Abstract and atmospheric. No text, no logos, no faces."
                ),
            },
            "emphasis": "hold" if i == len(sentences) - 1 else "normal",
        })
    return shots


def all_shots(script: dict) -> list[dict[str, Any]]:
    """Flatten to a single ordered shot list for the renderer."""
    flat = []
    for beat in script.get("beats", []):
        for shot in beat.get("shots", []):
            flat.append({**shot, "beat": beat.get("id")})
    for i, s in enumerate(flat):
        s["index"] = i
    return flat
