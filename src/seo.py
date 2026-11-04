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

TITLE_MAX = 90          # 70-100 characters rank better in search; the subject
                        # leads, so a truncated title still says what it is
SHORT_TITLE_MAX = 40    # the Shorts feed shows about 40: the subject goes first
CLICKBAIT = [
    "you won't believe", "shocking", "insane", "destroyed", "exposed", "exposes",
    "destroys", "slams",
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

WHAT PEOPLE TYPE INTO YOUTUBE SEARCH about this story (real search
suggestions, most searched first):
{searches}
Use the best-fitting phrase word for word, inside the first 40 characters, in
most of the titles. Leave out phrases that do not match what the video covers.

Rules:
- Between {minlen} and {maxlen} characters: longer titles carry more of the
  words people search for.
- Lead with the exact words people type into YouTube search for this story:
  the name of the person, company, place or event, plus the search word that
  goes with it ("Nvidia stock", "Boeing layoffs", "Fed rate cut",
  "US China trade deal"). Those words must be in the first 40 characters.
- Normal headline capitalisation: capitalise names and the main words. Search
  phrases arrive in lower case; never copy them that way.
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
question, the contradiction, the consequence for an ordinary viewer. Write five
"search" titles by the rules above, and three "curious" ones for the feed: a short
plain question or statement under 60 characters that names the subject and makes
people need the answer, usually with no number ("Can Meta Actually Survive This?",
"Why Buffets Suddenly Disappeared").

Return a JSON array of objects:
  title         - the title
  strategy      - which strategy
  style         - "search" or "curious"
  thumb_text    - 2-3 words for the thumbnail, in English (Latin letters). The
                  title already gives the facts; the thumbnail gives the STAKE or
                  the emotion, in words NOT used in the title (e.g. "BEAR MARKET",
                  "WHO PAYS?", "YOUR RATE RISES", "JOBS AT RISK").
  question      - one specific question to pin under the video, inviting viewers to
                  answer from their own life or opinion, in the title's language,
                  never financial advice (e.g. "Has a price rise changed what you buy?")
  thumb_entity  - who or where, 1-2 words, shown small as context ("CHINA",
                  "THE FED", "NVIDIA", "US-CHINA")
  thumb_figure  - one real figure from the research, at most 6 characters
                  ("-94%", "$4B", "37"), that is NOT already in the title, or ""
  summary       - two plain sentences describing the story, in the title's
                  language. The first sentence must START with the main search
                  term (the name of the person, company, place or event): it is
                  the only part of the description viewers see before "more".
                  Write it as a normal sentence with normal capitals ("Rising
                  costs in the US...", never "Rising cost of living usa is...").
                  Never start with "This video" or "The video"."""


def _lint(title: str, maxlen: int = TITLE_MAX) -> tuple[int, list[str]]:
    issues: list[str] = []
    score = 0
    low = title.lower()
    if len(title) >= maxlen * 2 // 3 and len(title) <= maxlen:
        score += 10                     # long enough to carry search words
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


SMALL_WORDS = {"a", "an", "and", "as", "at", "but", "by", "for", "in", "of", "on", "or",
               "the", "to", "vs", "with", "from", "is", "its", "over"}
ACRONYMS = {"ai", "ceo", "cfo", "ipo", "gdp", "cpi", "sec", "etf", "ev", "evs", "fbi", "irs",
            "ibm", "amd", "tsmc", "ups", "ftc", "doj", "nyse", "opec", "imf", "usa", "uk", "eu",
            "q1", "q2", "q3", "q4", "s&p", "at&t", "rbi", "idbi", "epfo", "ipl", "hdfc", "sbi",
            "tn", "tnpsc", "tneb", "tvk", "dmk", "lic", "upi", "emi", "gst", "pf", "lpg", "isro"}
SPELLED = {"openai": "OpenAI", "xai": "xAI", "spacex": "SpaceX", "tiktok": "TikTok",
           "youtube": "YouTube", "jpmorgan": "JPMorgan", "coreweave": "CoreWeave",
           "iphone": "iPhone", "chatgpt": "ChatGPT", "paypal": "PayPal", "ebay": "eBay",
           "bytedance": "ByteDance", "linkedin": "LinkedIn"}


def headline_case(title: str) -> str:
    """Search phrases come back in lower case and models copy them. A title that
    is mostly lower case gets headline capitals; a cased one is left alone."""
    words = title.split(" ")
    alpha = [w for w in words if w[:1].isalpha()]
    if not title.isascii() or not alpha:
        return title
    if sum(w[0].isupper() for w in alpha) * 2 >= len(alpha):
        return title[:1].upper() + title[1:]

    def fix(word: str, first: bool) -> str:
        if word.startswith("#"):
            return word
        m = re.match(r"^([^\w&]*)([\w&.'-]*)(.*)$", word)
        lead, core, tail = m[1], m[2], m[3]
        low = core.lower().rstrip("'s") if core.lower().endswith("'s") else core.lower()
        rest = core[len(low):] if core.lower().startswith(low) else ""
        if low in SPELLED:
            core = SPELLED[low] + rest
        elif low in ACRONYMS:
            core = low.upper() + rest
        elif core.islower() and (first or core not in SMALL_WORDS):
            core = core[:1].upper() + core[1:]
        return lead + core + tail

    return " ".join(fix(w, i == 0) for i, w in enumerate(words))


STOP = {"the", "and", "for", "with", "near", "new", "over", "into", "from", "its",
        "why", "how", "what", "after", "amid", "says", "news"}


def _names_subject(title: str, subject: str) -> bool:
    """The thing people search for must lead: a title that never says what
    it is about cannot rank or be understood in a feed."""
    head = title[:40].lower()
    words = [w for w in re.split(r"[^\w]+", subject.lower()) if len(w) > 2 and w not in STOP]
    return not words or any(w in head for w in words[:3])


def _unsupported_numbers(text: str, evidence: str) -> list[str]:
    """Figures in a title or thumbnail that the research never mentions."""
    plain = evidence.replace(",", "")
    return [n for n in re.findall(r"\d+(?:\.\d+)?", text.replace(",", ""))
            if n not in plain]


def titles(cfg: Config, company: str, angle: str, best_fact: str,
           language: str = "en", maxlen: int = TITLE_MAX,
           facts: list[str] | None = None, style: str = "search") -> list[dict[str, Any]]:
    from . import keywords

    facts = [f for f in (facts or []) if f][:12]
    evidence = " ".join(str(x) for x in [company, angle, best_fact, *facts])
    searches = keywords.for_story(company, language, context=evidence)
    options = llm.json_call(
        TITLE_PROMPT.format(
            company=company, angle=angle, fact=best_fact, maxlen=maxlen,
            facts="\n".join(f"- {f}" for f in facts) or "(none)",
            kind=cfg.get_path("format.kind", "documentary"), minlen=maxlen * 2 // 3,
            searches="\n".join(f"- {s}" for s in searches) or "(none found)",
            language=TITLE_LANGUAGE.get(language, TITLE_LANGUAGE["en"])),
        temperature=0.9,
    )
    out = []
    for o in options:
        if not isinstance(o, dict) or not o.get("title"):
            continue
        o["title"] = headline_case(str(o["title"]).strip())
        adj, issues = _lint(o["title"], maxlen)
        if not _names_subject(o["title"], company):
            adj -= 30
            issues.append("subject not named in the first 40 characters")
        made_up = _unsupported_numbers(o["title"], evidence)
        if made_up:
            adj -= 60
            issues.append(f"number not in research: {', '.join(made_up)}")
        o["thumb_text"] = latin_only(o.get("thumb_text", "")) or latin_only(company)
        o["thumb_entity"] = latin_only(str(o.get("thumb_entity", "")))[:16]
        o["thumb_figure"] = latin_only(str(o.get("thumb_figure", "")))[:6]
        if _unsupported_numbers(o["thumb_figure"], evidence):
            o["thumb_figure"] = ""
        out.append({**o, "lint_score": adj, "issues": issues})
    if not out:
        raise RuntimeError("Title generator returned nothing usable")
    out.sort(key=lambda t: (len(t["title"]) <= maxlen,     # the limit comes before everything
                            t.get("style") == style and t["lint_score"] > -20,
                            t["lint_score"], len(t["title"])), reverse=True)
    for t in out:
        t["searches"] = searches
        t["summary"] = re.sub(r"^(this|the) video (\w+ )?", "", t.get("summary", ""),
                              flags=re.I).strip()
        t["summary"] = _proper_case(t["summary"], company)
        t["summary"] = t["summary"][:1].upper() + t["summary"][1:]
    return out


def _proper_case(text: str, company: str) -> str:
    """Names copied from a lower-case search phrase get their capitals back:
    "Arista networks stock climbed", "cost of living usa" (2026-11-04 uploads)."""
    if company and not company.islower():
        text = re.sub(re.escape(company), company, text, flags=re.I)
    return re.sub(r"\b(usa|uk|ai|fed|rbi|ceo|ipo|gdp)\b",
                  lambda m: m[1].upper() if m[1] != "fed" else "Fed", text)


def chapters(cfg: Config, shots: list[dict]) -> str:
    """Chapter marks at beat boundaries. YouTube needs 00:00 as the first."""
    labels = {b["id"]: b.get("label", b["id"].replace("_", " ").capitalize())
              for b in cfg.get_path("structure.beats", [])}
    labels.update(cfg.get_path("labels.chapters", {}) or {})
    lines, t, seen = [], 0.0, set()
    for shot in shots:
        beat = shot.get("beat")
        if beat and beat not in seen:
            seen.add(beat)
            lines.append(f"{int(t)//60:02d}:{int(t)%60:02d} {labels.get(beat, beat)}")
        t += float(shot.get("duration", 5))
    # YouTube ignores chapters unless there are at least three, 10s+ apart.
    return "\n".join(lines) if len(lines) >= 3 else ""


def hashtags(cfg: Config, keywords: list[str], language: str, text: str = "") -> str:
    """Three, because only the first three show above the title: the subject, its
    category, and what the channel is. Specific tags reach people looking for that
    subject; "#viral" and the like only blur what the video is about."""
    from . import upload

    tags = []
    # Phrases with figures in them make unreadable hashtags (#Rbi550Repo).
    keywords = [k for k in keywords if not re.search(r"\d", k)] or keywords
    # The first short phrase, the subject; long ones make unreadable tags.
    for k in [k for k in keywords if len(k.split()) <= 3][:1] or keywords[:1]:
        word = re.sub(r"[^0-9A-Za-zऀ-ॿ\u0B80-\u0BFF]", "", k.title() if k.isascii() else k)
        if word and f"#{word}" not in tags:
            tags.append(f"#{word}")
    names = cfg.get_path("upload.hashtags", {}) or {}
    topic = next((names[n] for n in upload.topic_playlists(cfg, text, *keywords)
                  if n in names), cfg.get_path("upload.hashtag_default", "#business"))
    if topic not in tags:
        tags.append(topic)
    if language == "ta":                  # searched English tags come first
        tags.insert(1, "#tamilnews")
    else:
        tags.append({"hi": "#hindinews"}.get(language, "#businessnews"))
    return " ".join(tags)


def description(cfg: Config, summary: str, chapter_text: str, sources: list[dict],
                keywords: list[str], language: str = "en", facts: list[str] | None = None,
                credits: str = "", tags_extra: list[str] | None = None,
                topic_text: str = "", music: str = "") -> str:
    """Built from parts rather than generated: the sources and the disclosure
    must be exact, and a model asked to copy them verbatim sometimes does not.
    Order: summary, chapters, key facts, call to action, sources, credits,
    disclosure, then the hashtags last."""
    heads = cfg.get_path("labels.description", {}) or {}
    parts = [summary.strip()]
    if chapter_text:
        parts.append(heads.get("chapters", "CHAPTERS") + "\n" + chapter_text)
    facts = [f.strip() for f in (facts or []) if f and f.strip()]
    if facts:
        # The facts the video is built on: real words for search to match,
        # and the viewer can check them against the sources below.
        label = {"ta": "முக்கிய தகவல்கள்", "hi": "मुख्य तथ्य"}.get(language, "KEY FACTS")
        parts.append(label + "\n" + "\n".join(f"• {f}" for f in facts[:7]))
    if cfg.get_path("channel.cta"):
        links = [str(x) for x in cfg.get_path("channel.links", []) or [] if x]
        parts.append("\n".join([cfg.get_path("channel.cta").strip(), *links]))

    lines = []
    for s in sources[:12]:
        title, url = s.get("title", "Source"), s.get("url", "")
        # News-aggregator redirect links run to hundreds of characters.
        lines.append(f"- {title}" + (f" — {url}" if url and len(url) < 120 else ""))
    if lines:
        parts.append(heads.get("sources", "SOURCES") + "\n" + "\n".join(lines))
    if credits:
        parts.append("IMAGE CREDITS\n" + credits)

    if music:
        parts.append(music)
    parts.append(cfg.get_path("channel.disclosure", "").strip())
    tail = hashtags(cfg, keywords + list(tags_extra or []), language, f"{summary} {topic_text}")
    body = "\n\n".join(p for p in parts if p)
    # Trim the body, never the hashtags: they must close the description.
    return body[:4880 - len(tail)] + "\n\n" + tail


def tags(company: str, keywords: list[str], language: str = "en",
         extra: list[str] | None = None) -> list[str]:
    """A handful of distinct terms. Tags barely affect ranking any more; the
    old "<topic> explained / news / hindi" variants only padded the list.
    `extra` (search phrases in the channel's own script) goes first."""
    base = [*(extra or [])[:10], company, *keywords[:6]]
    if language != "ta":
        base.append("news explained")
    if language == "hi":
        base += ["hindi news"]
    elif language == "ta":
        base += ["tamil news", "business news tamil", "தமிழ் செய்திகள்"]
    out, total, seen = [], 0, set()
    for t in base[:14]:
        t = str(t).strip()[:60]
        if not t or t.lower() in seen:
            continue
        if total + len(t) + 1 > 480:
            break
        seen.add(t.lower())
        out.append(t)
        total += len(t) + 1
    return out


DELIVERS_PROMPT = """A viewer clicks a video because of its title and leaves if
the video is about something else. Below is what the video actually says, then
candidate titles. For each title, answer whether the narration delivers what
the title promises: the same subject, and an answer to the question it asks.
A title about grocery prices is NOT delivered by narration mostly about
childcare or health insurance, even if both are "cost of living".

NARRATION:
{narration}

TITLES:
{titles}

Return JSON: {{"delivers": [true or false for each title, in order]}}"""


def pick_delivered(candidates: list[str], narration: str) -> str:
    """The first title the script really delivers (review of 2026-11-04: a
    queued groceries title went out on a script about other household costs).
    One model call checks all of them; if it fails, the first title stands."""
    titles = [t for t in dict.fromkeys(candidates) if t]
    if len(titles) < 2 or not narration.strip():
        return titles[0] if titles else ""
    try:
        got = llm.json_call(DELIVERS_PROMPT.format(
            narration=narration[:6000],
            titles="\n".join(f"{n}. {t}" for n, t in enumerate(titles, 1))), temperature=0.0)
        marks = list((got or {}).get("delivers") or [])
    except Exception as exc:  # noqa: BLE001
        log.warning("Title check unavailable (%s)", exc)
        return titles[0]
    for title, ok in zip(titles, marks):
        if ok is True or str(ok).lower() == "true":
            if title != titles[0]:
                log.warning("Title %r does not match the script; using %r", titles[0], title)
            return title
    log.warning("No title option matched the script; keeping %r", titles[0])
    return titles[0]
