---
name: investigate-coach
description: >
  Produces a standardized investigation report for a single Aura coach. Given a coach ID
  (Firebase UID) or name, pulls the coach catalog and per-track detail from the Aura API, then
  synthesises a fact sheet covering upload cadence broken down by trackType and by locale (the
  cap is 10/month per locale, so a multi-locale coach is evaluated lane-by-lane), peer-band
  comparison, content composition, engagement quality, catalog concentration and boost-queue
  saturation. Closes with a green/yellow/red recommendation naming each locale over the cap.
  Trigger on "investigate coach {id-or-name}", "analyze coach {name}", "look into coach {id}",
  "is coach X suspicious", "give me a fact sheet on coach {id}", "compare coach X to peers",
  "what does coach X look like", "is this coach gaming the algorithm", or a pasted coach ID or
  name with intent to assess them. Do NOT use for a user's listening behaviour
  (`analyze-user-behavior`) or cohort analysis (Mixpanel UI).

metadata:
  version: "3.1.0"
knowledge:
  reads:
    - Compliance/Privacy                             # PII handling rule — coach names + emails are mildly sensitive; redact bio/contact details in any external paste
    - Guide/Index                                    # baseline routing aid (universal)
    - Products/Aura                             # what Aura is — frames the "what's normal for an Aura coach"
    - Products/Aura/Coaches/Coach Upload Policy         # 10/month cap, 100-track threshold, $6 QA cost, score_n boost mechanics
    - Tools/Aura API                                 # auth + base URL
    - Tools/Aura API/Content         # endpoint catalog this skill calls — /coaches, /coaches/<id>, /contents/<id>; types-shape quirk
    - Tools/Mixpanel                                 # Coach attribution section — why Mixpanel can't answer the upload-side questions this skill answers
    - Tools/Slack                                    # canonical Channel IDs (for skills) — resolves #coach-upload-monitor (cited in body)
---

# investigate-coach

End-to-end investigation report for one Aura coach. The skill **returns the
report as its result** — the caller decides what to do with it (paste in
Slack, drop in Notion, feed to another skill). No persistence.

## When to use

Trigger when the user asks any of:

- "investigate coach `<id>`" / "investigate coach `<name>`"
- "analyze coach `<id-or-name>`"
- "look into coach `<id-or-name>`"
- "is coach X suspicious?" / "is coach X gaming the algorithm?"
- "give me a fact sheet on coach `<id-or-name>`"
- "compare coach X to peers"
- "what does coach X look like?"
- pastes a 28-char Firebase UID or a coach display name with intent to assess

Common framing question this skill answers: *"Is this coach producing healthy
content at a sustainable pace, or are they gaming the upload-volume lever?"*

**Do NOT trigger for:**

- Per-user listening / friction analysis → `analyze-user-behavior` or
  `analyze-user-conversations`.
