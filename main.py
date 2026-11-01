#!/usr/bin/env python3
"""Terminal Value — documentary pipeline.

    python main.py                          full run: trending topic -> video -> upload
    python main.py --topic "RBI repo rate" --region IN    make a video on a given story
    python main.py --publish slot           schedule for the next slot instead of posting at once
    python main.py --no-upload              build only, leave the files on disk
    python main.py --reach                  performance report only
"""
from __future__ import annotations

import argparse
import copy
import json
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

from src import (assets, captions, engage, factcheck, follow, gate, hooks, reach, render,
                 research, script as script_mod, seo, shorts, state, thumbnail, upload, voice)
from src.config import ROOT, Config, latin_only, load, portrait

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s %(name)-18s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("pipeline")


def slug(text: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in text.lower()).strip("-")[:48]


def save_shots(shot_list: list[dict], workdir: Path) -> None:
    """The finished shot list, with picture paths relative to the build
    folder: the Tamil channel (src/follow.py) rebuilds the video from it."""
    out = []
    for s in shot_list:
        s = dict(s)
        if s.get("asset"):
            try:
                s["asset"] = str(Path(s["asset"]).resolve().relative_to(workdir.resolve()))
            except ValueError:
                s["asset"] = None
        out.append(s)
    (workdir / "shots.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")


