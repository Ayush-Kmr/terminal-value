"""Configuration loading: config.yaml + .env, with light validation."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


class Config(dict):
    """dict with dotted access: cfg.get_path('format.long.target_minutes')."""

    def get_path(self, dotted: str, default: Any = None) -> Any:
        node: Any = self
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node


def load(path: Path | None = None) -> Config:
    path = path or ROOT / "config.yaml"
    with open(path, "r", encoding="utf-8") as fh:
        return Config(yaml.safe_load(fh))


def portrait(cfg: Config) -> Config:
    """The same configuration laid out 9:16, for Shorts."""
    import copy

    out = Config(copy.deepcopy(dict(cfg)))
    out.setdefault("visuals", {})["resolution"] = [1080, 1920]
    return out


def env(key: str, required: bool = False, default: str | None = None) -> str | None:
    val = os.getenv(key, default)
    if required and not val:
        raise RuntimeError(
            f"Missing required environment variable {key!r}. "
            f"Copy .env.example to .env and fill it in."
        )
    return val


def read_channel_doc(name: str) -> str:
    """voice.md / visual-identity.md are injected into prompts."""
    p = ROOT / "channel" / name
    return p.read_text(encoding="utf-8") if p.exists() else ""


PALETTE_DEFAULT = {
    "bg": "#0E1116",
    "surface": "#161B22",
    "ink": "#F2F4F7",
    "muted": "#8B949E",
    "accent": "#E8B931",
    "danger": "#D1485A",
}


LANGUAGE_RULES = {
    "hi": (
        "LANGUAGE: Write all NARRATION in Hindi, in Devanagari script. Use the natural "
        "spoken Hindi of an Indian news explainer: everyday words, with common English "
        "terms (budget, company, technology, court, visa) and proper names kept the way "
        "people actually say them, but spelled in Devanagari exactly as they sound "
        "(कंपनी, टेक्नोलॉजी, एफ़-35, आई ए एफ़, सी ई ओ): the narrator reads Devanagari only, so "
        "no Latin letters in narration. Write numbers as digits. "
        "ALL ON-SCREEN TEXT (headline, value, caption, chart title and labels, timeline "
        "events) must be in ENGLISH using Latin letters only: the renderer cannot draw "
        "Devanagari. Image search terms and image prompts are always in English."
    ),
    "en": (
        "LANGUAGE: Write narration in clear international English that a viewer in "
        "India, the US or anywhere else follows on first listen. Explain any local "
        "term the first time it appears. All on-screen text in English."
    ),
}


def language_rules(language: str) -> str:
    return LANGUAGE_RULES.get(language, LANGUAGE_RULES["en"])


def latin_only(text: str) -> str:
    """Drop text the fonts cannot draw (Devanagari etc.) instead of rendering
    empty boxes. Keeps Latin letters, digits and common symbols."""
    if not text:
        return ""
    if any(ord(c) > 0x024F and c not in "–—‘’“”…₹€£¥•·×−" for c in str(text)):
        return ""
    return str(text)