- Per-experiment readouts → `analyze-experiment`.
- Cohort or aggregate behavioural analysis → Mixpanel UI directly.
- Routine cap-violation monitoring → the daily
  [#coach-upload-monitor](https://auramobile.slack.com/archives/C0B54DA3AT0)
  alert (GitHub Action at
  `.github/workflows/coach-upload-monitor.yml`) covers it.

## Inputs

- **`coach`** (required) — either a Firebase UID (28 alphanumeric chars, e.g.
  `Vkbbn5FAFabksyo94kWWbqFrGRB2`) **or** a coach display name (case-insensitive
  substring match against `name` in the catalog, e.g. `Sonic Healer`). If the
  name matches multiple coaches, the skill surfaces the candidates and asks
  the user to pick one.
- **`include_tracks`** (optional, default `true`) — when `true`, fetches every
  track's detail to compute trackType breakdowns and the top-tracks table. For
  coaches with very large catalogs (1,000+ tracks) this adds ~30–60 seconds of
  parallel fetching. Set to `false` to skip and produce a faster, lighter
  report without trackType analysis.
- **`peer_band`** (optional) — explicit follower-count band for peer
  comparison as `min,max` (e.g. `5000,10000`). If omitted, the skill picks the
  band adaptively: `floor(0.7 * followers), ceil(1.4 * followers)` with floors
  at 1,000 followers minimum band-width.

If `coach` is missing, ask. Don't guess.

## Prerequisites

- **`AURA_API_TOKEN`** in `~/.claude/settings.json` `env` block. If absent, ask
  the user to add it and restart Claude Code. No graceful-degradation path —
  this skill is API-only.
- Python 3.9+ on the runner. Script uses only the standard library.

## Workflow

### Step 1 — Resolve the coach

Run [`scripts/investigate.py`](scripts/investigate.py) with `--coach
"<id-or-name>"`. The script:

1. Fetches `/coaches?limit=500` (paginated) — the full active catalog.
2. If `coach` looks like a Firebase UID (28 alphanumeric chars matching
   `^[A-Za-z0-9]{28}$`), looks it up directly by `id`.
3. Otherwise treats it as a display name and searches `name`
   case-insensitively. If 0 matches: stop and ask the user to refine. If
   2+ matches: surface candidates and ask to pick.
4. Excludes podcast feeds (`types.podcast` truthy on listing or detail). If
   the target coach is a podcast feed, the skill warns and asks whether to
   proceed (podcast feeds aren't the intended audience for this report).

### Step 2 — Fetch coach detail and tracks

For the resolved coach:

1. Fetch `/coaches/<id>` for the `tracks: {trackId: createdAt}` map and the
   full record (followers, plays, listens, favorites, etc.).
2. If `include_tracks` is true (default), fan out `/contents/<trackId>` for
   every trackId in the map with 16-way parallel fetching. This populates
   per-track `trackType`, `duration`, `playedCount`, `listenedCount`,
   `favoritedCount` for trackType breakdowns and the top-tracks table.

### Step 3 — Compute the analysis

The script computes:

- **Cadence**: month-by-month upload counts over the last 24 months (totals),
  with two breakdowns for the last 12 months: per **locale** (the cap is
  10/month *per locale*, so each lane is evaluated independently and any
  `(month, locale)` cell at or above the cap is flagged) and per **trackType**
  (music vs spoken, content-strategy shifts). The per-locale view surfaces
  multi-locale coaches whose total looks high but each lane is fine, and
  catches single-locale coaches whose one lane is the actual violation.
- **Peer comparison**: ranks the coach within the peer follower band on
  total plays, plays/follower, listens/follower, favs/follower, completion
  rate (`listenedCount/playedCount`), and approved track count. Computes
  the peer median, p75, p90, and max for each metric.
- **Content composition**: tracks-by-trackType, plays/track by trackType,
  locale split. Identifies the "real value" slice vs catalog filler.
- **Engagement quality**:
  - Favs-per-follower vs peer median (the diagnostic for content-driven
    vs coach-driven engagement; >2× peer median is a strong content-driven
    signal — listeners save tracks but don't follow the person).
  - Listener-to-follower conversion (unique listeners / followers).
  - Completion rate vs peer band.
- **Catalog concentration**: % of plays from top 10/20/50% of tracks
  (Pareto check); count of zero-play and sub-100-play tracks.
- **Boost queue saturation**: count and % of catalog with `listenedCount <
  200`. **This is an upper-bound proxy** for tracks with `score_n < 200` —
  score_n counts unique-user encounters and is always ≥ listenedCount, so
  some tracks with `listenedCount < 200` may have already graduated via
  impressions. Note this in the report. For exact `score_n`-based queue
  size, link to the
  [Aura Recommendation score_n dashboard](https://datastudio.google.com/u/1/reporting/a522764f-b7e6-423d-bb92-47f905d66df5/page/VWw6B).

### Step 4 — Render the report

The script prints a structured Markdown report directly to stdout. Sections:

1. **Header** — coach name, ID, joined date, link to their profile
2. **Headline numbers** — followers, lifetime plays, lifetime listens,
   lifetime tracks, last-12-month uploads
3. **Upload cadence** — month-by-month totals for last 24 months; per-locale
   table for last 12 months with cap-violation flags; per-trackType table for
   last 12 months; per-locale 12-month average summary line
4. **Peer comparison** — table with SH numbers vs peer median/p75/p90 + rank
5. **Content composition** — trackType breakdown table, locale split
6. **Engagement quality** — the favs/follower line, listener-to-follower
   conversion, completion rate vs peers
7. **Catalog concentration** — Pareto stats
8. **Boost queue saturation** — % of catalog under 200 listens, age decay
9. **Top tracks** — top 10 tracks by lifetime plays (table)
10. **Recommendation** — single-line green/yellow/red verdict with the
    one-sentence rationale

### Step 5 — Hand back to caller

Print the report to chat. Do not write it to a file in the working tree (no
`outputs/` writes — output is chat-only). Do not auto-post to Slack or write
to Notion — the caller decides what to do.

## Output

A Markdown report printed to chat. The caller copies/forwards it as they see
fit. Approximate length: 80–150 lines depending on catalog size.

## Edge cases

- **Coach ID resolves to a podcast feed.** Stop and warn — this skill is
  designed for individual coaches, not external content partnerships. Offer
  to proceed anyway if the user confirms.
- **Coach not found.** Stop with a clear "no match" message; surface 3–5
  nearest name matches if the input looked like a name.
- **Coach has 0 tracks.** Report what we can (followers, joined date) and
  note that catalog analysis is N/A.
- **Coach has 2,000+ tracks.** Fetching every track detail takes longer
  (~60–90 seconds at 16-way parallelism). The script reports progress to
  stderr.
- **Aura API returns 5xx for a track detail.** Skip the track, log a warning
  to stderr, continue. Include a "fetched N of M tracks" note in the report
  if any failed.
- **Peer band has fewer than 5 coaches.** Widen the band (or fall back to
  the next-larger preset band) and note the widening in the report. Peer
  comparison is unreliable with <5 peers.
- **`coach` is ambiguous (multiple name matches).** Surface candidates with
  `name`, `id` prefix, `approvedTrackCount`, `followersCount`. Ask the user
  to pick.

## Security and privacy

- Coach `name`, `bio`, `paypalEmail`, etc. are in the API response. The
  report includes `name` (display name, fine to share within Aura) but
  **never** prints `paypalEmail`, `personalCoachingUrl`, or other contact
  details. If a caller wants those, they can read the coach record
  directly.
- The report contains no PII for end users; it summarises a coach's
  catalog and aggregate engagement.
- Tracks `userId` field (the uploading coach's id) is part of the report.

## Where it shows up

- Ad-hoc investigations triggered from chat
- Reference example: the Sonic Healer investigation that motivated this
  skill — see [Sonic Healer Investigation — Summary & Recommendations](https://www.notion.so/36675e23af208138a6a2d68a090f4dd0)
  for the full case study (May 2026)

## References

- [Tools/Aura API/Content](https://www.notion.so/36675e23af2081caa853e4bdbaebc40b) — the API endpoints this skill calls
- [Products/Aura/Coaches/Coach Upload Policy](https://www.notion.so/36675e23af20815a8d29e92f227d67f9) — the policy that frames the recommendation logic
- [Tools/Mixpanel](https://www.notion.so/34c75e23af20817baddaf32213eba8d5) → "Coach attribution" section — why this skill goes to the Aura API for upload data rather than Mixpanel
