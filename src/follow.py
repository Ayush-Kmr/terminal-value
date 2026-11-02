"""A second channel that publishes the main channel's videos in its own
language: the same stories, the same pictures, the same day.

The main channel's run leaves each video's shot list (shots.json), research
and pictures in its build folder. This module takes those folders, translates
the narration shot by shot (so every line still lands on its picture), speaks
it with the channel's own voice, renders the video again and uploads it to the
channel the current token belongs to.

    CHANNEL=ta python main.py --follow downloaded-output/
"""
from __future__ import annotations

import json
import logging
import re
import shutil
from datetime import datetime
from pathlib import Path

from . import assets, captions, engage, gate, llm, render, seo, shorts, state, thumbnail, upload, voice
from .config import ROOT, Config, language_rules, portrait

log = logging.getLogger(__name__)

LANGUAGE_NAMES = {"ta": "Tamil", "hi": "Hindi", "kn": "Kannada"}
BATCH = 18              # lines per translation call: keeps Groq under its limits

TRANSLATE_PROMPT = """Translate the narration of a news explainer video from English to {name}.

Video: {title}

{rules}

Translate line by line. Each line is spoken over its own picture, so keep every
line's meaning in that line: do not move facts between lines, merge or drop
lines, or add anything. Keep it natural and spoken, as a {name} news presenter
would say it, at about the same length as the English.

Lines:
{lines}

Return JSON: {{"lines": ["<line 1 in {name}>", ...]}} with exactly {n} strings,
in the same order."""

PACKAGE_PROMPT = """A YouTube {kind} from an English news channel is being published again,
narrated in {name}, on the channel's {name} edition.

English title: {title}
English summary: {summary}
Facts in the video (any number in the title must come from these):
{facts}

WHAT {name} VIEWERS SEARCH for about this story (for meaning only; do not copy
any English from it):
{searches}

Write everything in {name} script only: no English letters at all, not even in
names, and spell every name the way {name} speakers say it. Digits, % and $ are fine.
The title is {minlen} to {maxlen} characters. No exclamation marks, no clickbait.

Return JSON:
{{"title": "...",
  "subject": "<the story's subject in {name} script, 1-3 words>",
  "search_head": "<the one phrase from the search list above that best fits this story,
                  copied word for word in English letters; the only English allowed>",
  "summary": "<two plain sentences in {name}; the first STARTS with the subject>",
  "facts": ["<the {nfacts} most important facts above, each one short sentence in {name}>"],
  "thumb_text": "<2-3 words in {name} for the thumbnail: the stake or emotion,
                 not words from the title (e.g. 'உங்கள் வேலை ஆபத்தில்')>",
  "thumb_entity": "<who or where, 1-2 words in {name}>",
  "question": "<one specific question for viewers to answer in the comments, in {name}>",
  "search": ["<8 short phrases {name} viewers would type into YouTube search to find
             this story, in {name} script only>"]}}"""

ADAPT_PROMPT = """These lines from a US business video explain what a story means for an
American household. The video is being remade for Tamil viewers in India. Rewrite
only what needs it, so the lines speak to a family in Tamil Nadu: a 401(k) becomes
savings or a provident fund, a mortgage becomes a home loan EMI, gas becomes petrol,
a paycheck becomes a salary, a grocery bill stays a grocery bill.

Rules:
- The story's facts stay exactly as they are. Everything reported about the US stays
  about the US.
- Add no figures, dates, names, rupee amounts or claims about India. Do not say that
  anything has happened or will happen in India: say only what the viewer might watch for.
- Never say what to buy, sell or hold.
- Keep exactly one line for each line, in order, in simple English.

Video: {title}

Lines:
{lines}

Return JSON: {{"lines": ["<line 1>", ...]}} with exactly {n} strings."""

LOCAL_BEATS = {"stakes", "legacy", "whats_next", "short"}
US_WORDS = re.compile(r"401\(k\)|\bmortgages?\b|\bpaychecks?\b|\bcar loans?\b|\bretirement\b"
                      r"|\bgas prices?\b|\bAmericans?\b|\bfamil(y|ies)\b|\bhouseholds?\b", re.I)


