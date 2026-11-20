#!/usr/bin/env python3
"""Aurie CUMULATIVE cost-to-date per joined user — the running total of the cost-decay curve.

For each segment, the cumulative average cost/user by weeks-since-join: cum(N) = Σ_{k=0..N} avg_per_user(k),
where avg_per_user(k) = Σ(cohort-week-k cost) ÷ Σ(cohort size) over the cohorts complete at week k (an
inactive/churned user counts as $0, so it's cost per *joined* user — retention is already baked in). The
running total answers "how much have we spent, cumulatively, on a joined user by week N."

Also emits a per-cohort **cost-to-date**: for each join-week cohort, the total $/user spent so far (Σ its
observed weekly cost ÷ cohort size) — the "cumulative cost-to-date per cohort" view.

  Chart A — weeks since AI signup (`ai_created_at`), ALL users.
  Chart B — weeks since subscription activation (`ai_subscription_activated`), users who ACTIVATED premium
            (all-activated denominator, NOT survivorship-filtered — the right base for lifetime economics).
  Chart C — weeks since AI signup, free users who NEVER bought a subscription.

Companion to `eng:chart-aurie-cost-curve` (the per-week decay) and `eng:chart-aurie-lifetime-cost` (the
projected extension of this cumulative to a 52w / 104w horizon).

Sources (BigQuery, via GCP_KEY):
  aura-main.aurie_cost.aurie_per_user_daily_cost_prod   (env='prod' view; date, user_id, total_cost, subscription_state)
  aura-main.aurie_cost.aurie_user_ai_created       (user_id -> ai_created_at; deduped on read)
  mixpanel-prod-1.mixpanel_nessie_day_partitioned_963821.ai_subscription_activated (userid, time)

Coverage guard: Chart A/C need a complete ai_created_at dimension — run
`eng:backfill-aurie-user-ai-created` first if `missing` is non-zero.
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
ACTIVATED = "mixpanel-prod-1.mixpanel_nessie_day_partitioned_963821.ai_subscription_activated"

_DIM_CTE = f"""
dim AS (
  SELECT user_id, DATE(ai_created_at) AS anchor FROM (
    SELECT user_id, ai_created_at,
           ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY fetched_at DESC) AS rn
    FROM `{DIM}`
  ) WHERE rn = 1
)
"""


def _grid_sql(universe_cte: str, start_week: str, max_week: int, max_cost_date: str) -> str:
    """Per-cohort-week × week_n cost rows over the full complete grid (zero-spend cells included as $0),
    so the pooled denominator = Σ cohort_size over all cohorts complete at week N."""
    return f"""
WITH {_DIM_CTE},
cost AS (
  SELECT user_id, date AS d, total_cost
  FROM `{COST}` WHERE user_id != 'Other'
),
{universe_cte},
sizes AS (
  SELECT cohort_week, COUNT(*) AS cohort_size, MAX(anchor) AS max_anchor
  FROM u GROUP BY cohort_week
),
cells AS (
  SELECT u.cohort_week, DIV(DATE_DIFF(cost.d, u.anchor, DAY), 7) AS week_n,
         SUM(cost.total_cost) AS total
  FROM u JOIN cost USING(user_id)
  WHERE DATE_DIFF(cost.d, u.anchor, DAY) >= 0
  GROUP BY u.cohort_week, week_n
),
grid AS (
  SELECT s.cohort_week, week_n, s.cohort_size
  FROM sizes s
  CROSS JOIN UNNEST(GENERATE_ARRAY(0, {max_week})) AS week_n
  WHERE s.cohort_week >= DATE('{start_week}')
    AND DATE_ADD(s.max_anchor, INTERVAL 7 * week_n + 6 DAY) <= DATE('{max_cost_date}')
)
SELECT CAST(g.cohort_week AS STRING) AS cohort_week, g.week_n,
       ROUND(IFNULL(c.total, 0), 4) AS total, g.cohort_size
