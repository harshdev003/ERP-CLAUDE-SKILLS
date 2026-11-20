---
name: chart-aurie-cohort-payback
description: >
  Cohort payback / margin for Aurie — cumulative per-user COST vs cumulative per-user
  RECOGNIZED (amortized) subscription REVENUE, grouped by the week each user first paid, so
  you can read the payback week (when cumulative revenue overtakes cost) and the contribution
  margin per cohort. The "are new subscribers profitable?" answer behind the #ai margin thread.
  Trigger on: "cohort payback", "payback period", "cohort margin", "cost vs revenue per cohort",
  "contribution margin", "gross margin by cohort", "are new subscribers profitable", "paid vs
  cost per user", "cumulative cost vs revenue". Reads BigQuery aura-main.aurie_cost
  (per-user daily cost + the aurie_user_subscription revenue dimension); composes
  eng:build-aurie-user-subscription. Aggregate-only — never emits a UID. For per-week cost
  triangles without revenue use eng:chart-aurie-cohort-cost; for the daily dashboard use
  eng:chart-aurie-daily-cost.
metadata:
  version: "3.2.1"
knowledge:
  reads:
    - Guide/Index                        # baseline routing aid (universal)
    - Tools/BigQuery                     # dataset/table conventions, GCP_KEY auth, bq.py
    - Tools/Mixpanel/Subscription Events # revenue = amortized paid/renewed periods; lifecycle
    - Compliance/Privacy                 # reads PII-bearing tables — output must stay aggregate
---

# Chart Aurie Cohort Payback

## Goal

Answer **"are new subscribers profitable, and how fast?"** For each cohort — users grouped by the
week they **first paid** — track **cumulative per-user cost** against **cumulative per-user
recognized revenue** over weeks-since-first-purchase, and surface:

- **Payback week** — the first `week_n` where cumulative revenue ≥ cumulative cost.
- **Contribution margin** — `(cum_revenue − cum_cost) / cum_revenue` at the latest observed week.

This is the margin/payback member of the cost-skill family. Siblings:
`eng:chart-aurie-cohort-cost` (cost triangles, no revenue),
`eng:chart-aurie-daily-cost` (daily dashboard),
`eng:build-aurie-user-subscription` (the revenue dimension this reads).

## Revenue basis — amortized (recognized)

Each subscription period's revenue is **spread evenly across its billing period** (from
`aurie_user_subscription`), not booked in full on purchase day — so an annual sub recognizes ~1/365
per day, not a year of revenue up front. The recognition length is the **nominal** period from the
`period` field (`year` → 365 days, `month` → 30), which is authoritative: ~33 source rows have a
corrupt `expiration_at` only minutes after purchase (an immediate expire/refund event) that would
otherwise dump a full period of revenue onto day 0 — the nominal length spreads them correctly.

**Cost** is the per-user daily cost from `aurie_per_user_daily_cost`, cumulated from each user's
first-purchase anchor — **including any post-churn free-usage cost** (honest cohort economics: a
churned user who keeps using Aurie still costs us with no new revenue).

## Step 0 — Prerequisites

Reads BigQuery (`aura-main.aurie_cost`) via the bundled `bq.py` — needs `GCP_KEY` and
`google-cloud-bigquery`. No BigQuery MCP, so this is API-first; a missing key/library exits with a
clear message.

## Step 1 — Ensure the revenue dimension is current

This skill joins `aura-main.aurie_cost.aurie_user_subscription`. **Before charting, (re)build that
dimension** by invoking [`eng:build-aurie-user-subscription`](../build-aurie-user-subscription/SKILL.md)
(via the Skill tool) so revenue reflects the latest paid/renewed events. `payback.py` exits early
with a clear message if the table is missing.

