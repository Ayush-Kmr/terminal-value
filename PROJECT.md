# PROJECT.md: the whole project in one file

> **Read this first when coming back to the project** (for people and for Claude).
> Last updated: **2026-11-10**, after commit `4942e88`.
> - Setup steps are in `SETUP.md`.
> - The reasons for the niche are in `docs/STRATEGY.md`.
> - `README.md` is older. Where it disagrees with this file, this file is right (for example, the cadence).

---

## 1. What this is

This one repo runs two YouTube channels automatically from GitHub Actions. There are no faces, and the videos are made with AI tools.

| | **Terminal Value** (English) | **Bhojpuri AI Comedy** |
|---|---|---|
| Handle | `@terminalvaluedoc` | `@BhojpuriAIComedyShorts` |
| Channel ID | `UCm1uFmKTwjzhIAUeYC0NcZQ` | `UC5QnQWBbS5AVKvgxsZl4V3w` (this was the Tamil channel, rebranded on 2026-11-08) |
| Content | Explainers on US business and market news | Cartoon family comedy skits in Bhojpuri |
| Config | `config.yaml` | `config.yaml`, with `channels/bh.yaml` laid over it (`CHANNEL=bh`) |
| Code path | `main.py` → `src/*` | `main.py --skit` → `src/skit.py` |
| History | `data/history.json` | `data/history.bh.json` |
| Token secret | `YT_REFRESH_TOKEN` | `YT_REFRESH_TOKEN_BH`, falling back to `YT_REFRESH_TOKEN_TA` |

- Owner: Ayush (GitHub `Ayush-Kmr`, private repo `Ayush-Kmr/terminal-value`).
- GitHub `main` is the source of truth. Run `git pull` before working.
- `~/terminal-value` (without git) is an old copy. **Do not edit it.**

## 2. The weekly plan (set by Ayush on 2026-11-10; it replaces every earlier cadence)

| Day | English | Bhojpuri |
|---|---|---|
| **Sunday** | **1 long video and no Shorts.** About 10 minutes (at least 8), public at 11:00 ET. Topic: a deep dive on **the week's most-viewed Short** (≥100 views, `best_short_story`). | **1 long video and no Shorts.** Public at 18:00 IST. It is a 16:9 compilation of the week's skits, most-viewed first, with chapters (`skit.weekly`). |
| **Mon–Sat** | **4 Shorts**, at 08:00, 12:00, 16:00 and 20:00 ET, on trending US stories. | **4 skits**, at 12:30, 16:30, 19:30 and 21:30 IST. 2 of the 4 come from the India trending chart. |

How the timing works (`src/upload.py`):
- **`publish_day`:** "today" means channel time now + 4 h. The 03:30 UTC cron really starts at about 11:30 UTC, roughly 8 hours late.
- **`day_slot`:** gives the earliest free slot **on that day only**. If every slot that day has passed, the video goes **public at once**. It never spills into the next day.
- **`made_on`:** counts what was already uploaded for the day, so a restarted run tops the day up instead of doubling it.
- **`is_long_day`:** reads `format.long.days: [sunday]`. On that day `format.long.with_shorts: 0`.

## 3. Workflows (`.github/workflows/`)

| Workflow | When | What |
|---|---|---|
| `episode.yml` | Daily, cron 03:30 UTC (actually starts about 11:30 UTC), or run by hand | The English channel: a long video or Shorts. Inputs include topic, region and no-upload. |
| `bhojpuri.yml` | Daily, cron 03:30 UTC, or run by hand | The Bhojpuri channel (split out 2026-11-14): skits, or the weekly long video. Own concurrency group; `GEMINI_API_KEY_BH` if set. Inputs: premise, publish, count, no-upload. |
| `watchdog.yml` | Daily 13:00 UTC | Did both channels get their videos today? If not, it restarts what is missing (20 h window). |
| `reach.yml` | Mondays 03:15 UTC | Reach report into `reports/` and the history files. |
| `telegram.yml` | Hourly at :17 | Telegram bot: numbers and pipeline state for both channels. |
| `brand.yml` | By hand | Applies the banner, avatar, watermark, About text and keywords. |
| `publish-now.yml` | By hand | Publishes everything still waiting for its slot. |
| `doctor.yml` | By hand | Tests every API key with one small call. |
| `tags.yml`, `unlist.yml`, `telegram-check.yml` | By hand | Clean tags, unlist a language, send one test Telegram message. |

