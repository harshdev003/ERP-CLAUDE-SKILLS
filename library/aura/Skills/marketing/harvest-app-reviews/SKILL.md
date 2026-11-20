---
name: harvest-app-reviews
description: >
  Harvest public App Store and Google Play reviews for Aura and Aurie and turn
  them into a themed digest of billing and cancellation friction, bugs, paywall
  issues, AI-quality complaints, feature requests and what users praise, broken
  down by app version. No credentials: iOS reads Apple's public reviews feed,
  Play uses the google-play-scraper package. Every review is archived locally so
  history builds past the stores' 500-review cap. Trigger on "harvest app
  reviews", "what are people saying in app store reviews", "summarize our App
  Store reviews", "Google Play reviews", "review digest", "what do 1-star
  reviews say", "app store feedback for Aurie", or "external user voice". Do NOT
  use for in-app ratings of Aurie conversations (`data:investigate-user-rating`),
  cancellation feedback from the k8bot feed
  (`data:investigate-cancellation-feedback`), or Reddit and social listening.
metadata:
  version: "1.0.1"
knowledge:
  reads:
    - Guide/Index                  # baseline routing aid (universal)
    - Products/Aura                # features and pricing, to recognise what a review is about
    - Products/Aurie
    - Compliance/Privacy           # reviewer names are never emitted
    - Tools/Slack                  # only when the digest is shared
---

# harvest-app-reviews

Pull what users say about Aura and Aurie **in the stores**, outside the app, and
summarize it as themes the product and marketing teams can act on. This is the
store-review half of what social-listening vendors sell. It is free, and it is
most of the signal: in the 2026-10-15 probe, Reddit gave 3–5 genuine mentions per
100 hits, while the stores gave 500+ reviews per country.

## What the sources give you

| Source | App | Reach | Fields |
|---|---|---|---|
| Apple customer-reviews RSS (public, no auth) | Aura `1114223104`, Aurie `6748324270` | Newest **500 per country** | date, stars, version, title, text, helpful votes |
| Google Play via `google-play-scraper` | Aura `com.aurahealth` (Aurie isn't on Play) | Newest **500**, English | date, stars, version, text, thumbs-up, **our reply** |

Play serves reviews by language, not by storefront, so the script fetches Play
once. Neither store says where a reviewer lives beyond the store country.

## Step 1 — Fetch

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/harvest-app-reviews/scripts/fetch_reviews.py" \
    --since 30d --out /tmp/app-reviews.json
```

Flags: `--since Nd|YYYY-MM-DD` (default `30d`), `--apps aura,aurie`,
`--stores ios,play`, `--countries us,gb,ca,au`, `--archive PATH` (default
`${APP_REVIEWS_DIR:-~/.cache/app-reviews}/reviews.jsonl`), `--no-archive`.

Google Play needs `pip install google-play-scraper`. Without it the Play
source reports `status: error, unavailable: …` and the run continues with iOS. Say so
in the digest; never present an iOS-only run as covering both stores.

The output JSON has:

- `meta.sources[]`: one row per app × store × country, with `fetched`, `oldest`,
  `status` and `window_truncated`.
- `meta.counts`: `in_window`, `new_to_archive`, `sources_error`, `sources_truncated`.
- `stats`: per `app/store`, the rating distribution, average, low-star count
  (1–2★) and `by_version`.
- `reviews[]`: every review in the window, newest first, reviewer names omitted.

**Read `meta` before the reviews.** A source with `status: error` or `partial` (stopped part-way), or a
`window_truncated` source that the archive doesn't cover means part of the window
is missing. Nothing in `reviews[]` shows that gap, so the digest has to state it.
Truncation means the store's 500-review cap ends after the start of `--since`.
Shorten the window, or run the skill more often so the archive closes the gap.

## Step 2 — Theme

Read every review rated 1–3★, and the 4–5★ reviews for the "what works" theme.
Assign each review one or more themes from this fixed list. It is fixed so that
digests can be compared run to run:

| Theme | Examples |
|---|---|
| **Billing & cancellation** | can't cancel, charged after trial, subscription not visible in Apple/Google, refund |
| **Paywall & access** | paid but locked out, stuck on a paywall, no free tier, restore purchase fails |
| **Bugs & stability** | crashes, freezes, audio stops, login/reset failures, disconnects |
| **AI quality** (Aurie mostly) | repetitive or canned answers, poor memory, voice, tone |
| **Content & feature requests** | missing content types, offline, integrations, "I wish it…" |
| **Onboarding & UX** | confusing navigation, too complex, can't find things |
| **What works** | the specific things 4–5★ reviewers credit |

Rules:

- **Count before you characterize.** Every theme carries `n` reviews and the
  share of the app's low-star reviews. Don't call a theme "a few" or "many" without the number.
- **Version matters.** When a theme clusters on one version (use
  `stats.*.by_version` and each review's `version`), name the version. A spike
  on one release is a regression lead; a theme spread across versions is a
  standing issue.
- **Quote, don't paraphrase,** at most 2 short quotes per theme. Never include a
  reviewer name (`Compliance/Privacy`). The script drops usernames and rewrites the
  `Hi <Name>,` greeting our support replies open with to `Hi [name],`. Any other name
  in the digest came from free text (a review signed with a name, or a reply naming
  the reviewer mid-sentence) and should be cut.
- **Play replies:** say how many low-star Play reviews have our reply, and flag
  replies that point at instructions the review says don't exist. That combination
  showed up in the probe.

## Step 3 — Digest

Return the digest in chat, in this shape:

```
App reviews — <since> to <today> (<n> reviews: Aura iOS n, Aura Play n, Aurie iOS n)
Coverage: <sources ok> · <any error / truncated source, by name — or "complete">

Aura    avg ★x.x (iOS) / ★x.x (Play) · low-star n
Aurie   avg ★x.x (iOS) · low-star n

Top themes (low-star)
1. <Theme> — n (x%) · <app(s)> · <version note>
   "<quote>" (★1, iOS 3.24.0)
…
What works — <2–3 lines>
Emerging asks — <feature requests seen ≥2 times, with n>
```

**Sharing.** Only when the user asks, post the digest with `shared:post-to-slack`
and `--footer marketing:harvest-app-reviews` (script-built footer, never typed).

## Archive

The archive is append-only JSONL deduped on `(store, id)`. It is local to the
machine that runs the skill and is not shared. A Notion `Signals/App Reviews` table
would make it shared and is the natural next step; that needs its own schema file
under `record-knowledge`. Nothing in the archive is personal beyond the public
review text.

## Out of scope

- **Reddit, X, Instagram, TikTok.** Reddit's free paths are an unofficial
  archive plus heavy name ambiguity (Oura, Aura frames, identity-theft Aura).
  X search is paid. Instagram and TikTok only expose comments on our own posts.
- **Replying to reviews.** This skill is read-only.
- **Competitor reviews.** Add the competitor's app IDs to `APPS` in the script
  if this becomes a need.
