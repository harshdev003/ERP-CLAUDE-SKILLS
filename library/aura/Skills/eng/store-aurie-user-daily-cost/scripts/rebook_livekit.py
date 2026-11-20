"""Restate stored LiveKit dollars from the invoice instead of a per-minute rate card.

PROD-5998. Every day stored before that card booked
``voice_livekit = session_minutes x $0.01``, the plan's OVERAGE rate. That rate applies
to nothing while usage sits inside the allotment, so the column priced minutes that were
in fact marginally free while the real cost — a flat $500/mo subscription — was booked
nowhere at all. Measured against the invoices:

    Aug 2026   stored $119.90   actual $501.14  ($500 + $1.14 Krisp overage)
    Sep 2026   stored  $74.36   actual $500.00  (every usage line $0.00)

This pass rewrites each stored day to:

    voice_livekit           minutes x the plan's CREDIT rate (subscription / included
                            allotment; Scale = $0.01/min), per user.
    voice_livekit_platform  capacity paid for and NOT used, on the reconciling `Other`
                            row only — it belongs to nobody.
    livekit_minutes         the day's per-user minutes, made explicit.

WHERE THE MINUTES COME FROM, AND WHY THAT IS SOUND HERE
-------------------------------------------------------
The forwarded LiveKit logs have a retention window far shorter than this backfill, so
the minutes cannot be re-walked. They are RECOVERED instead, by dividing the stored
``voice_livekit`` by the rate that produced it (``--old-rate``, default $0.01). That is
exact rather than approximate: the stored column is ``minutes x rate`` by construction,
so the division inverts it. `eng:report-aurie-daily-costs` already recovers minutes this
way and cross-checked to within 0.001% of a live walk (1094.95 vs 1094.94 min).

It is also the last time this trick is needed. Recovery only works while the dollar
column is a pure multiple of minutes, which is exactly the property this change removes
— so the recovered value is written into ``livekit_minutes`` and read from there
afterwards. Running this pass twice would recover minutes from an overage column that is
now $0 and zero the store, which is why `--confirm` refuses a day already carrying a
platform figure unless `--force` is given.

MONTHS STORED UNDER THE CURRENT MODEL (ENG-1860)
-------------------------------------------------
A day `store.py` wrote itself already carries `livekit_minutes`, minutes x the credit rate,
the session-proxied pool accrual in `voice_livekit` and signed unused capacity on `Other`.
Recovering minutes from that `voice_livekit` would overstate them (by ~39% on 2026-10-15 ->
10-31) and adding the invoiced pools would book them twice. Such a month is booked from
its CLOSED statement instead, and only as a whole month:

    voice_livekit           the statement's agent-minute dollars (subscription + overage,
                            less any capacity left unused), per user by MEASURED minutes;
                            the total is the bill, the split is ours.
    voice_livekit_platform  the unused part of the subscription, spread per day on `Other`.
    `Other` voice_livekit   the invoiced pool lines (INVOICED_EXTRA_OVERAGE), spread per
                            day, replacing the accrual rather than adding to it.
    voice_deepgram          scaled to the statement's Inference line, every row by one
                            factor, so the per-user allocation is kept.

A month whose days already carry `livekit_minutes` (written by store.py, or restated by an
earlier run of this pass, as July-September were) and that has no closed statement, or is
not wholly in the range, is SKIPPED: left untouched and listed in the output. There is
nothing to book it to, and recovering its minutes from credits would be wrong.

DRY RUN BY DEFAULT. Prints the per-month before/after and the reconciliation against the
invoice; writes nothing until `--confirm`.

Stdlib + google-cloud-bigquery (via the bundled bq.py).
"""

from __future__ import annotations

import argparse
import calendar
import collections
import csv
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import livekit_plan  # noqa: E402
from bq import query, load_partition, PROJECT, DATASET  # noqa: E402
from bq_sink import STORES  # noqa: E402  (the per-user schema this table is loaded with)
from store import COLS  # noqa: E402

TABLE = f"{PROJECT}.{DATASET}.aurie_per_user_daily_cost"
OTHER_USER = "Other"

