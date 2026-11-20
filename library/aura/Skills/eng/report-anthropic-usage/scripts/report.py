#!/usr/bin/env python3
"""Report on `aura-main.anthropic_usage` — per-user Claude usage on the Team plan.

The question this exists to answer: **who is actually using their seat, and is
the seat tier right?** On a seat-based subscription the billed dollars are
overage only, so they answer a different and much narrower question ("who
overflowed their personal cap"). Reporting from tokens, priced at API list
rates, is what makes a light premium seat and a heavy one distinguishable.

Two numbers appear side by side throughout and must not be conflated:

* **API-equivalent value** — what the usage would have cost on the API at list
  price. Derived, not an invoice. Use it to compare against the seat price.
* **Billed overage** — real dollars actually charged, from the export. Usually a
  small fraction of the above, and $0 for anyone inside their allowance.

Modes: `summary` (org + per-user for a window), `user` (one person over time),
`trend` (daily/weekly/monthly series), `seats` (tier vs usage, the upgrade or
downgrade call).
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bq  # noqa: E402

PROJECT = "aura-main"
DATASET = "anthropic_usage"
USAGE = f"`{PROJECT}.{DATASET}.daily_user_usage`"
SEATS = f"`{PROJECT}.{DATASET}.member_seat`"
PRICES = f"`{PROJECT}.{DATASET}.model_pricing`"
LOG    = f"`{PROJECT}.{DATASET}.harvest_log`"

# Seat list prices, annual billing. Used only to put usage next to what the seat
# costs; change here if the plan changes.
SEAT_USD = {"premium": 100.0, "standard": 20.0}

# Valued usage, priced per model at the rate **in force on the usage date**.
#
# The rate is picked per row, not globally: the winning price row is the one
# with the greatest `effective_from` that is still on or before that day's
# `usage_date`. Taking the latest rate per model instead — which is the obvious
# shortcut — silently reprices all of history the moment a rate changes, which
# defeats the entire point of storing prices effective-dated. It would also
# never error; it would just quietly restate last quarter.
#
# Each rate row is given an explicit validity window with LEAD, so the windows
# per model are disjoint and the join matches exactly one. A plain
# `USING (model)` join instead fans out to one output row per rate row the
# moment a second rate exists, doubling every valuation silently.
#
# It has to be expressed this way rather than as a correlated "latest rate on or
# before this day" subquery: BigQuery rejects those outright
# ("Correlated subqueries that reference other tables are not supported").
VALUED = f"""
  SELECT u.*,
         (u.uncached_input_tokens   * p.input_usd_per_mtok
        + u.cache_read_tokens       * p.cache_read_usd_per_mtok
        + u.cache_write_5m_tokens   * p.cache_write_5m_usd_per_mtok
        + u.cache_write_1h_tokens   * p.cache_write_1h_usd_per_mtok
        + u.completion_tokens       * p.output_usd_per_mtok) / 1000000 AS api_value_usd,
         u.prompt_tokens + u.completion_tokens AS all_tokens
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


def _latest_seats() -> str:
    return f"""
  SELECT user_email, seat_tier, display_name, role
  FROM {SEATS}
  WHERE as_of_date = (SELECT MAX(as_of_date) FROM {SEATS})
"""


def _window(a) -> tuple:
    if a.since and a.through:
        return a.since, a.through
    end = dt.date.fromisoformat(a.through) if a.through else \
        dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=1)
    start = dt.date.fromisoformat(a.since) if a.since else end - dt.timedelta(days=a.days - 1)
    return start.isoformat(), end.isoformat()


def _fmt_tok(n) -> str:
    n = int(n or 0)
    for unit, div in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if n >= div:
            return f"{n/div:.1f}{unit}"
    return str(n)


def _coverage(since: str, through: str) -> dict:
    """Which days in the window were actually harvested.

    Reads `harvest_log`, which carries a row per day read **including days with
    no usage**. Counting distinct dates in the fact table instead cannot tell an
    idle Saturday from a day the harvest never ran: both hold zero rows. That
    version reported every quiet weekend as a gap, and would equally have hidden
    a real gap next to a busy day.

    The distinction matters because the two cases call for opposite readings. An
    idle day means the totals are right; an unharvested day means they are a
    lower bound.
    """
    rows = bq.query(f"""
      SELECT COUNT(DISTINCT harvest_date) AS days_harvested
      FROM {LOG} WHERE harvest_date BETWEEN DATE(@s) AND DATE(@e)
    """, PROJECT, params={"s": since, "e": through})
    have = rows[0]["days_harvested"] if rows else 0
    want = (dt.date.fromisoformat(through) - dt.date.fromisoformat(since)).days + 1
    return {"have": have, "want": want, "complete": have >= want}


