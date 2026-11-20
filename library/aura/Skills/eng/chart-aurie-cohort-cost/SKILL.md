---
name: chart-aurie-cohort-cost
description: >
  Cohort-based Aurie cost reporting — group users by the week they joined and chart
  their cost in week 0 / 1 / 2 / 3… (a cost "triangle"), so a surge in new subscribers
  doesn't distort the headline cost/margin. Two cohorts, each labelled by its anchor:
  Chart A = weeks since AI signup (aiCreatedAt), all users; Chart B = weeks since
  subscription activation, current-premium users only; Chart C = weeks since AI signup,
  free users who never bought a subscription. Trigger on: "cohort cost",
  "cohort based cost", "cost by join week", "week 1 week 2 week 3 cost", "cohort margin",
  "aurie cohort cost", "cost curve by cohort". Composes eng:backfill-aurie-user-ai-created
  as a coverage guard, then reads BigQuery aura-main.aurie_cost. Aggregate-only — never
  emits a UID. Not for daily workspace totals (use eng:chart-aurie-daily-cost) or
  per-user cost (use eng:report-user-costs).
metadata:
  version: "3.2.1"
knowledge:
  reads:
    - Guide/Index                  # baseline routing aid (universal)
    - Tools/BigQuery               # dataset/table conventions, GCP_KEY auth, bq.py
    - Compliance/Privacy           # reads PII-bearing tables — output must stay aggregate
---

# Chart Aurie Cohort Cost

## Goal

Show how much a user costs over their first weeks with Aurie, **grouped by the week they
joined**, as two cost "triangles" — the cohort view the team asked for so a spike in new
subscribers (who cost the most in week 0) doesn't distort headline cost/margin.

- **Chart A — weeks since AI signup (`ai_created_at`), all users.** The whole-funnel view.
- **Chart B — weeks since subscription activation (`ai_subscription_activated`),
  current-premium users only.** The margin-relevant view (paying users cost the most).
- **Chart C — weeks since AI signup (`ai_created_at`), free users who never bought a
  subscription.** The free-tier cost view — what we spend on users who never pay.
  "Never bought" = never in `ai_subscription_activated` (the authoritative activation
  event, same one Chart B uses) **and** never a paid/grace state in the cost table, so
  churned-paid and trial-then-paid users are excluded. Anchored on signup (these users
  have no activation).

Each cell is `week_n` (weeks since the anchor) and carries **`total`** (sum cost that week)
and **`avg_per_user`** (that total ÷ the cohort's size, so an inactive user counts as \$0 —
"what a joined user costs us in week N"). Every chart states its anchor explicitly; don't
read one as the other.

This is the **cohort** member of the cost-skill family. Siblings:
`eng:chart-aurie-daily-cost` (daily workspace totals, no cohorts),
`eng:store-aurie-user-daily-cost` (the ETL), `eng:backfill-aurie-user-ai-created`
(the `ai_created_at` dimension this composes).

## Step 0 — Prerequisites

Reads BigQuery (`aura-main.aurie_cost` + the Mixpanel Nessie
`ai_subscription_activated` event) via the bundled `bq.py` — needs `GCP_KEY` and
`google-cloud-bigquery`. No BigQuery MCP, so this is API-first; a missing key/library
exits with a clear message.

## Step 1 — Coverage guard (ensure the `ai_created_at` dimension is complete)

Chart A joins the cost table to `aura-main.aurie_cost.aurie_user_ai_created`. **Before
charting, make that dimension complete for the cost-table users** by invoking
[`eng:backfill-aurie-user-ai-created`](../backfill-aurie-user-ai-created/SKILL.md) (via
the Skill tool). It resolves any missing UIDs (Postgres `aiCreatedAt` → Mixpanel
first-AI-login) and appends them. `cohort.py` also reports its own coverage
(`missing` count) and warns if the dimension is incomplete — a non-zero `missing` means
Chart A undercounts those users, so run the backfill and re-run.

