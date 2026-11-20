---
name: chart-aurie-cost-concentration
description: >
  Aurie cost concentration — Pareto / Lorenz curve of per-user cost over a window: what share of
  total cost comes from the top 1% / 5% / 10% of users, plus a Gini coefficient. Finds the
  expensive heavy-user tail to target for optimization or caps. Trigger on: "cost concentration",
  "pareto cost", "heavy user tail", "top 1% cost", "lorenz curve", "cost gini", "which users cost
  the most", "cost distribution", "whales cost". Reads BigQuery
  aura-main.aurie_cost.aurie_per_user_daily_cost. Aggregate percentiles only — never emits a UID.
  For a per-user drill-down / named heavy users use the *-cost-per-user skills; for daily totals
  use eng:chart-aurie-daily-cost.
metadata:
  version: "3.2.1"
knowledge:
  reads:
    - Guide/Index                  # baseline routing aid (universal)
    - Tools/BigQuery               # dataset/table conventions, GCP_KEY auth, bq.py
    - Compliance/Privacy           # reads a UID-bearing table — output must stay aggregate
---

# Chart Aurie Cost Concentration

## Goal

Show how concentrated Aurie cost is across users over a window — the **Pareto / Lorenz** view — so
the expensive heavy-user tail is obvious and quantified:

- **Top-N-share cards:** what % of total cost the top 1% / 5% / 10% / 25% / 50% of users drive.
- **Concentration curve:** cumulative cost share vs cumulative top-user share (100 points).
- **Gini coefficient:** a single 0–1 concentration number (0 = everyone equal, 1 = one user).

This is the distribution member of the cost-skill family. Siblings:
`eng:chart-aurie-daily-cost` (daily totals), `eng:chart-aurie-cohort-cost` (cost by join-week),
`eng:analyze-openai-cost-per-user` / `eng:analyze-elevenlabs-cost-per-user` (named per-user
drill-downs — use those when you need the actual heavy users, not just the shape).

## Step 0 — Prerequisites

Reads BigQuery (`aura-main.aurie_cost`) via the bundled `bq.py` — needs `GCP_KEY` and
`google-cloud-bigquery`. No BigQuery MCP, so this is API-first; a missing key/library exits with a
clear message.

## Step 1 — Aggregate the concentration

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/chart-aurie-cost-concentration/scripts/concentration.py --last 30
```

Emits JSON:

```jsonc
{
  "cost_data_start": "2026-06-01", "cost_data_end": "2026-07-12",
  "window": "last 30 days present",
  "summary": { "n_users": 7997, "total_cost": 9209.08, "mean_cost": 1.15, "median_cost": 0.04, "gini": 0.914 },
  "top_shares": [
    { "pct": 1, "users": 80, "cost": 4413.71, "share": 0.479 },
    { "pct": 5, "users": 400, "cost": 7418.74, "share": 0.806 }, …
  ],
  "lorenz": [ { "top_user_pct": 0.01, "cost_share": 0.4793, "pctile": 1 }, …, { "top_user_pct": 1.0, "cost_share": 1.0, "pctile": 100 } ]
}
```

Window flags: `--last N` (most recent N days present in the store, default 30 — matches
`eng:chart-aurie-daily-cost`), `--from` / `--to` (inclusive `YYYY-MM-DD`), `--all`.

Users are ranked by their **total cost over the window** (the `Other` unattributed row is excluded;
zero-cost users are excluded so shares are over users who actually cost something).

## Step 2 — Render the concentration chart

Render with the visualization tool (host Claude Code):

- **Pareto / Lorenz curve:** x = cumulative top-user percentile (`top_user_pct`, 0→100%), y =
  cumulative `cost_share` (0→100%). Draw the 45° equality line for reference — the further the curve
  bows above it, the more concentrated. Mark the top-1/5/10% points.
- **Metric cards:** top 1% / 5% / 10% share, Gini, median vs mean cost/user (the mean ≫ median gap
  is the skew in one glance).

A ranked bar of cost by percentile bucket is a fine alternate. If the widget tool is unavailable,
skip to Step 3.

## Step 3 — Print the concentration table

Always print: the top-N-share table (pct / users / cost / share) and the summary line (n_users,
total, mean, median, Gini). Follow with a one-line read: "the top X% of users drive Y% of cost;
Gini Z — target the tail for caps/optimization."

## Notes & caveats

- **Aggregate-only.** Per-user cost is summed and ranked **inside BigQuery**; the output is bucket
  sums, shares, and a Gini — never a UID. To identify the actual heavy users (with UIDs, for
  outreach or caps), use the per-user cost skills, which apply the UID-truncation privacy rule.
- **Gini is exact**, computed via the rank formula `2·Σ(i·cᵢ)/(n·Σc) − (n+1)/n` over all users in
  the window (not a bucket approximation).
- **NTILE buckets are ~equal user counts.** With `n` not divisible by 100, the remainder spreads
  across buckets; top-N-share cards read cumulative cost at the exact percentile boundary.
- **Window = present days.** `--last N` uses the last N days *present* in the store (not calendar
  days), matching the sibling charts so comparisons line up on gap days.
- **Concentration is inherent to a freemium base.** Most users are free and near-zero cost, so a
  high Gini / heavy top-1% is expected; the value is trending it and sizing the tail for caps.

- **Prod-only (PROD-3589 Part B).** Reads `aurie_per_user_daily_cost_prod` (env='prod') instead
  of the base table — dev/QA and not-yet-tagged users are excluded from these aggregates; see
  eng:backfill-aurie-user-env, which owns and refreshes the view. Still aggregate-only, never
  emits a UID.

## Origin

Idea #3 from the #ai cost thread (2026-07-13): find the expensive tail (top 1/5/10% of users) to
target for optimization or caps — [PROD-3506](https://linear.app/aura-health/issue/PROD-3506),
part of the cost-understanding chart set ([PROD-3498](https://linear.app/aura-health/issue/PROD-3498)).
