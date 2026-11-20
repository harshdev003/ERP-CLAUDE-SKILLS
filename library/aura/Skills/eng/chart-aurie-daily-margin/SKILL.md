---
name: chart-aurie-daily-margin
description: >
  Daily Aurie contribution margin — amortized NET subscription revenue vs variable cost, split
  into paid-user contribution and free/trial/grace "drag," so you can read "are we making money
  today?" on the calendar (the daily companion to the cohort payback view). Trigger on: "daily
  margin", "are we making money", "daily profit/loss", "daily P&L", "contribution margin",
  "daily net revenue vs cost", "net revenue minus cost", "margin by day", "profitability per
  day". Reads BigQuery aura-main.aurie_cost (per-user daily cost + the aurie_user_subscription
  amortized-revenue dimension) via the bundled bq.py + GCP_KEY; no BigQuery MCP, so this is
  API-first. Aggregate-only — never emits a UID. For the cohort/join-week payback view use
  eng:chart-aurie-cohort-payback; for cost without revenue use eng:chart-aurie-daily-cost.
metadata:
  version: "3.5.1"
knowledge:
  reads:
    - Guide/Index                        # baseline routing aid (universal)
    - Tools/BigQuery                     # dataset/table conventions, GCP_KEY auth, bq.py
    - Tools/Mixpanel/Subscription Events # revenue = amortized paid/renewed periods; lifecycle
    - Compliance/Privacy                 # reads PII-bearing tables — output must stay aggregate
---

# Chart Aurie Daily Margin

## Goal

Answer **"are we making money, day by day?"** For each day on the daily-cost spine, compute
**amortized net subscription revenue** vs **variable cost**, and split the result into:

- **`paid_contribution`** — net revenue minus the cost of paid users (are paying subscribers
  covering their own variable cost, today?).
- **`free_drag`** — the cost of everyone else (free/trial/grace/other), which paid revenue has
  to cover for the business overall to be net-positive that day.
- **`net`** — amortized net revenue minus total variable cost, the headline number.

This is the **daily/calendar** member of the margin-skill family. Sibling:
`eng:chart-aurie-cohort-payback` (the same cost-vs-revenue question, grouped by join-week cohort
and read as cumulative payback rather than a daily P&L). Use this skill for "how are we doing
today/this week"; use the cohort skill for "do new subscribers pay back their acquisition cost."

## Revenue basis — amortized (recognized), NOMINAL period, net of a fee assumption

Each subscription period's revenue is spread evenly across its **nominal** billing length, not
the raw `[purchase_at, expiration_at]` window:

- `period_days` = **365.25** for `period = 'year'`, **30.44** for `'month'`, else
  `GREATEST(TIMESTAMP_DIFF(expiration_at, purchase_at, DAY), 1)` as a fallback for null/other
  period tokens. Some `expiration_at` values are corrupt (minutes after purchase, an immediate
  expire/refund) — amortizing by the nominal period avoids dumping a full period of revenue onto
  day 0 for those rows (same rationale as `eng:chart-aurie-cohort-payback`, which uses 365/30; this
  skill uses the more precise 365.25/30.44 average-month/year lengths — a deliberate, small
  divergence, not drift).
- A period's `daily_gross = revenue / period_days` contributes to every day in
  `[DATE(purchase_at), DATE(purchase_at) + CAST(period_days AS INT64) days)` — half-open.
- **Net of a fee assumption:** `amort_net_revenue = amort_gross_revenue * (1 - fee_rate)`,
  `fee_rate` defaulting to **0.30** (a blended in-app-purchase store-fee estimate — Apple/Google
  take roughly this share). Override with `--fee-rate` when a more precise blended rate is known;
  this is a configurable assumption, not a measured value.

**Cost** is the per-user daily variable cost (LLM + voice) from `aurie_per_user_daily_cost` —
**no fixed/overhead allocation**.

## Step 0 — Prerequisites

Reads BigQuery (`aura-main.aurie_cost`) via the bundled `bq.py` — needs `GCP_KEY` and
`google-cloud-bigquery`. No BigQuery MCP, so this is API-first; a missing key/library exits with a
clear message.

## Step 1 — Ensure the revenue dimension is current

This skill joins `aura-main.aurie_cost.aurie_user_subscription`. **Before charting, (re)build that
dimension** by invoking [`eng:build-aurie-user-subscription`](../build-aurie-user-subscription/SKILL.md)
(via the Skill tool) so revenue reflects the latest paid/renewed events.

