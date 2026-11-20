#!/usr/bin/env python3
"""Aurie cost per user by acquisition channel / campaign — marketing-spend efficiency.

Which channels bring cheap vs expensive users. Joins per-user cost over a window to the
acquisition-channel dimension (aurie_user_channel, from eng:backfill-aurie-user-channel) and reports
average cost/user + volume three ways:

  by_channel  — coarse: paid (has a paid Appstack campaign) vs organic (no campaign) vs unknown
                (not yet in the channel dimension).
  by_campaign — per paid Appstack campaign (volume + avg cost/user), highest-volume first.
  by_platform — per sourcePlatform (AIiOS / AurieWeb / UserWeb …).

Aggregate-only — per-user cost is summed and averaged in BigQuery; the output carries channel
sums/averages + user counts, never a UID.

Sources (BigQuery, via GCP_KEY):
  aura-main.aurie_cost.aurie_per_user_daily_cost_prod   (env='prod' view; date, user_id, total_cost)
  aura-main.aurie_cost.aurie_user_channel          (user_id -> ai_appstack_campaign, source_platform; deduped)

Coverage guard: run `eng:backfill-aurie-user-channel` first if `missing` is non-zero (missing users
fall into the `unknown` channel).
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
DIM = f"{bq.PROJECT}.{bq.DATASET}.aurie_user_channel"

# Deduped channel dimension (append-only table can hold a rare duplicate).
_DIM_CTE = f"""
dim AS (
  SELECT user_id, ai_appstack_campaign, source_platform FROM (
    SELECT user_id, ai_appstack_campaign, source_platform,
           ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY fetched_at DESC) AS rn
    FROM `{DIM}`
  ) WHERE rn = 1
)
"""


def _win(args) -> str:
    """WHERE fragment (leading AND) for the cost window; '' = all days (default).
    `--last N` = last N days present in the store (matches eng:chart-aurie-daily-cost)."""
    if args.date_from or args.date_to:
        parts = []
        if args.date_from:
            parts.append(f"date >= DATE('{args.date_from}')")
        if args.date_to:
            parts.append(f"date <= DATE('{args.date_to}')")
        return " AND " + " AND ".join(parts)
    if args.last is not None:
        return (f" AND date >= (SELECT MIN(d) FROM "
                f"(SELECT DISTINCT date AS d FROM `{COST}` ORDER BY d DESC LIMIT {args.last}))")
    return ""  # --all / default = every day


def _per_user_cost_cte(win: str) -> str:
    return f"""
{_DIM_CTE},
uc AS (
  SELECT user_id, SUM(total_cost) AS cost
  FROM `{COST}` WHERE user_id != 'Other'{win}
  GROUP BY user_id
),
joined AS (
  SELECT uc.user_id, uc.cost,
         CASE WHEN dim.user_id IS NULL THEN 'unknown'
              WHEN dim.ai_appstack_campaign IS NOT NULL THEN 'paid'
              ELSE 'organic' END AS channel,
         dim.ai_appstack_campaign AS campaign,
         dim.source_platform AS source_platform
  FROM uc LEFT JOIN dim USING(user_id)
)
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
    ap = argparse.ArgumentParser(description="Aurie cost per user by acquisition channel -> JSON.")
    ap.add_argument("--last", type=int, default=None, help="cost window: last N days present (default: all)")
    ap.add_argument("--from", dest="date_from", help="cost window start, inclusive (YYYY-MM-DD)")
    ap.add_argument("--to", dest="date_to", help="cost window end, inclusive (YYYY-MM-DD)")
    ap.add_argument("--all", action="store_true", help="all cost data (default)")
    ap.add_argument("--top-campaigns", type=int, default=15, help="max campaigns in by_campaign (default 15)")
    args = ap.parse_args()

    win = _win(args)
    base = _per_user_cost_cte(win)
    try:
        meta = bq.query(
            f"SELECT CAST(MIN(date) AS STRING) AS lo, CAST(MAX(date) AS STRING) AS hi, "
            f"COUNT(DISTINCT date) AS days FROM `{COST}` WHERE user_id != 'Other'{win}"
        )[0]
        by_channel = bq.query(f"""WITH {base}
          SELECT channel, COUNT(*) AS users, ROUND(SUM(cost), 2) AS total_cost,
                 ROUND(AVG(cost), 4) AS avg_cost_per_user, ROUND(APPROX_QUANTILES(cost, 100)[OFFSET(50)], 4) AS median_cost
          FROM joined GROUP BY channel ORDER BY total_cost DESC""")
        by_campaign = bq.query(f"""WITH {base}
          SELECT campaign, COUNT(*) AS users, ROUND(SUM(cost), 2) AS total_cost,
                 ROUND(AVG(cost), 4) AS avg_cost_per_user
          FROM joined WHERE campaign IS NOT NULL
          GROUP BY campaign ORDER BY users DESC LIMIT {args.top_campaigns}""")
        by_platform = bq.query(f"""WITH {base}
          SELECT IFNULL(source_platform, '(unknown)') AS source_platform, COUNT(*) AS users,
                 ROUND(SUM(cost), 2) AS total_cost, ROUND(AVG(cost), 4) AS avg_cost_per_user
          FROM joined GROUP BY source_platform ORDER BY users DESC""")
        # Coverage is over the SAME windowed cohort the chart aggregates (apply {win}), so
        # `missing` / the backfill warning reflect the charted users, not all-time cost users.
        cov = bq.query(f"""
          WITH {_DIM_CTE},
          cost AS (SELECT DISTINCT user_id FROM `{COST}` WHERE user_id != 'Other'{win})
          SELECT COUNT(*) AS cost_users, COUNTIF(dim.user_id IS NOT NULL) AS with_channel
          FROM cost LEFT JOIN dim USING(user_id)""")[0]
        missing = cov["cost_users"] - cov["with_channel"]
    except BQUnavailable as e:
        print(f"BigQuery unavailable: {e}", file=sys.stderr)
        return 1
    except BQError as e:
        print(f"BigQuery error: {e}", file=sys.stderr)
        return 1

    out = {
        "cost_window": f"{meta['lo']}..{meta['hi']} ({meta['days']} days present)",
        "coverage": {"cost_users": cov["cost_users"],
                     "with_channel": cov["with_channel"], "missing": missing},
        "by_channel": by_channel,
        "by_campaign": by_campaign,
        "by_platform": by_platform,
    }
    out["skill_version"] = _skill_version()
    print(json.dumps(out, indent=2))
    if missing:
        print(f"\nWARNING: {missing} cost-table users are missing from the channel dimension — they "
              f"fall into `unknown`. Run eng:backfill-aurie-user-channel first.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
