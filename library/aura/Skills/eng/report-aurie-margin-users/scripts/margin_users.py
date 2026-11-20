#!/usr/bin/env python3
"""Per-user Aurie contribution margin: who are we losing money on?

INTERNAL / UID-BEARING. This script prints Firebase UIDs to stdout / a local CSV.
Never pipe this output into the Artifact tool and never post it to Slack without an
explicit user ask — see the SKILL.md Privacy section.

Same revenue + amortization definitions as the daily companion `eng:chart-aurie-daily-margin`
(`plugins/eng/skills/chart-aurie-daily-margin/scripts/margin.py`), narrowed to the per-user
grain instead of the daily grain:

  amortized_net_revenue = SUM(amortized gross revenue active in window) * (1 - fee_rate)
  cost                  = SUM(total_cost) from aurie_per_user_daily_cost (user_id != 'Other')
  net                   = amortized_net_revenue - cost

Amortization — nominal period length, half-open [purchase_at, purchase_at + period_days):
  period_days = 365 (year) / 30 (month) / GREATEST(TIMESTAMP_DIFF(expiration_at,
  purchase_at, DAY), 1) as a fallback for null/other period tokens.

Tier is resolved from the user's MOST RECENT subscription period's `package_id` (falling back
to `product_id` for legacy pre-tagging periods, per the map below). A user with cost but no
subscription-period row at all is tier 'free/none'.

  packageId -> tier (source of truth: eng:manage-ai-packages fetch_catalogue.py; refresh this
  map if tiers change):
    b7b32619 -> Aurie Lite
    9428b0ed -> Aurie Plus
    f727ce9a -> Aurie Growth
    4044faaf -> Aurie Flourish
    295a1ed6 -> Aurie Unlimited-Internal
  Legacy fallback (package_id IS NULL), by product_id:
    ai_monthly_1999, ai_yearly_6999 -> Plus (legacy)
    anything else unmapped         -> Plus (legacy/unmapped)

"Paying" (for the actionable section) means the user has at least one row in
aurie_user_subscription — i.e. tier != 'free/none' — not necessarily subscription_state =
'paid' today (a lapsed/cancelled paying user we're still serving cost against is exactly the
segment ops wants to see).

Sources (BigQuery, via GCP_KEY):
  aura-main.aurie_cost.aurie_per_user_daily_cost   (date, user_id, total_cost, subscription_state)
  aura-main.aurie_cost.aurie_user_subscription     (user_id, purchase_at, expiration_at, revenue,
                                                     period, package_id, product_id)
"""
from __future__ import annotations

import argparse
import csv
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bq  # noqa: E402  (sibling copy — self-contained skill)
from bq import BQError, BQStale, BQUnavailable  # noqa: E402

COST = f"{bq.PROJECT}.{bq.DATASET}.aurie_per_user_daily_cost"
SUB = f"{bq.PROJECT}.{bq.DATASET}.aurie_user_subscription"
REBUILD_HINT = ("eng:build-aurie-user-subscription — "
                "python3 plugins/eng/skills/build-aurie-user-subscription/scripts/subscription.py")

DEFAULT_FEE_RATE = 0.30
DEFAULT_TOP = 25

# Same nominal-period amortization CTEs as chart-aurie-daily-margin's margin.py — keep these
# two skills' revenue definition in lockstep. `rev_ud` is the (user_id, date, gross) grain.
_AMORT_CTES = f"""
periods AS (
  SELECT user_id, DATE(purchase_at) AS p_start, revenue,
         CASE LOWER(period)
           WHEN 'year' THEN 365
           WHEN 'month' THEN 30
           ELSE GREATEST(TIMESTAMP_DIFF(expiration_at, purchase_at, DAY), 1)
         END AS period_days
  FROM `{SUB}`
),
sub AS (
  SELECT user_id, p_start,
         DATE_ADD(p_start, INTERVAL CAST(period_days AS INT64) DAY) AS p_end_excl,
         revenue / period_days AS daily_gross
  FROM periods
),
rev_ud AS (
  SELECT s.user_id, sp.d, s.daily_gross AS gross
  FROM spine sp
  JOIN sub s ON sp.d >= s.p_start AND sp.d < s.p_end_excl
)"""

