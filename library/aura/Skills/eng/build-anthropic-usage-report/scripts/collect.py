#!/usr/bin/env python3
"""Collect the four datasets the Claude usage report charts, as one JSON blob.

Split from `render.py` so the numbers are inspectable on their own and can feed
something other than the HTML later. This half needs BigQuery; the render half
needs nothing but stdlib.

Every section is built from `daily_user_usage` valued at the rate **in force on
the usage date** (`model_pricing` joined on a LEAD-derived validity window), and
every section is bounded by what `harvest_log` says was actually read. A window
with unharvested days reports a lower bound rather than a total — an idle day and
a missing day both contribute zero rows, and only one of them means the figure is
wrong.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bq  # noqa: E402

PROJECT = "aura-main"
DS = "anthropic_usage"
USAGE = f"`{PROJECT}.{DS}.daily_user_usage`"
SEATS = f"`{PROJECT}.{DS}.member_seat`"
PRICES = f"`{PROJECT}.{DS}.model_pricing`"
LOG = f"`{PROJECT}.{DS}.harvest_log`"

# Seat list prices, annual billing. The only figure here that is not measured.
SEAT_USD = {"premium": 100.0, "standard": 20.0}

# One person can hold several seats. Every per-person view groups by this map, so
# their usage, overage and seat cost add up under one name instead of reading as
# several light users. Seat cost stays per seat: each seat is billed. An email
# not listed here is its own person.
SEAT_PEOPLE = {
    "<redacted-email>": "Jerome Curlier",
    "<redacted-email>": "Jerome Curlier",
    "<redacted-email>": "Jerome Curlier",
    "<redacted-email>": "Jerome Curlier",
}


def person_sql(col: str) -> str:
    """SQL mapping a seat email column to its person (the email itself when unmapped)."""
    if not SEAT_PEOPLE:
        return col
    cases = " ".join(f"WHEN '{e}' THEN '{n}'" for e, n in sorted(SEAT_PEOPLE.items()))
    return f"(CASE LOWER({col}) {cases} ELSE {col} END)"


# The first day the export holds anything for this org.
EARLIEST = "2026-03-02"

# Usage priced per row at the rate in force that day. Each price row carries an
# explicit validity window, so exactly one matches: a plain join on model alone
# fans out to one row per rate once a second rate exists, doubling every value,
# and taking the newest rate instead silently reprices all of history.
VALUED = f"""
  SELECT u.*,
         {person_sql("u.user_email")} AS person,
         u.prompt_tokens + u.completion_tokens AS all_tokens,
         (u.uncached_input_tokens   * p.input_usd_per_mtok
        + u.cache_read_tokens       * p.cache_read_usd_per_mtok
        + u.cache_write_5m_tokens   * p.cache_write_5m_usd_per_mtok
        + u.cache_write_1h_tokens   * p.cache_write_1h_usd_per_mtok
        + u.completion_tokens       * p.output_usd_per_mtok) / 1000000 AS api_value_usd
  FROM {USAGE} u
  LEFT JOIN (
    SELECT model, input_usd_per_mtok, output_usd_per_mtok, cache_read_usd_per_mtok,
           cache_write_5m_usd_per_mtok, cache_write_1h_usd_per_mtok, effective_from,
           LEAD(effective_from) OVER (PARTITION BY model ORDER BY effective_from)
             AS effective_to
    FROM {PRICES}
  ) p
    ON  p.model = u.model
    AND u.usage_date >= p.effective_from
    AND (p.effective_to IS NULL OR u.usage_date < p.effective_to)
"""

LATEST_SEATS = f"""
  SELECT user_email, seat_tier, display_name, role
  FROM {SEATS}
  WHERE as_of_date = (SELECT MAX(as_of_date) FROM {SEATS})