# Every numeric column of the per-user schema. A synthesized row must carry all of them
# at 0.0 rather than only the ones this pass happens to set.
_NUMERIC_COLS = ("llm_cost", "voice_11labs_agent", "voice_11labs_intelligence",
                 "voice_11labs_livekit", "voice_cartesia", "voice_fish", "voice_inworld",
                 "voice_deepgram",
                 "deepgram_inference_drawdown",
                 "voice_livekit", "voice_livekit_platform", "livekit_minutes",
                 "voice_cost", "total_cost")

# Per-month variable overage actually invoiced, beyond the subscription. Read off the
# LiveKit Cloud dashboard; keyed by month because it is a fact about a bill, not
# something derivable from the agent-session minutes this store knows about (August's
# $1.14 was KRISP overage, a pool the forwarded logs cannot measure at all).
INVOICED_EXTRA_OVERAGE = {
    "2026-08": 1.14,   # 955 Krisp background minutes over the 10,000 allotment
    "2026-09": 113.20,  # Krisp $56.85 (47,378 min over) + observability audio $56.35 (11,271 min over)
    # Krisp $299.27 + observability audio $1,048.67 + spans $290.11 + transcripts $78.75
    "2026-10": 1716.80,
}


def _csv_bytes(rows):
    """Serialize rows to headerless CSV in COLS order, which is what load_partition takes.

    Order matters for the CSV itself (it is parsed positionally against the schema the
    load job supplies); BigQuery then maps that schema to the destination table BY NAME,
    so a table whose physical column order differs — the two PROD-5998 columns were added
    with ALTER TABLE and sit last — still lands correctly. Verified against a scratch
    table rather than assumed, because the positional reading of it would have silently
    written minutes into a dollar column.
    """
    buf = io.StringIO()
    csv.writer(buf).writerows([[r.get(c, "") for c in COLS] for r in rows])
    return buf.getvalue().encode("utf-8")


def month_days(month: str) -> int:
    y, m = (int(x) for x in month.split("-"))
    return calendar.monthrange(y, m)[1]


def fetch_rows(since: str, until: str, project: str):
    sql = f"""
    SELECT * FROM `{TABLE}`
    WHERE date >= DATE(@since) AND date <= DATE(@until)
    ORDER BY date, user_id
    """
    return [dict(r) for r in query(sql, project, params={"since": since, "until": until})]


def statement_lines(month: str, plan: str):
    """The closed statement's lines for `month`, or None without one (no closed dashboard
    reading of agent minutes, or no INVOICED_EXTRA_OVERAGE entry). Dollars:
    agent (subscription + agent-minute overage) = consumed + unused; pools; inference
    (the Inference line beyond the included credits, None when the reading has none)."""
    reading = livekit_plan.dashboard_reading(month) or {}
    billed = reading.get("agent_session_minutes")
    if billed is None or reading.get("partial") or month not in INVOICED_EXTRA_OVERAGE:
        return None
    # The statement's agent line when it was read, else billed minutes x the published rate.
    over = reading.get("agent_overage_usd",
                       livekit_plan.overage_usd("agent_session_minutes", plan, billed))
    allot = livekit_plan.included("agent_session_minutes", plan) or 0
    consumed = min(billed, allot) * (livekit_plan.credit_rate_per_min(plan) or 0.0) + over
    agent = livekit_plan.subscription_usd(plan) + over
    credits = reading.get("inference_credits_usd")
    inference = (None if credits is None else
                 round(max(0.0, credits - (livekit_plan.included("inference_credits_usd", plan) or 0.0)), 2))
    return {"agent": agent, "consumed": consumed, "unused": agent - consumed,
            "pools": INVOICED_EXTRA_OVERAGE[month], "inference": inference,
            "billed_minutes": billed}


def _current_model(day_rows) -> bool:
    """A day that already carries `livekit_minutes` on its user rows: written by store.py under
    the current model, or restated by an earlier run of this pass. Either way its minutes are
    read from that column, never recovered from a `voice_livekit` that is now credits."""
    return any(r["user_id"] != OTHER_USER and r.get("livekit_minutes") is not None for r in day_rows)


def _f(r, col):
    return float(r.get(col) or 0.0)


