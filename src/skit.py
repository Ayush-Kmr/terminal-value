"""Bhojpuri comedy skits: the whole pipeline of the Bhojpuri channel (CHANNEL=bh).

    CHANNEL=bh python main.py --skit                 make today's skits, upload at the slots
    CHANNEL=bh python main.py --skit --no-upload     build only

One skit = one vertical Short of 35-45 s: three recurring cartoon characters
(skit.characters in channels/bh.yaml) in a short dialogue that ends on a
punchline. Per line: a cartoon picture of the speaker (Cloudflare FLUX, with
the character's full look in every prompt so they stay recognisable), the line
in that character's own voice (Indic Parler-TTS), and big word captions.
Everything is free: the free LLM chain, the free image tier, an open voice
model on the runner.
"""
from __future__ import annotations

import base64
import json
import logging
import random
import re
from datetime import datetime
from pathlib import Path

import requests
import yaml

from . import captions, gate, llm, render, shorts, state, upload, voice
from .config import ROOT, Config, env, portrait

log = logging.getLogger(__name__)

IMAGE_MODEL = "@cf/black-forest-labs/flux-1-schnell"

PROMPT = """You write short, very funny comedy skits in BHOJPURI for a YouTube
Shorts channel. The same three cartoon characters appear in every video:

{cast}

Today's premise: {premise}
Characters in this skit: {in_skit}

Write ONE skit of {lo}-{hi} dialogue lines, {max_words} words in all at most.
- LANGUAGE: real, natural spoken Bhojpuri as people in Bhojpur, Ara, Buxar,
  Chhapra and Siwan talk at home (बा, बानी, बाड़ऽ, बाड़ी, रउआ, तोहार, हमार, काहे,
  केतना, कइसे, जाइब, खाइब, का हो, ए बबुआ, अरे बाप रे), NOT Hindi. Devanagari
  script only: no English letters at all. English words people really use
  (मोबाइल, फोन, सेल्फी, वाईफाई, ऑनलाइन) are written in Devanagari. Write numbers in words.
- Each line: one character speaks, at most {max_line_words} words, one or two short
  sentences, so the picture changes every few seconds.
- LINE 1 starts the conflict at once (a viewer decides in 2 seconds whether to
  keep watching). No greeting, no introduction, no narrator.
- The comedy comes from the characters: Chacha is confidently wrong, Chachi
  always wins, Bablu has a shortcut. Exaggeration, misunderstanding, timing.
- The LAST LINE is a surprise punchline twist that makes people laugh out loud.
- Clean family comedy. NEVER: vulgarity or double meanings, insults about caste,
  religion, region, skin colour, body or gender, violence, politics, real people,
  celebrities, brands or anything sad.

Return a JSON object:
  title     - a catchy, funny Bhojpuri title in Devanagari, at most 40 characters,
              that makes people curious (e.g. "चाचा के पहिला स्मार्टफोन")
  hook      - on-screen headline in Devanagari, 2-5 words
  setting   - in ENGLISH: where it happens, as a picture (e.g. "the courtyard
              of a village house in Bihar with a buffalo tied to a post, sunny afternoon")
  lines     - array of objects:
                speaker - one of: {keys}
                text    - the line, Bhojpuri, Devanagari only
                action  - in ENGLISH: what the speaker is doing and their facial
                          expression in this moment, as a picture (e.g. "holding a
                          phone far from his face, squinting, shocked")
                with    - optional: key of one other character visible in the picture
  summary   - one funny sentence in Bhojpuri (Devanagari) for the description
  tags      - 6 short English or Hinglish search tags (e.g. "chacha bhatija comedy")
{feedback}"""


