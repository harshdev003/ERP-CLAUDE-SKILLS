#!/usr/bin/env python3
"""Aurie subscription revenue split by TIER (Lite/Plus/Growth/Flourish/...) and BILLING PERIOD
(monthly vs annual).

Cross-tabs the `aurie_user_subscription` revenue dimension by the plan that produced each period's
revenue. Each subscription period is attributed to ITS OWN tier + billing period (not the user's
latest) — the right basis for "how much did each plan bring in". Two revenue bases, same as
eng:chart-aurie-revenue:

  * BOOKINGS — gross cash collected on the purchase day (annual books its full year up front).
  * RECOGNIZED (amortized) — each period's revenue spread evenly across its NOMINAL length
    (year -> 365, month -> 30; unknown tokens fall back to the raw expiration window, floored at 1),
    summed over its overlap with the window (recognized to date).

Net revenue = gross x (1 - fee_rate); fee_rate default 0.30 (blended IAP store cut, --fee-rate).

Tier resolution (source of truth: eng:manage-ai-packages fetch_catalogue.py; keep the map in sync):
  package_id STARTS_WITH  b7b32619 -> Aurie Lite   | 9428b0ed -> Aurie Plus | f727ce9a -> Aurie Growth
                          4044faaf -> Aurie Flourish| 295a1ed6 -> Aurie Unlimited-Internal
  legacy (package_id NULL): product_id ai_monthly_1999 / ai_yearly_6999 -> Plus (legacy); else
  Plus (legacy/unmapped). `package_id` is the FULL catalogue UUID — the 8-hex tokens are its leading
  segment, matched with STARTS_WITH.

Sources (BigQuery, via GCP_KEY):
  aura-main.aurie_cost.aurie_user_subscription   (user_id, purchase_at, expiration_at, revenue, period,
                                                  package_id, product_id)
  aura-main.aurie_cost.aurie_user_env            (prod filter; deduped to latest row per user)

Prerequisite: `aurie_user_subscription` must be current with package_id/product_id populated — build
it with `eng:build-aurie-user-subscription`. This is **enforced**, not advisory: the script calls
`bq.assert_fresh` and exits 2 if the dimension is over 36h old, because a stale rebuild silently
understates revenue rather than failing. `--allow-stale` overrides.

Aggregate-only: every output row is a tier x billing total. Never emits a UID.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bq  # noqa: E402  (sibling copy — self-contained skill)
from bq import BQError, BQStale, BQUnavailable  # noqa: E402

SUB = f"{bq.PROJECT}.{bq.DATASET}.aurie_user_subscription"
REBUILD_HINT = ("eng:build-aurie-user-subscription — "
                "python3 plugins/eng/skills/build-aurie-user-subscription/scripts/subscription.py")
ENV = f"{bq.PROJECT}.{bq.DATASET}.aurie_user_env"

# packageId -> tier. Source of truth: eng:manage-ai-packages fetch_catalogue.py. Mirrors the
# _TIER_CASE resolver in eng:report-aurie-margin-users (no 'free/none' branch here — every
# subscription period carries a package_id or product_id). Refresh both if the catalogue changes.
_TIER_CASE = """
CASE
  WHEN STARTS_WITH(package_id, 'b7b32619') THEN 'Aurie Lite'
  WHEN STARTS_WITH(package_id, '9428b0ed') THEN 'Aurie Plus'
  WHEN STARTS_WITH(package_id, 'f727ce9a') THEN 'Aurie Growth'
  WHEN STARTS_WITH(package_id, '4044faaf') THEN 'Aurie Flourish'
  WHEN STARTS_WITH(package_id, '295a1ed6') THEN 'Aurie Unlimited-Internal'
  WHEN product_id IN ('ai_monthly_1999', 'ai_yearly_6999') THEN 'Plus (legacy)'
  ELSE 'Plus (legacy/unmapped)'
END"""

_NOMINAL_DAYS = ("CASE LOWER(period) WHEN 'year' THEN 365 WHEN 'month' THEN 30 "
                 "ELSE GREATEST(DATE_DIFF(DATE(expiration_at), DATE(purchase_at), DAY), 1) END")

_BILLING_CASE = ("CASE LOWER(period) WHEN 'year' THEN 'annual' WHEN 'month' THEN 'monthly' "
                 "ELSE 'other' END")

_PROD_CTE = f"""
prod_users AS (
  SELECT user_id FROM (
    SELECT user_id, env, ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY fetched_at DESC) rn
    FROM `{ENV}`
  ) WHERE rn = 1 AND env = 'prod'
)"""


def by_tier_sql(all_env: bool) -> str:
    join = "" if all_env else "JOIN prod_users USING (user_id)"
    lead = "WITH " if all_env else f"WITH {_PROD_CTE},\n"
    return f"""{lead}periods AS (
  SELECT user_id,
         {_TIER_CASE} AS tier,
         {_BILLING_CASE} AS billing,
         DATE(purchase_at) AS pd,
         DATE(purchase_at) AS p_start,
         DATE_ADD(DATE(purchase_at), INTERVAL nd - 1 DAY) AS p_end,
         revenue,
         event_type,
         revenue / nd AS daily_rate
  FROM (
    SELECT user_id, purchase_at, expiration_at, revenue, period, package_id, product_id, event_type,
           {_NOMINAL_DAYS} AS nd
    FROM `{SUB}` {join}
  )
)
SELECT tier, billing,
       ROUND(SUM(IF(pd BETWEEN @start AND @end, revenue, 0)), 2) AS bookings_gross,
       COUNTIF(pd BETWEEN @start AND @end AND event_type != 'refunded') AS bookings_periods,
       COUNT(DISTINCT IF(pd BETWEEN @start AND @end, user_id, NULL)) AS bookings_users,
       ROUND(SUM(daily_rate * GREATEST(
         0, DATE_DIFF(LEAST(p_end, @end), GREATEST(p_start, @start), DAY) + 1)), 2) AS recognized_gross,
       COUNT(DISTINCT IF(p_start <= @end AND p_end >= @end, user_id, NULL)) AS active_subscribers
