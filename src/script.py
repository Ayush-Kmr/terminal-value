"""Script writing: beat by beat, with a shot list attached to every sentence.

Written one beat at a time rather than in a single pass. A 12-minute script
generated in one shot drifts into uniform sentence rhythm, which is exactly the
texture that reads as machine-made.
"""
from __future__ import annotations

import logging
from typing import Any

from . import llm
from .config import Config, language_rules, read_channel_doc

log = logging.getLogger(__name__)

BEAT_PROMPT = """Write section "{beat_id}" of a {kind}.

SUBJECT: {company}
THE QUESTION THIS VIDEO ANSWERS: {angle}

{language}

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
narration with one visual. Shots run {shot_min:g}-{shot_max:g} seconds. Never cut faster than {shot_min:g}s.

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
         "quote": "<quote_card only: the supplied quote, verbatim, max 25 words>",
         "chart": {{"kind":"line|bar","title":"...","x":[...],"y":[...],"y_label":"..."}},
         "events": [{{"year":"<date or year>","event":"<max 8 words>","pivotal":false}}],
         "search_terms": "<ONLY when the shot must show one REAL named person, place or thing: just its name, 1-4 words, as on Wikipedia>",
         "stock_terms": "<for every other photo, a scene with movement: 2-4 plain words with NO proper names, e.g. 'stock exchange trading floor'>",
         "prompt": "<for ai_still: image prompt. NO logos, NO text, NO real faces>"
      }},
      "emphasis": "<normal|hold>"
    }}
  ]
}}

Rules:
- Vary sentence length deliberately. Three short sentences in a row, then a long one.
- Every number spoken aloud should appear on screen in the same shot.
- stat_reveal is for an amount, a percentage or a count. Never a date or a
  year: dates go in a timeline or a headline.
- Use "hold" emphasis on at most two shots: a beat of silence after a number lands.
- If a fact is marked needs_verification, either omit it or phrase it as attributed
  ("according to Reuters", "the company says") rather than stated as bare fact.
- Use ONLY the facts, numbers and quotes supplied above. Do not add events,
  figures or names from memory: for current events your memory is out of date.
- A chart needs at least three real data points from FINANCIAL DATA. A timeline
  needs 3-6 "events". Use quote_card only for a supplied verbatim quote that is
  in English: put the quote in "quote" and "Name, role, date" in "caption". If you have neither data nor a quote, use photo or ai_still.
- Keep the picture moving. Prefer photo with "stock_terms" over ai_still: it
  brings real moving footage (traders, factories, cargo ships, city streets, chips
  on a production line). Use ai_still only for what no footage can show.
- Never use the same visual_type three shots in a row, and make at least half of
  the shots photo. Cards land hardest between pieces of footage.
- Every photo shot needs either "search_terms" (one real, named subject) or
  "stock_terms" (a generic scene), not both, never a sentence. Also give every photo
  shot a "prompt" as a fallback illustration."""


