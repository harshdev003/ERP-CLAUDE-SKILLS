---
name: build-aurie-user-subscription
description: >
  Build and refresh the per-user subscription/revenue dimension in BigQuery
  (aura-main.aurie_cost.aurie_user_subscription) — one row per Aurie subscription period
  (paid or renewed) with its entitlement window and revenue, plus a negative row per refund,
  reconstructed from the Mixpanel subscription events; and the aurie_user_current_plan view, one
  row per user with their current plan's price. Trigger on: "build aurie user subscription", "per-user subscription
  revenue dimension", "reconstruct subscription intervals", "how much has each user paid",
  "refresh aurie_user_subscription", "subscription revenue per user". The foundation for
  per-user paid-vs-cost margin and amortized cohort payback (the cohort-payback chart composes
  this). Pure BigQuery transform, no API calls. Do NOT use to render charts or compute cost
  (that's the cohort/chart skills, which read this table).
metadata:
  version: "3.3.1"
knowledge:
  reads:
    - Guide/Index                        # baseline routing aid (universal)
    - Tools/BigQuery                     # dataset/table conventions, GCP_KEY auth, bq.py, ai_ prefix
    - Tools/Mixpanel                     # Nessie subscription-event tables + the ai_ prefix rule
    - Tools/Mixpanel/Subscription Events # lifecycle model: premium = trial ∪ grace ∪ paid; cancel ≠ end
---

# Build Aurie User Subscription

## Goal

Maintain **`aura-main.aurie_cost.aurie_user_subscription`** — the per-user subscription/revenue
dimension — as the atomic foundation for per-user margin and payback analysis. Each row is one
**revenue-bearing subscription period**: a paid or renewed purchase and its entitlement window
`[purchase_at, expiration_at]` with the revenue collected. From this grain every per-user rollup
derives (total paid, premium intervals, currently-premium, days premium), and each period's revenue
can be **amortized** evenly across its window to compare against the daily cost store.

It's the write-side maintainer of that table; readers (the per-user paid-vs-cost / cohort-payback
skills) just `JOIN`/`GROUP BY` against it.

## Model (from the subscription lifecycle)

- **Premium = Trial ∪ Grace ∪ Paid** (all entitled). Users cycle Free ↔ Premium (subscribe → lapse
  → re-subscribe), so a user has **multiple periods** — hence the period grain, not one row per user.
- **Revenue = `ai_subscription_paid`.revenue + `ai_subscription_renewed`.revenue** (later periods)
  **− `ai_subscription_refunded`.revenue** (one negative `refunded` row per refund event).
  Every paid event carries `revenue > 0`, `purchaseat`, and `expirationat`, so revenue recognition
  is fully derivable in SQL — no per-user API call.
- **cancel ≠ end.** A cancel sets `autoRenew:false` but the user stays entitled until `expiration_at`;
  grace is still premium. Access truly ends at `expiration_at` (or an expired/revoked/refunded event).
  This table models the *paid entitlement windows*; trial/grace-only extension (no revenue) is out of
  scope here — layer it in later if exact days-premium including trials is needed.

## Table

`aura-main.aurie_cost.aurie_user_subscription` — clustered by `user_id`, unpartitioned, rebuilt
in full each run (`CREATE OR REPLACE`):

| Column | Type | Notes |
|---|---|---|
| `user_id` | STRING | Firebase uid (= cost table `user_id`, the join key) |
| `event_type` | STRING | `paid` \| `renewed` \| `refunded` |
| `event_time` | TIMESTAMP | Mixpanel event time (the refund time on a `refunded` row) |
| `purchase_at` | TIMESTAMP | period start (entitlement start) |
| `expiration_at` | TIMESTAMP | period access-end (entitlement end) |
| `revenue` | FLOAT64 | collected revenue for this period (USD); **negative** on a `refunded` row |
| `period` | STRING | RevenueCat period token (`P1M` / `P1Y` …), nullable |
| `package_id` | STRING | catalogue packageId → **tier** (Lite / Plus / Growth / Flourish); NULL on legacy events that predate packageId tagging |
| `product_id` | STRING | store/provider productId; the tier fallback for legacy null-`package_id` rows via the catalogue `packagePrices` map |
| `provider` | STRING | store/processor from the event (`stripe`, `apple`, …) |
| `country_code` | STRING | country from the event |
| `fetched_at` | TIMESTAMP | build time |

