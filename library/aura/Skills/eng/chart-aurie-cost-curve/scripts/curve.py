#!/usr/bin/env python3
"""Aurie cohort cost as a single pooled decay CURVE per segment (not a per-cohort triangle).

Companion to `eng:chart-aurie-cohort-cost`: same three segments, but all cohorts are pooled into one
average-cost-per-user curve over weeks-since-join (week 0, week 1, week 2, …) — the column-average of
the triangle — so you get a clean "what a joined user costs us in week N" line per segment.

  Chart A — weeks since AI signup (`ai_created_at`), ALL users.
  Chart B — weeks since subscription activation (`ai_subscription_activated`), CURRENT-premium only.
  Chart C — weeks since AI signup, free users who NEVER bought a subscription.

Pooled avg at week N = Σ(cohort-week-N cost) ÷ Σ(cohort size), over the cohorts whose week N is
complete (size-weighted). It reuses the triangle's per-user weeks + completeness rules, so a still-
open trailing week is dropped and week 0 is always a full first 7 days.

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


def _grid_sql(universe_cte: str, start_week: str, max_week: int, max_cost_date: str) -> str:
    """Per-cohort-week × week_n cost rows over the FULL complete grid.

    Unlike the triangle (which only shows cohort-weeks that had spend), this emits a row for EVERY
    (cohort_week, week_n) that is complete — even zero-spend ones — with `total`/`active_users` = 0
    via a LEFT JOIN to the cost cells. That keeps the pooled denominator = Σ cohort_size over *all*
    cohorts complete at week N (inactive users count as $0), so later-week averages aren't
    overstated by silently dropping idle-but-complete cohorts.
    """
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
  SELECT u.cohort_week,
         DIV(DATE_DIFF(cost.d, u.anchor, DAY), 7) AS week_n,
         SUM(cost.total_cost) AS total,
         COUNT(DISTINCT cost.user_id) AS active_users
  FROM u JOIN cost USING(user_id)
  WHERE DATE_DIFF(cost.d, u.anchor, DAY) >= 0
  GROUP BY u.cohort_week, week_n
),
grid AS (
  -- every (cohort_week, week_n) that is complete for EVERY user in the cohort, spend or not
  SELECT s.cohort_week, week_n, s.cohort_size
  FROM sizes s
  CROSS JOIN UNNEST(GENERATE_ARRAY(0, {max_week})) AS week_n
  WHERE s.cohort_week >= DATE('{start_week}')
    AND DATE_ADD(s.max_anchor, INTERVAL 7 * week_n + 6 DAY) <= DATE('{max_cost_date}')
)
SELECT CAST(g.cohort_week AS STRING) AS cohort_week, g.week_n,
       ROUND(IFNULL(c.total, 0), 4) AS total,
       IFNULL(c.active_users, 0) AS active_users,
       g.cohort_size
FROM grid g
LEFT JOIN cells c ON c.cohort_week = g.cohort_week AND c.week_n = g.week_n
ORDER BY g.cohort_week, g.week_n
"""


def _pool(rows: list) -> list:
    """Pool the triangle across cohorts into one size-weighted curve by week_n.

    avg_per_user[N] = Σ(cost at week N) ÷ Σ(cohort_size) over the cohorts whose week N is present
    (complete). Each such cohort contributes its full size to the denominator, so inactive users
    count as $0 — the "cost per joined user at age N" number, pooled.
    """
    by_week: dict = {}
    for r in rows:
        w = by_week.setdefault(r["week_n"], {"week_n": r["week_n"], "total": 0.0,
                                             "users": 0, "active_users": 0, "n_cohorts": 0})
        w["total"] += r["total"]
        w["users"] += r["cohort_size"]        # Σ cohort size over contributing cohorts
        w["active_users"] += r["active_users"]
        w["n_cohorts"] += 1
    out = []
    for w in sorted(by_week.values(), key=lambda x: x["week_n"]):
        users = w["users"] or 1
        out.append({
            "week_n": w["week_n"],
            "avg_per_user": round(w["total"] / users, 4),
            "total": round(w["total"], 2),
            "users": w["users"],
            "active_users": w["active_users"],
            "n_cohorts": w["n_cohorts"],
        })
    return out


