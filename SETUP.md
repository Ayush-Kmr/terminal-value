# Setup — do these in order

About 90 minutes total. Step 4 has a waiting period measured in weeks, so start
it today even though it sits fourth.

---

## 1. The channel (done)

- **Terminal Value** — `@terminalvaluedoc` — `UCm1uFmKTwjzhIAUeYC0NcZQ`
- Verify at <https://www.youtube.com/verify> if you haven't. Without it there are
  no custom thumbnails and no uploads over 15 minutes. Thumbnails are most of
  your click-through rate, so this is not optional.
- Settings → Upload defaults: **not made for kids**, comments on with "hold
  potentially inappropriate for review", category Education.

## 2. Free API keys (20 min)

| Key | Where | For |
|---|---|---|
| `GEMINI_API_KEY` | <https://aistudio.google.com/apikey> | scripts, research, SEO |
| `GROQ_API_KEY` | <https://console.groq.com/keys> | fallback when Gemini rate-limits |
| `CF_ACCOUNT_ID`, `CF_API_TOKEN` | <https://dash.cloudflare.com/profile/api-tokens> → "Workers AI" template | AI stills, 10k neurons/day free |
| `PIXABAY_API_KEY` | <https://pixabay.com/api/docs/> | stock imagery |
| `PEXELS_API_KEY` | <https://www.pexels.com/api/> | stock video (new keys are sometimes paused — skip if so) |

## 3. The voice — your own, cloned (free)

Narration is spoken in your own voice by an open cloning model that runs on the
build machine, so there is no per-character bill.

1. Record about two minutes of yourself reading Hindi and English in a quiet room.
2. Clean it and cut one 10-20 second clip per language into
   `assets/voice/ref_hi.wav` and `assets/voice/ref_en.wav` (24 kHz mono).
3. To run locally: `python3.12 -m venv .venv-voice`, then
   `.venv-voice/bin/pip install -r requirements-voice.txt`. GitHub Actions does
   this itself and caches it.

ElevenLabs is still supported: set `voice.provider: elevenlabs` in `config.yaml`
and put a voice ID in `ELEVENLABS_VOICE_ID` (Creator plan, $22/mo, for daily videos).

Test it before committing — this voice is the channel's single most recognisable
asset and changing it at episode 30 resets your audience's sense of who you are.

## 4. YouTube API access — START TODAY, approval takes weeks

1. <https://console.cloud.google.com/> → new project → **APIs & Services → Library**
   → enable **YouTube Data API v3** *and* **YouTube Analytics API**.
2. **OAuth consent screen** → External → add your email as a test user →
   **then click "Publish app" (In production)**. If you leave it in Testing, the
   refresh token expires every 7 days and the pipeline silently dies each week.
3. **Credentials → Create credentials → OAuth client ID → Desktop app** → download JSON.
4. Locally:
   ```bash
   python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
   .venv/bin/python scripts/get_youtube_token.py ~/Downloads/client_secret_*.json
   ```
   Google warns the app is unverified. It's your own app — Advanced → Continue.
5. **Submit the API audit form: <https://support.google.com/youtube/contact/yt_api_form>**

   Until Google approves it, every video uploaded through the API is locked to
   Private no matter what the code requests. That is their rule for new projects,
   not a bug. Approval is free and takes days to weeks. Submit it now; while you
   wait, the pipeline still produces and uploads, and you flip videos public by
   hand in Studio.

## 5. Telegram approval gates (10 min)

1. Message [@BotFather](https://t.me/BotFather) → `/newbot` → copy the token into
   `TELEGRAM_BOT_TOKEN`.
2. Message [@userinfobot](https://t.me/userinfobot) → copy your numeric ID into
   `TELEGRAM_CHAT_ID`.
3. Send your new bot any message once, so it's allowed to message you back.

Both gates now arrive as buttons on your phone. You can approve a topic in a lift
and review a cut on a lunch break, which is the whole point given you have a day job.

## 6. Fonts (5 min)

Download and drop into `assets/fonts/`:
- [Archivo Black](https://fonts.google.com/specimen/Archivo+Black) → `Archivo-Black.ttf`
- [Inter](https://fonts.google.com/specimen/Inter) → `Inter-Regular.ttf`
- [JetBrains Mono](https://fonts.google.com/specimen/JetBrains+Mono) → `JetBrainsMono-Regular.ttf`

All three are SIL Open Font License — free for commercial use including video.
The pipeline falls back to DejaVu if they're missing, but it will look generic.

## 7. Music (optional, 5 min)

Drop `.mp3` files into `assets/music/`. **Only** from YouTube Studio's own Audio
Library, filtered to *"Attribution not required"*. Free music from anywhere else
is frequently registered with Content ID and will get your video claimed.

## 8. Repo + secrets (10 min)

```bash
cd terminal-value
git init && git add -A && git commit -m "Initial pipeline"
gh repo create Ayush-Kmr/terminal-value --private --source=. --push
```

Then **Settings → Secrets and variables → Actions** and add every key from
`.env.example`. The workflow reads them by exactly those names.

## 9. First run — locally, no upload

```bash
cp .env.example .env     # fill it in
.venv/bin/python main.py --topic "Jawbone" --no-upload --no-gates
```

Watch the whole thing start to finish. Then open `channel/voice.md` and change
whatever made you wince. Do this three or four times before you ever let it
upload — the voice file is where almost all of the quality lives, and nobody
gets it right on the first pass.

## 10. Go live

Actions → **Episode** → Run workflow, with `no_upload` ticked for the first run.
Download the artifact, watch it. If it's good, run it again unticked.

After that it runs Mondays and Thursdays at 10:10 IST, producing a day ahead of
the Tuesday/Friday publish slots so you have time to review.

---

## 11. Tamil channel (same videos, in Tamil)

Every day, after the English videos are made, the **Episode** workflow's
`tamil` job translates each one shot by shot, narrates it in Tamil with
AI4Bharat's open Indic Parler-TTS (free, runs inside GitHub Actions, voice
"Jaya") and uploads it to the Tamil channel at the same publish slots. Same
stories, same pictures. Settings live in `channels/ta.yaml`; its history in
`data/history.ta.json`. The job does nothing until step 3 below is done.

1. **Create the channel** on the same Google account: youtube.com → profile
   picture → Settings → *Add or manage your channel(s)* → **Create a channel**.
   Name it (e.g. *Terminal Value Tamil*). Then verify it at
   <https://www.youtube.com/verify> (each channel is verified separately).
2. **Hugging Face token** (the Tamil voice model is free but asks you to accept
   its terms once): sign up at <https://huggingface.co/join>, open
   <https://huggingface.co/ai4bharat/indic-parler-tts> and click **Agree and
   access**, then make a *Read* token at
   <https://huggingface.co/settings/tokens> and save it as the GitHub secret
   `HF_TOKEN`.
3. **YouTube token for the Tamil channel**, from the repo folder:

   ```bash
   .venv/bin/python scripts/get_youtube_token.py ~/Downloads/client_secret_*.json --secret YT_REFRESH_TOKEN_TA
   ```

   When Google asks which channel, pick the **Tamil** one. The script prints the
   channel's name and ID: the ID goes in `channel.channel_id` in
   `channels/ta.yaml` (uploads refuse to go anywhere else).
4. Actions → **Brand channel** → Run workflow with channel `ta` (banner, About,
   keywords). Upload `assets/brand/avatar.png` as the profile picture by hand.
