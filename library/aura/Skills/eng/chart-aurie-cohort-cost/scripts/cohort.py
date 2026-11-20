#!/usr/bin/env python3
"""Aggregate Aurie cost into cohort triangles and emit JSON for the chart step.

Two cohorts, each labelled by its anchor so nobody confuses them:

  Chart A — weeks since **AI signup** (`ai_created_at`), ALL users.
  Chart B — weeks since **subscription activation** (`ai_subscription_activated`),
            CURRENT-premium users only.

A cell is `week_n` = floor((cost_date - anchor_date) / 7) since the anchor. Each cell
carries `total` (sum cost that week) and `avg_per_user` (that total ÷ the cohort's size,
so an inactive user counts as $0 in their week — "what a joined user costs us in week N").

Cohorts are restricted to anchor weeks on/after the cost-data start so week-0 is fully
observed (earlier cohorts have censored early weeks — no per-user cost before then).

Sources (BigQuery, via GCP_KEY):
  aura-main.aurie_cost.aurie_per_user_daily_cost_prod   (env='prod' view; date, user_id, total_cost, subscription_state)
  aura-main.aurie_cost.aurie_user_ai_created       (user_id -> ai_created_at; deduped on read)
  mixpanel-prod-1.mixpanel_nessie_day_partitioned_963821.ai_subscription_activated (userid, time)

Coverage guard: Chart A needs a complete ai_created_at dimension. This script reports how
many cost-table users are missing from it; run `eng:backfill-aurie-user-ai-created` first
if `missing` is non-zero.
"""
from __future__ import annotations

import argparse
import json
import sys

import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bq  # noqa: E402  (sibling copy — self-contained skill)
from bq import BQError, BQUnavailable  # noqa: E402

COST = f"{bq.PROJECT}.{bq.DATASET}.aurie_per_user_daily_cost_prod"  # PROD-3589 Part B: env='prod' only
DIM = f"{bq.PROJECT}.{bq.DATASET}.aurie_user_ai_created"
# Mixpanel project 963821 is shared by Aura (meditation) + Aurie (AI). Subscription events
# are prefixed by type (PaymentService src/config.js -> subscriptionTypes.<type>.
# mixpanelEventPrefix): Aurie = "AI" -> ai_subscription_*, Aura Premium = "" -> unprefixed
# subscription_* (which is ~300x larger). ALWAYS use the ai_ prefix here — the unprefixed
# subscription_activated is Aura meditation and would corrupt every number.
ACTIVATED = "mixpanel-prod-1.mixpanel_nessie_day_partitioned_963821.ai_subscription_activated"

# Deduped ai_created_at dimension (append-only table can hold a rare duplicate).
_DIM_CTE = f"""
dim AS (
  SELECT user_id, DATE(ai_created_at) AS anchor FROM (
    SELECT user_id, ai_created_at,
           ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY fetched_at DESC) AS rn
    FROM `{DIM}`
  ) WHERE rn = 1
)
"""

# Shared cohort roll-up given a `u` CTE of (user_id, anchor, cohort_week) and the cost CTE.
def _triangle_sql(universe_cte: str, start_week: str, max_week: int, max_cost_date: str) -> str:
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
  -- week_n = weeks since each user's own anchor (join), so week 0 is always that user's
  -- full first 7 days — not a partial calendar week.
  SELECT u.cohort_week,
         DIV(DATE_DIFF(cost.d, u.anchor, DAY), 7) AS week_n,
         SUM(cost.total_cost) AS total,
         COUNT(DISTINCT cost.user_id) AS active_users
  FROM u JOIN cost USING(user_id)
  WHERE DATE_DIFF(cost.d, u.anchor, DAY) >= 0
  GROUP BY u.cohort_week, week_n
)
SELECT CAST(c.cohort_week AS STRING) AS cohort_week, c.week_n,
       ROUND(c.total, 2) AS total, c.active_users, s.cohort_size,
       ROUND(SAFE_DIVIDE(c.total, s.cohort_size), 4) AS avg_per_user
