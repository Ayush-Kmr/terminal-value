# Terminal Value

Automated YouTube channel for a US audience: it finds the American business
stories people are searching for and explains them in English: a 9-minute video
every other day and two standalone Shorts a day. The Tamil channel makes its own videos on what is trending in Tamil Nadu (`channels/ta-src.yaml` picks and builds the story, `src/follow.py` narrates it in Tamil), never the English channel's stories.

Channel: <https://www.youtube.com/@terminalvaluedoc>

## What runs, and when

| Workflow | When | What it does |
|---|---|---|
| **Episode** | daily 03:30 UTC | Tuesdays and Fridays (`format.long.days`): top story → research → script → voice → 1 long video. Every day: 2 standalone Shorts on the next stories |
| **Reach report** | Mondays 08:45 IST | pulls view counts into `reports/` and `data/history.json` |
| **Doctor** | on demand | tests every API key with one small call each |
| **Brand channel** | on demand | applies `assets/brand/` and the About text in `config.yaml` |

Videos go public at fixed Eastern times (`format` in `config.yaml`). To publish
on upload instead, set `upload.publish: now`.

Run one by hand: **Actions → Episode → Run workflow**. Leave *topic* blank for
today's top trend, or type a story and choose its region.

## How a video is made

1. `src/trends.py` reads Google Trends and Google News for the US, and the model
   picks the stories. `trending.avoid` in `config.yaml` lists what it must skip.
2. `src/research.py` builds a fact base from a search-grounded briefing and the
   day's headlines. The script may only use facts from it.
3. `src/script.py` writes five sections; `src/voice.py` narrates each shot with the
   open Kokoro voice (`scripts/kokoro_tts.py`); `src/assets.py` gathers a visual per shot (data cards, Wikimedia
   photos of real people and places, AI stills for mood).
4. `src/render.py` assembles the video (animated cards, dissolves, music ducked under the voice); `src/shorts.py` builds the Shorts natively at 9:16.
5. `src/upload.py` uploads everything. A Telegram message reports the result,
   including when YouTube keeps a video private or a run fails.

## Things you will want to change

- **Voice.** Narration uses Kokoro-82M, free and Apache-2.0. Change the narrator
  or speed under `voice.kokoro` in `config.yaml`, and add any name it says wrongly
  to `voice.kokoro.lexicon`. `voice.provider: clone` brings back the owner's cloned
  voice; `elevenlabs` uses ElevenLabs (paid plan needed for daily videos).
- **Tone.** `channel/voice.md` is pasted into every script prompt.
- **What comes next.** `data/queue.yaml` lists stories that go ahead of the automatic picks.
  After it, long videos run in fours: explainer, history, explainer, news (`long_slot` in `main.py`).
- **Regions and priorities.** `trending.regions` in `config.yaml`.
- **Review before publishing.** Set `gates.topic_approval` / `gates.final_review`
  to `true` to get Telegram approval buttons.
- **Original documentary format.** Set `mode: documentary`.

## Local run

```bash
.venv/bin/python main.py --no-upload                       # needs a filled-in .env
.venv/bin/python main.py --topic "Fed rate cut" --region US --no-upload
```