# packageId -> tier. Source of truth: eng:manage-ai-packages fetch_catalogue.py. Refresh this
# map (and the legacy product_id fallback below) if the catalogue's tiers change.
# NOTE: `package_id` in aurie_user_subscription is the FULL catalogue UUID (e.g.
# `9428b0ed-4106-4460-a75e-00adcf85cae2`) — the 8-hex-char tokens below are just its leading
# segment, matched with STARTS_WITH (confirmed against live data: every observed package_id
# begins with one of these five segments).
_TIER_CASE = """
CASE
  WHEN lp.user_id IS NULL THEN 'free/none'
  WHEN STARTS_WITH(lp.package_id, 'b7b32619') THEN 'Aurie Lite'
  WHEN STARTS_WITH(lp.package_id, '9428b0ed') THEN 'Aurie Plus'
  WHEN STARTS_WITH(lp.package_id, 'f727ce9a') THEN 'Aurie Growth'
  WHEN STARTS_WITH(lp.package_id, '4044faaf') THEN 'Aurie Flourish'
  WHEN STARTS_WITH(lp.package_id, '295a1ed6') THEN 'Aurie Unlimited-Internal'
  WHEN lp.product_id IN ('ai_monthly_1999', 'ai_yearly_6999') THEN 'Plus (legacy)'
  ELSE 'Plus (legacy/unmapped)'
END"""


def _spine_cte(win: str) -> str:
    where = f" WHERE 1=1{win}" if win else ""
    return f"spine AS (SELECT DISTINCT date AS d FROM `{COST}`{where})"


def _win(args) -> str:
    """WHERE fragment (leading ' AND', empty = all days) selecting the daily-cost date spine.
    `--last N` = the last N days PRESENT in the store (matches the sibling cost charts)."""
    if args.all:
        return ""
    n = args.last if args.last is not None else 30
    return (f" AND date >= (SELECT MIN(d) FROM "
            f"(SELECT DISTINCT date AS d FROM `{COST}` ORDER BY d DESC LIMIT {n}))")


def _ranked_cte(win: str) -> str:
    """`ranked`: one row per real user (Other excluded) with amortized net revenue, cost, net,
    latest subscription_state seen in the window, and tier resolved from their most recent
    subscription period (regardless of window — tier is a point-in-time-of-lookup fact, not a
    windowed aggregate)."""
    return f"""
{_spine_cte(win)},
{_AMORT_CTES},
user_rev AS (SELECT user_id, SUM(gross) AS gross_rev FROM rev_ud GROUP BY user_id),
user_cost_raw AS (
  SELECT c.*, ROW_NUMBER() OVER (PARTITION BY c.user_id ORDER BY c.date DESC) AS rn
  FROM `{COST}` c
  JOIN spine sp ON sp.d = c.date
  WHERE c.user_id != 'Other'
),
user_cost AS (SELECT user_id, SUM(total_cost) AS cost FROM user_cost_raw GROUP BY user_id),
latest_state AS (SELECT user_id, subscription_state FROM user_cost_raw WHERE rn = 1),
latest_period AS (
  SELECT user_id, package_id, product_id FROM (
    SELECT user_id, package_id, product_id,
           ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY purchase_at DESC) AS rn
    FROM `{SUB}`
  ) WHERE rn = 1
),
combined AS (
  SELECT COALESCE(uc.user_id, ur.user_id) AS user_id,
         IFNULL(ur.gross_rev, 0) AS gross_rev,
         IFNULL(uc.cost, 0) AS cost
  FROM user_cost uc FULL OUTER JOIN user_rev ur ON uc.user_id = ur.user_id
),
ranked AS (
  SELECT c.user_id,
         ROUND(c.gross_rev * (1 - @fee_rate), 4) AS amortized_net_revenue,
         ROUND(c.cost, 4) AS cost,
         ROUND(c.gross_rev * (1 - @fee_rate) - c.cost, 4) AS net,
         ls.subscription_state AS subscription_state,
         {_TIER_CASE} AS tier,
         lp.user_id IS NOT NULL AS has_subscription
  FROM combined c
  LEFT JOIN latest_state ls ON ls.user_id = c.user_id
  LEFT JOIN latest_period lp ON lp.user_id = c.user_id
)"""


def _stats_sql(win: str) -> str:
    return f"""
WITH {_ranked_cte(win)}
SELECT COUNT(*) AS total_users, COUNTIF(net < 0) AS negative_users,
       ROUND(SUM(IF(net < 0, -net, 0)), 4) AS total_loss
FROM ranked
"""


def _top_paying_unprofitable_sql(win: str, top_n: int) -> str:
    return f"""
WITH {_ranked_cte(win)}
SELECT user_id, tier, subscription_state, amortized_net_revenue, cost, net
FROM ranked
WHERE net < 0 AND has_subscription
ORDER BY net ASC
LIMIT {top_n}
"""


def _top_overall_sql(win: str, top_n: int) -> str:
    return f"""
WITH {_ranked_cte(win)}
SELECT user_id, tier, subscription_state, amortized_net_revenue, cost, net
FROM ranked
ORDER BY net ASC
LIMIT {top_n}
"""


def _fmt_money(v) -> str:
    v = v or 0.0
    sign = "-" if v < 0 else ""
    return f"{sign}${abs(v):,.2f}"