FROM cells c JOIN sizes s USING(cohort_week)
WHERE c.cohort_week >= DATE('{start_week}')
  AND c.week_n BETWEEN 0 AND {max_week}
  -- Keep only weeks complete for EVERY user in the cohort (the latest joiner's week fully
  -- observed), so a still-open trailing week can't understate the fixed-denominator average.
  AND DATE_ADD(s.max_anchor, INTERVAL 7 * c.week_n + 6 DAY) <= DATE('{max_cost_date}')
ORDER BY c.cohort_week, c.week_n
"""


def _shape(rows: list) -> list:
    """Group flat (cohort_week, week_n, ...) rows into per-cohort objects."""
    by_cohort: dict = {}
    for r in rows:
        cw = r["cohort_week"]
        c = by_cohort.setdefault(cw, {"cohort_week": cw, "cohort_size": r["cohort_size"], "weeks": []})
        c["weeks"].append({
            "week_n": r["week_n"], "total": r["total"],
            "avg_per_user": r["avg_per_user"], "active_users": r["active_users"],
        })
    return list(by_cohort.values())


def _trim_last_weeks(cohorts: list, last_weeks) -> list:
    """Keep only the most recent `last_weeks` complete cohort-weeks.

    `cohorts` is already ascending by cohort_week and holds only weeks the SQL
    completeness rule accepted (done weeks), so slicing the tail yields the last N
    complete cohorts — no preliminary/forming cohort is introduced.
    """
    if not last_weeks or last_weeks <= 0:
        return cohorts
    return cohorts[-last_weeks:]


def _first_monday_on_or_after(date_str: str) -> str:
    rows = bq.query(
        f"SELECT CAST(DATE_TRUNC(DATE_ADD(DATE('{date_str}'), INTERVAL 6 DAY), WEEK(MONDAY)) AS STRING) AS w"
    )
    return rows[0]["w"]


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
    ap = argparse.ArgumentParser(description="Aurie cohort cost triangles -> JSON.")
    ap.add_argument("--max-week", type=int, default=8, help="Highest week_n to include (default 8)")
    ap.add_argument("--start", help="Cohort start week (YYYY-MM-DD); default = first full week of cost data")
    ap.add_argument("--last-weeks", type=int, default=None,
                    help="Only the most recent N complete cohort-weeks (default: all since data start)")
    ap.add_argument("--premium-window", type=int, default=7,
                    help="'Current premium' = latest paid cost row within N days of the data (default 7)")
    args = ap.parse_args()

    try:
        # Cost-data start + coverage guard.
        meta = bq.query(
            f"SELECT CAST(MIN(date) AS STRING) AS start, CAST(MAX(date) AS STRING) AS `end`, "
            f"COUNT(DISTINCT user_id) AS cost_users "
            f"FROM `{COST}` WHERE user_id != 'Other'"
        )[0]
        cost_start, max_cost_date = meta["start"], meta["end"]
        start_week = args.start or _first_monday_on_or_after(cost_start)

        cov = bq.query(f"""
          WITH {_DIM_CTE},
          cost AS (SELECT DISTINCT user_id FROM `{COST}` WHERE user_id != 'Other')
          SELECT COUNT(*) AS cost_users,
                 COUNTIF(dim.user_id IS NOT NULL) AS with_ai_created
          FROM cost LEFT JOIN dim USING(user_id)
        """)[0]
        missing = cov["cost_users"] - cov["with_ai_created"]

        # Chart A — all users, anchor = ai_created_at.
        u_a = """u AS (
          SELECT dim.user_id, dim.anchor, DATE_TRUNC(dim.anchor, WEEK(MONDAY)) AS cohort_week
          FROM dim WHERE dim.user_id IN (SELECT DISTINCT user_id FROM cost)
        )"""
        chart_a = _shape(bq.query(_triangle_sql(u_a, start_week, args.max_week, max_cost_date)))

        # Chart B — current-premium users only, anchor = first ai_subscription_activated.
        u_b = f"""
        activ AS (
          SELECT userid AS user_id, DATE(MIN(time)) AS anchor
          FROM `{ACTIVATED}` WHERE userid IS NOT NULL GROUP BY userid
        ),
        current_premium AS (
          -- "currently premium" proxy: the most-recent cost row is paid AND recent (within
          -- --premium-window days of the latest data), so a churned user whose last paid row
          -- is weeks old drops out instead of inflating the cohort. The store only writes a
          -- row on days with attributed spend, so a stale last-paid row would otherwise
          -- linger. (Fully authoritative currentness is the Aura API premiumAI flag, avoided
          -- here — per-user calls for an aggregate chart.)
          SELECT user_id FROM (
            SELECT user_id, subscription_state, date,
                   ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY date DESC) AS rn
            FROM `{COST}` WHERE user_id != 'Other'
          ) WHERE rn = 1 AND subscription_state = 'paid'
                AND date >= DATE_SUB(DATE('{max_cost_date}'), INTERVAL {args.premium_window} DAY)
        ),
        u AS (
          SELECT activ.user_id, activ.anchor, DATE_TRUNC(activ.anchor, WEEK(MONDAY)) AS cohort_week
          FROM activ
          JOIN current_premium USING(user_id)
          WHERE activ.user_id IN (SELECT DISTINCT user_id FROM cost)
        )"""
        chart_b = _shape(bq.query(_triangle_sql(u_b, start_week, args.max_week, max_cost_date)))

        # Chart C — free users who NEVER bought a subscription, anchor = ai_created_at.
        # "never bought" = never activated a subscription (not in ai_subscription_activated —
        # the same authoritative event Chart B uses) AND never carried a paid/grace state in
        # the cost table (belt + suspenders, in case an activation wasn't captured as an
        # event). This excludes churned-paid and anyone who ever entered the paid funnel;
        # the cost-table `subscription_state` alone is a spend-only snapshot and isn't
        # enough. Anchor is AI signup (these users have no activation).
        u_c = f"""
        ever_bought AS (
          SELECT userid AS user_id FROM `{ACTIVATED}` WHERE userid IS NOT NULL
          UNION DISTINCT
          SELECT user_id FROM `{COST}`
          WHERE user_id != 'Other' AND subscription_state IN ('paid', 'grace')
        ),
        u AS (
          SELECT dim.user_id, dim.anchor, DATE_TRUNC(dim.anchor, WEEK(MONDAY)) AS cohort_week
          FROM dim
          WHERE dim.user_id IN (SELECT DISTINCT user_id FROM cost)
            AND dim.user_id NOT IN (SELECT user_id FROM ever_bought)
        )"""
        chart_c = _shape(bq.query(_triangle_sql(u_c, start_week, args.max_week, max_cost_date)))
        chart_a = _trim_last_weeks(chart_a, args.last_weeks)
        chart_b = _trim_last_weeks(chart_b, args.last_weeks)
        chart_c = _trim_last_weeks(chart_c, args.last_weeks)
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
        "coverage": {"cost_users": cov["cost_users"],
                     "with_ai_created": cov["with_ai_created"], "missing": missing},
        "chart_a": {"anchor": "ai_created_at (weeks since AI signup)",
                    "scope": "all AI users", "cohorts": chart_a},
        "chart_b": {"anchor": "ai_subscription_activated (weeks since subscription activation)",
                    "scope": "current-premium users only", "cohorts": chart_b},
        "chart_c": {"anchor": "ai_created_at (weeks since AI signup)",
                    "scope": "free users, never bought a subscription", "cohorts": chart_c},
    }
    out["skill_version"] = _skill_version()
    print(json.dumps(out, indent=2))
    if missing:
        print(f"\nWARNING: {missing} cost-table users are missing ai_created_at — "
              f"Chart A undercounts them. Run eng:backfill-aurie-user-ai-created first.",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