**Refund rows.** One per `ai_subscription_refunded` event, never deduped against the periods
(BackendPayment's counts since 2026-07-05 match row for row: Stripe 349 / \$18,463.04, Apple 12 /
\$719.88). A refund row carries the refunded period's `purchase_at`, `expiration_at`, `period`,
`package_id` and `product_id`, so a reader that amortizes revenue over the window nets it there.
An event with no period dates is windowed at its refund time and counted as
`refunds_dates_inferred` in the summary. A refund does not end the period or count as a
cancellation: an upgrade refund is not churn. Refund history before 2026-11-02 is incomplete in
Mixpanel; that caveat belongs to the analysis, not this table.

## View: `aurie_user_current_plan`

A user's spend is their **current subscription**, the same rule for every store; charges minus
refunds across the table is the cross-check. The view, rebuilt with the table, has one row per user
with a `paid` row, taken from their **latest `paid` row** by `event_time`: `user_id`, `product_id`,
`package_id`, `period`, `purchase_at`, `expiration_at`, `provider`, `country_code`, plus:

- `revenue`: that payment plus every later `renewed` row for the same `product_id` (renewals are
  chained by product; the source has no original-transaction id). Plans it replaced don't count.
- `current_plan_refunded`: a `refunded` row **for the same `product_id`** came after that
  payment, so the user cancelled with a refund and has no subscription; `revenue` is then 0. A
  refund of another product after it is an upgrade credit for the plan they left, not churn (15
  users on the 2026-11-19 dry run). A plan that lapsed without a refund keeps its payments.
- `expiration_at`: from the newest row in the chain (the paid row or its latest renewal), so a
  plan that keeps renewing stays active.