def write(cfg: Config, factbase: Any, hook_text: str, language: str = "en",
          beats: list[dict] | None = None, total_words: int | None = None) -> dict[str, Any]:
    import json as _json

    fb = factbase.to_dict() if hasattr(factbase, "to_dict") else factbase
    target_min = cfg.get_path("format.long.target_minutes", 12)
    wpm = cfg.get_path("format.long.words_per_minute", 150)
    total_words = total_words or int(target_min * wpm)

    voice_doc = read_channel_doc("voice.md")
    visual_doc = read_channel_doc("visual-identity.md")

    beats_cfg = beats or cfg.get_path("structure.beats", [])
    out_beats: list[dict[str, Any]] = []
    said_so_far: list[str] = []

    for beat in beats_cfg:
        bid = beat["id"]
        words = int(total_words * beat["share"])
        seconds = int(words / wpm * 60)

        # The cold open is already written and scored — don't regenerate it.
        if beat is beats_cfg[0] and hook_text:
            out_beats.append({
                "id": bid,
                "narration": hook_text,
                "shots": _shots_for_hook(hook_text, fb),
            })
            said_so_far.append(hook_text)
            continue

        relevant = [f for f in fb.get("facts", []) if f.get("beat") == bid] or fb.get("facts", [])[:10]
        # Compact JSON and trimmed docs: on Groq's free tier the prompt and the
        # answer share 8,000 tokens a minute.
        prompt = BEAT_PROMPT.format(
            beat_id=bid, company=fb["company"], angle=fb["angle"],
            shot_min=cfg.get_path("visuals.shot_seconds", [4, 9])[0],
            shot_max=cfg.get_path("visuals.shot_seconds", [4, 9])[1],
            kind=cfg.get_path("format.kind", "documentary"),
            language=language_rules(language),
            intent=beat["intent"], words=words, seconds=seconds,
            voice=voice_doc[:2200], visuals=visual_doc[:1200],
            facts=_json.dumps(relevant[:16], ensure_ascii=False),
            financials=_json.dumps(fb.get("financials", [])[:12], ensure_ascii=False),
            quotes=_json.dumps(fb.get("quotes", [])[:6], ensure_ascii=False),
            previous=" ".join(said_so_far)[-1500:] or "(nothing yet)",
        )
        note = str(cfg.get_path("script.local_note", "") or "").strip()
        if note:
            prompt += f"\n- For this channel's viewers: {note}"
        result = llm.json_call(prompt, temperature=0.8)
        if not isinstance(result, dict) or not result.get("shots"):
            raise RuntimeError(f"Beat {bid} came back without shots")
        # Models write short (Groq, used when Gemini's quota runs out, most of
        # all): a beat well under its length is asked for again, up to twice.
        result = _lengthen(prompt, result, words, bid, tries=2)
        result["id"] = bid
        result["_prompt"], result["_words"] = prompt, words
        out_beats.append(result)
        said_so_far.append(result.get("narration", ""))
        log.info("Beat %s written (%d shots)", bid, len(result.get("shots", [])))

    # Still under format.long.min_minutes as a whole (seen: 4.7 min on a
    # 6-minute target): the beats furthest under their length get one more go.
    floor = float(cfg.get_path("format.long.min_minutes", 0)) * wpm
    if not beats and floor:
        total = sum(_count(b) for b in out_beats)
        for b in sorted((b for b in out_beats if b.get("_prompt")),
                        key=lambda b: _count(b) - b["_words"])[:3]:
            if total >= floor:
                break
            before = _count(b)
            better = _lengthen(b["_prompt"], b, b["_words"], b["id"], tries=1, force=True)
            if better is not b:
                better["id"] = b["id"]
                out_beats[out_beats.index(b)] = better
                total += _count(better) - before
        log.info("Script length after top-up: %d words (floor %d)", total, floor)
    for b in out_beats:
        b.pop("_prompt", None)
        b.pop("_words", None)

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


def _count(beat: dict) -> int:
    return len(" ".join(s.get("narration", "") for s in beat.get("shots", [])).split())


def _lengthen(prompt: str, result: dict, words: int, bid: str, tries: int = 2,
              force: bool = False) -> dict:
    """Ask again while a beat is under 90% of its length; keep the longest
    draft. `force` asks even when the beat is near its length."""
    best, got = result, _count(result)
    for attempt in range(tries):
        if got >= words * 0.9 and not force:
            break
        log.info("Beat %s at %d/%d words; asking for the full length (%d)",
                 bid, got, words, attempt + 1)
        longer = llm.json_call(
            prompt + f"\n\nIMPORTANT: a previous draft of this beat was only {got} "
            f"words. It must be at least {words} words. Add depth from the facts above "
            "(context, numbers, who said what, what it means for viewers), not padding.",
            temperature=0.8)
        if isinstance(longer, dict) and longer.get("shots") and _count(longer) > got:
            best, got = longer, _count(longer)
        force = False
    return best


def _figure(text: str) -> str:
    """The headline figure of a sentence, e.g. "$4.2", "37%", "1,400"."""
    import re

    figures = re.findall(r"[$₹€£]?\d[\d,]*(?:\.\d+)?%?", text or "")
    # Prefer the figure carrying a unit; a bare "7" is usually a date.
    figures.sort(key=lambda f: (f[0] in "$₹€£" or f.endswith("%"), len(f)), reverse=True)
    return figures[0].rstrip(",") if figures else ""


