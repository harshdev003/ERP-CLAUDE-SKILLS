#!/usr/bin/env python3
"""Free-tier Aurie cost economics: what we spend on a free user before they convert or go dormant.

Three groups, restricted to users whose AI signup is on/after the cost-data start (so their whole
free period is observed — earlier signups have censored free cost):

  converters           — have a subscription activation event. Free cost = cost on days in
                         [ai_created_at, first ai_subscription_activated) ("AI cost to acquire a
                         paying user") + days-to-convert.
  never_bought         — never activated AND never carried a paid/grace state in the cost table
                         (belt + suspenders, matching eng:chart-aurie-cohort-cost Chart C, so a
                         buyer whose activation wasn't captured as an event isn't counted as free).
                         Free cost = all their cost (always free); split dormant vs active.
  bought_no_activation — has a paid/grace cost row but no activation event, so cost-to-convert
                         isn't measurable. Reported as a count only, excluded from both headlines.

Emits headline averages, a cost-bucket distribution, and the conversion rate — all aggregate,
never a UID.

Sources (BigQuery, via GCP_KEY):
  aura-main.aurie_cost.aurie_per_user_daily_cost_prod   (env='prod' view; date, user_id, total_cost, subscription_state)
  aura-main.aurie_cost.aurie_user_ai_created       (user_id -> ai_created_at; deduped) — signup anchor
  mixpanel-prod-1.mixpanel_nessie_day_partitioned_963821.ai_subscription_activated (userid, time) — conversion

Coverage guard: run `eng:backfill-aurie-user-ai-created` first if `missing` is non-zero.
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

DORMANT_DAYS = 14  # a never-converter is "dormant" if last cost activity is older than this


def _per_user_cte(dormant_days: int) -> str:
    return f"""
WITH dim AS (
  SELECT user_id, DATE(ai_created_at) AS created FROM (
    SELECT user_id, ai_created_at,
           ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY fetched_at DESC) AS rn
    FROM `{DIM}`
  ) WHERE rn = 1
),
activ_all AS (  -- any activation at all = the user bought at some point
  SELECT DISTINCT userid AS user_id FROM `{ACTIVATED}` WHERE userid IS NOT NULL
),
activ AS (
  -- the measurable conversion = the FIRST activation on/after signup. Using a global MIN(time)
  -- would let a spurious pre-signup activation shadow a real later one and misroute the user to
  -- excluded_buyer; anchoring to ai_created_at picks the genuine post-signup conversion.
  SELECT a.userid AS user_id, DATE(MIN(a.time)) AS conv_date
  FROM `{ACTIVATED}` a JOIN dim ON dim.user_id = a.userid
  WHERE a.userid IS NOT NULL AND DATE(a.time) >= dim.created
  GROUP BY a.userid
),
cost AS (SELECT user_id, date AS d, total_cost FROM `{COST}` WHERE user_id != 'Other'),
-- "ever bought" = ANY activation OR ever carried a paid/grace state in the cost table
-- (matches eng:chart-aurie-cohort-cost Chart C, so a buyer whose activation wasn't captured as an
-- event isn't misclassified as a never-converter and their paid cost isn't summed as free spend).
ever_bought AS (
  SELECT user_id FROM activ_all
  UNION DISTINCT
  SELECT DISTINCT user_id FROM `{COST}`
  WHERE user_id != 'Other' AND subscription_state IN ('paid', 'grace')
),
meta AS (SELECT MIN(date) AS c0, MAX(date) AS c1 FROM `{COST}`),
u AS (
  -- signed up within the observable cost window (full free period observed) and has cost data
  SELECT dim.user_id, dim.created, a.conv_date,
         (a.conv_date IS NOT NULL) AS converted,
         (eb.user_id IS NOT NULL) AS ever_bought
  FROM dim
  LEFT JOIN activ a USING(user_id)
  LEFT JOIN ever_bought eb USING(user_id)
  WHERE dim.created >= (SELECT c0 FROM meta)
    AND dim.user_id IN (SELECT DISTINCT user_id FROM cost)
),
per_user AS (
  SELECT u.user_id,
         -- A "converter" is only measurable when conversion is on/after signup. When conv_date
         -- precedes created (a late or fallback-derived ai_created_at), cost-to-convert and
         -- days-to-convert are ill-defined (empty free window, negative gap), so route those to
         -- excluded_buyer (count only) instead of skewing the converter aggregates.
         CASE WHEN u.converted AND u.conv_date >= u.created THEN 'converters'
              WHEN NOT u.ever_bought THEN 'never_bought'
              ELSE 'excluded_buyer' END AS category,
         u.created, u.conv_date,
         -- cost while free: on/after signup, and before conversion for converters. The
         -- c.d >= created guard drops any pre-signup-dated cost (matches the sibling cohort SQL).
         ROUND(SUM(IF(c.d >= u.created AND (u.conv_date IS NULL OR c.d < u.conv_date),
                      c.total_cost, 0)), 4) AS free_cost,
         MAX(c.d) AS last_day,
         IF(u.converted AND u.conv_date >= u.created,
            DATE_DIFF(u.conv_date, u.created, DAY), NULL) AS days_to_convert,
         -- dormant = idle for at least N days through the latest data date (no cost in the
         -- trailing N days). DATE_DIFF >= N includes the boundary day c1-N as idle.
         ((NOT u.ever_bought)
          AND DATE_DIFF((SELECT c1 FROM meta), MAX(c.d), DAY) >= {dormant_days}) AS dormant
  FROM u LEFT JOIN cost c ON c.user_id = u.user_id
  GROUP BY u.user_id, u.created, u.conv_date, u.converted, u.ever_bought
)
"""


def _summary_sql(dormant_days: int) -> str:
    return _per_user_cte(dormant_days) + """
