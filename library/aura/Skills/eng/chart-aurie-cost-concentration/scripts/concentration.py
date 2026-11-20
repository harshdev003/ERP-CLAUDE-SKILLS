#!/usr/bin/env python3
"""Aurie cost concentration — Pareto / Lorenz of per-user cost over a window.

Answers "what share of cost comes from the top 1% / 5% / 10% of users?" — the expensive tail to
target for optimization or caps. Ranks users by their total cost over the window and emits the
concentration curve (cumulative cost share vs top-user percentile) plus top-N-share cards and a
Gini coefficient.

Aggregate percentiles only — the per-user cost is computed and ranked in BigQuery; the output
carries bucket sums and shares, **never a UID**.

Source (BigQuery, via GCP_KEY):
  aura-main.aurie_cost.aurie_per_user_daily_cost_prod   (env='prod' view; date, user_id, total_cost)
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


def _win(args) -> str:
    """WHERE fragment (without the leading AND) selecting the window; '' = all days.
    `--last N` = the last N days PRESENT in the store (matches eng:chart-aurie-daily-cost)."""
    if args.all:
        return ""
    if args.date_from or args.date_to:
        parts = []
        if args.date_from:
            parts.append(f"date >= DATE('{args.date_from}')")
        if args.date_to:
            parts.append(f"date <= DATE('{args.date_to}')")
        return " AND " + " AND ".join(parts)
    n = args.last if args.last is not None else 30
    return (f" AND date >= (SELECT MIN(d) FROM "
            f"(SELECT DISTINCT date AS d FROM `{COST}` ORDER BY d DESC LIMIT {n}))")


def _user_totals_cte(win: str) -> str:
    return f"""
u AS (
  SELECT SUM(total_cost) AS c
  FROM `{COST}`
  WHERE user_id != 'Other'{win}
  GROUP BY user_id
  HAVING SUM(total_cost) > 0
)"""


def _curve_sql(win: str) -> str:
    # NTILE(100) DESC: pctile 1 = the most expensive 1% of users. Cumulating from pctile 1
    # gives "top X% share". Buckets are ~equal user counts (remainder spread across low pctiles).
    return f"""
WITH {_user_totals_cte(win)},
b AS (SELECT c, NTILE(100) OVER (ORDER BY c DESC) AS pctile FROM u),
per AS (SELECT pctile, SUM(c) AS cost, COUNT(*) AS users FROM b GROUP BY pctile)
SELECT pctile, ROUND(cost, 4) AS cost, users,
       SUM(cost) OVER (ORDER BY pctile) AS cum_cost,
       SUM(users) OVER (ORDER BY pctile) AS cum_users,
       SUM(cost) OVER () AS total_cost,
       SUM(users) OVER () AS total_users
FROM per ORDER BY pctile
"""


def _summary_sql(win: str) -> str:
    # Exact Gini via the rank formula (no UID emitted): G = 2*Σ(i*c)/(n*Σc) - (n+1)/n,
    # i = ascending rank. Plus n, total, mean, median.
    return f"""
WITH {_user_totals_cte(win)},
r AS (SELECT c, ROW_NUMBER() OVER (ORDER BY c) AS i, COUNT(*) OVER () AS n, SUM(c) OVER () AS tot FROM u)
SELECT
  MAX(n) AS n_users,
  ROUND(MAX(tot), 2) AS total_cost,
  ROUND(MAX(tot) / MAX(n), 4) AS mean_cost,
  ROUND((SELECT APPROX_QUANTILES(c, 2)[OFFSET(1)] FROM u), 4) AS median_cost,
  ROUND(2 * SUM(i * c) / (MAX(n) * MAX(tot)) - (MAX(n) + 1) / MAX(n), 4) AS gini
FROM r
"""


def _top_share(curve: list, total_cost: float, pct: int) -> dict:
    rows = [r for r in curve if r["pctile"] <= pct]
    cost = rows[-1]["cum_cost"] if rows else 0.0
    users = rows[-1]["cum_users"] if rows else 0
    return {"pct": pct, "users": users, "cost": round(cost, 2),
            "share": round(cost / total_cost, 4) if total_cost else None}


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
    ap = argparse.ArgumentParser(description="Aurie cost concentration (Pareto/Lorenz) -> JSON.")
    ap.add_argument("--last", type=int, default=None, help="most recent N days present (default 30)")
    ap.add_argument("--from", dest="date_from", help="window start, inclusive (YYYY-MM-DD)")
    ap.add_argument("--to", dest="date_to", help="window end, inclusive (YYYY-MM-DD)")
    ap.add_argument("--all", action="store_true", help="every day in the store")
    args = ap.parse_args()

    win = _win(args)
    try:
        meta = bq.query(
            f"SELECT CAST(MIN(date) AS STRING) AS start, CAST(MAX(date) AS STRING) AS `end` "
            f"FROM `{COST}`"
        )[0]
        curve = bq.query(_curve_sql(win))
        summary = bq.query(_summary_sql(win))[0]
    except BQUnavailable as e:
        print(f"BigQuery unavailable: {e}", file=sys.stderr)
        return 1
    except BQError as e:
        print(f"BigQuery error: {e}", file=sys.stderr)
        return 1

    if not curve:
        print("No cost rows in the selected window.", file=sys.stderr)
        return 1

    total_cost = curve[0]["total_cost"]
    total_users = curve[0]["total_users"]
    # Label the window from the dates actually included (not the requested N), so a short
    # store or gap days can't make the label overstate the span.
    wb = bq.query(f"SELECT CAST(MIN(date) AS STRING) AS lo, CAST(MAX(date) AS STRING) AS hi, "
                  f"COUNT(DISTINCT date) AS days FROM `{COST}` WHERE user_id != 'Other'{win}")[0]
    # Pareto/Lorenz points: cumulative cost share vs cumulative top-user share.
    lorenz = [{"top_user_pct": round(r["cum_users"] / total_users, 4),
               "cost_share": round(r["cum_cost"] / total_cost, 4),
               "pctile": r["pctile"]} for r in curve]

    out = {
        "cost_data_start": meta["start"],
        "cost_data_end": meta["end"],
        "window": f"{wb['lo']}..{wb['hi']} ({wb['days']} days present)",
        "summary": summary,
        "top_shares": [_top_share(curve, total_cost, p) for p in (1, 5, 10, 25, 50)],
        "lorenz": lorenz,
    }
    out["skill_version"] = _skill_version()
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