def rebook(rows, plan, old_rate):
    """Return (by_date -> [row], report). Pure: no I/O, so the dry run and the write
    path cannot diverge. A month whose days carry `livekit_minutes` is booked only whole and
    from its closed statement; one that cannot be is SKIPPED: its days are dropped from
    `by_date`, so nothing of it is written, and the report lists it with the reason."""
    by_date = collections.defaultdict(list)
    for r in rows:
        by_date[str(r["date"])].append(dict(r))

    months = collections.defaultdict(list)
    for day in by_date:
        months[day[:7]].append(day)
    statements, report = {}, []
    for month, days in sorted(months.items()):
        current = [d for d in days if _current_model(by_date[d])]
        if not current:
            continue
        lines = statement_lines(month, plan)
        reason = ("no closed statement (DASHBOARD_READINGS partial=False with "
                  f"agent_session_minutes, and INVOICED_EXTRA_OVERAGE[{month!r}])" if lines is None
                  else f"only {len(days)} of {month_days(month)} days in the range"
                  if len(days) != month_days(month)
                  else f"{len(days) - len(current)} day(s) have no livekit_minutes"
                  if len(current) != len(days) else None)
        if reason:
            for d in days:
                del by_date[d]
            report.append({"date": min(days), "month": month, "skipped": reason})
            continue
        statements[month] = lines

    for month in sorted(statements):
        report += _rebook_from_statement(by_date, months[month], statements[month])
    report += _rebook_old_model(by_date, plan, old_rate,
                                [d for d in by_date if d[:7] not in statements])
    report.sort(key=lambda x: x["date"])
    return by_date, report


def skipped_months(report):
    return [r for r in report if r.get("skipped")]


def _booked(report):
    return [r for r in report if not r.get("skipped")]




def _ensure_other(day, day_rows):
    if not any(r["user_id"] == OTHER_USER for r in day_rows):
        # Built from the FULL schema: a partition replace serializes by schema, and an
        # omitted column lands as NULL. Mirrors store.py's own `Other` constructor.
        day_rows.append({**{c: 0.0 for c in _NUMERIC_COLS},
                         "date": day, "user_id": OTHER_USER,
                         "subscription_state": "other", "engaged": "0", "active": "0",
                         "activity_source": "reconcile"})


def _set(r, lk, plat, mins, dg=None):
    old_voice = _f(r, "voice_cost")
    old = _f(r, "voice_livekit") + _f(r, "voice_livekit_platform")
    r["voice_livekit"] = round(lk, 4)
    r["voice_livekit_platform"] = round(plat, 4)
    r["livekit_minutes"] = round(mins, 4)
    new = r["voice_livekit"] + r["voice_livekit_platform"]
    if dg is not None:
        old += _f(r, "voice_deepgram")
        r["voice_deepgram"] = round(dg, 4)
        new += r["voice_deepgram"]
    r["voice_cost"] = round(old_voice - old + new, 4)
    r["total_cost"] = round(_f(r, "llm_cost") + r["voice_cost"], 4)


def _rebook_from_statement(by_date, days, lines):
    """Book a whole current-model month to its closed statement (see the module doc)."""
    n = len(days)
    minutes = {d: {r["user_id"]: _f(r, "livekit_minutes") for r in by_date[d]
                   if r["user_id"] != OTHER_USER} for d in days}
    month_min = sum(sum(m.values()) for m in minutes.values())
    rate = lines["consumed"] / month_min if month_min > 0 else 0.0
    stored_dg = sum(_f(r, "voice_deepgram") for d in days for r in by_date[d])
    factor = (lines["inference"] / stored_dg
              if lines["inference"] is not None and stored_dg > 0 else None)
    report = []
    for day in sorted(days):
        day_rows = by_date[day]
        _ensure_other(day, day_rows)
        day_min = sum(minutes[day].values())
        stored_lk = sum(_f(r, "voice_livekit") + _f(r, "voice_livekit_platform") for r in day_rows)
        day_dg = sum(_f(r, "voice_deepgram") for r in day_rows)
        # With no minutes in the month there is nobody to bill: the agent dollars sit on `Other`.
        consumed_day = lines["consumed"] * day_min / month_min if month_min > 0 else lines["consumed"] / n
        lk_target = consumed_day + lines["pools"] / n
        dg_target = day_dg * factor if factor is not None else None
        users = [r for r in day_rows if r["user_id"] != OTHER_USER]
        for r in users:
            m = minutes[day].get(r["user_id"], 0.0)
            _set(r, m * rate, 0.0, m, None if factor is None else _f(r, "voice_deepgram") * factor)
        # `Other` takes the pools, the unused capacity and the day's rounding, so each day
        # (and so the month) lands on the statement to the cent.
        other = next(r for r in day_rows if r["user_id"] == OTHER_USER)
        _set(other, lk_target - sum(r["voice_livekit"] for r in users), lines["unused"] / n, 0.0,
             None if factor is None else dg_target - sum(r["voice_deepgram"] for r in users))
        report.append({"date": day, "month": day[:7], "minutes": day_min,
                       "stored_livekit": stored_lk, "new_overage": consumed_day,
                       "new_platform": lines["unused"] / n, "new_extra": lines["pools"] / n,
                       "stored_deepgram": day_dg,
                       "new_deepgram": dg_target if factor is not None else day_dg,
                       "statement": lines, "deepgram_factor": factor})
    return report


