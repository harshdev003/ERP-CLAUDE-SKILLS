---
name: chart-aurie-revenue
description: >
  Aurie subscription REVENUE over time — gross + net, on two bases (BOOKINGS: cash collected on the
  purchase day; RECOGNIZED: amortized evenly over each period, accrued to date) — at daily / weekly /
  monthly grain, with active-subscriber count + net ARPU. The revenue counterpart to the cost-only
  chart-aurie-* family (revenue on its own, not paired against cost). Trigger on: "aurie revenue",
  "revenue over time", "revenue trend", "MRR", "bookings vs recognized", "subscription revenue chart",
  "how much revenue is aurie making", "monthly revenue", "weekly revenue", "ARPU". Reads BigQuery
  aura-main.aurie_cost.aurie_user_subscription (composes eng:build-aurie-user-subscription).
  Aggregate-only — never emits a UID. For revenue split by plan use eng:chart-aurie-revenue-by-tier;
  for revenue vs cost use eng:chart-aurie-daily-margin / eng:chart-aurie-cohort-payback.
metadata:
  version: "3.2.2"
knowledge:
  reads:
    - Guide/Index                        # baseline routing aid (universal)
    - Tools/BigQuery                     # dataset/table conventions, GCP_KEY auth, bq.py
    - Tools/Mixpanel/Subscription Events # revenue = paid/renewed periods; amortization basis
    - Compliance/Privacy                 # reads PII-bearing tables — output must stay aggregate
---

# Chart Aurie Revenue

## Goal

Show **Aurie subscription revenue over time**, on its own — the revenue counterpart to the cost-only
`chart-aurie-*` family, which only ever shows revenue paired against cost (daily-margin,
cohort-payback). Two bases, because they answer different questions:

- **Bookings** — gross cash **collected** on the purchase day. An annual sub books its full year on
  day 1. This is the "how much money came in" / cash-flow view.
- **Recognized (amortized)** — each period's revenue spread evenly across its **nominal** length
  (`year` → 365 days, `month` → 30), summed **to date**. This is the "how much did we earn for the
  service we've actually delivered" view — the right basis to compare against cost.

Plus, per bucket: active-subscriber count and **net ARPU** (recognized). Net revenue =
`gross × (1 − fee_rate)`, default `fee_rate` 0.30 (blended IAP store cut).

Siblings: `eng:chart-aurie-revenue-by-tier` (same revenue, split by plan + billing period),
`eng:chart-aurie-daily-margin` / `eng:chart-aurie-cohort-payback` (revenue vs cost),
`eng:build-aurie-user-subscription` (the revenue dimension this reads).

## Revenue basis — bookings vs recognized

The amortized (recognized) basis matches `eng:chart-aurie-cohort-payback`: the **nominal** period
length is authoritative because ~33 source rows have a corrupt `expiration_at` only minutes after
purchase (an immediate expire/refund event) that would otherwise dump a full period of revenue onto
day 0. Unknown `period` tokens fall back to the raw `[purchase_at, expiration_at]` window (floored at
1 day). Recognized revenue is summed only up to the window end — revenue recognized *to date*, never
projected forward.

**Recognized ≠ cash.** Bookings is the cash lens (annual sub pays back immediately); recognized is
the ongoing-relationship lens. Revenue is **gross of refunds** (the `revenue` field is
per-transaction, not verified net of refunds) — treat totals as directional.

## Step 0 — Prerequisites

Reads BigQuery (`aura-main.aurie_cost`) via the bundled `bq.py` — needs `GCP_KEY` and
`google-cloud-bigquery`. No BigQuery MCP, so this is API-first; a missing key/library exits with a
clear message.

## Step 1 — Ensure the revenue dimension is current

This skill reads `aura-main.aurie_cost.aurie_user_subscription`. **Before charting, (re)build that
dimension** by invoking [`eng:build-aurie-user-subscription`](../build-aurie-user-subscription/SKILL.md)
(via the Skill tool) so revenue reflects the latest paid/renewed events. `revenue.py` exits early
with a clear message if the table is missing.

## Step 2 — Aggregate the revenue

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/chart-aurie-revenue/scripts/revenue.py --monthly
```

Emits JSON:

```jsonc
{
  "window": { "start": "2025-11-13", "end": "2026-07-17" },
  "grain": "monthly",
  "fee_rate": 0.3,
  "env": "prod",
  "revenue_basis": { "bookings": "...", "recognized": "..." },
  "totals": { "bookings_gross": 59604.49, "bookings_net": 41723.14,
              "recognized_gross": 15406.93, "recognized_net": 10784.83 },
  "bookings":   [ { "bucket": "2026-07-01", "gross": 18225.39, "net": 12757.77,
                    "periods": 461, "paying_users": 459 }, … ],
  "recognized": [ { "bucket": "2026-07-01", "gross": 5842.42, "net": 4089.69,
                    "active_subscribers": 1146, "arpu_net": 3.57 }, … ]
}
```

Flags:
- `--daily` / `--weekly` / `--monthly` (or `--grain daily|weekly|monthly`; default **weekly**).
- `--since YYYY-MM-DD` / `--until YYYY-MM-DD` (default: first purchase → today, UTC).
- `--fee-rate F` — net fraction override (default 0.30). `--all-env` — include dev/QA users (default
  restricts to env='prod').

## Step 3 — Render the revenue chart

Render with the visualization tool (host Claude Code):

- **Primary** — a bar or line of **gross + net** per bucket, one panel per basis (bookings and
  recognized side by side), so the cash-vs-earned gap is visible. Recognized is the smoother line;
  bookings spikes with annual purchases.
- **Secondary** — recognized **active subscribers** and **net ARPU** as an overlaid line / second
  axis.
- **Metric cards** — window totals (bookings gross/net, recognized gross/net), latest-bucket
  active subscribers + net ARPU.

If the widget tool is unavailable, skip to Step 4.

## Step 4 — Print the revenue table

Always print a markdown table: one row per bucket with bookings gross/net + periods/paying-users and
recognized gross/net + active-subscribers/net-ARPU. Follow with a one-line read: the revenue trend,
the bookings-vs-recognized gap (deferred annual revenue), and the latest net ARPU — with the
gross-of-refunds caveat.

## Notes & caveats

- **Bookings buckets skip days with no purchases**; recognized has a row for every bucket in the
  window (it accrues from earlier periods). This is correct, not a gap.
- **Prod-only by default (PROD-3589 Part B).** Revenue is restricted to the deduped `aurie_user_env`
  env='prod' population — the same filter the cost aggregates use — so dev/QA test purchases don't
  inflate the trend. `--all-env` includes everything (typically ~7% higher gross).
- **Recognized ≠ cash; revenue is gross of refunds.** See "Revenue basis" above.
- **Privacy.** Output is aggregate-only (per-bucket sums + distinct counts), never a UID.

## Origin

Revenue-trend counterpart to the cost chart family — [PROD-3662](https://linear.app/aura-health/issue/PROD-3662)
(parent [PROD-3244](https://linear.app/aura-health/issue/PROD-3244)); builds on
[PROD-3510](https://linear.app/aura-health/issue/PROD-3510) (the revenue dimension) and
[PROD-3591](https://linear.app/aura-health/issue/PROD-3591) (tier/period fields).