**Secrets:**
- LLM keys: `GEMINI_API_KEY`, `GROQ_API_KEY`, `XAI_API_KEY`, `OPENAI_API_KEY`.
- Pictures: `CF_ACCOUNT_ID`, `CF_API_TOKEN` (Cloudflare FLUX), `PIXABAY_API_KEY`, `PEXELS_API_KEY` (blank; new keys are paused).
- YouTube: `YT_CLIENT_ID`, `YT_CLIENT_SECRET`, `YT_REFRESH_TOKEN`, `YT_REFRESH_TOKEN_BH` / `_TA`.
- Telegram: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.
- Indic voice: `HF_TOKEN`.
- `ELEVENLABS_*` is optional and not used by default.
- **Never type Ayush's keys or passwords into web forms.**

## 4. How an English video is made (`main.py`)

1. **Pick the topic.**
   - `src/trends.py` reads Google Trends and Google News. `US_RULES` ranks market shocks, AI money and giant deals first, and puts price explainers last.
   - `data/queue.yaml` stories go ahead of the automatic picks. On Sunday, `best_short_story` beats the queue.
2. **Research:** `src/research.py` builds a sourced fact base. The script may use only those facts.
3. **Hook:** `src/hooks.py` writes cold opens and scores them.
   - It writes **6 cold opens**. The 6th is the "proven" strategy, built on the best Short's first line, which gets +8 score.
4. **Script and fact check:** `src/script.py` writes the script beat by beat, with shots. `src/factcheck.py` checks it against the research.
5. **Retention edits in `main.py`:**
   - `quick_opening`: the cold open is cut into shots of at most 9 words, so the picture changes within 5 s.
   - `add_long_ctas`: spoken follow asks go in at about 1 and 4 minutes (`format.long.cta_lines`).
   - `add_follow_line`: Shorts get a follow line before their last sentence.
   - `ends_clean`: Shorts with cut-off endings are rewritten.
6. **Voice:** `src/voice.py` uses Kokoro (`scripts/kokoro_tts.py`), with a lexicon in `voice.kokoro.lexicon`.
7. **Pictures:** `src/assets.py` and `src/visuals/` make data cards and use Wikimedia photos and FLUX stills.
8. **Captions and render:** `src/captions.py` makes word-by-word captions. `src/render.py` assembles with ffmpeg. `src/shorts.py` builds the Shorts at 9:16.
9. **Packaging:** `src/seo.py` writes titles, descriptions, tags and chapters ("Label: subject"). `src/thumbnail.py` makes the thumbnail.
10. **Upload:** `src/upload.py` uploads and checks the token matches the channel. `src/engage.py` posts a question comment with a link to the deep dive. Shorts link `state.latest_long_url`.
11. **Report:** Telegram reports the result through `src/gate.py`. The gates are off by default (`gates.*`).

**LLM:** the failover order is Gemini → Groq → Grok → GPT (`src/llm.py`). Gemini often hits its 429 quota, and the Groq fallback works.

## 5. How a Bhojpuri skit is made (`src/skit.py`)

