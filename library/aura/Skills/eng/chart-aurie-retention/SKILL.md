---
name: chart-aurie-retention
description: >
  Aurie cohort activity RETENTION as a single pooled curve per segment — the share of a cohort still
  active by weeks-since-join (week 0 = 100%, then decays), pooling all cohorts into one line each.
  The retention companion to eng:chart-aurie-cost-curve, on the same weeks-since-join grid. Same three
  segments: all users (since AI signup), premium (since subscription activation), free-never-subscribed.
  It's the missing second factor for a lifetime-cost projection — lifetime cost ≈ Σ cost_curve(N) ×
  retention(N). Trigger for: "retention curve", "cohort retention", "how long do users stay active",
  "activity retention by week", "survival curve", "week 1 / week 2 retention", "do premium users
  retain". Distinct from eng:chart-aurie-cost-curve (cost per week, not retention) and
  eng:chart-aurie-cohort-cost (per-cohort cost heatmap). Composes eng:backfill-aurie-user-ai-created;
  reads BigQuery aura-main.aurie_cost. Aggregate-only — never emits a UID.
metadata:
  version: "3.2.1"
knowledge:
  reads:
    - Guide/Index                  # baseline routing aid (universal)
    - Tools/BigQuery               # dataset/table conventions, GCP_KEY auth, bq.py
    - Compliance/Privacy           # reads PII-bearing tables — output must stay aggregate
---

# Chart Aurie Retention

## Goal

Show how many of a cohort are **still active** over their first weeks with Aurie, as **one pooled
line per segment** — retention at week 0, 1, 2, … — the survival curve that pairs with the cost
decay curve. Same segments and same weeks-since-join grid as
[`eng:chart-aurie-cost-curve`](../chart-aurie-cost-curve/SKILL.md), so the two multiply cleanly into a
lifetime view: **lifetime cost ≈ Σ_N cost_curve(N) × retention(N)**.

- **Chart A — weeks since AI signup (`ai_created_at`), all users.**
- **Chart B — weeks since subscription activation (`ai_subscription_activated`), users who activated premium.**
- **Chart C — weeks since AI signup, free users who never bought a subscription.**

**Definition (classic cohort retention, starts at 100% and decays):** the cohort is the members
active in their **week 0** (first 7 days since the anchor); `retention(N)` = `Σ(members active in
week N) ÷ Σ(cohort size)`, size-weighted over the cohorts whose week N is complete. "Active" = the
`active` flag on the per-user daily cost store (a mobile-app DAU signal). Chart B is **not**
survivorship-filtered to current-premium — that would make retention circular; it counts everyone who
activated, which is exactly the churn signal.

## Step 0 — Prerequisites

Reads BigQuery (`aura-main.aurie_cost` + the Mixpanel Nessie `ai_subscription_activated` event) via
the bundled `bq.py` — needs `GCP_KEY` and `google-cloud-bigquery`. No BigQuery MCP, so this is
API-first; a missing key/library exits with a clear message.

## Step 1 — Coverage guard

Charts A/C join `aura-main.aurie_cost.aurie_user_ai_created`. Before charting, make that dimension
complete by invoking [`eng:backfill-aurie-user-ai-created`](../backfill-aurie-user-ai-created/SKILL.md).
`retention.py` reports coverage (`missing`) and warns if incomplete.

