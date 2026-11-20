---
name: harvest-social-mentions
description: >
  Harvest public Reddit posts and comments that mention Aura or Aurie, keep the
  ones that are really about our apps, and summarize what people say: why they
  recommend it, what they compare it to, complaints, and which communities talk
  about it. "Aura" is a common word (photo frames, Oura ring, identity-theft
  Aura, spiritual aura), so every hit is relevance-checked and the drop count is
  reported. No credentials: Reddit is read through the PullPush archive.
  Mentions are archived locally so history builds run to run. Trigger on
  "harvest social mentions", "what is Reddit saying about Aura", "Reddit
  mentions", "social listening", "who recommends Aura on Reddit", "brand
  mentions", or "what do people say about Aurie online". Do NOT use for App
  Store or Google Play reviews (`marketing:harvest-app-reviews`), in-app ratings
  (`data:investigate-user-rating`), or posting to social media.
metadata:
  version: "1.0.1"
knowledge:
  reads:
    - Guide/Index                  # baseline routing aid (universal)
    - Products/Aura                # to recognise features and content a post refers to
    - Products/Aurie
    - Domain/Competitors           # comparisons people draw (Calm, Headspace, Insight Timer)
    - Compliance/Privacy           # Reddit usernames are never emitted
    - Tools/Slack                  # only when the digest is shared
---

# harvest-social-mentions

Find what people say about Aura and Aurie **on Reddit**, unprompted, and turn the few
real mentions into a short digest. It complements `marketing:harvest-app-reviews`.
Store reviews are mostly complaints written at the moment of friction. Reddit
mentions are mostly recommendations, comparisons and use cases, the reasons people
stay.

**Expect low volume.** The 2026-10-15 probe found about 12 genuine mentions in 90 days
out of 171 hits, and none for Aurie. Every one praised the hypnosis and sleep tracks
or shared a track link. A digest with 2–5 mentions is a normal week, not a broken run.

X is the planned second source (pay-per-use API, blocked on a developer account).
YouTube was probed and skipped: a handful of review videos with 1–4 comments each.

## Step 1 — Fetch

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/harvest-social-mentions/scripts/fetch_mentions.py" \
    --since 30d --out /tmp/social-mentions.json
```

Flags: `--since Nd|YYYY-MM-DD` (default `30d`), `--queries` (comma-separated, default
`"aura app"`, `"aura meditation"`, `"aura health"`, `aurahealth`, `aurie`),
`--delay` seconds between requests (default 5), `--archive PATH` (default
`${SOCIAL_MENTIONS_DIR:-~/.cache/social-mentions}/mentions.jsonl`), `--no-archive`.

**PullPush rate-limits hard.** A burst of about 12 fast requests got this machine
blocked for over 45 minutes on 2026-10-15. Keep `--delay` at 5 or more, and don't run
the skill in a tight loop. A blocked query reports `status: error` with `HTTP 429`.
Re-run later; the archive keeps what earlier runs found. PullPush is also a few days
behind Reddit, so `meta.newest_seen` tells you where the data stops.

Output:

- `meta.sources[]`: one row per query × kind (`submission`, `comment`), with `status`,
  `fetched` and `newest`.
- `meta.counts`: `in_window`, `prefilter_pass`, `prefilter_fail`, `sources_error`.
- `mentions[]`: every hit in the window, newest first. Each has `community`
  (subreddit), `url`, `link` (a link post's target), `title`, `text`, `score`, `queries` and
  `prefilter`. No usernames.

## Step 2 — Judge relevance

`prefilter: true` means the text has meditation, sleep, stress or Aura-domain context
near "aura", or names `aurahealth`, `aura app`, or Aurie with app or AI context. It is
deliberately loose: it passed all 12 genuine mentions in the probe, along with about
65 that aren't ours. **The script never drops a hit.** You decide relevance, on read:

1. Skip `prefilter: false` hits. Report their count.
2. Read every `prefilter: true` hit and mark it **ours** only when the text is clearly
   about Aura Health's meditation/sleep/hypnosis app or Aurie. Signals: aurahealth.io
   links, hypnosis or sleep tracks, coaches, the 7-day trial, comparisons with
   Calm, Headspace or Insight Timer.
3. Not ours: Aura photo frames, Oura ring, Asus/TUF Aura, Aura identity-theft
   protection, Meta Quest "Aura", other apps named Aura, spiritual or fiction "aura",
   and "Aurie" as a name or place.
4. Report `ours` / `not ours` / `prefilter fail`. When unsure, say so in the digest
   instead of guessing; don't count the hit either way.

## Step 3 — Digest

```
Reddit mentions — <since> to <newest_seen> (data lags; newest_seen = where PullPush stops)
Coverage: <n queries ok> · <any errored query, by name — or "complete">
Hits: <in_window> · prefilter pass <n> · ours <n> · not ours <n> · unsure <n>

What people credit — <themes with n, e.g. "hypnosis tracks (5)", 1 short quote each>
Comparisons — <apps mentioned alongside Aura, with n>
Complaints / asks — <themes with n, or "none">
Where — <communities with n>
Aurie — <n mentions, or "none">
Links — <url per mention marked ours>
```

Quote at most one short line per theme, and never a username. When the user asks to
share it, post with `shared:post-to-slack --footer marketing:harvest-social-mentions`.

## Out of scope

- **X / Twitter.** Planned second source. It needs an X developer account with
  pay-per-use credits.
- **Official Reddit API.** Needs Reddit's manual approval, and our use is commercial.
  PullPush is the no-approval path. If approval is granted, swap the transport in
  `fetch_pullpush` and keep the output shape.
- **Replying or posting** on Reddit. This skill is read-only.
