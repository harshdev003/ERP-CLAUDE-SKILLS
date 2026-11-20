#!/usr/bin/env python3
"""Aurie projected LIFETIME cost per joined user — the cost-decay curve extrapolated to a fixed horizon.

The per-joined-user cost curve (avg cost/user by weeks-since-join, pooled per segment) already bakes in
retention — it is cost per *joined* user, so a churned user contributes $0. Its running total is therefore
the cumulative lifetime cost per joined user; projecting the decaying tail forward and summing to a horizon
H gives the estimated lifetime cost per user at H weeks.

  projected_lifetime(H) = Σ_{w=0..last} avg_per_user(w)          # observed cost-to-date
                        + Σ_{w=last+1..H} avg_per_user(last)·r^(w-last)   # geometric-decay tail

r = the geometric mean of the post-onboarding (w≥1) week-over-week ratios, clamped to (0, 0.98). Because
per-joined-user cost decays toward $0 as a cohort churns, the tail converges — 52w and 104w usually differ
little, which is itself the finding: cost is front-loaded, so lifetime ≈ cost-to-date + a small tail.

  Chart A — weeks since AI signup, ALL users.
  Chart B — weeks since subscription activation, users who ACTIVATED premium (all-activated denominator,
            NOT survivorship-filtered — else lifetime is overstated by conditioning on survivors).
  Chart C — weeks since AI signup, free users who NEVER bought a subscription.

Horizons default to 52w and 104w (Steve's ask). Companion to `eng:chart-aurie-cumulative-cost` (the observed
running total) and `eng:chart-aurie-cost-curve` (the per-week decay). PROJECTION — directional, see caveats.

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
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bq  # noqa: E402  (sibling copy — self-contained skill)
from bq import BQError, BQUnavailable  # noqa: E402

COST = f"{bq.PROJECT}.{bq.DATASET}.aurie_per_user_daily_cost_prod"  # PROD-3589 Part B: env='prod' only
DIM = f"{bq.PROJECT}.{bq.DATASET}.aurie_user_ai_created"
ACTIVATED = "mixpanel-prod-1.mixpanel_nessie_day_partitioned_963821.ai_subscription_activated"

R_CAP = 0.98  # clamp the decay ratio below 1 so the projected tail always converges

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
    return f"""
