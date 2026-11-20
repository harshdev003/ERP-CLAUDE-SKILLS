#!/usr/bin/env python3
"""Aurie cohort activity RETENTION as a single pooled curve per segment.

Companion to `eng:chart-aurie-cost-curve`: same three segments and the same weeks-since-join grid,
but the metric is the share of a cohort still ACTIVE in week N (app activity), not cost. It's the
missing second factor for a true lifetime-cost projection: lifetime cost ≈ Σ_N cost_curve(N) ×
retention(N). Cost tells you what an active user costs in week N; retention tells you how many are
still around to incur it.

  Chart A — weeks since AI signup (`ai_created_at`), ALL users.
  Chart B — weeks since subscription activation (`ai_subscription_activated`), users who activated premium.
  Chart C — weeks since AI signup, free users who NEVER bought a subscription.

Definition (classic cohort retention, so the curve starts at 100% and decays):
  cohort   = members active in their week 0 (the first 7 days since the anchor).
  retention(N) = Σ(members active in week N) ÷ Σ(cohort size), size-weighted over the cohorts whose
                 week N is complete (same completeness rule as the cost curve — a still-open trailing
                 week is dropped, keyed to the cohort's latest joiner).
"active" = the `active` flag on the per-user daily cost store (a mobile-app DAU signal, backfilled from
Mixpanel). It only marks activity on a day we booked a cost row; a user active but costing ~$0 that day
is a rare miss. Retention is measured over users present in the cost data.

Sources (BigQuery, via GCP_KEY):
  aura-main.aurie_cost.aurie_per_user_daily_cost_prod   (env='prod' view; date, user_id, active, subscription_state)
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
    """Per-cohort-week × week_n retention rows over the full complete grid.

    The cohort is the set of members active in their week 0 (`base`); `active_users` at week N counts
    how many of those base members were active again in week N. A LEFT JOIN to the grid emits a row
    for EVERY (cohort_week, week_n) that is complete for the cohort's latest joiner — so a week with
    zero returning members is a real 0, not a dropped cell, and the pooled denominator stays the full
    week-0 cohort size.
    """
    return f"""
WITH {_DIM_CTE},
act AS (
  SELECT DISTINCT user_id, date AS d
  FROM `{COST}` WHERE user_id != 'Other' AND active = 1
),
{universe_cte},
wk AS (
  SELECT u.user_id, u.cohort_week, u.anchor,
         DIV(DATE_DIFF(act.d, u.anchor, DAY), 7) AS week_n
  FROM u JOIN act USING(user_id)
  WHERE DATE_DIFF(act.d, u.anchor, DAY) >= 0
  GROUP BY u.user_id, u.cohort_week, u.anchor, week_n
),
base AS (
  -- retention denominator: members active in their first 7 days
  SELECT DISTINCT user_id, cohort_week, anchor FROM wk WHERE week_n = 0
),
sizes AS (
  SELECT cohort_week, COUNT(*) AS cohort_size, MAX(anchor) AS max_anchor
  FROM base GROUP BY cohort_week
),
cells AS (
  SELECT b.cohort_week, wk.week_n, COUNT(DISTINCT wk.user_id) AS active_users
  FROM base b JOIN wk USING(user_id, cohort_week)
  GROUP BY b.cohort_week, wk.week_n
),
grid AS (
  SELECT s.cohort_week, week_n, s.cohort_size
  FROM sizes s
  CROSS JOIN UNNEST(GENERATE_ARRAY(0, {max_week})) AS week_n
  WHERE s.cohort_week >= DATE('{start_week}')
    AND DATE_ADD(s.max_anchor, INTERVAL 7 * week_n + 6 DAY) <= DATE('{max_cost_date}')
)
SELECT CAST(g.cohort_week AS STRING) AS cohort_week, g.week_n,
       IFNULL(c.active_users, 0) AS active_users, g.cohort_size
