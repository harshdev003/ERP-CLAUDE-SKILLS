"""Report ElevenLabs credit quota + burn for the Aura workspace.

Per-vendor quota-watch skill, sibling of report-cartesia-quota. Unlike Cartesia (no balance API,
so quota is DERIVED), ElevenLabs exposes an ACTUAL quota via `/v1/user/subscription`:
`character_count` / `character_limit` (workspace credits used vs the grant/plan limit),
`next_character_count_reset_unix` (when the counter resets), and `current_overage.amount`. This skill
surfaces the quota/burn/overage view on its own and, with `--alert`, an OK/WARN/CRITICAL verdict.

ElevenLabs bills on-demand overage by raising `character_limit` in blocks as usage reaches it, so
`used` never passes the live limit and `current_overage.amount` stays "0". Overage is therefore
measured against the plan's base credit grant: `--base-credits`, or the plan price
(`next_invoice.subtotal_cents`) divided by the period's spend per credit from the Usage API.
`current_overage` is kept as a cross-check only.

Going past the plan limit is normal: Aura pays usage-based on-demand overage every month and voice
keeps working past the limit. So the limit is not a run-out date. The report shows the days until
on-demand overage starts and a projected overage in dollars, and `--alert` warns per `--overage-step-usd`
step of live overage ($500 by default) instead of on the limit itself.

Exit codes mirror report-cartesia-quota so a wrapper can gate on `$?`: 0=OK; with --alert 10=WARN /
20=CRITICAL for the verdict; 1=API failure and 2=config error stay reserved for actual failures.

Auth: `ELEVENLABS_ADMIN_API_KEY` (preferred) or `ELEVENLABS_API_KEY` (fallback), read from env only,
sent as the `xi-api-key` header — never printed or passed on argv.
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


ELEVENLABS_API = "https://api.elevenlabs.io/v1"
SUBSCRIPTION_PATH = "/user/subscription"
CHARACTER_STATS_PATH = "/usage/character-stats"

# Alert exit codes, DISTINCT from the failure codes (1=API failure, 2=config) so a wrapper gating on
# $? never confuses a verdict with a crash.
EXIT = {"OK": 0, "WARN": 10, "CRITICAL": 20}


# ---------------------------------------------------------------------------
# API client
# ---------------------------------------------------------------------------

def fetch_subscription(api_key: str) -> dict:
    """GET /v1/user/subscription — the workspace's live quota + renewal + overage."""
    url = f"{ELEVENLABS_API}{SUBSCRIPTION_PATH}"
    r = requests.get(url, headers={"xi-api-key": api_key}, timeout=60)
    if not r.ok:
        sys.exit(f"ElevenLabs {r.status_code} on {SUBSCRIPTION_PATH}: {r.text[:300]}")
    return r.json()


def _usage_total(api_key: str, start: datetime, end: datetime, metric: str) -> float:
    params = {
        "start_unix": int(start.timestamp() * 1000),
        "end_unix": int(end.timestamp() * 1000),
        "breakdown_type": "none",
        "metric": metric,
        "aggregation_interval": "day",
        "include_workspace_metrics": "true",
    }
    r = requests.get(f"{ELEVENLABS_API}{CHARACTER_STATS_PATH}", headers={"xi-api-key": api_key},
                     params=params, timeout=60)
    if not r.ok:
        raise RuntimeError(f"ElevenLabs {r.status_code} on {CHARACTER_STATS_PATH} ({metric})")
    return sum(float(v) for series in (r.json().get("usage") or {}).values()
               if isinstance(series, list) for v in series if v is not None)


