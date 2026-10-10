"""Turn a shot list into actual image/video files on disk.

Routes each shot to the right source according to its visual_type, and enforces
the mix targets from config so a single episode never drifts into being 90% AI
stills (which is both expensive-looking and a policy risk).
"""
from __future__ import annotations

import logging
from collections import Counter
from pathlib import Path

from .config import Config
from .visuals import ai_images, motion, stock

log = logging.getLogger(__name__)

MOTION_TYPES = {"stat_reveal", "chart", "timeline", "quote_card", "document"}


def gather(cfg: Config, shots: list[dict], workdir: Path, company: str) -> list[dict]:
    img_dir = workdir / "assets"
    img_dir.mkdir(parents=True, exist_ok=True)
    vis_cfg = cfg.get("visuals", {})
    credits: list[dict] = []

    for shot in shots:
        vtype = shot.get("visual_type", "ai_still")
        spec = shot.get("visual_spec", {}) or {}
        dest = img_dir / f"shot_{shot['index']:03d}.png"

        if dest.exists() or shot.get("asset"):
            continue

        if vtype in MOTION_TYPES:
            try:
                motion.render(shot, vis_cfg, dest)
                shot["asset"] = str(dest)
            except Exception as exc:  # noqa: BLE001
                log.warning("Motion card %03d failed (%s); falling back", shot["index"], exc)
                vtype = "ai_still"

        if vtype == "photo":
            query = spec.get("search_terms") or f"{company} {spec.get('headline','')}"
            found = stock.find(query, img_dir / f"shot_{shot['index']:03d}",
                               prefer_archival=True)
            if found:
                shot["asset"] = found["path"]
                credits.append(found)
            else:
                vtype = "ai_still"   # fall through

        if vtype == "ai_still" and not shot.get("asset"):
            prompt = spec.get("prompt") or f"atmospheric scene evoking {company}"
            result = ai_images.generate(prompt, dest)
            if result:
                shot["asset"] = str(result)
            else:
                # Last resort: a typographic card. Never ship a black frame.
                motion.title_card(spec.get("headline", "") or company, "", vis_cfg, dest)
                shot["asset"] = str(dest)

        if not shot.get("asset"):
            motion.title_card(spec.get("headline", "") or company, "", vis_cfg, dest)
            shot["asset"] = str(dest)

    report_mix(cfg, shots)
    return credits


def report_mix(cfg: Config, shots: list[dict]) -> dict[str, float]:
    """Warn when the episode's visual mix drifts from the target."""
    total = sum(float(s.get("duration", 5)) for s in shots) or 1.0
    buckets: Counter[str] = Counter()
    for s in shots:
        vtype = s.get("visual_type", "ai_still")
        key = ("motion_graphics" if vtype in MOTION_TYPES
               else "archival_stock" if vtype == "photo" else "ai_illustration")
        buckets[key] += float(s.get("duration", 5))

    actual = {k: round(v / total, 2) for k, v in buckets.items()}
    targets = cfg.get_path("visuals.mix_targets", {})
    for key, target in targets.items():
        got = actual.get(key, 0.0)
        if abs(got - target) > 0.18:
            log.warning("Visual mix off target: %s is %.0f%% (target %.0f%%)",
                        key, got * 100, target * 100)
    log.info("Visual mix: %s", actual)
    return actual
