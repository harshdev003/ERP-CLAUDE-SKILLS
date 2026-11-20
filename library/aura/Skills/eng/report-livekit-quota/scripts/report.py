"""Report LiveKit agent-session-minute quota + burn for the Aurie voice agent.

Per-vendor quota-watch skill, sibling of report-cartesia-quota. LiveKit Cloud exposes NO
billing/usage API (spend + the plan's included minutes live only in the dashboard), so — like
Cartesia — this quota is DERIVED: agent-session minutes consumed this billing period from the
forwarded GCP logs (the same `livekit_core` minute logic as analyze-livekit-usage) vs the plan's
included agent-session minutes. It reports minutes used / included, % used, burn/day, projected
period-end minutes, projected overage ($0.010/min over included), and a plan recommendation; with
`--alert`, an OK/WARN/CRITICAL verdict.

Plan facts live in `livekit_plan.py` (the plan of record, every pool's allotment, and the rate
card) rather than in constants here, because the same facts are needed by the other LiveKit cost
skills and drifted between them. Override with --plan, --included-minutes, --overage-rate-per-min,
--renewal-day. The LiveKit dashboard remains the source of truth for exact usage.

LiveKit bills several INDEPENDENT pools, and the one everyone watches is the one furthest from
binding: in Aug 2026 agent-session minutes were 11,381 of 50,000 while Krisp minutes were 10,000 of
10,000 and went 955 over — $1.14, the only variable dollar ever paid to LiveKit. So this report
lists every pool. The ones the forwarded logs cannot measure are printed as UNMEASURED with their
allotment rather than omitted: an absent pool reads exactly like a healthy one.

It also reports the EFFECTIVE $/min — (subscription + overage) / minutes actually used — which is
what a voice minute really costs us. The published $0.010/min is the OVERAGE rate, and the plans are
priced so it equals subscription/allotment exactly at 100% utilization, making it the floor rather
than the cost.

Exit codes mirror report-cartesia-quota so a wrapper can gate on `$?`: 0=OK; with --alert 10=WARN /
20=CRITICAL for the verdict; 1=API/query failure and 2=config error stay reserved for failures.

Data source: forwarded logs `projects/firebase-auratech16/logs/aurie-voice-agent` (resource.type
global). Auth via GCP_KEY (SA JSON) or ADC — see livekit_core.py.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import livekit_core as lk  # noqa: E402
import livekit_plan as plan_facts  # noqa: E402

# The pool this report can actually measure from the forwarded logs.
MINUTES_POOL = "agent_session_minutes"
CREDITS_POOL = "inference_credits_usd"
# The drawdown is NOT in the forwarded logs -- the cost store is its only source,
# which is why the pool's `derivable` flag stays False even now that it is reported.
COST_TABLE = "aura-main.aurie_cost.aurie_per_user_daily_cost"

DEFAULT_PLAN = plan_facts.DEFAULT_PLAN
DEFAULT_RENEWAL_DAY = plan_facts.DEFAULT_RENEWAL_DAY
DEFAULT_OVERAGE_PER_MIN = plan_facts.overage_per_unit(MINUTES_POOL, DEFAULT_PLAN)

# Alert exit codes, DISTINCT from failure codes (1=query failure, 2=config).
EXIT = {"OK": 0, "WARN": 10, "CRITICAL": 20}


def billing_period_start(now: datetime, renewal_day: int) -> datetime:
    """Most recent occurrence of `renewal_day` at or before `now` (UTC midnight).

    Renewal day is capped at 28 by the validator so every month has it — no end-of-month clamp.
    """
    if now.day >= renewal_day:
        return now.replace(day=renewal_day, hour=0, minute=0, second=0, microsecond=0)
    first_of_this_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    prev_month_end = first_of_this_month - timedelta(days=1)
    return prev_month_end.replace(day=renewal_day, hour=0, minute=0, second=0, microsecond=0)


def _fmt_int(n: float) -> str:
    return f"{int(round(n)):,}"


def measured_overage_usd(included: float, minutes: float, rate: float) -> float:
    """Overage $ for `minutes` against THIS RUN's allotment and rate.

    Derived from the payload's own `included_minutes` / `overage_rate` rather than the
    plan table, so `--included-minutes` and `--overage-rate-per-min` reach the effective
    rate too. Reading the table here instead let the minutes section and the
    effective-rate numerator disagree about the same overage — and a non-default rate
    could push the quoted $/min back below the floor this report asserts.
    """
    return max(0.0, minutes - included) * rate


def _fmt_usd(n: float) -> str:
    """Dollars keep their cents — a pool metered in USD (inference credits) has fractional
    allotments, and rounding $2.50 to "2" misstates the Build plan's whole balance."""
    return f"${n:,.2f}"


def build_recommendation(plan: str, included: int, consumed: float, projected: float,
                         overage_rate: float) -> tuple[str, str]:
    """Return (headline, detail) for the plan situation."""
    over_proj = projected > included
    if not over_proj:
        return (
            "On track within plan",
            f"Projected ~{_fmt_int(projected)} min is within the {plan_facts.plan_label(plan)} allotment of "
            f"{_fmt_int(included)} min — no overage. No action needed.",
        )
    over_min = projected - included
    over_cost = over_min * overage_rate
    nxt = {"build": "ship", "ship": "scale", "scale": None}[plan]
    tail = ""
    if nxt:
        nxt_included = plan_facts.included(MINUTES_POOL, nxt)
        tail = (f" Next tier {plan_facts.plan_label(nxt)} includes {_fmt_int(nxt_included)} min — evaluate it "
                f"against the overage once the plan's base fees are confirmed (LiveKit doesn't expose "
                f"them; see the dashboard).")
    else:
        tail = " Already on the top published tier — talk to LiveKit about a custom allotment."
    return (
        f"Projected over {plan_facts.plan_label(plan)} — ~${over_cost:.2f} overage",
        f"Projected ~{_fmt_int(projected)} min exceeds the {plan_facts.plan_label(plan)} allotment of "
        f"{_fmt_int(included)} min by ~{_fmt_int(over_min)} min → ~${over_cost:.2f} overage at "
        f"${overage_rate:.3f}/min.{tail}",
    )