## Step 2 — Aggregate the cohorts

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/chart-aurie-cohort-payback/scripts/payback.py --max-week 8
```

Emits JSON:

```jsonc
{
  "cost_data_start": "2026-06-01",
  "cohort_start_week": "2026-06-01",   // earlier cohorts are censored (no cost before the store began)
  "max_week": 8,
  "anchor": "first subscription purchase (weeks since first paid)",
  "revenue_basis": "amortized (recognized): period revenue spread evenly across [purchase_at, expiration_at]",
  "cohorts": [
    { "cohort_week": "2026-06-01", "cohort_size": 80, "payback_week": 2, "margin_latest": 0.3042,
      "weeks": [
        { "week_n": 0, "week_cost": 675.8, "week_rev": 355.2,
          "cum_cost_per_user": 8.45, "cum_rev_per_user": 4.44, "active_users": 74 }, … ] }, …
  ]
}
```

Flags: `--max-week N` (highest `week_n`, default 8), `--start YYYY-MM-DD` (override the cohort start
week).

## Step 3 — Render the payback chart

Render with the visualization tool (host Claude Code). For each cohort (or a small-multiples grid):

- **x** = `week_n`; two cumulative-per-user lines — **cost** (`cum_cost_per_user`) and **revenue**
  (`cum_rev_per_user`). Shade the gap: red where cost > revenue (pre-payback), green after the
  crossover. Mark the **payback week** with a vertical rule.
- **Metric cards:** per cohort — payback week (or "not yet, week N observed"), latest contribution
  margin, latest cum cost/user and cum revenue/user. Plus a portfolio line: blended margin across
  cohorts.

A single overlay of all cohorts' cumulative cost vs revenue is a fine summary view; the
small-multiples (one panel per cohort) reads the payback crossover most clearly. If the widget tool
is unavailable, skip to Step 4.

## Step 4 — Print the payback table

Always print a markdown table: one row per cohort with `cohort_size`, `payback_week`,
`margin_latest`, and latest `cum_cost_per_user` / `cum_rev_per_user`. Follow with a one-line read:
which cohorts have paid back, the typical payback week, and the blended contribution margin — with
the censoring caveat for the youngest cohorts.

## Notes & caveats

- **Censoring dominates young cohorts.** Cost data starts `cost_data_start`, so only cohorts whose
  first-purchase week is on/after the first full cost week are included, and the newest cohorts have
  only week 0–1 observed — they'll read as "not paid back yet" (all cost, little recognized revenue)
  purely because few weeks are visible. Compare like-aged weeks, not raw `margin_latest`, across
  cohorts of different ages.
- **Recognized ≠ cash.** This is amortized (recognized) revenue — the chosen basis. Cash collected
  up front (an annual sub's full charge on day 0) pays back immediately in cash terms; the recognized
  view answers "is the ongoing relationship margin-positive," which is the right lens for cost/margin.
- **Revenue is gross of refunds.** The `revenue` field is per-transaction and not verified net of
  refunds; the ~33 short-`expiration_at` rows (≈3.9% of revenue) may include refunds. Amortizing by
  nominal period limits their day-0 distortion, but treat cohort margins as directional, not booked.
- **Weeks are per-user + completed only** (same rule as `eng:chart-aurie-cohort-cost`): `week_n`
  counts from each user's own first purchase, and a week is emitted only once complete for every user
  in the cohort, so a still-open trailing week can't understate a fixed-denominator cumulative.
- **Anchor = first purchase**, not AI signup — so a user enters their payback cohort when they start
  paying. (`eng:chart-aurie-cohort-cost` Chart A anchors on AI signup; don't conflate them.)
- **Privacy.** Output is aggregate-only (cohort × week sums + cumulative per-user), never a UID.

- **Prod-only (PROD-3589 Part B).** Reads `aurie_per_user_daily_cost_prod` (env='prod') instead
  of the base table — dev/QA and not-yet-tagged users are excluded from these aggregates; see
  eng:backfill-aurie-user-env, which owns and refreshes the view. Still aggregate-only, never
  emits a UID.

## Origin

Idea #1 from the #ai cost thread (2026-07-13): the gross-margin / payback-period-per-cohort answer
behind the margin question — [PROD-3504](https://linear.app/aura-health/issue/PROD-3504), builds on
[PROD-3498](https://linear.app/aura-health/issue/PROD-3498) and
[PROD-3510](https://linear.app/aura-health/issue/PROD-3510) (the revenue dimension).
