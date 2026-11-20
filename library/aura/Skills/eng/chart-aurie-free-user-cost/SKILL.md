---
name: chart-aurie-free-user-cost
description: >
  Free-tier Aurie cost economics — how much we spend on a free user before they convert to paid or
  go dormant. Splits observable-signup users into converters (free cost before their first
  subscription activation = "AI cost to acquire a paying user", plus days-to-convert) vs
  never-bought (lifetime free cost, dormant vs active), with a cost-bucket distribution and the
  conversion rate. Trigger on: "free user cost", "free tier cost", "cost to convert", "cost before
  conversion", "cost of free users", "acquisition cost", "free tier economics", "cost of
  never-converters", "how much do free users cost". Reads BigQuery aura-main.aurie_cost +
  the Mixpanel Nessie ai_subscription_activated event; composes eng:backfill-aurie-user-ai-created.
  Aggregate-only — never emits a UID.
metadata:
  version: "3.2.1"
knowledge:
  reads:
    - Guide/Index                  # baseline routing aid (universal)
    - Tools/BigQuery               # dataset/table conventions, GCP_KEY auth, bq.py
    - Tools/Mixpanel               # Nessie ai_subscription_activated (conversion event)
    - Compliance/Privacy           # reads PII-bearing tables — output must stay aggregate
---

# Chart Aurie Free-User Cost

## Goal

Size the **free-tier acquisition-cost economics**: what we spend on a free user before they either
convert to paid or go dormant. Three groups, over the observable-signup cohort:

- **converters** — users with a subscription activation **on/after signup**. Their **free cost** =
  cost on days in `[ai_created_at, first ai_subscription_activated)` — the AI cost to acquire a
  paying user — plus how long they took to convert (days-to-convert).
- **never-bought** — never activated **and** never carried a `paid`/`grace` state in the cost table
  (belt + suspenders, matching `eng:chart-aurie-cohort-cost` Chart C, so a buyer whose activation
  wasn't captured as an event isn't counted as free). Their free cost = all their cost (always
  free), split **dormant** (idle ≥ `--dormant-days`) vs still-active.
- **excluded-buyer** — a buyer whose cost-to-convert isn't measurable: has a `paid`/`grace` cost row
  but no activation event, **or** an activation dated before signup (a late/fallback `ai_created_at`
  would give a negative gap). Reported as a count only, excluded from both headlines.

Ties the free cohort (Chart C in `eng:chart-aurie-cohort-cost`) to conversion. This is the
free-tier member of the cost-skill family.

## Step 0 — Prerequisites

Reads BigQuery (`aura-main.aurie_cost` + the Mixpanel Nessie `ai_subscription_activated` event) via
the bundled `bq.py` — needs `GCP_KEY` and `google-cloud-bigquery`. No BigQuery MCP, so this is
API-first; a missing key/library exits with a clear message.

## Step 1 — Coverage guard

Joins `aura-main.aurie_cost.aurie_user_ai_created` for the signup anchor. Before running, make that
dimension complete by invoking [`eng:backfill-aurie-user-ai-created`](../backfill-aurie-user-ai-created/SKILL.md).
`free_cost.py` reports coverage (`missing`) and warns if incomplete — missing users are excluded
from the observable-signup cohort.

## Step 2 — Aggregate

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/chart-aurie-free-user-cost/scripts/free_cost.py
```

Emits JSON:

```jsonc
{
  "cost_data_start": "2026-06-01", "cost_data_end": "2026-07-12",
  "cohort": "users whose AI signup is on/after the cost-data start (full free period observed)",
  "dormant_days": 14,
  "coverage": { "cost_users": 8309, "with_ai_created": 8145, "missing": 164 },
  "conversion_rate": 0.1927,
  "converters":   { "n": 709,  "avg_free_cost": 0.13, "median_free_cost": 0.0,  "avg_days_to_convert": 0.5,  "median_days_to_convert": 0.0 },
  "never_bought": { "n": 2971, "avg_free_cost": 0.74, "median_free_cost": 0.21, "total_free_cost": 2200.94, "dormant_users": 260, "dormant_free_cost": 143.69 },
  "excluded_buyer_count": 1,
  "distribution": [ { "group": "never_bought", "bucket": "2+", "users": 288, "cost": 1244.35 }, … ]
}
```

Flag: `--dormant-days N` (idle threshold for "dormant", default 14).

## Step 3 — Render

Render with the visualization tool (host Claude Code):

- **Metric cards:** conversion rate; converters' avg/median cost-to-convert + days-to-convert;
  never-bought avg/median free cost, total sunk free cost, dormant count + dormant cost.
- **Distribution:** grouped histogram of free cost per user (buckets `0` … `2+`), converters vs
  never-bought — shows most converters convert at ~\$0 (day-0) while a heavy free tail drives the
  sunk cost.

If the widget tool is unavailable, skip to Step 4.

## Step 4 — Print the tables

Always print the two-group summary table + the distribution table. Follow with a one-line read:
"AI cost to acquire a paying user ≈ $X (most convert day-0 at ~\$0); never-converters average $Y,
$Z total sunk, concentrated in the heavy free tail."

## Notes & caveats

- **Observable-signup cohort only.** Restricted to users whose `ai_created_at` is on/after the
  cost-data start, so the full free period is captured; earlier signups have censored free cost and
  are excluded. So `conversion_rate` here is over this cohort, not all-time.
- **Cost-to-convert is small because conversion is fast.** Aurie is largely paywall-gated — most
  converters subscribe on day 0 (median days-to-convert 0), so they barely touch the free tier;
  the free-tier spend concentrates in never-converters.
- **`days_to_convert` accuracy depends on `ai_created_at`.** For users whose signup came from the
  Mixpanel first-AI-event fallback (not Postgres `aiCreatedAt`), the anchor can sit close to their
  activation, biasing days-to-convert low. Read it directionally.
- **Dormant is a spend-activity proxy.** "Dormant" = no cost row in the last `--dormant-days` days;
  a free user active but below the per-day spend threshold may look dormant. Directional.
- **`conversion_rate` = ever-bought rate** = `(converters + excluded_buyer) / all cohort users`.
  Every buyer is in the numerator (measurable converters + excluded buyers, who did buy — a
  paid/grace state or a pre-signup activation), and the denominator is the whole cohort. It is *not*
  `converters / (converters + never_bought)`, which would drop real buyers.
- **`ai_created_at` deduped on read**; the `Other` unattributed row is excluded.
- **Privacy.** Aggregate-only (group sums, averages, buckets), never a UID.

- **Prod-only (PROD-3589 Part B).** Reads `aurie_per_user_daily_cost_prod` (env='prod') instead
  of the base table — dev/QA and not-yet-tagged users are excluded from these aggregates; see
  eng:backfill-aurie-user-env, which owns and refreshes the view. Still aggregate-only, never
  emits a UID.

## Origin

Idea #6 from the #ai cost thread (2026-07-13): the free-tier acquisition-cost economics — cost to
convert vs cost of never-converters — [PROD-3509](https://linear.app/aura-health/issue/PROD-3509),
part of the cost-understanding chart set ([PROD-3498](https://linear.app/aura-health/issue/PROD-3498)).