FROM periods
GROUP BY tier, billing
ORDER BY tier, billing"""


def _iso(s: str) -> str:
    dt.date.fromisoformat(s)  # validate; raises ValueError on bad input
    return s


def _subtotals(rows: list, key: str) -> list:
    agg: dict = {}
    for r in rows:
        a = agg.setdefault(r[key], {key: r[key], "bookings_gross": 0.0, "bookings_net": 0.0,
                                    "recognized_gross": 0.0, "recognized_net": 0.0,
                                    "bookings_periods": 0, "active_subscribers": 0})
        for f in ("bookings_gross", "bookings_net", "recognized_gross", "recognized_net"):
            a[f] = round(a[f] + r[f], 2)
        a["bookings_periods"] += r["bookings_periods"]
        a["active_subscribers"] += r["active_subscribers"]
    return list(agg.values())


def _skill_version():
    """This skill's metadata.version (reads the sibling SKILL.md) for output provenance."""
    import re
    try:
        _t = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "SKILL.md"),
                  encoding="utf-8").read()
    except OSError:
        return None
    _fm = re.match(r"^---\n(.*?)\n---", _t, re.DOTALL)
    _fm = _fm.group(1) if _fm else _t
    _b = re.search(r"(?m)^metadata:[ \t]*\n((?:[ \t]+\S.*\n?)+)", _fm)
    if not _b:
        return None
    _v = re.search(r'''(?m)^[ \t]+version:[ \t]*["\']?([^"\'\s]+)["\']?[ \t]*$''', _b.group(1))
    return _v.group(1) if _v else None


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(
        description="Aurie subscription revenue by tier x billing period -> JSON.")
    ap.add_argument("--since", type=_iso, help="Window start YYYY-MM-DD (default: first purchase)")
    ap.add_argument("--until", type=_iso, help="Window end YYYY-MM-DD (default: today, UTC)")
    ap.add_argument("--fee-rate", type=float, default=0.30,
                    help="Store/processor fee fraction for net = gross x (1 - fee_rate) (default 0.30)")
    ap.add_argument("--all-env", action="store_true",
                    help="Include all users (skip the env='prod' filter)")
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

    if not 0.0 <= args.fee_rate < 1.0:
        print("--fee-rate must be in [0, 1)", file=sys.stderr)
        return 2

    try:
        if not bq.table_exists(SUB):
            print(f"Missing {SUB} — build it first with eng:build-aurie-user-subscription.",
                  file=sys.stderr)
            return 1
        span = bq.query(
            f"SELECT CAST(MIN(DATE(purchase_at)) AS STRING) AS start, "
            f"CAST(CURRENT_DATE() AS STRING) AS today FROM `{SUB}`"
        )[0]
        start = args.since or span["start"]
        end = args.until or span["today"]
        if start > end:
            print(f"--since ({start}) is after --until ({end})", file=sys.stderr)
            return 2
        rows = bq.query(by_tier_sql(args.all_env), params={"start": start, "end": end})
    except BQUnavailable as e:
        print(f"BigQuery unavailable: {e}", file=sys.stderr)
        return 1
    except BQError as e:
        print(f"BigQuery error: {e}", file=sys.stderr)
        return 1

    net = 1.0 - args.fee_rate
    for r in rows:
        r["bookings_net"] = round(r["bookings_gross"] * net, 2)
        r["recognized_net"] = round(r["recognized_gross"] * net, 2)

    out = {
        "window": {"start": start, "end": end},
        "fee_rate": args.fee_rate,
        "env": "all" if args.all_env else "prod",
        "revenue_basis": {
            "bookings": "gross cash collected on the purchase day",
            "recognized": "amortized: period revenue spread over its nominal length, summed over "
                          "the period's overlap with the window",
        },
        "totals": {
            "bookings_gross": round(sum(r["bookings_gross"] for r in rows), 2),
            "bookings_net": round(sum(r["bookings_net"] for r in rows), 2),
            "recognized_gross": round(sum(r["recognized_gross"] for r in rows), 2),
            "recognized_net": round(sum(r["recognized_net"] for r in rows), 2),
        },
        "by_tier": _subtotals(rows, "tier"),
        "by_billing": _subtotals(rows, "billing"),
        "rows": rows,
    }
    out["skill_version"] = _skill_version()
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