"""


def _f(v) -> float:
    return float(v or 0)


def _i(v) -> int:
    return int(v or 0)


def last_harvested() -> str | None:
    r = bq.query(f"SELECT MAX(harvest_date) d FROM {LOG}", PROJECT)
    return str(r[0]["d"]) if r and r[0]["d"] else None


def coverage(since: str, through: str) -> dict:
    """Gaps inside the window, and separately how far behind the data is.

    The two are different failures and must not be conflated. A gap means the
    figures are a lower bound. Lag means they are complete but old. Folding lag
    into the gap count made every report read INCOMPLETE forever, because the
    export settles a day late and the newest day is never harvestable yet —
    which trains the reader to ignore the one banner that matters.
    """
    rows = bq.query(
        f"SELECT COUNT(DISTINCT harvest_date) n, MIN(harvest_date) lo, MAX(harvest_date) hi "
        f"FROM {LOG} WHERE harvest_date BETWEEN DATE(@s) AND DATE(@e)",
        PROJECT, params={"s": since, "e": through})
    r = rows[0] if rows else {"n": 0, "lo": None, "hi": None}
    want = (dt.date.fromisoformat(through) - dt.date.fromisoformat(since)).days + 1
    newest = last_harvested()
    # The export refreshes daily with a one-day delay, so one unsettled day at
    # the tail is normal rather than a miss.
    settled = dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=1)
    lag = (settled - dt.date.fromisoformat(newest)).days if newest else None
    return {"days_harvested": _i(r["n"]), "days_in_window": want,
            "complete": _i(r["n"]) >= want,
            "first": str(r["lo"]) if r["lo"] else None,
            "last": str(r["hi"]) if r["hi"] else None,
            "newest_harvested": newest,
            "lag_days": lag,
            "stale": bool(lag is not None and lag > 2)}


def seat_fit(since: str, through: str, days: int) -> list:
    """Per-person value against seat price, projected to a 30-day month.

    A person with several seats (SEAT_PEOPLE) is one row: their seat prices add up,
    and active days are distinct days across all their seats.
    """
    rows = bq.query(f"""
      WITH v AS ({VALUED}),
      s AS (SELECT {person_sql("user_email")} AS person,
                   LOWER(IFNULL(seat_tier, 'unknown')) AS tier, role
            FROM ({LATEST_SEATS})),
      ps AS (SELECT person, ARRAY_AGG(tier ORDER BY tier) AS tiers, MAX(role) AS role
             FROM s GROUP BY person),
      u AS (SELECT person,
                   SUM(all_tokens)    AS tokens,
                   SUM(api_value_usd) AS api_value,
                   SUM(net_spend_usd) AS billed,
                   COUNT(DISTINCT usage_date) AS active_days
            FROM v WHERE usage_date BETWEEN DATE(@s) AND DATE(@e)
            GROUP BY person)
      SELECT ps.person, ps.tiers, ps.role, u.tokens, u.api_value, u.billed, u.active_days
      FROM ps LEFT JOIN u USING (person)
    """, PROJECT, params={"s": since, "e": through})
    return seat_rows(rows, days)


def seat_rows(rows: list, days: int) -> list:
    scale = 30.0 / days if days else 1.0
    out = []
    for r in rows:
        tiers = list(r["tiers"] or ["unknown"])
        seat = sum(SEAT_USD.get(t, 0.0) for t in tiers)
        val = _f(r["api_value"]) * scale
        out.append({
            "user": r["person"], "tier": tiers[0] if len(set(tiers)) == 1 else "mixed",
            "tiers": tiers, "role": r["role"],
            "seat_usd": seat,
            "value_per_month": round(val, 2),
            "ratio": round(val / seat, 1) if seat else None,
            "tokens": _i(r["tokens"]),
            "billed": round(_f(r["billed"]), 2),
            "active_days": _i(r["active_days"]),
        })
    out.sort(key=lambda x: -x["value_per_month"])
    return out


def adoption(since: str, through: str, grain: str) -> list:
    """Tokens, value and active users per period.

    Rendered as TWO plots, never one with two y-axes. Tokens and people are
    different scales and a shared axis would misstate both.
    """
    bucket = {"day": "usage_date",
              "week": "DATE_TRUNC(usage_date, WEEK(MONDAY))",
              "month": "DATE_TRUNC(usage_date, MONTH)"}[grain]
    rows = bq.query(f"""
      WITH v AS ({VALUED})
      SELECT {bucket} AS period,
             SUM(all_tokens)    AS tokens,
             SUM(api_value_usd) AS api_value,
             SUM(net_spend_usd) AS billed,
             COUNT(DISTINCT NULLIF(person,'')) AS users
      FROM v WHERE usage_date BETWEEN DATE(@s) AND DATE(@e)
      GROUP BY period ORDER BY period
    """, PROJECT, params={"s": since, "e": through})
    return [{"period": str(r["period"]), "tokens": _i(r["tokens"]),
             "api_value": round(_f(r["api_value"]), 2),
             "billed": round(_f(r["billed"]), 2),
             "users": _i(r["users"])} for r in rows]


# Categorical slots are finite, so the series list is capped and the remainder
# folds into one "Other" band. A ninth generated hue is never an option.
MODEL_SLOTS = 4


def model_mix(since: str, through: str, grain: str = "month") -> dict:
    bucket = {"week": "DATE_TRUNC(usage_date, WEEK(MONDAY))",
              "month": "DATE_TRUNC(usage_date, MONTH)"}[grain]
    rows = bq.query(f"""
      WITH v AS ({VALUED})
      SELECT {bucket} AS period, model, SUM(api_value_usd) AS api_value,
             SUM(all_tokens) AS tokens
      FROM v WHERE usage_date BETWEEN DATE(@s) AND DATE(@e)
      GROUP BY period, model
    """, PROJECT, params={"s": since, "e": through})
    totals = {}
    for r in rows:
        totals[r["model"]] = totals.get(r["model"], 0.0) + _f(r["api_value"])
    top = [m for m, _ in sorted(totals.items(), key=lambda x: -x[1])[:MODEL_SLOTS]]
    periods = sorted({str(r["period"]) for r in rows})
    series = {m: {p: 0.0 for p in periods} for m in top + ["Other"]}
    for r in rows:
        key = r["model"] if r["model"] in top else "Other"
        series[key][str(r["period"])] += _f(r["api_value"])
    # Drop an empty Other rather than draw a flat zero band.
    if not any(series["Other"].values()):
        del series["Other"]
    return {
        "periods": periods,
        "series": [{"model": m, "values": [round(series[m][p], 2) for p in periods],
                    "total": round(sum(series[m].values()), 2)}
                   for m in series],
        "all_time_totals": {m: round(v, 2) for m, v in
                            sorted(totals.items(), key=lambda x: -x[1])},
    }


# A stacked bar reads on the *adjacent* pairlist, where the full eight-slot
# categorical order validates in both modes — unlike the all-pairs case that
# caps model mix at four. So this section names seven users and folds the rest,
# which with fifteen members covers everyone who registers.
USER_SLOTS = 7


def user_mix(since: str, through: str) -> dict:
    """Usage per person, bucketed BOTH monthly and weekly from one read.

    Two grains rather than one because the questions differ: a month says who
    carried the quarter, a week says who is carrying it now, and a monthly bar
    hides a person who appeared three weeks ago.

    **One query, one ranking, both grains.** The read is at day grain and the
    buckets are formed here, which costs one BigQuery scan instead of two and —
    the part that matters — makes it impossible for the two views to rank people
    differently. Colour follows the entity, never its rank, so if the monthly
    view folded a different set into "Other" than the weekly one, the same
    person would change colour when the reader flipped the toggle. Sharing the
    ranking means that cannot happen, rather than happening to be true.
    """
    rows = bq.query(f"""
      WITH v AS ({VALUED})
      SELECT usage_date,
             COALESCE(NULLIF(person,''), '(org service)') AS who,
             SUM(api_value_usd) AS api_value, SUM(all_tokens) AS tokens
      FROM v WHERE usage_date BETWEEN DATE(@s) AND DATE(@e)
      GROUP BY usage_date, who
    """, PROJECT, params={"s": since, "e": through})

    totals, tok_totals = {}, {}
    for r in rows:
        totals[r["who"]] = totals.get(r["who"], 0.0) + _f(r["api_value"])
        tok_totals[r["who"]] = tok_totals.get(r["who"], 0) + _i(r["tokens"])
    ranked = [u for u, _ in sorted(totals.items(), key=lambda x: -x[1])]
    top = ranked[:USER_SLOTS]
    folded = ranked[USER_SLOTS:]
    slots = top + (["Other"] if folded else [])

    def bucketed(grain: str) -> dict:
        by = {}
        for r in rows:
            d = r["usage_date"]
            d = d if isinstance(d, dt.date) else dt.date.fromisoformat(str(d)[:10])
            period = (d.replace(day=1) if grain == "month"
                      else d - dt.timedelta(days=d.weekday()))   # Monday, matching
            key = r["who"] if r["who"] in top else "Other"        # BigQuery's WEEK(MONDAY)
            by.setdefault(str(period), {}).setdefault(key, 0.0)
            by[str(period)][key] += _f(r["api_value"])
        periods = sorted(by)
        return {"periods": periods,
                "series": [{"user": u,
                            "values": [round(by[p].get(u, 0.0), 2) for p in periods],
                            "total": round(sum(by[p].get(u, 0.0) for p in periods), 2)}
                           for u in slots]}

    return {
        # The slot order both grains share. Emitted so a reader — and a test —
        # can see that the two views cannot disagree about who gets which hue.
        "slots": slots,
        "grains": {g: bucketed(g) for g in ("month", "week")},
        # Stated, not implied: an unnamed band is only honest with its count.
        "folded": {"count": len(folded), "users": folded,
                   "total": round(sum(totals[u] for u in folded), 2)},
        "all_time": [{"user": u, "value": round(totals[u], 2),
                      "tokens": tok_totals[u]} for u in ranked],
    }


def overage_mix(since: str, through: str) -> dict:
    """**Billed overage** per person, monthly and weekly, from one read.

    This is the only chart in the report denominated in money that was actually
    charged. Everything else per-person is *API-equivalent value* — a derived
    counterfactual useful for comparing against a seat price. This is the
    invoice.

    The two rank people differently, and that difference is the point rather
    than a wrinkle to hide. Overage is what spills past a personal allowance, so
    a heavy user well inside their cap owes nothing while a lighter user who
    overflowed owes real money. Over the current window Rohan is third by usage
    and seventh by overage; the org service account is sixteenth by usage and
    fourth by overage.

    **So this ranks by overage, on its own, and does not inherit the usage
    chart's slot order.** Sharing that order would be tidier — a person would
    keep one colour across both charts — but it would fold the fourth-largest
    payer into "Other" for being a light user, which is exactly the fact this
    chart exists to surface. Colours therefore differ between the two charts by
    design, and the lede says so.
    """
    rows = bq.query(f"""
      SELECT usage_date,
             COALESCE(NULLIF({person_sql("user_email")},''), '(org service)') AS who,
             SUM(net_spend_usd) AS billed
      FROM `aura-main.anthropic_usage.daily_user_usage`
      WHERE usage_date BETWEEN DATE(@s) AND DATE(@e)
      GROUP BY usage_date, who
    """, PROJECT, params={"s": since, "e": through})

    totals = {}
    for r in rows:
        totals[r["who"]] = totals.get(r["who"], 0.0) + _f(r["billed"])
    # Someone who never overflowed belongs in the table below, not in a stack of
    # zero-height bars competing for one of eight colours.
    paying = [u for u, v in sorted(totals.items(), key=lambda x: -x[1]) if v > 0]
    top, folded = paying[:USER_SLOTS], paying[USER_SLOTS:]
    slots = top + (["Other"] if folded else [])

    def bucketed(grain: str) -> dict:
        # Every period in the window gets an axis slot, including ones where
        # nobody overflowed. Registering a period only when a charge lands would
        # give this chart a different timeline from the usage chart beside it —
        # and the lede asks the reader to compare the two — while making
        # sporadic charges read as continuous, because the quiet weeks simply
        # would not be drawn.
        def bucket_of(r):
            d = r["usage_date"]
            d = d if isinstance(d, dt.date) else dt.date.fromisoformat(str(d)[:10])
            return str(d.replace(day=1) if grain == "month"
                       else d - dt.timedelta(days=d.weekday()))

        by = {bucket_of(r): {} for r in rows}
        for r in rows:
            v = _f(r["billed"])
            if v <= 0:
                continue
            key = r["who"] if r["who"] in top else "Other"
            b = by[bucket_of(r)]
            b[key] = b.get(key, 0.0) + v
        periods = sorted(by)
        return {"periods": periods,
                "series": [{"user": u,
                            "values": [round(by[pd].get(u, 0.0), 2) for pd in periods],
                            "total": round(sum(by[pd].get(u, 0.0) for pd in periods), 2)}
                           for u in slots]}

    return {
        "slots": slots,
        "grains": {g: bucketed(g) for g in ("month", "week")},
        "folded": {"count": len(folded), "users": folded,
                   "total": round(sum(totals[u] for u in folded), 2)},
        "total": round(sum(totals.values()), 2),
        "payers": len(paying),
        "people": len(totals),
        "all_time": [{"user": u, "billed": round(totals[u], 2)}
                     for u in sorted(totals, key=lambda x: -totals[x])],
    }


def true_cost(since: str, through: str) -> dict:
    """What we actually pay each month: seat subscription **plus** billed overage.

    The report showed both halves of the bill and never the bill. Overage is real
    money but it is the small half — $1,020/month of seats dwarfs a typical
    month's overflow — so a reader could study every existing chart and still not
    know what Claude costs.

    **Monthly only, deliberately.** Seats are billed monthly. Splitting a seat
    charge across weeks would be a derived allocation dressed as a fact, and this
    report works hard to keep derived and actual apart. So this is the one
    time-series card with no weekly toggle.

    **Months before the first seat snapshot are ASSUMED, and say so.** The roster
    is only known from the day `member_seat` was first captured; earlier months
    can only be priced by assuming today's roster applied then, and headcount was
    almost certainly lower. Each month therefore carries `measured`: true when a
    snapshot falls inside it, false when the roster was carried back. Dropping
    those months would hide most of the window; presenting them as invoices would
    be a confident fiction. Tag, don't drop.
    """
    seats = bq.query("""
      SELECT as_of_date, seat_tier, COUNT(*) AS n
      FROM `aura-main.anthropic_usage.member_seat`
      GROUP BY as_of_date, seat_tier
    """, PROJECT)
    by_snap = {}
    for r in seats:
        by_snap.setdefault(str(r["as_of_date"]), {})[r["seat_tier"]] = _i(r["n"])
    if not by_snap:
        return {"months": [], "unmeasured": 0, "note": "no seat snapshot exists"}
    snap_days = sorted(by_snap)
    earliest = by_snap[snap_days[0]]

    spend = bq.query("""
      SELECT DATE_TRUNC(usage_date, MONTH) AS m, SUM(net_spend_usd) AS billed
      FROM `aura-main.anthropic_usage.daily_user_usage`
      WHERE usage_date BETWEEN DATE(@s) AND DATE(@e)
      GROUP BY m ORDER BY m
    """, PROJECT, params={"s": since, "e": through})
    billed_by_month = {str(r["m"]): _f(r["billed"]) for r in spend}

    # Which months were actually READ. A month absent from daily_user_usage is
    # either idle or never harvested, and that table cannot tell them apart —
    # which is exactly why harvest_log exists.
    logged = bq.query("""
      SELECT DISTINCT FORMAT_DATE('%Y-%m-01', harvest_date) AS m
      FROM `aura-main.anthropic_usage.harvest_log`
      WHERE harvest_date BETWEEN DATE(@s) AND DATE(@e)
    """, PROJECT, params={"s": since, "e": through})
    harvested_months = {str(r["m"]) for r in logged}

    # The axis is the WINDOW, not the months that happen to carry usage rows.
    # Seats are billed whether or not anybody typed, so an idle month still costs
    # $1,020 — building the axis from usage would drop it and understate the bill,
    # and could empty the card entirely for a quiet window. Same rule the overage
    # chart needed: a period's existence is a fact about the calendar, not about
    # whether something landed in it.
    first = dt.date.fromisoformat(since).replace(day=1)
    last = dt.date.fromisoformat(through).replace(day=1)
    axis, cur = [], first
    while cur <= last:
        axis.append(cur.isoformat())
        cur = (cur.replace(day=28) + dt.timedelta(days=4)).replace(day=1)

    def roster_for(month: str):
        """The snapshot taken inside this month, else the earliest ever taken."""
        inside = [d for d in snap_days if d[:7] == month[:7]]
        if inside:
            return by_snap[inside[-1]], True
        return earliest, False

    months = []
    for m in axis:
        roster, measured = roster_for(m)
        seat_usd = sum(SEAT_USD.get(tier, 0.0) * n for tier, n in roster.items())
        over = round(billed_by_month.get(m, 0.0), 2)
        months.append({
            "period": m,
            "seats": round(seat_usd, 2),
            "overage": over,
            "total": round(seat_usd + over, 2),
            "members": sum(roster.values()),
            # False means the roster was carried back from a later snapshot —
            # the total is an estimate, and the card marks it as one.
            "measured": measured,
            # False means no day of this month was ever harvested, so its $0 of
            # overage is an absence of reading rather than an absence of charge.
            # Tagged rather than dropped: the seat cost is still owed and still
            # shown, but the overage figure must not read as a measured zero.
            "overage_known": m in harvested_months,
        })
    return {
        "months": months,
        "unmeasured": sum(1 for m in months if not m["measured"]),
        "unharvested": sum(1 for m in months if not m["overage_known"]),
        "first_snapshot": snap_days[0],
        "snapshots": len(snap_days),
        "seat_prices": SEAT_USD,
    }


def concentration(since: str, through: str) -> dict:
    rows = bq.query(f"""
      WITH v AS ({VALUED})
      SELECT NULLIF(person,'') AS user, SUM(api_value_usd) AS api_value
      FROM v WHERE usage_date BETWEEN DATE(@s) AND DATE(@e)
      GROUP BY user HAVING SUM(api_value_usd) > 0 ORDER BY api_value DESC
    """, PROJECT, params={"s": since, "e": through})
    vals = [(r["user"] or "(org service)", _f(r["api_value"])) for r in rows]
    total = sum(v for _, v in vals)
    # `or 1.0` guards the share division, but writing that sentinel into the
    # output would report $1.00 of usage for a window that had none. Keep the
    # real total in the JSON and use the guard only where it divides.
    denom = total or 1.0
    cum, pts = 0.0, []
    for i, (u, v) in enumerate(vals, 1):
        cum += v
        pts.append({"rank": i, "user": u, "value": round(v, 2),
                    "share": round(v / denom * 100, 2),
                    "cum_share": round(cum / denom * 100, 2)})
    def at(n):
        return round(pts[min(n, len(pts)) - 1]["cum_share"], 1) if pts else 0.0
    return {"points": pts, "total": round(total, 2),
            "top1": at(1), "top3": at(3), "top5": at(5), "top10": at(10)}


def token_mix(since: str, through: str) -> dict:
    """Where the tokens actually are. Explains why billed overage is so small."""
    r = bq.query(f"""
      SELECT SUM(uncached_input_tokens) unc, SUM(cache_read_tokens) cr,
             SUM(cache_write_5m_tokens + cache_write_1h_tokens) cw,
             SUM(completion_tokens) out, SUM(prompt_tokens + completion_tokens) tot
      FROM {USAGE} WHERE usage_date BETWEEN DATE(@s) AND DATE(@e)
    """, PROJECT, params={"s": since, "e": through})[0]
    tot = _i(r["tot"]) or 1
    return {"uncached_input": _i(r["unc"]), "cache_read": _i(r["cr"]),
            "cache_write": _i(r["cw"]), "output": _i(r["out"]), "total": _i(r["tot"]),
            "cache_read_pct": round(_i(r["cr"]) / tot * 100, 2)}


def products(since: str, through: str) -> list:
    rows = bq.query(f"""
      SELECT product, SUM(prompt_tokens + completion_tokens) tokens
      FROM {USAGE} WHERE usage_date BETWEEN DATE(@s) AND DATE(@e)
      GROUP BY product HAVING tokens > 0 ORDER BY tokens DESC
    """, PROJECT, params={"s": since, "e": through})
    return [{"product": r["product"], "tokens": _i(r["tokens"])} for r in rows]


def pricing_meta(since: str, through: str) -> dict:
    r = bq.query(f"SELECT MAX(verified_on) v, COUNT(*) n FROM {PRICES}", PROJECT)[0]
    v = str(r["v"]) if r["v"] else None
    age = None
    if v:
        age = (dt.date.today() - dt.date.fromisoformat(v)).days
    # A model with no rate row values at $0 through the LEFT JOIN, which reads
    # exactly like usage inside the allowance. Name every one that carries tokens.
    unpriced = bq.query(f"""
      SELECT model, SUM(all_tokens) AS tokens, MIN(usage_date) AS first_day
      FROM ({VALUED}) WHERE usage_date BETWEEN @s AND @e AND api_value_usd IS NULL
      GROUP BY model HAVING tokens > 0 ORDER BY tokens DESC
    """, PROJECT, params={"s": since, "e": through})
    return {"verified_on": v, "rows": _i(r["n"]), "age_days": age,
            "unpriced": [{"model": u["model"], "tokens": _i(u["tokens"]),
                          "first_day": str(u["first_day"])} for u in unpriced]}


def seat_snapshot_meta() -> dict:
    r = bq.query(f"SELECT MAX(as_of_date) d, COUNT(DISTINCT as_of_date) n FROM {SEATS}",
                 PROJECT)[0]
    return {"as_of": str(r["d"]) if r["d"] else None, "snapshots": _i(r["n"])}


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description="Collect Claude usage report data")
    p.add_argument("--since", default=EARLIEST)
    p.add_argument("--through")
    p.add_argument("--grain", choices=["week", "month"], default="month",
                   help="Bucket for the adoption + model-mix series (default month)")
    p.add_argument("--out", help="Write JSON here (default stdout)")
    a = p.parse_args()

    # Default the window end to the newest harvested day, not to yesterday. The
    # export settles a day late, so "yesterday" is never harvestable and every
    # report would carry a permanent INCOMPLETE banner for a day that was never
    # missing. Real staleness is reported separately as lag_days.
    try:
        newest = last_harvested()
    except bq.BQError:
        newest = None
    through = a.through or newest or (dt.datetime.now(dt.timezone.utc).date()
                                      - dt.timedelta(days=1)).isoformat()
    since = a.since
    if since < EARLIEST:
        print(f"! --since {since} predates the first data day; clamping to {EARLIEST}",
              file=sys.stderr)
        since = EARLIEST
    if since > through:
        print(f"nothing to do: {since} is after {through}", file=sys.stderr)
        return 1

    try:
        cov = coverage(since, through)
        data = {
            "window": {"since": since, "through": through, "grain": a.grain,
                       "days": cov["days_in_window"]},
            "coverage": cov,
            "seat_fit": seat_fit(since, through, cov["days_in_window"]),
            "adoption": adoption(since, through, a.grain),
            "adoption_daily": adoption(since, through, "day"),
            "model_mix": model_mix(since, through, a.grain),
            "user_mix": user_mix(since, through),
            "overage_mix": overage_mix(since, through),
            "true_cost": true_cost(since, through),
            "concentration": concentration(since, through),
            "token_mix": token_mix(since, through),
            "products": products(since, through),
            "pricing": pricing_meta(since, through),
            "seats_meta": seat_snapshot_meta(),
            "seat_prices": SEAT_USD,
            "generated_at": dt.datetime.now(dt.timezone.utc)
                              .strftime("%Y-%m-%d %H:%M UTC"),
        }
    except bq.BQUnavailable as e:
        print(f"BigQuery unavailable: {e}", file=sys.stderr)
        return 3
    except bq.BQError as e:
        print(f"query failed: {e}", file=sys.stderr)
        return 1

    if not cov["complete"]:
        print(f"! only {cov['days_harvested']}/{cov['days_in_window']} days harvested — "
              f"the report will label its figures a lower bound", file=sys.stderr)
    for u in data["pricing"]["unpriced"]:
        print(f"! {u['model']} has no model_pricing row — {u['tokens']:,} tokens since "
              f"{u['first_day']} are valued at $0; add it to seed_pricing.py",
              file=sys.stderr)
    if data["pricing"]["age_days"] is not None and data["pricing"]["age_days"] > 120:
        print(f"! model_pricing last verified {data['pricing']['age_days']} days ago — "
              f"re-check before quoting API-equivalent value externally", file=sys.stderr)

    blob = json.dumps(data, indent=2)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write(blob)
        print(f"wrote {a.out}  ({cov['days_harvested']}/{cov['days_in_window']} days, "
              f"{len(data['seat_fit'])} members)")
    else:
        print(blob)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
