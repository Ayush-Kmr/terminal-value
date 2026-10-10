# Strategy

## The maths you're actually playing against

YouTube Partner Programme needs **1,000 subscribers** and **4,000 public watch
hours in 12 months**. Shorts views do not count toward those hours — Shorts have
their own separate 10M-views-in-90-days path. So the two formats do different jobs:

- **Longs buy watch hours.** Nothing else does.
- **Shorts buy subscribers.** They're cheap here because they're cut from the long.

4,000 hours = 240,000 watch minutes. What that costs in views:

| Episode length | Avg view % | Minutes/view | Views needed |
|---|---|---|---|
| 7 min | 45% | 3.2 | **76,000** |
| 12 min | 45% | 5.4 | **44,000** |
| 12 min | 55% | 6.6 | **36,000** |
| 18 min | 45% | 8.1 | **30,000** |

This is why the format is 12 minutes, not 7. Going longer is the single cheapest
lever on time-to-monetisation, and it costs you nothing but a longer script.

Once you're past 20 episodes and retention data is real, consider pushing toward
15–18 minutes. Don't start there — long episodes with weak retention are worse
than short ones.

## Why this niche, specifically

Business documentary audiences attract B2B software, brokerage and business
education advertisers — the highest-paying categories on the platform. Published
sales listings for faceless documentary channels in adjacent niches show RPMs
above $20 with 98% margins, and a history-documentary channel with 1M subs
reporting $416K over twelve months at a $7.44 RPM. Business skews higher than
history. Treat those as upper bounds from sellers with an incentive, but the
direction is real: this is a premium-RPM niche, which is why 44,000 views is
worth more here than 300,000 views on a gaming channel.

The other property that matters: **these videos don't decay.** A documentary
about a 2012 bankruptcy is as watchable in 2029 as today. Forty good episodes
keep earning. That's the asset you're building, and it's why quality per episode
beats volume.

## The competitive wedge

Company Man, Modern MBA, Business Casual and Bright Sun Films own the obvious
American stories. Going head-to-head on Blockbuster or Kodak means competing
with a ten-year back catalogue and better production.

Three wedges available to you:

1. **Primary-source rigour.** Most channels in this niche retell the Wikipedia
   version with better editing. Building every episode off filings, court records
   and earnings transcripts is both a differentiator and the thing that keeps you
   clear of the inauthentic-content policy. This is what the pipeline enforces.

2. **Asian and Indian business collapses, told for a global audience.** Almost
   nobody is doing this in English at documentary quality, and you have cultural
   and language access that American creators don't. Satyam, Kingfisher Airlines,
   Yes Bank, Jet Airways, Evergrande, Luckin Coffee, Byju's. Global audiences
   *will* watch these — the constraint has always been that nobody makes them well.
   This is probably your strongest long-term position.

3. **The recent collapse.** Everyone covers a bankruptcy three years later. A
   rigorous 12-minute film within six weeks of a Chapter 11 filing has almost no
   competition and enormous search demand.

I'd ship episodes 1–10 as a mix of (1) and (3) to establish the format, then lean
hard into (2) once the channel has a voice.

## The 100 days

**Days 1–14 — build the format, publish nothing**
Produce four full episodes locally with `--no-upload`. Rewrite `channel/voice.md`
after each one. You're not looking for good; you're looking for *consistent*. The
fourth should sound like the first in a way nobody could mistake for anyone else.

**Days 15–30 — publish the first four, two per week**
Resist the urge to publish all four at once. Two per week from day one trains
both the algorithm and you. Watch retention graphs, not view counts — at this
stage views tell you nothing and the retention curve tells you everything.

**Days 31–60 — iterate on packaging, not content**
By now you have eight to twelve episodes and real retention data. Almost every
problem at this stage is packaging, not filmmaking. Run the weekly reach report.
Retitle and re-thumbnail anything under 40% of your median — one change at a
time, two weeks apart, or you learn nothing.

**Days 61–100 — find the vein and mine it**
One or two episodes will outperform your median by 2.5x or more. That outlier is
telling you what your audience actually wants from you, and it is usually not
what you expected. Make three more like it immediately. This is the single
highest-return action available on YouTube and most people ignore it because
they're attached to their original plan.

Realistic expectation: monetisation is unlikely inside 100 days and that is fine.
The 100-day commitment is about building the machine and the habit. Channels in
this niche that work typically take 6–12 months to cross the threshold, and then
the back catalogue earns indefinitely.

## Staying monetisable

The policy that matters is **inauthentic content**, clarified on 16 July 2026
into three non-monetisable buckets: generic or template-based content; off-putting
content; and AI personas giving advice on sensitive topics like health, finance
or law. Enforcement is a three-strike ladder — warning, 90-day suspension,
permanent removal. A January 2026 wave terminated 16 channels holding 4.7 billion
views between them.

What is **explicitly still allowed**: faceless narration over stock footage,
recurring series formats where every episode follows the same structure, AI used
for scripting, voice synthesis, editing and thumbnails, and consistent intros and
graphics across a whole catalogue. The line is not AI. The line is whether there
is original research, argument and human judgement in the video.

How this build stays on the right side of it:

- Two human gates, and nothing publishes on a timeout.
- Every episode makes a **claim** that could be wrong, not a recap.
- Every episode must contain one fact a well-informed viewer hasn't heard. If
  research can't find one, kill the topic.
- Primary sources required; research warnings surface at gate 2.
- Synthetic-media disclosure set on every upload, and stated in the description.
- A visible corrections policy, honoured.

One caution on bucket three: you are adjacent to finance, and the prohibition is
on **AI personas giving financial advice**. Historical analysis of a company's
collapse is not advice. Keep it that way — never let a script drift into what the
viewer should buy, sell or hold. If an episode's lesson beat starts sounding like
a recommendation, rewrite it.

## When to spend more

Don't upgrade anything until a specific constraint bites.

| Trigger | Spend |
|---|---|
| Retention dies in the first 30 seconds across several videos | Nothing — rewrite `voice.md`. This is never a money problem. |
| You're at 15+ episodes and the visuals feel repetitive | ~$5/episode of AI hero shots: set `visuals.ai_video.enabled: true` |
| Research is the bottleneck on your time | A paid news archive, or Gemini Pro for longer context |
| You're monetised and want to scale | A human researcher at $50–100/episode, before any tooling |