def _no_blank_numbers(shot: dict) -> None:
    """A stat card the writer left without its number would print a bare dash
    (seen on a Groq-written Short, 2026-11-04). Take the figure from the
    narration, or show the scene instead."""
    spec = shot.get("visual_spec") or {}
    if shot.get("visual_type") != "stat_reveal" or str(spec.get("value", "")).strip():
        return
    figure = _figure(shot.get("narration", ""))
    if figure:
        spec["value"] = figure
    else:
        shot["visual_type"] = "photo" if spec.get("stock_terms") else "ai_still"
    shot["visual_spec"] = spec


def _shots_for_hook(hook: str, fb: dict) -> list[dict[str, Any]]:
    """The cold open gets a fixed, deliberately stark treatment."""
    import re

    # Split on sentence ends (including the Hindi danda), keeping the mark.
    sentences = [s.strip() for s in re.split(r"(?<=[.।?!])\s+", hook.replace("\n", " "))
                 if s.strip()]
    shots = []
    for i, s in enumerate(sentences):
        figure = _figure(s)
        shots.append({
            "narration": s,
            "visual_type": "stat_reveal" if figure else "ai_still",
            "visual_spec": {
                "headline": "",
                "value": figure,
                "caption": "",
                "prompt": (
                    f"Wide establishing shot of the real-world setting of {fb.get('company','the story')}: "
                    "the place, the industry or the objects involved. No text, no logos, no faces."
                ),
            },
            "emphasis": "hold" if i == len(sentences) - 1 else "normal",
        })
    return shots


def pieces(text: str, max_words: int) -> list[str]:
    """Narration cut at sentence ends, then at clause breaks, so no shot runs long."""
    import re

    out: list[str] = []
    for sentence in re.split(r"(?<=[.?!।])\s+", text.strip()):
        while len(sentence.split()) > max_words:
            words = sentence.split()
            breaks = [i for i, w in enumerate(words[:-3], 1) if i >= 4 and (
                w[-1] in ",;:—" or words[i].lower() in ("and", "but", "while", "which", "after",
                                                         "because", "as", "meaning"))]
            if not breaks:
                break
            cut = min(breaks, key=lambda i: abs(i - max_words * 0.7))
            out.append(" ".join(words[:cut]))
            sentence = " ".join(words[cut:])
        out.append(sentence)
    merged: list[str] = []
    for piece in out:
        if merged and len(piece.split()) < 4:
            merged[-1] += " " + piece
        elif piece:
            merged.append(piece)
    return merged


def all_shots(script: dict, max_words: int = 0) -> list[dict[str, Any]]:
    """Flatten to a single ordered shot list for the renderer. With `max_words`,
    long shots are split, each new piece getting its own moving picture."""
    import copy

    shots = [{**s, "beat": b.get("id")} for b in script.get("beats", []) for s in b.get("shots", [])]
    scenes = [s["visual_spec"]["stock_terms"] for s in shots
              if (s.get("visual_spec") or {}).get("stock_terms")]
    flat: list[dict[str, Any]] = []
    for shot in shots:
        parts = pieces(shot.get("narration") or "", max_words) if max_words else []
        if len(parts) < 2:
            flat.append(shot)
            continue
        for n, line in enumerate(parts):
            piece = copy.deepcopy(shot)
            piece["narration"] = line
            if n < len(parts) - 1:
                piece["emphasis"] = "normal"
            if n and piece.get("visual_type") not in ("photo", "ai_still") and scenes:
                piece["visual_type"] = "photo"
                piece["visual_spec"] = {"stock_terms": scenes[(len(flat)) % len(scenes)],
                                        "prompt": (shot.get("visual_spec") or {}).get("prompt", "")}
            elif n and piece.get("visual_type") in ("photo", "ai_still") and scenes:
                piece["visual_spec"] = {**piece["visual_spec"], "search_terms": "",
                                        "stock_terms": scenes[(len(flat)) % len(scenes)]}
            flat.append(piece)
    for i, s in enumerate(flat):
        _no_blank_numbers(s)
        s["index"] = i
    return flat
