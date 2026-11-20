#!/usr/bin/env python3
"""Daily Aurie contribution margin: amortized net revenue vs variable cost.

Answers "are we making money, day to day?" — the daily companion to the cohort payback view
(`eng:chart-aurie-cohort-payback`). For each day on the cost-data spine:

  amort_net_revenue = (Sigma amortized gross revenue active that day) * (1 - fee_rate)
  paid_contribution = amort_net_revenue - paid_cost      (subscription_state = 'paid')
  free_drag         = nonpaid_cost                       (free/trial/grace/other)
  net               = amort_net_revenue - total_cost

Revenue basis — amortized (recognized), NOMINAL period length (not the raw entitlement window):
  period_days = 365 (year) / 30 (month) / GREATEST(TIMESTAMP_DIFF(expiration_at,
  purchase_at, DAY), 1) as a fallback for null/other period tokens. Some `expiration_at` values
  are corrupt (minutes after purchase); amortizing by the nominal period avoids dumping a whole
  period of revenue onto day 0. A period contributes its daily_gross to every day in
  [DATE(purchase_at), DATE(purchase_at) + CAST(period_days AS INT64) days) — half-open.

Fee rate is a configurable assumption (`--fee-rate`, default 0.30 — a blended in-app-purchase
store-fee estimate), applied uniformly to amortized gross revenue.

`total_cost` is variable cost only (LLM + voice); no fixed/overhead allocation.

Aggregate-only. Per-user net is computed and ranked **inside BigQuery** for the concentration
section; the output carries counts/shares, **never a user_id**.

Env-filtered (PROD-3589 Part B): BOTH sides of the margin are restricted to the same prod
population. Cost comes from the prod-only `aurie_per_user_daily_cost_prod` view; revenue
(`aurie_user_subscription`) is inner-joined to the deduped `env='prod'` user set before
amortizing — otherwise dev/test subscription revenue (and revenue for users never tagged in
`aurie_user_env`) would count while their cost is excluded, overstating net. The env dimension
is append-only with no unique key, so every prod-user select dedups to the latest row per user
(`ROW_NUMBER() … ORDER BY fetched_at DESC`). A second, small query against the *base* table +
the deduped `aurie_user_env` dimension computes the `excluded` block (dev/unknown/untagged/Other
$ for the same window) so the report can show a prod-exclusions callout alongside the numbers.

Sources (BigQuery, via GCP_KEY):
  aura-main.aurie_cost.aurie_per_user_daily_cost_prod   (env='prod' view; date, user_id, total_cost, subscription_state)
  aura-main.aurie_cost.aurie_user_subscription          (user_id, purchase_at, expiration_at, revenue, period — prod-filtered)
  aura-main.aurie_cost.aurie_user_env                   (user_id -> env; revenue prod filter + excluded-$ query, deduped)
  aura-main.aurie_cost.aurie_per_user_daily_cost        (base table — excluded-$ query only)
"""
from __future__ import annotations

import argparse
import datetime
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
BASE_COST = f"{bq.PROJECT}.{bq.DATASET}.aurie_per_user_daily_cost"  # excluded-$ callout only
ENV_TABLE = f"{bq.PROJECT}.{bq.DATASET}.aurie_user_env"             # cost + revenue prod filter, excluded-$ callout

DEFAULT_FEE_RATE = 0.30

# Deduped prod-user set (PROD-3589 Part B). `aurie_user_env` is append-only with no unique key,
# so a user_id can have >1 row; take the latest (`ROW_NUMBER() … ORDER BY fetched_at DESC`) before
# filtering to env='prod'. Cost already comes from the prod view; the revenue side (SUB) must be
# restricted to the SAME population, otherwise dev/test subscription revenue (and revenue for users
# never tagged in aurie_user_env) would count while their cost is excluded — overstating net.
_PROD_USERS_CTE = f"""
prod_users AS (
  SELECT user_id FROM (
    SELECT user_id, env,
           ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY fetched_at DESC) AS rn
    FROM `{ENV_TABLE}`
  ) WHERE rn = 1 AND env = 'prod'
)"""

