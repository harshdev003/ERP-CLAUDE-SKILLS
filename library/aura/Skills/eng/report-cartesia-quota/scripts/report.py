#!/usr/bin/env python3
"""
Cartesia credit-quota estimator for the Aura workspace.

Cartesia's dashboard shows "Model Credits Remaining" and "Voice Agent Dollars
Remaining", but there is NO public API for balance / plan / renewal — every
such endpoint (/balance /subscription /account /plan /billing) 404s (verified).
So this script does NOT read the remaining balance. It DERIVES it from actual
consumption over the current billing period versus the known plan allotment,
and labels the result as an approximation. The Cartesia dashboard stays the
source of truth for the exact remaining figure.

What it does:

  1. Sum /usage/credits over the current billing period → credits consumed
     (ACTUAL: TTS = 1 credit/char, STT = 3 credits/sec, Voice Changer = 15/sec).
  2. derived_remaining = allotment − consumed (APPROXIMATE — will not match the
     dashboard; rollover / period-boundary / grants aren't API-exposed. We have
     observed the API-derived consumed figure running ~400k ABOVE the dashboard).
  3. Burn rate (avg credits/day over the elapsed period, plus last-7-day avg)
     → projected period-end consumption.
  4. Voice-Agent-$ pool from /usage/agents (cents/100 = USD spent) vs the plan's
     prepaid agent dollars (same approximation caveat).
  5. A recommendation: finishing over plan (overage applies), consider Scale
     ($299 / 8M credits), or "even Scale short — talk to Cartesia / Enterprise".

Plan facts are CONFIG, not API-exposed — they're flags with defaults below.

Usage:
    python report.py                                   # Scale plan, renewal day 21
    python report.py --plan startup
    python report.py --allotment 1250000               # override included credits
    python report.py --renewal-day 1                   # if the renewal day changes
    python report.py --overage-rate-per-million 12.0   # if you know the true rate
    python report.py --json out.json

Env:
    CARTESIA_ADMIN_API_KEY   Required. Sent as `Authorization: Bearer <key>`.
                             Read from env only — never printed, argv'd, or filed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

try:
    import requests
except ImportError:
    sys.exit("Missing dependency: pip3 install requests")


CARTESIA_API = "https://api.cartesia.ai"
CARTESIA_VERSION = "2026-03-01"
CREDITS_PATH = "/usage/credits"
AGENTS_PATH = "/usage/agents"

# Plan config — Cartesia does NOT expose these via API, so they live here as
# defaults overridable by flags. Contract rates confirmed 2026-07-02 from
# cartesia.ai/pricing: (monthly flat $, included credits, prepaid agent $,
# overage $ per 1M credits beyond the allotment). Free has no overage (requests
# are blocked once credits run out).
PLANS: dict[str, dict] = {
    "free": {"monthly_usd": 0, "credits": 20_000, "agent_usd": 1, "overage_per_m": None},
    "pro": {"monthly_usd": 5, "credits": 100_000, "agent_usd": 5, "overage_per_m": 65.0},
    "startup": {"monthly_usd": 49, "credits": 1_250_000, "agent_usd": 49, "overage_per_m": 45.0},
    "scale": {"monthly_usd": 299, "credits": 8_000_000, "agent_usd": 299, "overage_per_m": 38.0},
}


def monthly_plan_cost(plan_key: str, monthly_credits: float) -> float:
    """Full monthly $ for a plan at a given monthly credit consumption.

    cost = flat monthly fee + overage on credits beyond the plan's allotment.
    Overage only applies to paid tiers with an overage rate (Free is blocked, no overage).
    """
    p = PLANS[plan_key]
    flat = float(p["monthly_usd"])
    over_rate = p.get("overage_per_m")
    if over_rate is None:
        return flat
    over_credits = max(0.0, monthly_credits - p["credits"])
    return flat + (over_credits / 1_000_000.0) * over_rate


def scale_breakeven_credits(current_plan: str) -> Optional[float]:
    """Monthly credit consumption at which Scale's flat fee ties the current plan's
    flat+overage cost (valid while that point is below Scale's included allotment, which it
    is for Startup→Scale ≈ 6.81M). Returns None if the current plan has no overage rate."""
    cur = PLANS[current_plan]
    over_rate = cur.get("overage_per_m")
    if not over_rate or current_plan == "scale":
        return None
    scale_flat = float(PLANS["scale"]["monthly_usd"])
    return cur["credits"] + (scale_flat - float(cur["monthly_usd"])) / over_rate * 1_000_000.0

DEFAULT_PLAN = "scale"
DEFAULT_RENEWAL_DAY = 21


# ---------------------------------------------------------------------------
# API client
# ---------------------------------------------------------------------------

def _get(path: str, params: dict, api_key: str) -> dict:
    """GET against the Cartesia API with the required version header."""
    url = f"{CARTESIA_API}{path}"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Cartesia-Version": CARTESIA_VERSION,
    }
    r = requests.get(url, headers=headers, params=params, timeout=60)
    if not r.ok:
        sys.exit(f"Cartesia {r.status_code} on {path}: {r.text[:300]}")
    return r.json()


def fetch_credits(start_ts: str, api_key: str, *, interval: str | None = None) -> list[dict]:
    """Actual TTS + STT + Voice-Changer credit consumption since start_ts."""
    params: dict = {"start_ts": start_ts}
    if interval:
        params["interval"] = interval
    return _get(CREDITS_PATH, params, api_key).get("data", [])


def fetch_agents(start_ts: str, api_key: str, *, interval: str | None = None) -> list[dict]:
    """Actual voice-agent (agent-calling + telephony) usage since start_ts."""
    params: dict = {"start_ts": start_ts}
    if interval:
        params["interval"] = interval
    return _get(AGENTS_PATH, params, api_key).get("data", [])


# ---------------------------------------------------------------------------
# Billing-period math
# ---------------------------------------------------------------------------

def billing_period_start(now: datetime, renewal_day: int) -> datetime:
    """Most recent occurrence of `renewal_day` at or before `now` (UTC midnight).

    Renewal day is capped at 28 by the flag validator, so every month has it —
    no end-of-month clamp needed.
    """
    if now.day >= renewal_day:
        return now.replace(day=renewal_day, hour=0, minute=0, second=0, microsecond=0)
    # Roll back into the previous month.
    first_of_this_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    prev_month_end = first_of_this_month - timedelta(days=1)
    return prev_month_end.replace(day=renewal_day, hour=0, minute=0, second=0, microsecond=0)


def rfc3339(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _fmt_int(n: float) -> str:
    return f"{int(round(n)):,}"


def build_recommendation(
    plan: str, allotment: int, consumed: int, projected: float
) -> tuple[str, str]:
    """Return (short_headline_phrase, detail_paragraph)."""
    # Next tier up (for the "consider Scale" nudge).
    over_now = consumed > allotment
    over_projected = projected > allotment

    # Decide on Scale by COST, not just volume: compare the full monthly cost (flat + overage)
    # of the current plan vs Scale at the projected monthly consumption. Being over the
    # allotment does NOT by itself mean Scale is cheaper — at low overage volumes Startup's
    # $49 + overage beats Scale's $299 flat (Startup→Scale break-even ≈ 6.81M credits/mo).
    cur_cost = monthly_plan_cost(plan, projected)
    scale_cost = monthly_plan_cost("scale", projected)
    savings = cur_cost - scale_cost  # > 0 ⇒ Scale is cheaper
    be = scale_breakeven_credits(plan)
    be_txt = f" Scale breaks even at ~{_fmt_int(be)} credits/mo." if be else ""

    # Already on the top (Scale) tier.
    if plan == "scale":
        if over_projected:
            return (
                "Even Scale is short — talk to Cartesia",
                f"Projected ~{_fmt_int(projected)} credits exceeds the Scale allotment of "
                f"{_fmt_int(allotment)} — reach out to Cartesia about Enterprise / a custom allotment.",
            )
        return (
            "On track within plan",
            f"Projected ~{_fmt_int(projected)} credits is within the Scale allotment of "
            f"{_fmt_int(allotment)}. No action needed.",
        )

    # Within the effective allotment — no overage, nothing to decide.
    if not over_now and not over_projected:
        return (
            "On track within plan",
            f"Projected ~{_fmt_int(projected)} credits is within the allotment of "
            f"{_fmt_int(allotment)} — no overage. No action needed.",
        )

    # Over the allotment — overage applies on the current plan. Recommend Scale only if it's
    # actually cheaper at projected volume.
    cmp_line = (
        f"At projected ~{_fmt_int(projected)} credits/mo: {plan.title()} ≈ ${cur_cost:.0f}/mo "
        f"(${PLANS[plan]['monthly_usd']} flat + overage) vs Scale ≈ ${scale_cost:.0f}/mo."
    )
    if savings > 0.5:
        return (
            f"Move to Scale — saves ~${savings:.0f}/mo",
            cmp_line + f" Scale is now cheaper — **save ~${savings:.0f}/mo** by upgrading.{be_txt}",
        )
    return (
        f"Stay on {plan.title()} — overage still cheaper than Scale",
        cmp_line + f" Staying on {plan.title()} is ~${-savings:.0f}/mo cheaper even with overage — "
        f"don't upgrade yet.{be_txt}",
    )


def render(payload: dict) -> str:
    p = payload
    lines: list[str] = []
    over_by = p["allotment"] - p["consumed"]
    pct = p["pct_used"]

    # Headline.
    lines.append("# Cartesia Credit Quota — estimate")
    lines.append("")
    status_bit = (
        f"OVER by ~{_fmt_int(-over_by)} credits"
        if over_by < 0
        else f"~{_fmt_int(over_by)} credits remaining (approx)"
    )
    lines.append(
        f"**{p['plan'].title()} plan** · consumed **{_fmt_int(p['consumed'])} / "
        f"{_fmt_int(p['allotment'])}** ({pct:.0f}% used) · {status_bit}"
    )
    lines.append(
        f"Projected period-end: **~{_fmt_int(p['projected_period_end'])} credits** · "
        f"**{p['recommendation']['headline']}**"
    )
    lines.append("")

    # Credit breakdown.
    lines.append("## Credits")
    lines.append("")
    lines.append(f"- Billing period: `{p['period_start']}` → `{p['period_end']}` "
                 f"({p['period_days_elapsed']} of ~{p['period_days_total']} days elapsed)")
    lines.append(f"- Consumed this period (actual): **{_fmt_int(p['consumed'])}** credits")
    lines.append(f"- Allotment ({p['plan'].title()}): {_fmt_int(p['allotment'])} credits")
    remaining_bit = (
        f"**~{_fmt_int(p['remaining_approx'])}** (approx)"
        if p["remaining_approx"] >= 0
        else f"**OVER by ~{_fmt_int(-p['remaining_approx'])}** (approx)"
    )
    lines.append(f"- Derived remaining: {remaining_bit}")
    recent_days = p.get("burn_recent_days", 7)
    recent_label = f"last {recent_days}d" if recent_days else "recent"
    lines.append(f"- Burn rate: **{_fmt_int(p['burn_per_day'])}/day** avg "
                 f"({recent_label}: {_fmt_int(p['burn_per_day_7d'])}/day)")
    lines.append(f"- Days left in period: {p['period_days_remaining']}")
    lines.append(f"- Projected period-end: **~{_fmt_int(p['projected_period_end'])}** credits")
    overage_state = (
        "overage billing applies (projected over allotment)"
        if p["projected_period_end"] > p["allotment"]
        else "within allotment (projected)"
    )
    if p["overage_rate_per_million"] is not None and p["projected_period_end"] > p["allotment"]:
        over_millions = (p["projected_period_end"] - p["allotment"]) / 1_000_000
        est = over_millions * p["overage_rate_per_million"]
        overage_state += f" — est. overage cost ~${est:.2f} at ${p['overage_rate_per_million']:.2f}/M"
    else:
        overage_state += " — overage rate unknown (Cartesia doesn't publish it)"
    lines.append(f"- Overage: {overage_state}")
    lines.append("")

    # Voice-Agent dollars.
    lines.append("## Voice-Agent dollars")
    lines.append("")
    lines.append(f"- Agent $ spent this period (actual): **${p['agent_usd_consumed']:.2f}** "
                 f"({_fmt_int(p['agent_minutes'])} min over {p['agent_calls']} calls)")
    lines.append(f"- Prepaid agent pool ({p['plan'].title()}): ${p['agent_usd_allotment']:.2f}")
    agent_rem = p["agent_usd_remaining_approx"]
    agent_bit = (
        f"**~${agent_rem:.2f}** (approx)" if agent_rem >= 0 else f"**OVER by ~${-agent_rem:.2f}** (approx)"
    )
    lines.append(f"- Derived remaining: {agent_bit}")
    lines.append("")

    # Recommendation detail.
    lines.append("## Recommendation")
    lines.append("")
    lines.append(p["recommendation"]["detail"])
    lines.append("")

    # Caveat — always last, always present.
    lines.append("> ⚠️ These figures are DERIVED from consumption vs the configured plan "
                 "allotment — they are an **estimate** and will not match Cartesia's dashboard "
                 "exactly. Rollover, period-boundary alignment, and credit grants aren't exposed "
                 "by any API (we've observed the API-derived consumed figure running ~400k "
                 "credits above the dashboard). The **Cartesia dashboard is the source of truth** "
                 "for the exact remaining balance.")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

# MARK: - Alerting


def build_alert(payload: dict, warn_pct: float, crit_pct: float, agent_usd_warn_pct: float) -> dict:
    """Evaluate the quota payload against thresholds and return an alert verdict.

    Alerting is proactive: it fires on the PROJECTED period-end consumption (burn-rate
    extrapolation), not only on credits already spent, so a runaway burn trips the alert days
    before the allotment is actually exhausted. Both the current `pct_used` and the projected
    percentage are checked; the higher severity wins. The Voice-Agent-$ pool is a separate,
    independently-alerted budget (its own prepaid dollars, unrelated to model credits).

    Severity: CRITICAL when consumed OR projected reaches `crit_pct`% of the allotment (overage is
    happening or is on track to); WARN when either reaches `warn_pct`%, or the agent-$ pool is
    `agent_usd_warn_pct`% consumed; otherwise OK. Everything here is DERIVED (see the report caveat)
    — the alert flags a trend to check on the Cartesia dashboard, it is not a billing statement.

    Returns:
        dict: {status, reasons[], projected_overage_usd|None} — status in OK/WARN/CRITICAL.
    """
    allotment = payload["allotment"] or 0
    pct_used = payload["pct_used"]
    projected = payload["projected_period_end"]
    projected_pct = (projected / allotment * 100.0) if allotment else 0.0
    agent_alloc = payload["agent_usd_allotment"] or 0
    agent_used = payload["agent_usd_consumed"]
    agent_pct = (agent_used / agent_alloc * 100.0) if agent_alloc else 0.0

    reasons: list[str] = []
    status = "OK"

    def bump(level: str) -> None:
        nonlocal status
        order = {"OK": 0, "WARN": 1, "CRITICAL": 2}
        if order[level] > order[status]:
            status = level

    if pct_used >= crit_pct:
        bump("CRITICAL")
        reasons.append(f"credits already at {pct_used:.0f}% of allotment (>= {crit_pct:.0f}% crit)")
    elif pct_used >= warn_pct:
        bump("WARN")
        reasons.append(f"credits at {pct_used:.0f}% of allotment (>= {warn_pct:.0f}% warn)")

    if projected_pct >= crit_pct:
        bump("CRITICAL")
        reasons.append(
            f"projected period-end ~{projected_pct:.0f}% of allotment (>= {crit_pct:.0f}% crit)"
        )
    elif projected_pct >= warn_pct:
        bump("WARN")
        reasons.append(
            f"projected period-end ~{projected_pct:.0f}% of allotment (>= {warn_pct:.0f}% warn)"
        )

    if agent_pct >= agent_usd_warn_pct:
        bump("WARN")
        reasons.append(
            f"Voice-Agent-$ pool at {agent_pct:.0f}% consumed (>= {agent_usd_warn_pct:.0f}% warn)"
        )

    # Projected overage cost, when the plan's marginal rate is known.
    projected_overage_usd = None
    rate = payload.get("overage_rate_per_million")
    if rate and projected > allotment:
        projected_overage_usd = round((projected - allotment) / 1_000_000 * rate, 2)

    return {"status": status, "reasons": reasons, "projected_overage_usd": projected_overage_usd}


def render_alert(payload: dict, alert: dict) -> str:
    """Render the alert verdict as a compact block (appended below the quota report)."""
    icon = {"OK": "✅", "WARN": "⚠️", "CRITICAL": "🚨"}[alert["status"]]
    lines = [
        "",
        f"## Alert: {icon} {alert['status']} — Cartesia {payload['plan']} plan",
    ]
    if alert["reasons"]:
        for r in alert["reasons"]:
            lines.append(f"- {r}")
    else:
        lines.append("- all thresholds clear")
    if alert["status"] != "OK" and alert["projected_overage_usd"] is not None:
        lines.append(
            f"- projected overage this period: ~${alert['projected_overage_usd']:.2f} "
            f"(at ${payload['overage_rate_per_million']:.0f}/1M over the allotment)"
        )
    lines.append(f"- {payload['recommendation']['headline']}")
    lines.append("- DERIVED estimate — confirm exact remaining on the Cartesia dashboard.")
    return "\n".join(lines)


def post_to_slack(text: str, webhook: str) -> bool:
    """Post the alert text to a Slack incoming webhook. Returns True on 2xx."""
    resp = requests.post(webhook, json={"text": text}, timeout=15)
    return 200 <= resp.status_code < 300


# MARK: - Entry point


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        description="Estimate Cartesia credit quota from consumption vs the configured plan."
    )
    parser.add_argument("--plan", choices=sorted(PLANS.keys()), default=DEFAULT_PLAN,
                        help=f"Cartesia plan (default {DEFAULT_PLAN})")
    parser.add_argument("--allotment", type=int,
                        help="Override included monthly credits (defaults to the plan's allotment)")
    parser.add_argument("--renewal-day", type=int, default=DEFAULT_RENEWAL_DAY,
                        help=f"Day of month the plan renews, 1-28 (default {DEFAULT_RENEWAL_DAY}, from Tools/Cartesia)")
    parser.add_argument("--overage-rate-per-million", type=float,
                        help="True marginal $/1M credits while over allotment "
                             "(Cartesia doesn't publish it; left unknown unless provided)")
    parser.add_argument("--json", help="Write structured JSON to this path")
    parser.add_argument("--alert", action="store_true",
                        help="Alert mode: evaluate thresholds, print a verdict, and exit with a "
                             "severity code DISTINCT from the failure codes (10=WARN, 20=CRITICAL; "
                             "1=API failure and 2=config error stay reserved) so a wrapper can gate "
                             "on $? without confusing a verdict with a crash.")
    parser.add_argument("--warn-pct", type=float, default=80.0,
                        help="WARN when credits used OR projected period-end reaches this %% of the "
                             "allotment (default 80).")
    parser.add_argument("--crit-pct", type=float, default=100.0,
                        help="CRITICAL when credits used OR projected reaches this %% of the "
                             "allotment, i.e. overage (default 100).")
    parser.add_argument("--agent-usd-warn-pct", type=float, default=80.0,
                        help="WARN when the Voice-Agent-$ prepaid pool is this %% consumed "
                             "(default 80).")
    parser.add_argument("--slack", action="store_true",
                        help="Post the alert to Slack via SLACK_WEBHOOK_URL when status is not OK "
                             "(requires --alert; the webhook is read from env, never argv).")
    args = parser.parse_args()

    if not 1 <= args.renewal_day <= 28:
        print("ERROR: --renewal-day must be 1-28 (so every month has that day)", file=sys.stderr)
        return 2

    api_key = os.environ.get("CARTESIA_ADMIN_API_KEY")
    if not api_key:
        print("ERROR: CARTESIA_ADMIN_API_KEY not set", file=sys.stderr)
        return 2
    print("# auth: CARTESIA_ADMIN_API_KEY present", file=sys.stderr)

    plan = args.plan
    allotment = args.allotment if args.allotment is not None else PLANS[plan]["credits"]
    agent_usd_allotment = float(PLANS[plan]["agent_usd"])

    # Resolve the billing period.
    now = datetime.now(tz=timezone.utc)
    period_start_dt = billing_period_start(now, args.renewal_day)
    start_ts = rfc3339(period_start_dt)
    period_days_elapsed = max(1, (now - period_start_dt).days)
    # Period length = days from this renewal to the next occurrence of renewal_day.
    next_start_dt = billing_period_start(now + timedelta(days=32), args.renewal_day)
    # Guard: ensure next_start is strictly after period_start.
    while next_start_dt <= period_start_dt:
        next_start_dt = billing_period_start(next_start_dt + timedelta(days=32), args.renewal_day)
    period_days_total = (next_start_dt - period_start_dt).days
    period_days_remaining = max(0, period_days_total - period_days_elapsed)
    print(f"# query: plan={plan} allotment={allotment} renewal-day={args.renewal_day} "
          f"period={start_ts} ({period_days_elapsed}/{period_days_total}d)", file=sys.stderr)

    # Credits consumed this period.
    credit_rows = fetch_credits(start_ts, api_key)
    consumed = sum(int(row.get("credits", 0)) for row in credit_rows)

    # Daily rows for the 7-day burn.
    daily_rows = fetch_credits(start_ts, api_key, interval="day")
    print(f"# fetched: {len(daily_rows)} daily credit rows; consumed={consumed}", file=sys.stderr)

    # Voice-agent pool.
    agent_rows = fetch_agents(start_ts, api_key)
    agent_cents = sum(int(round(row.get("cents", 0))) for row in agent_rows)
    agent_minutes = sum(float(row.get("minutes", 0)) for row in agent_rows)
    agent_calls = sum(int(row.get("calls", 0)) for row in agent_rows)
    agent_usd_consumed = agent_cents / 100.0
    print(f"# fetched: {len(agent_rows)} agent rows; agent_usd={agent_usd_consumed:.2f}", file=sys.stderr)

    # Burn rates.
    burn_per_day = consumed / period_days_elapsed if period_days_elapsed else 0.0
    last7 = daily_rows[-7:] if daily_rows else []
    # Early in the period there are fewer than 7 daily buckets — track the real window size so
    # the render doesn't mislabel a shorter-window average as "last 7d".
    burn_recent_days = len(last7)
    if last7:
        burn_per_day_7d = sum(int(r.get("credits", 0)) for r in last7) / len(last7)
    else:
        burn_per_day_7d = burn_per_day

    projected_period_end = consumed + burn_per_day * period_days_remaining
    remaining_approx = allotment - consumed
    pct_used = (consumed / allotment * 100.0) if allotment else 0.0

    headline, detail = build_recommendation(plan, allotment, consumed, projected_period_end)

    payload = {
        "derived": True,
        "dashboard_is_source_of_truth": True,
        "plan": plan,
        "allotment": allotment,
        "consumed": consumed,
        "remaining_approx": remaining_approx,
        "pct_used": pct_used,
        "period_start": start_ts,
        "period_end": rfc3339(now),
        "period_days_elapsed": period_days_elapsed,
        "period_days_total": period_days_total,
        "period_days_remaining": period_days_remaining,
        "burn_per_day": burn_per_day,
        "burn_per_day_7d": burn_per_day_7d,
        "burn_recent_days": burn_recent_days,
        "projected_period_end": projected_period_end,
        # Default to the plan's known contract overage rate; --overage-rate-per-million overrides.
        "overage_rate_per_million": (
            args.overage_rate_per_million
            if args.overage_rate_per_million is not None
            else PLANS[plan].get("overage_per_m")
        ),
        "agent_usd_consumed": agent_usd_consumed,
        "agent_usd_allotment": agent_usd_allotment,
        "agent_usd_remaining_approx": agent_usd_allotment - agent_usd_consumed,
        "agent_minutes": agent_minutes,
        "agent_calls": agent_calls,
        "recommendation": {"headline": headline, "detail": detail},
    }

    print(render(payload))

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, default=str)
        print(f"\nWrote {args.json}", file=sys.stderr)

    if args.alert:
        alert = build_alert(payload, args.warn_pct, args.crit_pct, args.agent_usd_warn_pct)
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
        # Exit encodes alert severity with codes DISTINCT from the failure codes so a wrapper
        # gating on $? can't confuse a quota verdict with a crash: 1 = operational failure (API
        # error via _get's sys.exit) and 2 = config error (missing key / bad --renewal-day) are
        # reserved, so alerts use 10 (WARN) / 20 (CRITICAL). 0 stays OK.
        return {"OK": 0, "WARN": 10, "CRITICAL": 20}[alert["status"]]

    return 0


if __name__ == "__main__":
    sys.exit(main())
