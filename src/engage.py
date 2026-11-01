"""After upload: the question under each video, and other languages.

A comment cannot go on a video until it is public, and uploads wait for their
slot, so questions are saved with the episode and posted by the next run. None
of this can lose a video: every failure is logged and skipped.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from . import llm, state, upload
from .config import Config

log = logging.getLogger(__name__)
BATCH = 30


def post_questions(cfg: Config, limit: int = 6) -> int:
    st = state.load()
    waiting = [e for e in st.get("episodes", [])
               if e.get("question") and e.get("video_id") and not e.get("commented")][:limit]
    if not waiting:
        return 0
    posted = 0
    try:
        yt = upload.client()
        channel = cfg.get_path("channel.channel_id")
        items = yt.videos().list(part="status",
                                 id=",".join(e["video_id"] for e in waiting)).execute()
        public = {v["id"] for v in items.get("items", [])
                  if v["status"]["privacyStatus"] == "public"}
        for e in waiting:
            if e["video_id"] not in public:
                continue
            text = e["question"].strip()
            if e.get("parent_url"):
                label = cfg.get_path("labels.full_story", "Full story")
                text += f"\n\n{label}: {e['parent_url']}"
            yt.commentThreads().insert(part="snippet", body={"snippet": {
                "channelId": channel, "videoId": e["video_id"],
                "topLevelComment": {"snippet": {"textOriginal": text}}}}).execute()
            e["commented"] = True
            posted += 1
        state.save(st)
    except Exception as exc:  # noqa: BLE001
        log.warning("Posting questions stopped: %s", str(exc)[:200])
        state.save(st)
    log.info("Questions posted under %d video(s)", posted)
    return posted


LOCALISE_PROMPT = """Translate this YouTube video's title and summary into {name}, for
ordinary viewers. Keep names of companies and people as {name} speakers write them.
Keep any figures unchanged. Title under 95 characters.

Title: {title}
Summary: {summary}

Return JSON: {{"title": "...", "description": "..."}}"""

LINES_PROMPT = """Translate these subtitle lines from English into simple, natural {name}.
Keep exactly one translated line for each line, in order, with figures unchanged.

{lines}

Return JSON: {{"lines": ["<line 1>", ...]}} with exactly {n} strings."""


def _srt_lines(text: str) -> list[list[str]]:
    return [b.split("\n") for b in text.strip().split("\n\n") if b.count("\n") >= 2]


def _subtitles(cfg: Config, video_id: str, srt: Path, code: str, name: str) -> None:
    blocks = _srt_lines(srt.read_text(encoding="utf-8"))
    lines = [" ".join(b[2:]) for b in blocks]
    out: list[str] = []
    for start in range(0, len(lines), BATCH):
        part = lines[start:start + BATCH]
        for _ in range(3):
            got = llm.json_call(LINES_PROMPT.format(
                name=name, n=len(part),
                lines="\n".join(f"{i + 1}. {t}" for i, t in enumerate(part))), temperature=0.2)
            got = got.get("lines") if isinstance(got, dict) else got
            if isinstance(got, list) and len(got) == len(part):
                break
        else:
            raise RuntimeError("subtitle batch came back the wrong length")
        out += [re.sub(r"\s+", " ", str(t)).strip() for t in got]
    path = srt.with_suffix(f".{code}.srt")
    path.write_text("\n".join(f"{b[0]}\n{b[1]}\n{t}\n" for b, t in zip(blocks, out)),
                    encoding="utf-8")
    upload.upload_captions(video_id, path, code)


def localise(cfg: Config, video_id: str, title: str, summary: str,
             srt: Path | None = None) -> None:
    """Translated title and summary, plus subtitles when `srt` is given."""
    languages = cfg.get_path("localize.languages", {}) or {}
    if not languages:
        return
    try:
        yt = upload.client()
        local = {}
        for code, name in languages.items():
            got = llm.json_call(LOCALISE_PROMPT.format(name=name, title=title[:100],
                                                       summary=summary[:800]), temperature=0.3)
            if isinstance(got, dict) and got.get("title"):
                local[code] = {"title": upload._safe(got["title"], 300)[:100],
                               "description": upload._safe(got.get("description", ""), 2000)}
        video = yt.videos().list(part="snippet", id=video_id).execute()["items"][0]
        snippet = {k: video["snippet"][k] for k in ("title", "categoryId", "description", "tags",
                                                    "defaultLanguage", "defaultAudioLanguage")
                   if k in video["snippet"]}
        snippet.setdefault("defaultLanguage", cfg.get_path("channel.language", "en"))
        yt.videos().update(part="snippet,localizations", body={
            "id": video_id, "snippet": snippet, "localizations": local}).execute()
        log.info("Translated title added: %s", ", ".join(local))
        if srt:
            for code, name in languages.items():
                _subtitles(cfg, video_id, srt, code, name)
    except Exception as exc:  # noqa: BLE001
        log.warning("Translations stopped: %s", str(exc)[:200])