def _coverage_note(cov: dict) -> str:
    """One line to print under any figure derived from a window."""
    if cov["complete"]:
        return ""
    return (f"!! INCOMPLETE: {cov['have']} of {cov['want']} days harvested. "
            f"Figures below are a lower bound.")


def cmd_summary(a) -> int:
    since, through = _window(a)
    cov = _coverage(since, through)
    rows = bq.query(f"""
      WITH v AS ({VALUED}), s AS ({_latest_seats()})
      SELECT COALESCE(NULLIF(v.user_email,''), '(org service)') AS who,
             COALESCE(s.seat_tier, 'departed/none')             AS seat,
             SUM(v.all_tokens)      AS tokens,
             SUM(v.requests)        AS requests,
             SUM(v.api_value_usd)   AS api_value,
             SUM(v.net_spend_usd)   AS billed,
             COUNT(DISTINCT v.usage_date) AS active_days
      FROM v LEFT JOIN s ON s.user_email = v.user_email
      WHERE v.usage_date BETWEEN DATE(@s) AND DATE(@e)
      GROUP BY who, seat
      ORDER BY api_value DESC
    """, PROJECT, params={"s": since, "e": through})
    if not rows:
        print(f"No usage rows for {since} → {through}. "
              f"Harvested days in window: {cov['have']}/{cov['want']}.")
        return 0

    ndays = cov["want"]
    print(f"Claude usage · {since} → {through} ({ndays} days)")
    note = _coverage_note(cov)
    if note:
        print(note)
    print()
    print(f"{'user':30}{'seat':>10}{'tokens':>10}{'API value':>12}{'billed':>10}{'days':>6}")
    print("-" * 78)
    tv = tb = tt = 0.0
    for r in rows:
        print(f"{r['who']:30}{r['seat']:>10}{_fmt_tok(r['tokens']):>10}"
              f"{float(r['api_value'] or 0):>12,.0f}{float(r['billed'] or 0):>10,.2f}"
              f"{r['active_days']:>6}")
        tv += float(r["api_value"] or 0); tb += float(r["billed"] or 0)
        tt += float(r["tokens"] or 0)
    print("-" * 78)
    print(f"{'TOTAL':30}{'':>10}{_fmt_tok(tt):>10}{tv:>12,.0f}{tb:>10,.2f}")
    print()
    print(f"API value = list-price cost of the same usage. Billed = real overage only.")
    print(f"Ratio: ${tv:,.0f} of usage for ${tb:,.2f} charged beyond seats.")
    return 0


def cmd_seats(a) -> int:
    since, through = _window(a)
    cov = _coverage(since, through)
    days = cov["want"]
    rows = bq.query(f"""
      WITH v AS ({VALUED}), s AS ({_latest_seats()})
      SELECT s.user_email, s.seat_tier, s.role,
             SUM(v.all_tokens)    AS tokens,
             SUM(v.api_value_usd) AS api_value,
             COUNT(DISTINCT v.usage_date) AS active_days
      FROM s LEFT JOIN v
        ON v.user_email = s.user_email AND v.usage_date BETWEEN DATE(@s) AND DATE(@e)
      GROUP BY s.user_email, s.seat_tier, s.role
      ORDER BY api_value DESC NULLS LAST
    """, PROJECT, params={"s": since, "e": through})
    if not rows:
        print("No seat snapshot yet — run the harvest skill first.")
        return 0

    scale = 30.0 / days if days else 1.0
    print(f"Seat fit · usage {since} → {through}, projected to a 30-day month")
    note = _coverage_note(cov)
    if note:
        print(note)
    print()
    print(f"{'user':30}{'seat':>10}{'seat $':>8}{'value/mo':>11}{'ratio':>8}{'days':>6}  note")
    print("-" * 88)
    seat_total = value_total = 0.0
    for r in rows:
        tier = (r["seat_tier"] or "unknown").lower()
        seat_cost = SEAT_USD.get(tier, 0.0)
        val = float(r["api_value"] or 0) * scale
        ratio = (val / seat_cost) if seat_cost else 0.0
        note = ""
        if tier == "premium" and val < seat_cost:
            note = "under-using a premium seat"
        elif tier == "standard" and val > SEAT_USD["premium"]:
            note = "heavy — premium may pay off"
        elif not r["active_days"]:
            note = "no usage in window"
        print(f"{r['user_email']:30}{tier:>10}{seat_cost:>8,.0f}{val:>11,.0f}"
              f"{ratio:>7,.0f}x{r['active_days'] or 0:>6}  {note}")
        seat_total += seat_cost; value_total += val
    print("-" * 88)
    print(f"{'TOTAL':30}{'':>10}{seat_total:>8,.0f}{value_total:>11,.0f}"
          f"{(value_total/seat_total if seat_total else 0):>7,.0f}x")
    print()
    print("ratio = API-equivalent value of usage ÷ monthly seat price. Above 1x the")
    print("seat pays for itself against API rates; it is not a cash saving.")
    return 0


