"""Bhojpuri comedy skits: the whole pipeline of the Bhojpuri channel (CHANNEL=bh).

    CHANNEL=bh python main.py --skit                 make today's skits, upload at the slots
    CHANNEL=bh python main.py --skit --no-upload     build only

One skit = one vertical Short of 35-45 s: three recurring cartoon characters
(skit.characters in channels/bh.yaml) in a short dialogue that ends on a
punchline. Per line: a cartoon picture of the speaker (Cloudflare FLUX, with
the character's full look in every prompt so they stay recognisable), the line
in that character's own voice (Indic Parler-TTS), the picture talking along
with that voice and the speaker's name tag (src/animate.py), big word captions
timed from the take, and comedy sound effects (assets/sfx, scripts/make_sfx.py)
with a ba-dum-tss after the punchline.

Premises: one a day from what India is watching on YouTube right now (the
trending chart, videos with a million views or more; the subject only, never
the video or the real people in it), the rest from data/bhojpuri_ideas.yaml.
Everything is free: the free LLM chain, the free image tier, an open voice
model on the runner.
"""
from __future__ import annotations

import base64
import json
import logging
import random
import re
import shutil
import subprocess
from datetime import date, datetime, timedelta
from pathlib import Path

import requests
import yaml

from . import animate, captions, gate, llm, render, shorts, state, upload, voice
from .config import ROOT, Config, env, portrait

log = logging.getLogger(__name__)

IMAGE_MODEL = "@cf/black-forest-labs/flux-1-schnell"

