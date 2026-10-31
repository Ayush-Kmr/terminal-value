# Terminal Value

Automated YouTube channel for a US audience: it finds the American business
stories people are searching for and explains them in English: a 10-minute video
every other day and two standalone Shorts a day. The Tamil channel is paused.

Channel: <https://www.youtube.com/@terminalvaluedoc>

## What runs, and when

| Workflow | When | What it does |
|---|---|---|
| **Episode** | daily 03:30 UTC | alternate days: top story → research → script → voice → 1 long video. Every day: 2 standalone Shorts on the next stories |
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
3. `src/script.py` writes five sections; `src/voice.py` narrates each shot in the
   owner's cloned voice (an open model run by `scripts/clone_tts.py`); `src/assets.py` gathers a visual per shot (data cards, Wikimedia
   photos of real people and places, AI stills for mood).
4. `src/render.py` assembles the video (animated cards, dissolves, music ducked under the voice); `src/shorts.py` builds the Shorts natively at 9:16.
5. `src/upload.py` uploads everything. A Telegram message reports the result,
   including when YouTube keeps a video private or a run fails.

## Things you will want to change

- **Voice.** Narration is cloned from `assets/voice/ref_hi.wav` (Hindi) and
  `ref_en.wav` (English), cut from the owner's own recording. Replace those two
  clips (10-20 s of clean speech each) to change the narrator; tune delivery
  under `voice.clone.settings` in `config.yaml`. Set `voice.provider: elevenlabs`
  to use ElevenLabs instead (paid plan needed for daily videos).
- **Tone.** `channel/voice.md` is pasted into every script prompt.
- **Regions and priorities.** `trending.regions` in `config.yaml`.
- **Review before publishing.** Set `gates.topic_approval` / `gates.final_review`
  to `true` to get Telegram approval buttons.
- **Original documentary format.** Set `mode: documentary`.

## Local run

```bash
.venv/bin/python main.py --no-upload                       # needs a filled-in .env
.venv/bin/python main.py --topic "Fed rate cut" --region US --no-upload
```