def _print_table(title: str, rows: list) -> None:
    print(f"\n=== {title} ===")
    if not rows:
        print("  (none)")
        return
    cols = [
        ("user_id", "user_id", 28),
        ("tier", "tier", 24),
        ("subscription_state", "state", 12),
        ("amortized_net_revenue", "net_revenue", 14),
        ("cost", "cost", 14),
        ("net", "net", 14),
    ]
    rendered = []
    for r in rows:
        rendered.append({
            "user_id": r["user_id"] or "",
            "tier": r["tier"] or "",
            "subscription_state": r["subscription_state"] or "n/a",
            "amortized_net_revenue": _fmt_money(r["amortized_net_revenue"]),
            "cost": _fmt_money(r["cost"]),
            "net": _fmt_money(r["net"]),
        })
    widths = {key: max(len(header), max(len(row[key]) for row in rendered))
              for key, header, _min in cols}
    header_line = "  ".join(header.ljust(widths[key]) for key, header, _min in cols)
    print(header_line)
    print("-" * len(header_line))
    for row in rendered:
        print("  ".join(row[key].ljust(widths[key]) for key, _h, _m in cols))


def _write_csv(path: str, rows: list) -> None:
    fields = ["user_id", "tier", "subscription_state", "amortized_net_revenue", "cost", "net"]
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k) for k in fields})


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(
        description="Per-user Aurie contribution margin ranking (INTERNAL, UID-bearing)."
    )
    ap.add_argument("--all", action="store_true", default=True,
                    help="every day in the store (default)")
    ap.add_argument("--last", type=int, default=None,
                    help="most recent N days present in the store (overrides --all)")
    ap.add_argument("--fee-rate", type=float, default=DEFAULT_FEE_RATE,
                    help=f"blended IAP store-fee rate applied to amortized gross revenue "
                         f"(default {DEFAULT_FEE_RATE})")
    ap.add_argument("--top", type=int, default=DEFAULT_TOP,
                    help=f"rows per section (default {DEFAULT_TOP})")
    ap.add_argument("--csv", dest="csv_path", default=None,
                    help="also write the combined (paying-unprofitable + overall) rows to this "
                         "CSV path. INTERNAL/UID-bearing — treat the file like the stdout output.")
    ap.add_argument("--allow-stale", action="store_true",
                    help="proceed even if the revenue dimension is stale "
                         "(revenue will be understated — see REBUILD_HINT)")
    args = ap.parse_args()

    # The revenue dimension is rebuilt wholesale, not appended per day, so a stale
    # copy still answers every query — just with recent periods missing, silently
    # understating revenue. Fail closed; --allow-stale is the deliberate override.
    try:
        bq.assert_fresh(SUB, rebuild_hint=REBUILD_HINT)
    except BQStale as e:
        if not args.allow_stale:
            print(f"stale revenue dimension: {e}", file=sys.stderr)
            print("pass --allow-stale to proceed anyway (revenue will be understated).",
                  file=sys.stderr)
            return 2
        print(f"WARNING: proceeding on a stale revenue dimension: {e}", file=sys.stderr)
    except BQUnavailable as e:
        print(f"BigQuery unavailable: {e}", file=sys.stderr)
        return 1
    except BQError as e:
        print(f"BigQuery error: {e}", file=sys.stderr)
        return 1
    if args.last is not None:
        args.all = False

    win = _win(args)
    params = {"fee_rate": args.fee_rate}
    try:
        stats = bq.query(_stats_sql(win), params=params)[0]
        top_paying = bq.query(_top_paying_unprofitable_sql(win, args.top), params=params)
        top_overall = bq.query(_top_overall_sql(win, args.top), params=params)
    except BQUnavailable as e:
        print(f"BigQuery unavailable: {e}", file=sys.stderr)
        return 1
    except BQError as e:
        print(f"BigQuery error: {e}", file=sys.stderr)
        return 1

    window_label = f"last {args.last} days present" if args.last else "all days present"
    print("Aurie per-user margin report (INTERNAL, UID-bearing — do not publish or post)")
    print(f"window: {window_label}  |  fee rate: {args.fee_rate:.0%}  |  "
          f"generated: {datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')}")

    _print_table(f"Top {args.top} paying-but-unprofitable users "
                 f"(has a subscription AND net < 0)", top_paying)
    _print_table(f"Top {args.top} most-negative users overall (incl. free)", top_overall)

    total_loss = stats["total_loss"] or 0.0  # SUM(IF(net<0,-net,0)) is a positive loss magnitude
    print(f"\n{stats['negative_users']} of {stats['total_users']} users are net-negative "
          f"in this window; total net loss ${total_loss:,.2f}")

    if args.csv_path:
        combined = top_paying + [r for r in top_overall
                                  if r["user_id"] not in {p["user_id"] for p in top_paying}]
        _write_csv(args.csv_path, combined)
        print(f"\nWrote {args.csv_path} ({len(combined)} rows)", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