def _rebook_old_model(by_date, plan, old_rate, days):
    """Days written before PROD-5998: minutes recovered from `voice_livekit / old_rate`."""

    # Month-to-date minutes accumulate across days, because the allotment is monthly.
    mtd = collections.defaultdict(float)
    report = []
    for day in sorted(days):
        month = day[:7]
        day_rows = by_date[day]

        # Recover minutes from the column that was minutes x rate by construction.
        # The Other row's share was never attributable to a user, so it carries no
        # minutes — only a dollar remainder.
        minutes = {}
        for r in day_rows:
            if r["user_id"] == OTHER_USER:
                continue
            minutes[r["user_id"]] = float(r.get("voice_livekit") or 0.0) / old_rate
        total_min = sum(minutes.values())

        days_in_month = month_days(month)
        # Invoiced overage on a pool the forwarded logs cannot measure (August's $1.14
        # was Krisp, not agent-session). It is real invoiced cost, so it is BOOKED
        # rather than left as a permanent reconciliation gap — but it lands whole on the
        # reconciling `Other` row, because attributing it by agent-session minute share
        # would assert a per-user split of a quantity we cannot measure per user.
        # Spread per day for the same reason the subscription is: this store is
        # append-only per day and a lump on one day would misdate the cost.
        extra_day = INVOICED_EXTRA_OVERAGE.get(month, 0.0) / days_in_month
        credit = livekit_plan.credit_rate_per_min(plan) or 0.0
        day_consumed = total_min * credit
        day_unused = livekit_plan.unused_capacity_usd(plan, day, mtd[month], total_min)
        mtd[month] += total_min

        # Unused capacity has to land somewhere on every day it is non-zero. A day whose
        # stored rows happen to carry no `Other` row would otherwise drop it silently —
        # and the printed summary uses the COMPUTED figure, so a dry run would show
        # delta 0 while the write did not reconcile. That divergence between what the
        # dry run promises and what the write does is worse than the miss itself.
        if not any(r["user_id"] == OTHER_USER for r in day_rows) and (
                abs(day_unused) > 0.005 or abs(extra_day) > 0.005):
            # Built from the FULL schema, not a handful of fields. A partition replace
            # serializes by schema, so an omitted column lands as NULL — and any
            # `SUM(voice_x)` without COALESCE then drops the unused-capacity figure this
            # row exists to carry. Mirrors store.py's own `Other` constructor.
            day_rows.append({**{c: 0.0 for c in _NUMERIC_COLS},
                             "date": day, "user_id": OTHER_USER,
                             "subscription_state": "other", "engaged": "0", "active": "0",
                             "activity_source": "reconcile"})
        stored_lk = sum(float(r.get("voice_livekit") or 0.0) for r in day_rows)
        for r in day_rows:
            uid = r["user_id"]
            m = minutes.get(uid, 0.0)
            share = (m / total_min) if total_min > 0 else 0.0
            if uid == OTHER_USER:
                # Unused capacity belongs to nobody, so it lives here rather than being
                # split across whoever happened to talk that day. Invoiced overage on an
                # unmeasurable pool (Krisp) joins it, for the same reason.
                plat = day_unused
                over = extra_day
                mins = 0.0
            else:
                plat = 0.0
                over = day_consumed * share
                mins = m
            _set(r, over, plat, mins)
        report.append({
            "date": day, "month": month, "minutes": total_min,
            "stored_livekit": stored_lk,
            "new_overage": day_consumed, "new_platform": day_unused,
            "new_extra": extra_day,
        })
    return report


