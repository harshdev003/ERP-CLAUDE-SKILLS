#!/usr/bin/env python3
"""Cohort payback: cumulative cost per cohort vs cumulative subscription revenue.

For each cohort (users grouped by the week they first paid), track cumulative per-user cost
against cumulative per-user **recognized (amortized) revenue** over weeks-since-first-purchase,
so you can read the **payback week** (when cumulative revenue overtakes cumulative cost) and the
**contribution margin** per cohort. The "are new subscribers profitable?" answer behind the #ai
margin thread.

Revenue basis — **amortized (recognized)**: each subscription period's revenue is spread evenly
across its `[purchase_at, expiration_at]` days (from `aurie_user_subscription`), so an annual sub
doesn't book a year of revenue on day 1. Cost is the per-user daily cost from the store, cumulated
from each user's first-purchase anchor (including any post-churn free-usage cost — honest cohort
economics: a churned user who keeps using Aurie still costs us with no new revenue).

Cohorts are restricted to first-purchase weeks on/after the cost-data start so week-0 cost is fully
observed (earlier cohorts have censored early weeks — no per-user cost before the store began).

Sources (BigQuery, via GCP_KEY):
  aura-main.aurie_cost.aurie_per_user_daily_cost_prod   (env='prod' view; date, user_id, total_cost)
  aura-main.aurie_cost.aurie_user_subscription     (user_id, purchase_at, expiration_at, revenue)

Prerequisite: `aurie_user_subscription` must be current — build it with
`eng:build-aurie-user-subscription` before running. This is **enforced**, not advisory:
the script calls `bq.assert_fresh` and exits 2 if the dimension is over 36h old, because a
stale rebuild silently understates revenue rather than failing. `--allow-stale` overrides.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bq  # noqa: E402  (sibling copy — self-contained skill)
from bq import BQError, BQStale, BQUnavailable  # noqa: E402

COST = f"{bq.PROJECT}.{bq.DATASET}.aurie_per_user_daily_cost_prod"  # PROD-3589 Part B: env='prod' only
SUB = f"{bq.PROJECT}.{bq.DATASET}.aurie_user_subscription"
REBUILD_HINT = ("eng:build-aurie-user-subscription — "
                "python3 plugins/eng/skills/build-aurie-user-subscription/scripts/subscription.py")
ENV = f"{bq.PROJECT}.{bq.DATASET}.aurie_user_env"  # PROD-3589 Part B: restrict revenue to prod users too


def _cohort_sql(start_week: str, max_week: int, max_cost_date: str) -> str:
    """Per-cohort weekly cost + amortized revenue (weeks since first purchase).

    Anchor = each user's first purchase (`MIN(purchase_at)`); cohort = that anchor's Monday week.
    `week_n` counts from the user's own anchor so week 0 is their first 7 days. A week is emitted
    only once complete for EVERY user in the cohort (latest joiner's span within the data), so a
    still-open trailing week can't understate a fixed-denominator cumulative.
    """
    return f"""
WITH prod_users AS (
  -- PROD-3589 Part B: revenue must be on the same prod population as cost (which comes from
  -- the env='prod' view). Dedup the append-only env dimension to the latest row per user.
  SELECT user_id FROM (
    SELECT user_id, env, ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY fetched_at DESC) rn
    FROM `{ENV}`
  ) WHERE rn = 1 AND env = 'prod'
),
sub AS (
  -- Each period's daily recognized-revenue rate, amortized over its NOMINAL length from
  -- `period` (year -> 365, month -> 30), not the raw expiration window. ~33 rows have a
  -- corrupt expiration only minutes after purchase (an immediate expire/refund event), which
  -- would otherwise dump a full year/month of revenue onto day 0; the nominal length spreads
  -- every period correctly. Unknown period tokens fall back to the raw window (floored at 1).
  SELECT user_id, p_start,
         DATE_ADD(p_start, INTERVAL days - 1 DAY) AS p_end,
         revenue / days AS daily_rate
  FROM (
    SELECT user_id, DATE(purchase_at) AS p_start, revenue,
           CASE LOWER(period)
             WHEN 'year' THEN 365
             WHEN 'month' THEN 30
             ELSE GREATEST(DATE_DIFF(DATE(expiration_at), DATE(purchase_at), DAY), 1)
           END AS days
    FROM `{SUB}`
    JOIN prod_users USING (user_id)
  )
),
anchor AS (SELECT user_id, MIN(p_start) AS anchor FROM sub GROUP BY user_id),
cohort_users AS (
  SELECT a.user_id, a.anchor, DATE_TRUNC(a.anchor, WEEK(MONDAY)) AS cohort_week
  FROM anchor a
  WHERE DATE_TRUNC(a.anchor, WEEK(MONDAY)) >= DATE('{start_week}')
),
cost_days AS (SELECT DISTINCT date AS d FROM `{COST}`),
rev_by_day AS (
  -- amortized revenue recognized per user per observable day (>= anchor)
  SELECT cu.user_id, cd.d, SUM(s.daily_rate) AS rev_d
  FROM cohort_users cu
  JOIN cost_days cd ON cd.d >= cu.anchor
  JOIN sub s ON s.user_id = cu.user_id AND cd.d BETWEEN s.p_start AND s.p_end
  GROUP BY cu.user_id, cd.d
),
grid AS (
  SELECT cu.cohort_week, cu.anchor, cu.user_id,
         DIV(DATE_DIFF(cd.d, cu.anchor, DAY), 7) AS week_n,
         IFNULL(c.total_cost, 0) AS cost_d, IFNULL(r.rev_d, 0) AS rev_d
  FROM cohort_users cu
  JOIN cost_days cd ON cd.d >= cu.anchor
  LEFT JOIN `{COST}` c ON c.user_id = cu.user_id AND c.date = cd.d
  LEFT JOIN rev_by_day r ON r.user_id = cu.user_id AND r.d = cd.d
),
sizes AS (SELECT cohort_week, COUNT(*) AS cohort_size, MAX(anchor) AS max_anchor
          FROM cohort_users GROUP BY cohort_week),