- `first_payment` (the paid row's revenue), `renewal_count`, `is_active` (`expiration_at >
  fetched_at`) and `monthly_price` (the paid row's price: `year` ÷ 12, `month` as is, NULL
  otherwise), as context.

Renewed rows that don't chain to a user's latest paid row (an earlier plan, or a renewal on a
different product id) stay in the table and out of the view; the build prints them as
`unchained_renewals`. A product change at renewal may be an upgrade within one subscription (Apple
can change the id at renewal); that rule is not settled yet.

**No PII** — the subscription events carry no email/name; only the uid, timestamps, and revenue are
stored. **A period is `(user_id, purchase_at, expiration_at)`** — the build dedups on that key to
collapse Mixpanel double-emits and the few renewed events that echo a paid row (keeping `paid` +
higher revenue on a collision); genuinely-distinct later periods survive.

**Tier resolution.** `package_id` maps 1:1 to a tier via the `eng:manage-ai-packages` catalogue
(`packages`: `packageId → name`/`level` — `b7b32619`=Lite (L1), `9428b0ed`=Plus (L2),
`f727ce9a`=Growth (L3), `4044faaf`=Flourish (L4), `295a1ed6`=Unlimited-Internal (L5, not sold)).
Events that predate packageId tagging (the multi-tier `auriePricingTiers` rollout, v1.25.4 /
2026-07-11) carry no `package_id` — resolve those via `product_id` → `packageId` through the
catalogue `packagePrices` map (`productId → packageId`), which is exactly what
`eng:manage-ai-packages` `fetch_catalogue.py` returns. This skill stores the raw identifiers; the
`packageId`/`productId` → tier mapping is applied by the consumer (e.g. tier-split revenue in the
cohort-payback chart) against the live catalogue, so a new tier never requires a schema change here.

## ai_ prefix (critical)

Mixpanel project 963821 is shared by **Aura** (meditation) and **Aurie** (AI). Subscription events
are prefixed by subscription type — Aurie = `AI` → **`ai_subscription_*`**; Aura Premium = `` →
unprefixed `subscription_*` (~300× larger, a different product). This skill is Aurie-only and reads
**only** the `ai_`-prefixed tables (`ai_subscription_paid`, `ai_subscription_renewed`). Never point it
at the unprefixed tables. (Full prefix table: `Tools/Mixpanel/Subscription Events` in AI Knowledge.)

## API-first

No BigQuery MCP; the skill is API-first via `GCP_KEY` (BigQuery + the Mixpanel Nessie dataset). Ships
its own copy of `bq.py` (canonical in `eng:query-bigquery`). Needs `google-cloud-bigquery`. Pure BQ
transform — no `requests`, no Aura API, no per-user calls.

## Step 0 — Prerequisites

```bash
test -n "$GCP_KEY" && echo "gcp: OK" || echo "gcp: MISSING"
```

`GCP_KEY` → BigQuery (`aura-main` read/write) **and** the Mixpanel Nessie dataset
(`mixpanel-prod-1.mixpanel_nessie_day_partitioned_963821`); the SA must be a viewer there
(granted via the `<redacted-email>` group).

## Step 1 — Preview (dry run)

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/build-aurie-user-subscription/scripts/subscription.py --dry-run
```

Computes the dimension and the current-plan view from source and prints the summary (periods,
users, gross revenue, refunds and their \$, net revenue, renewed periods, currently-premium users,
date range, refunds with inferred dates, current plans, active plans (not refunded) and their monthly \$, refunded
current plans and how many have not expired yet) **without writing anything**.

## Step 2 — Build the table

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/build-aurie-user-subscription/scripts/subscription.py
```

Runs `CREATE OR REPLACE TABLE aura-main.aurie_cost.aurie_user_subscription AS SELECT …`, then
`CREATE OR REPLACE VIEW aura-main.aurie_cost.aurie_user_current_plan`, and prints the same summary
read back from both. **This writes to the prod analytics warehouse: run it only on Jerome's go.** Idempotent — a re-run recomputes the same rows (plus
any new subscription events since). Flags: `--project` / `--dataset` overrides.

## Step 3 — Read it (rollups for consumers)

Per-user summary (`total_paid`, intervals, currently-premium) is a `GROUP BY`:

```sql
SELECT user_id,
       ROUND(SUM(revenue), 2)                         AS total_paid,      -- net of refunds
       COUNTIF(event_type != 'refunded')              AS periods,
       MIN(purchase_at)                               AS first_paid_at,
       MAX(expiration_at)                             AS last_expiration,
       MAX(IF(event_type != 'refunded', expiration_at, NULL)) >= CURRENT_TIMESTAMP() AS is_currently_premium
FROM `aura-main.aurie_cost.aurie_user_subscription`
GROUP BY user_id
```

**Amortized (recognized) revenue** for the per-user paid-vs-cost / payback comparison: spread each
period's revenue evenly across its `[purchase_at, expiration_at]` days, then join to the daily cost
store on `(user_id, date)`. That is the methodology the cohort-payback chart (PROD-3504) builds on —
it keeps annual subs from looking free after month 1.

## Notes & caveats

- **Period grain, not per-user.** A user with N purchases has N rows. Roll up with `GROUP BY user_id`.
  Don't `SELECT DISTINCT user_id` expecting one row each.
- **Count periods without refund rows.** `COUNT(*)` counts each refund as a period; use
  `COUNTIF(event_type != 'refunded')`. Sums of `revenue` net refunds by design.
- **Revenue is deduped.** Naive `SUM` over the raw `ai_subscription_paid` table over-counts by the 3
  Mixpanel double-emits (+3 paid/renewed echoes); the build's `(user_id, purchase_at, expiration_at)`
  dedup removes them. Read totals from this table, not the raw event tables.
- **Cost-window skew.** The per-user cost store starts 2026-06-01, but subscription revenue accrues
  from a user's first purchase (earliest 2025-11-13). For a fair margin/payback comparison, align the
  windows (restrict to the cost window, or amortize only the overlapping days) — the raw
  revenue-vs-cost ratio otherwise flatters margins.
- **Aurie only.** Reads `ai_`-prefixed tables exclusively (see ai_ prefix above).

## Origin

Split out as the revenue-side foundation so per-user margin / amortized cohort payback
([PROD-3504](https://linear.app/aura-health/issue/PROD-3504)) joins a stable dimension instead of
re-reconstructing subscription intervals every run — [PROD-3510](https://linear.app/aura-health/issue/PROD-3510).