def bq_project() -> str:
    """The WAREHOUSE project for the cost store, read from the bundled bq helper."""
    from bq import PROJECT
    return PROJECT


def _span_days(entries: list) -> int:
    """Whole days spanned by a list of log entries, for a truncated read's burn denominator.

    A capped read keeps the newest entries, so the minutes it yields describe the tail of
    the window rather than the whole of it. Burn must therefore be divided by the span the
    entries actually cover; dividing by the full elapsed period understates it, which is the
    same reassuring-direction error the cap itself produced (ENG-1297).
    """
    stamps = sorted(e.get("timestamp") or "" for e in entries)
    stamps = [t for t in stamps if t]
    if len(stamps) < 2:
        return 0
    from datetime import datetime as _dt
    def _p(t):
        t = t.replace("Z", "+00:00")
        try:
            return _dt.fromisoformat(t)
        except ValueError:
            return None
    a, b = _p(stamps[0]), _p(stamps[-1])
    if not a or not b:
        return 0
    return max(1, round((b - a).total_seconds() / 86400))


def stored_session_minutes(period_start_day: str, day: str, project: str, table: str):
    """Agent-session minutes for the billing period, from the cost store.

    Returns ``(minutes, days, last_day)`` or ``None`` when BigQuery cannot be reached.

    **Why the store and not the forwarded logs.** A month of this log stream is roughly
    630k entries, an order of magnitude past any sane `max_entries`, so a windowed walk
    can only ever see its tail: the quota report measured the most recent ~4.5 days of
    September and rendered 2,689 min against LiveKit's own 8,037 — a 3x under-report from
    the one instrument meant to warn before an allotment binds (ENG-1297). The store holds
    the same quantity already aggregated per day by `eng:store-aurie-user-daily-cost`,
    whose `livekit_minutes.py` paces its reads and RAISES on a quota rejection rather than
    returning a partial walk, and it reconciles with the LiveKit dashboard to ~5%.

    This is the same move the Inference pool already made: read the stored column rather
    than re-deriving from logs. The pool's `derivable` flag stays as it is — what changed
    is the source, not whether the logs could ever answer.

    The store lags by up to a day (it writes complete days only), so `last_day` is
    returned and the burn rate is computed over days actually covered. Treating an
    unstored day as a zero-minute day would understate burn, which is the same direction
    of error this function exists to remove.
    """
    from bq import query
    sql = f"""
    SELECT ROUND(COALESCE(SUM(livekit_minutes), 0), 1) AS minutes,
           COUNT(DISTINCT date)                        AS days,
           MAX(date)                                   AS last_day
    FROM `{table}`
    WHERE date >= DATE(@start) AND date <= DATE(@day)
    """
    try:
        rows = query(sql, project, params={"start": period_start_day, "day": day})
    except Exception:  # noqa: BLE001 - unreachable BQ must degrade, never report a wrong figure
        return None
    if not rows or rows[0].get("days") in (None, 0):
        return None
    r = rows[0]
    return float(r["minutes"] or 0.0), int(r["days"] or 0), str(r["last_day"])


def stored_inference_drawdown(day: str, project: str, table: str):
    """Month-to-date Inference credit drawdown from the cost store, with its coverage.

    Returns ``(drawdown_usd, days, unmeasured_days)`` or ``None`` when BigQuery cannot be
    reached — a pool we could not read must fall back to UNMEASURED, never to 0, because
    a zero here reads as "the block is untouched" and that is the one thing it must not
    be mistaken for.

    **How an unmeasured day is told apart from a genuinely quiet one**, using only stored
    columns: a day with LiveKit minutes but *both* `voice_deepgram` and
    `deepgram_inference_drawdown` at zero is a day nothing could measure the STT on —
    exactly the condition `eng:store-aurie-user-daily-cost` tags
    `deepgram_transport_unknown`. A genuinely quiet day has no LiveKit minutes either.
    The distinction matters: summing drawdown across a month containing unmeasured days
    yields a LOWER BOUND, and reporting it as the figure would understate how full the
    block is — the same silent-undercount shape the column was added to remove.
    """
    from bq import query
    sql = f"""
    WITH d AS (
      SELECT date,
             SUM(deepgram_inference_drawdown) AS dd,
             SUM(voice_deepgram)              AS dg,
             SUM(livekit_minutes)             AS lk
      FROM `{table}`
      WHERE date >= DATE_TRUNC(DATE(@day), MONTH) AND date <= DATE(@day)
      GROUP BY date
    )
    SELECT ROUND(COALESCE(SUM(dd), 0), 4) AS drawdown,
           COUNT(*)                       AS days,
           -- COALESCE on BOTH sides is load-bearing. `deepgram_inference_drawdown` is
           -- NULL for every row written before the column existed, SUM(NULL) is NULL,
           -- and `NULL = 0` is NULL rather than TRUE -- so a bare `dd = 0` counts none
           -- of those days and the pool reports "$0.00, 0%, OK" for a month whose
           -- drawdown nothing ever recorded. A comparison that silently never fires is
           -- the same silent-undercount shape this whole column exists to remove.
           COUNTIF(lk > 0 AND COALESCE(dg, 0) = 0 AND COALESCE(dd, 0) = 0)
             AS unmeasured_days
    FROM d
    """
    try:
        rows = query(sql, project, params={"day": day})
    except Exception:  # noqa: BLE001 — unreachable BQ degrades to UNMEASURED, never to 0
        return None
    if not rows:
        return None
    r = rows[0]
    return float(r["drawdown"]), int(r["days"]), int(r["unmeasured_days"])


