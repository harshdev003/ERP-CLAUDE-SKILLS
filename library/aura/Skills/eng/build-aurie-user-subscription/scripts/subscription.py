#!/usr/bin/env python3
"""Build the per-user subscription/revenue dimension: aura-main.aurie_cost.aurie_user_subscription.

One row per *revenue-bearing subscription period* (each paid or renewed purchase and its
entitlement window), reconstructed purely in BigQuery from the Mixpanel Nessie subscription
events. This is the atomic foundation for per-user margin / payback: from the period grain a
consumer derives total_paid, premium intervals [(purchase_at, expiration_at)], is_currently_premium,
days_premium, and can amortize each period's revenue evenly across its [purchase_at, expiration_at]
window to compare against the daily cost store.

Full rebuild, idempotent: `CREATE OR REPLACE TABLE ... AS SELECT`. Revenue events are immutable,
so a re-run just recomputes the same rows from source (plus any new events since). No API calls —
a pure BigQuery transform over the Mixpanel dataset.

*** ai_ PREFIX (CRITICAL) ***
Mixpanel project 963821 is shared by Aura (meditation) and Aurie (AI). Subscription events are
prefixed by subscription type (PaymentService src/config.js -> subscriptionTypes.<type>.mixpanelEventPrefix):
Aurie = "AI" -> `ai_subscription_*`; Aura Premium (content) = "" -> unprefixed `subscription_*`
(~300x larger, a DIFFERENT product). This skill is Aurie-only, so it reads ONLY the `ai_`-prefixed
tables. Never point it at the unprefixed `subscription_*` tables.

Env: GCP_KEY (BigQuery + the Mixpanel Nessie dataset). Needs `google-cloud-bigquery`. The SA must be
a viewer on `mixpanel-prod-1.mixpanel_nessie_day_partitioned_963821` (granted via <redacted-email>).
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bq  # noqa: E402  (sibling copy — self-contained skill)
from bq import BQError, BQUnavailable  # noqa: E402

# Aurie subscription events only — see the ai_ PREFIX note in the module docstring.
MIX_DS = "mixpanel-prod-1.mixpanel_nessie_day_partitioned_963821"
PAID = f"{MIX_DS}.ai_subscription_paid"
RENEWED = f"{MIX_DS}.ai_subscription_renewed"
REFUNDED = f"{MIX_DS}.ai_subscription_refunded"

SCHEMA = [
    ("user_id", "STRING"),         # Firebase uid — the join key (= cost table user_id)
    ("event_type", "STRING"),      # paid | renewed | refunded
    ("event_time", "TIMESTAMP"),   # Mixpanel event time
    ("purchase_at", "TIMESTAMP"),  # period start (entitlement start)
    ("expiration_at", "TIMESTAMP"),# period access-end (entitlement end)
    ("revenue", "FLOAT64"),        # USD collected for this period; negative on a refunded row
    ("period", "STRING"),          # RevenueCat period token (P1M / P1Y ...), nullable
    ("package_id", "STRING"),      # catalogue packageId -> tier; NULL on legacy pre-tagging events
    ("product_id", "STRING"),      # store/provider productId; legacy tier fallback via catalogue packagePrices
    ("provider", "STRING"),        # stripe | apple | google ..., from the event
    ("country_code", "STRING"),    # from the event
    ("fetched_at", "TIMESTAMP"),   # build time
]


def periods_cte() -> str:
    """The deduped set of distinct subscription periods (paid ∪ renewed).

    A period is uniquely identified by (user_id, purchase_at, expiration_at): same start + same
    access-end = the same period. We dedup on that key to collapse (a) Mixpanel double-emits within
    a table and (b) the handful of renewed events that echo a paid row — keeping the `paid` label
    and the higher revenue when a key collides. The 37 renewed events with a *distinct* window
    survive as their own periods (genuinely additive later-period revenue).
    """
    return f"""