def produce(cfg, story: dict, workdir: Path) -> dict:
    if story.get("history"):            # the story arc of the past, not of the news
        cfg = Config(copy.deepcopy(dict(cfg)))
        arc = "history_beats" if story.get("collapse", True) else "biography_beats"
        cfg["structure"]["beats"] = cfg["structure"][arc]
    company, angle = story["topic"], story.get("angle", "")
    language, region = story.get("language", "en"), story.get("region")
    kind = cfg.get_path("format.kind", "documentary")
    news = cfg.get_path("mode", "trending") == "trending"

    # ---- research -------------------------------------------------------
    if news:
        fb = research.build_news(cfg, story)
        warnings = research.validate_news(cfg, fb)
    else:
        fb = research.build(cfg, company, angle)
        warnings = research.validate(cfg, fb)
    (workdir / "research.json").write_text(
        json.dumps(fb.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")

    # ---- hook -----------------------------------------------------------
    hook = hooks.generate_and_score(company, angle, fb.facts, language, kind)
    log.info("Hook (%d): %s", hook["winner"]["final_score"], hook["winner"]["text"][:110])

    # ---- script ---------------------------------------------------------
    sc = script_mod.write(cfg, fb, hook["winner"]["text"], language)
    sc, checked = factcheck.check(sc, fb)
    warnings = [*warnings, *checked]
    (workdir / "script.json").write_text(
        json.dumps(sc, indent=2, ensure_ascii=False), encoding="utf-8")
    shot_list = script_mod.all_shots(sc, int(cfg.get_path("format.long.max_shot_words", 20)))

    # ---- narration (sets real shot durations) ---------------------------
    shot_list = voice.narrate_shots(cfg, shot_list, workdir, region, language)
    log.info("Narration: %d characters", len(sc["full_narration"]))

    # ---- visuals --------------------------------------------------------
    credits = assets.gather(cfg, shot_list, workdir, company)

    # ---- render ---------------------------------------------------------
    clips = render.build_shot_clips(cfg, shot_list, workdir)
    video = render.concat_with_audio(cfg, clips, shot_list, workdir,
                                     render.pick_music(cfg, company))
    save_shots(shot_list, workdir)

    # ---- packaging ------------------------------------------------------
    best_fact = next((f.get("claim", "") for f in fb.facts if f.get("is_surprising")), "")
    fact_lines = [str(f.get("claim", "")) for f in fb.facts]
    title_opts = seo.titles(cfg, company, angle, best_fact, language, facts=fact_lines,
                            style=title_style())
    best = title_opts[0]
    title = set_title(story.get("title", ""), fact_lines, best["title"])
    series = (cfg.get_path("upload.series_playlist")
              if story.get("history") and story.get("collapse", True) else "")
    if series and len(title) + len(series) + 3 <= 100:
        title = f"{title} | {series}"
    keywords = [str(k) for k in story.get("keywords", [])]
    chapter_text = seo.chapters(cfg, shot_list)
    # Each picture credited once, however many shots used it.
    attribution = "\n".join(dict.fromkeys(
        f"- {c['credit']} ({c['licence']}) {c.get('url','')}"
        for c in credits if c.get("needs_attribution")))
    desc = seo.description(
        cfg, best.get("summary") or fb.summary, chapter_text,
        [s if isinstance(s, dict) else s.__dict__ for s in fb.sources],
        [company, *keywords], language, facts=fact_lines, credits=attribution,
        music=render.music_credit(cfg, company))

    srt = captions.srt(shot_list, workdir / "captions.srt")

    # A real photograph of the subject beats an illustration on a thumbnail.
    stills = [s for s in shot_list
              if s.get("visual_type") in {"photo", "ai_still"} and s.get("asset")
              and Path(s["asset"]).suffix.lower() != ".mp4"]
    # Prefer a scene or an illustration to a photo found by a name: a named
    # search can return people who are not the story (a "CSI 300" search
    # brought back a politician's visit to an exchange).
    stills.sort(key=lambda s: (s.get("visual_type") == "photo"
                               and bool((s.get("visual_spec") or {}).get("search_terms")),
                               s.get("visual_type") != "photo"))
    backdrop = Path(stills[0]["asset"]) if stills else None
    thumb = thumbnail.generate(
        cfg,
        {"company": company, "thumb_figure": best.get("thumb_figure", ""),
         "thumb_words": best.get("thumb_text") or company, "title": title,
         "thumb_entity": series.upper() or best.get("thumb_entity", "")},
        backdrop, workdir / "thumbnail.jpg")
    # Two more to try in YouTube Studio's Test & Compare (it has no API).
    for n, option in enumerate(title_opts[1:3], 2):
        try:
            thumbnail.generate(cfg, {"company": company, "title": option["title"],
                                     "thumb_figure": option.get("thumb_figure", ""),
                                     "thumb_words": option.get("thumb_text") or company,
                                     "thumb_entity": series.upper() or option.get("thumb_entity", "")},
                               backdrop, workdir / f"thumbnail_{n}.jpg")
        except Exception as exc:  # noqa: BLE001
            log.warning("Spare thumbnail failed: %s", exc)

    runtime = sum(s["duration"] for s in shot_list)
    floor = float(cfg.get_path("format.long.min_minutes", 0))
    if floor and runtime < floor * 60:
        warnings = [*warnings, f"Long video is only {runtime / 60:.1f} min, under "
                               f"{floor:g}: no mid-roll ads on it"]
    meta = {
        "company": company, "angle": angle, "title": title,
        "region": region, "language": language, "series": bool(series),
        "question": best.get("question", ""), "title_style": best.get("style", ""),
        "category_id": cfg.get_path("upload.category_explainer")
        if story.get("history") or story.get("evergreen") else None,
        "title_options": title_opts, "description": desc,
        "tags": seo.tags(company, keywords, language, extra=best.get("searches")),
        "chapters": chapter_text,
        "runtime": f"{int(runtime//60)}:{int(runtime%60):02d}",
        "word_count": sc["word_count"], "warnings": warnings,
        "video": str(video), "thumbnail": str(thumb), "captions": str(srt),
    }
    (workdir / "metadata.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

    meta["_shots"] = shot_list
    return meta


SHORT_BEAT = {
    "id": "short",
    "label": "Short",
    "share": 1.0,
    "intent": ("A complete {seconds}-second explainer of this one story for someone who has "
               "not heard of it. FIRST SENTENCE: at most 8 words, spoken in the first 3 seconds: "
               "the story's biggest number or sharpest conflict, so a scrolling viewer needs "
               "the rest (e.g. 'Boeing just lost its biggest order.'). Use everyday words, "
               "never jargon like 'free cash flow'. Then what happened, "
               "then why it matters to the viewer. 6-9 shots of ONE sentence each, under 12 "
               "words, so the picture changes every few seconds. Stay on one subject, with a "
               "real number in the first or second sentence, as the Shorts that held "
               "viewers best did. LAST SENTENCE: short, repeating the opening's key word or number, and leading "
               "straight back into the first sentence, so the Short loops without a pause. "
               "No greeting, no sign-off, no 'subscribe'."),
}


def hook_words(sc: dict) -> int:
    """Words in a script's first sentence."""
    import re
    first = next((s.get("narration", "") for b in sc.get("beats", []) for s in b.get("shots", [])
                  if (s.get("narration") or "").strip()), "")
    return len(re.split(r"(?<=[.?!।])\s+", first.strip())[0].split())


def loops(sc: dict) -> bool:
    """The last line picks up a word or number from the first, so the Short replays."""
    import re

    lines = [s.get("narration", "") for b in sc.get("beats", []) for s in b.get("shots", [])
             if (s.get("narration") or "").strip()]
    if len(lines) < 2:
        return True
    key = lambda text: {w for w in re.findall(r"[a-z0-9]+", text.lower())
                        if len(w) > 4 or w.isdigit()}
    return bool(key(lines[0]) & key(lines[-1]))


def produce_short(cfg, story: dict, workdir: Path) -> dict:
    """One standalone Short on one story: research -> ~30s script -> render."""
    company, language = story["topic"], story.get("language", "en")
    seconds = int(cfg.get_path("format.short.target_seconds", 30))
    wpm = cfg.get_path("format.long.words_per_minute", 150)

    fb = research.build_news(cfg, story)
    warnings = research.validate_news(cfg, fb)
    (workdir / "research.json").write_text(
        json.dumps(fb.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    beat = {**SHORT_BEAT, "intent": SHORT_BEAT["intent"].format(seconds=seconds)}
    tall = portrait(cfg)          # drawn 9:16 from the start, not cropped later
    # Shorts change picture faster than long videos.
    tall["visuals"]["shot_seconds"] = cfg.get_path("format.short.shot_seconds",
                                                   cfg.get_path("visuals.shot_seconds"))
    # The first line decides whether the thumb keeps scrolling: a script whose
    # opening sentence runs long is written again (up to twice).
    for attempt in range(3):
        sc = script_mod.write(tall, fb, "", language, beats=[beat],
                              total_words=int(seconds / 60 * wpm))
        limit = int(seconds / 60 * wpm * 1.2)
        if hook_words(sc) <= 10 and sc["word_count"] <= limit and loops(sc):
            break
        log.info("Short has a %d-word opening, %d words, loop %s; rewriting (%d)",
                 hook_words(sc), sc["word_count"], loops(sc), attempt + 1)
        beat = {**beat, "intent": beat["intent"] + f" YOUR LAST DRAFT RAN {sc['word_count']} "
                f"WORDS WITH A {hook_words(sc)}-WORD FIRST SENTENCE. THE LIMIT IS {limit} WORDS "
                "IN ALL AND 8 IN THE FIRST SENTENCE. THE LAST SENTENCE MUST REPEAT A KEY WORD OR "
                "NUMBER FROM THE FIRST."}
    for b_ in sc["beats"]:              # a silent shot in a Short is dead air
        b_["shots"] = [s for s in b_.get("shots", []) if (s.get("narration") or "").strip()]
    (workdir / "script.json").write_text(
        json.dumps(sc, indent=2, ensure_ascii=False), encoding="utf-8")
    sc, checked = factcheck.check(sc, fb)
    warnings = [*warnings, *checked]
    shot_list = voice.narrate_shots(
        tall, script_mod.all_shots(sc, int(cfg.get_path("format.short.max_shot_words", 12))),
        workdir, story.get("region"), language)
    assets.gather(tall, shot_list, workdir, company)
    clips = render.build_shot_clips(tall, shot_list, workdir)
    video = render.concat_with_audio(tall, clips, shot_list, workdir,
                                     render.pick_music(cfg, company))
    save_shots(shot_list, workdir)

    best_fact = next((f.get("claim", "") for f in fb.facts if f.get("is_surprising")), "")
    best = seo.titles(cfg, company, story.get("angle", ""), best_fact, language,
                      maxlen=seo.SHORT_TITLE_MAX, style=title_style(),
                      facts=[str(f.get("claim", "")) for f in fb.facts])[0]
    keywords = [str(k) for k in story.get("keywords", [])]
    # The on-screen headline is the title itself: written to be understood
    # cold, where the 2-4 word thumbnail text often is not.
    best["title"] = set_title(story.get("title", ""),
                              [str(f.get("claim", "")) for f in fb.facts], best["title"])
    if story.get("stock") and "stock" not in best["title"].lower():
        full = f"{story['stock']} Stock: {best['title']}"
        best["title"] = full if len(full) <= seo.SHORT_TITLE_MAX else f"Why {story['stock']} Stock Moved"
    headline = latin_only(best["title"]) or best.get("thumb_text") or company
    short = shorts.standalone(cfg, video, shot_list, workdir,
                              headline, best["title"], language)
    lead = best.get("summary") or fb.summary
    if story.get("parent_url"):
        lead += f"\n\nWatch the full story: {story['parent_url']}"
    short["description"] = seo.description(
        cfg, lead, "",
        [s if isinstance(s, dict) else s.__dict__ for s in fb.sources][:6],
        [company, *keywords], language,
        facts=[str(f.get("claim", "")) for f in fb.facts][:4],
        music=render.music_credit(cfg, company)) + " #shorts"
    short["captions"] = str(captions.srt(shot_list, workdir / "captions.srt"))
    short.update({"company": company,
                  "tags": seo.tags(company, keywords, language, extra=best.get("searches")),
                  "language": language, "region": story.get("region"),
                  "warnings": warnings, "angle": story.get("angle", ""),
                  "question": best.get("question", ""), "title_style": best.get("style", ""),
                  "queue": story.get("queue", ""),
                  "parent_url": story.get("parent_url", "")})
    (workdir / "metadata.json").write_text(
        json.dumps(short, indent=2, ensure_ascii=False), encoding="utf-8")
    return short


def title_style() -> str:
    """Search titles and curious titles in turn, so the report can compare them."""
    return "curious" if len(state.load().get("episodes", [])) % 2 else "search"


def desc_lead(meta: dict) -> str:
    return meta["description"].split("\n\n")[0]


def manual_story(cfg, args) -> dict:
    regions = {r["code"]: r for r in cfg.get_path("trending.regions", [])}
    region = (args.region or next(iter(regions), "US")).upper()
    return {
        "topic": args.topic, "company": args.topic,
        "angle": args.angle or f"What is happening with {args.topic}, and why does it matter?",
        "region": region,
        "language": args.language or regions.get(region, {}).get("language", "en"),
        "search_query": args.topic, "wiki_title": "", "keywords": [args.topic],
    }


def candidates_for(cfg, args, n: int) -> list[dict]:
    """Ranked stories: a manual topic, today's trends, or documentary topics."""
    if args.topic:
        return [manual_story(cfg, args)]
    if cfg.get_path("mode", "trending") == "trending":
        from src import trends
        return trends.pick(cfg, n=max(n, 4))
    candidates = topics_or_die(cfg)
    for c in candidates:
        c.setdefault("topic", c.get("company"))
        c.setdefault("language", cfg.get_path("channel.language", "en"))
    return candidates


def history_story(cfg, args) -> dict | None:
    """Every format.long.history_every-th long video: a past business story
    already proven at a million views on YouTube."""
    every = int(cfg.get_path("format.long.history_every", 0))
    if args.topic or every <= 0:
        return None
    eps = [e for e in state.load().get("episodes", []) if e.get("kind", "long") == "long"]
    if len(eps) % every != every - 1:
        return None
    from src import viral
    return viral.history_story(cfg)


def queued(args, kind: str, n: int) -> list[dict]:
    """The next stories from data/queue.yaml not yet uploaded."""
    import yaml

    path = ROOT / "data" / "queue.yaml"
    if args.topic or not path.exists():
        return []
    used = {e.get("queue") for e in state.load().get("episodes", [])}
    out = []
    for item in (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get(kind, []):
        key = f"{kind}:{item['topic']}"
        if key in used or str(item.get("not_before", "")) > datetime.now().date().isoformat():
            continue
        story = {"company": item["topic"], "region": "US", "language": "en", "wiki_title": "",
                 **item, "queue": key}
        story["history"] = item["topic"] if item.get("history") else ""
        out.append(story)
        if len(out) >= n:
            break
    return out


def set_title(fixed: str, facts: list[str], best: str) -> str:
    """A queued title stands only if the research supports every figure in it."""
    return fixed if fixed and not seo._unsupported_numbers(fixed, " ".join(facts)) else best


def evergreen_story(cfg, args) -> dict | None:
    """Every format.long.evergreen_every-th long video: the next unused topic
    from data/evergreen.yaml, written as an explainer rather than news."""
    import yaml

    every = int(cfg.get_path("format.long.evergreen_every", 0))
    path = ROOT / "data" / "evergreen.yaml"
    if args.topic or every <= 0 or not path.exists():
        return None
    eps = [e for e in state.load().get("episodes", []) if e.get("kind", "long") == "long"]
    if len(eps) % every != every - 1:
        return None
    used = {str(e.get("evergreen", "")).lower() for e in eps}
    for t in (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("topics", []):
        if t["topic"].lower() not in used:
            return {"topic": t["topic"], "company": t["topic"], "angle": t["angle"],
                    "region": "US", "language": "en", "search_query": t["topic"],
                    "wiki_title": "", "keywords": list(t.get("search", []))[:4] or [t["topic"]],
                    "evergreen": t["topic"]}
    log.warning("Every evergreen topic has been used: add more to %s", path)
    return None


def todays_kind(cfg, args) -> str:
    """'long' on a long-video day, otherwise 'shorts' (standalone Shorts)."""
    if args.kind != "auto":
        return args.kind
    tz = upload.ZoneInfo(cfg.get_path("channel.timezone", "Asia/Kolkata"))
    today = datetime.now(tz).strftime("%A").lower()
    days = [d.lower() for d in cfg.get_path("format.long.days", [])]
    if days and today not in days:
        return "shorts"
    # Counted from the last long video actually uploaded, so a long day that
    # fails is retried the next day instead of being skipped.
    every = int(cfg.get_path("format.long.every_days", 1))
    # Measured in hours, not calendar dates: GitHub starts the run anywhere from
    # late evening to morning Eastern, which would otherwise shift the date.
    last = max((datetime.fromisoformat(e["created_at"]).astimezone(tz)
                for e in state.load().get("episodes", [])
                if e.get("kind", "long") == "long" and e.get("created_at")),
               default=None)
    if every > 1 and last and datetime.now(tz) - last < timedelta(days=every, hours=-12):
        return "shorts"
    return "long"


def report(lines: list[str], video_id: str, wanted_public: bool) -> None:
    yt_status = upload.status(video_id)
    if wanted_public and yt_status.get("privacyStatus") != "public":
        lines.append("⚠️ YouTube kept it PRIVATE (status: "
                     f"{yt_status.get('privacyStatus')}). New API projects are locked to "
                     "private until Google approves the API audit form.")
    gate.notify("\n".join(lines), markdown=False)
    log.info("YouTube status for %s: %s", video_id, yt_status)


def with_stock(cfg, args, stories: list[dict]) -> list[dict]:
    """The second Short of the day is a stock move: "iren stock" was the channel's
    best search term by far."""
    from src import trends

    move = None if args.topic else trends.stock_move(cfg)
    if move:
        stories.insert(min(1, len(stories)), move)
    return stories


def lead_of(short: dict) -> str:
    return short["description"].split("\n\n")[0]


def fresh_or_queue(cfg, args, n: int, kinds: tuple[str, ...]) -> list[dict]:
    """Today's picks. If the picker fails, a day with queued stories still runs."""
    try:
        return candidates_for(cfg, args, n)
    except Exception as exc:  # noqa: BLE001
        if not any(queued(args, k, 1) for k in kinds):
            raise
        log.warning("Story picker failed (%s); using the queue", str(exc)[:160])
        return []


def run_shorts_day(cfg, args) -> int:
    """No long video today: one standalone Short on each of the top stories."""
    n = args.count or int(cfg.get_path("format.short.per_day", 2))
    stories = with_stock(cfg, args, queued(args, "shorts", n)
                         + fresh_or_queue(cfg, args, n, ("shorts",)))
    return make_shorts(cfg, args, stories[:n], stories[n:])


def make_shorts(cfg, args, stories: list[dict], spares: list[dict] | None = None) -> int:
    """One standalone Short per story, each at its own daily slot. A story that
    fails is replaced by the next spare, so one bad story does not lose a Short."""
    n = len(stories)
    times = upload.slots(cfg, "short", n)
    queue = [*stories, *(spares or [])]
    st = state.load()
    done, failures = [], []

    while queue and len(done) < n:
        story, i = queue.pop(0), len(done)
        company = story["topic"]
        workdir = ROOT / "output" / f"{datetime.now():%Y-%m-%d}-short-{slug(company)}"
        workdir.mkdir(parents=True, exist_ok=True)
        log.info("Short %d/%d: %s [%s/%s]", i + 1, n, company,
                 story.get("region"), story.get("language"))
        try:
            short = produce_short(cfg, story, workdir)
            if args.no_upload:
                done.append(f"{company} (built, not uploaded)")
                continue
            now = args.publish == "now"
            when = None if now else times[i] if i < len(times) else None
            sid = upload.upload(cfg, Path(short["path"]),
                                {"title": short["title"][:100], "language": short["language"],
                                 "description": short["description"], "tags": short["tags"]},
                                privacy="public" if now else "private", publish_at=when)
            upload.upload_captions(sid, Path(short["captions"]), short["language"])
            upload.add_to_playlists(cfg, sid, short["language"], company, short["title"],
                                    " ".join(short["tags"]))
            engage.localise(cfg, sid, short["title"], lead_of(short))
            state.record_episode(st, {
                "company": company, "angle": short["angle"], "title": short["title"],
                "region": short["region"], "language": short["language"],
                "video_id": sid, "kind": "short", "warnings": short["warnings"],
                "question": short["question"], "parent_url": short["parent_url"],
                "title_style": short["title_style"], "queue": short["queue"],
            })
            state.save(st)
            lines = [f"✅ Short: {short['title']}", f"https://youtu.be/{sid}",
                     f"{short['region']} · {short['language']}",
                     "Published now" if now else f"Goes public {when:%a %d %b %H:%M}"]
            if short["warnings"]:
                lines.append("Research warnings: " + "; ".join(short["warnings"]))
            report(lines, sid, now)
            done.append(company)
        except Exception as exc:  # noqa: BLE001
            log.exception("Short on %s failed", company)
            failures.append(f"{company}: {type(exc).__name__}: {str(exc)[:200]}")

    if len(done) < n:
        raise RuntimeError(f"{n - len(done)} of {n} Shorts not made — " + " | ".join(failures))
    if failures:
        gate.notify("⚠️ Replaced with a spare story: " + " | ".join(failures), markdown=False)
    log.info("Shorts day done: %s", ", ".join(done))
    return 0


def run(args) -> int:
    cfg = load()
    args.publish = args.publish or cfg.get_path("upload.publish", "now")
    do_gates = not args.no_gates

    if args.reach:
        path = reach.report(cfg)
        print(path.read_text(encoding="utf-8"))
        return 0

    if not args.no_upload:
        engage.post_questions(cfg)

    if args.follow:
        return follow.run(cfg, Path(args.follow), args.publish, args.no_upload)

    if todays_kind(cfg, args) == "shorts":
        return run_shorts_day(cfg, args)

    n_shorts = 0 if args.no_shorts else int(cfg.get_path("format.short.per_day", 2))
    candidates = fresh_or_queue(cfg, args, 4 + n_shorts, ("long",))
    first = queued(args, "long", 1)
    story = (first[0] if first else None) or history_story(cfg, args) \
        or evergreen_story(cfg, args) or candidates[0]
    if story.get("evergreen") or story.get("history"):
        log.info("Not today's news: %s", story["angle"])
    if do_gates and cfg.get_path("gates.topic_approval", False):
        story = gate.topic_gate(cfg, candidates)
    if story is None:
        log.info("No topic approved. Stopping — nothing produced.")
        return 0
    # A story that cannot be made gives way to the next one rather than losing
    # the day's video.
    meta, last_error = None, None
    for story in [story, *[c for c in candidates if c is not story][:2]]:
        company = story["topic"]
        workdir = ROOT / "output" / f"{datetime.now():%Y-%m-%d}-{slug(company)}"
        workdir.mkdir(parents=True, exist_ok=True)
        log.info("Producing %s [%s/%s] -> %s", company, story.get("region"),
                 story.get("language"), workdir)
        try:
            meta = produce(cfg, story, workdir)
            break
        except voice.VoiceError:
            raise                   # the next story would fail the same way
        except Exception as exc:  # noqa: BLE001
            log.exception("Long video on %s failed", company)
            last_error = exc
    if meta is None:
        raise last_error
    shot_list = meta.pop("_shots")
    video = Path(meta["video"])
    # Shorts are their own stories (the next ones down today's list): they
    # outperformed cuts from the long video about five to one. A manual topic
    # has no other stories, so its Shorts are cut from the long video.
    others = with_stock(cfg, args, queued(args, "shorts", n_shorts)
                        + [c for c in candidates if c["topic"] != company])
    short_stories, spare_stories = others[:n_shorts], others[n_shorts:]

    if args.no_upload:
        if n_shorts and not short_stories:
            shorts.build_all(cfg, video, shot_list, workdir, company, n_shorts,
                             meta["language"])
        log.info("Built %s (upload skipped)", video)
        if short_stories:
            make_shorts(cfg, args, short_stories, spare_stories)
        return 0

    # ---- optional review gate -------------------------------------------
    decision = "publish" if args.publish == "now" else "schedule"
    if do_gates and cfg.get_path("gates.final_review", False):
        decision = gate.review_gate(cfg, video, meta, meta["warnings"])
    if decision == "hold":
        log.info("Held at review. File is at %s", video)
        return 0

    publish_at = upload.next_slot(cfg, "long") if decision == "schedule" else None
    video_id = upload.upload(cfg, video, meta,
                             privacy="public" if decision == "publish" else "private",
                             publish_at=publish_at)
    upload.set_thumbnail(video_id, Path(meta["thumbnail"]))
    upload.upload_captions(video_id, Path(meta["captions"]), meta["language"])
    upload.add_to_playlists(cfg, video_id, meta["language"], company, meta["title"],
                            " ".join(meta["tags"]))
    if meta["series"]:
        upload.add_to_playlist(video_id, cfg.get_path("upload.series_playlist"))
    engage.localise(cfg, video_id, meta["title"], desc_lead(meta), Path(meta["captions"]))
    # A history film also gets a Short that sends viewers to it.
    if story.get("history") and n_shorts:
        short_stories.insert(min(1, len(short_stories)),
                             {**story, "parent_url": f"https://youtu.be/{video_id}"})
        spare_stories = short_stories[n_shorts:] + spare_stories
        short_stories = short_stories[:n_shorts]

    # ---- shorts ----------------------------------------------------------
    short_ids = []
    if n_shorts and not short_stories:
        built = shorts.build_all(cfg, video, shot_list, workdir, company, n_shorts,
                                 meta["language"], f"https://youtu.be/{video_id}")
        # Shorts go out after the long, at their own daily slots.
        after = publish_at or datetime.now(upload.ZoneInfo(
            cfg.get_path("channel.timezone", "Asia/Kolkata")))
        times = [t for t in upload.slots(cfg, "short", len(built) + 4) if t > after]
        for s, when in zip(built, times):
            try:
                direct = decision == "publish"
                sid = upload.upload(cfg, Path(s["path"]),
                                    {"title": s["title"][:100], "language": meta["language"],
                                     "description": s["description"], "tags": meta["tags"]},
                                    privacy="public" if direct else "private",
                                    publish_at=None if direct else when)
                short_ids.append(sid)
            except Exception as exc:  # noqa: BLE001
                log.warning("Short upload failed: %s", exc)

    st = state.load()
    state.record_episode(st, {
        "company": company, "angle": meta["angle"], "title": meta["title"],
        "region": meta["region"], "language": meta["language"],
        "video_id": video_id, "short_ids": short_ids, "kind": "long",
        "question": meta["question"], "title_style": meta["title_style"],
        "queue": story.get("queue", ""),
        "evergreen": story.get("evergreen", ""), "history": story.get("history", ""),
        "runtime": meta["runtime"], "warnings": meta["warnings"],
    })
    state.save(st)

    lines = [
        f"✅ {meta['title']}",
        f"https://youtu.be/{video_id}",
        f"{meta['region']} · {meta['language']} · {meta['runtime']}",
        (f"Goes public {publish_at:%a %d %b %H:%M}" if publish_at else "Published now"),
    ]
    if meta["warnings"]:
        lines.append("Research warnings: " + "; ".join(meta["warnings"]))
    report(lines, video_id, decision == "publish")
    if short_stories:
        return make_shorts(cfg, args, short_stories, spare_stories)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--topic")
    ap.add_argument("--angle", default="")
    ap.add_argument("--region", help="US or MARKETS (with --topic)")
    ap.add_argument("--language", choices=["hi", "en"], help="override narration language")
    ap.add_argument("--publish", choices=["slot", "now"],
                    help="now: public as soon as it uploads; slot: schedule for the next "
                         "publish time. Default: upload.publish in config.yaml")
    ap.add_argument("--count", type=int, default=0,
                    help="with --kind shorts: how many Shorts to make (default: per_day)")
    ap.add_argument("--kind", choices=["auto", "long", "shorts"], default="auto",
                    help="auto: long on format.long.days, Shorts otherwise")
    ap.add_argument("--no-upload", action="store_true")
    ap.add_argument("--no-gates", action="store_true")
    ap.add_argument("--no-shorts", action="store_true")
    ap.add_argument("--reach", action="store_true")
    ap.add_argument("--follow", metavar="DIR",
                    help="second channel (CHANNEL=ta): publish the main channel's builds "
                         "found under DIR again in this channel's language")
    args = ap.parse_args()

    try:
        return run(args)
    except Exception as exc:  # noqa: BLE001
        log.exception("Run failed")
        gate.notify(f"❌ {load().get_path('channel.name', 'Terminal Value')} run failed: "
                    f"{type(exc).__name__}: {str(exc)[:500]}",
                    markdown=False)
        return 1


def topics_or_die(cfg):
    from src import topics
    candidates = topics.propose(cfg, n=6)
    if not candidates:
        raise RuntimeError("Topic generator returned nothing")
    return candidates


if __name__ == "__main__":
    sys.exit(main())