def build_pool_report(plan: str, measured: dict, coverage: dict | None = None,
                      reading: dict | None = None) -> list[dict]:
    """One row per metered pool on `plan`, measured where the logs can see it.

    `measured` maps a pool key to consumed units. A pool absent from it is reported with
    `measured=None` and `status="UNMEASURED"` — deliberately listed rather than dropped,
    because a pool that does not appear reads exactly like a pool that is fine, and the only
    LiveKit overage we have ever paid was on a pool nothing here was watching (Krisp,
    Aug 2026: 10,000 of 10,000 included, 955 min over, $1.14).
    """
    rows = []
    for key, pool in plan_facts.POOLS.items():
        incl = plan_facts.included(key, plan)
        if incl is None:
            continue  # plan does not meter this pool
        used = measured.get(key)
        # A pool we cannot derive may still have been READ off the dashboard. Prefer the
        # measured value where we have one (it is per-period and self-updating); fall back
        # to the hand-read figure, tagged as such, rather than printing "not derivable" at
        # a reader who is holding the number in another tab.
        source = "measured" if used is not None else None
        # A derived value only wins while it is WHOLE. `inference_credits_usd` sums to a
        # LOWER BOUND across days the store could not measure, and the dashboard carries the
        # actual -- so preferring "measured" unconditionally would print >= $0.00 next to a
        # known $15.35. Derive the allocation, never the total: an actual outranks a bound.
        bounded = bool((coverage or {}).get(key, {}).get("unmeasured_days"))
        if reading and key in reading and (used is None or bounded):
            used = reading[key]
            source = "dashboard"
        row = {
            "source": source,
            "pool": key,
            "label": pool["label"],
            "unit": pool["unit"],
            "included": incl,
            "measured": used,
            "derivable": pool["derivable"],
            "overage_per_unit": plan_facts.overage_per_unit(key, plan),
        }
        cov = (coverage or {}).get(key)
        if used is None:
            row.update({"status": "UNMEASURED", "pct_used": None, "overage_usd": None})
        else:
            # Coverage describes the STORE's derivation. Once the dashboard actual has
            # replaced it, the days the store could not measure say nothing about the value
            # being rendered — carrying the LOWER BOUND note across would caveat an actual.
            partial = (bool(cov and cov.get("unmeasured_days")) if source == "measured"
                       else bool((reading or {}).get("partial")))
            row.update({
                # PARTIAL, not OK: with days nothing could measure, the sum is a LOWER
                # BOUND. Calling it OK would be a confident understatement of how full
                # the block is, which is worse than the honest UNMEASURED it replaced.
                "status": ("OVER" if used > incl
                           else "PARTIAL" if partial else "OK"),
                "pct_used": (used / incl * 100.0) if incl else 0.0,
                "overage_usd": plan_facts.overage_usd(key, plan, used),
            })
            if cov and source == "measured":
                row["coverage"] = cov
        rows.append(row)
    return rows


