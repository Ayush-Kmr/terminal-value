# Terminal Value — documentary pipeline

Semi-automated production for [@terminalvaluedoc](https://www.youtube.com/@terminalvaluedoc):
12-minute business documentaries, twice a week, plus Shorts cut from each episode.

**Two human gates, deliberately.** YouTube's July 2026 clarification of the
inauthentic-content policy targets exactly one pattern: an AI voice reading an
AI script over stock footage with no editorial judgement anywhere in the loop.
A fully autonomous version of this pipeline would be cheaper to run and would
very likely never be monetised. So you approve the topic, and you watch the cut.
Twelve minutes per episode. Everything else is automatic.

```
topic picker (your own performance history + competitor outliers)
   └─► ❶ GATE: you pick the company + angle on Telegram
        └─► research (Wikipedia → SEC EDGAR → LLM extraction → validation)
             └─► 5 cold opens generated, scored, best one survives
                  └─► script written beat by beat (6-beat spine, never one pass)
                       └─► ElevenLabs narration, one file per shot
                            └─► visuals: motion graphics ▸ archival ▸ AI stills
                                 └─► ffmpeg assembly, loudness-normalised
                                      └─► title options linted, description, chapters, captions, thumbnail
                                           └─► ❷ GATE: you watch it on your phone
                                                └─► upload, schedule, Shorts, playlist
                                                     └─► history.json ──┐
                                                                        │
         weekly reach report: outliers, retitle candidates ◄────────────┘
```

## Why this is built differently from a typical faceless pipeline

**Motion graphics are the primary visual, not a fallback.** Generated video runs
roughly $0.07–0.15 per second at current API rates, so a 12-minute episode of it
costs $30–65 and still cannot show a real logo, a real founder, or a real balance
sheet. Charts, stat reveals, timelines, quote cards and document highlights are
free, instantly on-brand, and are what the channels that win this niche actually
use. AI stills handle mood and era. Generated video is off by default.

**Research is validated before a word is scripted.** Wikipedia may start the
research; it may never finish it. The pipeline pulls SEC filings, counts sources,
tracks which facts are unverified, and surfaces warnings at gate 2 rather than
letting thin research reach the render.

**Nothing publishes on a timeout.** If you don't answer a gate, the run holds.

## Commands

```bash
python main.py                              # full run
python main.py --topic "Jawbone"            # skip the topic picker
python main.py --topic "Jawbone" --no-upload   # build locally, inspect it
python main.py --no-gates                   # bypass approval (don't)
python main.py --reach                      # performance report only
```

Output lands in `output/<date>-<company>/`: `final.mp4`, `thumbnail.jpg`,
`script.json`, `research.json`, `metadata.json`, `captions.srt`, `shorts/`.

## Running costs

| Item | Monthly |
|---|---|
| ElevenLabs Creator (narration) | $22 |
| Gemini / Groq (scripts, research, SEO) | $0 free tier |
| Cloudflare Workers AI (stills) | $0 free tier |
| Pexels / Pixabay / Wikimedia | $0 |
| GitHub Actions | $0 under the free minutes |
| **Total** | **~$22** |

Voice is the only paid line and it's the right one — at roughly 11,000 characters
per episode it costs about $1 a video, and the narrator *is* the brand.

## Documentation

- [`SETUP.md`](SETUP.md) — one-time setup, in order, about 90 minutes
- [`docs/STRATEGY.md`](docs/STRATEGY.md) — the 100-day plan and the monetisation maths
- [`docs/SEO-PLAYBOOK.md`](docs/SEO-PLAYBOOK.md) — packaging, titles, thumbnails, retention
- [`channel/voice.md`](channel/voice.md) — narrator rules (edit this often)
- [`channel/visual-identity.md`](channel/visual-identity.md) — the visual system