def expected_invoice(month: str, plan: str):
    """Return (dollars, has_agent_overage) for what LiveKit billed for `month`.

    The agent-minute overage needs the invoiced minute count, which only a dashboard
    reading carries. Without one, the figure is subscription + extras and is flagged
    as missing the agent overage rather than passed off as the whole bill.
    """
    base = livekit_plan.subscription_usd(plan) + INVOICED_EXTRA_OVERAGE.get(month, 0.0)
    reading = livekit_plan.dashboard_reading(month) or {}
    agent_min = reading.get("agent_session_minutes")
    if agent_min is None or reading.get("partial"):
        return base, False
    return base + reading.get("agent_overage_usd",
                              livekit_plan.overage_usd("agent_session_minutes", plan, agent_min)), True


def summarise(report, plan):
    months = collections.defaultdict(lambda: {"minutes": 0.0, "stored": 0.0, "overage": 0.0,
                                              "platform": 0.0, "extra": 0.0, "days": 0})
    for r in skipped_months(report):
        print(f"SKIPPED {r['month']} (left untouched, nothing written): {r['skipped']}")
    report = _booked(report)
    for r in report:
        m = months[r["month"]]
        m["minutes"] += r["minutes"]
        m["stored"] += r["stored_livekit"]
        m["overage"] += r["new_overage"]
        m["extra"] += r["new_extra"]
        m["platform"] += r["new_platform"]
        m["days"] += 1
    print(f"\n{'month':<9}{'days':>5}{'minutes':>11}{'stored $':>11}"
          f"{'unused $':>12}{'consumed $':>12}{'other ovg':>11}{'new total':>11}{'invoice':>10}{'delta':>9}")
    missing_agent = False
    for month in sorted(months):
        m = months[month]
        new_total = m["platform"] + m["overage"] + m["extra"]
        # The invoice is the subscription for every day of the month, whether or not
        # this store holds every day — so a partially-stored month legitimately shows a
        # gap. Only a FULLY stored month is expected to reconcile.
        full = m["days"] == month_days(month)
        invoice, complete = expected_invoice(month, plan) if full else (None, True)
        if invoice is None:
            inv_s, delta = f"{'partial':>10}", f"{'-':>9}"
        else:
            inv_s = f"{invoice:>9.2f}{'' if complete else '*'}"
            delta = f"{new_total - invoice:>9.2f}"
            missing_agent = missing_agent or not complete
        print(f"{month:<9}{m['days']:>5}{m['minutes']:>11,.0f}{m['stored']:>11.2f}"
              f"{m['platform']:>12.2f}{m['overage']:>12.2f}{m['extra']:>11.2f}"
              f"{new_total:>11.2f}{inv_s}{delta}")
    print("\nNote: a month is only expected to reconcile when every one of its days is "
          "stored — the subscription is owed for the whole month either way.")
    if missing_agent:
        print("* no dashboard reading of that month's agent minutes: the invoice shown is "
              "subscription + extras and leaves out any agent-minute overage.")
    print_statements(report)
    extra = {k: v for k, v in INVOICED_EXTRA_OVERAGE.items() if k in months}
    if extra:
        print(f"Invoiced overage on unmeasurable pools, booked to the `Other` row rather "
              f"than split per user: {extra}")


def _delta(booked, invoice):
    """Rounded to the cent, with no `-0.00` for a sub-cent float residue."""
    return round(booked - invoice, 2) + 0.0


