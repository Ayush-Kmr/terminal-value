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
