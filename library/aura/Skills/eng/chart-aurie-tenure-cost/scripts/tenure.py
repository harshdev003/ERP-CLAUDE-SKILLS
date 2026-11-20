#!/usr/bin/env python3
"""Split each day's Aurie cost into NEW (first 7 days since AI signup) vs ESTABLISHED users.

Answers the #ai concern live on the daily view: new users cost the most in their first week,
so a new-subscriber surge inflates the headline daily cost even when per-user cost is falling.
This makes that split visible day-to-day (the cohort skill shows it by join-week; this shows it
on the calendar).

A user is **new** on a cost day `d` if `0 <= d - ai_created_at <= 6` days, **established** if
`>= 7`. Cost that can't be aged — the `Other` unattributed row, users missing from the
ai_created_at dimension, or a cost row dated before the user's resolved signup — is bucketed as
**other** so the three buckets always reconcile to the day's real total.

Sources (BigQuery, via GCP_KEY):
  aura-main.aurie_cost.aurie_per_user_daily_cost_prod   (env='prod' view; date, user_id, total_cost)
  aura-main.aurie_cost.aurie_user_ai_created       (user_id -> ai_created_at; deduped on read)

Coverage guard: an accurate split needs a complete ai_created_at dimension. This script reports
how many cost-table users are missing from it; run `eng:backfill-aurie-user-ai-created` first if
`missing` is non-zero (missing users fall into `other`, understating new/established).
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bq  # noqa: E402  (sibling copy — self-contained skill)
from bq import BQError, BQUnavailable  # noqa: E402

COST = f"{bq.PROJECT}.{bq.DATASET}.aurie_per_user_daily_cost_prod"  # PROD-3589 Part B: env='prod' only
DIM = f"{bq.PROJECT}.{bq.DATASET}.aurie_user_ai_created"

NEW_WINDOW_DAYS = 7  # "new" = a user's first 7 days (day-diff 0..6)

# Deduped ai_created_at dimension (append-only table can hold a rare duplicate).
_DIM_CTE = f"""
dim AS (
  SELECT user_id, DATE(ai_created_at) AS created FROM (
    SELECT user_id, ai_created_at,
           ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY fetched_at DESC) AS rn
    FROM `{DIM}`
  ) WHERE rn = 1
)
"""

# A cost row is "new" when the user is in their first 7 days, "established" at >= 7 days.
# Anything unattributed / unknown-age / pre-signup is neither (bucketed as `other`).
_IS_NEW = f"c.user_id != 'Other' AND dim.created IS NOT NULL AND DATE_DIFF(c.d, dim.created, DAY) BETWEEN 0 AND {NEW_WINDOW_DAYS - 1}"
_IS_EST = "c.user_id != 'Other' AND dim.created IS NOT NULL AND DATE_DIFF(c.d, dim.created, DAY) >= 7"

_SERIES_SQL = f"""
WITH {_DIM_CTE},
cost AS (SELECT date AS d, user_id, total_cost FROM `{COST}`)
SELECT CAST(c.d AS STRING) AS date,
  ROUND(SUM(IF({_IS_NEW}, c.total_cost, 0)), 2) AS new_cost,
  ROUND(SUM(IF({_IS_EST}, c.total_cost, 0)), 2) AS established_cost,
  ROUND(SUM(IF(NOT ({_IS_NEW}) AND NOT ({_IS_EST}), c.total_cost, 0)), 2) AS other_cost,
  COUNT(DISTINCT IF({_IS_NEW}, c.user_id, NULL)) AS new_users,
  COUNT(DISTINCT IF({_IS_EST}, c.user_id, NULL)) AS established_users,
  ROUND(SUM(c.total_cost), 2) AS total_cost
FROM cost c LEFT JOIN dim USING(user_id)
GROUP BY c.d
ORDER BY c.d
"""


def _window_clause(args) -> str:
    if args.all:
        return ""
    if args.date_from or args.date_to:
        parts = []
        if args.date_from:
            parts.append(f"c.d >= DATE('{args.date_from}')")
        if args.date_to:
            parts.append(f"c.d <= DATE('{args.date_to}')")
        return "WHERE " + " AND ".join(parts)
    # "last N days present in the store" (NOT last N calendar days) — matches
    # eng:chart-aurie-daily-cost's select_window, so the two skills agree on gap days.
    n = args.last if args.last is not None else 30
    return (f"WHERE c.d >= (SELECT MIN(d) FROM "
            f"(SELECT DISTINCT date AS d FROM `{COST}` ORDER BY d DESC LIMIT {n}))")


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
    ap = argparse.ArgumentParser(description="Daily Aurie cost split: new vs established users -> JSON.")
    ap.add_argument("--last", type=int, default=None, help="most recent N days (default 30)")
    ap.add_argument("--from", dest="date_from", help="window start, inclusive (YYYY-MM-DD)")
    ap.add_argument("--to", dest="date_to", help="window end, inclusive (YYYY-MM-DD)")
    ap.add_argument("--all", action="store_true", help="every day in the store")
    args = ap.parse_args()

    try:
        meta = bq.query(
            f"SELECT CAST(MIN(date) AS STRING) AS start, CAST(MAX(date) AS STRING) AS `end` "
            f"FROM `{COST}`"
        )[0]
        max_date = meta["end"]

        # Inject the window into the series query (it wraps the aggregate SELECT's FROM).
        window = _window_clause(args)
        sql = _SERIES_SQL.replace("GROUP BY c.d", f"{window}\nGROUP BY c.d") if window else _SERIES_SQL
        rows = bq.query(sql)

        cov = bq.query(f"""
          WITH {_DIM_CTE},
          cost AS (SELECT DISTINCT user_id FROM `{COST}` WHERE user_id != 'Other')
          SELECT COUNT(*) AS cost_users, COUNTIF(dim.user_id IS NOT NULL) AS with_ai_created
          FROM cost LEFT JOIN dim USING(user_id)
        """)[0]
        missing = cov["cost_users"] - cov["with_ai_created"]
    except BQUnavailable as e:
        print(f"BigQuery unavailable: {e}", file=sys.stderr)
        return 1
    except BQError as e:
        print(f"BigQuery error: {e}", file=sys.stderr)
        return 1

    out = {
        "cost_data_start": meta["start"],
        "cost_data_end": max_date,
        "new_window_days": NEW_WINDOW_DAYS,
        "coverage": {"cost_users": cov["cost_users"],
                     "with_ai_created": cov["with_ai_created"], "missing": missing},
        "days": rows,
    }
    out["skill_version"] = _skill_version()
    print(json.dumps(out, indent=2))
    if missing:
        print(f"\nWARNING: {missing} cost-table users are missing ai_created_at — their cost "
              f"falls into `other`, understating the new/established split. Run "
              f"eng:backfill-aurie-user-ai-created first.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