# ---------------------------------------------------------------- ideas
def _ideas(cfg: Config) -> list[dict]:
    path = ROOT / cfg.get_path("skit.ideas", "data/bhojpuri_ideas.yaml")
    if not path.exists():
        return []
    return (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("ideas", [])


def pick_ideas(cfg: Config, n: int, topic: str = "") -> list[dict]:
    """Today's premises: a given one, else unused ideas (this month's seasonal
    ones first), else premises the model invents."""
    keys = list(cfg.get_path("skit.characters", {}))
    if topic:
        return [{"id": f"manual-{datetime.now():%Y%m%d%H%M}", "premise": topic, "cast": keys}]
    used = {e.get("idea") for e in state.load().get("episodes", [])}
    month = datetime.now().month
    # Out-of-season ideas wait for their months; in-season ones go first.
    fresh = [i for i in _ideas(cfg) if i["id"] not in used
             and (not i.get("months") or month in i["months"])]
    random.Random(datetime.now().strftime("%Y%m%d")).shuffle(fresh)
    fresh.sort(key=lambda i: not i.get("months"))            # stable: seasonal first
    out = fresh[:n]
    while len(out) < n:
        out.append(invent(cfg, [*[i["premise"] for i in out], *recent_titles()]))
    return out


def recent_titles(k: int = 30) -> list[str]:
    return [e.get("title", "") for e in state.load().get("episodes", [])[-k:]]


def invent(cfg: Config, avoid: list[str]) -> dict:
    """A new premise once the idea bank is used up."""
    keys = list(cfg.get_path("skit.characters", {}))
    cast = "\n".join(f"- {c['name']}: {c['role'].strip()}"
                     for c in cfg.get_path("skit.characters", {}).values())
    got = llm.json_call(
        "Invent ONE new premise for a 40-second family comedy skit set in a village "
        f"house in Bihar, with these characters:\n{cast}\n\nIt must be about everyday "
        "life (phones, the internet, food, festivals, weather, relatives, money, school) "
        "and clearly different from these earlier ones:\n- " + "\n- ".join(avoid[-40:]) +
        f"\n\nReturn JSON: {{\"premise\": \"one English sentence\", \"cast\": [two or three of "
        f"{keys}]}}", temperature=1.0)
    cast_keys = [c for c in (got.get("cast") or keys) if c in keys] or keys
    return {"id": f"invented-{datetime.now():%Y%m%d%H%M%S}-{random.randint(100, 999)}",
            "premise": str(got["premise"]), "cast": cast_keys}


# --------------------------------------------------------------- script
LATIN = re.compile(r"[A-Za-z]")


def problems(cfg: Config, sk: dict, cast: list[str]) -> list[str]:
    """What is wrong with a draft, in words the model can act on."""
    lo, hi = cfg.get_path("skit.lines", [6, 9])
    lines = sk.get("lines") or []
    out = []
    if not lo <= len(lines) <= hi:
        out.append(f"it has {len(lines)} lines, it must have {lo}-{hi}")
    words = sum(len(str(x.get("text", "")).split()) for x in lines)
    if words > int(cfg.get_path("skit.max_words", 85)):
        out.append(f"it has {words} words, the limit is {cfg.get_path('skit.max_words', 85)}")
    long_ = [i + 1 for i, x in enumerate(lines)
             if len(str(x.get("text", "")).split()) > int(cfg.get_path("skit.max_line_words", 14))]
    if long_:
        out.append(f"lines {long_} are too long")
    bad = sorted({str(x.get("speaker")) for x in lines} - set(cast))
    if bad:
        out.append(f"unknown speakers {bad}: use only {cast}")
    latin = [i + 1 for i, x in enumerate(lines) if LATIN.search(str(x.get("text", "")))]
    if latin:
        out.append(f"lines {latin} contain English letters: write them in Devanagari")
    if not str(sk.get("title", "")).strip() or LATIN.search(str(sk.get("title", ""))):
        out.append("the title must be Devanagari only")
    return out


def write(cfg: Config, idea: dict) -> dict:
    chars = cfg.get_path("skit.characters", {})
    cast = [c for c in idea.get("cast") or list(chars) if c in chars] or list(chars)
    lo, hi = cfg.get_path("skit.lines", [6, 9])
    feedback = ""
    sk: dict = {}
    for attempt in range(3):
        sk = llm.json_call(PROMPT.format(
            cast="\n".join(f"- {k}: {c['name']} ({c['name_bho']}): {c['role'].strip()}"
                           for k, c in chars.items()),
            premise=idea["premise"], in_skit=", ".join(chars[c]["name"] for c in cast),
            lo=lo, hi=hi, max_words=cfg.get_path("skit.max_words", 85),
            max_line_words=cfg.get_path("skit.max_line_words", 14),
            keys=", ".join(cast), feedback=feedback), temperature=0.9)
        if not isinstance(sk, dict):
            sk = {}
        wrong = problems(cfg, sk, cast)
        if not wrong:
            return sk
        log.info("Skit draft %d rejected: %s", attempt + 1, "; ".join(wrong))
        feedback = ("\nYOUR LAST DRAFT WAS REJECTED BECAUSE " + "; ".join(wrong).upper()
                    + ". FIX THAT.")
    # The last draft, cleaned: unknown speakers fall back to the lead.
    for x in sk.get("lines") or []:
        if x.get("speaker") not in cast:
            x["speaker"] = cast[0]
    if not sk.get("lines"):
        raise RuntimeError(f"No usable skit for {idea['id']}")
    log.warning("Using an imperfect skit draft: %s", "; ".join(problems(cfg, sk, cast)))
    return sk


# --------------------------------------------------------------- pictures
def _cloudflare(prompt: str, dest: Path) -> bool:
    account, token = env("CF_ACCOUNT_ID"), env("CF_API_TOKEN")
    if not (account and token):
        return False
    for _ in range(3):
        try:
            r = requests.post(
                f"https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{IMAGE_MODEL}",
                headers={"Authorization": f"Bearer {token}"},
                json={"prompt": prompt[:2000]}, timeout=90)    # FLUX schnell: prompt only
            if r.status_code >= 400:
                log.warning("Cloudflare HTTP %s: %s", r.status_code, r.text[:200])
                continue
            image = (r.json().get("result") or {}).get("image")
            if image:
                dest.write_bytes(base64.b64decode(image))
                return dest.stat().st_size > 10_000
        except Exception as exc:  # noqa: BLE001
            log.warning("Cloudflare image failed: %s", exc)
    return False


def tall_frame(src: Path, dest: Path) -> Path:
    """A 1080x1920 frame from any picture: a square one sits in the middle over
    a blurred, enlarged copy of itself, so no character is cropped away."""
    from PIL import Image, ImageFilter

    img = Image.open(src).convert("RGB")
    w, h = img.size
    if h / w > 1.6:                                  # already tall
        img.resize((1080, int(1080 * h / w))).crop((0, 0, 1080, 1920)).save(dest, quality=94)
        return dest
    back = img.resize((int(1920 * w / h), 1920)).filter(ImageFilter.GaussianBlur(28))
    left = (back.width - 1080) // 2
    back = back.crop((left, 0, left + 1080, 1920))
    back = Image.eval(back, lambda v: int(v * 0.72))
    front = img.resize((1080, int(1080 * h / w)))
    back.paste(front, (0, 1920 * 44 // 100 - front.height // 2))
    back.save(dest, quality=94)
    return dest


def picture(cfg: Config, sk: dict, line: dict, i: int, workdir: Path) -> Path | None:
    chars = cfg.get_path("skit.characters", {})
    who = chars.get(line.get("speaker"), {})
    other = chars.get(line.get("with")) if line.get("with") != line.get("speaker") else None
    prompt = (f"{who.get('look', '').strip()}, {line.get('action', '')}"
              + (f", next to {other['look'].strip()}" if other else "")
              + f". Setting: {sk.get('setting', 'a village house in Bihar')}. "
              + cfg.get_path("skit.style", "").strip())
    raw = workdir / "assets" / f"scene_{i:03d}_raw.png"
    raw.parent.mkdir(parents=True, exist_ok=True)
    # No keyless fallback: tested 2026-11-08, Pollinations ignored the prompt,
    # stamped a watermark and drew a face like a real politician. A line whose
    # picture fails keeps the previous line's picture instead.
    if not _cloudflare(prompt, raw):
        return None
    return tall_frame(raw, raw.with_name(f"scene_{i:03d}.jpg"))


# --------------------------------------------------------------- build
def build(cfg: Config, idea: dict, workdir: Path) -> dict:
    """One finished skit Short on disk, with its upload metadata."""
    tall = portrait(cfg)
    tall["visuals"]["shot_seconds"] = cfg.get_path("format.short.shot_seconds", [1.5, 7])
    chars = cfg.get_path("skit.characters", {})
    yt_lang = cfg.get_path("channel.youtube_language", "hi")

    sk = write(cfg, idea)
    (workdir / "script.json").write_text(
        json.dumps({"idea": idea, **sk}, indent=2, ensure_ascii=False),
        encoding="utf-8")
    log.info("Skit: %s (%d lines)", sk.get("title"), len(sk["lines"]))

    shots, last_pic = [], None
    audio_dir = workdir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    for i, line in enumerate(sk["lines"]):
        pic = picture(cfg, sk, line, i, workdir) or last_pic
        if pic is None:
            raise RuntimeError("No picture could be made for the first line")
        last_pic = pic
        # The character's voice, picked up by voice._indic_batch.
        (audio_dir / f"shot_{i:03d}.desc").write_text(
            chars[line["speaker"]]["voice"].strip(), encoding="utf-8")
        shots.append({"index": i, "beat": "skit", "narration": str(line["text"]).strip(),
                      "visual_type": "ai_still", "asset": str(pic),
                      "speaker": line["speaker"]})
    shots = voice.narrate_shots(tall, shots, workdir, None, yt_lang)
    clips = render.build_shot_clips(tall, shots, workdir)
    seed = idea["id"]
    video = render.concat_with_audio(tall, clips, shots, workdir, render.pick_music(cfg, seed))
    out = shorts.finish(cfg, video, workdir / "short.mp4", str(sk.get("hook") or sk["title"]),
                        shots=shots)

    in_skit = list(dict.fromkeys(s["speaker"] for s in shots))
    phrases = cfg.get_path("skit.search_phrases", ["Bhojpuri Comedy"])
    phrase = phrases[len(state.load().get("episodes", [])) % len(phrases)]
    title = f"{str(sk['title']).strip()} 😂 | {phrase}"
    if len(title) > 90:
        title = f"{str(sk['title']).strip()[:60]} 😂 | {phrase}"
    names = ", ".join(f"{chars[c]['name_bho']} ({chars[c]['name']})" for c in in_skit)
    tags = list(dict.fromkeys([*(str(t) for t in sk.get("tags") or []),
                               *[p.lower() for p in phrases],
                               *(chars[c]["name"].lower() for c in in_skit),
                               "funny video", "comedy shorts", "bhojpuri"]))[:20]
    hashtags = "#bhojpuricomedy #comedy #funny #shorts"
    description = "\n\n".join(p for p in [
        str(sk.get("summary", "")).strip(),
        f"किरदार: {names}",
        "\n".join([cfg.get_path("channel.cta", "").strip(),
                   *[str(x) for x in cfg.get_path("channel.links", []) or []]]),
        render.music_credit(cfg, seed),
        cfg.get_path("channel.disclosure", "").strip(),
        hashtags,
    ] if p)
    srt = captions.srt(shots, workdir / "captions.srt")
    meta = {"title": title[:100], "description": description, "tags": tags,
            "language": yt_lang, "path": str(out), "captions": str(srt),
            "idea": idea["id"], "premise": idea["premise"], "characters": in_skit,
            "runtime": round(sum(float(s["duration"]) for s in shots), 1)}
    (workdir / "metadata.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("Skit built: %s (%.1f s)", out, meta["runtime"])
    return meta


def slug(text: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in text.lower()).strip("-")[:48]


def run(cfg: Config, publish: str = "slot", no_upload: bool = False, count: int = 0,
        topic: str = "") -> int:
    """Today's skits, each at its own publish slot. A skit that fails gives
    way to a spare idea, so one bad draft does not lose a slot."""
    n = count or int(cfg.get_path("skit.per_day", 2))
    queue = pick_ideas(cfg, n + 2, topic)
    times = upload.slots(cfg, "short", n)
    st = state.load()
    done, failures = [], []
    while queue and len(done) < n:
        idea, i = queue.pop(0), len(done)
        workdir = ROOT / "output" / f"{datetime.now():%Y-%m-%d}-skit-{slug(idea['id'])}"
        workdir.mkdir(parents=True, exist_ok=True)
        log.info("Skit %d/%d: %s", i + 1, n, idea["premise"][:100])
        try:
            meta = build(cfg, idea, workdir)
            if no_upload:
                done.append(f"{meta['title']} (built, not uploaded)")
                continue
            now = publish == "now"
            when = None if now else times[i] if i < len(times) else None
            sid = upload.upload(cfg, Path(meta["path"]), meta,
                                privacy="public" if now else "private", publish_at=when)
            upload.upload_captions(sid, Path(meta["captions"]), meta["language"])
            upload.add_to_playlists(cfg, sid, meta["language"], " ".join(meta["characters"]))
            state.record_episode(st, {
                "company": idea["premise"][:80], "title": meta["title"], "idea": idea["id"],
                "characters": meta["characters"], "language": "bho", "video_id": sid,
                "kind": "short", "runtime": meta["runtime"]})
            state.save(st)
            gate.notify("\n".join([
                f"😂 Bhojpuri skit: {meta['title']}", f"https://youtu.be/{sid}",
                "Published now" if now else f"Goes public {when:%a %d %b %H:%M}"]),
                markdown=False)
            done.append(meta["title"])
        except voice.VoiceError:
            raise                       # the next skit would fail the same way
        except Exception as exc:  # noqa: BLE001
            log.exception("Skit %s failed", idea["id"])
            failures.append(f"{idea['id']}: {type(exc).__name__}: {str(exc)[:200]}")
    if len(done) < n:
        raise RuntimeError(f"{n - len(done)} of {n} skits not made — " + " | ".join(failures))
    if failures:
        gate.notify("⚠️ Bhojpuri: replaced a failed skit with a spare idea: "
                    + " | ".join(failures), markdown=False)
    log.info("Skits done: %s", "; ".join(done))
    return 0