## Step 2 — Aggregate the cohorts

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/chart-aurie-cohort-cost/scripts/cohort.py --max-week 8
```

Emits JSON:

```jsonc
{
  "cost_data_start": "2026-06-01",
  "cohort_start_week": "2026-06-01",   // first full week of cost data (earlier cohorts are censored)
  "max_week": 8,
  "coverage": { "cost_users": 8309, "with_ai_created": 8145, "missing": 164 },
  "chart_a": { "anchor": "ai_created_at (weeks since AI signup)", "scope": "all AI users",
    "cohorts": [ { "cohort_week": "2026-06-01", "cohort_size": 656,
      "weeks": [ { "week_n": 0, "total": 1259.2, "avg_per_user": 1.92, "active_users": 410 }, … ] }, … ] },
  "chart_b": { "anchor": "ai_subscription_activated (weeks since subscription activation)",
    "scope": "current-premium users only", "cohorts": [ … ] },
  "chart_c": { "anchor": "ai_created_at (weeks since AI signup)",
    "scope": "free users, never bought a subscription", "cohorts": [ … ] }
}
```

Flags: `--max-week N` (highest `week_n`, default 8), `--start YYYY-MM-DD` (override the
cohort start week).

**Why the cohort start is clamped.** Per-user cost data begins `cost_data_start`, so a
cohort that joined earlier has censored early weeks (no cost before then). The script only
emits cohorts whose anchor week is on/after the first full cost week, so week-0 is always
fully observed. The triangle is naturally ragged: the newest cohort has only week-0, the
oldest has the most weeks.

## Step 3 — Render the two cohort triangles

Render with the visualization tool (host Claude Code) as a **cohort retention-style
triangle heatmap** — the format the team uses for cohort/retention views. For **each**
chart (A, B, C), draw a grid:

- **rows** = `cohort_week` (label each with its `cohort_size`, e.g. `06-01 · n=656`),
  oldest cohort on top;
- **columns** = `week_n` 0..max (`Wk 0`, `Wk 1`, …);
- **cells** = shaded on a single sequential blue ramp by `avg_per_user` — **darker =
  higher cost** — with the dollar value printed in the cell. Leave no-data cells blank so
  the triangle stays ragged (the newest cohort is a single cell).

Shade each chart on **its own** max (Chart B runs several × higher than Chart A), and use
`sqrt(value / max)` for the shade so the post-week-0 decay stays legible instead of
washing out. Title each panel with its anchor + scope from the JSON (`chart_a.anchor` /
`chart_b.anchor`) so the AI-signup vs subscription-activation distinction is unmistakable.
Add metric cards: latest cohort week-0 avg, the week-0 → week-1 drop, the premium-vs-all
week-0 ratio, and coverage. A cohort-curve line chart (x = `week_n`, y = `avg_per_user`,
one line per cohort) is a fine **alternate** view — the heatmap triangle is the default.
If the widget tool is unavailable, skip to Step 4.

## Step 4 — Print the triangle tables

Always print, per chart, a markdown table: rows = `cohort_week` (+ `cohort_size`),
columns = `week_n` 0..max, cells = `avg_per_user` (with `total` available in the JSON).
Follow with a one-line read: latest cohort week-0 avg, the week-0→week-1 decay, and how
Chart B (premium) compares to Chart A (all users).

## Notes & caveats

- **Aurie events are `ai_`-prefixed — never use the unprefixed ones.** Mixpanel project
  963821 is shared by Aura (meditation) + Aurie (AI); subscription events are prefixed by
  type (authoritative: PaymentService `src/config.js` → `subscriptionTypes.<type>.
  mixpanelEventPrefix` — Aurie = `AI` → `ai_subscription_*`; Aura Premium = `` → unprefixed
  `subscription_*`, ~300× larger). This skill uses `ai_subscription_activated`; using the
  unprefixed `subscription_activated` would pull in Aura meditation and corrupt every
  number. See AI Knowledge → Tools/Mixpanel → Subscription Events.
- **Two anchors — never conflate them.** Chart A counts from `ai_created_at` (AI signup);
  Chart B from `ai_subscription_activated` (subscription start). A user appears in A from
  signup and in B (if currently premium) from activation — different week-0s.
- **`avg_per_user` denominator = cohort size**, not active users — so it's the average
  across *all* joined users (inactive weeks count as \$0), the right number for
  cost-per-joined-user. `active_users` is also in the JSON if you want per-active cost.
- **Current premium is a proxy** read from the cost table: the user's most-recent cost row
  is `paid` **and** within `--premium-window` days (default 7) of the latest data. The
  store only writes a row on days with spend, so requiring recency drops churned users
  whose last `paid` row is stale (otherwise they'd inflate the cohort and dilute the
  average). Fully authoritative currentness is the Aura API `premiumAI` flag — avoided
  here to keep the chart aggregate (no per-user calls).
- **Weeks are per-user + completed only.** `week_n` counts from each user's own join, so
  week 0 is always a full first 7 days (not a partial calendar week). A cell is emitted
  only once that week is complete for **every** user in the cohort (the latest joiner's
  7-day span is within the data) — the still-open trailing week is dropped, so a partial
  week never divides by the full cohort size and understates the average.
- **Censoring.** Cohorts before the cost-data start are excluded; the newest cohorts have
  only the first completed week or two. Compare like-aged weeks across cohorts, not raw
  row counts.
- **`ai_created_at` dimension** is deduped on read (`ROW_NUMBER … ORDER BY fetched_at
  DESC`), so a rare append-only duplicate can't double-count a user.
- **Privacy.** Output is aggregate-only (cohort × week sums/averages), never a UID, even
  though it reads the PII-bearing per-user cost + dimension tables.

- **Prod-only (PROD-3589 Part B).** Reads `aurie_per_user_daily_cost_prod` (env='prod') instead
  of the base table — dev/QA and not-yet-tagged users are excluded from these aggregates; see
  eng:backfill-aurie-user-env, which owns and refreshes the view. Still aggregate-only, never
  emits a UID.

## Origin

The cohort-based cost/margin view requested in #ai (2026-07-13) — new subscribers cost
most in week 1, so cohort reporting keeps a new-subscriber surge from distorting the
headline. [PROD-3498](https://linear.app/aura-health/issue/PROD-3498).
