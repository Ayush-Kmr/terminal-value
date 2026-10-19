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
SHORT_TITLE_MAX = 50    # the Shorts feed shows about 40 characters
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
Other facts in the video (any number in a title must come from these):
{facts}

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
- Every word must be plain English a viewer understands at a glance. Never
  use jargon or nicknames from the reporting (say "tunnel-boring machines",
  never "moles"). Read the title back: it must make sense to someone who has
  not seen the news.

Vary the strategy across the eight: the claim, the specific number, the open
question, the contradiction, the consequence for an ordinary viewer.

Return a JSON array of objects:
  title         - the title
  strategy      - which strategy
  thumb_text    - 2-4 words for the thumbnail, ALWAYS in English capitals-friendly
                  Latin letters. It must name WHO or WHAT the story is about plus
                  the tension (e.g. "CHINA BLOCKS INDIA", "RBI CUTS RATES"), so it
                  makes sense on its own. Do not repeat the title or the figure.
  thumb_figure  - one real figure from the research for the thumbnail, at most
                  6 characters (e.g. "-94%", "$4B", "37"), or "" if none fits
  summary       - two plain sentences describing the video, in the title's
                  language. The first sentence must contain the main search term
                  (the name of the person, company, place or event): it is the
                  only part of the description viewers see before "more"."""


def _lint(title: str, maxlen: int = TITLE_MAX) -> tuple[int, list[str]]:
    issues: list[str] = []
    score = 0
    low = title.lower()
    if len(title) > maxlen:
        score -= 20
        issues.append(f"{len(title)} chars (max {maxlen})")
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


STOP = {"the", "and", "for", "with", "near", "new", "over", "into", "from", "its",
        "why", "how", "what", "after", "amid", "says", "news"}


def _names_subject(title: str, subject: str) -> bool:
    """The thing people search for must lead: a title that never says what
    it is about cannot rank or be understood in a feed."""
    head = title[:45].lower()
    words = [w for w in re.split(r"[^\w]+", subject.lower()) if len(w) > 2 and w not in STOP]
    return not words or any(w in head for w in words[:3])


def _unsupported_numbers(text: str, evidence: str) -> list[str]:
    """Figures in a title or thumbnail that the research never mentions."""
    plain = evidence.replace(",", "")
    return [n for n in re.findall(r"\d+(?:\.\d+)?", text.replace(",", ""))
            if n not in plain]


def titles(cfg: Config, company: str, angle: str, best_fact: str,
           language: str = "en", maxlen: int = TITLE_MAX,
           facts: list[str] | None = None) -> list[dict[str, Any]]:
    facts = [f for f in (facts or []) if f][:12]
    evidence = " ".join([company, angle, best_fact, *facts])
    options = llm.json_call(
        TITLE_PROMPT.format(
            company=company, angle=angle, fact=best_fact, maxlen=maxlen,
            facts="\n".join(f"- {f}" for f in facts) or "(none)",
            kind=cfg.get_path("format.kind", "documentary"),
            language=TITLE_LANGUAGE.get(language, TITLE_LANGUAGE["en"])),
        temperature=0.9,
    )
    out = []
    for o in options:
        if not isinstance(o, dict) or not o.get("title"):
            continue
        adj, issues = _lint(o["title"], maxlen)
        if not _names_subject(o["title"], company):
            adj -= 30
            issues.append("subject not named in the first 45 characters")
        made_up = _unsupported_numbers(o["title"], evidence)
        if made_up:
            adj -= 60
            issues.append(f"number not in research: {', '.join(made_up)}")
        o["thumb_text"] = latin_only(o.get("thumb_text", "")) or latin_only(company)
        o["thumb_figure"] = latin_only(str(o.get("thumb_figure", "")))[:6]
        if _unsupported_numbers(o["thumb_figure"], evidence):
            o["thumb_figure"] = ""
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
    """Three at most: the first three show above the title."""
    tags = []
    for k in keywords[:2]:
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
    """A handful of distinct terms. Tags barely affect ranking any more; the
    old "<topic> explained / news / hindi" variants only padded the list."""
    base = [company, *keywords[:6], "news explained"]
    if language == "hi":
        base += ["hindi news"]
    out, total, seen = [], 0, set()
    for t in base[:10]:
        t = str(t).strip()[:60]
        if not t or t.lower() in seen:
            continue
        if total + len(t) + 1 > 480:
            break
        seen.add(t.lower())
        out.append(t)
        total += len(t) + 1
    return out