FROM grid g
LEFT JOIN cells c ON c.cohort_week = g.cohort_week AND c.week_n = g.week_n
ORDER BY g.cohort_week, g.week_n
"""


def _pool_cumulative(rows: list) -> list:
    """Pool to a size-weighted per-week avg, then a running cumulative."""
    by_week: dict = {}
    for r in rows:
        w = by_week.setdefault(r["week_n"], {"week_n": r["week_n"], "total": 0.0, "users": 0, "n_cohorts": 0})
        w["total"] += r["total"]
        w["users"] += r["cohort_size"]
        w["n_cohorts"] += 1
    out, cum = [], 0.0
    for w in sorted(by_week.values(), key=lambda x: x["week_n"]):
        avg = w["total"] / (w["users"] or 1)
        cum += avg
        out.append({"week_n": w["week_n"], "avg_per_user": round(avg, 4),
                    "cum_per_user": round(cum, 4), "users": w["users"], "n_cohorts": w["n_cohorts"]})
    return out


def _cohort_to_date(rows: list) -> list:
    """Per-cohort cumulative: running $/user and total cost-to-date (sum of that cohort's observed weeks)."""
    by_cohort: dict = {}
    for r in rows:
        c = by_cohort.setdefault(r["cohort_week"], {"cohort_week": r["cohort_week"],
                                                    "cohort_size": r["cohort_size"], "cells": []})
        c["cells"].append((r["week_n"], r["total"]))
    out = []
    for c in sorted(by_cohort.values(), key=lambda x: x["cohort_week"]):
        size = c["cohort_size"] or 1
        cum, weeks = 0.0, []
        for wk, total in sorted(c["cells"]):
            cum += total / size
            weeks.append({"week_n": wk, "cum_per_user": round(cum, 4)})
        out.append({"cohort_week": c["cohort_week"], "cohort_size": c["cohort_size"],
                    "weeks_observed": len(weeks), "cost_to_date": round(cum, 4), "cum": weeks})
    return out


def _filter_last_weeks(rows: list, last_weeks) -> list:
    """Keep only rows from the most recent `last_weeks` complete cohort-weeks (matches the cohort family's
    Mode-B trim, so cumulative lines up with the decay curve / heatmap on the same report page)."""
    if not last_weeks or last_weeks <= 0:
        return rows
    # Key on spend-bearing cohort-weeks (total > 0), matching curve.py, so the Mode-B window is identical.
    cohort_weeks = sorted({r["cohort_week"] for r in rows if r["total"] > 0})
    keep = set(cohort_weeks[-last_weeks:])
    return [r for r in rows if r["cohort_week"] in keep]


def _first_monday_on_or_after(date_str: str) -> str:
    rows = bq.query(
        f"SELECT CAST(DATE_TRUNC(DATE_ADD(DATE('{date_str}'), INTERVAL 6 DAY), WEEK(MONDAY)) AS STRING) AS w"
    )
    return rows[0]["w"]


# Segment universes (premium = all-activated, NOT current-premium — the base for lifetime economics).
def _universe(segment: str) -> str:
    # Restrict every segment to users present in the cost table (matches curve.py / cohort.py) so the
    # cohort_size denominator — and thus the pooled avg / cumulative — reconciles with the decay curve.
    if segment == "a":
        return """u AS (
          SELECT dim.user_id, dim.anchor, DATE_TRUNC(dim.anchor, WEEK(MONDAY)) AS cohort_week
          FROM dim WHERE dim.user_id IN (SELECT DISTINCT user_id FROM cost)
        )"""
    if segment == "b":
        return f"""
        activ AS (
          SELECT userid AS user_id, DATE(MIN(time)) AS anchor
          FROM `{ACTIVATED}` WHERE userid IS NOT NULL GROUP BY userid
        ),
        u AS (
          SELECT activ.user_id, activ.anchor, DATE_TRUNC(activ.anchor, WEEK(MONDAY)) AS cohort_week
          FROM activ WHERE activ.user_id IN (SELECT DISTINCT user_id FROM cost)
        )"""
    return f"""
        ever_bought AS (
          SELECT userid AS user_id FROM `{ACTIVATED}` WHERE userid IS NOT NULL
          UNION DISTINCT
          SELECT user_id FROM `{COST}` WHERE user_id != 'Other' AND subscription_state IN ('paid', 'grace')
        ),
        u AS (
          SELECT dim.user_id, dim.anchor, DATE_TRUNC(dim.anchor, WEEK(MONDAY)) AS cohort_week
          FROM dim WHERE dim.user_id NOT IN (SELECT user_id FROM ever_bought)
            AND dim.user_id IN (SELECT DISTINCT user_id FROM cost)
        )"""


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
        description="Aurie cumulative cost-to-date per joined user (pooled curve + per-cohort) -> JSON.")
    ap.add_argument("--max-week", type=int, default=8, help="Highest week_n to include (default 8)")
    ap.add_argument("--start", help="Cohort start week (YYYY-MM-DD); default = first full week of cost data")
    ap.add_argument("--last-weeks", type=int, default=None,
                    help="Pool only the most recent N complete cohort-weeks (default: all since data start)")
    args = ap.parse_args()

    try:
        meta = bq.query(
            f"SELECT CAST(MIN(date) AS STRING) AS start, CAST(MAX(date) AS STRING) AS `end`, "
            f"COUNT(DISTINCT user_id) AS cost_users FROM `{COST}` WHERE user_id != 'Other'"
        )[0]
        cost_start, max_cost_date = meta["start"], meta["end"]
        start_week = args.start or _first_monday_on_or_after(cost_start)

        cov = bq.query(f"""
          WITH {_DIM_CTE},
          cost AS (SELECT DISTINCT user_id FROM `{COST}` WHERE user_id != 'Other')
          SELECT COUNT(*) AS cost_users, COUNTIF(dim.user_id IS NOT NULL) AS with_ai_created
          FROM cost LEFT JOIN dim USING(user_id)
        """)[0]
        missing = cov["cost_users"] - cov["with_ai_created"]

        charts = {}
        for seg, key, anchor, scope in [
            ("a", "chart_a", "ai_created_at (weeks since AI signup)", "all AI users"),
            ("b", "chart_b", "ai_subscription_activated (weeks since subscription activation)",
             "users who activated premium"),
            ("c", "chart_c", "ai_created_at (weeks since AI signup)", "free users, never bought a subscription"),
        ]:
            rows = _filter_last_weeks(
                bq.query(_grid_sql(_universe(seg), start_week, args.max_week, max_cost_date)), args.last_weeks)
            charts[key] = {"anchor": anchor, "scope": scope,
                           "curve": _pool_cumulative(rows), "cohorts": _cohort_to_date(rows)}
    except BQUnavailable as e:
        print(f"BigQuery unavailable: {e}", file=sys.stderr)
        return 1
    except BQError as e:
        print(f"BigQuery error: {e}", file=sys.stderr)
        return 1

    out = {
        "cost_data_start": cost_start, "cost_data_end": max_cost_date,
        "cohort_start_week": start_week, "max_week": args.max_week,
        "metric": "cumulative cost per joined user by weeks-since-join (running total of the cost-decay curve)",
        "coverage": {"cost_users": cov["cost_users"], "with_ai_created": cov["with_ai_created"], "missing": missing},
        **charts,
    }
    out["skill_version"] = _skill_version()
    print(json.dumps(out, indent=2))
    if missing:
        print(f"\nWARNING: {missing} cost-table users are missing ai_created_at — Chart A/C undercount them. "
              f"Run eng:backfill-aurie-user-ai-created first.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