# Shared amortization CTEs: the nominal-period daily rate, and its per-day contribution to
# the requested date spine. `rev_ud` is the (user_id, date, gross) grain both the daily
# aggregate and the per-user concentration section fan out from. `periods` is inner-joined to
# `prod_users` so revenue is on the identical prod population as cost.
_AMORT_CTES = f"""
{_PROD_USERS_CTE},
periods AS (
  SELECT s.user_id, DATE(s.purchase_at) AS p_start, s.revenue,
         CASE LOWER(s.period)
           WHEN 'year' THEN 365
           WHEN 'month' THEN 30
           ELSE GREATEST(TIMESTAMP_DIFF(s.expiration_at, s.purchase_at, DAY), 1)
         END AS period_days
  FROM `{SUB}` s
  JOIN prod_users pu USING (user_id)
),
sub AS (
  SELECT user_id, p_start,
         DATE_ADD(p_start, INTERVAL CAST(period_days AS INT64) DAY) AS p_end_excl,
         revenue / period_days AS daily_gross
  FROM periods
),
rev_ud AS (
  SELECT s.user_id, sp.d, s.daily_gross AS gross
  FROM spine sp
  JOIN sub s ON sp.d >= s.p_start AND sp.d < s.p_end_excl
)"""


def _spine_cte(win: str) -> str:
    where = f" WHERE 1=1{win}" if win else ""
    return f"spine AS (SELECT DISTINCT date AS d FROM `{COST}`{where})"


def _win(args) -> str:
    """WHERE fragment (leading ' AND', empty = all days) selecting the daily-cost date spine.
    `--last N` = the last N days PRESENT in the store (matches the sibling cost charts)."""
    if args.all:
        return ""
    n = args.last if args.last is not None else 30
    return (f" AND date >= (SELECT MIN(d) FROM "
            f"(SELECT DISTINCT date AS d FROM `{COST}` ORDER BY d DESC LIMIT {n}))")


def _daily_sql(win: str) -> str:
    return f"""
WITH {_spine_cte(win)},
{_AMORT_CTES},
rev_by_day AS (SELECT d, SUM(gross) AS gross_rev FROM rev_ud GROUP BY d),
cost_by_day AS (
  SELECT date AS d,
         SUM(total_cost) AS total_cost,
         SUM(IF(subscription_state = 'paid', total_cost, 0)) AS paid_cost
  FROM `{COST}`
  GROUP BY date
)
SELECT CAST(sp.d AS STRING) AS date,
       IFNULL(r.gross_rev, 0) AS gross_rev,
       ROUND(c.total_cost, 4) AS total_cost,
       ROUND(c.paid_cost, 4) AS paid_cost
FROM spine sp
JOIN cost_by_day c ON c.d = sp.d
LEFT JOIN rev_by_day r ON r.d = sp.d
ORDER BY sp.d
"""


def _user_net_cte(win: str) -> str:
    """`user_net`: per real user (Other excluded), net = amortized net revenue - cost, over the
    window. Fee rate is applied via the `@fee_rate` query parameter."""
    return f"""
{_spine_cte(win)},
{_AMORT_CTES},
user_rev AS (SELECT user_id, SUM(gross) AS gross_rev FROM rev_ud GROUP BY user_id),
user_cost AS (
  SELECT c.user_id, SUM(c.total_cost) AS cost
  FROM `{COST}` c
  JOIN spine sp ON sp.d = c.date
  WHERE c.user_id != 'Other'
  GROUP BY c.user_id
),
user_net AS (
  SELECT COALESCE(uc.user_id, ur.user_id) AS user_id,
         IFNULL(ur.gross_rev, 0) * (1 - @fee_rate) - IFNULL(uc.cost, 0) AS net
  FROM user_cost uc FULL OUTER JOIN user_rev ur ON uc.user_id = ur.user_id
)"""


def _stats_sql(win: str) -> str:
    return f"""
WITH {_user_net_cte(win)}
SELECT COUNT(*) AS total_users, COUNTIF(net < 0) AS negative_users,
       ROUND(SUM(IF(net < 0, -net, 0)), 4) AS total_loss
FROM user_net
"""