def fetch_period_usage(api_key: str, sub: dict, periods_back: int = 1,
                       now: datetime | None = None) -> dict | None:
    """Credits and spend ($) of one refresh period, from the Usage API.

    `periods_back=0` is the current period up to `now`, 1 the period before it. `fiat_units_spent`
    comes back in cents. Returns None when the period can't be placed or a read fails: the rate it
    feeds is derived, so a failure leaves the overage blank, never the report.
    """
    reset_unix = sub.get("next_character_count_reset_unix")
    if not reset_unix:
        return None
    months = 12 if period_days_for(sub.get("character_refresh_period") or sub.get("billing_period")) == 365 else 1
    end = months_before(datetime.fromtimestamp(reset_unix, tz=timezone.utc), months * periods_back)
    start = months_before(end, months)
    if now is not None:
        end = min(end, now)
    try:
        credits = _usage_total(api_key, start, end, "credits")
        usd = _usage_total(api_key, start, end, "fiat_units_spent") / 100.0
    except (requests.RequestException, RuntimeError, ValueError, TypeError) as e:
        print(f"# previous-period usage read failed: {e}", file=sys.stderr)
        return None
    return {"start": start.strftime("%Y-%m-%d"), "end": end.strftime("%Y-%m-%d"),
            "credits": credits, "usd": usd}


def fetch_previous_period(api_key: str, sub: dict) -> dict | None:
    return fetch_period_usage(api_key, sub, 1)


# ---------------------------------------------------------------------------
# Quota math
# ---------------------------------------------------------------------------

def period_days_for(refresh_period: str | None) -> int:
    """Length of the credit-refresh period in days, inferred from the API's period label.

    ElevenLabs reports `character_refresh_period` / `billing_period` as e.g. `annual_period` or
    `monthly_period`; the exact reset instant comes from `next_character_count_reset_unix`, so this
    only sizes the elapsed/remaining split for the burn rate. Defaults to 30 (monthly) when unknown.
    """
    label = (refresh_period or "").lower()
    if "annual" in label or "year" in label:
        return 365
    return 30


def months_before(dt: datetime, months: int) -> datetime:
    """`dt` moved back by whole calendar months, clamping the day (Mar 31 -> Feb 28/29)."""
    y, m = divmod(dt.year * 12 + dt.month - 1 - months, 12)
    m += 1
    nxt = datetime(y + (m == 12), m % 12 + 1, 1, tzinfo=dt.tzinfo)
    last = (nxt - timedelta(days=1)).day
    return dt.replace(year=y, month=m, day=min(dt.day, last))


def _fmt_int(n: float) -> str:
    return f"{int(round(n)):,}"