def render_pools(rows: list[dict]) -> list[str]:
    """Markdown table of every metered pool, measured and unmeasured alike."""
    lines = ["## All metered pools", "",
             "| Pool | Used | Included | % | Overage |",
             "|---|---|---|---|---|"]
    for r in rows:
        # Format by unit: _fmt_int would turn Build's $2.50 of inference credits into "2".
        fmt = _fmt_usd if r["unit"] == "USD" else (lambda v, u=r["unit"]: f"{_fmt_int(v)} {u}")
        if r["measured"] is None:
            used, pct, over = "_not derivable, and not read off the dashboard_", "—", "—"
        elif r.get("source") == "dashboard":
            # Say where it came from in the cell itself: a hand-read figure is only as
            # current as `read_on`, and a reader must not mistake it for a live derivation.
            used = f"{fmt(r['measured'])} _(dashboard)_"
            pct = f"{r['pct_used']:.0f}%" if r["pct_used"] is not None else "—"
            over = _fmt_usd(r["overage_usd"] or 0.0) if r["overage_usd"] else "none"
        elif r["status"] == "PARTIAL":
            # A lower bound, and it says so. `>=` on both the figure and the percentage,
            # because N days of the month measured nothing at all.
            c = r["coverage"]
            used = (f"≥ {fmt(r['measured'])} "
                    f"({c['unmeasured_days']} of {c['days']} days unmeasured)")
            pct = f"≥ {r['pct_used']:.0f}%"
            over = f"${r['overage_usd']:.2f}" if r["overage_usd"] else "none"
        else:
            used = fmt(r["measured"])
            pct = f"{r['pct_used']:.0f}%"
            over = f"${r['overage_usd']:.2f}" if r["overage_usd"] else "none"
        lines.append(f"| {r['label']} | {used} | {fmt(r['included'])} | {pct} | {over} |")
    partial = [r for r in rows if r["status"] == "PARTIAL"]
    if partial:
        # PARTIAL now has two distinct causes and they need different sentences. Days the
        # store could not measure is a coverage gap; a mid-period dashboard read is simply
        # not the whole month yet. Rendering the first sentence for the second case would
        # assert an STT gap that does not exist -- and the old code assumed `coverage` was
        # always present, which raised the moment a dashboard-sourced row became PARTIAL.
        notes, ptd = [], []
        for r in partial:
            cov = r.get("coverage")
            if cov:
                notes.append(
                    f"**{r['label']}** is a LOWER BOUND — {cov['unmeasured_days']} of "
                    f"{cov['days']} days this month recorded no STT measurement at all "
                    f"(the voice agent logs the duration only from v1.11.0), so the block is "
                    f"at least this full and may be fuller")
            else:
                ptd.append(r["label"])
        if ptd:
            # One sentence for all of them: repeating an identical clause per pool is noise
            # that buries the one note that differs.
            notes.insert(0, f"**{', '.join(ptd)}** are PERIOD-TO-DATE — the dashboard was "
                            f"read mid-period, so these are what had accrued by then, not "
                            f"the month's totals")
        lines += ["", "> " + "; ".join(notes)]
    unmeasured = [r["label"] for r in rows if r["measured"] is None]
    # Provenance prints whether or not anything is unmeasured. It used to be gated on
    # `if unmeasured:` — which was fine when a non-derivable pool had no value, but a
    # dashboard read fills them all, so the gate silently removed the "only 1 of 7 is
    # derivable" caveat at exactly the moment the table began mixing three sources.
    # Counted on `derivable`, NOT on "has a value". Inference credits carry a value but
    # come from the COST STORE, so counting them here would print "derivable from the
    # forwarded agent logs" about a pool the logs cannot see — the exact claim this
    # change exists to avoid making.
    derivable = [r for r in rows if r["derivable"]]
    # Split by actual source. A non-derivable pool with a value used to be assumed to
    # come from the cost store; dashboard-filled pools now match that shape too, so the
    # old filter would credit the store with a figure a human typed in — and the
    # omit-unread-keys workflow makes partial readings normal, not exceptional.
    stored = [r["label"] for r in rows
              if not r["derivable"] and r["measured"] is not None
              and r.get("source") != "dashboard"]
    from_dashboard = [r["label"] for r in rows if r.get("source") == "dashboard"]
    line = (f"> Only **{len(derivable)} of {len(rows)}** pools are derivable from the "
            f"forwarded agent logs")
    if stored:
        line += f"; {', '.join(stored)} is read from the cost store instead"
    if from_dashboard:
        line += (f"; {', '.join(from_dashboard)} "
                 f"{'are' if len(from_dashboard) > 1 else 'is'} hand-read off the "
                 f"LiveKit Cloud dashboard")
    if unmeasured:
        line += (f". The rest ({', '.join(unmeasured)}) are listed with their allotment "
                 f"so they are not mistaken for absent — read their usage off the "
                 f"LiveKit Cloud dashboard")
    line += ". A verdict below covers the measured pools ONLY."
    lines += ["", line]
    return lines


def render_dashboard_read(p: dict) -> list[str]:
    """State when the dashboard was last read, and what the invoiced overage was.

    The pools this report cannot derive are knowable only from the LiveKit Cloud dashboard,
    which has no API. Before this, a period nobody had opened rendered identically to one
    confirmed clean — so the August Krisp overage (955 min, $1.14) was found by looking
    rather than by anything noticing, and "book it once it is big enough" had no trigger.
    An unread period is now the loud case.
    """
    reading = p.get("dashboard_reading")
    overage = p.get("invoiced_overage")
    threshold = p.get("overage_booking_threshold_usd") or 0.0
    lines = ["## Dashboard read", ""]
    if not reading:
        lines += [
            "> \u26a0\ufe0f **This billing period has not been read off the LiveKit Cloud "
            "dashboard.** Six of the seven pools are not derivable from anything we collect, "
            "so their usage and any invoiced overage are **unknown for this period** — not "
            "zero. Open project `p_5bw0of3laoy` \u2192 Settings \u2192 Billing and add an "
            "entry to `DASHBOARD_READINGS` in `livekit_plan.py`.",
        ]
        return lines
    partial = " (period-to-date — read mid-period)" if reading.get("partial") else ""
    lines.append(f"- Read on `{reading.get('read_on', 'unknown')}`{partial}")
    if overage is None:
        lines.append("- Invoiced overage: **not recorded in this reading** — unknown, not zero")
    else:
        amount, booked = overage
        if amount <= 0:
            lines.append("- Invoiced overage: **$0.00**, confirmed against the invoice")
        elif reading.get("partial"):
            lines.append(f"- Invoiced overage to date: **{_fmt_usd(amount)}**, **not booked yet** "
                         f"— the period is still open; it is booked from the final invoice.")
        elif booked:
            lines.append(f"- Invoiced overage: **{_fmt_usd(amount)}** — at or above the "
                         f"{_fmt_usd(threshold)} threshold, so it is booked to the `Other` row")
        else:
            # Reported, deliberately unbooked. The alternative -- omitting it -- makes the
            # month reconcile to a number that is not the invoice, silently.
            lines.append(f"- Invoiced overage: **{_fmt_usd(amount)}**, **not booked** "
                         f"(below the {_fmt_usd(threshold)} threshold). The cost store "
                         f"reconciles to the subscription; this amount is real and sits "
                         f"outside it.")
    return lines