## Step 2 — Aggregate the curves

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/chart-aurie-retention/scripts/retention.py --max-week 8
```

Emits JSON:

```jsonc
{
  "cost_data_start": "2026-06-01", "cohort_start_week": "2026-06-01", "max_week": 8,
  "metric": "share of the week-0-active cohort still active in week N (active flag)",
  "coverage": { "cost_users": 8309, "with_ai_created": 8145, "missing": 164 },
  "chart_a": { "anchor": "ai_created_at (weeks since AI signup)", "scope": "all AI users",
    "curve": [ { "week_n": 0, "retention": 1.0, "active_users": 2075, "cohort_size": 2075, "n_cohorts": 5 }, … ],
    "triangle": [ { "cohort_week": "2026-06-01", "cohort_size": 84, "weeks": [ { "week_n": 0, "retention": 1.0, "active_users": 84 }, … ] }, … ] },
  "chart_b": { "anchor": "ai_subscription_activated …", "scope": "users who activated premium", "curve": [ … ] },
  "chart_c": { "anchor": "ai_created_at …", "scope": "free users, never bought a subscription", "curve": [ … ] }
}
```

Flags: `--max-week N` (highest `week_n`, default 8), `--start YYYY-MM-DD` (override the cohort start
week), `--last-weeks N` (pool only the most recent N complete cohort-weeks).

## Step 3 — Render the curve + heatmap

**Curve (pooled line).** Render `curve` with the visualization tool (host Claude Code) as a **line
chart**: x = `week_n` (Wk 0, Wk 1, …), y = `retention` (%, 0–100). Draw the three segments — one line
each (or small-multiples of three panels, matching the cost-curve layout). A shared 0–100% axis works
for all three since retention is already normalised. Add metric cards: week-1 retention per segment
and the premium-vs-free week-1 ratio.

**Heatmap (per-cohort triangle).** Render `triangle` — the retention analog of
[`eng:chart-aurie-cohort-cost`](../chart-aurie-cohort-cost/SKILL.md): one row per join-cohort, columns
= `week_n`, each cell = that cohort's own `retention` (every row starts at 100% in week 0 and decays
across; the triangle shortens for younger cohorts). `eng:build-aurie-cost-report` renders both the
pooled curve and this heatmap. If the widget tool is unavailable, skip to Step 4.

## Step 4 — Print the curve table

Always print, per segment, a markdown table: `week_n` | `retention` (%) | `cohort_size` (the pooled
week-0 denominator) | `n_cohorts`. Follow with a one-line read: week-1 retention per segment and
premium-vs-free.

## Notes & caveats

- **Denominator = week-0-active cohort.** Week 0 is 100% by construction; a later week uses only
  cohorts whose week N is complete (same completeness rule as the cost curve, keyed to the cohort's
  latest joiner), so a still-open trailing week is never divided into a full cohort.
- **"Active" = the cost-store `active` flag** (a mobile-app DAU, backfilled from Mixpanel). It only
  marks activity on a day we booked a cost row, so a user active but costing ~\$0 that day is a rare
  miss. These are strict, cost-anchored retention numbers — the relative shape (premium vs free) is
  the signal; absolute levels run below a raw "opened the app" retention.
- **Two anchors — never conflate.** Chart A/C count from `ai_created_at` (AI signup); Chart B from
  `ai_subscription_activated` (subscription start).
- **Chart B is not survivorship-filtered.** It counts everyone who activated premium, so the decline
  is real churn — unlike the cost curve's Chart B, which conditions on current-premium.
- **Pairs with the cost curve for lifetime cost.** Multiply `retention(N)` by
  `eng:chart-aurie-cost-curve`'s `avg_per_user(N)` per week and sum for an activity-weighted lifetime
  cost. Align the segment + horizon before multiplying (premium cost-curve Chart B conditions on
  survival, so use its all-activated variant if you need a strict product). In a trailing window
  (Mode B), align cost + retention on an explicit shared `--start` rather than independent
  `--last-weeks` — the two trim on slightly different criteria (populated vs spend-bearing cohort-weeks).
- **`ai_created_at` deduped on read**; the `Other` unattributed row is excluded.
- **Privacy.** Output is aggregate-only (per-week pooled shares), never a UID.

- **Prod-only (PROD-3589 Part B).** Reads `aurie_per_user_daily_cost_prod` (env='prod') instead
  of the base table — dev/QA and not-yet-tagged users are excluded from these aggregates; see
  eng:backfill-aurie-user-env, which owns and refreshes the view. Still aggregate-only, never
  emits a UID.

## Origin

The retention companion to the cost decay curve — the second factor for lifetime-cost / payback
projection ([PROD-3556](https://linear.app/aura-health/issue/PROD-3556)); part of the
cost-understanding chart set ([PROD-3498](https://linear.app/aura-health/issue/PROD-3498)).