PROMPT = """You write short, very funny comedy skits in BHOJPURI for a YouTube
Shorts channel. The same three cartoon characters appear in every video:

{cast}

Today's premise: {premise}
Characters in this skit: {in_skit}
{trend}
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
                sfx     - a comedy sound effect at the START of this line, or "none":
                          boing (something silly or a fall), pop (a sudden idea or
                          surprise), ding (a "smart" idea), gasp (shock or drama),
                          whoosh (someone rushes in). At most 3 lines get one.
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
    # An idea with dates runs only on those days (India time) and goes first;
    # out-of-season ideas wait for their months; in-season ones come next.
    today = datetime.now(upload.ZoneInfo(cfg.get_path("channel.timezone", "Asia/Kolkata")))
    day = today.date().isoformat()
    fresh = [i for i in _ideas(cfg) if i["id"] not in used
             and (not i.get("months") or month in i["months"])
             and (not i.get("dates") or day in [str(d) for d in i["dates"]])]
    random.Random(datetime.now().strftime("%Y%m%d")).shuffle(fresh)
    fresh.sort(key=lambda i: (not i.get("dates"), not i.get("months")))   # stable
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
# Models write the Bhojpuri lengthening mark ऽ as a Latin "s" (कsले, रहs).
AVAGRAHA = re.compile(r"(?<=[\u0900-\u097F])[sS](?![A-Za-z])")


def tidy(sk: dict) -> dict:
    """Fix the script habits a redraft never cures, before checking it."""
    for key in ("title", "hook", "summary"):
        if isinstance(sk.get(key), str):
            sk[key] = AVAGRAHA.sub("ऽ", sk[key]).strip()
    for x in sk.get("lines") or []:
        x["text"] = AVAGRAHA.sub("ऽ", str(x.get("text", ""))).strip()
    return sk


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
            trend=(f"This premise comes from what India is watching on YouTube right now "
                   f"({idea['keyword']}). Make that link obvious in the first two lines, "
                   "so people who know it get the joke, but never name or portray a real "
                   "person: say 'the hero', 'the cricket team', 'the new film'.\n"
                   if idea.get("keyword") else ""),
            lo=lo, hi=hi, max_words=cfg.get_path("skit.max_words", 85),
            max_line_words=cfg.get_path("skit.max_line_words", 14),
            keys=", ".join(cast), feedback=feedback), temperature=0.9)
        sk = tidy(sk) if isinstance(sk, dict) else {}
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
    # FLUX sometimes draws cinema letterbox bars: trim near-black edge rows.
    import numpy as np
    rows = np.asarray(img.convert("L")).mean(axis=1)
    lit = np.where(rows > 14)[0]
    if len(lit) and (lit[0] > 4 or lit[-1] < len(rows) - 5):
        img = img.crop((0, int(lit[0]), img.width, int(lit[-1]) + 1))
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
        json.dumps({"idea": idea, **sk}, indent=2, ensure_ascii=False, default=str),
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
    # Comic timing: a beat of silence before the punchline, and time after it
    # for the ba-dum-tss while the last picture holds.
    if len(shots) > 1:
        shots[-2]["duration"] = float(shots[-2]["duration"]) + 0.35
    shots[-1]["duration"] = float(shots[-1]["duration"]) + 1.5
    clips = talking_clips(tall, shots, workdir, sk["lines"])
    seed = idea["id"]
    video = render.concat_with_audio(tall, clips, shots, workdir, render.pick_music(cfg, seed))
    video = add_sfx(video, sound_events(shots, sk["lines"]), workdir)
    out = shorts.finish(cfg, video, workdir / "short.mp4", str(sk.get("hook") or sk["title"]),
                        shots=shots)

    in_skit = list(dict.fromkeys(s["speaker"] for s in shots))
    phrases = cfg.get_path("skit.search_phrases", ["Bhojpuri Comedy"])
    phrase = phrases[len(state.load().get("episodes", [])) % len(phrases)]
    head = str(sk["title"]).strip()
    title = f"{head} 😂 | {idea['keyword']} | {phrase}" if idea.get("keyword") \
        else f"{head} 😂 | {phrase}"
    if len(title) > 95:
        title = f"{head[:50]} 😂 | {phrase}"
    names = ", ".join(f"{chars[c]['name_bho']} ({chars[c]['name']})" for c in in_skit)
    tags = list(dict.fromkeys([*([str(idea["keyword"]), f"{idea['keyword']} comedy"]
                                 if idea.get("keyword") else []),
                               *(str(t) for t in sk.get("tags") or []),
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
            "keyword": idea.get("keyword", ""), "source": idea.get("source", {}),
            "runtime": round(sum(float(s["duration"]) for s in shots), 1)}
    (workdir / "metadata.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("Skit built: %s (%.1f s)", out, meta["runtime"])
    return meta


# --------------------------------------------------------------- motion + sound
def talking_clips(cfg: Config, shots: list[dict], workdir: Path, lines: list[dict]) -> list[Path]:
    """One talking clip per line, timed to that line's take (src/animate.py).
    Clips run render.XFADE long so the join stays in step."""
    chars = cfg.get_path("skit.characters", {})
    folder = workdir / "clips"
    folder.mkdir(parents=True, exist_ok=True)
    out = []
    for shot in shots:
        who = chars.get(shot["speaker"], {})
        dest = folder / f"clip_{shot['index']:03d}.mp4"
        animate.talking_clip(Path(shot["asset"]), Path(shot["audio"]) if shot.get("audio") else None,
                             float(shot["duration"]) + render.XFADE, dest,
                             name=who.get("name_bho", ""), colour=who.get("colour", "#FF4D6D"),
                             vis=cfg.get("visuals", {}), seed=shot["index"])
        shot["clip"] = str(dest)
        out.append(dest)
    return out


SFX = {"boing", "pop", "ding", "gasp", "whoosh"}


def sound_events(shots: list[dict], lines: list[dict]) -> list[tuple[str, float, float]]:
    """(effect, start second, volume): the script's picks at their lines (at
    most 3), and the rimshot just after the punchline's last word."""
    out, t = [], 0.0
    for shot, line in zip(shots, lines):
        fx = str(line.get("sfx") or "none").lower().strip()
        if fx in SFX and len(out) < 3:
            out.append((fx, max(t - 0.05, 0.0), 0.9))
        t += float(shot["duration"])
    last = shots[-1]
    end = sum(float(s["duration"]) for s in shots[:-1]) + float(
        last.get("spoken_duration") or last["duration"])
    out.append(("rimshot", end + 0.2, 1.4))
    return out


