---
name: chart-aurie-cumulative-cost
description: >
  Aurie cumulative cost-to-date per joined user — the running total of the cost-decay curve, per segment
  (cum cost/user by weeks-since-join) plus per-cohort cost-to-date (total $/user spent so far per join-week).
  Same three segments as eng:chart-aurie-cost-curve: all users (since AI signup), premium (since
  subscription activation, all-activated), free-never-subscribed. Trigger for: "cumulative cost", "cost to
  date", "cost so far per cohort", "running total cost per user", "how much have we spent per user". The
  observed companion to eng:chart-aurie-lifetime-cost (which projects this to a 52w/104w horizon) and
  eng:chart-aurie-cost-curve (the per-week decay). Composes eng:backfill-aurie-user-ai-created; reads
  BigQuery aura-main.aurie_cost. Aggregate-only — never emits a UID.
metadata:
  version: "3.2.1"
knowledge:
  reads:
    - Guide/Index                  # baseline routing aid (universal)
    - Tools/BigQuery               # dataset/table conventions, GCP_KEY auth, bq.py
    - Compliance/Privacy           # reads PII-bearing tables — output must stay aggregate
---

# Chart Aurie Cumulative Cost

## Goal

Show the **cumulative** cost-to-date per joined user — the running total of the cost-decay curve — so you
can read "how much have we spent, cumulatively, on a joined user by week N," per segment, plus each
join-week cohort's total cost-to-date.

- **Chart A — weeks since AI signup (`ai_created_at`), all users.**
- **Chart B — weeks since subscription activation (`ai_subscription_activated`), users who activated premium**
  (all-activated denominator — NOT survivorship-filtered to current-premium, so it's the right base for
  lifetime economics).
- **Chart C — weeks since AI signup, free users who never bought a subscription.**

`cum(N)` = `Σ_{k=0..N} avg_per_user(k)`, where `avg_per_user(k)` = `Σ(cohort-week-k cost) ÷ Σ(cohort size)`
over the cohorts complete at week k. Because the per-week figure is cost per *joined* user (a churned user
counts as \$0), retention is already baked in and the running total is the cumulative lifetime cost so far.

## Step 0 — Prerequisites

Reads BigQuery (`aura-main.aurie_cost` + the Mixpanel Nessie `ai_subscription_activated` event) via the
bundled `bq.py` — needs `GCP_KEY` and `google-cloud-bigquery`. No BigQuery MCP, so this is API-first; a
missing key/library exits with a clear message.

## Step 1 — Coverage guard

Charts A/C join `aura-main.aurie_cost.aurie_user_ai_created`. Before charting, make that dimension complete
by invoking [`eng:backfill-aurie-user-ai-created`](../backfill-aurie-user-ai-created/SKILL.md).
`cumulative.py` reports coverage (`missing`) and warns if incomplete.

## Step 2 — Aggregate

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/chart-aurie-cumulative-cost/scripts/cumulative.py --max-week 8
```

Emits JSON per segment: `curve` (pooled `{week_n, avg_per_user, cum_per_user, users, n_cohorts}`) and
`cohorts` (`{cohort_week, cohort_size, weeks_observed, cost_to_date, cum:[{week_n, cum_per_user}]}`).
Flags: `--max-week N`, `--last-weeks N` (trailing window), `--start YYYY-MM-DD`.

## Step 3 — Render

Render `curve` as a **cumulative line chart** (x = `week_n`, y = `cum_per_user`, one line per segment,
linear axis — cumulative is monotonic). Optionally render `cohorts[].cost_to_date` as a per-cohort bar.
`eng:build-aurie-cost-report` renders the cumulative curve. If the widget tool is unavailable, print a
per-segment table: `week_n` | `cum_per_user`, plus a per-cohort `cohort_week` | `cost_to_date` table.

## Notes & caveats

- **Cumulative = running total of the pooled cost-decay curve.** A week-N point uses only cohorts complete
  at week N (same completeness rule as the curve), so it never divides a partial week by the full cohort.
- **Per-joined-user (retention baked in).** The denominator is joined users; churned users count as \$0, so
  the cumulative is cost per joined user — directly the lifetime-so-far.
- **Premium = all-activated**, not current-premium — the correct base for lifetime economics (the cost
  curve's premium line conditions on survivors and reads higher).
- **Per-cohort cost-to-date is age-confounded** — older cohorts have more weeks, so compare cohorts at the
  same week_n (use `cum`), not raw `cost_to_date`.
- **Privacy.** Output is aggregate-only (per-week pooled totals), never a UID.

- **Prod-only (PROD-3589 Part B).** Reads `aurie_per_user_daily_cost_prod` (env='prod') instead
  of the base table — dev/QA and not-yet-tagged users are excluded from these aggregates; see
  eng:backfill-aurie-user-env, which owns and refreshes the view. Still aggregate-only, never
  emits a UID.

## Origin

The observed cumulative behind the lifetime projection ([PROD-3559](https://linear.app/aura-health/issue/PROD-3559));
requested in the cost thread (cumulative cost-to-date alongside the cohort graph). Part of the
cost-understanding chart set ([PROD-3498](https://linear.app/aura-health/issue/PROD-3498)).
