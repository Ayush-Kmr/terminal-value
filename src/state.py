"""Durable channel state: what we've covered, how it performed.

history.json is committed back to the repo by CI so tomorrow's topic picker
learns from yesterday's numbers.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .config import ROOT, channel_key

# Each channel keeps its own history: data/history.json for the main channel,
# data/history.<CHANNEL>.json for the others.
HISTORY = ROOT / "data" / (f"history.{channel_key()}.json" if channel_key() else "history.json")


def _blank() -> dict[str, Any]:
    return {"episodes": [], "rejected_topics": [], "channel_median_views": None}


def load() -> dict[str, Any]:
    if not HISTORY.exists():
        return _blank()
    try:
        return json.loads(HISTORY.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return _blank()


def save(state: dict[str, Any]) -> None:
    HISTORY.parent.mkdir(parents=True, exist_ok=True)
    HISTORY.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def covered_companies(state: dict[str, Any], cooldown_days: int) -> set[str]:
    """Companies we may not touch again yet."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=cooldown_days)
    out: set[str] = set()
    for ep in state.get("episodes", []):
        try:
            when = datetime.fromisoformat(ep["created_at"])
        except (KeyError, ValueError):
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        if when > cutoff:
            out.add(ep.get("company", "").strip().lower())
    out.update(t.strip().lower() for t in state.get("rejected_topics", []))
    return {c for c in out if c}


def record_episode(state: dict[str, Any], episode: dict[str, Any]) -> None:
    episode.setdefault("created_at", datetime.now(timezone.utc).isoformat())
    state.setdefault("episodes", []).append(episode)


def record_rejection(state: dict[str, Any], company: str) -> None:
    """A topic you said no to at gate 1 never comes back."""
    state.setdefault("rejected_topics", []).append(company)


def own_median_views(state: dict[str, Any]) -> float | None:
    """Our own median. An episode is an outlier against this, not against
    some absolute view count."""
    views = sorted(
        ep["stats"]["views"]
        for ep in state.get("episodes", [])
        if ep.get("stats", {}).get("views") is not None
    )
    if not views:
        return None
    mid = len(views) // 2
    return float(views[mid]) if len(views) % 2 else (views[mid - 1] + views[mid]) / 2.0
