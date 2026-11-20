---
name: chart-aurie-revenue-by-tier
description: >
  Aurie subscription REVENUE split by TIER (Lite / Plus / Growth / Flourish / Unlimited-Internal) and
  by BILLING PERIOD (monthly vs annual) — gross + net, on both bookings and recognized bases, with
  per-tier and per-billing subtotals and active-subscriber counts. Answers "how much does each plan
  bring in, and is annual or monthly bigger?". Trigger on: "revenue by tier", "revenue by plan",
  "revenue by package", "Lite Plus Growth Flourish revenue", "monthly vs annual revenue", "plan mix",
  "which tier makes the most", "annual vs monthly subscriptions". Reads BigQuery
  aura-main.aurie_cost.aurie_user_subscription (package_id/product_id -> tier via the
  eng:manage-ai-packages catalogue; composes eng:build-aurie-user-subscription). Aggregate-only —
  never emits a UID. For the revenue trend over time use eng:chart-aurie-revenue; for revenue vs cost
  use eng:chart-aurie-daily-margin / eng:chart-aurie-cohort-payback.
metadata:
  version: "3.2.2"
knowledge:
  reads:
    - Guide/Index                        # baseline routing aid (universal)
    - Tools/BigQuery                     # dataset/table conventions, GCP_KEY auth, bq.py
    - Tools/Mixpanel/Subscription Events # revenue = paid/renewed periods; package_id/period fields
    - Compliance/Privacy                 # reads PII-bearing tables — output must stay aggregate
---

# Chart Aurie Revenue by Tier

## Goal

Break Aurie subscription revenue down by **plan** — the tier (Lite / Plus / Growth / Flourish /
Unlimited-Internal) and the billing period (monthly vs annual) — so you can read the plan mix: which
tier brings in the most, and whether annual or monthly dominates.

Each subscription **period** is attributed to **its own** tier + billing period (not the user's
latest), which is the right basis for "how much did each plan bring in". Two revenue bases (same as
`eng:chart-aurie-revenue`): **bookings** (cash on the purchase day) and **recognized** (amortized,
to date). Net = `gross × (1 − fee_rate)`, default `fee_rate` 0.30.

Siblings: `eng:chart-aurie-revenue` (revenue trend over time),
`eng:chart-aurie-cost-by-channel` (same by-dimension shape, cost side),
`eng:build-aurie-user-subscription` (the revenue dimension this reads, incl. `package_id`/`period`).

## Tier resolution

`package_id → tier` via `STARTS_WITH` on the catalogue UUID's leading segment (source of truth:
[`eng:manage-ai-packages`](../manage-ai-packages/SKILL.md) `fetch_catalogue.py`; mirrors the
`_TIER_CASE` resolver in `eng:report-aurie-margin-users` — keep both in sync if the catalogue
changes):

| segment | tier | | segment | tier |
|---|---|---|---|---|
| `b7b32619` | Aurie Lite | | `4044faaf` | Aurie Flourish |
| `9428b0ed` | Aurie Plus | | `295a1ed6` | Aurie Unlimited-Internal |
| `f727ce9a` | Aurie Growth | | | |

Legacy periods that predate packageId tagging carry a NULL `package_id` and resolve by `product_id`
(`ai_monthly_1999` / `ai_yearly_6999` → `Plus (legacy)`; anything else unmapped →
`Plus (legacy/unmapped)`). Billing period comes from the `period` field (`year` → annual, `month` →
monthly, else `other`).

## Step 0 — Prerequisites

Reads BigQuery (`aura-main.aurie_cost`) via the bundled `bq.py` — needs `GCP_KEY` and
`google-cloud-bigquery`. No BigQuery MCP, so this is API-first; a missing key/library exits with a
clear message.

## Step 1 — Ensure the revenue dimension is current

