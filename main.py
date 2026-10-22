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
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

from src import (assets, captions, follow, gate, hooks, reach, render, research,
                 script as script_mod, seo, shorts, state, thumbnail, upload, voice)
from src.config import ROOT, latin_only, load, portrait

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
    (workdir / "script.json").write_text(
        json.dumps(sc, indent=2, ensure_ascii=False), encoding="utf-8")
    shot_list = script_mod.all_shots(sc)

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
    title_opts = seo.titles(cfg, company, angle, best_fact, language, facts=fact_lines)
    best = title_opts[0]
    title = best["title"]
    keywords = [str(k) for k in story.get("keywords", [])]
    chapter_text = seo.chapters(cfg, shot_list)
    desc = seo.description(
        cfg, best.get("summary") or fb.summary, chapter_text,
        [s if isinstance(s, dict) else s.__dict__ for s in fb.sources],
        keywords or [company], language)
    attribution = "\n".join(
        f"- {c['credit']} ({c['licence']}) {c.get('url','')}"
        for c in credits if c.get("needs_attribution"))
    if attribution:
        desc = (desc + "\n\nIMAGE CREDITS\n" + attribution)[:4900]

    srt = captions.srt(shot_list, workdir / "captions.srt")

    # A real photograph of the subject beats an illustration on a thumbnail.
    stills = [s for s in shot_list
              if s.get("visual_type") in {"photo", "ai_still"} and s.get("asset")
              and Path(s["asset"]).suffix.lower() != ".mp4"]
    stills.sort(key=lambda s: s.get("visual_type") != "photo")
    backdrop = Path(stills[0]["asset"]) if stills else None
    thumb = thumbnail.generate(
        cfg,
        {"company": company, "thumb_figure": best.get("thumb_figure", ""),
         "thumb_words": best.get("thumb_text") or company},
        backdrop, workdir / "thumbnail.jpg")

    runtime = sum(s["duration"] for s in shot_list)
    meta = {
        "company": company, "angle": angle, "title": title,
        "region": region, "language": language,
        "title_options": title_opts, "description": desc,
        "tags": seo.tags(company, keywords, language), "chapters": chapter_text,
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
               "not heard of it: the single most striking verified fact first, then what "
               "happened, then why it matters. 4-6 shots. No greeting, no sign-off."),
}


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
    sc = script_mod.write(cfg, fb, "", language, beats=[beat],
                          total_words=int(seconds / 60 * wpm))
    (workdir / "script.json").write_text(
        json.dumps(sc, indent=2, ensure_ascii=False), encoding="utf-8")
    shot_list = voice.narrate_shots(cfg, script_mod.all_shots(sc), workdir,
                                    story.get("region"), language)
    tall = portrait(cfg)          # drawn 9:16 from the start, not cropped later
    assets.gather(tall, shot_list, workdir, company)
    clips = render.build_shot_clips(tall, shot_list, workdir)
    video = render.concat_with_audio(tall, clips, shot_list, workdir,
                                     render.pick_music(cfg, company))
    save_shots(shot_list, workdir)

    best_fact = next((f.get("claim", "") for f in fb.facts if f.get("is_surprising")), "")
    best = seo.titles(cfg, company, story.get("angle", ""), best_fact, language,
                      maxlen=seo.SHORT_TITLE_MAX,
                      facts=[str(f.get("claim", "")) for f in fb.facts])[0]
    keywords = [str(k) for k in story.get("keywords", [])]
    # The on-screen headline is the title itself: written to be understood
    # cold, where the 2-4 word thumbnail text often is not.
    headline = latin_only(best["title"]) or best.get("thumb_text") or company
    short = shorts.standalone(cfg, video, shot_list, workdir,
                              headline, best["title"], language)
    short["description"] = seo.description(
        cfg, best.get("summary") or fb.summary, "",
        [s if isinstance(s, dict) else s.__dict__ for s in fb.sources][:6],
        keywords or [company], language) + "\n#shorts"
    short.update({"company": company, "tags": seo.tags(company, keywords, language),
                  "language": language, "region": story.get("region"),
                  "warnings": warnings, "angle": story.get("angle", "")})
    (workdir / "metadata.json").write_text(
        json.dumps(short, indent=2, ensure_ascii=False), encoding="utf-8")
    return short


