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

from . import assets, captions, gate, llm, render, seo, shorts, state, thumbnail, upload, voice
from .config import ROOT, Config, language_rules, latin_only, portrait

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

WHAT {name} VIEWERS TYPE INTO YOUTUBE SEARCH about this story (real search
suggestions; they mostly search in English with "tamil" added):
{searches}

Write the title in {name} script, but START it with the best-fitting search
phrase above in English letters, followed by " | " (e.g. "Gold Rate Tamil |
தங்கம் விலை குறையுமா?", "RBI Repo Rate Tamil | உங்கள் EMI உயருமா?"). If
none fits, start with the subject's name as {name} viewers type it. Between
{minlen} and {maxlen} characters. No ALL CAPS, no exclamation marks, no
clickbait.

Return JSON:
{{"title": "...",
  "summary": "<two plain sentences in {name}; the first STARTS with the subject>",
  "facts": ["<the {nfacts} most important facts above, each one short sentence in {name}>"],
  "thumb_text": "<2-3 words in {name} for the thumbnail: the stake or emotion,
                 not words from the title (e.g. 'உங்கள் EMI உயரும்')>",
  "search": ["<6 short phrases {name} viewers would type into YouTube search to find
             this story, in {name} script, e.g. the subject + news / share price / explained>"]}}"""


def builds(root: Path) -> list[Path]:
    """Every finished build folder under `root`, long videos first."""
    found = [p.parent for p in root.rglob("shots.json")
             if (p.parent / "metadata.json").exists()]
    return sorted(found, key=lambda d: ("short" in d.name, d.name))


def translate(cfg: Config, title: str, lines: list[str], language: str) -> list[str]:
    name = LANGUAGE_NAMES.get(language, language)
    out: list[str] = []
    for start in range(0, len(lines), BATCH):
        part = lines[start:start + BATCH]
        listing = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(part))
        for attempt in range(3):
            got = llm.json_call(TRANSLATE_PROMPT.format(
                name=name, title=title, rules=language_rules(language),
                lines=listing, n=len(part)), temperature=0.3)
            got = got.get("lines") if isinstance(got, dict) else got
            # Latin letters are read badly by the Tamil voice ("பinflation").
            if isinstance(got, list) and len(got) == len(part) \
                    and all(isinstance(t, str) and t.strip() for t in got) \
                    and (attempt == 2 or not any(re.search(r"[A-Za-z]{2,}", t) for t in got)):
                out += [t.strip() for t in got]
                break
            log.warning("Translation of lines %d-%d came back wrong (attempt %d)",
                        start + 1, start + len(part), attempt + 1)
        else:
            raise RuntimeError(f"Could not translate lines {start + 1}-{start + len(part)}")
    return out


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


ACRONYMS = {"rbi", "us", "usa", "uk", "ipo", "gdp", "emi", "sebi", "gst", "imf", "ai", "it", "ev",
            "cpi", "fd", "sip", "nse", "bse", "tcs", "hdfc", "sbi", "lic", "upi", "iren"}


def _head_case(title: str) -> str:
    """"gold price tamil | ..." -> "Gold Price Tamil | ...": search phrases
    come back in lower case."""
    head, sep, rest = title.partition(" | ")
    if not sep or not head.isascii():
        return title
    head = " ".join(w.upper() if w.lower() in ACRONYMS else w[:1].upper() + w[1:]
                    for w in head.split())
    return f"{head} | {rest}"


def _thumbnail(cfg: Config, meta: dict, pkg: dict, title: str, shots: list[dict],
               src: Path, workdir: Path) -> Path:
    """The channel's own thumbnail: its words over the same picture, with the
    English video's figure and direction. Falls back to the English one."""
    en = (meta.get("title_options") or [{}])[0]
    stills = [s for s in shots if s.get("visual_type") in {"photo", "ai_still"}
              and s.get("asset") and Path(s["asset"]).suffix.lower() != ".mp4"]
    stills.sort(key=lambda s: (s.get("visual_type") == "photo"
                               and bool((s.get("visual_spec") or {}).get("search_terms")),
                               s.get("visual_type") != "photo"))
    try:
        return thumbnail.generate(cfg, {
            "title": title, "thumb_words": str(pkg.get("thumb_text") or en.get("thumb_text", "")),
            "thumb_entity": en.get("thumb_entity", ""), "thumb_figure": en.get("thumb_figure", ""),
            "direction": thumbnail.direction(en.get("thumb_figure", ""), meta.get("title", "")),
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
    spoken = [s for s in shots if (s.get("narration") or "").strip()]
    for shot, line in zip(spoken, translate(cfg, en_title,
                                            [s["narration"] for s in spoken], language)):
        shot["narration"] = line
    (workdir / "script.json").write_text(
        json.dumps(shots, indent=2, ensure_ascii=False), encoding="utf-8")

    frame = portrait(cfg) if is_short else cfg
    assets.redraw_cards(frame, shots, workdir / "assets", company)
    assets.gather(frame, shots, workdir, company)      # anything that did not copy
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
    pkg = llm.json_call(PACKAGE_PROMPT.format(
        searches="\n".join(f"- {s}" for s in searches) or "(none found)",
        kind="Short" if is_short else "video", name=name, title=en_title,
        summary=(meta.get("title_options") or [{}])[0].get("summary", "")
        or research.get("summary", ""),
        facts="\n".join(f"- {f}" for f in facts[:25]) or "-", maxlen=maxlen,
        minlen=maxlen * 2 // 3, nfacts=nfacts),
        temperature=0.5)
    title = _head_case(str(pkg.get("title") or en_title).strip())[:maxlen + 10]
    summary = str(pkg.get("summary") or "").strip()
    local_facts = [str(f) for f in pkg.get("facts") or [] if f][:nfacts]
    # Real search phrases first, then the model's {name}-script ones.
    search = list(dict.fromkeys(searches[:4] + [str(t) for t in pkg.get("search") or [] if t]))[:8]
    sources = research.get("sources", [])
    keywords = [t for t in meta.get("tags", []) if t != "news explained"][:4]

    out = {"company": company, "angle": meta.get("angle", ""), "region": meta.get("region"),
           "language": language, "warnings": meta.get("warnings", []),
           "tags": seo.tags(company, keywords, language, extra=search), "source": src.name,
           "captions": str(captions.srt(shots, workdir / "captions.srt"))}
    if is_short:
        # The headline over the Short stays the English one: on-screen text
        # is drawn in Latin letters, like the rest of the pictures.
        short = shorts.standalone(cfg, video, shots, workdir,
                                  latin_only(en_title) or company, title, language)
        out.update(short, kind="short", description=seo.description(
            cfg, summary, "", sources[:6], keywords or [company], language,
            facts=local_facts, tags_extra=search) + " #shorts")
    else:
        out.update(kind="long", title=title, video=str(video),
                   description=seo.description(cfg, summary, seo.chapters(cfg, shots),
                                               sources, keywords or [company], language,
                                               facts=local_facts, tags_extra=search),
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
            state.record_episode(st, {
                "company": meta["company"], "angle": meta["angle"], "title": meta["title"],
                "region": meta["region"], "language": meta["language"], "video_id": vid,
                "kind": meta["kind"], "source": src.name, "warnings": meta["warnings"],
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