WITH periods AS (
  SELECT userid AS user_id, 'paid' AS event_type,
         SAFE_CAST(time AS TIMESTAMP) AS event_time,
         SAFE_CAST(purchaseat AS TIMESTAMP) AS purchase_at,
         SAFE_CAST(expirationat AS TIMESTAMP) AS expiration_at,
         SAFE_CAST(revenue AS FLOAT64) AS revenue,
         CAST(period AS STRING) AS period,
         CAST(packageid AS STRING) AS package_id,
         CAST(productid AS STRING) AS product_id,
         CAST(provider AS STRING) AS provider,
         CAST(country_code AS STRING) AS country_code
  FROM `{PAID}` WHERE userid IS NOT NULL
  UNION ALL
  SELECT userid, 'renewed',
         SAFE_CAST(time AS TIMESTAMP),
         SAFE_CAST(purchaseat AS TIMESTAMP),
         SAFE_CAST(expirationat AS TIMESTAMP),
         SAFE_CAST(revenue AS FLOAT64),
         CAST(period AS STRING),
         CAST(packageid AS STRING),
         CAST(productid AS STRING),
         CAST(provider AS STRING),
         CAST(country_code AS STRING)
  FROM `{RENEWED}` WHERE userid IS NOT NULL
),
dedup AS (
  SELECT * EXCEPT(rn) FROM (
    SELECT *, ROW_NUMBER() OVER (
      PARTITION BY user_id, purchase_at, expiration_at
      ORDER BY CASE event_type WHEN 'paid' THEN 0 ELSE 1 END, revenue DESC, event_time
    ) AS rn
    FROM periods
  ) WHERE rn = 1
),
-- One row per refund event, never deduped against the periods above: it carries the refunded
-- period's window, period and product, so amortizing readers net it over that same window. A
-- refund without period dates falls back to its own event time (counted as refunds_dates_inferred).
refunds AS (
  SELECT userid AS user_id, 'refunded' AS event_type,
         SAFE_CAST(time AS TIMESTAMP) AS event_time,
         COALESCE(SAFE_CAST(purchaseat AS TIMESTAMP), SAFE_CAST(time AS TIMESTAMP)) AS purchase_at,
         COALESCE(SAFE_CAST(expirationat AS TIMESTAMP), SAFE_CAST(purchaseat AS TIMESTAMP),
                  SAFE_CAST(time AS TIMESTAMP)) AS expiration_at,
         -ABS(SAFE_CAST(revenue AS FLOAT64)) AS revenue,
         CAST(period AS STRING) AS period,
         CAST(packageid AS STRING) AS package_id,
         CAST(productid AS STRING) AS product_id,
         CAST(provider AS STRING) AS provider,
         CAST(country_code AS STRING) AS country_code,
         purchaseat IS NULL AS dates_inferred
  FROM `{REFUNDED}` WHERE userid IS NOT NULL
),
all_rows AS (
  SELECT * FROM dedup
  UNION ALL
  SELECT * EXCEPT(dates_inferred) FROM refunds
)"""


def build_sql(table: str) -> str:
    return (
        f"CREATE OR REPLACE TABLE `{table}` CLUSTER BY user_id AS "
        + periods_cte()
        + """
SELECT user_id, event_type, event_time, purchase_at, expiration_at, revenue, period,
       package_id, product_id, provider, country_code,
       CURRENT_TIMESTAMP() AS fetched_at
FROM all_rows"""
    )


def current_plan_parts(source: str) -> tuple[str, str]:
    """(CTEs, SELECT) for one row per user: their current subscription, the latest `paid` row.

    revenue = that payment plus every later `renewed` row for the same product_id; plans it
    replaced don't count. A `refunded` row for the same product_id after that payment (no newer
    payment exists, by construction) means the user cancelled with a refund: current_plan_refunded
    and revenue 0. A refund of another product after it is an upgrade credit, not a cancellation.
    A plan that lapsed without a refund keeps its payments. `source` is the subscription table,
    or the dry run's `src` CTE.
    """
    ctes = f"""