def add_sfx(video: Path, events: list[tuple[str, float, float]], workdir: Path) -> Path:
    """Lay the sound effects over the finished mix, each at its moment."""
    folder = ROOT / "assets" / "sfx"
    events = [e for e in events if (folder / f"{e[0]}.wav").exists()]
    if not events:
        return video
    inputs, parts = ["-i", str(video)], []
    for i, (fx, at, vol) in enumerate(events, 1):
        inputs += ["-i", str(folder / f"{fx}.wav")]
        ms = int(at * 1000)
        parts.append(f"[{i}:a]aformat=channel_layouts=stereo,volume={vol},"
                     f"adelay={ms}|{ms}[s{i}]")
    mix = "[0:a]" + "".join(f"[s{i}]" for i in range(1, len(events) + 1))
    graph = ";".join(parts) + f";{mix}amix=inputs={len(events) + 1}:duration=first:" \
        "normalize=0,alimiter=limit=0.95[a]"
    out = workdir / "final_sfx.mp4"
    res = subprocess.run(
        ["ffmpeg", "-y", *inputs, "-filter_complex", graph, "-map", "0:v", "-map", "[a]",
         "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", str(out)], capture_output=True, text=True)
    if res.returncode != 0:
        log.warning("Sound effects skipped: %s", res.stderr[-400:])
        return video
    log.info("Sound effects: %s", ", ".join(f"{fx}@{at:.1f}s" for fx, at, _ in events))
    return out


# --------------------------------------------------------------- trending
TREND_PROMPT = """These videos are trending on YouTube in India right now, each
with a million views or more:

{videos}

Our channel makes 40-second Bhojpuri family comedy skits with three cartoon
characters in a Bihar village house:
{cast}

Pick the {n} trending SUBJECTS that make the funniest, safest skit for these
characters: a cricket match, a new film or song everyone is talking about, a
festival, a viral challenge, a new phone, the weather, prices. SKIP anything
about politics, religion, crime, death, accidents, disasters, war, adult
content or a real person's private life. Take the subject only: never retell
the video, and never name or portray a real person in the premise (say "the
new film", "the cricket team", "the famous singer").

Return a JSON array of {n} objects:
  video_id - from the list
  keyword  - 2-4 words people would search for this subject on YouTube, in
             English or Hinglish (e.g. "India vs Australia", "Chhath Puja")
  premise  - one English sentence: the comic situation for our characters
  cast     - two or three of {keys}"""

BLOCK = re.compile(r"\b(modi|rahul|bjp|congress|election|vote|minister|murder|killed|death|died|"
                   r"accident|crash|attack|war|bomb|terror|rape|arrest|police|court|hindu|muslim|"
                   r"temple|mosque|masjid|jihad|riot|flood|earthquake|funeral|suicide|"
                   r"18\+|sexy|hot\b)", re.I)


def trending(cfg: Config, n: int = 1) -> list[dict]:
    """Premises from India's trending chart, videos at 1M+ views. Nothing (not
    a failure) when the API or the model says no: the idea bank covers it."""
    if n <= 0:
        return []
    try:
        yt = upload.client()
        seen, vids = set(), []
        floor = int(cfg.get_path("skit.trending.min_views", 1_000_000))
        for cat in cfg.get_path("skit.trending.categories", ["", "24", "23", "17", "10"]):
            kw = {"videoCategoryId": cat} if cat else {}
            try:
                items = yt.videos().list(part="snippet,statistics", chart="mostPopular",
                                         regionCode="IN", maxResults=50, **kw
                                         ).execute().get("items", [])
            except Exception as exc:  # noqa: BLE001 - some categories have no chart
                log.info("Trending chart %s unavailable: %s", cat or "all", str(exc)[:100])
                continue
            for v in items:
                views = int(v.get("statistics", {}).get("viewCount", 0))
                title = v["snippet"]["title"]
                if v["id"] in seen or views < floor or BLOCK.search(title):
                    continue
                seen.add(v["id"])
                vids.append({"id": v["id"], "title": title, "views": views,
                             "channel": v["snippet"].get("channelTitle", "")})
        used = {(e.get("source") or {}).get("video_id") for e in state.load().get("episodes", [])}
        used |= {str(e.get("keyword", "")).lower() for e in state.load().get("episodes", [])[-20:]}
        vids = [v for v in vids if v["id"] not in used][:60]
        log.info("Trending in India at %d+ views: %d videos", floor, len(vids))
        if not vids:
            return []
        chars = cfg.get_path("skit.characters", {})
        keys = list(chars)
        picks = llm.json_call(TREND_PROMPT.format(
            videos="\n".join(f"- {v['id']} | {v['title']} | {v['channel']} | "
                             f"{v['views'] / 1e6:.1f}M views" for v in vids),
            cast="\n".join(f"- {c['name']}: {c['role'].strip()}" for c in chars.values()),
            n=n, keys=keys), temperature=0.7)
        by_id = {v["id"]: v for v in vids}
        out = []
        for p in picks if isinstance(picks, list) else []:
            v = by_id.get(str(p.get("video_id")))
            if not v or not p.get("premise") or str(p.get("keyword", "")).lower() in used:
                continue
            out.append({"id": f"trend-{v['id']}", "premise": str(p["premise"]),
                        "keyword": str(p.get("keyword") or "").strip()[:40],
                        "cast": [c for c in p.get("cast") or keys if c in keys] or keys,
                        "source": {"video_id": v["id"], "title": v["title"],
                                   "views": v["views"]}})
            log.info("Trending premise: %s (from %r, %.1fM views)", out[-1]["keyword"],
                     v["title"][:70], v["views"] / 1e6)
        return out[:n]
    except Exception as exc:  # noqa: BLE001
        log.warning("Trending premises unavailable: %s", str(exc)[:200])
        return []