def adapt(cfg: Config, title: str, shots: list[dict]) -> int:
    """Rewrites viewer-impact lines for the channel's audience, before translation.
    A rewrite that brings in any new figure is refused; failure keeps the original."""
    if not cfg.get_path("follow.adapt_to"):
        return 0
    chosen = [s for s in shots if s.get("beat") in LOCAL_BEATS
              and US_WORDS.search(s.get("narration") or "")]
    changed = 0
    for start in range(0, len(chosen), BATCH):
        part = chosen[start:start + BATCH]
        try:
            got = llm.json_call(ADAPT_PROMPT.format(
                title=title, n=len(part),
                lines="\n".join(f"{i + 1}. {s['narration']}" for i, s in enumerate(part))),
                temperature=0.3)
        except Exception as exc:  # noqa: BLE001
            log.warning("Adapting lines skipped: %s", str(exc)[:160])
            continue
        got = got.get("lines") if isinstance(got, dict) else got
        if not isinstance(got, list) or len(got) != len(part):
            continue
        for shot, line in zip(part, got):
            old = shot["narration"]
            if (isinstance(line, str) and line.strip()
                    and set(re.findall(r"\d+(?:\.\d+)?", line)) <= set(re.findall(r"\d+(?:\.\d+)?", old))
                    and "₹" not in line and "rupee" not in line.lower()):
                shot["narration"] = line.strip()
                changed += line.strip() != old
    log.info("Adapted %d lines for this channel's viewers", changed)
    return changed


SCREEN_NOTE = """These lines are short text printed on the picture of the video, not speech.
Keep each one as short as the English. Digits, %, $ and dates as digits stay; every
word, name, month and unit goes into Tamil script ("$4 billion" -> "4 பில்லியன் டாலர்",
"Reuters" -> "ராய்ட்டர்ஸ்", "Mar 2026" -> "மார்ச் 2026")."""


def builds(root: Path) -> list[Path]:
    """Every finished build folder under `root`, Shorts first: the long video
    takes far longer to voice, and if it fails the Shorts are already out."""
    found = [p.parent for p in root.rglob("shots.json")
             if (p.parent / "metadata.json").exists()]
    return sorted(found, key=lambda d: ("short" not in d.name, d.name))


def translate(cfg: Config, title: str, lines: list[str], language: str,
              note: str = "", latin: str = r"[A-Za-z]{2,}") -> list[str]:
    name = LANGUAGE_NAMES.get(language, language)
    out: list[str] = []
    for start in range(0, len(lines), BATCH):
        part = lines[start:start + BATCH]
        listing = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(part))
        for attempt in range(3):
            got = llm.json_call(TRANSLATE_PROMPT.format(
                name=name, title=title, rules=f"{language_rules(language)}\n{note}",
                lines=listing, n=len(part)), temperature=0.3)
            got = got.get("lines") if isinstance(got, dict) else got
            # Latin letters are read badly by the Tamil voice ("பinflation").
            if isinstance(got, list) and len(got) == len(part) \
                    and all(isinstance(t, str) and t.strip() for t in got) \
                    and (attempt == 2 or not any(re.search(latin, t) for t in got)):
                out += [t.strip() for t in got]
                break
            log.warning("Translation of lines %d-%d came back wrong (attempt %d)",
                        start + 1, start + len(part), attempt + 1)
        else:
            raise RuntimeError(f"Could not translate lines {start + 1}-{start + len(part)}")
    return out


def _drawable(text: str) -> str:
    """The Tamil font has no $ sign: "$4.2 பில்லியன்" -> "4.2 பில்லியன் டாலர்"."""
    def money(m: re.Match) -> str:
        return f"{m[1]}{m[2] or ''} டாலர்"
    text = re.sub(r"\$\s?(\d[\d,.]*)(\s?(?:மில்லியன்|பில்லியன்|டிரில்லியன்|ஆயிரம்))?", money, str(text))
    text = re.sub(r"(டாலர்)(\s+\1)+", r"\1", text)
    return text.replace("·", ":").replace("$", "").replace("&", " மற்றும் ")


def _screen_slots(spec: dict) -> list[tuple]:
    """Every piece of text a card prints, as (container, key)."""
    slots = [(spec, k) for k in ("headline", "value", "caption", "quote") if spec.get(k)]
    chart = spec.get("chart")
    if isinstance(chart, dict):
        slots += [(chart, k) for k in ("title", "y_label") if chart.get(k)]
        slots += [(chart["x"], i) for i in range(len(chart.get("x") or []))]
    for ev in spec.get("events") or []:
        if isinstance(ev, dict):
            slots += [(ev, k) for k in ("year", "event") if ev.get(k)]
    return slots