## Step 2 — Aggregate the daily margin

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/chart-aurie-daily-margin/scripts/margin.py --all
```

Emits JSON:

```jsonc
{
  "daily": [
    { "date": "2026-07-14", "amort_net_revenue": 291.58, "total_cost": 476.88,
      "paid_cost": 385.87, "nonpaid_cost": 91.02,
      "paid_contribution": -94.29, "free_drag": 91.02, "net": -185.31 }, …
  ],
  "cumulative": [ { "date": "2026-06-01", "cumulative_net": -123.45 }, … ],
  "segment_summary": {
    "days": 44, "paid_contribution": -1965.08, "free_drag": 3997.73, "net": -5962.81,
    "avg_paid_contribution_per_day": -44.66, "avg_free_drag_per_day": 90.86,
    "avg_net_per_day": -135.52
  },
  "concentration": {
    "total_users": 8679, "negative_net_users": 7889, "total_loss": 10364.39,
    "worst_loss_share": { "top_1_pct": 0.4919, "top_5_pct": 0.7912, "top_10_pct": 0.8858 }
  },
  "meta": { "window": "all days present", "cost_data_start": "2026-06-01",
            "cost_data_end": "2026-07-14", "fee_rate": 0.3, "generated": "…",
            "source_tables": ["aura-main.aurie_cost.aurie_per_user_daily_cost",
                               "aura-main.aurie_cost.aurie_user_subscription"] }
}
```

Flags: `--all` (default — every day in the store), `--last N` (most recent N days present,
overrides `--all`), `--fee-rate` (blended IAP store-fee rate, default 0.30), `--json <path>`
(write to a file instead of stdout).

`concentration` ranks **real users only** (the `Other` reconciling row is excluded) by their own
`net = amortized net revenue − cost` over the window, computed and ranked **inside BigQuery** —
the output is counts and loss-concentration shares, **never a user_id**. It answers "is the daily
loss broad-based or driven by a small tail of heavy-cost users."

## Step 3 — Render the daily margin chart

Render with the visualization tool (host Claude Code):

- **Panel 1 — stacked daily bars:** `paid_contribution` and `free_drag` as two series (one
  typically positive-ish, the other a cost-only drag), with a `net` line overlaid — the sum of the
  two contribution pieces. Zero-line reference so above/below net-positive is obvious at a glance.
- **Panel 2 — cumulative net:** the `cumulative` series as a running total since the window start —
  is the trend toward breakeven or away from it.
- **Metric cards:** latest-day net, window `segment_summary` (`net`, `avg_net_per_day`), and the
  concentration read (`negative_net_users / total_users`, `worst_loss_share.top_5_pct`).

If the widget tool is unavailable, skip to Step 4.

## Step 4 — Print the daily table

Always print a markdown table: one row per day with `amort_net_revenue`, `total_cost`,
`paid_contribution`, `free_drag`, `net`. Follow with a one-line read: the latest day's net, the
window's average net/day, whether paid users are covering their own cost
(`paid_contribution` sign), and the loss concentration (e.g. "the worst 5% of loss-making users
drive ~79% of total loss").

## Notes & caveats

- **`fee_rate` is an assumption, not a measured value.** The default 0.30 approximates a blended
  Apple/Google IAP cut; if Aura's actual blended fee differs, pass `--fee-rate` — don't treat the
  default as authoritative in a finance-facing readout.
- **Variable cost only.** `total_cost` is LLM + voice from the daily-cost store; no fixed costs,
  headcount, or infra overhead are allocated here — `net` is a **contribution margin**, not a
  fully-loaded P&L.
- **`paid` is the only "paid" bucket.** `paid_cost`/`paid_contribution` use
  `subscription_state = 'paid'` exactly; `free_drag` covers everything else (`free`, `trial`,
  `grace`, `other`, and the `Other` reconciling row's own cost) — a deliberate, stricter split than
  `eng:chart-aurie-free-user-cost`'s `paid` + `grace` grouping. Don't conflate the two skills' totals.
- **Revenue is gross of refunds** (same caveat as `eng:chart-aurie-cohort-payback`) — the
  `aurie_user_subscription.revenue` field isn't verified net of refunds; amortizing by nominal
  period limits the day-0 distortion from the handful of corrupt-`expiration_at` rows, but treat
  daily margin as directional.
- **Window = days present in the cost store**, not calendar days — `--last N` matches the sibling
  cost charts so comparisons line up on gap days.
- **Privacy.** Output is aggregate-only (daily/window sums + loss-concentration shares), never a
  UID, though it reads the PII-bearing per-user cost + subscription tables.

- **Prod-only, both sides (PROD-3589 Part B).** Cost reads `aurie_per_user_daily_cost_prod`
  (env='prod') instead of the base table, and **revenue is filtered to the same prod population** —
  `aurie_user_subscription` is inner-joined to the deduped `env='prod'` user set before amortizing,
  so dev/test subscription revenue can't count against prod-only cost and overstate net. `aurie_user_env`
  is append-only with no unique key, so every prod-user select dedups to the latest row per user; see
  eng:backfill-aurie-user-env, which owns and refreshes the view. Still aggregate-only, never emits a UID.
  Also emits a top-level `excluded` block (`dev_cost`, `unknown_cost`, `other_cost`, `untagged_cost`,
  `total_excluded`, `all_cost`, `pct_of_all`, `other_by_component`) — a small extra query against the
  *base* table + the deduped `aurie_user_env` for the same window, computing exactly what the prod-only
  view dropped. `other_by_component` splits `other_cost` by cost column (`llm`, the ElevenLabs/Cartesia/
  Fish/Inworld/Deepgram/LiveKit voice columns, and the signed `livekit_unused_capacity`) and sums to it exactly;
  the `Other` row is every vendor's remainder, LLM usually the largest part, not a voice-only one.
  `collapsed_voice_paths` flags the vendor-days inside it where a per-user voice path billed and no
  user carried any of it (an attribution miss, or a charge with no session behind it). It lists the
  days at or above `floor_usd` (1.00 USD) and counts the rest as `below_floor_count` /
  `below_floor_dollars`. Intelligence Service TTS and unused LiveKit capacity are left out because
  they reach no user by design. Derived from the stored rows, so no schema field is needed
  ([PROD-6992](https://linear.app/aura-health/issue/PROD-6992)).
  `eng:build-aurie-cost-report` reads this block to render a prod-exclusions callout that splits genuine
  dev/QA-user traffic from the unattributable `Other` reconciling remainder.

## Origin

[PROD-3592](https://linear.app/aura-health/issue/PROD-3592) — the daily-calendar answer to "are we
making money" behind the #ai margin thread, complementing the cohort payback view
([PROD-3504](https://linear.app/aura-health/issue/PROD-3504)).