FROM grid g
LEFT JOIN cells c ON c.cohort_week = g.cohort_week AND c.week_n = g.week_n
ORDER BY g.cohort_week, g.week_n
"""


def _pool(rows: list) -> list:
    """Pool the per-cohort grid into one size-weighted retention curve by week_n.

    retention[N] = Σ(active members at week N) ÷ Σ(week-0 cohort size) over the cohorts whose week N
    is complete. Week 0 is 100% by construction (every base member is active in week 0).
    """
    by_week: dict = {}
    for r in rows:
        w = by_week.setdefault(r["week_n"], {"week_n": r["week_n"], "active": 0,
                                             "cohort": 0, "n_cohorts": 0})
        w["active"] += r["active_users"]
        w["cohort"] += r["cohort_size"]
        w["n_cohorts"] += 1
    out = []
    for w in sorted(by_week.values(), key=lambda x: x["week_n"]):
        cohort = w["cohort"] or 1
        out.append({
            "week_n": w["week_n"],
            "retention": round(w["active"] / cohort, 4),
            "active_users": w["active"],
            "cohort_size": w["cohort"],
            "n_cohorts": w["n_cohorts"],
        })
    return out


def _triangle(rows: list) -> list:
    """Group the per-cohort grid into a retention triangle: one row per join-cohort, cells by week_n.

    Each cell is that single cohort's own retention (active in week N ÷ its week-0 size), so every row
    starts at 1.0 in week 0 and decays across — the per-cohort view behind the pooled curve, mirroring
    eng:chart-aurie-cohort-cost's cost triangle.
    """
    by_cohort: dict = {}
    for r in rows:
        c = by_cohort.setdefault(r["cohort_week"], {"cohort_week": r["cohort_week"],
                                                    "cohort_size": r["cohort_size"], "weeks": []})
        size = r["cohort_size"] or 1
        c["weeks"].append({"week_n": r["week_n"],
                           "retention": round(r["active_users"] / size, 4),
                           "active_users": r["active_users"]})
    out = []
    for c in sorted(by_cohort.values(), key=lambda x: x["cohort_week"]):
        c["weeks"].sort(key=lambda w: w["week_n"])
        out.append(c)
    return out


def _filter_last_weeks(rows: list, last_weeks) -> list:
    """Keep only rows from the most recent `last_weeks` complete cohort-weeks.

    Selection is over cohort-weeks with a non-empty week-0 cohort. Every complete cell of the selected
    weeks is returned, so the pooled denominator keeps counting members who went inactive. No
    preliminary cohort is introduced — rows are already completeness-filtered.

    Pairing caveat: this trims on *populated* cohort-weeks, whereas eng:chart-aurie-cost-curve trims on
    *spend-bearing* ones — the two sets differ only for a cohort with spend but no week-0 activity
    (rare). For a strict Mode-B (trailing-window) lifetime-cost product, align cost + retention on an
    explicit shared --start rather than relying on independent --last-weeks.
    """
    if not last_weeks or last_weeks <= 0:
        return rows
    cohort_weeks = sorted({r["cohort_week"] for r in rows if r["cohort_size"] > 0})
    keep = set(cohort_weeks[-last_weeks:])
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
    ap = argparse.ArgumentParser(
        description="Aurie pooled cohort activity-retention curves (share active by week_n) -> JSON.")
    ap.add_argument("--max-week", type=int, default=8, help="Highest week_n to include (default 8)")
    ap.add_argument("--start", help="Cohort start week (YYYY-MM-DD); default = first full week of cost data")
    ap.add_argument("--last-weeks", type=int, default=None,
                    help="Pool only the most recent N complete cohort-weeks (default: all since data start)")
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
          FROM dim
        )"""
        rows_a = _filter_last_weeks(
            bq.query(_grid_sql(u_a, start_week, args.max_week, max_cost_date)), args.last_weeks)

        # Chart B — users who activated premium, anchor = first ai_subscription_activated.
        # Not survivorship-filtered to current-premium: retention is precisely the churn signal.
        u_b = f"""
        activ AS (
          SELECT userid AS user_id, DATE(MIN(time)) AS anchor
          FROM `{ACTIVATED}` WHERE userid IS NOT NULL GROUP BY userid
        ),
        u AS (
          SELECT activ.user_id, activ.anchor, DATE_TRUNC(activ.anchor, WEEK(MONDAY)) AS cohort_week
          FROM activ
        )"""
        rows_b = _filter_last_weeks(
            bq.query(_grid_sql(u_b, start_week, args.max_week, max_cost_date)), args.last_weeks)

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
          WHERE dim.user_id NOT IN (SELECT user_id FROM ever_bought)
        )"""
        rows_c = _filter_last_weeks(
            bq.query(_grid_sql(u_c, start_week, args.max_week, max_cost_date)), args.last_weeks)
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
        "metric": "share of the week-0-active cohort still active in week N (active flag)",
        "coverage": {"cost_users": cov["cost_users"],
                     "with_ai_created": cov["with_ai_created"], "missing": missing},
        "chart_a": {"anchor": "ai_created_at (weeks since AI signup)", "scope": "all AI users",
                    "curve": _pool(rows_a), "triangle": _triangle(rows_a)},
        "chart_b": {"anchor": "ai_subscription_activated (weeks since subscription activation)",
                    "scope": "users who activated premium",
                    "curve": _pool(rows_b), "triangle": _triangle(rows_b)},
        "chart_c": {"anchor": "ai_created_at (weeks since AI signup)",
                    "scope": "free users, never bought a subscription",
                    "curve": _pool(rows_c), "triangle": _triangle(rows_c)},
    }
    out["skill_version"] = _skill_version()
    print(json.dumps(out, indent=2))
    if missing:
        print(f"\nWARNING: {missing} cost-table users are missing ai_created_at — Chart A/C "
              f"undercount them. Run eng:backfill-aurie-user-ai-created first.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