def _loss_curve_sql(win: str) -> str:
    # NTILE(100) DESC by loss: pctile 1 = the worst 1% of loss-making users. Cumulating from
    # pctile 1 gives "worst top X% of loss-makers' share of total loss" (mirrors the Pareto
    # pattern in eng:chart-aurie-cost-concentration, restricted to net-negative users).
    return f"""
WITH {_user_net_cte(win)},
loss_rows AS (SELECT -net AS loss FROM user_net WHERE net < 0),
b AS (SELECT loss, NTILE(100) OVER (ORDER BY loss DESC) AS pctile FROM loss_rows),
per AS (SELECT pctile, SUM(loss) AS loss FROM b GROUP BY pctile)
SELECT pctile, SUM(loss) OVER (ORDER BY pctile) AS cum_loss
FROM per ORDER BY pctile
"""


# The `Other` row's columns, in display order. It is the per-COLUMN reconciling remainder
# (authoritative total - attributed) for LLM and every voice vendor, so the LLM column is
# usually its largest part — not a voice-only remainder. `voice_livekit_platform` is signed
# (unused LiveKit capacity, reversed once a month crosses into overage).
OTHER_COMPONENTS = (
    ("llm", "llm_cost"),
    ("elevenlabs_agent", "voice_11labs_agent"),
    ("elevenlabs_intelligence", "voice_11labs_intelligence"),
    ("elevenlabs_livekit", "voice_11labs_livekit"),
    ("cartesia", "voice_cartesia"),
    ("fish", "voice_fish"),
    ("inworld", "voice_inworld"),
    ("deepgram", "voice_deepgram"),
    ("livekit", "voice_livekit"),
    ("livekit_unused_capacity", "voice_livekit_platform"),
)


def _excluded_sql(win: str) -> str:
    """dev/unknown/untagged/Other $ over the same window, straight from the BASE table joined
    to the deduped `aurie_user_env` — i.e. exactly what `aurie_per_user_daily_cost_prod`'s INNER
    JOIN drops. `untagged_cost` is cost from a user_id with no `aurie_user_env` row yet (a coverage
    gap in the env backfill, not a real dev/QA classification — see that skill's SKILL.md caveat).
    The env table is deduped to the latest row per user (append-only, no unique key) so a duplicate
    dimension row can't fan out the base cost rows and overstate the excluded figures.
    The `other_<component>` columns split the `Other` row by cost column (OTHER_COMPONENTS)."""
    other_cols = ",\n".join(
        f"  SUM(CASE WHEN b.user_id = 'Other' THEN b.{col} ELSE 0 END) AS other_{key}"
        for key, col in OTHER_COMPONENTS
    )
    return f"""
WITH env_latest AS (
  SELECT user_id, env FROM (
    SELECT user_id, env,
           ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY fetched_at DESC) AS rn
    FROM `{ENV_TABLE}`
  ) WHERE rn = 1
)
SELECT
  SUM(CASE WHEN e.env = 'dev' THEN b.total_cost ELSE 0 END) AS dev_cost,
  SUM(CASE WHEN e.env = 'unknown' THEN b.total_cost ELSE 0 END) AS unknown_cost,
  SUM(CASE WHEN b.user_id = 'Other' THEN b.total_cost ELSE 0 END) AS other_cost,
  SUM(CASE WHEN e.user_id IS NULL AND b.user_id != 'Other' THEN b.total_cost ELSE 0 END) AS untagged_cost,
  SUM(b.total_cost) AS all_cost,
{other_cols}
FROM `{BASE_COST}` b
LEFT JOIN env_latest e USING (user_id)
WHERE 1=1{win}
"""