weekly AS (SELECT cohort_week, week_n, SUM(cost_d) AS wc, SUM(rev_d) AS wr,
                  COUNT(DISTINCT user_id) AS active
           FROM grid GROUP BY cohort_week, week_n)
SELECT CAST(w.cohort_week AS STRING) AS cohort_week, w.week_n, s.cohort_size,
       ROUND(w.wc, 2) AS week_cost, ROUND(w.wr, 2) AS week_rev, w.active AS active_users
FROM weekly w JOIN sizes s USING(cohort_week)
WHERE w.week_n BETWEEN 0 AND {max_week}
  AND DATE_ADD(s.max_anchor, INTERVAL 7 * w.week_n + 6 DAY) <= DATE('{max_cost_date}')
ORDER BY w.cohort_week, w.week_n
"""


def _shape(rows: list) -> list:
    """Group flat weekly rows into per-cohort objects with cumulative per-user curves,
    payback week, and latest contribution margin."""
    by_cohort: dict = {}
    for r in rows:
        c = by_cohort.setdefault(r["cohort_week"],
                                 {"cohort_week": r["cohort_week"], "cohort_size": r["cohort_size"],
                                  "weeks": []})
        c["weeks"].append(r)
    out = []
    for c in by_cohort.values():
        size = c["cohort_size"] or 1
        cum_cost = cum_rev = 0.0
        payback_week = None
        weeks = []
        for r in sorted(c["weeks"], key=lambda x: x["week_n"]):
            cum_cost += r["week_cost"]
            cum_rev += r["week_rev"]
            ccpu = round(cum_cost / size, 4)
            crpu = round(cum_rev / size, 4)
            if payback_week is None and cum_rev >= cum_cost:
                payback_week = r["week_n"]
            weeks.append({
                "week_n": r["week_n"],
                "week_cost": r["week_cost"], "week_rev": r["week_rev"],
                "cum_cost_per_user": ccpu, "cum_rev_per_user": crpu,
                "active_users": r["active_users"],
            })
        margin_latest = round((cum_rev - cum_cost) / cum_rev, 4) if cum_rev else None
        out.append({**c, "weeks": weeks, "payback_week": payback_week,
                    "margin_latest": margin_latest})
    return out


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
    ap = argparse.ArgumentParser(description="Cohort payback: cumulative cost vs recognized revenue -> JSON.")
    ap.add_argument("--max-week", type=int, default=8, help="Highest week_n to include (default 8)")
    ap.add_argument("--start", help="Cohort start week (YYYY-MM-DD); default = first full week of cost data")
    ap.add_argument("--last-weeks", type=int, default=None,
                    help="Only the most recent N complete cohort-weeks (default: all since data start)")
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

    try:
        if not bq.table_exists(SUB):
            print(f"Missing {SUB} — build it first with eng:build-aurie-user-subscription.",
                  file=sys.stderr)
            return 1
        meta = bq.query(
            f"SELECT CAST(MIN(date) AS STRING) AS start, CAST(MAX(date) AS STRING) AS `end` "
            f"FROM `{COST}`"
        )[0]
        cost_start, max_cost_date = meta["start"], meta["end"]
        start_week = args.start or bq.query(
            f"SELECT CAST(DATE_TRUNC(DATE_ADD(DATE('{cost_start}'), INTERVAL 6 DAY), "
            f"WEEK(MONDAY)) AS STRING) AS w"
        )[0]["w"]
        cohorts = _shape(bq.query(_cohort_sql(start_week, args.max_week, max_cost_date)))
        # --last-weeks: keep the most recent N complete cohort-weeks (ascending -> tail).
        if args.last_weeks and args.last_weeks > 0:
            cohorts = cohorts[-args.last_weeks:]
    except BQUnavailable as e:
        print(f"BigQuery unavailable: {e}", file=sys.stderr)
        return 1
    except BQError as e:
        print(f"BigQuery error: {e}", file=sys.stderr)
        return 1

    out = {
        "cost_data_start": cost_start,
        "cost_data_end": max_cost_date,
        "cohort_start_week": start_week,
        "last_weeks": args.last_weeks,
        "max_week": args.max_week,
        "anchor": "first subscription purchase (weeks since first paid)",
        "revenue_basis": "amortized (recognized): period revenue spread evenly across "
                         "[purchase_at, expiration_at]",
        "cohorts": cohorts,
    }
    out["skill_version"] = _skill_version()
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