WITH {_DIM_CTE},
cost AS (
  SELECT user_id, date AS d, total_cost FROM `{COST}` WHERE user_id != 'Other'
),
{universe_cte},
sizes AS (
  SELECT cohort_week, COUNT(*) AS cohort_size, MAX(anchor) AS max_anchor FROM u GROUP BY cohort_week
),
cells AS (
  SELECT u.cohort_week, DIV(DATE_DIFF(cost.d, u.anchor, DAY), 7) AS week_n, SUM(cost.total_cost) AS total
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


def _universe(segment: str) -> str:
    # Restrict every segment to users present in the cost table (matches curve.py / cohort.py) so the
    # cohort_size denominator — and thus the fitted curve — reconciles with the decay curve.
    if segment == "a":
        return """u AS (SELECT dim.user_id, dim.anchor, DATE_TRUNC(dim.anchor, WEEK(MONDAY)) AS cohort_week
                        FROM dim WHERE dim.user_id IN (SELECT DISTINCT user_id FROM cost))"""
    if segment == "b":
        return f"""
        activ AS (SELECT userid AS user_id, DATE(MIN(time)) AS anchor
                  FROM `{ACTIVATED}` WHERE userid IS NOT NULL GROUP BY userid),
        u AS (SELECT activ.user_id, activ.anchor, DATE_TRUNC(activ.anchor, WEEK(MONDAY)) AS cohort_week
              FROM activ WHERE activ.user_id IN (SELECT DISTINCT user_id FROM cost))"""
    return f"""
        ever_bought AS (
          SELECT userid AS user_id FROM `{ACTIVATED}` WHERE userid IS NOT NULL
          UNION DISTINCT
          SELECT user_id FROM `{COST}` WHERE user_id != 'Other' AND subscription_state IN ('paid', 'grace')),
        u AS (SELECT dim.user_id, dim.anchor, DATE_TRUNC(dim.anchor, WEEK(MONDAY)) AS cohort_week
              FROM dim WHERE dim.user_id NOT IN (SELECT user_id FROM ever_bought)
                AND dim.user_id IN (SELECT DISTINCT user_id FROM cost))"""


def _filter_last_weeks(rows: list, last_weeks) -> list:
    """Keep only rows from the most recent `last_weeks` complete cohort-weeks (matches the cohort family's
    Mode-B trim, so the projection is fit on the same window as the decay curve on the report page)."""
    if not last_weeks or last_weeks <= 0:
        return rows
    # Key on spend-bearing cohort-weeks (total > 0), matching curve.py, so the window is identical.
    cohort_weeks = sorted({r["cohort_week"] for r in rows if r["total"] > 0})
    keep = set(cohort_weeks[-last_weeks:])
    return [r for r in rows if r["cohort_week"] in keep]


def _pool(rows: list) -> list:
    """Pooled per-week avg cost per joined user (size-weighted)."""
    by_week: dict = {}
    for r in rows:
        w = by_week.setdefault(r["week_n"], {"week_n": r["week_n"], "total": 0.0, "users": 0})
        w["total"] += r["total"]
        w["users"] += r["cohort_size"]
    return [{"week_n": w["week_n"], "avg_per_user": w["total"] / (w["users"] or 1), "users": w["users"]}
            for w in sorted(by_week.values(), key=lambda x: x["week_n"])]


def _fit_ratio(avgs: list) -> tuple:
    """Geometric mean of post-onboarding (w>=1) week-over-week ratios, clamped to (0, R_CAP)."""
    ratios = []
    for i in range(1, len(avgs) - 1):
        a, b = avgs[i]["avg_per_user"], avgs[i + 1]["avg_per_user"]
        if a > 0 and b > 0:
            ratios.append(b / a)
    if not ratios:
        return 0.5, "default (insufficient post-onboarding weeks to fit)"
    r = math.exp(sum(math.log(x) for x in ratios) / len(ratios))
    if r >= 1:
        return R_CAP, f"clamped (fitted r={r:.2f} >= 1 — flat/rising tail; bounded at {R_CAP})"
    return r, f"geometric mean of {len(ratios)} post-onboarding ratios"


def _project(avgs: list, r: float, horizons: list) -> dict:
    """Observed cumulative + geometric tail to each horizon."""
    obs_cum = sum(a["avg_per_user"] for a in avgs)
    last = avgs[-1]["week_n"]
    v_last = avgs[-1]["avg_per_user"]
    projections = []
    for H in horizons:
        n = max(0, H - last)
        # geometric sum v_last*r + v_last*r^2 + ... + v_last*r^n
        tail = v_last * r * (1 - r ** n) / (1 - r) if n > 0 else 0.0
        projections.append({"horizon_weeks": H, "projected_lifetime_per_user": round(obs_cum + tail, 4),
                            "tail_added": round(tail, 4)})
    converged = v_last * r / (1 - r)  # H -> infinity
    return {"observed_cost_to_date": round(obs_cum, 4), "last_observed_week": last,
            "last_week_avg": round(v_last, 4), "decay_ratio": round(r, 4),
            "converged_lifetime_per_user": round(obs_cum + converged, 4), "projections": projections}


def _cohort_projected(rows: list, r: float, horizons: list) -> list:
    """Per-cohort: observed cost-to-date + geometric tail from that cohort's last observed week (pooled r)."""
    by_cohort: dict = {}
    for row in rows:
        c = by_cohort.setdefault(row["cohort_week"], {"cohort_week": row["cohort_week"],
                                                      "cohort_size": row["cohort_size"], "cells": []})
        c["cells"].append((row["week_n"], row["total"]))
    out = []
    for c in sorted(by_cohort.values(), key=lambda x: x["cohort_week"]):
        size = c["cohort_size"] or 1
        cells = sorted(c["cells"])
        cum = sum(t / size for _, t in cells)
        last = cells[-1][0]
        v_last = cells[-1][1] / size
        proj = {}
        for H in horizons:
            n = max(0, H - last)
            tail = v_last * r * (1 - r ** n) / (1 - r) if n > 0 else 0.0
            proj[f"w{H}"] = round(cum + tail, 4)
        out.append({"cohort_week": c["cohort_week"], "cohort_size": c["cohort_size"],
                    "weeks_observed": len(cells), "cost_to_date": round(cum, 4), "projected": proj})
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
    ap = argparse.ArgumentParser(
        description="Aurie projected lifetime cost per joined user to a fixed horizon -> JSON.")
    ap.add_argument("--max-week", type=int, default=8, help="Highest observed week_n to fit (default 8)")
    ap.add_argument("--horizons", default="52,104", help="Comma-separated projection horizons in weeks (default 52,104)")
    ap.add_argument("--start", help="Cohort start week (YYYY-MM-DD); default = first full week of cost data")
    ap.add_argument("--last-weeks", type=int, default=None,
                    help="Fit on only the most recent N complete cohort-weeks (default: all since data start)")
    args = ap.parse_args()
    horizons = [int(h) for h in args.horizons.split(",") if h.strip()]

    try:
        meta = bq.query(
            f"SELECT CAST(MIN(date) AS STRING) AS start, CAST(MAX(date) AS STRING) AS `end`, "
            f"COUNT(DISTINCT user_id) AS cost_users FROM `{COST}` WHERE user_id != 'Other'"
        )[0]
        cost_start, max_cost_date = meta["start"], meta["end"]
        start_week = args.start or bq.query(
            f"SELECT CAST(DATE_TRUNC(DATE_ADD(DATE('{cost_start}'), INTERVAL 6 DAY), WEEK(MONDAY)) AS STRING) AS w"
        )[0]["w"]

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
            avgs = _pool(rows)
            if not avgs:
                charts[key] = {"anchor": anchor, "scope": scope, "observed_curve": [],
                               "observed_cost_to_date": 0.0, "decay_ratio": None,
                               "fit_method": "no data in window", "converged_lifetime_per_user": 0.0,
                               "projections": [{"horizon_weeks": h, "projected_lifetime_per_user": 0.0,
                                                "tail_added": 0.0} for h in horizons], "cohorts": []}
                continue
            r, method = _fit_ratio(avgs)
            fit = _project(avgs, r, horizons)
            fit["fit_method"] = method
            charts[key] = {"anchor": anchor, "scope": scope,
                           "observed_curve": [{"week_n": a["week_n"], "avg_per_user": round(a["avg_per_user"], 4)}
                                              for a in avgs],
                           **fit, "cohorts": _cohort_projected(rows, r, horizons)}
    except BQUnavailable as e:
        print(f"BigQuery unavailable: {e}", file=sys.stderr)
        return 1
    except BQError as e:
        print(f"BigQuery error: {e}", file=sys.stderr)
        return 1

    out = {
        "cost_data_start": cost_start, "cost_data_end": max_cost_date,
        "cohort_start_week": start_week, "max_week": args.max_week, "horizons": horizons,
        "metric": "projected lifetime cost per joined user (observed cost-to-date + geometric-decay tail to horizon)",
        "caveat": ("PROJECTION — directional. Fit on ~5-6 weeks of data; assumes a geometric decay of "
                   "per-joined-user cost (retention already baked in); premium samples thin in later weeks."),
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
