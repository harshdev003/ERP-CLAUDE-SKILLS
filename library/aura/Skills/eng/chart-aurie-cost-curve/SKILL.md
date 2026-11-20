---
name: chart-aurie-cost-curve
description: >
  Aurie cohort cost as a single pooled decay CURVE per segment — average cost/user by weeks-since-join
  (week 0, week 1, week 2, …) as a line graph, pooling all cohorts into one line each. The
  column-average companion to eng:chart-aurie-cohort-cost's retention triangle. Same three segments:
  all users (since AI signup), current-premium (since subscription activation), free-never-subscribed.
  Trigger ONLY for the pooled LINE view (all cohorts collapsed into one line per segment): "pooled
  cost curve", "cost decay curve", "cost curve line graph", "average cost per week as a line", "plot
  cost by weeks since join", "one line per segment cost". This is distinct from
  eng:chart-aurie-cohort-cost — use THAT (not this) for the per-cohort-week retention triangle / "cost
  by join week" / "week 1 week 2 week 3 cost" / "cohort cost" heatmap; use eng:chart-aurie-cohort-payback
  for revenue/payback. Composes eng:backfill-aurie-user-ai-created; reads BigQuery aura-main.aurie_cost.
  Aggregate-only — never emits a UID.
metadata:
  version: "3.2.1"
knowledge:
  reads:
    - Guide/Index                  # baseline routing aid (universal)
    - Tools/BigQuery               # dataset/table conventions, GCP_KEY auth, bq.py
    - Compliance/Privacy           # reads PII-bearing tables — output must stay aggregate
---

# Chart Aurie Cost Curve

## Goal

Show how much a joined user costs over their first weeks with Aurie as **one pooled line per
segment** — average cost/user at week 0, 1, 2, … — the clean decay curve behind the per-cohort
triangle. It's the **column-average** of [`eng:chart-aurie-cohort-cost`](../chart-aurie-cohort-cost/SKILL.md):
same segments, same per-user weeks + completeness rules, but all join-week cohorts pooled into a
single curve so the shape is obvious at a glance.

- **Chart A — weeks since AI signup (`ai_created_at`), all users.**
- **Chart B — weeks since subscription activation (`ai_subscription_activated`), current-premium only.**
- **Chart C — weeks since AI signup, free users who never bought a subscription.**

**Pooled avg at week N** = `Σ(cohort-week-N cost) ÷ Σ(cohort size)` over the cohorts whose week N is
complete — a size-weighted average across cohorts, where an inactive user counts as \$0 (so it's
"cost per *joined* user at age N"). Use this when you want the trend line; use the triangle when you
want to compare cohorts against each other.

## Step 0 — Prerequisites

Reads BigQuery (`aura-main.aurie_cost` + the Mixpanel Nessie `ai_subscription_activated` event) via
the bundled `bq.py` — needs `GCP_KEY` and `google-cloud-bigquery`. No BigQuery MCP, so this is
API-first; a missing key/library exits with a clear message.

## Step 1 — Coverage guard

Charts A/C join `aura-main.aurie_cost.aurie_user_ai_created`. Before charting, make that dimension
complete by invoking [`eng:backfill-aurie-user-ai-created`](../backfill-aurie-user-ai-created/SKILL.md).
`curve.py` reports coverage (`missing`) and warns if incomplete.

## Step 2 — Aggregate the curves

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/chart-aurie-cost-curve/scripts/curve.py --max-week 8
```

Emits JSON:

```jsonc
{
  "cost_data_start": "2026-06-01", "cohort_start_week": "2026-06-01", "max_week": 8,
  "coverage": { "cost_users": 8309, "with_ai_created": 8145, "missing": 164 },
  "chart_a": { "anchor": "ai_created_at (weeks since AI signup)", "scope": "all AI users",
    "curve": [ { "week_n": 0, "avg_per_user": 1.94, "total": 5384.2, "users": 2775, "active_users": 1712, "n_cohorts": 5 }, … ] },
  "chart_b": { "anchor": "ai_subscription_activated …", "scope": "current-premium users only", "curve": [ … ] },
  "chart_c": { "anchor": "ai_created_at …", "scope": "free users, never bought a subscription", "curve": [ … ] }
}
```

Flags: `--max-week N` (highest `week_n`, default 8), `--start YYYY-MM-DD` (override the cohort start
week), `--premium-window N` (Chart B "current premium" recency, default 7).

## Step 3 — Render the curves

Render with the visualization tool (host Claude Code) as a **line chart**: x = `week_n` (Wk 0, Wk 1,
…), y = `avg_per_user` ($). Draw the three segments — one line each (or a small-multiples of three
panels, matching `eng:chart-aurie-cohort-cost`'s layout). Label each line/panel with its anchor +
scope from the JSON. Because Chart B (premium) runs several × higher than A/C, either use a shared
axis to show the gap or give each panel its own y-axis (state which). Add metric cards: week-0
avg per segment, the week-0 → week-1 drop, and the premium-vs-free week-0 ratio. If the widget tool
is unavailable, skip to Step 4.

## Step 4 — Print the curve table

Always print, per segment, a markdown table: `week_n` | `avg_per_user` | `users` (the pooled
denominator) | `n_cohorts`. Follow with a one-line read: week-0 cost per segment, how fast it decays,
and premium-vs-free.

## Notes & caveats

- **Pooled = size-weighted column average of the triangle.** A week-N point uses only cohorts whose
  week N is complete (same completeness rule as the triangle), so it never divides a partial week by
  the full cohort size. `users` is the pooled denominator (Σ cohort size of contributing cohorts).
- **Denominator = joined users, not active users.** Inactive users count as \$0, so this is
  cost-per-joined-user. `active_users` is also emitted if you want per-active cost.
- **Two anchors — never conflate.** Chart A/C count from `ai_created_at` (AI signup); Chart B from
  `ai_subscription_activated` (subscription start).
- **Current premium is a proxy** (most-recent cost row is `paid` and within `--premium-window` days),
  same as the triangle — avoids per-user API calls to keep the chart aggregate.
- **`ai_created_at` deduped on read**; the `Other` unattributed row is excluded.
- **Privacy.** Output is aggregate-only (per-week pooled sums/averages), never a UID.

- **Prod-only (PROD-3589 Part B).** Reads `aurie_per_user_daily_cost_prod` (env='prod') instead
  of the base table — dev/QA and not-yet-tagged users are excluded from these aggregates; see
  eng:backfill-aurie-user-env, which owns and refreshes the view. Still aggregate-only, never
  emits a UID.

## Origin

The line-graph companion to the cohort triangle — pool cohorts into one decay curve per segment
([PROD-3516](https://linear.app/aura-health/issue/PROD-3516)); part of the cost-understanding chart
set ([PROD-3498](https://linear.app/aura-health/issue/PROD-3498)).
