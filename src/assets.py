"""Turn a shot list into actual image/video files on disk.

Routes each shot to the right source according to its visual_type, and enforces
the mix targets from config so a single episode never drifts into being 90% AI
stills (which is both expensive-looking and a policy risk).
"""
from __future__ import annotations

import logging
from collections import Counter
from pathlib import Path

from .config import Config, latin_only
from .visuals import ai_images, motion, stock

log = logging.getLogger(__name__)

MOTION_TYPES = {"stat_reveal", "chart", "timeline", "quote_card", "document"}


def _vis(cfg: Config) -> dict:
    return {**cfg.get("visuals", {}), "wordmark": cfg.get_path("channel.name", "")}


def redraw_cards(cfg: Config, shots: list[dict], img_dir: Path, company: str) -> None:
    """Draw the cards again for a different frame shape (a Short cut from a
    long video). Photographs and clips are reused as they are."""
    img_dir.mkdir(parents=True, exist_ok=True)
    vis_cfg = _vis(cfg)
    for shot in shots:
        dest = img_dir / f"shot_{shot['index']:03d}.png"
        asset = Path(shot["asset"]) if shot.get("asset") else None
        try:
            if shot.get("visual_type") in MOTION_TYPES:
                motion.render(shot, vis_cfg, dest)
                shot["asset"] = str(dest)
            elif asset is None or motion.frames_dir(asset):
                # A typographic fallback card, not a picture.
                motion.title_card((shot.get("visual_spec") or {}).get("headline", "") or company,
                                  "", vis_cfg, dest)
                shot["asset"] = str(dest)
        except Exception as exc:  # noqa: BLE001
            log.warning("Could not redraw card %03d (%s); keeping the original",
                        shot["index"], exc)


def gather(cfg: Config, shots: list[dict], workdir: Path, company: str) -> list[dict]:
    img_dir = workdir / "assets"
    img_dir.mkdir(parents=True, exist_ok=True)
    vis_cfg = _vis(cfg)
    credits: list[dict] = []

    for shot in shots:
        vtype = shot.get("visual_type", "ai_still")
        spec = shot.get("visual_spec", {}) or {}
        dest = img_dir / f"shot_{shot['index']:03d}.png"

        if dest.exists() or shot.get("asset"):
            continue

        # On-screen text must be drawable. A card left with nothing to show
        # becomes a still rather than an empty frame.
        for key in ("headline", "value", "caption"):
            if key in spec:
                spec[key] = latin_only(spec.get(key))
        if isinstance(spec.get("chart"), dict):
            for key in ("title", "y_label"):
                spec["chart"][key] = latin_only(spec["chart"].get(key, ""))
        for ev in spec.get("events") or []:
            if isinstance(ev, dict):
                ev["event"] = latin_only(ev.get("event", ""))
        shot["visual_spec"] = spec
        has_text = bool(spec.get("value") or spec.get("headline")
                        or spec.get("chart") or spec.get("events"))
        if vtype in MOTION_TYPES and not has_text:
            vtype = shot["visual_type"] = "ai_still"

        if vtype in MOTION_TYPES:
            try:
                motion.render(shot, vis_cfg, dest)
                shot["asset"] = str(dest)
            except Exception as exc:  # noqa: BLE001
                log.warning("Motion card %03d failed (%s); falling back", shot["index"], exc)
                vtype = "ai_still"

        if vtype == "photo":
            base = img_dir / f"shot_{shot['index']:03d}"
            # A named person or place comes from Wikimedia Commons or not at
            # all: a generic stock photo passed off as a real person is a lie.
            name = (spec.get("search_terms") or company).strip()
            found = (stock.wikimedia_image(name, base)
                     or stock.wikimedia_image(" ".join(name.split()[:3]), base))
            if not found and spec.get("stock_terms"):
                found = stock.find(spec["stock_terms"], base,
                                   allow_video=shot["index"] % 2 == 0)
            if found:
                shot["asset"] = found["path"]
                credits.append(found)
            else:
                vtype = "ai_still"   # fall through

        if vtype == "ai_still" and not shot.get("asset"):
            prompt = (spec.get("prompt") or spec.get("stock_terms")
                      or f"atmospheric scene evoking {company}")
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