def cmd_trend(a) -> int:
    since, through = _window(a)
    cov = _coverage(since, through)
    grain = a.grain
    bucket = {"day": "usage_date",
              "week": "DATE_TRUNC(usage_date, WEEK(MONDAY))",
              "month": "DATE_TRUNC(usage_date, MONTH)"}[grain]
    rows = bq.query(f"""
      WITH v AS ({VALUED})
      SELECT {bucket} AS bucket,
             SUM(all_tokens)    AS tokens,
             SUM(api_value_usd) AS api_value,
             SUM(net_spend_usd) AS billed,
             COUNT(DISTINCT NULLIF(user_email,'')) AS users
      FROM v WHERE usage_date BETWEEN DATE(@s) AND DATE(@e)
      GROUP BY bucket ORDER BY bucket
    """, PROJECT, params={"s": since, "e": through})
    if not rows:
        # An empty result in a half-harvested window is not "no usage" — it is
        # the idle-versus-missing collapse coverage exists to prevent. Say which
        # one it is before the reader concludes anything.
        print(f"No rows for {since} → {through}.")
        note = _coverage_note(cov)
        print(note if note else
              f"All {cov['want']} days in the window were harvested, so this is "
              f"genuinely no activity.")
        return 0
    print(f"Claude usage by {grain} · {since} → {through}")
    note = _coverage_note(cov)
    if note:
        print(note)
    print()
    print(f"{'period':14}{'users':>7}{'tokens':>10}{'API value':>12}{'billed':>10}")
    print("-" * 53)
    for r in rows:
        print(f"{str(r['bucket']):14}{r['users']:>7}{_fmt_tok(r['tokens']):>10}"
              f"{float(r['api_value'] or 0):>12,.0f}{float(r['billed'] or 0):>10,.2f}")
    return 0


def cmd_user(a) -> int:
    since, through = _window(a)
    cov = _coverage(since, through)
    rows = bq.query(f"""
      WITH v AS ({VALUED})
      SELECT product, model,
             SUM(all_tokens)    AS tokens,
             SUM(requests)      AS requests,
             SUM(api_value_usd) AS api_value,
             SUM(net_spend_usd) AS billed,
             COUNT(DISTINCT usage_date) AS days
      FROM v
      WHERE usage_date BETWEEN DATE(@s) AND DATE(@e) AND LOWER(user_email) = LOWER(@u)
      GROUP BY product, model ORDER BY api_value DESC
    """, PROJECT, params={"s": since, "e": through, "u": a.email})
    if not rows:
        print(f"No usage for {a.email} between {since} and {through}.")
        note = _coverage_note(cov)
        print(note if note else
              f"All {cov['want']} days in the window were harvested, so this is "
              f"genuinely no activity for this user.")
        return 0
    print(f"{a.email} · {since} → {through}")
    note = _coverage_note(cov)
    if note:
        print(note)
    print()
    print(f"{'product':20}{'model':28}{'tokens':>10}{'API value':>12}{'billed':>10}")
    print("-" * 80)
    tv = tb = 0.0
    for r in rows:
        print(f"{r['product']:20}{r['model']:28}{_fmt_tok(r['tokens']):>10}"
              f"{float(r['api_value'] or 0):>12,.0f}{float(r['billed'] or 0):>10,.2f}")
        tv += float(r["api_value"] or 0); tb += float(r["billed"] or 0)
    print("-" * 80)
    print(f"{'TOTAL':48}{'':>10}{tv:>12,.0f}{tb:>10,.2f}")
    return 0


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description="Report on claude.ai subscription usage")
    p.add_argument("--since"); p.add_argument("--through")
    p.add_argument("--days", type=int, default=30, help="Trailing window (default 30)")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("summary", help="org + per-user totals")
    sub.add_parser("seats", help="seat tier vs actual usage")
    t = sub.add_parser("trend", help="time series")
    t.add_argument("--grain", choices=["day", "week", "month"], default="week")
    u = sub.add_parser("user", help="one person's breakdown")
    u.add_argument("email")
    a = p.parse_args()
    fn = {"summary": cmd_summary, "seats": cmd_seats, "trend": cmd_trend,
          "user": cmd_user}.get(a.cmd or "summary", cmd_summary)
    if a.cmd == "trend" and not hasattr(a, "grain"):
        a.grain = "week"
    try:
        return fn(a)
    except bq.BQUnavailable as e:
        print(f"BigQuery unavailable: {e}", file=sys.stderr); return 3
    except bq.BQError as e:
        print(f"Query failed: {e}", file=sys.stderr); return 1


if __name__ == "__main__":
    raise SystemExit(main())
