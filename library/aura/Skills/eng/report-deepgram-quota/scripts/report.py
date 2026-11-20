"""Report Deepgram prepaid-balance runway for the Aura STT project.

Per-vendor quota-watch skill, sibling of report-cartesia-quota. Deepgram is prepaid pay-as-you-go —
there is NO monthly allotment, so the signal is not "% of quota" but RUNWAY: how many days of prepaid
balance remain at the current burn. This skill reads the ACTUAL prepaid balance
(`/v1/projects/{id}/balances`), the hours consumed over a recent window (`/v1/projects/{id}/usage`),
derives the spend (hours × rate) and burn/day, and reports days-until-empty with a "top up soon"
alarm. With `--alert` it prints an OK/WARN/CRITICAL verdict.

Balance is ACTUAL (from the API). Spend/burn are DERIVED: Deepgram's usage endpoint returns hours,
not dollars for this project/plan, so USD = hours × --rate-per-hour (default $0.348/hr, Nova-3
Multilingual streaming PAYG — our current model). Override on a plan/model change.

Exit codes mirror the quota-skill family so a wrapper can gate on `$?`: 0=OK; with --alert 10=WARN /
20=CRITICAL for the verdict; 1=API failure and 2=config/scope error stay reserved for failures.

Auth: DEEPGRAM_OWNER_API_KEY (preferred, management key) or DEEPGRAM_API_KEY (fallback), read from
env only, sent as `Authorization: Token …`. The key must be a member of the STT project (a usage:read
key on that project) — see analyze-deepgram-costs / PROD-3179 for the project-scoping caveat.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

try:
    import requests
except ImportError:
    sys.exit("Missing dependency: pip3 install requests")


DEEPGRAM_API = "https://api.deepgram.com"
PROJECTS_PATH = "/v1/projects"
# Nova-3 Multilingual streaming, on-demand PAYG, USD/hour ($0.0058/min × 60). Override on a
# plan/model change (e.g. --rate-per-hour 0.288 to model monolingual). Kept in sync with
# analyze-deepgram-costs.
DEFAULT_RATE_PER_HOUR = 0.348
EXIT = {"OK": 0, "WARN": 10, "CRITICAL": 20}


class DeepgramScopeError(Exception):
    """The key can't see the requested project (NOT_FOUND / INSUFFICIENT_PERMISSIONS)."""


def _request(path: str, api_key: str, params: dict | None = None) -> dict:
    """GET with retry on 429; DeepgramScopeError on project-scope failures (403/404)."""
    url = f"{DEEPGRAM_API}{path}"
    headers = {"Authorization": f"Token {api_key}"}
    for attempt in range(5):
        print(f"# query {path}", file=sys.stderr)
        r = requests.get(url, headers=headers, params=params or {}, timeout=60)
        if r.status_code == 429:
            wait = float(r.headers.get("Retry-After", 2 ** attempt))
            time.sleep(min(wait, 30))
            continue
        if r.status_code in (403, 404):
            category = ""
            try:
                category = (r.json() or {}).get("category", "")
            except ValueError:
                pass
            raise DeepgramScopeError(f"{r.status_code} {category or r.reason} on {path}")
        if not r.ok:
            sys.exit(f"Deepgram {r.status_code} on {path}: {r.text[:300]}")
        try:
            return r.json()
        except ValueError:
            sys.exit(f"Deepgram {path}: non-JSON response: {r.text[:300]}")
    sys.exit(f"Deepgram {path}: rate-limited after 5 retries")


def resolve_project(api_key: str, project_id: str | None) -> dict:
    """Resolve which project to report on (single visible, or --project-id)."""
    if project_id:
        return {"project_id": project_id, "name": f"(project {project_id})"}
    print("# auth resolving projects", file=sys.stderr)
    projects = (_request(PROJECTS_PATH, api_key).get("projects") or [])
    if not projects:
        sys.exit("This key is not a member of any Deepgram project. "
                 "Check DEEPGRAM_OWNER_API_KEY / DEEPGRAM_API_KEY, or pass --project-id.")
    if len(projects) == 1:
        return projects[0]
    names = ", ".join(f"{p.get('name')!r} ({p.get('project_id')})" for p in projects)
    sys.exit(f"Multiple projects visible — pass --project-id.\nAvailable: {names}")