def render_effective_cost(p: dict) -> list[str]:
    """The fully-loaded $/min — what a voice minute actually costs us."""
    lines = ["## Effective cost per minute", ""]
    sub = p["subscription_usd"]
    eff, proj = p["effective_rate_per_min"], p["projected_effective_rate_per_min"]
    lines.append(f"- Subscription: **${sub:,.2f}/mo** ({plan_facts.plan_label(p['plan'])})")
    if eff is None:
        lines.append("- Effective $/min: n/a (no minutes consumed this period)")
    else:
        measured_overage = measured_overage_usd(
            p["included_minutes"], p["consumed_minutes"], p["overage_rate"])
        numerator = (f"${sub:,.2f} + ${measured_overage:,.2f} overage" if measured_overage
                     else f"${sub:,.2f}")
        lines.append(f"- Effective so far: **${eff:.4f}/min** ({numerator} / "
                     f"{_fmt_int(p['consumed_minutes'])} min)")
    if proj is not None:
        lines.append(f"- Projected period-end: **${proj:.4f}/min** at "
                     f"~{_fmt_int(p['projected_minutes'])} min")
    lines.append("- Covers the subscription plus agent-session overage. Overage on the pools this "
                 "report cannot measure (see the table below) is NOT included.")
    # The floor claim holds only for a plan with a non-zero subscription: it comes from
    # subscription/allotment equalling the overage rate at full utilization. Build's
    # subscription is $0, so its effective rate genuinely starts at $0 and RISES toward the
    # overage rate — asserting the floor there would print a statement the numbers contradict.
    if sub > 0:
        lines.append(f"- Overage rate: ${p['overage_rate']:.3f}/min — the FLOOR, not the cost. This "
                     f"plan is priced so subscription/allotment equals it exactly at 100% "
                     f"utilization, so the effective rate is always at least that and falls as "
                     f"volume grows.")
    else:
        lines.append(f"- Overage rate: ${p['overage_rate']:.3f}/min. On a $0 plan the effective rate "
                     f"starts at $0 and RISES toward it as overage accrues — the floor property of "
                     f"the paid plans does not apply here.")
    lines.append("")
    return lines