def _filter_last_weeks(rows: list, last_weeks) -> list:
    """Keep only rows from the most recent `last_weeks` complete cohort-weeks.

    The selection is over cohort-weeks that had SPEND — the same universe
    eng:chart-aurie-cohort-cost's triangle trims — so the pooled curve and the
    heatmap show the identical window for a segment (the grid also emits zero-spend
    complete cohort-weeks, which must not shift the window). Every complete cell
    (including zero-spend) of the selected weeks is still returned, so the pooled
    denominator keeps counting inactive-but-complete users as $0. No preliminary
    cohort is introduced — the rows are already completeness-filtered.
    """
    if not last_weeks or last_weeks <= 0:
        return rows
    spend_weeks = sorted({r["cohort_week"] for r in rows if r["total"] > 0})
    keep = set(spend_weeks[-last_weeks:])
    return [r for r in rows if r["cohort_week"] in keep]


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
    ap = argparse.ArgumentParser(description="Aurie pooled cohort cost curves (avg cost/user by week_n) -> JSON.")
    ap.add_argument("--max-week", type=int, default=8, help="Highest week_n to include (default 8)")
    ap.add_argument("--start", help="Cohort start week (YYYY-MM-DD); default = first full week of cost data")
    ap.add_argument("--last-weeks", type=int, default=None,
                    help="Pool only the most recent N complete cohort-weeks (default: all since data start)")
    ap.add_argument("--premium-window", type=int, default=7,
                    help="'Current premium' = latest paid cost row within N days of the data (default 7)")
    args = ap.parse_args()

    try:
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
          SELECT COUNT(*) AS cost_users, COUNTIF(dim.user_id IS NOT NULL) AS with_ai_created
          FROM cost LEFT JOIN dim USING(user_id)
        """)[0]
        missing = cov["cost_users"] - cov["with_ai_created"]

        # Chart A — all users, anchor = ai_created_at.
        u_a = """u AS (
          SELECT dim.user_id, dim.anchor, DATE_TRUNC(dim.anchor, WEEK(MONDAY)) AS cohort_week
          FROM dim WHERE dim.user_id IN (SELECT DISTINCT user_id FROM cost)
        )"""
        curve_a = _pool(_filter_last_weeks(bq.query(_grid_sql(u_a, start_week, args.max_week, max_cost_date)), args.last_weeks))

        # Chart B — current-premium users only, anchor = first ai_subscription_activated.
        u_b = f"""
        activ AS (
          SELECT userid AS user_id, DATE(MIN(time)) AS anchor
          FROM `{ACTIVATED}` WHERE userid IS NOT NULL GROUP BY userid
        ),
        current_premium AS (
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
        curve_b = _pool(_filter_last_weeks(bq.query(_grid_sql(u_b, start_week, args.max_week, max_cost_date)), args.last_weeks))

        # Chart C — free users who NEVER bought a subscription, anchor = ai_created_at.
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
        curve_c = _pool(_filter_last_weeks(bq.query(_grid_sql(u_c, start_week, args.max_week, max_cost_date)), args.last_weeks))
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
        "chart_a": {"anchor": "ai_created_at (weeks since AI signup)", "scope": "all AI users",
                    "curve": curve_a},
        "chart_b": {"anchor": "ai_subscription_activated (weeks since subscription activation)",
                    "scope": "current-premium users only", "curve": curve_b},
        "chart_c": {"anchor": "ai_created_at (weeks since AI signup)",
                    "scope": "free users, never bought a subscription", "curve": curve_c},
    }
    out["skill_version"] = _skill_version()
    print(json.dumps(out, indent=2))
    if missing:
        print(f"\nWARNING: {missing} cost-table users are missing ai_created_at — Chart A/C "
              f"undercount them. Run eng:backfill-aurie-user-ai-created first.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