def translate_screen(cfg: Config, title: str, shots: list[dict], language: str) -> None:
    """Everything printed on a card goes into the channel's language. Anything
    still in English afterwards is dropped from captions, where it can go."""
    every = [(c, k) for s in shots for c, k in _screen_slots(s.get("visual_spec") or {})]
    slots = [(c, k) for c, k in every if re.search(r"[A-Za-z]", str(c[k]))]
    texts = list(dict.fromkeys(str(c[k]) for c, k in slots))
    done = {} if not texts else dict(zip(texts, translate(cfg, title, texts, language, SCREEN_NOTE, r"[A-Za-z]")))
    for c, k in every:
        if str(c[k]) in done:
            continue
        c[k] = _drawable(c[k])
    stubborn = [t for t, tr in done.items() if re.search(r"[A-Za-z]", tr)]
    for text in stubborn:           # one at a time, where the model has only this to do
        done[text] = translate(cfg, title, [text], language,
                               SCREEN_NOTE + "\nSpell every English word in Tamil letters.",
                               r"[A-Za-z]")[0]
    left = 0
    for c, k in slots:
        line = _drawable(done[str(c[k])])
        if re.search(r"[A-Za-z]", line):
            left += 1
            # Any English left would print as empty boxes, so it comes off the card.
            line = re.sub(r"\s{2,}", " ", re.sub(r"[A-Za-z][A-Za-z.&'’-]*", "", line)).strip(" -:,")
        c[k] = line
    if left:
        log.warning("%d on-screen text(s) had English removed", left)


def _load_shots(src: Path, workdir: Path) -> list[dict]:
    """The main channel's shot list, with its pictures copied in. Cards are
    drawn again (their animation frames are not shipped between jobs)."""
    shots = json.loads((src / "shots.json").read_text(encoding="utf-8"))
    for shot in shots:
        for key in ("audio", "words", "spoken_duration"):
            shot.pop(key, None)
        rel = shot.get("asset")
        if not rel:
            continue
        found = src / rel
        if found.exists():
            dest = workdir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(found, dest)
            shot["asset"] = str(dest)
        else:
            shot["asset"] = None
    return shots


def _thumbnail(cfg: Config, meta: dict, pkg: dict, title: str, shots: list[dict],
               src: Path, workdir: Path) -> Path:
    """The channel's own thumbnail: its words over the same picture, with the
    English video's figure and direction. Falls back to the English one."""
    en = (meta.get("title_options") or [{}])[0]
    figure = str(en.get("thumb_figure", ""))
    figure = "" if re.search(r"[A-Za-z]", figure) else figure.replace("$", "")
    stills = [s for s in shots if s.get("visual_type") in {"photo", "ai_still"}
              and s.get("asset") and Path(s["asset"]).suffix.lower() != ".mp4"]
    stills.sort(key=lambda s: (s.get("visual_type") == "photo"
                               and bool((s.get("visual_spec") or {}).get("search_terms")),
                               s.get("visual_type") != "photo"))
    try:
        return thumbnail.generate(cfg, {
            "title": title, "thumb_words": str(pkg.get("thumb_text") or en.get("thumb_text", "")),
            "thumb_entity": str(cfg.get_path("upload.series_playlist")
                                if meta.get("series") else pkg.get("thumb_entity") or ""),
            "thumb_figure": figure,
            "direction": thumbnail.direction(figure, meta.get("title", "")),
        }, Path(stills[0]["asset"]) if stills else None, workdir / "thumbnail.jpg")
    except Exception as exc:  # noqa: BLE001
        log.warning("Own thumbnail failed (%s); using the main channel's", exc)
        return src / "thumbnail.jpg"