def fetch_balance_usd(api_key: str, pid: str) -> float:
    """Sum the prepaid USD balances for the project (actual)."""
    balances = _request(f"{PROJECTS_PATH}/{pid}/balances", api_key).get("balances") or []
    return sum(float(b.get("amount", 0) or 0) for b in balances if (b.get("units") or "usd") == "usd")


def fetch_hours(api_key: str, pid: str, start: str, end: str) -> float:
    """Total audio hours in [start, end] (Deepgram treats `end` as inclusive)."""
    usage = _request(f"{PROJECTS_PATH}/{pid}/usage", api_key, {"start": start, "end": end})
    total = 0.0
    for row in usage.get("results") or []:
        for k in ("total_hours", "hours"):
            if k in row:
                try:
                    total += float(row.get(k) or 0)
                except (TypeError, ValueError):
                    pass
                break
    return total


def _fmt_money(v: float) -> str:
    return f"${v:,.2f}"


# Above this, runway is dominated by rounding noise on a near-zero burn — show a plain-language cap
# instead of an absurd "~994,927 days".
_RUNWAY_CAP_DAYS = 3650  # 10 years


def _runway_str(runway: float | None) -> str:
    if runway is None:
        return "runway n/a (no burn in window)"
    if runway > _RUNWAY_CAP_DAYS:
        return "runway > 10 years (negligible burn — balance effectively not drawing down)"
    return f"~{runway:.0f} days of runway"


def build_payload(balance_usd: float, hours: float, window_days: int, rate: float) -> dict:
    spend = hours * rate
    burn_per_day = spend / window_days if window_days else 0.0
    runway_days = (balance_usd / burn_per_day) if burn_per_day > 0 else None
    return {
        "balance_usd": balance_usd,       # ACTUAL
        "window_days": window_days,
        "hours": hours,                   # ACTUAL
        "rate_per_hour": rate,
        "spend_usd": spend,               # DERIVED (hours × rate)
        "burn_per_day": burn_per_day,     # DERIVED
        "runway_days": runway_days,       # DERIVED
    }