- **Characters** (`channels/bh.yaml`):
  - Lallan Chacha (voice: Rohit)
  - Phoolmati Chachi (voice: Divya)
  - Bablu (voice: Aman)
  - **Fufa Ji** (Rohit, slow and nasal, colour #2F9E44; added on 2026-11-10)
- **Ideas:** these come from `data/bhojpuri_ideas.yaml`, which has dated festival ideas and a `lead:` field (the lead speaks the first line), and from India trending topics.
- **Series:** each lead has a numbered series (`skit.series`).
  - Title format: `head 😂 | <series> भाग N | keyword | phrase`.
  - Series names: ललन चाचा के कांड, चाची के जलवा, बबलू के जुगाड़, फूफा जी.
  - After the punchline, the lead says a follow line, "follow for भाग N+1" (`skit.follow_lines`). The rimshot stays on the punchline.
- **Voice and picture:**
  - Voice: Indic Parler-TTS (`scripts/indic_tts.py`).
  - Pictures: Cloudflare FLUX.
  - Animation: `src/animate.py`, with talking animation and name tags.
  - Captions use the Baloo 2 font. Sound effects come from `scripts/make_sfx.py`. The comedy music beds are by Kevin MacLeod.
- **Weekly long video:**
  - Every skit is archived as an asset of the GitHub release **`skit-archive`** (needs `GH_TOKEN`).
  - On Sunday, `weekly()` compiles 6–24 clips into 16:9 with a blurred background. It adds chapters, a PIL thumbnail and the question "which part was funniest".
- **Language fields** in YouTube are `hi`, because `bho` fails YouTube's validation.

## 6. Where things are

```
main.py                 entry point: --topic --region --kind --count --no-upload --no-shorts --reach --skit --publish
config.yaml             English channel and shared settings (format.long / format.short, upload, voice, trending)
channels/bh.yaml        Bhojpuri overrides: characters, series, skit.*, publish times, About text
channel/voice.md        house voice, pasted into every script prompt
data/                   queue.yaml, evergreen.yaml, bhojpuri_ideas.yaml, history*.json (written by the bot; commits say [skip ci])
src/                    pipeline modules (section 4 and 5)
scripts/                one-off and helper tools (doctor, brand, tokens, TTS runners, watchdog, telegram bot, publish_now)
assets/                 brand art, fonts, music, sfx
reports/                reach reports and newsletter draft (bot-written)
docs/                   STRATEGY.md, SEO-PLAYBOOK.md, BHOJPURI-WEEK-PLAN.md
```

**Local run** (needs a filled-in `.env`):

```bash
.venv/bin/python main.py --no-upload
.venv/bin/python main.py --topic "Fed rate cut" --region US --no-upload
CHANNEL=bh .venv/bin/python main.py --skit --no-upload
```

## 7. Numbers and audit state (2026-11-10)

**English:**
- 75 videos and 6 subscribers (0.09% of 6,504 views in 28 days).
- Long videos get 0–51 views and are 16–24% watched.
- The winners are market-crash, AI-bubble and India-impact Shorts (300–619 views). The best is `aUPucp6nUpQ`, the $2.5T AI sell-off, with 619 views.
- Traffic: 57% Shorts feed, 37% search, 1% suggested.

**Bhojpuri:**
- Median 6 views and 0 comments. The audience was inherited from the Tamil channel.
- Bhojpuri hits elsewhere are live-action family-relation series (e.g. Fufa Ji 3, 18M views) and numbered "Part 2" videos.

**Audit grades:** English **D** (SEO C, performance D, strategy C−, monetisation F). Bhojpuri **F**.

**Shipped from the audit:**
- `aa0788c`, `321c781`, `d8118f9`: follow lines, Bhojpuri series and Fufa Ji, links to the long video, chapter labels, `ends_clean`, skit question comments, and 3 English Shorts tagged.
- `4942e88`: the long-video hook work (proven opening, fast cuts, follow asks at 1 and 4 min).

**Plan for Sunday 15 Nov (English long, $2.5T AI sell-off):**
- Use the Shock/Contradiction hook.
- Only these facts are verified: S&P 500 −4.4%, Dow −1,833 points (−3.4%), Nasdaq −5.5% and 11.9% below its high, about $2.5T wiped off Nasdaq Composite value, and worries over AI infrastructure and data-centre power.

## 8. Open items and to-dos

- [ ] **Lip-sync work in progress (SadTalker, CPU) is not committed.** It lives only in the working tree, with a backup in `git stash` "lipsync-wip".
  - Files: `scripts/lipsync.py`, `requirements-lipsync.txt`, and changes to `episode.yml`, `.gitignore`, `channels/bh.yaml`, `src/animate.py`, `src/skit.py`.
  - **Do not commit it by accident.** Commit those files on purpose when the work is finished and tested.
- [ ] **After Sunday 15 Nov**, check the long video's retention in Studio. If more than 20% of viewers leave in the first 15 s, rebuild the opening. Also check that the Bhojpuri weekly compilation was published.
- [ ] Re-run `/youtube analyze` (it was due on 2026-11-11).
- [ ] Things the API can't do, which need Studio by hand: end screens, the Shorts related-video link, pinned comments.
- [ ] Deleting the old Tamil videos is destructive and **needs Ayush's OK.** (17 were unlisted and 10 playlists made private.)
- [ ] Optional: Ayush recording his own voice for the first 30 s of the Sunday long video. AI narration retains noticeably less.
- [ ] Optional: the Pixabay video endpoint as a stock-video source.

## 9. Rules for working on this repo

- Ayush is not deeply technical. Give step-by-step instructions and plain answers.
- Never put API keys or passwords into web forms. Keys live only in GitHub secrets and `.env`.
- Commit only the files you changed. Check `git status` first, because the lip-sync files sit uncommitted (section 8).
- Bot commits ("chore: … history [skip ci]") land on `main` often, so pull or rebase before pushing.
- Test with the Episode workflow's no-upload input (or `--no-upload` locally) before changing anything that publishes.
- Update **section 2, 7 and 8 of this file** whenever the cadence, the numbers or the to-dos change.

## 10. Change log (short)

| Date | Change |
|---|---|
| 2026-10-10 | Project started: English business documentaries, Telegram gates, under $30 a month. |
| 2026-10-20 | Overhaul: English first, real-article research, Whisper QA, burned-in captions, slot schedule. |
| 2026-11-04 | Long videos on Tue and Fri; the Tamil channel picked its own stories; competitor analysis. |
| 2026-11-08 | **The Tamil channel became Bhojpuri AI Comedy** (cartoon skits). |
| 2026-11-10 | **New cadence: Sunday 1 long video, Mon–Sat 4 Shorts, on both channels.** Audit fixes, Fufa Ji, series, hook work (`de761fa` … `4942e88`). |
