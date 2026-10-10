#!/usr/bin/env python3
"""Terminal Value — documentary pipeline.

    python main.py                          full run: trending topic -> video -> upload
    python main.py --topic "RBI repo rate" --region IN    make a video on a given story
    python main.py --publish now            publish immediately instead of the next slot
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

from src import (assets, gate, hooks, reach, render, research, script as script_mod,
                 seo, shorts, state, thumbnail, upload, voice)
from src.config import ROOT, load

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s %(name)-18s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("pipeline")


def slug(text: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in text.lower()).strip("-")[:48]


def write_srt(shots: list[dict], out: Path) -> Path:
    """Captions from shot timings. Uploaded alongside the video — captions
    measurably help retention and they are free."""
    def stamp(t: float) -> str:
        h, rem = divmod(int(t), 3600)
        m, s = divmod(rem, 60)
        return f"{h:02d}:{m:02d}:{s:02d},{int((t % 1) * 1000):03d}"

    lines, t = [], 0.0
    for i, shot in enumerate(shots, 1):
        dur = float(shot.get("duration", 5))
        text = (shot.get("narration") or "").strip()
        if text:
            lines += [str(i), f"{stamp(t)} --> {stamp(t + dur)}", text, ""]
        t += dur
    out.write_text("\n".join(lines), encoding="utf-8")
    return out


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
    music = next((ROOT / "assets" / "music").glob("*.mp3"), None)
    video = render.concat_with_audio(cfg, clips, shot_list, workdir, music)

    # ---- packaging ------------------------------------------------------
    best_fact = next((f.get("claim", "") for f in fb.facts if f.get("is_surprising")), "")
    title_opts = seo.titles(cfg, company, angle, best_fact, language)
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

    srt = write_srt(shot_list, workdir / "captions.srt")

    backdrop = next((Path(s["asset"]) for s in shot_list
                     if s.get("visual_type") in {"photo", "ai_still"} and s.get("asset")
                     and Path(s["asset"]).suffix.lower() != ".mp4"), None)
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


def choose_story(cfg, args, do_gates: bool) -> dict | None:
    """A story dict: topic, angle, region, language (+ research hints)."""
    regions = {r["code"]: r for r in cfg.get_path("trending.regions", [])}
    if args.topic:
        region = (args.region or next(iter(regions), "IN")).upper()
        return {
            "topic": args.topic, "company": args.topic,
            "angle": args.angle or f"What is happening with {args.topic}, and why does it matter?",
            "region": region,
            "language": args.language or regions.get(region, {}).get("language", "en"),
            "search_query": args.topic, "wiki_title": "", "keywords": [args.topic],
        }

    if cfg.get_path("mode", "trending") == "trending":
        from src import trends
        candidates = trends.pick(cfg, n=4)
    else:
        candidates = topics_or_die(cfg)
        for c in candidates:
            c.setdefault("topic", c.get("company"))
            c.setdefault("language", cfg.get_path("channel.language", "en"))

    if do_gates and cfg.get_path("gates.topic_approval", False):
        return gate.topic_gate(cfg, candidates)
    return candidates[0]


def run(args) -> int:
    cfg = load()
    do_gates = not args.no_gates

    if args.reach:
        path = reach.report(cfg)
        print(path.read_text(encoding="utf-8"))
        return 0

    story = choose_story(cfg, args, do_gates)
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
    n_shorts = 0 if args.no_shorts else int(cfg.get_path("format.short.per_day", 2))

    if args.no_upload:
        if n_shorts:
            shorts.build_all(cfg, video, shot_list, workdir, company, n_shorts,
                             meta["language"])
        log.info("Built %s (upload skipped)", video)
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
    if n_shorts:
        built = shorts.build_all(cfg, video, shot_list, workdir, company, n_shorts,
                                 meta["language"], f"https://youtu.be/{video_id}")
        # Shorts go out after the long, at their own daily slots.
        after = publish_at or datetime.now(upload.ZoneInfo(
            cfg.get_path("channel.timezone", "Asia/Kolkata")))
        times = [t for t in upload.slots(cfg, "short", len(built) + 4) if t > after]
        for s, when in zip(built, times):
            try:
                sid = upload.upload(cfg, Path(s["path"]),
                                    {"title": s["title"][:100], "language": meta["language"],
                                     "description": s["description"], "tags": meta["tags"]},
                                    privacy="private", publish_at=when)
                short_ids.append(sid)
            except Exception as exc:  # noqa: BLE001
                log.warning("Short upload failed: %s", exc)

    st = state.load()
    state.record_episode(st, {
        "company": company, "angle": meta["angle"], "title": meta["title"],
        "region": meta["region"], "language": meta["language"],
        "video_id": video_id, "short_ids": short_ids,
        "runtime": meta["runtime"], "warnings": meta["warnings"],
    })
    state.save(st)

    yt_status = upload.status(video_id)
    locked = decision == "publish" and yt_status.get("privacyStatus") != "public"
    lines = [
        f"✅ {meta['title']}",
        f"https://youtu.be/{video_id}",
        f"{meta['region']} · {meta['language']} · {meta['runtime']} · {len(short_ids)} Short(s) queued",
        (f"Goes public {publish_at:%a %d %b %H:%M}" if publish_at else "Published now"),
    ]
    if locked:
        lines.append("⚠️ YouTube kept it PRIVATE (status: "
                     f"{yt_status.get('privacyStatus')}). New API projects are locked to "
                     "private until Google approves the API audit form.")
    if meta["warnings"]:
        lines.append("Research warnings: " + "; ".join(meta["warnings"]))
    gate.notify("\n".join(lines), markdown=False)
    log.info("YouTube status for %s: %s", video_id, yt_status)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--topic")
    ap.add_argument("--angle", default="")
    ap.add_argument("--region", help="IN, US, CN, RU or WORLD (with --topic)")
    ap.add_argument("--language", choices=["hi", "en"], help="override narration language")
    ap.add_argument("--publish", choices=["slot", "now"], default="slot",
                    help="slot: schedule for the next publish time; now: public immediately")
    ap.add_argument("--no-upload", action="store_true")
    ap.add_argument("--no-gates", action="store_true")
    ap.add_argument("--no-shorts", action="store_true")
    ap.add_argument("--reach", action="store_true")
    args = ap.parse_args()

    try:
        return run(args)
    except Exception as exc:  # noqa: BLE001
        log.exception("Run failed")
        gate.notify(f"❌ Terminal Value run failed: {type(exc).__name__}: {str(exc)[:500]}",
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
