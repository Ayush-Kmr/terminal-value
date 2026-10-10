#!/usr/bin/env python3
"""Terminal Value — documentary pipeline.

    python main.py                          full run: topic -> gate -> video -> gate -> upload
    python main.py --topic "Jawbone"        skip topic selection
    python main.py --no-upload              build only, leave the file on disk
    python main.py --no-gates               skip human approval (NOT recommended)
    python main.py --shorts-only <dir>      re-cut Shorts from an existing render
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


def produce(cfg, company: str, angle: str, workdir: Path, do_gates: bool) -> dict:
    # ---- research -------------------------------------------------------
    fb = research.build(cfg, company, angle)
    warnings = research.validate(cfg, fb)
    (workdir / "research.json").write_text(
        json.dumps(fb.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")

    # ---- hook -----------------------------------------------------------
    hook = hooks.generate_and_score(company, angle, fb.facts)
    log.info("Hook (%d): %s", hook["winner"]["final_score"], hook["winner"]["text"][:110])

    # ---- script ---------------------------------------------------------
    sc = script_mod.write(cfg, fb, hook["winner"]["text"])
    (workdir / "script.json").write_text(
        json.dumps(sc, indent=2, ensure_ascii=False), encoding="utf-8")
    shot_list = script_mod.all_shots(sc)

    # ---- narration (sets real shot durations) ---------------------------
    shot_list = voice.narrate_shots(cfg, shot_list, workdir)
    log.info("Estimated voice cost: $%.2f", voice.estimate_cost(sc["full_narration"]))

    # ---- visuals --------------------------------------------------------
    credits = assets.gather(cfg, shot_list, workdir, company)

    # ---- render ---------------------------------------------------------
    clips = render.build_shot_clips(cfg, shot_list, workdir)
    music = next((ROOT / "assets" / "music").glob("*.mp3"), None)
    video = render.concat_with_audio(cfg, clips, shot_list, workdir, music)

    # ---- packaging ------------------------------------------------------
    best_fact = next((f.get("claim", "") for f in fb.facts if f.get("is_surprising")), "")
    title_opts = seo.titles(cfg, company, angle, best_fact)
    title = title_opts[0]["title"]
    chapter_text = seo.chapters(shot_list, sc["beats"])
    desc = seo.description(cfg, title, company, angle, fb.summary, chapter_text,
                           [s if isinstance(s, dict) else s.__dict__ for s in fb.sources])
    if credits:
        attribution = "\n".join(
            f"- {c['credit']} ({c['licence']}) {c.get('url','')}"
            for c in credits if c.get("needs_attribution"))
        if attribution:
            desc += "\n\nIMAGE CREDITS\n" + attribution

    srt = write_srt(shot_list, workdir / "captions.srt")

    backdrop = next((Path(s["asset"]) for s in shot_list
                     if s.get("visual_type") == "photo" and s.get("asset")), None)
    thumb = thumbnail.generate(
        cfg,
        {"company": company,
         "thumb_figure": title_opts[0].get("thumb_pairing", "")[:7],
         "thumb_words": title_opts[0].get("thumb_pairing", company)},
        backdrop, workdir / "thumbnail.jpg")

    runtime = sum(s["duration"] for s in shot_list)
    meta = {
        "company": company, "angle": angle, "title": title,
        "title_options": title_opts, "description": desc,
        "tags": seo.tags(company, angle), "chapters": chapter_text,
        "runtime": f"{int(runtime//60)}:{int(runtime%60):02d}",
        "word_count": sc["word_count"], "warnings": warnings,
        "video": str(video), "thumbnail": str(thumb), "captions": str(srt),
    }
    (workdir / "metadata.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

    meta["_shots"] = shot_list
    return meta


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--topic")
    ap.add_argument("--angle", default="")
    ap.add_argument("--no-upload", action="store_true")
    ap.add_argument("--no-gates", action="store_true")
    ap.add_argument("--no-shorts", action="store_true")
    ap.add_argument("--reach", action="store_true")
    ap.add_argument("--shorts-only")
    args = ap.parse_args()

    cfg = load()
    do_gates = not args.no_gates

    if args.reach:
        path = reach.report(cfg)
        print(path.read_text(encoding="utf-8"))
        return 0

    # ---- choose the topic ------------------------------------------------
    if args.topic:
        company, angle = args.topic, args.angle or f"What really ended {args.topic}."
    else:
        candidates = topics_or_die(cfg)
        if do_gates:
            chosen = gate.topic_gate(cfg, candidates)
            if chosen is None:
                log.info("No topic approved. Stopping — nothing produced.")
                return 0
        else:
            chosen = candidates[0]
        company, angle = chosen["company"], chosen.get("angle", "")

    workdir = ROOT / "output" / f"{datetime.now():%Y-%m-%d}-{slug(company)}"
    workdir.mkdir(parents=True, exist_ok=True)
    log.info("Producing %s -> %s", company, workdir)

    meta = produce(cfg, company, angle, workdir, do_gates)
    shot_list = meta.pop("_shots")
    video = Path(meta["video"])

    if args.no_upload:
        log.info("Built %s (upload skipped)", video)
        return 0

    # ---- gate 2 ----------------------------------------------------------
    decision = gate.review_gate(cfg, video, meta, meta["warnings"]) if do_gates else "schedule"
    if decision == "hold":
        log.info("Held at review. File is at %s", video)
        return 0

    publish_at = upload.next_slot(cfg, "long") if decision == "schedule" else None
    video_id = upload.upload(cfg, video, meta,
                             privacy="public" if decision == "publish" else "private",
                             publish_at=publish_at)
    upload.set_thumbnail(video_id, Path(meta["thumbnail"]))
    upload.upload_captions(video_id, Path(meta["captions"]))
    upload.add_to_playlist(video_id, cfg.get_path("upload.default_playlist", "Rise & Fall"))

    # ---- shorts ----------------------------------------------------------
    short_ids = []
    if not args.no_shorts:
        for s in shorts.build_all(cfg, video, shot_list, workdir, meta["company"]):
            try:
                sid = upload.upload(cfg, Path(s["path"]),
                                    {"title": s["title"][:100],
                                     "description": s["description"], "tags": meta["tags"]},
                                    privacy="private",
                                    publish_at=upload.next_slot(cfg, "short"))
                short_ids.append(sid)
            except Exception as exc:  # noqa: BLE001
                log.warning("Short upload failed: %s", exc)

    st = state.load()
    state.record_episode(st, {
        "company": meta["company"], "angle": meta["angle"], "title": meta["title"],
        "video_id": video_id, "short_ids": short_ids,
        "runtime": meta["runtime"], "warnings": meta["warnings"],
    })
    state.save(st)

    gate.notify(
        f"✅ *{meta['title']}*\nhttps://youtu.be/{video_id}\n"
        f"{len(short_ids)} Short(s) queued."
        + (f"\nScheduled for {publish_at:%a %d %b %H:%M}" if publish_at else "\nLive now.")
    )
    return 0


def topics_or_die(cfg):
    from src import topics
    candidates = topics.propose(cfg, n=6)
    if not candidates:
        raise RuntimeError("Topic generator returned nothing")
    return candidates


if __name__ == "__main__":
    sys.exit(main())