def _overage_amount(sub: dict) -> float:
    """`current_overage.amount` as dollars; the API sends it as a string ("0", "123.45")."""
    try:
        return float((sub.get("current_overage") or {}).get("amount", 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def _plan_usd(sub: dict) -> float | None:
    """The base plan price for the period: `next_invoice.subtotal_cents`, which excludes the top-ups."""
    cents = (sub.get("next_invoice") or {}).get("subtotal_cents")
    try:
        return float(cents) / 100.0 if cents else None
    except (TypeError, ValueError):
        return None


def build_payload(sub: dict, now: datetime, previous: dict | None = None,
                  overage_step_usd: float = 500.0, current: dict | None = None,
                  base_credits: float | None = None) -> dict:
    """Derive the quota/burn/overage view from a `/v1/user/subscription` response.

    `current` / `previous` are `fetch_period_usage` results for this period and the one before; they
    supply the $/credit rate (this period's first). Overage is credits past the base grant, because
    the live `character_limit` is raised in blocks as usage reaches it and never shows overage.
    """
    used = int(sub.get("character_count", 0))
    limit = int(sub.get("character_limit", 0))

    rate, rate_source = None, None
    for period, source in ((current, "current_period_spend"), (previous, "previous_period_spend")):
        if period and period.get("credits", 0) > 0 and period.get("usd", 0) > 0:
            rate, rate_source = period["usd"] / period["credits"], source
            break

    plan_usd = _plan_usd(sub)
    if base_credits:
        base, base_source = int(base_credits), "--base-credits"
    elif plan_usd and rate:
        base, base_source = int(round(plan_usd / rate, -3)), "plan_price_at_rate"
    else:
        base, base_source = limit, "live_limit"  # no rate: the old, understating measure

    remaining = base - used
    pct_used = (used / base * 100.0) if base else 0.0

    reset_unix = sub.get("next_character_count_reset_unix")
    reset_dt = datetime.fromtimestamp(reset_unix, tz=timezone.utc) if reset_unix else None
    days_to_reset = max(0, (reset_dt - now).days) if reset_dt else None

    # Elapsed since the last reset = reset − one period; burn = used / elapsed.
    period_days = period_days_for(sub.get("character_refresh_period") or sub.get("billing_period"))
    if reset_dt:
        last_reset_ts = reset_dt.timestamp() - period_days * 86400
        days_elapsed = max(1.0, (now.timestamp() - last_reset_ts) / 86400)
    else:
        days_elapsed = float(period_days)
    burn_per_day = used / days_elapsed

    # Past the limit, usage continues as paid on-demand overage; this is when that starts.
    if remaining <= 0:
        days_until_overage = 0.0
    else:
        days_until_overage = (remaining / burn_per_day) if burn_per_day > 0 else None
    projected_at_reset = used + burn_per_day * days_to_reset if days_to_reset is not None else None

    overage = sub.get("current_overage") or {}
    api_overage_usd = _overage_amount(sub)
    overage_credits = max(0, used - base)
    overage_usd = overage_credits * rate if rate is not None else api_overage_usd
    projected_overage_credits = (max(0.0, projected_at_reset - base)
                                 if projected_at_reset is not None else None)
    projected_overage_usd = (projected_overage_credits * rate
                             if rate is not None and projected_overage_credits is not None else None)
    overage_step = int(overage_usd // overage_step_usd) if overage_step_usd > 0 else 0

    return {
        "source": "actual",  # from the ElevenLabs API (not derived like Cartesia)
        "tier": sub.get("tier"),
        "status": sub.get("status"),
        "used": used,
        "limit": limit,
        "base_credits": base,
        "base_source": base_source,
        "plan_usd": plan_usd,
        "extension_credits": max(0, limit - base),
        "remaining": remaining,
        "pct_used": pct_used,
        "reset": reset_dt.strftime("%Y-%m-%dT%H:%M:%SZ") if reset_dt else None,
        "days_to_reset": days_to_reset,
        "period_days": period_days,
        "days_elapsed": round(days_elapsed, 1),
        "burn_per_day": burn_per_day,
        "days_until_overage": days_until_overage,
        "projected_at_reset": projected_at_reset,
        "overage_usd": overage_usd,
        "api_overage_usd": api_overage_usd,
        "overage_currency": (overage.get("currency") or "usd"),
        "overage_credits": overage_credits,
        "overage_step_usd": overage_step_usd,
        "overage_step": overage_step,
        "overage_rate_usd_per_credit": rate,
        "overage_rate_source": rate_source,
        "current_period": current,
        "previous_period": previous,
        "projected_overage_credits": projected_overage_credits,
        "projected_overage_usd": projected_overage_usd,
    }


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _until_overage_text(p: dict) -> str:
    d = p["days_until_overage"]
    if d is None:
        return "days until on-demand overage starts n/a"
    if d <= 0:
        return "on-demand overage has started (normal on this plan)"
    if d < 1:
        return "under a day until on-demand overage starts"
    return f"~{d:.0f} days until on-demand overage starts"


def render(p: dict) -> str:
    lines: list[str] = ["# ElevenLabs Credit Quota", ""]
    lines.append(
        f"**{p['tier']}** ({p['status']}) · used **{_fmt_int(p['used'])} / {_fmt_int(p['base_credits'])}** "
        f"({p['pct_used']:.0f}%) · {_until_overage_text(p)}"
    )
    lines.append("")

    lines.append("## Credits")
    lines.append("")
    lines.append(f"- Used this period (actual): **{_fmt_int(p['used'])}** credits")
    base_basis = {"--base-credits": "from --base-credits",
                  "plan_price_at_rate": f"derived: plan price ${(p['plan_usd'] or 0):,.0f} at the period's $/credit",
                  "live_limit": "no rate, so the live limit; overage understated"}[p["base_source"]]
    lines.append(f"- Plan base grant: {_fmt_int(p['base_credits'])} credits ({base_basis}); "
                 "usage past it is billed as on-demand overage")
    lines.append(f"- Live limit: {_fmt_int(p['limit'])} credits "
                 f"({_fmt_int(p['extension_credits'])} added by on-demand extensions this period)")
    lines.append(f"- Remaining before overage: **{_fmt_int(max(0, p['remaining']))}**")
    lines.append(f"- % of base grant: **{p['pct_used']:.1f}%**")
    if p["reset"]:
        lines.append(f"- Resets: `{p['reset']}` (in {p['days_to_reset']} days)")
    lines.append(
        f"- Burn rate: **{_fmt_int(p['burn_per_day'])}/day** "
        f"(over ~{p['days_elapsed']:.0f} days elapsed this {p['period_days']}-day period)"
    )
    lines.append(f"- **{_until_overage_text(p).capitalize()}**")
    if p["projected_at_reset"] is not None:
        lines.append(f"- Projected at reset (at current burn): ~{_fmt_int(p['projected_at_reset'])} credits")
    lines.append("")

    lines.append("## Overage")
    lines.append("")
    if p["overage_usd"] > 0:
        lines.append(f"- **Overage so far: ${p['overage_usd']:,.2f} {p['overage_currency'].upper()}** "
                     f"(derived) on {_fmt_int(p['overage_credits'])} credits past the base grant "
                     f"(step {p['overage_step']} of ${p['overage_step_usd']:,.0f})")
    else:
        lines.append("- No overage yet this period.")
    lines.append(f"- API `current_overage` (cross-check): ${p['api_overage_usd']:,.2f}; it stays $0 "
                 "because extensions are billed as top-ups")
    if p["projected_overage_usd"] is not None:
        period = p["current_period"] if p["overage_rate_source"] == "current_period_spend" else p["previous_period"]
        which = "this" if p["overage_rate_source"] == "current_period_spend" else "the previous"
        basis = f"{which} period's spend per credit, {(period or {}).get('start')} to {(period or {}).get('end')}"
        lines.append(
            f"- **Projected overage at reset: ~${p['projected_overage_usd']:,.0f}** (derived): "
            f"~{_fmt_int(p['projected_overage_credits'])} credits past the base grant at "
            f"${p['overage_rate_usd_per_credit'] * 1000:.4f}/1k credits, from {basis}"
        )
    else:
        lines.append("- Projected overage $: n/a (no rate: the Usage API read failed)")
    lines.append("")
    lines.append("> Used, live limit and reset are ACTUAL from the ElevenLabs `/v1/user/subscription` API, "
                 "and spend per credit from its Usage API. The base grant and overage $ are derived from them; "
                 "burn rate, days until overage and the projected overage are extrapolations at the current "
                 "pace; the migration off ElevenLabs ConvAI lowers burn.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Alerting
# ---------------------------------------------------------------------------

def build_alert(p: dict, warn_pct: float | None = None, crit_pct: float | None = None,
                warn_days: float | None = None, crit_days: float | None = None) -> dict:
    """Evaluate the quota → OK/WARN/CRITICAL verdict.

    Overage is normal on this plan, so by default the only signal is the live overage reaching a new
    `overage_step_usd` step (WARN; `overage_step` in the payload lets a caller alert once per step).
    The %-of-limit and days-until-overage thresholds are opt-in. The highest severity wins.
    """
    status = "OK"
    reasons: list[str] = []

    def bump(level: str) -> None:
        nonlocal status
        order = {"OK": 0, "WARN": 1, "CRITICAL": 2}
        if order[level] > order[status]:
            status = level

    if p["overage_step"] >= 1:
        bump("WARN")
        reasons.append(f"overage ${p['overage_usd']:,.2f} reached step {p['overage_step']} "
                       f"(${p['overage_step'] * p['overage_step_usd']:,.0f})")

    pct = p["pct_used"]
    if crit_pct is not None and pct >= crit_pct:
        bump("CRITICAL")
        reasons.append(f"credits at {pct:.0f}% of base grant (>= {crit_pct:.0f}% crit)")
    elif warn_pct is not None and pct >= warn_pct:
        bump("WARN")
        reasons.append(f"credits at {pct:.0f}% of base grant (>= {warn_pct:.0f}% warn)")

    days = p["days_until_overage"]
    if days is not None:
        if crit_days is not None and days <= crit_days:
            bump("CRITICAL")
            reasons.append(f"~{days:.0f} days until on-demand overage (<= {crit_days:.0f}d crit)")
        elif warn_days is not None and days <= warn_days:
            bump("WARN")
            reasons.append(f"~{days:.0f} days until on-demand overage (<= {warn_days:.0f}d warn)")

    if p["status"] and p["status"] != "active":
        bump("WARN")
        reasons.append(f"subscription status is '{p['status']}' (not active)")

    return {"status": status, "reasons": reasons}


def render_alert(p: dict, alert: dict) -> str:
    icon = {"OK": "✅", "WARN": "⚠️", "CRITICAL": "🚨"}[alert["status"]]
    lines = ["", f"## Alert: {icon} {alert['status']} — ElevenLabs {p['tier']}"]
    if alert["reasons"]:
        for r in alert["reasons"]:
            lines.append(f"- {r}")
    else:
        lines.append("- all thresholds clear")
    lines.append("- Overage $ is derived (credits past the base grant at the period's $/credit); burn, "
                 "days until overage and projected overage are current-pace extrapolations.")
    return "\n".join(lines)


def post_to_slack(text: str, webhook: str) -> bool:
    resp = requests.post(webhook, json={"text": text}, timeout=15)
    return 200 <= resp.status_code < 300


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        description="Report ElevenLabs credit quota, burn rate and on-demand overage from /user/subscription."
    )
    parser.add_argument("--json", help="Write the structured payload to this path")
    parser.add_argument("--alert", action="store_true",
                        help="Alert mode: print an OK/WARN/CRITICAL verdict and exit by severity "
                             "(10=WARN, 20=CRITICAL; 1=API failure and 2=config stay reserved).")
    parser.add_argument("--overage-step-usd", type=float, default=500.0,
                        help="WARN each time live overage reaches another step of this many $ "
                             "(default 500). Overage itself is normal on this plan.")
    parser.add_argument("--base-credits", type=float, default=None,
                        help="The plan's base credit grant per period. Default: derived from the plan "
                             "price (next_invoice) at the period's spend per credit.")
    parser.add_argument("--warn-pct", type=float, default=None,
                        help="Opt-in: WARN when credits used reach this %% of the base grant.")
    parser.add_argument("--crit-pct", type=float, default=None,
                        help="Opt-in: CRITICAL at this %% of the base grant.")
    parser.add_argument("--warn-runway-days", type=float, default=None,
                        help="Opt-in: WARN when days until on-demand overage starts is <= this.")
    parser.add_argument("--crit-runway-days", type=float, default=None,
                        help="Opt-in: CRITICAL when days until on-demand overage starts is <= this.")
    parser.add_argument("--slack", action="store_true",
                        help="Post the alert to SLACK_WEBHOOK_URL when status != OK (requires "
                             "--alert; the webhook is read from env, never argv).")
    args = parser.parse_args()

    api_key = os.environ.get("ELEVENLABS_ADMIN_API_KEY") or os.environ.get("ELEVENLABS_API_KEY")
    if not api_key:
        print("ERROR: set ELEVENLABS_ADMIN_API_KEY (preferred) or ELEVENLABS_API_KEY", file=sys.stderr)
        return 2
    which = "ELEVENLABS_ADMIN_API_KEY" if os.environ.get("ELEVENLABS_ADMIN_API_KEY") else "ELEVENLABS_API_KEY"
    print(f"# auth: {which} present", file=sys.stderr)

    now = datetime.now(tz=timezone.utc)
    sub = fetch_subscription(api_key)
    current = fetch_period_usage(api_key, sub, 0, now)
    previous = None
    if not current or current.get("credits", 0) <= 0 or current.get("usd", 0) <= 0:
        previous = fetch_previous_period(api_key, sub)
    payload = build_payload(sub, now, previous, args.overage_step_usd, current, args.base_credits)
    print(f"# fetched: tier={payload['tier']} used={payload['used']} limit={payload['limit']} "
          f"base={payload['base_credits']} ({payload['base_source']})", file=sys.stderr)

    print(render(payload))

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, default=str)
        print(f"\nWrote {args.json}", file=sys.stderr)

    if args.alert:
        alert = build_alert(payload, args.warn_pct, args.crit_pct,
                            args.warn_runway_days, args.crit_runway_days)
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