SELECT category,
  COUNT(*) AS n,
  ROUND(AVG(free_cost), 4) AS avg_free_cost,
  ROUND(APPROX_QUANTILES(free_cost, 2)[OFFSET(1)], 4) AS median_free_cost,
  ROUND(SUM(free_cost), 2) AS total_free_cost,
  ROUND(AVG(days_to_convert), 1) AS avg_days_to_convert,
  ROUND(APPROX_QUANTILES(days_to_convert, 2)[OFFSET(1)], 1) AS median_days_to_convert,
  COUNTIF(dormant) AS dormant_users,
  ROUND(SUM(IF(dormant, free_cost, 0)), 2) AS dormant_free_cost
FROM per_user GROUP BY category
"""


def _dist_sql(dormant_days: int) -> str:
    # Free-cost buckets per group (histogram): $0, (0,0.1], (0.1,0.5], (0.5,1], (1,2], >2.
    return _per_user_cte(dormant_days) + """
SELECT category,
  CASE
    WHEN free_cost <= 0 THEN '0'
    WHEN free_cost <= 0.1 THEN '0-0.1'
    WHEN free_cost <= 0.5 THEN '0.1-0.5'
    WHEN free_cost <= 1 THEN '0.5-1'
    WHEN free_cost <= 2 THEN '1-2'
    ELSE '2+'
  END AS bucket,
  COUNT(*) AS users, ROUND(SUM(free_cost), 2) AS cost
FROM per_user
WHERE category IN ('converters', 'never_bought')
GROUP BY category, bucket
ORDER BY category, MIN(free_cost)
"""


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
    ap = argparse.ArgumentParser(description="Free-tier Aurie cost to conversion vs never-converters -> JSON.")
    ap.add_argument("--dormant-days", type=int, default=DORMANT_DAYS,
                    help=f"a never-converter is dormant if idle this many days (default {DORMANT_DAYS})")
    args = ap.parse_args()

    try:
        meta = bq.query(
            f"SELECT CAST(MIN(date) AS STRING) AS start, CAST(MAX(date) AS STRING) AS `end` FROM `{COST}`"
        )[0]
        rows = bq.query(_summary_sql(args.dormant_days))
        dist = bq.query(_dist_sql(args.dormant_days))
        cov = bq.query(f"""
          WITH dim AS (
            SELECT DISTINCT user_id FROM `{DIM}`
          ), cost AS (SELECT DISTINCT user_id FROM `{COST}` WHERE user_id != 'Other')
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

    groups = {r["category"]: r for r in rows}
    conv = groups.get("converters", {})
    never = groups.get("never_bought", {})
    excluded = groups.get("excluded_buyer", {})
    n_conv = conv.get("n", 0) or 0
    n_never = never.get("n", 0) or 0
    n_excl = excluded.get("n", 0) or 0
    total = n_conv + n_never + n_excl

    out = {
        "cost_data_start": meta["start"],
        "cost_data_end": meta["end"],
        "cohort": "users whose AI signup is on/after the cost-data start (full free period observed)",
        "dormant_days": args.dormant_days,
        "coverage": {"cost_users": cov["cost_users"],
                     "with_ai_created": cov["with_ai_created"], "missing": missing},
        # ever-bought rate = ALL buyers / ALL cohort users. Numerator counts every buyer —
        # measurable converters PLUS excluded_buyer (real buyers: a paid/grace cost state, or an
        # activation dated before signup) — since both groups did buy; denominator is the whole
        # cohort (converters + never_bought + excluded_buyer). Not n_conv/(n_conv+n_never), which
        # would drop real buyers from both terms.
        "conversion_rate": round((n_conv + n_excl) / total, 4) if total else None,
        "converters": conv,
        "never_bought": never,
        "excluded_buyer_count": n_excl,  # buyers excluded from headlines (no measurable conv date)
        "distribution": [
            {"group": d["category"], "bucket": d["bucket"], "users": d["users"], "cost": d["cost"]}
            for d in dist
        ],
    }
    out["skill_version"] = _skill_version()
    print(json.dumps(out, indent=2))
    if missing:
        print(f"\nWARNING: {missing} cost-table users are missing ai_created_at — they're excluded "
              f"from the observable-signup cohort. Run eng:backfill-aurie-user-ai-created first.",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