def render(p: dict) -> str:
    lines = ["# LiveKit Agent-Minute Quota — estimate", ""]
    rem = p["included_minutes"] - p["consumed_minutes"]
    status_bit = (f"OVER by ~{_fmt_int(-rem)} min" if rem < 0
                  else f"~{_fmt_int(rem)} min remaining")
    lines.append(
        f"**{plan_facts.plan_label(p['plan'])} plan** · consumed **{_fmt_int(p['consumed_minutes'])} / "
        f"{_fmt_int(p['included_minutes'])} min** ({p['pct_used']:.0f}% used) · {status_bit}"
    )
    lines.append(f"Projected period-end: **~{_fmt_int(p['projected_minutes'])} min** · "
                 f"**{p['recommendation']['headline']}**")
    lines.append("")
    lines.append("## Agent-session minutes")
    lines.append("")
    lines.append(f"- Billing period: `{p['period_start']}` → `{p['period_end']}` "
                 f"({p['period_days_elapsed']} of ~{p['period_days_total']} days elapsed)")
    # Provenance belongs in the rendered report, not only on stderr: the figure below is a
    # period total whose completeness depends entirely on where it came from, and a reader
    # pasting this into Slack does not see stderr.
    src = p.get("minutes_source") or "forwarded logs"
    through = p.get("minutes_through")
    covered = p.get("days_covered")
    if p.get("sessions") is None:
        lines.append(f"- Sessions this period: _not counted by this source ({src})_")
    else:
        lines.append(f"- Sessions this period: {p['sessions']}")
    if through and covered is not None:
        lines.append(f"- Minutes source: {src} — {covered} day(s) through `{through}`")
    lines.append(f"- Consumed (derived): **{_fmt_int(p['consumed_minutes'])} min**")
    lines.append(f"- Included ({plan_facts.plan_label(p['plan'])}): {_fmt_int(p['included_minutes'])} min")
    lines.append(f"- Burn rate: **{p['burn_per_day']:.0f} min/day**")
    lines.append(f"- Days left in period: {p['period_days_remaining']}")
    lines.append(f"- Projected period-end: **~{_fmt_int(p['projected_minutes'])} min**")
    if p["projected_minutes"] > p["included_minutes"]:
        over = p["projected_minutes"] - p["included_minutes"]
        lines.append(f"- Projected overage: ~{_fmt_int(over)} min → **~${over * p['overage_rate']:.2f}** "
                     f"at ${p['overage_rate']:.3f}/min")
    else:
        lines.append("- Projected overage: none (within allotment)")
    lines.append("")
    lines.extend(render_effective_cost(p))
    lines.extend(render_pools(p["pools"]))
    lines += [""] + render_dashboard_read(p)
    lines.append("")
    lines.append("## Recommendation")
    lines.append("")
    lines.append(p["recommendation"]["detail"])
    lines.append("")
    lines.append("> ⚠️ DERIVED from the forwarded agent logs: minutes are `(last_ts − first_ts)` per "
                 "LiveKit room (no explicit duration field is logged), and the plan's included minutes "
                 "/ overage rate are config flags (LiveKit exposes no billing API). The **LiveKit Cloud "
                 "dashboard is the source of truth** for exact usage and spend.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Alerting
# ---------------------------------------------------------------------------

def build_alert(p: dict, warn_pct: float, crit_pct: float) -> dict:
    """OK/WARN/CRITICAL on current AND projected % of included minutes; higher severity wins."""
    status = "OK"
    reasons: list[str] = []
    ptd_pools: list[str] = []

    def bump(level: str) -> None:
        nonlocal status
        order = {"OK": 0, "WARN": 1, "CRITICAL": 2}
        if order[level] > order[status]:
            status = level

    pct = p["pct_used"]
    proj_pct = (p["projected_minutes"] / p["included_minutes"] * 100.0) if p["included_minutes"] else 0.0
    if pct >= crit_pct:
        bump("CRITICAL")
        reasons.append(f"minutes at {pct:.0f}% of included (>= {crit_pct:.0f}% crit)")
    elif pct >= warn_pct:
        bump("WARN")
        reasons.append(f"minutes at {pct:.0f}% of included (>= {warn_pct:.0f}% warn)")
    if proj_pct >= crit_pct:
        bump("CRITICAL")
        reasons.append(f"projected ~{proj_pct:.0f}% of included (>= {crit_pct:.0f}% crit)")
    elif proj_pct >= warn_pct:
        bump("WARN")
        reasons.append(f"projected ~{proj_pct:.0f}% of included (>= {warn_pct:.0f}% warn)")

    projected_overage_usd = None
    if p["projected_minutes"] > p["included_minutes"]:
        projected_overage_usd = round((p["projected_minutes"] - p["included_minutes"]) * p["overage_rate"], 2)

    # Scope the verdict explicitly. This alert can only see agent-session minutes, and that is
    # the pool furthest from binding — so an unqualified OK is a claim the data does not
    # support, and is how a Krisp overage sat unnoticed behind 23% session-minute usage.
    # Any OTHER pool we can actually measure must join the verdict. Before this, a pool
    # left `unmeasured_pools` the moment it gained a value but never entered the OK/WARN/
    # CRITICAL check — so `--alert` could print OK while that pool was OVER, which is the
    # unqualified-OK shape the Krisp comment above exists to prevent, reintroduced by the
    # very change that made the pool measurable.
    scoped = ["agent-session minutes"]
    for r in p["pools"]:
        if r["pool"] == MINUTES_POOL or r["measured"] is None:
            continue
        scoped.append(r["label"])
        if r["status"] == "OVER":
            bump("CRITICAL")
            reasons.append(f"{r['label']} at {r['pct_used']:.0f}% of included — OVER")
        elif r["pct_used"] is not None and r["pct_used"] >= warn_pct:
            bump("WARN")
            reasons.append(f"{r['label']} at {r['pct_used']:.0f}% of included "
                           f"(>= {warn_pct:.0f}% warn)")
        if r["status"] == "PARTIAL":
            # A floor can only UNDERstate, so a quiet verdict on one is not a clean bill.
            # PARTIAL has two causes and only one carries `coverage`: a store derivation
            # with unmeasured days, or a dashboard read taken mid-period. Assuming the
            # first raised KeyError here on the live September path — the same assumption
            # already fixed in the pools footnote, left standing in its sibling.
            c = r.get("coverage")
            if c:
                reasons.append(f"{r['label']} is a lower bound — {c['unmeasured_days']} of "
                               f"{c['days']} days unmeasured, so it may be fuller than this")
            else:
                ptd_pools.append(r["label"])

    if ptd_pools:
        # One line for all of them. Six identical sentences bury the reason that differs,
        # which on an alert surface is the only one a reader needs to see.
        reasons.append(f"{', '.join(ptd_pools)} "
                       f"{'are' if len(ptd_pools) > 1 else 'is'} period-to-date — the "
                       f"dashboard was read mid-period, so period totals may be higher")

    unmeasured = [r["label"] for r in p["pools"] if r["measured"] is None]

    return {
        "status": status,
        "reasons": reasons,
        "projected_overage_usd": projected_overage_usd,
        "scope": " + ".join(scoped),
        "unmeasured_pools": unmeasured,
    }


def render_alert(p: dict, alert: dict) -> str:
    icon = {"OK": "✅", "WARN": "⚠️", "CRITICAL": "🚨"}[alert["status"]]
    lines = ["", f"## Alert: {icon} {alert['status']} — LiveKit {plan_facts.plan_label(p['plan'])} plan"]
    for r in (alert["reasons"] or ["all thresholds clear"]):
        lines.append(f"- {r}")
    if alert["status"] != "OK" and alert["projected_overage_usd"] is not None:
        lines.append(f"- projected overage this period: ~${alert['projected_overage_usd']:.2f} "
                     f"(at ${p['overage_rate']:.3f}/min over the allotment)")
    if alert["unmeasured_pools"]:
        lines.append(f"- SCOPE: {alert['scope']}. Not covered here: "
                     f"{', '.join(alert['unmeasured_pools'])} — check the dashboard.")
    lines.append("- DERIVED from forwarded logs — confirm exact usage on the LiveKit dashboard.")
    return "\n".join(lines)


def post_to_slack(text: str, webhook: str) -> bool:
    try:
        import requests
    except ImportError:
        print("# requests not installed; cannot post to Slack", file=sys.stderr)
        return False
    resp = requests.post(webhook, json={"text": text}, timeout=15)
    return 200 <= resp.status_code < 300


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        description="Report LiveKit agent-session-minute quota vs the plan, derived from the logs."
    )
    parser.add_argument("--plan", choices=sorted(plan_facts.PLANS), default=DEFAULT_PLAN,
                        help=f"LiveKit plan (default {DEFAULT_PLAN})")
    parser.add_argument("--included-minutes", type=int,
                        help="Override included agent-session minutes (defaults to the plan's)")
    parser.add_argument("--overage-rate-per-min", type=float, default=DEFAULT_OVERAGE_PER_MIN,
                        help=f"Overage $/min beyond included (default {DEFAULT_OVERAGE_PER_MIN})")
    parser.add_argument("--renewal-day", type=int, default=DEFAULT_RENEWAL_DAY,
                        help=f"Day of month the plan renews, 1-28 (default {DEFAULT_RENEWAL_DAY})")
    parser.add_argument("--project", help="Override the GCP project derived from the SA key")
    parser.add_argument("--max-entries", type=int, default=50000, help="Cap log entries fetched")
    parser.add_argument("--json", help="Write the structured payload to this path")
    parser.add_argument("--alert", action="store_true",
                        help="Alert mode: print an OK/WARN/CRITICAL verdict and exit by severity "
                             "(10=WARN, 20=CRITICAL; 1=query failure and 2=config stay reserved).")
    parser.add_argument("--warn-pct", type=float, default=80.0,
                        help="WARN when minutes used OR projected reach this %% of included (default 80).")
    parser.add_argument("--crit-pct", type=float, default=100.0,
                        help="CRITICAL at this %% of included, i.e. overage (default 100).")
    parser.add_argument("--slack", action="store_true",
                        help="Post the alert to SLACK_WEBHOOK_URL when status != OK (requires --alert).")
    args = parser.parse_args()

    if not 1 <= args.renewal_day <= 28:
        print("ERROR: --renewal-day must be 1-28 (so every month has that day)", file=sys.stderr)
        return 2

    plan = args.plan
    included = args.included_minutes if args.included_minutes is not None else plan_facts.included(MINUTES_POOL, plan)
    # --overage-rate-per-min defaults to the DEFAULT plan's rate at parse time; re-resolve it
    # against the plan actually chosen so `--plan build` doesn't silently price at Scale.
    if args.overage_rate_per_min == DEFAULT_OVERAGE_PER_MIN:
        args.overage_rate_per_min = plan_facts.overage_per_unit(MINUTES_POOL, plan)

    try:
        auth = lk.get_client(args.project)
    except lk.AuthError as exc:
        print(f"ERROR: GCP auth failed: {exc}", file=sys.stderr)
        return 2
    print(f"# auth: project={auth.project}", file=sys.stderr)

    now = datetime.now(tz=timezone.utc)
    period_start = billing_period_start(now, args.renewal_day)
    period_days_elapsed = max(1, (now - period_start).days)
    next_start = billing_period_start(now + timedelta(days=32), args.renewal_day)
    while next_start <= period_start:
        next_start = billing_period_start(next_start + timedelta(days=32), args.renewal_day)
    period_days_total = (next_start - period_start).days
    period_days_remaining = max(0, period_days_total - period_days_elapsed)

    filt = lk.base_filter(lk.range_filter(period_start, now))
    print(f"# query: plan={plan} included={included} renewal-day={args.renewal_day} "
          f"period={period_start.strftime('%Y-%m-%dT%H:%M:%SZ')} "
          f"({period_days_elapsed}/{period_days_total}d)", file=sys.stderr)
    # Agent-session minutes come from the cost store, not a log walk: a month of this
    # stream cannot fit under any sane cap, so a walk only ever sees its tail (ENG-1297).
    # The log path remains as the fallback for an unreachable warehouse, and it now
    # reports its own truncation instead of passing a short total off as the period's.
    minutes_source = "cost store"
    minutes_through = now.strftime("%Y-%m-%d")
    sessions = None   # log-derived only; the store aggregates minutes, not session counts
    days_covered = period_days_elapsed
    stored = stored_session_minutes(
        period_start.strftime("%Y-%m-%d"), now.strftime("%Y-%m-%d"), bq_project(), COST_TABLE)
    if stored is not None:
        consumed_minutes, days_covered, minutes_through = stored
        print(f"# minutes: {consumed_minutes:.1f} min over {days_covered} stored day(s) "
              f"through {minutes_through} (source: cost store)", file=sys.stderr)
    else:
        minutes_source = "forwarded logs (cost store unreachable)"
        try:
            entries = list(lk.list_entries(auth, filt, max_entries=args.max_entries))
            truncated = False
        except lk.TruncatedReadError as exc:
            entries, truncated = exc.entries, True
        sessions = lk.build_sessions(entries)
        consumed_minutes = sum(s.minutes for s in sessions.values())
        if truncated:
            # A truncated read keeps the NEWEST entries, so its minutes belong to the tail
            # of the period, not to all of it. Dividing them by the full elapsed days is
            # the exact undercount-in-the-reassuring-direction this change exists to
            # remove, so the burn denominator has to shrink to the span actually covered.
            days_covered = _span_days(entries) or 1
            minutes_through = now.strftime("%Y-%m-%d")
            minutes_source = (f"forwarded logs, TRUNCATED at the {args.max_entries}-entry cap "
                              f"— period total is a LOWER BOUND (only the most recent "
                              f"{days_covered} day(s) are represented)")
            print(f"# WARNING: log read truncated at {args.max_entries} entries; "
                  f"{consumed_minutes:.1f} min covers only the most recent {days_covered} "
                  f"day(s) and UNDERCOUNTS the period", file=sys.stderr)
        print(f"# fetched: {len(entries)} entries; {len(sessions)} sessions; "
              f"consumed={consumed_minutes:.1f} min", file=sys.stderr)

    burn_per_day = consumed_minutes / days_covered if days_covered else 0.0
    projected_minutes = consumed_minutes + burn_per_day * max(
        0, period_days_total - days_covered)
    pct_used = (consumed_minutes / included * 100.0) if included else 0.0
    headline, detail = build_recommendation(plan, included, consumed_minutes, projected_minutes,
                                            args.overage_rate_per_min)

    # The Inference credit block is the second pool that stopped being UNMEASURED, and it
    # became measurable only because `eng:store-aurie-user-daily-cost` now STORES the
    # drawdown -- it is not derivable from the forwarded logs, which is why the pool's
    # `derivable` flag stays False. Unreachable BigQuery degrades to UNMEASURED, never
    # to 0: a zero reads as "block untouched", which is the one wrong answer here.
    pool_measured = {MINUTES_POOL: consumed_minutes}
    pool_coverage = {}
    # `bq.PROJECT`, never `args.project`/`auth.project` -- those name the Cloud LOGGING
    # project (firebase-auratech16) and `--project` is a log override. The SA holds
    # bigquery.jobUser on the warehouse (aura-main) only, so billing the job to the logging
    # project fails, and the failure is swallowed into UNMEASURED: the pool would silently
    # stay blank on every default run. Reading a stored column is a warehouse question, and
    # the project must come from the warehouse helper.
    drawdown = stored_inference_drawdown(
        now.strftime("%Y-%m-%d"), bq_project(), COST_TABLE)
    if drawdown is not None:
        usd, days, unmeasured_days = drawdown
        pool_measured[CREDITS_POOL] = usd
        pool_coverage[CREDITS_POOL] = {"days": days, "unmeasured_days": unmeasured_days}
        print(f"# inference credits: ${usd:,.2f} drawn month-to-date over {days} day(s), "
              f"{unmeasured_days} unmeasured", file=sys.stderr)
    else:
        print("# inference credits: cost store unreachable — reporting UNMEASURED",
              file=sys.stderr)

    payload = {
        "derived": True,
        "dashboard_is_source_of_truth": True,
        "plan": plan,
        "included_minutes": included,
        "consumed_minutes": consumed_minutes,
        "pct_used": pct_used,
        # None, never 0, when minutes came from the store: a session count of zero reads as
        # "nothing ran", which is the one thing an unavailable count must not be mistaken for.
        "sessions": (len(sessions) if sessions is not None else None),
        "minutes_source": minutes_source,
        "minutes_through": minutes_through,
        "days_covered": days_covered,
        "period_start": period_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "period_end": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "period_days_elapsed": period_days_elapsed,
        "period_days_total": period_days_total,
        "period_days_remaining": period_days_remaining,
        "burn_per_day": burn_per_day,
        "projected_minutes": projected_minutes,
        "overage_rate": args.overage_rate_per_min,
        "subscription_usd": plan_facts.subscription_usd(plan),
        # Overage is part of the invoice, so it belongs in the effective rate. Omitting it made
        # the quoted $/min fall BELOW the overage floor past 100% utilization — i.e. exactly when
        # this report's CRITICAL path fires, it would have contradicted its own "the overage rate
        # is the floor" claim. Only the measured pool's overage is included; the pools this report
        # cannot see (Krisp et al) are called out in the all-pools table instead.
        "effective_rate_per_min": plan_facts.effective_rate_per_min(
            plan, consumed_minutes,
            extra_overage_usd=measured_overage_usd(
                included, consumed_minutes, args.overage_rate_per_min),
        ),
        "projected_effective_rate_per_min": plan_facts.effective_rate_per_min(
            plan, projected_minutes,
            extra_overage_usd=measured_overage_usd(
                included, projected_minutes, args.overage_rate_per_min),
        ),
        "plan_facts_verified": plan_facts.PLAN_FACTS_VERIFIED,
        "pools": build_pool_report(plan, pool_measured, pool_coverage,
                                   reading=plan_facts.dashboard_reading(
                                       period_start.strftime("%Y-%m"))),
        # Three distinct states, kept distinct: a figure that was read and is below the
        # booking threshold, a confirmed zero, and NOBODY LOOKED. The last one is what a
        # bare 0.00 used to be indistinguishable from.
        "dashboard_reading": plan_facts.dashboard_reading(period_start.strftime("%Y-%m")),
        "invoiced_overage": plan_facts.unbooked_overage(period_start.strftime("%Y-%m")),
        "overage_booking_threshold_usd": plan_facts.OVERAGE_BOOKING_THRESHOLD_USD,
        "recommendation": {"headline": headline, "detail": detail},
    }

    print(render(payload))

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, default=str)
        print(f"\nWrote {args.json}", file=sys.stderr)

    if args.alert:
        alert = build_alert(payload, args.warn_pct, args.crit_pct)
        alert_text = render_alert(payload, alert)
        print(alert_text)
        if args.slack and alert["status"] != "OK":
            webhook = os.environ.get("SLACK_WEBHOOK_URL")
            if not webhook:
                print("# --slack given but SLACK_WEBHOOK_URL not set; skipping post", file=sys.stderr)
            elif post_to_slack(alert_text, webhook):
                print("# posted alert to Slack", file=sys.stderr)
            else:
                print("# Slack post failed", file=sys.stderr)
        return EXIT[alert["status"]]

    return 0


if __name__ == "__main__":
    sys.exit(main())