Reads `aura-main.aurie_cost.aurie_user_subscription` (with `package_id`/`product_id` populated).
**Before charting, (re)build it** by invoking
[`eng:build-aurie-user-subscription`](../build-aurie-user-subscription/SKILL.md) (via the Skill tool).
`revenue_by_tier.py` exits early with a clear message if the table is missing.

## Step 2 — Aggregate the cross-tab

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/chart-aurie-revenue-by-tier/scripts/revenue_by_tier.py
```

Emits JSON:

```jsonc
{
  "window": { "start": "2025-11-13", "end": "2026-07-17" },
  "fee_rate": 0.3,
  "env": "prod",
  "revenue_basis": { "bookings": "...", "recognized": "..." },
  "totals":    { "bookings_gross": 59604.49, "recognized_gross": 15406.93, … },
  "by_tier":   [ { "tier": "Aurie Plus", "bookings_gross": 32681.63, "recognized_gross": 7486.06,
                   "active_subscribers": 712, … }, … ],
  "by_billing":[ { "billing": "annual", "bookings_gross": 47753.26, "active_subscribers": 661, … },
                 { "billing": "monthly", "bookings_gross": 11851.23, … } ],
  "rows":      [ { "tier": "Aurie Plus", "billing": "annual", "bookings_gross": 22326.81,
                   "bookings_periods": 319, "bookings_users": 318, "recognized_gross": …,
                   "active_subscribers": 318 }, … ]
}
```

Flags: `--since` / `--until YYYY-MM-DD` (default: first purchase → today, UTC), `--fee-rate F`
(default 0.30), `--all-env` (include dev/QA users; default restricts to env='prod').

## Step 3 — Render the chart

Render with the visualization tool (host Claude Code):

- **Primary** — stacked/grouped bars of bookings gross (or net) **by tier**, each bar split by
  **billing period** (monthly vs annual). A second small chart for the recognized basis.
- **Metric cards** — window totals, the annual-vs-monthly split, and the dominant tier's share.

If the widget tool is unavailable, skip to Step 4.

## Step 4 — Print the table

Always print a markdown table: one row per (tier, billing) with bookings gross/net + periods, and
recognized gross/net + active subscribers; then the `by_tier` and `by_billing` subtotal rows. Follow
with a one-line read: the dominant tier, the annual-vs-monthly mix, and any legacy-bucket share.

## Notes & caveats

- **Per-period attribution.** Revenue is attributed to the tier/billing of the period that produced
  it — a user who upgraded contributes to **both** tiers' bookings across their periods. Correct for
  revenue attribution; don't read `by_tier` rows as distinct-user counts.
- **`active_subscribers` is a per-(tier, billing) distinct count** (users entitled as of the window
  end). The `by_tier` / `by_billing` subtotals **sum** those counts, so a user with active periods in
  two cells is counted in each — treat the subtotals as directional plan-mix, not an exact headcount.
- **Legacy buckets.** Periods before packageId tagging land in `Plus (legacy)` /
  `Plus (legacy/unmapped)` — a meaningful share of historical revenue. They are genuinely Plus-family
  by price, but can't be split finer without the catalogue mapping at purchase time.
- **Prod-only by default (PROD-3589 Part B).** Restricted to the deduped `aurie_user_env` env='prod'
  population; `--all-env` includes everything.
- **Recognized ≠ cash; revenue is gross of refunds** (see `eng:chart-aurie-revenue`).
- **Privacy.** Output is aggregate-only (tier × billing sums + distinct counts), never a UID.

## Origin

The plan-mix counterpart to `eng:chart-aurie-revenue` —
[PROD-3663](https://linear.app/aura-health/issue/PROD-3663) (parent
[PROD-3244](https://linear.app/aura-health/issue/PROD-3244)); builds on
[PROD-3510](https://linear.app/aura-health/issue/PROD-3510) (the revenue dimension) and
[PROD-3591](https://linear.app/aura-health/issue/PROD-3591) (the `package_id` / `product_id` tier
fields).