def slug(text: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in text.lower()).strip("-")[:48]


def run(cfg: Config, publish: str = "slot", no_upload: bool = False, count: int = 0,
        topic: str = "") -> int:
    """Today's skits, each at its own publish slot. A skit that fails gives
    way to a spare idea, so one bad draft does not lose a slot. Sunday
    (format.long.days) is the long video's day instead, and nothing else."""
    day = upload.publish_day(cfg)
    if not (count or topic) and upload.is_long_day(cfg, day):
        return weekly(cfg, publish, no_upload)
    n = count or int(cfg.get_path("skit.per_day", 4))
    if not count:
        n -= upload.made_on(state.load().get("episodes", []), day, "short")
        if n <= 0:
            log.info("Today's skits are already up")
            return 0
    # Trending skits take the later slots, when India watches most.
    trend = [] if topic else trending(cfg, min(n, int(cfg.get_path("skit.trending.per_day", 1))))
    bank = pick_ideas(cfg, n - len(trend) + 2, topic)
    queue = [*bank[: n - len(trend)], *trend, *bank[n - len(trend):]]
    taken: list = []
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
            when = None if publish == "now" else upload.day_slot(cfg, "short", day, taken)
            now = when is None          # every slot today has gone: public at once
            taken.append(when)
            sid = upload.upload(cfg, Path(meta["path"]), meta,
                                privacy="public" if now else "private", publish_at=when)
            upload.upload_captions(sid, Path(meta["captions"]), meta["language"])
            upload.add_to_playlists(cfg, sid, meta["language"], " ".join(meta["characters"]))
            archive(Path(meta["path"]), day, sid)
            state.record_episode(st, {
                "company": idea["premise"][:80], "title": meta["title"], "idea": idea["id"],
                "characters": meta["characters"], "language": "bho", "video_id": sid,
                "keyword": meta["keyword"], "source": meta["source"],
                "kind": "short", "day": day.isoformat(), "runtime": meta["runtime"]})
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


# ---------- Sunday: the week's skits as one long video ----------
# The runner forgets everything, so each skit's finished file is kept as an
# asset of one GitHub release (named <publish day>_<video id>.mp4) until the
# Sunday long video has used it.
ARCHIVE = "skit-archive"


def _gh(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["gh", *args], capture_output=True, text=True)


def archive(path: Path, day: date, video_id: str) -> None:
    """Keep a published skit for Sunday. Never fails the skit."""
    if not shutil.which("gh"):
        return
    copy = path.with_name(f"{day.isoformat()}_{video_id}.mp4")
    shutil.copy(path, copy)
    _gh("release", "create", ARCHIVE, "--prerelease", "--title", "Skit archive",
        "--notes", "Each day's Bhojpuri skits, kept for the Sunday long video.")
    r = _gh("release", "upload", ARCHIVE, str(copy), "--clobber")
    if r.returncode:
        log.warning("Could not archive %s for Sunday: %s", copy.name, r.stderr[:200])


