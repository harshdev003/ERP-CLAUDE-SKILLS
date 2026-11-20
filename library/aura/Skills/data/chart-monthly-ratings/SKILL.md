---
name: chart-monthly-ratings
description: >
  Charts one calendar month of Aurie conversation ratings from Slack #ai-data-intelligence
  (C07MB0TMF5J) — the k8bot `*[TYPE] RATED*` messages — broken down by rating type (chat /
  journal / profile / conversation / quick-processing / coaching) and polarity (positive /
  neutral / negative). Produces two views: absolute counts and normalized (100% share).
  Renders an interactive chart inline and generates matplotlib PNGs for Slack. Defaults to the
  last complete calendar month; accepts an explicit YYYY-MM. Optionally posts the charts + a
  summary back to the ratings channel (opt-in, draft-and-confirm). Trigger phrases: "monthly
  rating chart", "chart this month's ratings", "rating breakdown for May", "ratings by type
  for {month}", "monthly Aurie ratings snapshot", "chart ratings absolute and normalized".
  Sibling to `data:investigate-user-rating` (which drills into individual negatives); this
  skill is the aggregate monthly snapshot.

metadata:
  version: "3.3.1"
knowledge:
  reads:
    - Guide/Index   # baseline routing aid (universal)
    - Team/Internal Accounts  # employee allowlist passed to chart_ratings.py --internal-accounts-file (the only test-drop signal here)
    - Tools/Slack   # ratings channel ID + Slack post format / footer rules
---

# chart-monthly-ratings

The aggregate monthly counterpart to `data:investigate-user-rating`. Where that
skill drills into individual negative/neutral ratings one at a time, this one
gives the month's bird's-eye view: how many ratings of each **type** (chat,
journal, profile, conversation, quick-processing, coaching) came in, and how they split across
**polarity** (positive / neutral / negative) — both as absolute counts and
normalized to 100% share so the small negative/neutral slices are readable next
to the dominant positives.

It is deliberately small: one deterministic fetch+aggregate script, an inline
chart for the session, and an opt-in Slack post. It does **not** analyse any
single conversation — for that, hand a specific rating permalink to
`data:investigate-user-rating` or `data:profile-user`.

## When to use

- "chart this month's ratings" / "monthly rating chart"
- "ratings by type for May" / "rating breakdown for <month>"
- "give me the monthly Aurie ratings snapshot"
- "chart ratings absolute and normalized"
- a recurring monthly cadence (e.g. start-of-month review of the prior month)

**Do NOT use for:**

- Drilling into a single negative/neutral rating → `data:investigate-user-rating`
  (sweep) or `data:profile-user` (one permalink).
- Daily trend lines or arbitrary windows → that's a one-off; this skill is
  month-granular by design.
- Per-prompter-version regression analysis → out of scope (see "Out of scope").

## Inputs

- **`month`** (optional, `YYYY-MM`) — the calendar month to chart. Default: the
  **last complete** calendar month (running in June charts May).