# Voice paths allocated per user. A day where one billed and every user row carries $0
# for it is a collapsed path: the store keeps the day and its dollars sit on `Other`
# (PROD-6992). Intelligence Service TTS and unused LiveKit capacity are excluded — they
# reach no user by construction, every day.
COLLAPSE_PATHS = (
    ("elevenlabs_agent", "voice_11labs_agent"),
    ("elevenlabs_livekit", "voice_11labs_livekit"),
    ("cartesia", "voice_cartesia"),
    ("fish", "voice_fish"),
    ("inworld", "voice_inworld"),
    ("deepgram", "voice_deepgram"),
    ("livekit", "voice_livekit"),
)
# Below this a vendor-day is rounding dust (a $0.04 LiveKit accrual on a no-traffic day),
# not a collapse. Days under it are still counted, in `below_floor_count`.
COLLAPSE_FLOOR_USD = 1.00


def _collapsed_paths_sql(win: str) -> str:
    """Vendor-days in the window where a per-user voice path billed and reached no user."""
    structs = ", ".join(
        f"STRUCT('{key}' AS path, {col} AS v)" for key, col in COLLAPSE_PATHS)
    return f"""
WITH d AS (
  SELECT b.date, p.path, SUM(p.v) AS total,
         SUM(IF(b.user_id != 'Other', p.v, 0)) AS attributed
  FROM `{BASE_COST}` b, UNNEST([{structs}]) p
  WHERE 1=1{win}
  GROUP BY b.date, p.path
)
SELECT CAST(date AS STRING) AS date, path, total
FROM d WHERE total > 0.005 AND attributed < 0.0001
ORDER BY total DESC
"""


def _collapsed_summary(rows: list) -> dict:
    """Aggregate the collapsed vendor-days into the `excluded.collapsed_voice_paths` block."""
    flagged = [{"date": r["date"], "path": r["path"], "dollars": round(r["total"], 4)}
               for r in rows if r["total"] >= COLLAPSE_FLOOR_USD]
    dust = [r for r in rows if r["total"] < COLLAPSE_FLOOR_USD]
    return {
        "floor_usd": COLLAPSE_FLOOR_USD,
        "count": len(flagged),
        "dollars": round(sum(f["dollars"] for f in flagged), 4),
        "days": flagged,
        "below_floor_count": len(dust),
        "below_floor_dollars": round(sum(r["total"] for r in dust), 4),
    }