def _duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    return float(out.stdout.strip() or 0)


def _stamp(sec: float) -> str:
    sec = int(sec)
    return f"{sec // 60}:{sec % 60:02d}" if sec < 3600 else \
        f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def _thumbnail(cfg: Config, clip: Path, text: str, dest: Path) -> Path:
    """The funniest-looking moment of the top skit, wide, with a big headline."""
    from PIL import Image, ImageDraw, ImageFilter, ImageFont

    frame = dest.with_name("thumb_frame.jpg")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{_duration(clip) * 0.3:.2f}",
                    "-i", str(clip), "-frames:v", "1", str(frame)], check=True)
    img = Image.open(frame).convert("RGB")
    back = img.resize((1280, int(1280 * img.height / img.width))).crop((0, 0, 1280, 720))
    back = back.filter(ImageFilter.GaussianBlur(18))
    front = img.resize((int(720 * img.width / img.height), 720))
    back.paste(front, (1280 - front.width - 30, 0))
    draw = ImageDraw.Draw(back)
    font_path = str(ROOT / cfg.get_path("visuals.fonts.display", "assets/fonts/Baloo2-Variable.ttf"))
    try:
        font = ImageFont.truetype(font_path, 92, layout_engine=ImageFont.Layout.RAQM)
        font.set_variation_by_name("ExtraBold")
    except Exception:  # noqa: BLE001 - plain layout still works for most text
        font = ImageFont.truetype(font_path, 92)
    y = 60
    for line in text.split("\n"):
        draw.text((48, y), line, font=font, fill="#FFD23F", stroke_width=8, stroke_fill="#1B1030")
        y += 130
    back.save(dest, quality=92)
    return dest