def render(p: dict) -> str:
    runway = p["runway_days"]
    lines = ["# Deepgram Prepaid Balance — runway", ""]
    lines.append(f"**Prepaid balance {_fmt_money(p['balance_usd'])}** (actual) · {_runway_str(runway)} "
                 f"at ~{_fmt_money(p['burn_per_day'])}/day")
    lines.append("")
    lines.append("## Balance & burn")
    lines.append("")
    lines.append(f"- Prepaid balance: **{_fmt_money(p['balance_usd'])}** (actual, from /balances)")
    lines.append(f"- Window: last {p['window_days']} days")
    lines.append(f"- Audio hours (actual): **{p['hours']:.2f}** hrs")
    lines.append(f"- Derived spend: **{_fmt_money(p['spend_usd'])}** "
                 f"({p['hours']:.2f} hrs × {_fmt_money(p['rate_per_hour'])}/hr)")
    lines.append(f"- Burn rate: **{_fmt_money(p['burn_per_day'])}/day** (derived)")
    if runway is None:
        lines.append("- **Runway: n/a** — no usage in the window (burn ~$0); balance not drawing down")
    elif runway > _RUNWAY_CAP_DAYS:
        lines.append("- **Runway: > 10 years** — negligible burn this window; balance not meaningfully "
                     "drawing down")
    else:
        lines.append(f"- **Runway: ~{runway:.0f} days** until the prepaid balance is exhausted")
    lines.append("")
    lines.append("> Balance is ACTUAL from the Deepgram management API. Spend + burn + runway are "
                 "DERIVED (hours × rate; Deepgram's usage endpoint returns hours, not $). Deepgram is "
                 "prepaid PAYG — there is no monthly quota, so runway (days-to-empty) is the signal. "
                 "The Deepgram console is the source of truth for the exact balance.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Alerting
# ---------------------------------------------------------------------------

def build_alert(p: dict, warn_runway_days: float, crit_runway_days: float, min_balance: float) -> dict:
    """OK/WARN/CRITICAL on runway (days-to-empty) and an absolute balance floor."""
    status = "OK"
    reasons: list[str] = []

    def bump(level: str) -> None:
        nonlocal status
        order = {"OK": 0, "WARN": 1, "CRITICAL": 2}
        if order[level] > order[status]:
            status = level

    runway = p["runway_days"]
    if runway is not None:
        if runway <= crit_runway_days:
            bump("CRITICAL")
            reasons.append(f"~{runway:.0f} days of runway (<= {crit_runway_days:.0f}d crit)")
        elif runway <= warn_runway_days:
            bump("WARN")
            reasons.append(f"~{runway:.0f} days of runway (<= {warn_runway_days:.0f}d warn)")

    if p["balance_usd"] <= min_balance:
        bump("CRITICAL")
        reasons.append(f"balance {_fmt_money(p['balance_usd'])} <= floor {_fmt_money(min_balance)}")

    return {"status": status, "reasons": reasons}


def render_alert(p: dict, alert: dict) -> str:
    icon = {"OK": "✅", "WARN": "⚠️", "CRITICAL": "🚨"}[alert["status"]]
    lines = ["", f"## Alert: {icon} {alert['status']} — Deepgram prepaid balance"]
    for r in (alert["reasons"] or ["all thresholds clear"]):
        lines.append(f"- {r}")
    lines.append("- balance ACTUAL; runway DERIVED (hours × rate). Prepaid PAYG — no monthly quota.")
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
        description="Report Deepgram prepaid-balance runway (balance ÷ burn) for the Aura STT project."
    )
    parser.add_argument("--project-id", help="Deepgram project id (default: the key's sole project)")
    parser.add_argument("--days", type=int, default=30,
                        help="Window (days) over which to measure burn (default 30)")
    parser.add_argument("--rate-per-hour", type=float, default=DEFAULT_RATE_PER_HOUR,
                        help=f"USD/hr for derived spend (default {DEFAULT_RATE_PER_HOUR} multilingual; "
                             f"use 0.288 to model monolingual)")
    parser.add_argument("--json", help="Write the structured payload to this path")
    parser.add_argument("--alert", action="store_true",
                        help="Alert mode: print an OK/WARN/CRITICAL verdict and exit by severity "
                             "(10=WARN, 20=CRITICAL; 1=API failure and 2=config/scope stay reserved).")
    parser.add_argument("--warn-runway-days", type=float, default=30.0,
                        help="WARN when days-to-empty at current burn is <= this (default 30).")
    parser.add_argument("--crit-runway-days", type=float, default=14.0,
                        help="CRITICAL when days-to-empty at current burn is <= this (default 14).")
    parser.add_argument("--min-balance", type=float, default=0.0,
                        help="CRITICAL when the prepaid balance is <= this $ floor (default 0).")
    parser.add_argument("--slack", action="store_true",
                        help="Post the alert to SLACK_WEBHOOK_URL when status != OK (requires --alert).")
    args = parser.parse_args()

    if args.days < 1:
        print("ERROR: --days must be >= 1", file=sys.stderr)
        return 2

    api_key = os.environ.get("DEEPGRAM_OWNER_API_KEY") or os.environ.get("DEEPGRAM_API_KEY")
    if not api_key:
        print("ERROR: set DEEPGRAM_OWNER_API_KEY (preferred) or DEEPGRAM_API_KEY", file=sys.stderr)
        return 2
    which = "DEEPGRAM_OWNER_API_KEY" if os.environ.get("DEEPGRAM_OWNER_API_KEY") else "DEEPGRAM_API_KEY"
    print(f"# auth: {which} present", file=sys.stderr)

    now = datetime.now(tz=timezone.utc)
    start = (now - timedelta(days=args.days)).strftime("%Y-%m-%d")
    end = now.strftime("%Y-%m-%d")

    try:
        project = resolve_project(api_key, args.project_id)
        pid = project.get("project_id")
        balance_usd = fetch_balance_usd(api_key, pid)
        hours = fetch_hours(api_key, pid, start, end)
    except DeepgramScopeError as exc:
        print(f"ERROR: key can't access this project ({exc}). A usage:read key on the STT project is "
              f"required — see analyze-deepgram-costs / PROD-3179.", file=sys.stderr)
        return 2

    print(f"# fetched: project={pid} balance=${balance_usd:.2f} hours={hours:.2f} "
          f"({start}..{end})", file=sys.stderr)

    payload = build_payload(balance_usd, hours, args.days, args.rate_per_hour)
    payload["project_id"] = pid
    print(render(payload))

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, default=str)
        print(f"\nWrote {args.json}", file=sys.stderr)

    if args.alert:
        alert = build_alert(payload, args.warn_runway_days, args.crit_runway_days, args.min_balance)
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