def _loss_share(curve: list, total_loss: float, pct: int) -> float | None:
    rows = [r for r in curve if r["pctile"] <= pct]
    if not rows or not total_loss:
        return None
    return round(rows[-1]["cum_loss"] / total_loss, 4)


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
        description="Daily Aurie contribution margin (amortized net revenue vs variable cost) -> JSON."
    )
    ap.add_argument("--all", action="store_true", default=True,
                    help="every day in the store (default)")
    ap.add_argument("--last", type=int, default=None,
                    help="most recent N days present in the store (overrides --all)")
    ap.add_argument("--fee-rate", type=float, default=DEFAULT_FEE_RATE,
                    help=f"blended IAP store-fee rate applied to amortized gross revenue "
                         f"(default {DEFAULT_FEE_RATE})")
    ap.add_argument("--json", dest="json_path", default=None,
                    help="write JSON to this path instead of stdout")
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
    if args.last is not None:
        args.all = False

    win = _win(args)
    params = {"fee_rate": args.fee_rate}
    try:
        meta = bq.query(
            f"SELECT CAST(MIN(date) AS STRING) AS start, CAST(MAX(date) AS STRING) AS `end` "
            f"FROM `{COST}`"
        )[0]
        rows = bq.query(_daily_sql(win))
        stats = bq.query(_stats_sql(win), params=params)[0]
        curve = bq.query(_loss_curve_sql(win), params=params)
        excluded_row = bq.query(_excluded_sql(win))[0]
        collapsed_rows = bq.query(_collapsed_paths_sql(win))
    except BQUnavailable as e:
        print(f"BigQuery unavailable: {e}", file=sys.stderr)
        return 1
    except BQError as e:
        print(f"BigQuery error: {e}", file=sys.stderr)
        return 1

    if not rows:
        print("No cost rows in the selected window.", file=sys.stderr)
        return 1

    fee_rate = args.fee_rate
    daily = []
    cum_net = 0.0
    cumulative = []
    for r in rows:
        amort_net_revenue = round(r["gross_rev"] * (1 - fee_rate), 4)
        total_cost = r["total_cost"]
        paid_cost = r["paid_cost"]
        nonpaid_cost = round(total_cost - paid_cost, 4)
        paid_contribution = round(amort_net_revenue - paid_cost, 4)
        free_drag = nonpaid_cost
        net = round(amort_net_revenue - total_cost, 4)
        cum_net = round(cum_net + net, 4)
        daily.append({
            "date": r["date"],
            "amort_net_revenue": amort_net_revenue,
            "total_cost": total_cost,
            "paid_cost": paid_cost,
            "nonpaid_cost": nonpaid_cost,
            "paid_contribution": paid_contribution,
            "free_drag": free_drag,
            "net": net,
        })
        cumulative.append({"date": r["date"], "cumulative_net": cum_net})

    n_days = len(daily)
    sum_paid_contribution = round(sum(d["paid_contribution"] for d in daily), 4)
    sum_free_drag = round(sum(d["free_drag"] for d in daily), 4)
    sum_net = round(sum(d["net"] for d in daily), 4)
    segment_summary = {
        "days": n_days,
        "paid_contribution": sum_paid_contribution,
        "free_drag": sum_free_drag,
        "net": sum_net,
        "avg_paid_contribution_per_day": round(sum_paid_contribution / n_days, 4),
        "avg_free_drag_per_day": round(sum_free_drag / n_days, 4),
        "avg_net_per_day": round(sum_net / n_days, 4),
    }

    total_loss = stats["total_loss"] or 0.0
    concentration = {
        "total_users": stats["total_users"],
        "negative_net_users": stats["negative_users"],
        "total_loss": total_loss,
        "worst_loss_share": {
            "top_1_pct": _loss_share(curve, total_loss, 1),
            "top_5_pct": _loss_share(curve, total_loss, 5),
            "top_10_pct": _loss_share(curve, total_loss, 10),
        },
    }

    # dev/QA/untagged $ excluded from the prod-only numbers above, over the same window
    # (PROD-3589 Part B) — drives the report's "dev/QA excluded" callout.
    dev_cost = round(excluded_row["dev_cost"] or 0, 4)
    unknown_cost = round(excluded_row["unknown_cost"] or 0, 4)
    other_cost = round(excluded_row["other_cost"] or 0, 4)
    untagged_cost = round(excluded_row["untagged_cost"] or 0, 4)
    all_cost = round(excluded_row["all_cost"] or 0, 4)
    total_excluded = round(dev_cost + unknown_cost + other_cost + untagged_cost, 4)
    excluded = {
        "dev_cost": dev_cost,
        "unknown_cost": unknown_cost,
        "other_cost": other_cost,
        "untagged_cost": untagged_cost,
        "total_excluded": total_excluded,
        "all_cost": all_cost,
        "pct_of_all": round(100 * total_excluded / all_cost, 4) if all_cost else None,
        # What the `Other` row is made of, per cost column; sums to `other_cost`.
        "other_by_component": {
            key: round(excluded_row[f"other_{key}"] or 0, 4) for key, _ in OTHER_COMPONENTS
        },
        # Vendor-days whose whole bill reached no user, so it sits inside `other_cost`.
        "collapsed_voice_paths": _collapsed_summary(collapsed_rows),
    }

    out = {
        "daily": daily,
        "cumulative": cumulative,
        "segment_summary": segment_summary,
        "concentration": concentration,
        "excluded": excluded,
        "meta": {
            "window": f"last {args.last} days present" if args.last else "all days present",
            "cost_data_start": meta["start"],
            "cost_data_end": meta["end"],
            "fee_rate": fee_rate,
            "generated": datetime.datetime.now(datetime.timezone.utc)
                .isoformat(timespec="seconds"),
            "source_tables": [COST, SUB],
            "excluded_source_tables": [BASE_COST, ENV_TABLE],
        },
    }

    out["skill_version"] = _skill_version()

    text = json.dumps(out, indent=2)
    if args.json_path:
        with open(args.json_path, "w") as f:
            f.write(text)
        print(f"Wrote {args.json_path} ({n_days} days)", file=sys.stderr)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