def print_statements(report):
    """Line by line, each month booked from its closed statement against that statement."""
    months = collections.defaultdict(list)
    for r in report:
        if r.get("statement"):
            months[r["month"]].append(r)
    for month, rs in sorted(months.items()):
        st = rs[0]["statement"]
        agent = sum(r["new_overage"] + r["new_platform"] for r in rs)
        pools = sum(r["new_extra"] for r in rs)
        dg = sum(r["new_deepgram"] for r in rs)
        lines = [("agent minutes (subscription + overage)", agent, st["agent"]),
                 ("pool lines (INVOICED_EXTRA_OVERAGE)", pools, st["pools"])]
        if st["inference"] is not None:
            lines.append(("Inference", dg, st["inference"]))
        print(f"\n{month} booked from its closed statement "
              f"({st['billed_minutes']:,} billed agent minutes; "
              f"{sum(r['minutes'] for r in rs):,.0f} measured, which only split the dollars):")
        for name, booked, invoice in lines:
            print(f"  {name:<42}{booked:>11.2f}  invoice {invoice:>10.2f}  delta {_delta(booked, invoice):>7.2f}")
        total = sum(b for _, b, _ in lines)
        invoice = sum(i for _, _, i in lines)
        print(f"  {'total':<42}{total:>11.2f}  invoice {invoice:>10.2f}  delta {_delta(total, invoice):>7.2f}")
        f = rs[0]["deepgram_factor"]
        if f is not None:
            print(f"  voice_deepgram: every row x {f:.6f} (stored {sum(r['stored_deepgram'] for r in rs):,.2f}), "
                  f"so the per-user split is kept")
        print("  The pools replace the accrual already in voice_livekit; they are not added to it.")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--since", default="2026-07-01", help="First date to restate (default 2026-07-01)")
    ap.add_argument("--until", required=True, help="Last date to restate (inclusive)")
    ap.add_argument("--plan", default=livekit_plan.DEFAULT_PLAN, choices=sorted(livekit_plan.PLANS))
    ap.add_argument("--old-rate", type=float, default=0.01,
                    help="The per-minute rate the stored voice_livekit was written with "
                         "(default 0.01). Minutes are recovered by dividing by it.")
    ap.add_argument("--project", default=PROJECT)
    ap.add_argument("--confirm", action="store_true", help="Write. Without this, dry run.")
    ap.add_argument("--force", action="store_true",
                    help="Restate days that already carry a platform figure. Off by "
                         "default: re-running recovers minutes from an overage column "
                         "that is now $0 and would zero the store.")
    args = ap.parse_args()

    if args.old_rate <= 0:
        print("ERROR: --old-rate must be > 0 (it is a divisor)", file=sys.stderr)
        return 2

    rows = fetch_rows(args.since, args.until, args.project)
    if not rows:
        print(f"No rows in {TABLE} for {args.since}..{args.until}")
        return 0

    # Only an OLD-model day is at risk: its minutes would be recovered from a column that
    # is now credits. A current-model day carries its minutes and is booked to a statement.
    already = sorted({str(r["date"]) for r in rows
                      if float(r.get("voice_livekit_platform") or 0.0) > 0
                      and r.get("livekit_minutes") is None})
    if already and not args.force:
        print(f"REFUSED: {len(already)} day(s) already carry voice_livekit_platform "
              f"(first {already[0]}, last {already[-1]}).\nRe-running would recover "
              f"minutes from an overage column that is now $0 and zero the store. "
              f"Pass --force only if you know these days need restating.", file=sys.stderr)
        return 2

    by_date, report = rebook(rows, args.plan, args.old_rate)
    summarise(report, args.plan)

    if not args.confirm:
        n_rows = sum(len(v) for v in by_date.values())
        print(f"\nDRY RUN — {n_rows} row(s) across {len(by_date)} day(s) would be "
              f"rewritten. Re-run with --confirm to write.")
        return 0

    schema = STORES["per_user"]["schema"]
    for day in sorted(by_date):
        n = load_partition(TABLE, _csv_bytes(by_date[day]), schema, day, project=args.project)
        print(f"# wrote {day} ({n} rows)", file=sys.stderr)
    print(f"\nRestated {sum(len(v) for v in by_date.values())} row(s) across {len(by_date)} day(s) in {TABLE}.")
    # This wrote BigQuery only. unit_costs.py reads the LOCAL per-user CSV, so it must
    # come after bq_pull, and it refuses a day whose local rows differ (ENG-1864).
    print(f"BigQuery only was written. Next, in this order:\n"
          f"  bq_pull.py --from {args.since} --to {args.until}     # REQUIRED before unit_costs\n"
          f"  unit_costs.py --force --date {args.since} --until {args.until}\n"
          f"  bq_sink.py --date <d>   (each day)\n"
          f"  bq_pull.py --from {args.since} --to {args.until}     # confirms local == BigQuery")
    return 0


if __name__ == "__main__":
    sys.exit(main())