latest_paid AS (
  SELECT * EXCEPT(rn) FROM (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY event_time DESC, purchase_at DESC) AS rn
    FROM {source} WHERE event_type = 'paid'
  ) WHERE rn = 1
),
renewals AS (
  SELECT p.user_id, COUNT(*) AS renewal_count, SUM(r.revenue) AS renewed_revenue,
         MAX(r.expiration_at) AS last_expiration
  FROM latest_paid p JOIN {source} r
    ON r.user_id = p.user_id AND r.event_type = 'renewed'
   AND r.product_id = p.product_id AND r.event_time > p.event_time
  GROUP BY p.user_id
),
refunded_after AS (
  SELECT p.user_id
  FROM latest_paid p JOIN {source} r
    ON r.user_id = p.user_id AND r.event_type = 'refunded' AND r.event_time > p.event_time
   AND r.product_id = p.product_id
  GROUP BY p.user_id
)"""
    select = """
SELECT p.user_id, p.product_id, p.package_id, p.period, p.purchase_at,
       GREATEST(p.expiration_at, IFNULL(rn.last_expiration, p.expiration_at)) AS expiration_at,
       p.provider, p.country_code,
       IF(ra.user_id IS NOT NULL, 0, p.revenue + IFNULL(rn.renewed_revenue, 0)) AS revenue,
       p.revenue AS first_payment,
       IFNULL(rn.renewal_count, 0) AS renewal_count,
       GREATEST(p.expiration_at, IFNULL(rn.last_expiration, p.expiration_at)) > p.fetched_at AS is_active,
       CASE LOWER(p.period) WHEN 'year' THEN p.revenue / 12 WHEN 'month' THEN p.revenue END
         AS monthly_price,
       ra.user_id IS NOT NULL AS current_plan_refunded
