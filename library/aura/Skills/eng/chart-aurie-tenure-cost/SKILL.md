---
name: chart-aurie-tenure-cost
description: >
  Split each day's Aurie cost into NEW users (first 7 days since AI signup) vs ESTABLISHED
  users and chart it as a stacked daily time series — so the new-subscriber-surge distortion
  is visible live on the calendar, not just by join-week cohort. New users cost the most in
  their first week, so a surge inflates headline daily cost even as per-user cost falls.
  Trigger on: "new vs established cost", "daily cost new users", "new user cost split",
  "cost by user age", "cost by tenure", "first 7 days cost", "split daily cost by new vs
  established", "how much do new users cost per day". Joins aura-main.aurie_cost per-user
  daily cost to the aurie_user_ai_created dimension; composes eng:backfill-aurie-user-ai-created
  as a coverage guard. Aggregate-only — never emits a UID. Not for the full daily KPI
  dashboard (use eng:chart-aurie-daily-cost) or the join-week cohort triangles (use
  eng:chart-aurie-cohort-cost).
metadata:
  version: "3.2.1"
knowledge:
  reads:
    - Guide/Index                  # baseline routing aid (universal)
    - Tools/BigQuery               # dataset/table conventions, GCP_KEY auth, bq.py
    - Compliance/Privacy           # reads PII-bearing tables — output must stay aggregate
---

# Chart Aurie Tenure Cost (new vs established)

## Goal

Show each day's Aurie cost **split by user tenure** — cost from users in their **first 7 days**
since AI signup (`new`) vs everyone older (`established`) — as a stacked daily time series. The
cohort skill (`eng:chart-aurie-cohort-cost`) shows the same new-users-cost-most effect grouped by
join-week; this shows it **live on the calendar**, directly answering the #ai concern that a
new-subscriber surge inflates the headline daily cost even when per-user cost is falling fast.

A user is **new** on cost day `d` when `0 ≤ d − ai_created_at ≤ 6` days, **established** at `≥ 7`.
Cost that can't be aged — the `Other` unattributed row, users missing from the `ai_created_at`
dimension, or a row dated before the user's resolved signup — is bucketed as **other**, so the
three buckets always reconcile to the day's real total.

This is the **calendar/tenure** member of the cost-skill family. Siblings:
`eng:chart-aurie-daily-cost` (the full daily KPI dashboard),
`eng:chart-aurie-cohort-cost` (join-week cost triangles),
`eng:backfill-aurie-user-ai-created` (the `ai_created_at` dimension this composes).

> **Why a sibling, not a panel on `chart-aurie-daily-cost`.** The daily dashboard aggregates the
> per-*day* unit/KPI stores; this split needs a per-*user* join to the `ai_created_at` dimension —
> a different grain. Keeping it separate leaves the dashboard stable and the split independently
> runnable (the "or a variant" option in the request).

## Step 0 — Prerequisites

Reads BigQuery (`aura-main.aurie_cost`) via the bundled `bq.py` — needs `GCP_KEY` and
`google-cloud-bigquery`. No BigQuery MCP, so this is API-first; a missing key/library exits with a
clear message.

## Step 1 — Coverage guard (ensure the `ai_created_at` dimension is complete)

The split joins the cost table to `aura-main.aurie_cost.aurie_user_ai_created`. **Before charting,
make that dimension complete for the cost-table users** by invoking
[`eng:backfill-aurie-user-ai-created`](../backfill-aurie-user-ai-created/SKILL.md) (via the Skill
tool). `tenure.py` also reports its own coverage (`missing` count) and warns if incomplete — a
non-zero `missing` means those users' cost lands in `other` and understates the new/established
split, so run the backfill and re-run.

## Step 2 — Aggregate the daily split

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/chart-aurie-tenure-cost/scripts/tenure.py --last 30
```

Emits JSON:

```jsonc
{
  "cost_data_start": "2026-06-01",
  "cost_data_end": "2026-07-12",
  "new_window_days": 7,
  "coverage": { "cost_users": 8309, "with_ai_created": 8145, "missing": 164 },
  "days": [
    { "date": "2026-07-12", "new_cost": 212.45, "established_cost": 201.16, "other_cost": 3.35,
      "new_users": 892, "established_users": 3006, "total_cost": 416.96 }, …
  ]
}
```

Window flags: `--last N` (most recent N days, default 30), `--from` / `--to` (inclusive
`YYYY-MM-DD`), `--all` (every day in the store).

## Step 3 — Render the stacked daily chart

Render with the visualization tool (host Claude Code):

- **Panel 1 — stacked daily cost bars:** `new_cost` (accent) + `established_cost` + `other_cost`
  stacked to the day's total, one bar per day. Overlay a line for the **new-cost share**
  (`new_cost / total_cost`) on a secondary 0–100% axis — that line is the headline: new users are
  a minority of accounts but often the majority of spend.
- **Panel 2 — per-user daily cost by tenure:** `new_cost / new_users` vs
  `established_cost / established_users` as two lines. This is the "new users cost ~N× more per
  user" view (typically ~3–5× in week 0), and it stays flat even when the stacked total spikes —
  the point being that the spike is a *mix* effect, not a per-user cost regression.

Add metric cards: latest-day total, latest new-cost share, latest new vs established per-user cost
(and the ratio), and coverage (`with_ai_created / cost_users`). If the widget tool is unavailable,
skip to Step 4.

## Step 4 — Print the daily table

Always print a markdown table: one row per day with `new_cost`, `established_cost`, `other_cost`,
`total_cost`, `new_users`, `established_users`, and the new-cost share. Follow with a one-line read:
the latest new-cost share, the new-vs-established per-user ratio, and whether a recent total spike
was driven by new-user mix (share up) or a per-user cost rise.

## Notes & caveats

- **`new` = first 7 days since AI signup**, day-diff `0..6` (`ai_created_at`, the AI join — not the
  Aura `createdAt`, which is years old for legacy meditation users). `established` = `≥ 7` days.
- **Three buckets reconcile.** `new + established + other = total_cost` every day. `other` holds the
  `Other` unattributed row + users missing `ai_created_at` + any pre-signup-dated row; a large
  `other` means run the backfill (Step 1).
- **`established_users` swings day to day** because the store writes a per-user row only on days
  with attributed spend, and ambient/batch jobs touch different user counts per day. The *cost*
  buckets are the signal; the user counts are context for the per-user lines.
- **Per-user cost, not per-call billing.** Cost is allocated (token/minute share calibrated to the
  authoritative daily totals) upstream in `eng:store-aurie-user-daily-cost`.
- **`ai_created_at` dimension** is deduped on read (`ROW_NUMBER … ORDER BY fetched_at DESC`), so a
  rare append-only duplicate can't double-count a user.
- **Privacy.** Output is aggregate-only (daily bucket sums + counts), never a UID, though it reads
  the PII-bearing per-user cost + dimension tables.

- **Prod-only (PROD-3589 Part B).** Reads `aurie_per_user_daily_cost_prod` (env='prod') instead
  of the base table — dev/QA and not-yet-tagged users are excluded from these aggregates; see
  eng:backfill-aurie-user-env, which owns and refreshes the view. Still aggregate-only, never
  emits a UID.

## Origin

Idea #2 from the #ai cost thread (2026-07-13): make the new-subscriber-surge distortion visible
live on the daily view, not only in the join-week cohorts — [PROD-3505](https://linear.app/aura-health/issue/PROD-3505),
follow-up to [PROD-3498](https://linear.app/aura-health/issue/PROD-3498).