def manual_story(cfg, args) -> dict:
    regions = {r["code"]: r for r in cfg.get_path("trending.regions", [])}
    region = (args.region or next(iter(regions), "IN")).upper()
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
    last = max((datetime.fromisoformat(e["created_at"]).astimezone(tz).date()
                for e in state.load().get("episodes", [])
                if e.get("kind", "long") == "long" and e.get("created_at")),
               default=None)
    if every > 1 and last and (datetime.now(tz).date() - last).days < every:
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


def run_shorts_day(cfg, args) -> int:
    """No long video today: one standalone Short on each of the top stories."""
    n = args.count or int(cfg.get_path("format.short.per_day", 2))
    return make_shorts(cfg, args, candidates_for(cfg, args, n)[:n])


def make_shorts(cfg, args, stories: list[dict]) -> int:
    """One standalone Short per story, each at its own daily slot."""
    n = len(stories)
    times = upload.slots(cfg, "short", n)
    st = state.load()
    done, failures = [], []

    for i, story in enumerate(stories):
        company = story["topic"]
        workdir = ROOT / "output" / f"{datetime.now():%Y-%m-%d}-short-{slug(company)}"
        workdir.mkdir(parents=True, exist_ok=True)
        log.info("Short %d/%d: %s [%s/%s]", i + 1, len(stories), company,
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
            state.record_episode(st, {
                "company": company, "angle": short["angle"], "title": short["title"],
                "region": short["region"], "language": short["language"],
                "video_id": sid, "kind": "short", "warnings": short["warnings"],
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

    if failures:
        raise RuntimeError(f"{len(failures)} of {len(stories)} Shorts failed — "
                           + " | ".join(failures))
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

    if args.follow:
        return follow.run(cfg, Path(args.follow), args.publish, args.no_upload)

    if todays_kind(cfg, args) == "shorts":
        return run_shorts_day(cfg, args)

    n_shorts = 0 if args.no_shorts else int(cfg.get_path("format.short.per_day", 2))
    candidates = candidates_for(cfg, args, 4 + n_shorts)
    story = candidates[0]
    if do_gates and cfg.get_path("gates.topic_approval", False):
        story = gate.topic_gate(cfg, candidates)
    if story is None:
        log.info("No topic approved. Stopping — nothing produced.")
        return 0
    company = story["topic"]

    workdir = ROOT / "output" / f"{datetime.now():%Y-%m-%d}-{slug(company)}"
    workdir.mkdir(parents=True, exist_ok=True)
    log.info("Producing %s [%s/%s] -> %s", company, story.get("region"),
             story.get("language"), workdir)

    meta = produce(cfg, story, workdir)
    shot_list = meta.pop("_shots")
    video = Path(meta["video"])
    # Shorts are their own stories (the next ones down today's list): they
    # outperformed cuts from the long video about five to one. A manual topic
    # has no other stories, so its Shorts are cut from the long video.
    short_stories = [c for c in candidates if c["topic"] != company][:n_shorts]

    if args.no_upload:
        if n_shorts and not short_stories:
            shorts.build_all(cfg, video, shot_list, workdir, company, n_shorts,
                             meta["language"])
        log.info("Built %s (upload skipped)", video)
        if short_stories:
            make_shorts(cfg, args, short_stories)
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
    playlist = cfg.get_path(f"upload.playlists.{meta['language']}") \
        or cfg.get_path("upload.default_playlist", "")
    if playlist:
        upload.add_to_playlist(video_id, playlist)

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
        return make_shorts(cfg, args, short_stories)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--topic")
    ap.add_argument("--angle", default="")
    ap.add_argument("--region", help="IN, US, CN, RU or WORLD (with --topic)")
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