- **`channel_id`** (optional) — read from `Tools/Slack` in AI Knowledge
  (Step 0 pre-fetches it). Canonical value `C07MB0TMF5J` (#ai-data-intelligence);
  override only for testing.

## Prerequisites

- **Slack is MCP-first** (best-practices principle 11): prefer the Slack MCP
  connector when one is connected. The rating sweep (`chart_ratings.py`,
  `conversations.history`) and the opt-in chart upload (`post_charts.py`,
  external file-upload flow) run the bundled scripts directly — API-first by
  necessity (windowed history pagination + multi-file upload). `SLACK_BOT_TOKEN`
  is the fallback token for that script path.
- **`SLACK_BOT_TOKEN`** — required for the fetch and any post. The bot must be a
  member of the channel and (for posting) hold `files:write` + `chat:write`.
- **`matplotlib`** — required only for PNG rendering (`--png`); imported lazily,
  so the JSON-only path runs without it. If absent, fall back to the inline
  widget for the in-session view and tell the user PNGs (and therefore the Slack
  post) need `pip install matplotlib`.

**TLS gotcha.** Direct `urllib` HTTPS calls fail in some sandboxes with
`SSL: CERTIFICATE_VERIFY_FAILED`. `chart_ratings.py` uses `requests` (bundles
certifi) directly; `post_charts.py` posts through the shared `slack_post` helper
(prefers `requests`, falls back to `curl` with a status check; never `urllib`).
Don't hand-roll `urllib` calls.

**Cowork caveat.** `mcp__workspace__bash` strips host env (PROD-1305), so
`SLACK_BOT_TOKEN` won't reach a Cowork sandbox. Run from the host harness, or use
the per-session paste-export workaround in [`/CLAUDE.md`](../../../../CLAUDE.md) →
"Known Cowork constraints".

## Workflow

### Step 0 — Resolve inputs

Read `Tools/Slack` from AI Knowledge (declared in `knowledge.reads`) for the
canonical channel ID (`C07MB0TMF5J`) and the Slack post-format / footer rules
used in Step 4. Resolve `month` (default last complete month) and `channel_id`.

### Step 1 — Fetch + aggregate (deterministic)

Run the script. For an in-session chart you want the PNGs too (Step 3 can use
either the inline widget or the PNGs), so pass `--png`:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/chart-monthly-ratings/scripts/chart_ratings.py \
  --month <YYYY-MM> --png /tmp/rating_charts \
  --internal-accounts-file ${AI_KNOWLEDGE_CACHE_DIR:-~/.cache/ai-knowledge}/team__internal-accounts.md
```

`--internal-accounts-file` is the `Team/Internal Accounts` page as `retrieve-knowledge`
caches it (a `knowledge.reads` path); the script reads every Firebase UID and `google:` id
off it — never hand-build `--internal-ids` ([ENG-1436](https://linear.app/aura-health/issue/ENG-1436)).
That allowlist is the only thing dropped as test, and omitting it drops nothing.

Omit `--month` for the last complete month. The script fetches the month's channel
history, parses all six RATED shapes, drops allowlisted internal accounts, aggregates
real entries into a per-type × polarity matrix, and emits JSON:

- `counts` — `parsed` / `real` / `test_dropped`
- `test_reasons` — breakdown of the drops (`allowlist:<uid-prefix>`)
- `high_volume_uids` — UIDs with ≥40 ratings in the month, **reported not dropped**
- `overall` — `positive` / `neutral` / `negative` / `unknown` / `total` + `pct`.
  `total` is the **charted** total (the three known polarities); `unknown` counts
  real entries whose emoji didn't parse. They reconcile as
  `counts.real == overall.total + overall.unknown`.
- `by_type` — one row per shape with `positive` / `neutral` / `negative` / `total`,
  sorted by total desc
- `profile_by_renderer` — profile ratings only, one row per rated renderer
  (`core-quote-renderer`, …) with the same polarity columns, sorted by total desc.
  A profile rating whose footer carries no `renderer:` rated the whole profile and
  is bucketed under `whole-profile`. Empty list when the month has no profile
  ratings.
- `png_paths` — the two rendered charts (when `--png` was passed)

### Step 2 — Show the numbers (plan-before-side-effects)

Before rendering or posting, show the user the headline:

- Month + total ratings. Use `overall.total` (the charted total) as the headline
  number so it matches the bars and the percentages. When `overall.unknown > 0`,
  call it out separately ("+N unrecognised-polarity") rather than folding it into
  the headline — that keeps the headline equal to what's charted.
- How many entries were dropped as internal (`counts.test_dropped` + `test_reasons`),
  **stated even at zero**. A silent drop is indistinguishable from "nothing happened".
- `high_volume_uids` when non-empty, as a note — the month's most prolific raters, kept
  in the chart. Flag one only if it looks like an actual QA loop.
- That an internal account **not** on the allowlist is counted as a user here (see
  the internal-coverage caveat), so the split is "all raters minus known employees".
- Polarity split **per scale group, not one blended figure** — the three-option surfaces
  (conversation · quick-processing · profile) and the two-option ones (chat · journal) are
  rated on different widgets, so a single pooled percentage measures which surfaces can
  express a neutral rather than how the month went. See the per-surface caveat below.
- The per-type table — the honest view, and the one to lead with.

This is the moment to slow down — the user can switch months or stop before any
chart or Slack post.

### Step 3 — Render the chart inline

Render an inline chart for the session using the `mcp__visualize__show_widget`
tool — two horizontal stacked bars per type (absolute + normalized), polarity
colours green `#639922` / amber `#EF9F27` / red `#E24B4A`, with metric cards for
the overall totals. (The matplotlib PNGs from Step 1 carry the same data for
Slack; the widget is the richer in-session view.) Build the widget data directly
from the script's `by_type` + `overall` JSON — do not recompute.

### Step 4 — (Opt-in) Offer to post to Slack

Ask whether to post the charts to the ratings channel. **Never auto-post** —
sending on the user's behalf needs explicit per-turn approval (draft-and-confirm).

On approval:

1. **Draft the summary comment** and show it in chat first. Shape:

   ```
   *Aurie ratings — <Month YYYY>*
   <total> ratings
   3-option (conversation · qp · profile): <pos>% pos · <neu>% neu · <neg>% neg
   2-option (chat · journal, 👍/👎): <pos>% pos · <neg>% neg

   • chat: <p>/<n>/<g> (pos/neu/neg)
   • conversation: …
   • journal: …
   • profile: …
   • quick-processing: …
   ```

   One line per shape present in `by_type` — omit shapes with no ratings that
   month rather than printing zeros. When `profile_by_renderer` has more than one
   row, add a short per-renderer line under the profile line; a single
   `whole-profile` row adds nothing over the profile line itself.

   `post_charts.py` appends the versioned `Tools/Slack` footer automatically — it
   passes `footer_skill=data:chart-monthly-ratings` to `upload_files`, which stamps
   `_Generated by \`data:chart-monthly-ratings:<version>\` · <YYYY-MM-DD>_` from
   `metadata.version`. Don't put a footer in the `--comment` text. No PII in the
   summary (this skill aggregates counts only — no UIDs/emails to leak, but keep it
   that way).

2. **On explicit approval**, post the two PNGs + the comment via the helper:

   ```bash
   python3 ${CLAUDE_PLUGIN_ROOT}/skills/chart-monthly-ratings/scripts/post_charts.py \
     --channel <channel_id> \
     --comment "<approved summary text>" \
     --png /tmp/rating_charts/ratings_<YYYY-MM>_absolute.png \
     --png /tmp/rating_charts/ratings_<YYYY-MM>_normalized.png
   ```

   The helper uses Slack's modern external-upload flow and prints the first
   file's permalink. Confirm that permalink back to the user.

If the user declines, stop — the inline chart already answered the ask.

## Output format

- **Inline:** the headline numbers (Step 2) as text + the interactive chart
  (Step 3).
- **Slack (opt-in):** two PNG charts (absolute + normalized) with the summary
  comment + attribution footer.

## Edge cases

- **Zero ratings in the month.** Report it and stop — nothing to chart (e.g. a
  month before the channel existed, or before the k8bot rating rollout).
- **`chat` and `journal` have no neutral bucket — ever.** Their widget exposes only
  👍/👎, so a zero-neutral month on those two shapes is the rating UI, not the data.
  Their positive share is mechanically higher than a three-option shape's (a user who
  would have picked OKAY had to pick 👍), so never pool the two groups into one
  percentage and never read "chat beat conversation" off the blended figure. Canonical
  option-set table, per-surface distribution, and why the constraint is client-side:
  the [`Signals/Conversation Patterns` schema](../../../shared/skills/record-knowledge/references/schemas/conversation-patterns.md)
  → "`Rating` is not comparable across `Session Type`" ([ENG-1118](https://linear.app/aura-health/issue/ENG-1118)).
- **`journal` with zero negatives is not a positive-only surface.** The 👎 exists and
  gets used — roughly 0.6% of journal ratings over a 60-day window, so a single month
  can easily contain none. Report it as a small-sample observation, not as "journal
  can't capture a negative".
- **A neutral appearing on `chat` or `journal` means a client sent `0`.** The service
  accepts `-1`/`0`/`1` on every endpoint, so the parse is right and the row is real —
  it just can't have come from the shipped widget. Every such entry observed so far was
  a test account calling the endpoint directly, and the allowlist plus the API join
  drop those; one from a genuine user is worth chasing rather than charting silently.
- **Profile ratings are sparse and rate a different object.** A `*PROFILE RATED*`
  entry rates the user's generated profile (compiled insight + card renderers), not
  a conversation — so its polarity split isn't comparable to the conversational
  shapes, and low volume makes a single thumbs-down swing the share hard. Report
  the count alongside the share when the profile row is small. Profile ratings do
  carry all three polarities (the rating is `1` / `0` / `-1`), so a neutral profile
  bucket is real, not a parse error.
- **Every profile rating lands in `whole-profile`.** Means no rating that month
  named a single card. Expected while most ratings come from the profile-level
  control; not a parser failure.
- **Partial month requested via explicit YYYY-MM = current month.** Allowed, but
  flag in Step 2 that the month is incomplete so the totals aren't compared
  one-to-one against full months.
- **Rating volume is not a test signal — never filter on it.** Volume tracks
  engagement, so a per-UID rate threshold silences the most engaged paying accounts, and
  over a *calendar month* the effect compounds: a user rating twice a week clears any
  small threshold by mid-month. Prolific UIDs are annotated `high_volume` (≥40/month)
  and reported under `high_volume_uids`, never dropped. Rationale and the measured
  distortion: [ENG-1050](https://linear.app/aura-health/issue/ENG-1050) /
  [ENG-1048](https://linear.app/aura-health/issue/ENG-1048).
- **Internal-account coverage is allowlist-only here.** The allowlist is the only
  test criterion. Unlike the `fetch_ratings.py` rating sweeps, this script carries no
  Aura-API internal ladder (`role` / `isAIReviewer` / `@aurahealth.io` / `test` /
  creator sub) — that join runs per distinct UID and a month has hundreds, a
  multi-minute walk for a chart that needs only aggregate counts. Consequence worth
  stating in the report: an internal account the allowlist doesn't name is counted as a
  user in these percentages.
- **Unrecognised polarity.** A RATED message whose emoji doesn't parse to
  positive/neutral/negative is still a real entry (counted in `counts.real`) but
  has no charted bucket — it lands in `overall.unknown`, not `overall.total`. The
  two reconcile as `counts.real == overall.total + overall.unknown`. Normally
  `unknown` is 0; if it's non-zero, surface it in Step 2 rather than letting the
  headline (`overall.total`) silently differ from `counts.real`.
- **`matplotlib` missing.** Render the inline widget only; tell the user PNGs and
  the Slack post need `pip install matplotlib`.
- **Bot not in channel / missing `files:write`.** `not_in_channel` /
  `missing_scope` from Slack → surface it: invite `aura-ai` to
  #ai-data-intelligence and confirm the app has `files:write` + `chat:write`.

## Out of scope (v1)

- **Auto-posting / scheduled runs.** No cron, no auto-post. Posting always
  requires per-turn approval. (A monthly schedule could wrap this skill later via
  `anthropic-skills:schedule`, but that's a separate opt-in.)
- **Per-prompter-version breakdown.** This skill charts type × polarity only.
  Prompter-version regression is a different cut — keep it out so the chart stays
  legible.
- **Daily / arbitrary windows.** Month granularity only. One-off daily trends are
  a manual ask, not this skill.
- **Drilling into individual ratings.** That's `data:investigate-user-rating` /
  `data:profile-user`.

## Key references

- **Sibling (per-rating drill-in):** [`data:investigate-user-rating`](../investigate-user-rating/SKILL.md)
- **Channel:** #ai-data-intelligence = `C07MB0TMF5J` (canonical in AI Knowledge → `Tools/Slack`).
- **Slack post format / footer:** `Tools/Slack` → Post format.
- **Linear:** [PROD-2631](https://linear.app/aura-health/issue/PROD-2631) · parent [PROD-2273](https://linear.app/aura-health/issue/PROD-2273) (AI Native Cycle 30).