def weekly(cfg: Config, publish: str = "slot", no_upload: bool = False) -> int:
    """Sunday's long video: the week's skits back to back in 16:9, the most
    watched first, with a chapter per skit."""
    import json as _json

    day = upload.publish_day(cfg)
    st = state.load()
    if upload.made_on(st.get("episodes", []), day, "long"):
        log.info("This Sunday's long video is already up")
        return 0
    r = _gh("release", "view", ARCHIVE, "--json", "assets")
    if r.returncode:
        raise RuntimeError(f"No skit archive to build Sunday's video from: {r.stderr[:200]}")
    names = [a["name"] for a in _json.loads(r.stdout).get("assets", [])]
    since = day - timedelta(days=int(cfg.get_path("skit.weekly.days_back", 7)))
    week, old = [], []
    for name in names:
        try:
            made = date.fromisoformat(name[:10])
        except ValueError:
            continue
        (week if since <= made < day else old if made < since - timedelta(days=14) else []
         ).append(name)
    for name in old:                     # tidy: three weeks is plenty
        _gh("release", "delete-asset", ARCHIVE, name, "-y")
    if len(week) < int(cfg.get_path("skit.weekly.min_clips", 6)):
        raise RuntimeError(f"Only {len(week)} skits archived this week; need "
                           f"{cfg.get_path('skit.weekly.min_clips', 6)} for the long video")

    titles = {e.get("video_id"): str(e.get("title", "")) for e in st.get("episodes", [])}
    ids = [n.split("_", 1)[1].rsplit(".", 1)[0] for n in week]
    views: dict[str, int] = {}
    try:
        for i in range(0, len(ids), 50):
            for v in upload.client().videos().list(part="statistics", id=",".join(ids[i:i + 50])
                                                   ).execute().get("items", []):
                views[v["id"]] = int(v["statistics"].get("viewCount", 0))
    except Exception as exc:  # noqa: BLE001
        log.warning("View counts unavailable, keeping the week's order: %s", str(exc)[:120])
    order = sorted(zip(week, ids), key=lambda x: -views.get(x[1], 0))
    order = order[: int(cfg.get_path("skit.weekly.max_clips", 24))]

    workdir = ROOT / "output" / f"{day.isoformat()}-weekly"
    clips_dir = workdir / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)
    pats = [x for name, _ in order for x in ("-p", name)]
    r = _gh("release", "download", ARCHIVE, *pats, "-D", str(clips_dir), "--clobber")
    if r.returncode:
        raise RuntimeError(f"Could not download the week's skits: {r.stderr[:200]}")

    wide = ("[0:v]split[a][b];[a]scale=1920:1080:force_original_aspect_ratio=increase,"
            "crop=1920:1080,boxblur=30:4,eq=brightness=-0.10[bg];[b]scale=-2:1080[fg];"
            "[bg][fg]overlay=(W-w)/2:0,fps=30,format=yuv420p[v]")
    parts, chapters, t = [], [], 0.0
    for k, (name, vid) in enumerate(order):
        out = workdir / f"wide_{k:02d}.mp4"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(clips_dir / name),
                        "-filter_complex", wide, "-map", "[v]", "-map", "0:a",
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
                        "-af", "aresample=48000,aformat=channel_layouts=stereo",
                        "-c:a", "aac", "-b:a", "160k", str(out)], check=True)
        head = re.split(r"\s*😂|\s*\|", titles.get(vid, ""))[0].strip() or f"कॉमेडी {k + 1}"
        chapters.append(f"{_stamp(t)} {head}")
        parts.append(out)
        t += _duration(out)
    listing = workdir / "list.txt"
    listing.write_text("".join(f"file '{p.name}'\n" for p in parts), encoding="utf-8")
    video = workdir / "long.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
                    "-i", str(listing), "-c", "copy", "-movflags", "+faststart", str(video)],
                   check=True)
    minutes = t / 60
    log.info("Sunday long video: %d skits, %.1f min", len(parts), minutes)

    chars = cfg.get_path("skit.characters", {})
    cast = " ".join(c["name"] for c in chars.values())
    title = (f"ललन चाचा, फूलमती चाची आ बबलू के हफ्ता भर के बवाल 😂 | "
             f"Bhojpuri Comedy Video {int(minutes)} Minute")[:100]
    description = "\n\n".join(p for p in [
        "पूरा हफ्ता के सबसे मजेदार भोजपुरी कॉमेडी एके वीडियो में! ललन चाचा, फूलमती चाची "
        "आ बबलू के गाँव के बवाल, एक के बाद एक।",
        "This week's funniest Bhojpuri comedy skits in one video: Lallan Chacha, "
        "Phoolmati Chachi and Bablu.",
        "\n".join(chapters),
        "\n".join([cfg.get_path("channel.cta", "").strip(),
                   *[str(x) for x in cfg.get_path("channel.links", []) or []]]),
        cfg.get_path("channel.disclosure", "").strip(),
        "#bhojpuricomedy #bhojpurifunnyvideo #comedy",
    ] if p)
    phrases = cfg.get_path("skit.search_phrases", ["Bhojpuri Comedy"])
    tags = list(dict.fromkeys([*[p.lower() for p in phrases], "bhojpuri comedy video",
                               "bhojpuri comedy compilation", "bhojpuri funny video full",
                               "lallan chacha comedy", *cast.lower().split(),
                               "भोजपुरी कॉमेडी", "भोजपुरी कॉमेडी वीडियो", "comedy video"]))[:25]
    thumb = _thumbnail(cfg, clips_dir / order[0][0], "हफ्ता भर के\nबवाल!", workdir / "thumb.jpg")
    yt_lang = cfg.get_path("channel.youtube_language", "hi")
    if no_upload:
        log.info("Built %s (upload skipped)", video)
        return 0
    when = None if publish == "now" else upload.day_slot(cfg, "long", day)
    sid = upload.upload(cfg, video, {"title": title, "description": description, "tags": tags,
                                     "language": yt_lang},
                        privacy="public" if when is None else "private", publish_at=when)
    upload.set_thumbnail(sid, thumb)
    upload.add_to_playlists(cfg, sid, yt_lang, " ".join(chars))
    state.record_episode(st, {"company": f"Week to {day.isoformat()}", "title": title,
                              "language": "bho", "video_id": sid, "kind": "long",
                              "day": day.isoformat(), "runtime": round(t, 1),
                              "clips": [vid for _, vid in order]})
    state.save(st)
    gate.notify("\n".join([f"🎬 Bhojpuri Sunday video: {title}", f"https://youtu.be/{sid}",
                           f"{len(parts)} skits, {minutes:.1f} min",
                           "Published now" if when is None else f"Goes public {when:%a %d %b %H:%M}"]),
                markdown=False)
    return 0
