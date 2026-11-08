# Bhojpuri AI Comedy: week plan, 9-15 November 2026

Channel: <https://www.youtube.com/@BhojpuriAIComedyShorts> · two skit Shorts a day,
made and scheduled automatically by the `bhojpuri` job in the daily **Episode**
run (03:30 UTC = 09:00 IST).

## The daily shape

| Slot (IST) | What | Source |
|---|---|---|
| **12:30** (lunch) | Festival / everyday skit | `data/bhojpuri_ideas.yaml`: dated festival ideas first, then seasonal, then the rest |
| **19:30** (evening peak) | **Trending skit** | YouTube's India trending chart, videos with **1M+ views**: the subject becomes a Chacha/Chachi/Bablu premise; its search keyword goes in the title and tags |

Every skit: 6-9 lines in Bhojpuri, one cartoon per line with the speaker
talking along with their voice and their name tag, captions timed from the
voice, up to three sound effects, a beat before the punchline and a
ba-dum-tss after it.

## This week

Dates of Bhai Dooj and Chhath are approximate; the dated ideas cover a day
either side.

| Day | 12:30 skit | 19:30 skit |
|---|---|---|
| Sun 8 (Diwali) | posted at once on launch: festival idea + trending | |
| Mon 9 | Leftover Diwali mithai: Chacha hides the sweets and forgets where | Trending (1M+) |
| Tue 10 | Bhai Dooj: Bablu's "free online course" gift | Trending (1M+) |
| Wed 11 | Bhai Dooj (if not used) / Diwali cleaning: Chacha won't throw away the broken radio | Trending (1M+) |
| Thu 12 | Chhath thekua: Chacha cooks from a reel | Trending (1M+) |
| Fri 13 | Chhath rush: Bablu on the packed train home, Chacha's seat "advice" | Trending (1M+) |
| Sat 14 | Chhath ghat: Bablu's sunset selfie, Chacha photobombs | Trending (1M+) |
| Sun 15 | Wedding season: the first card, Chacha plans the food "war" | Trending (1M+) |

That is 16 Shorts by Sunday night.

## What to watch (Telegram `/bhojpuri`, Studio)

- **Day 1-2:** every Short has its captions, voices are clear, no picture
  repeats from a failed image (the run log says "keeps the previous picture").
- **Wednesday 11:** first numbers. A Short is working if more than 70% of
  viewers watch to the end ("Viewed vs swiped away" in Studio) and it gets
  100+ views in 48 hours.
- **Sunday 15 review:**
  - Trending vs festival skits: which slot gets more views per Short? Give
    the winner both slots next week (`skit.trending.per_day` in `channels/bh.yaml`).
  - Which character's skits do best? Write more premises around them.
  - Any skit that people comment is "Hindi, not Bhojpuri": note the words
    and add them to the language rules in `src/skit.py`.
  - Average length: if Shorts over 35 s are swiped more, lower
    `skit.max_words` from 85 to 70.

## By hand, once

- Listen to the first two skits on a phone, with sound. If a voice is
  unclear, say which character: their voice description in
  `channels/bh.yaml` is the knob.
- Optional: make the channel's own token secret `YT_REFRESH_TOKEN_BH`
  (SETUP.md §11); until then the old secret is used, and it works.