def produce(cfg: Config, src: Path, workdir: Path) -> dict:
    language = cfg.get_path("channel.language", "en")
    meta = json.loads((src / "metadata.json").read_text(encoding="utf-8"))
    research = json.loads((src / "research.json").read_text(encoding="utf-8")) \
        if (src / "research.json").exists() else {}
    is_short = "path" in meta
    company = meta.get("company", "")
    en_title = meta["title"].replace("#shorts", "").strip()

    shots = _load_shots(src, workdir)
    adapt(cfg, en_title, shots)
    spoken = [s for s in shots if (s.get("narration") or "").strip()]
    for shot, line in zip(spoken, translate(cfg, en_title,
                                            [s["narration"] for s in spoken], language)):
        shot["narration"] = line
    (workdir / "script.json").write_text(
        json.dumps(shots, indent=2, ensure_ascii=False), encoding="utf-8")

    translate_screen(cfg, en_title, shots, language)
    subject = translate(cfg, en_title, [company], language)[0]

    frame = portrait(cfg) if is_short else cfg
    assets.redraw_cards(frame, shots, workdir / "assets", subject)
    assets.gather(frame, shots, workdir, subject)      # anything that did not copy
    shots = voice.narrate_shots(cfg, shots, workdir, meta.get("region"), language)
    clips = render.build_shot_clips(frame, shots, workdir)
    video = render.concat_with_audio(frame, clips, shots, workdir,
                                     render.pick_music(cfg, company))

    facts = [str(f.get("claim", "")) for f in research.get("facts", [])]
    name = LANGUAGE_NAMES.get(language, language)
    maxlen = seo.SHORT_TITLE_MAX if is_short else seo.TITLE_MAX
    nfacts = 4 if is_short else 7
    from . import keywords
    searches = keywords.for_story(company, language)
    # Tamil viewers mostly search in English plus "tamil", so those phrases lead
    # the tags and hashtags. Everything the viewer reads stays in Tamil.
    core = keywords._core(company)
    english = list(dict.fromkeys([s for s in searches if s.isascii()]
                                 + [f"{core} tamil", f"{core} news tamil"]))
    hashhead = next((s for s in english if len(s.split()) <= 3), " ".join(core.split()[:2]) + " tamil")
    pkg = llm.json_call(PACKAGE_PROMPT.format(
        searches="\n".join(f"- {s}" for s in searches) or "(none found)",
        kind="Short" if is_short else "video", name=name, title=en_title,
        summary=(meta.get("title_options") or [{}])[0].get("summary", "")
        or research.get("summary", ""),
        facts="\n".join(f"- {f}" for f in facts[:25]) or "-", maxlen=maxlen,
        minlen=maxlen * 2 // 3, nfacts=nfacts),
        temperature=0.5)
    title = _drawable(str(pkg.get("title") or subject).strip())[:maxlen + 10]
    # The title opens with what Tamil viewers type into search; the headline on
    # the picture stays Tamil only, and the Tamil font has no English letters.
    head = str(pkg.get("search_head") or "").strip()
    head = next((s for s in english if s.lower() == head.lower() and len(s) <= 40), hashhead)
    full_title = f"{seo.headline_case(head)} | {title}"[:100]
    summary = str(pkg.get("summary") or "").strip()
    local_facts = [str(f) for f in pkg.get("facts") or [] if f][:nfacts]
    # Real search phrases first, then the model's {name}-script ones.
    native = lambda t: not re.search(r"[A-Za-z]", str(t))      # nothing English anywhere
    search = [str(t) for t in pkg.get("search") or [] if t and native(t)][:8]
    sources = [dict(s) for s in research.get("sources", [])[:12]]
    if sources:
        names = translate(cfg, en_title, [str(s.get("title", "")) for s in sources], language,
                          SCREEN_NOTE)
        for s, n in zip(sources, names):
            s["title"] = n
    keywords = [hashhead]
    tamil_subject = str(pkg.get("subject") or subject)
    topic_text = " ".join([en_title, *meta.get("tags", [])])

    out = {"company": company, "angle": meta.get("angle", ""), "region": meta.get("region"),
           "language": language, "warnings": meta.get("warnings", []),
           "category_id": meta.get("category_id"), "series": bool(meta.get("series")),
           "question": str(pkg.get("question") or ""),
           "parent_url": meta.get("parent_url", ""),
           "tags": seo.tags(tamil_subject, [], language, extra=[*english[:6], *search[:4]]),
           "source": src.name,
           "captions": str(captions.srt(shots, workdir / "captions.srt"))}
    if is_short:
        short = shorts.standalone(cfg, video, shots, workdir, title, title, language)
        out.update(short, kind="short", title=full_title, description=seo.description(
            cfg, summary, "", sources[:6], keywords, language,
            facts=local_facts, tags_extra=search, topic_text=topic_text,
            music=render.music_credit(cfg, company)))
    else:
        out.update(kind="long", title=full_title, video=str(video),
                   description=seo.description(cfg, summary, seo.chapters(cfg, shots),
                                               sources, keywords, language, facts=local_facts,
                                               tags_extra=search, topic_text=topic_text,
                                               music=render.music_credit(cfg, company)),
                   thumbnail=str(_thumbnail(cfg, meta, pkg, title, shots, src, workdir)),
                   runtime=f"{int(sum(s['duration'] for s in shots) // 60)}:"
                           f"{int(sum(s['duration'] for s in shots) % 60):02d}")
    (workdir / "metadata.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    return out


def run(cfg: Config, root: Path, publish: str = "slot", no_upload: bool = False) -> int:
    folders = builds(root)
    if not folders:
        log.info("Nothing to follow under %s", root)
        return 0
    st = state.load()
    done_before = {e.get("source") for e in st.get("episodes", [])}
    short_slots = upload.slots(cfg, "short", 4)
    n_short = 0
    done, failures = [], []
    skip_regions = {r.upper() for r in cfg.get_path("follow.skip_regions", []) or []}
    max_shorts = int(cfg.get_path("follow.max_shorts", 99))
    shorts_taken = 0

    for src in folders:
        if src.name in done_before:
            log.info("Already published %s; skipping", src.name)
            continue
        # Only the stories that matter to this channel's viewers.
        src_meta = json.loads((src / "metadata.json").read_text(encoding="utf-8"))
        if str(src_meta.get("region") or "").upper() in skip_regions:
            log.info("Skipping %s: %s story", src.name, src_meta.get("region"))
            continue
        if "path" in src_meta:
            if shorts_taken >= max_shorts:
                log.info("Skipping %s: already %d Short(s) today", src.name, shorts_taken)
                continue
            shorts_taken += 1
        workdir = ROOT / "output" / cfg.get_path("channel.language", "xx") / src.name
        workdir.mkdir(parents=True, exist_ok=True)
        log.info("Following %s -> %s", src.name, workdir)
        try:
            meta = produce(cfg, src, workdir)
            if no_upload:
                done.append(f"{meta['title']} (built, not uploaded)")
                continue
            now = publish == "now"
            if meta["kind"] == "short":
                when = None if now else short_slots[min(n_short, len(short_slots) - 1)]
                n_short += 1
                path = Path(meta["path"])
            else:
                when = None if now else upload.next_slot(cfg, "long")
                path = Path(meta["video"])
            vid = upload.upload(cfg, path, meta, privacy="public" if now else "private",
                                publish_at=when)
            if meta["kind"] == "long":
                upload.set_thumbnail(vid, Path(meta["thumbnail"]))
            upload.upload_captions(vid, Path(meta["captions"]), meta["language"])
            # Topic playlists match on the English subject and tags.
            upload.add_to_playlists(cfg, vid, meta["language"], meta["company"],
                                    src_meta.get("title", ""), " ".join(src_meta.get("tags", [])))
            if meta["series"]:
                upload.add_to_playlist(vid, cfg.get_path("upload.series_playlist"))
            state.record_episode(st, {
                "company": meta["company"], "angle": meta["angle"], "title": meta["title"],
                "region": meta["region"], "language": meta["language"], "video_id": vid,
                "kind": meta["kind"], "source": src.name, "warnings": meta["warnings"],
                "question": meta["question"],
            })
            state.save(st)
            gate.notify("\n".join([
                f"✅ {cfg.get_path('channel.name')}: {meta['title']}",
                f"https://youtu.be/{vid}",
                "Published now" if now else f"Goes public {when:%a %d %b %H:%M}"]),
                markdown=False)
            done.append(meta["title"])
        except Exception as exc:  # noqa: BLE001
            log.exception("Following %s failed", src.name)
            failures.append(f"{src.name}: {type(exc).__name__}: {str(exc)[:200]}")

    if failures:
        raise RuntimeError(f"{len(failures)} of {len(folders)} failed — " + " | ".join(failures))
    log.info("Followed: %s", ", ".join(done) or "nothing new")
    return 0
