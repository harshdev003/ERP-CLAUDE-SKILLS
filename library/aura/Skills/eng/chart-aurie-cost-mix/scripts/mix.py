#!/usr/bin/env python3
"""Decompose Aurie cost into LLM vs voice, by subscription segment and by tenure week.

Answers "is the cost (and the premium week-0 spike) deep-context chat or voice minutes?" — which
lever (LLM context trimming vs voice caps) moves margin most, and for which users.

Two lenses (both aggregate-only, never a UID):
  by_segment — over a window, LLM vs voice per subscription_state (free/trial/paid/grace).
  by_week    — over ALL cost data, LLM vs voice by weeks-since-AI-signup (the lifecycle mix);
               shows whether the week-0 spike skews LLM or voice.

`llm_cost` and `voice_cost` are stored per user/day in the source table, so this is a pure roll-up.

Sources (BigQuery, via GCP_KEY):
  aura-main.aurie_cost.aurie_per_user_daily_cost_prod   (env='prod' view; date, user_id, llm_cost, voice_cost, total_cost, subscription_state)
  aura-main.aurie_cost.aurie_user_ai_created       (user_id -> ai_created_at; deduped on read) — by_week only

Coverage guard (by_week): run `eng:backfill-aurie-user-ai-created` first if `missing` is non-zero.
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

_DIM_CTE = f"""
dim AS (
  SELECT user_id, DATE(ai_created_at) AS created FROM (
    SELECT user_id, ai_created_at,
           ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY fetched_at DESC) AS rn
    FROM `{DIM}`
  ) WHERE rn = 1
)
"""


def _win(args) -> str:
    """WHERE fragment (leading AND) selecting the segment-lens window; '' = all days.
    `--last N` = last N days present in the store (matches eng:chart-aurie-daily-cost)."""
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


def _segment_sql(win: str) -> str:
    return f"""
SELECT subscription_state,
       ROUND(SUM(llm_cost), 2) AS llm_cost,
       ROUND(SUM(voice_cost), 2) AS voice_cost,
       ROUND(SUM(total_cost), 2) AS total_cost,
       ROUND(SAFE_DIVIDE(SUM(voice_cost), SUM(total_cost)), 4) AS voice_share,
       COUNT(DISTINCT user_id) AS users
FROM `{COST}`
WHERE user_id != 'Other'{win}
GROUP BY subscription_state
ORDER BY total_cost DESC
"""


def _week_sql(max_week: int) -> str:
    return f"""
WITH {_DIM_CTE}
SELECT DIV(DATE_DIFF(c.date, dim.created, DAY), 7) AS week_n,
       ROUND(SUM(c.llm_cost), 2) AS llm_cost,
       ROUND(SUM(c.voice_cost), 2) AS voice_cost,
       ROUND(SUM(c.total_cost), 2) AS total_cost,
       ROUND(SAFE_DIVIDE(SUM(c.voice_cost), SUM(c.total_cost)), 4) AS voice_share,
       COUNT(*) AS user_days
FROM `{COST}` c JOIN dim USING(user_id)
WHERE c.user_id != 'Other' AND DATE_DIFF(c.date, dim.created, DAY) >= 0
GROUP BY week_n
HAVING week_n BETWEEN 0 AND {max_week}
ORDER BY week_n
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
    ap = argparse.ArgumentParser(description="Aurie LLM vs voice cost split by segment + tenure -> JSON.")
    ap.add_argument("--last", type=int, default=None, help="segment lens: most recent N days present (default 30)")
    ap.add_argument("--from", dest="date_from", help="segment lens window start, inclusive (YYYY-MM-DD)")
    ap.add_argument("--to", dest="date_to", help="segment lens window end, inclusive (YYYY-MM-DD)")
    ap.add_argument("--all", action="store_true", help="segment lens: every day in the store")
    ap.add_argument("--max-week", type=int, default=8, help="tenure lens: highest week_n (default 8)")
    args = ap.parse_args()

    win = _win(args)
    try:
        meta = bq.query(
            f"SELECT CAST(MIN(date) AS STRING) AS start, CAST(MAX(date) AS STRING) AS `end` "
            f"FROM `{COST}`"
        )[0]
        by_segment = bq.query(_segment_sql(win))
        # Label the segment window from the dates actually included (not the requested N),
        # so a short store or gap days can't make the label overstate the span.
        wb = bq.query(f"SELECT CAST(MIN(date) AS STRING) AS lo, CAST(MAX(date) AS STRING) AS hi, "
                      f"COUNT(DISTINCT date) AS days FROM `{COST}` WHERE user_id != 'Other'{win}")[0]
        by_week = bq.query(_week_sql(args.max_week))
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
        "cost_data_end": meta["end"],
        "segment_window": f"{wb['lo']}..{wb['hi']} ({wb['days']} days present)",
        "tenure_scope": "all cost data (weeks since AI signup)",
        "coverage": {"cost_users": cov["cost_users"],
                     "with_ai_created": cov["with_ai_created"], "missing": missing},
        "by_segment": by_segment,
        "by_week": by_week,
    }
    out["skill_version"] = _skill_version()
    print(json.dumps(out, indent=2))
    if missing:
        print(f"\nWARNING: {missing} cost-table users are missing ai_created_at — the by_week "
              f"lens undercounts them. Run eng:backfill-aurie-user-ai-created first.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
