# CLAUDE.md: Terminal Value (two automated YouTube channels)

Claude Code loads this file on every prompt. It sets the working rules and
the architecture. History and the reasons behind decisions are in
`PROJECT.md`. Where the two differ, the code and config win, then this file.
Last checked against the code: 2026-11-14 (commit 2c95955).

---

## 0. Rules for every task (read first)

1. **The owner is Ayush** (GitHub `Ayush-Kmr`, private repo `Ayush-Kmr/terminal-value`).
   - They are not deeply technical: give plain answers and copy-paste steps.
   - Push only with the `Ayush-Kmr` gh account, never `ayushkumar-glitch`.
2. **GitHub `main` is the source of truth.**
   - Bot commits ("chore: … history [skip ci]") land on it several times a day. Always `git fetch` first, and rebase before pushing.
   - `~/terminal-value` (no git) is an old copy. Its `.env` holds working local keys, but never edit code there.
3. **Uncommitted lip-sync work sits in this checkout. Never commit it by accident.**
   - Untracked: `scripts/lipsync.py`, `requirements-lipsync.txt`.
   - Modified: `.github/workflows/bhojpuri.yml`, `.gitignore`, `channels/bh.yaml`, `src/animate.py`, `src/skit.py`.
   - Workflow for code changes:
     1. Make a worktree from `origin/main`: `git worktree add ../tv-x origin/main -b x`.
     2. Edit, test, commit and `git push origin HEAD:main`.
     3. Update this checkout: `git pull --rebase --autostash`.
     4. Remove the worktree and its branch.
   - Read committed code with `git show origin/main:<path>`. The working tree differs for those files.
   - If you only change unrelated files here, `git add` them by name. Never use `git add -A`.
4. **Secrets:**
   - Keys live only in GitHub secrets (and the old local `.env`).
   - Never type Ayush's keys or passwords into web forms. Never print a secret.
   - There is no local `CF_ACCOUNT_ID`.
5. **Test before changing anything that publishes:**
   - Use the workflow's `no_upload` input (English: Episode; Bhojpuri: Bhojpuri), or `--no-upload` locally.
   - Mock-test new logic with `~/code/terminal-value/.venv/bin/python` (Python 3.12). Plain `python3` is 3.9.
   - There is no test suite. Scratch tests go in the session scratchpad.
6. **Published videos:** actions that change public state need Ayush's OK unless they asked for them: deleting, unlisting, rescheduling, retitling.
   - To clear a scheduled release, set `status.publishAt` to `None` explicitly. Leaving the field out does not clear it.
   - `videos.update` replaces the whole `status` part: keep `KEEP_STATUS` (see `upload.set_privacy`).
7. **Comments in code explain why**, usually with a date and the evidence (audit, test run ID). Keep that style. Config values carry their reasons as comments; update the comment when you change a value.
8. **After a change:** append a dated line to the memory file `terminal-value-youtube-project.md`. Update `PROJECT.md` §2/§7/§8 when the cadence, the numbers or the to-dos change.

---

## 1. What this repo is

One repo, run from GitHub Actions, makes two faceless channels with AI:

| | **Terminal Value** (English) | **Bhojpuri AI Comedy** |
|---|---|---|
| Handle / ID | `@terminalvaluedoc` · `UCm1uFmKTwjzhIAUeYC0NcZQ` | `@BhojpuriAIComedyShorts` · `UC5QnQWBbS5AVKvgxsZl4V3w` (ex-Tamil channel, rebranded 2026-11-08) |
| Content | US business and markets news explainers | Cartoon family comedy skits in Bhojpuri |
| Config | `config.yaml` | `config.yaml` with `channels/bh.yaml` merged over it (`CHANNEL=bh`) |
| Entry | `main.py` → `src/*` | `main.py --skit` → `src/skit.py` (shares only upload, voice, render, captions, shorts, state, gate) |
| Workflow | `.github/workflows/episode.yml` (concurrency `episode`) | `.github/workflows/bhojpuri.yml` (concurrency `bhojpuri`; split out 2026-11-14) |
| History | `data/history.json` | `data/history.bh.json` |
| Token | `YT_REFRESH_TOKEN` | `YT_REFRESH_TOKEN_BH`, falling back to `_TA`; the workflow maps it into `YT_REFRESH_TOKEN` |
| Voice | Kokoro-82M (`scripts/kokoro_tts.py`, `.venv-kokoro`) | AI4Bharat Indic Parler-TTS (`scripts/indic_tts.py`, `.venv-indic`, needs `HF_TOKEN`) |
| YouTube language | `en` | `hi` (`bho` fails YouTube's validation; `channel.youtube_language`) |

Everything is meant to be free:
- **LLMs:** free tiers.
- **Pictures:** Cloudflare's free tier, 10,000 neurons a day, **shared by both channels**.
- **Voice models:** open models run on the GitHub runner.

`upload.check_channel` refuses to upload when the token's channel isn't the config's `channel.channel_id`.

**Same Google account:** the English `YT_REFRESH_TOKEN` in `~/terminal-value/.env` can read and update the Bhojpuri channel's videos through `src.upload.client(token)`.

---

## 2. Schedule (the config is the truth; PROJECT.md §2 is partly stale)

Both workflows cron at `30 3 * * *` UTC. GitHub often starts them hours late (around 11:30 UTC).

| Day | English | Bhojpuri |
|---|---|---|
| Mon–Sat | **2 Shorts** at 12:00 and 20:00 ET (`format.short.per_day: 2`, since 2026-11-13). The 2nd is a "How X makes money" Short (`format.short.second: money`, from `data/money.yaml`). | **2 skits** at 12:30 and 19:30 IST (`skit.per_day: 2`). One is from India's trending chart (`skit.trending.per_day: 1`). |
| Sunday | **1 long video**, about 10 min (at least 8), public 11:00 ET, no Shorts (`format.long.days: [sunday]`, `with_shorts: 0`) | **1 long weekly compilation** at 18:00 IST (`skit.weekly`), no skits |

Timing logic (`src/upload.py`):
- **`publish_day`:** "today" means channel time now + 4 h.
- **`day_slot`:** the earliest free slot that day, at least 20 min from now. If every slot has gone, it returns `None` and the video goes **public at once**. It never spills into the next day.
- **`made_on`:** counts the day's uploads, so a restarted run tops the day up instead of doubling it.
- **`is_long_day`:** whether today is in `format.long.days`.

---

## 3. English pipeline (`main.py`)

`run()` routes the day:
- `todays_kind` returns `shorts`, `long` or `done`.
- `--reach` runs the report; `--skit` goes to `skit.run`.
- Before producing, it calls `engage.post_questions` (a question comment under videos that have gone public).

### Shorts day: `run_shorts_day` → `make_shorts` → `produce_short`
1. **Stories:**
   - `queued(args,"shorts",n)` comes first: entries from `data/queue.yaml`, skipped once their `queue` key `"shorts:<topic>"` is in history.
   - Then `fresh_or_queue` → `trends.pick`.
   - `with_stock` inserts the 2nd Short (money, question or stock). Spares replace a failed story.
2. **`produce_short`:**
   1. Research with `research.build_news` and `validate_news`.
   2. `script.write` with `SHORT_BEAT`, about 25 s (`target_seconds: 25`, `words_per_minute: 140`).
   3. It is rewritten up to 3× unless all of these hold:
      - `hook_words ≤ 10`;
      - it is within the word limit;
      - `loops()` (the last line repeats a key word from the first);
      - `not names_loop` (never says "loop" or "back to the start");
      - `ends_clean`.
   4. `factcheck.check`, then `add_follow_line` (a follow ask before the last sentence).
   5. `voice.narrate_shots`, `assets.gather`, `render.build_shot_clips`, `concat_with_audio`.
   6. `seo.titles` (max 40 characters), `set_title`, `seo.pick_delivered`, then `shorts.standalone` (headline plate, captions, 58 s cap).
   7. Upload: `upload.upload`, `upload_captions`, `add_to_playlists`, `engage.localise` (es/hi titles), `state.record_episode`, and a Telegram report.

### Long day (Sunday): `produce`
1. **Story priority:**
   1. A queued long video whose `ahead_of_best_short` date has not passed.
   2. `best_short_story`: the week's most-viewed Short with ≥100 views. It passes `proven_hook` and `parent_url`.
   3. The next queued long video.
   4. `history_story` (`viral.py`) or `evergreen_story`, per `long_slot` (n % 4 → evergreen, history, evergreen, news).
   5. Today's trends.
   - If a story fails, the next 2 candidates are tried. A `VoiceError` aborts instead.
2. **History stories** (`story.history`) swap the beat spine to `structure.history_beats`, or to `biography_beats` when `collapse: false`.
3. **Steps:**
   1. research
   2. `hooks.generate_and_score`: 5–6 cold opens of 30–40 words. The "proven" strategy gets +8. The lint deducts for a rhetorical question, no number, a first sentence over 15 words, and more than 45 or fewer than 25 words.
   3. `script.write`: beat by beat (`structure.beats`), with lengthening retries to reach `min_minutes`.
   4. `factcheck`
   5. `quick_opening`: the cold open is split into shots of `opening_shot_words: 6`.
   6. `add_long_ctas`: spoken follow asks at minutes 1 and 4.
   7. voice, visuals, render
   8. titles (max 90 characters; the "How It Collapsed" series suffix for history), description, chapters, tags
   9. `thumbnail.generate` plus 2 spare thumbnails for Studio's Test & Compare
   10. upload, thumbnail, captions, playlists, localise.

### Research (`src/research.py`): the trust gate
- **`build_news`:**
  1. `grounded_briefing`: Gemini with Google Search grounding. If that fails, OpenAI web search (`OPENAI_API_KEY` is **not set** yet).
  2. Plus `article_texts` (Bing news and trafilatura full text) and Google News headlines (`when:3d` unless history) and Wikipedia.
  3. Off-topic material is dropped (`on_topic`), then `NEWS_EXTRACT_PROMPT` turns the rest into a `FactBase`.
- **Hard gate:** at least `research.min_trusted: 2` sources from `research.trusted_sources`, or **1 when `history: true`**. Otherwise the call raises.
  - A news story covered by a single outlet fails the gate. Requeue it as a `history: true` queue entry framed as the wider, well-reported trend.
- **Titles:** a queued `title` stands only if every figure in it appears in the research (`seo._unsupported_numbers`).

### Topic picking (`src/trends.py`)
- **Sources:** Google Trends RSS and Google News RSS for `trending.regions` (US, MARKETS). The LLM ranks the candidates using `US_RULES`.
- **Priorities:** market shocks with big numbers, AI money and giant deals come first. Price explainers come last.
- **Blocking:** `trending.block_words` (elections, sports) is checked in code. `trending.avoid` is passed to the prompt.
- **Cooldown:** 10 days (`cooldown_days`).

---

## 4. Bhojpuri pipeline (`src/skit.py`)

### `run()`
1. Post question comments.
2. If today is Sunday, run `weekly()` and stop.
3. Otherwise `n = per_day - made_on`.
4. The queue is bank ideas first, then trending ideas (they take the later slot), then spare ideas. A failed skit gives way to a spare. `VoiceError` aborts.

### `build()`, in order
1. **`write()`:**
   - `PROMPT` is filled from the characters in `skit.characters`.
   - The lead speaks line 1: the `lead` on the idea, else `skit.lead: lallan`.
   - Three drafts are checked by `problems()`:
     - 7–9 lines (`skit.lines`);
     - at most 75 words in all (`max_words`);
     - line 1 at most 8 words (`first_line_words`);
     - at most 14 words a line (`max_line_words`);
     - only known speakers;
     - no Latin letters;
     - a Devanagari title.
   - Then one low-temperature `REPAIR` call. If that fails too, it **raises** and the skit is dropped: imperfect drafts never ship.
   - `tidy()` fixes a Latin "s" written for the avagraha mark (ऽ).
2. **Follow line:** a line from `skit.follow_lines` is appended after the punchline, spoken by the lead.
3. **Voice before pictures** (2026-11-14), so a dropped skit costs no picture allowance:
   - Each line's character voice is written to `audio/shot_NNN.desc`.
   - `voice.narrate_shots` runs, then `fix_garbled()`:
     - lines whose Whisper match is below `skit.voice_floor: 0.4` are rewritten through `PLAIN` and spoken again;
     - if still below the floor, it raises.
4. **Pictures:** `picture()` uses Cloudflare.
   - The prompt is the character's `look` + the line's `action` + the setting + `skit.style` + `skit.tall_style`.
   - **Models tried in order:**
     1. `TALL_MODELS`: FLUX.2 klein-4b, then FLUX.2 dev. Both use multipart form fields at 768×1344.
     2. Last resort: square `flux-1-schnell`. It goes over blurred bands in `tall_frame`; a tall picture (h/w > 1.6) is cover-cropped to 1080×1920 instead.
     - `skit.image_models` overrides the list.
   - **Errors:**
     - A "flagged" response moves this picture alone to the next model.
     - A 429 "daily free allocation" stops all picture requests.
     - A 400/403/404 on a tall model marks it `_dead` for the run.
   - **Fallback picture:** a line whose picture fails reuses the previous line's picture. If the first line fails, the skit fails.
   - **No keyless fallback:** Pollinations was rejected (watermark, a face like a real politician).
   - Lucid Origin was dropped (different style, costly).
5. **Timing:** a pause before the punchline, +1.5 s after it for the rimshot.
6. **Assembly:**
   - `talking_clips`: `animate.talking_clip`, a loudness-driven bounce plus a name tag. Lip-sync is WIP only.
   - `concat_with_audio`, then `add_sfx`: up to 3 script sound effects (boing, pop, ding, gasp, whoosh) plus a rimshot. They come from `assets/sfx`, made by `scripts/make_sfx.py`.
   - `shorts.finish` adds the headline and captions in Baloo 2 (Devanagari).
7. **Title:** `"<जब … title> 😂 | <keyword> | <search phrase>"`.
   - Titles start with "जब" plus the lead's name.
   - Numbered series are off (`skit.series: {}`).
8. **Upload, then `archive()`:** the finished file is uploaded to the GitHub release **`skit-archive`** as `<day>_<videoId>.mp4`. This needs `GH_TOKEN`.

### Supporting pieces
- **Ideas:** from `data/bhojpuri_ideas.yaml`.
  - Fields: `id`, `premise`, `cast`, optional `lead`, `months`, `dates`.
  - Dated ideas go first, then this month's seasonal ones.
  - Used ideas are tracked by `idea` in history. When the bank runs out, `invent()`.
- **Trending premises:** `trending()` reads the India `mostPopular` chart (≥1M views, filtered by the `BLOCK` regex). The LLM picks the subject only, never real people.
- **`weekly()`:** downloads the week's archive assets (needs at least 6, at most 24) and orders them by views.
  - Each is put in a 16:9 frame over a blurred copy of itself; the clips are joined and get chapters and a PIL thumbnail.
  - Assets older than 3 weeks are deleted from the release.
- **Characters** (`channels/bh.yaml` → `skit.characters`): each has `name`, `name_bho`, `colour`, `role`, `look` and `voice` (a Parler speaker description).

  | Character | Parler speaker | Notes |
  |---|---|---|
  | Lallan Chacha | Rohit | |
  | Phoolmati Chachi | Divya | |
  | Bablu | Aman | |
  | Fufa Ji | Rohit (slow, nasal) | added 2026-11-10 |

---

## 5. Shared modules (`src/`)

| Module | Job / key facts |
|---|---|
| `config.py` | `load()` merges `channels/<CHANNEL>.yaml` over `config.yaml`. The keys in `REPLACE` are replaced whole: `topic_playlists`, `title_series`, `hashtags`, `playlists`, `credits`. `Config.get_path("a.b.c")`. `portrait(cfg)` gives the 9:16 config (`visuals.short=True`, short palette and fonts). `latin_only`, `language_rules`. |
| `llm.py` | `call` / `json_call` fail over **Gemini** (`gemini-flash-latest`, then `-lite`) → **Groq** (auto-ranked; 8k TPM budget) → **Grok** → **GPT**. A model that hits 429/404/504 is skipped for the rest of the run. `json_call` re-prompts on bad JSON and keeps the complete items of a cut-off array. On the Bhojpuri channel, `GEMINI_API_KEY` = `GEMINI_API_KEY_BH` if that secret is set. |
| `voice.py` | Providers: `kokoro`, `indic`, `clone` (Chatterbox, `.venv-voice`), `elevenlabs`. Details below the table. |
| `speech_text.py` | `spoken()`: figures written out as words, and finance shorthand expanded for TTS (Hindi numbers too). |
| `captions.py` | Word-by-word captions from Whisper word timings. "Caption News" style for English Shorts (one keyword on a honey-coloured block). Devanagari through `visuals.fonts.caption` and libraqm. Also writes the `.srt`. |
| `render.py` | ffmpeg assembly. `XFADE=0.4` s overlaps; `visuals.hard_cuts` for the skits; the "News Package" layer (strap, bug, tabs) on long videos; Ken Burns on stills. `pick_music` is chosen by the story; `music_credit` writes the CC credit. |
| `shorts.py` | `finish()` adds the headline plate and captions and caps at **58 s**. `standalone()` is for Shorts rendered 9:16 from the start. `cut()` / `build_all()` cut Shorts from a long video (only used for a manual topic). |
| `assets.py`, `visuals/` | Visuals per shot. `motion.py`: stat, quote, timeline, chart and document cards, lower thirds, the News Package. `stock.py`: Wikimedia first for real people and places; Pixabay (Pexels has no key). `ai_images.py`: Cloudflare FLUX with a Pollinations fallback (English only). |
| `seo.py` | Title max 90 characters (long) and 40 (Short). Lint covers clickbait words, the subject coming first and unsupported numbers. Also `pick_delivered`; a description built from parts (summary, chapters, facts, CTA, sources, credits, AI disclosure); 3 hashtags; tags; chapters ("Label: subject"). |
| `thumbnail.py` | `story_layout` and others. The thumbnail never repeats the title's figure or words. A queue entry can fix `thumb_text`, `thumb_figure` and `thumb_entity`. |
| `upload.py` | Data API v3 (see §2). `title_series` regex playlists ("Why Is It So Expensive?", "How It Makes Money"); `topic_playlists` by whole-word match; language playlists. `_safe` strips `<` and `>` and limits by bytes (Devanagari is 3 bytes a character). |
| `engage.py` | `post_questions`: the question comment, with a link to the deep dive. `localise`: es/hi titles and subtitles (`localize.languages`; each caption costs 400 quota units). |
| `gate.py` | Telegram: `notify` never raises. The topic and review gates exist but are off (`gates.*: false`). |
| `state.py` | Loads and saves the history file for the channel. `record_episode`, `latest_long_url`, `covered_companies`. Episode keys: `company`, `title`, `video_id`, `kind` (short/long), `day`, `queue`, `hook`, `question`, `deep_dive`, …; on Bhojpuri also `idea`, `characters`, `keyword`, `source`, `series`, `part`, `runtime`, `stats`. |
| `factcheck.py` | Lines with a figure or name not in the research are rewritten or dropped; at most 25% of shots may be lost. Cards lose figures the research lacks. |
| `hooks.py`, `script.py` | See §3. `channel/voice.md` (house style: numbers, attribution, no advice, no rhetorical openers) is pasted into every prompt; `channel/visual-identity.md` too. |
| `reach.py` | Weekly report: stats, retention, drop-off, traffic sources, countries, YPP progress. Writes `reports/reach-report*.md` and the history `stats`. |
| `viral.py`, `keywords.py`, `topics.py`, `newsletter.py` | Proven-demand search (subjects only, never copying); YouTube autocomplete keywords; the old documentary mode (`mode: documentary`); the weekly newsletter draft. |
| `animate.py` | The skits' talking-picture clip: a loudness envelope drives the bounce, plus the name pill. |

**`voice.py` details:**
- Each provider speaks one take per shot (`audio/shot_NNN.mp3`), and shot durations come from the real audio.
- **Whisper check** (Groq `whisper-large-v3-turbo`) compares each take with its line:
  - The bar is `voice.match_ok`: 0.82 default, **0.45 on Bhojpuri**, because Whisper hears Bhojpuri as Hindi.
  - Loose takes (under `match_ok`) get `voice.check_rounds` retakes (1 on Bhojpuri).
  - Then takes still under `skit.voice_floor` get up to `voice.garbled_rounds` (3) more tries.
  - Each round uses a **fresh seed**: `indic_tts.py` otherwise resets to seed 7, so a lone retake came back identical. The best take is kept.
  - Each Parler round reloads the model, about 1–2 minutes.
  - Kokoro is deterministic, so it gets 0 rounds; it still uses the transcripts for caption timing.
- `shot["match"]` and `shot["words"]` are set for each take.
- In Shorts, the last shot ends where its voice ends, so the loop has no gap.
- `VoiceError` is an unrecoverable failure: the run aborts.

---

## 6. Workflows and secrets

| Workflow | When | Does |
|---|---|---|
| `episode.yml` | daily 03:30 UTC, or by hand | English. Inputs: `topic`, `region` (US/MARKETS), `kind` (auto/long/shorts), `publish` (slot/now), `count`, `no_upload`. Artifact `episode-N`. Commits `data/history.json`. |
| `bhojpuri.yml` | daily 03:30 UTC, or by hand | Bhojpuri. Inputs: `premise`, `publish`, `count`, `no_upload`. Artifact `bhojpuri-N` (short.mp4, metadata, script, scene jpgs). Commits `data/history.bh.json`. |
| `watchdog.yml` | 13:00 and 20:00 UTC | `scripts/watchdog.py` restarts a channel's workflow if it uploaded nothing in 20 h. `scripts/health.py` sends the evening Telegram report. |
| `reach.yml` | Mon 03:15 UTC | `main.py --reach` for both channels. |
| `telegram.yml` | hourly at :17 | `scripts/telegram_bot.py --poll`: slash commands with each channel's numbers. |
| By hand | | `brand.yml` (`scripts/brand_channel.py`: banner, About, keywords, watermark), `publish-now.yml`, `doctor.yml` (tests every key), `tags.yml` (`clean_tags.py`), `unlist.yml` (`unlist_language.py`, `backfill_series.py`), `link-long.yml` (`link_long.py`), `competitors.yml`, `telegram-check.yml`. |

**Running workflows:**
- Dispatch: `gh workflow run bhojpuri.yml -R Ayush-Kmr/terminal-value --ref main -f no_upload=true`.
- Watch with `gh run view <id> --log | grep …`. Download an artifact with `gh run download <id>`.
- A gh network error is usually transient: retry with `-R` and `--ref`.

**Secrets:**
- LLMs: `GEMINI_API_KEY`, `GEMINI_API_KEY_BH` (optional), `GROQ_API_KEY`, `XAI_API_KEY`, `OPENAI_API_KEY` (missing).
- Pictures: `CF_ACCOUNT_ID`, `CF_API_TOKEN`, `PIXABAY_API_KEY`, `PEXELS_API_KEY` (blank).
- YouTube: `YT_CLIENT_ID`, `YT_CLIENT_SECRET`, `YT_REFRESH_TOKEN`, `YT_REFRESH_TOKEN_BH` / `_TA`.
- Other: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `HF_TOKEN`, optional `ELEVENLABS_*`.
- Tokens come from `scripts/get_youtube_token.py` (`--secret YT_REFRESH_TOKEN_BH`).

---

## 7. Data and content files

- **`data/queue.yaml`**: stories that go ahead of the automatic picks, under `long:` and `shorts:`.
  - Fields: `topic`, `title`, `angle`, `search_query`, `keywords`, `history`, `wiki_title`, `collapse`, `not_before`, `ahead_of_best_short`, `proven_hook`, `proven_views`, `thumb_text`, `thumb_figure`, `thumb_entity`.
  - An entry leaves the queue once its `"<kind>:<topic>"` key is in history.
- **`data/money.yaml`**: "How X makes money" Shorts (fixed titles of at most 40 characters). **`data/evergreen.yaml`**: explainer topics for long videos and question Shorts. Both are tracked by `queue` keys (`money-short:`, `evergreen-short:`).
- **`data/bhojpuri_ideas.yaml`**: skit premises (§4).
- **`data/history*.json`**: written by the bot. Never hand-edit while a run is going.
- **Prose files:**
  - `channel/voice.md`, `channel/visual-identity.md`: prompt inputs.
  - `docs/STRATEGY.md`, `docs/SEO-PLAYBOOK.md`, `docs/BHOJPURI-WEEK-PLAN.md`: background.
  - `reports/`: written by the bot.
- **`assets/`:**
  - `fonts/`: Barlow Condensed, IBM Plex Mono, Inter Tight, Baloo 2, Archivo.
  - `music/`: Kevin MacLeod CC beds, credited in `audio.credits`; comedy beds under `music/comedy`.
  - `sfx/`, `brand/` (`brand/bh/` for Bhojpuri), `voice/` (clone references).
- **`output/`:** the local build folder (gitignored). One folder per video:
  - English: `YYYY-MM-DD-short-<slug>` or `YYYY-MM-DD-<slug>`.
  - Bhojpuri: `YYYY-MM-DD-skit-<id>`.
  - Each holds `research.json`, `script.json`, `shots.json`, `metadata.json`, `audio/`, `assets/`, `short.mp4` / `final.mp4`.

**Local runs** (local keys are in `~/terminal-value/.env`, so copy or source them; real runs happen in Actions):
```bash
.venv/bin/python main.py --no-upload
.venv/bin/python main.py --topic "Fed rate cut" --region US --no-upload
CHANNEL=bh .venv/bin/python main.py --skit --topic "<premise>" --no-upload
```

---

## 8. Lessons that shaped the code (don't undo them)

**English Shorts:**
- Shorts of 21–28 s held 60–110% of viewers; 42–44 s ones held 30%. Keep them short, with a hook of at most 8–10 words and a loop ending.
- Standalone Shorts beat cuts from long videos about 5 to 1.
- Winning topics: market shocks with big numbers, the AI bubble, AI money, and money Shorts (Kalshi held 91%). Price explainers did worst.

**English long videos:**
- They lose most viewers in the first tenth. Hence the 30–40-word cold open, cuts within 3 s, and follow asks at minutes 1 and 4.

**Bhojpuri skits:**
- Skits of 16–21 s were too short; aim for 30–40 s with a hook of at most 8 words.
- Numbered "भाग N" (Part N) series got 0–20 views, so they are off.
- Imperfect drafts and garbled voice lines used to ship and were the most-skipped skits. Both are now hard drops.

**Pictures:**
- Square pictures left blurred bands over a quarter of the screen, hence the tall FLUX.2 models.
- Cloudflare's daily allowance can run out mid-run.

**YouTube API:**
- At most 3 uploads a day trigger subscriber notifications, hence 2 videos a day per channel.
- Captions cost 400 quota units each. The daily quota is 10,000.
- The API cannot set end screens, the Shorts related-video link, pinned comments, the profile picture or the handle. Those need Studio.

**General:**
- A slot that has already gone means publish now, never tomorrow.
- One failed item gives way to a spare, so the day's count is kept.

---

## 9. Open items (2026-11-14)

- [ ] Lip-sync (SadTalker on CPU) is uncommitted work in progress. Commit it only once it is finished and tested.
- [ ] **Mon 16 Nov:** the first real Bhojpuri run with 9:16 pictures, voice before pictures and garbled-line retakes. Check:
  - whether two skits a day fit the shared Cloudflare allowance;
  - how many skits the voice gate drops.
- [ ] **17 Nov:** check the Sunday tariff-refund long video's retention. If more than 20% of viewers leave in the first 15 s, rebuild the opening.
- [ ] **About 21 Nov:** check the 8 English videos retitled on 14 Nov.
- [ ] `OPENAI_API_KEY` is still missing: the research fallback when Gemini's search allowance runs out.
- [ ] Deleting the old Tamil videos needs Ayush's OK.