FROM latest_paid p
LEFT JOIN renewals rn USING (user_id)
LEFT JOIN refunded_after ra USING (user_id)"""
    return ctes, select


def view_sql(table: str, view: str) -> str:
    ctes, select = current_plan_parts(f"`{table}`")
    return f"CREATE OR REPLACE VIEW `{view}` AS\nWITH{ctes}{select}"


def plan_summary_sql(view: str) -> str:
    return f"SELECT{_PLAN_COLS}\nFROM `{view}`"


def dry_plan_sql() -> str:
    """The current-plan summary from source, through the same SELECT the view runs."""
    ctes, select = current_plan_parts("src")
    return (periods_cte() + ",\nsrc AS (SELECT *, CURRENT_TIMESTAMP() AS fetched_at FROM all_rows),"
            + ctes + f",\nplan AS ({select}\n)\nSELECT{_PLAN_COLS}\nFROM plan")


_PLAN_COLS = """
  COUNT(*) AS plan_users,
  COUNTIF(is_active AND NOT current_plan_refunded) AS active_plans,
  COUNTIF(current_plan_refunded) AS refunded_plans,
  COUNTIF(is_active AND current_plan_refunded) AS refunded_unexpired_plans,
  ROUND(SUM(revenue), 2) AS plan_revenue,
  SUM(renewal_count) AS renewals,
  COUNTIF(monthly_price IS NULL) AS unknown_period_plans,
  ROUND(SUM(IF(is_active AND NOT current_plan_refunded, monthly_price, 0)), 2) AS active_monthly_price"""


# Summary aggregation — runs against `dedup` (dry-run, no table) or the built table.
_SUMMARY_COLS = """
  COUNTIF(event_type != 'refunded') AS periods,
  COUNT(DISTINCT IF(event_type != 'refunded', user_id, NULL)) AS users,
  ROUND(SUM(IF(event_type != 'refunded', revenue, 0)), 2) AS gross_revenue,
  COUNTIF(event_type = 'refunded') AS refunds,
  ROUND(SUM(IF(event_type = 'refunded', revenue, 0)), 2) AS refunded_revenue,
  ROUND(SUM(revenue), 2) AS total_revenue,
  COUNTIF(event_type = 'renewed') AS renewed_periods,
  ROUND(SUM(IF(event_type = 'renewed', revenue, 0)), 2) AS renewed_revenue,
  COUNT(DISTINCT IF(event_type != 'refunded' AND expiration_at >= CURRENT_TIMESTAMP(), user_id, NULL))
    AS currently_premium_users,
  MIN(purchase_at) AS earliest_purchase,
  MAX(expiration_at) AS latest_expiration"""


def dry_sql() -> str:
    return periods_cte() + f"\nSELECT{_SUMMARY_COLS}\nFROM all_rows"


def summary_sql(table: str) -> str:
    return f"SELECT{_SUMMARY_COLS}\nFROM `{table}`"


def inferred_sql() -> str:
    """Refunds whose period dates were missing and fell back to the refund time (from source)."""
    return f"SELECT COUNTIF(purchaseat IS NULL) AS n FROM `{REFUNDED}` WHERE userid IS NOT NULL"


def _print_summary(row: dict) -> None:
    print(f"  periods:                 {row['periods']:,}")
    print(f"  users:                   {row['users']:,}")
    print(f"  gross revenue:           ${row['gross_revenue']:,.2f}")
    print(f"  refunds:                 {row['refunds']:,} (${row['refunded_revenue']:,.2f})")
    print(f"  net revenue:             ${row['total_revenue']:,.2f}")
    print(f"  renewed periods:         {row['renewed_periods']:,} (${row['renewed_revenue']:,.2f})")
    print(f"  currently premium users: {row['currently_premium_users']:,}")
    print(f"  earliest purchase:       {row['earliest_purchase']}")
    print(f"  latest expiration:       {row['latest_expiration']}")


def _print_plan(row: dict, renewed_periods: int) -> None:
    print(f"  current plans (users):   {row['plan_users']:,} (users with a paid row)")
    print(f"  current-plan revenue:    ${row['plan_revenue']:,.2f} ({row['renewals']:,} renewals)")
    print(f"  unchained_renewals:      {renewed_periods - row['renewals']:,} "
          "(renewed rows not chained to a user's latest paid row; table only, not the view)")
    print(f"  active plans:            {row['active_plans']:,} "
          f"(${row['active_monthly_price']:,.2f}/month, refunded plans excluded)")
    print(f"  refunded current plans:  {row['refunded_plans']:,} (revenue 0; "
          f"{row['refunded_unexpired_plans']:,} not yet expired)")
    print(f"  unknown-period plans:    {row['unknown_period_plans']:,} (monthly_price NULL)")


def _print_inferred(project: str) -> None:
    n = bq.query(inferred_sql(), project=project)[0]["n"]
    print(f"  refunds_dates_inferred:  {n:,} (no period dates on the event; windowed at the refund time)")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Build the aurie_user_subscription revenue dimension.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Compute + print the summary from source without writing the table")
    ap.add_argument("--project", default=bq.PROJECT)
    ap.add_argument("--dataset", default=bq.DATASET)
    args = ap.parse_args()

    table = f"{args.project}.{args.dataset}.aurie_user_subscription"
    view = f"{args.project}.{args.dataset}.aurie_user_current_plan"

    try:
        if args.dry_run:
            print(f"dry-run — computing the dimension from {PAID.split('.')[-1]} + "
                  f"{RENEWED.split('.')[-1]} + {REFUNDED.split('.')[-1]} (no write)\n")
            rows = bq.query(dry_sql(), project=args.project)
            _print_summary(rows[0])
            _print_inferred(args.project)
            _print_plan(bq.query(dry_plan_sql(), project=args.project)[0], rows[0]['renewed_periods'])
            return 0

        print(f"building {table} (CREATE OR REPLACE) ...")
        bq.query(build_sql(table), project=args.project)
        rows = bq.query(summary_sql(table), project=args.project)
        print("\n=== aurie_user_subscription ===")
        _print_summary(rows[0])
        _print_inferred(args.project)
        print(f"building view {view} (CREATE OR REPLACE) ...")
        bq.query(view_sql(table, view), project=args.project)
        _print_plan(bq.query(plan_summary_sql(view), project=args.project)[0], rows[0]['renewed_periods'])
        return 0
    except BQUnavailable as e:
        print(f"BigQuery unavailable: {e}", file=sys.stderr)
        return 1
    except BQError as e:
        print(f"BigQuery error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
