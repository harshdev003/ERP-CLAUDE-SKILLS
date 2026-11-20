#!/usr/bin/env python3
"""Aurie subscription revenue over time — bookings vs recognized, at daily/weekly/monthly grain.

The revenue counterpart to the cost-only `chart-aurie-*` family. Emits two series from the
`aurie_user_subscription` revenue dimension so revenue can be read on its own (the cost family only
ever shows it paired against cost, in daily-margin / cohort-payback):

  * BOOKINGS — gross cash collected on the purchase day (an annual sub books its full year up front).
  * RECOGNIZED (amortized) — each period's revenue spread evenly across its NOMINAL length from
    `period` (year -> 365, month -> 30; unknown tokens fall back to the raw expiration window,
    floored at 1). Same accrual basis as chart-aurie-cohort-payback: the nominal length spreads the
    ~33 rows with a corrupt expiration (an immediate expire/refund only minutes after purchase) that
    would otherwise dump a full period of revenue onto day 0. Recognized is only summed up to the
    window end (revenue recognized *to date*, never projected forward).

Net revenue = gross x (1 - fee_rate); fee_rate default 0.30 (blended IAP store cut, --fee-rate).

Sources (BigQuery, via GCP_KEY):
  aura-main.aurie_cost.aurie_user_subscription   (user_id, purchase_at, expiration_at, revenue, period)
  aura-main.aurie_cost.aurie_user_env            (prod filter; deduped to latest row per user)

Prerequisite: `aurie_user_subscription` must be current — build it with
`eng:build-aurie-user-subscription` before running. This is **enforced**, not advisory:
the script calls `bq.assert_fresh` and exits 2 if the dimension is over 36h old, because a
stale rebuild silently understates revenue rather than failing. `--allow-stale` overrides.

Aggregate-only: every output row is a bucket total. Never emits a UID.
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

GRAINS = ("daily", "weekly", "monthly")


def _trunc(grain: str, col: str) -> str:
    """The bucket expression for `grain` applied to DATE column `col`. `grain` is whitelisted
    (argparse choices) and `col` is a literal, so the result is safe to interpolate into SQL."""
    return {
        "daily": col,
        "weekly": f"DATE_TRUNC({col}, WEEK(MONDAY))",
        "monthly": f"DATE_TRUNC({col}, MONTH)",
    }[grain]

# The deduped prod-user set (PROD-3589 Part B): revenue restricted to the same env='prod' population
# the cost aggregates use, so dev/QA test purchases don't inflate the trend. Skipped with --all-env.
_PROD_CTE = f"""
prod_users AS (
  SELECT user_id FROM (
    SELECT user_id, env, ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY fetched_at DESC) rn
    FROM `{ENV}`
  ) WHERE rn = 1 AND env = 'prod'
)"""


def _nominal_days_case() -> str:
    return ("CASE LOWER(period) WHEN 'year' THEN 365 WHEN 'month' THEN 30 "
            "ELSE GREATEST(DATE_DIFF(DATE(expiration_at), DATE(purchase_at), DAY), 1) END")


def _sub_join(all_env: bool) -> str:
    return "" if all_env else "JOIN prod_users USING (user_id)"


def bookings_sql(grain: str, all_env: bool) -> str:
    trunc = _trunc(grain, "pd")
    lead = f"WITH {_PROD_CTE},\n" if not all_env else "WITH "
    return f"""{lead}base AS (
  SELECT user_id, DATE(purchase_at) AS pd, revenue, event_type
  FROM `{SUB}` {_sub_join(all_env)}
)
SELECT CAST({trunc} AS STRING) AS bucket,
       ROUND(SUM(revenue), 2) AS gross,
       COUNTIF(event_type != 'refunded') AS periods,
       COUNT(DISTINCT user_id) AS paying_users
FROM base
WHERE pd BETWEEN @start AND @end
GROUP BY bucket
ORDER BY bucket"""


def recognized_sql(grain: str, all_env: bool) -> str:
    trunc = _trunc(grain, "d")
    lead = f"{_PROD_CTE},\n" if not all_env else ""
    return f"""WITH {lead}periods AS (
  SELECT user_id, DATE(purchase_at) AS p_start,
         DATE_ADD(DATE(purchase_at), INTERVAL nd - 1 DAY) AS p_end,
         revenue / nd AS daily_rate
  FROM (
    SELECT user_id, purchase_at, expiration_at, revenue, {_nominal_days_case()} AS nd
    FROM `{SUB}` {_sub_join(all_env)}
  )
),
spine AS (SELECT d FROM UNNEST(GENERATE_DATE_ARRAY(@start, @end)) AS d),
per_day AS (
  SELECT s.d, p.user_id, p.daily_rate
  FROM spine s JOIN periods p ON s.d BETWEEN p.p_start AND p.p_end
)
SELECT CAST({trunc} AS STRING) AS bucket,
       ROUND(SUM(daily_rate), 2) AS gross,
       COUNT(DISTINCT user_id) AS active_subscribers
FROM per_day
GROUP BY bucket
ORDER BY bucket"""


def _iso(s: str) -> str:
    dt.date.fromisoformat(s)  # validate; raises ValueError on bad input
    return s


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
    ap = argparse.ArgumentParser(description="Aurie subscription revenue over time -> JSON.")
    ap.add_argument("--grain", choices=list(GRAINS), default="weekly",
                    help="Bucket grain (default weekly)")
    ap.add_argument("--daily", dest="grain", action="store_const", const="daily",
                    help="Shortcut for --grain daily")
    ap.add_argument("--weekly", dest="grain", action="store_const", const="weekly",
                    help="Shortcut for --grain weekly")
    ap.add_argument("--monthly", dest="grain", action="store_const", const="monthly",
                    help="Shortcut for --grain monthly")
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
        params = {"start": start, "end": end}
        bookings = bq.query(bookings_sql(args.grain, args.all_env), params=params)
        recognized = bq.query(recognized_sql(args.grain, args.all_env), params=params)
    except BQUnavailable as e:
        print(f"BigQuery unavailable: {e}", file=sys.stderr)
        return 1
    except BQError as e:
        print(f"BigQuery error: {e}", file=sys.stderr)
        return 1

    net = 1.0 - args.fee_rate
    for r in bookings:
        r["net"] = round(r["gross"] * net, 2)
    for r in recognized:
        r["net"] = round(r["gross"] * net, 2)
        r["arpu_net"] = round(r["net"] / r["active_subscribers"], 2) if r["active_subscribers"] else None

    out = {
        "window": {"start": start, "end": end},
        "grain": args.grain,
        "fee_rate": args.fee_rate,
        "env": "all" if args.all_env else "prod",
        "revenue_basis": {
            "bookings": "gross cash collected on the purchase day",
            "recognized": "amortized: period revenue spread evenly over its nominal length "
                          "(year->365, month->30), summed to date",
        },
        "totals": {
            "bookings_gross": round(sum(r["gross"] for r in bookings), 2),
            "bookings_net": round(sum(r["net"] for r in bookings), 2),
            "recognized_gross": round(sum(r["gross"] for r in recognized), 2),
            "recognized_net": round(sum(r["net"] for r in recognized), 2),
        },
        "bookings": bookings,
        "recognized": recognized,
    }
    out["skill_version"] = _skill_version()
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
