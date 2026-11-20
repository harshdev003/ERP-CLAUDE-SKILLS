#!/usr/bin/env python3
"""
Deepgram STT spend report for the Aura project(s).

Deepgram is our speech-to-text provider (nova-3) on the LiveKit voice path.
This report pulls usage/spend from the Deepgram management API and renders a
markdown report of **actuals only** — no end-of-period projection:

  - **Prepaid balance** (actual USD) from `/v1/projects/{id}/balances`.
  - **Actual USD spend** from `/v1/projects/{id}/billing/breakdown` when that
    endpoint returns per-period dollar figures (preferred). Otherwise:
  - **Actual hours + requests** from `/v1/projects/{id}/usage` (and per-model /
    per-feature detail from `/v1/projects/{id}/usage/breakdown`), with USD
    DERIVED as hours × --rate-per-hour and clearly labelled as an estimate.

The report leads with the actuals (hours, requests, balance) and treats the
derived USD as secondary. See references/methodology.md for the per-hour rate
default, its source + date, and the actual-vs-derived distinction.

**Project scoping caveat.** The management key can only see projects it is a
member of. `GET /v1/projects` lists those; `--project-id` overrides. Hitting a
project the key isn't a member of returns `{"category":"NOT_FOUND"}` or
`{"category":"INSUFFICIENT_PERMISSIONS"}` — the script surfaces a clear message
(a usage:read key on that project is needed) instead of crashing. Production STT
usage only shows up when the deployed agent's `DEEPGRAM_API_KEY` and this
`DEEPGRAM_OWNER_API_KEY` share the same project (both the Aura STT project) AND
voice traffic has flowed; otherwise a run may show zero usage but a real
prepaid balance.

Usage:
    python report.py                                  # last 7 days (default)
    python report.py --window last-30d
    python report.py --window yesterday
    python report.py --window today
    python report.py --since 2026-06-01 --until 2026-07-01
    python report.py --project-id <uuid>
    python report.py --rate-per-hour 0.288   # model monolingual (default is 0.348 multilingual)
    python report.py --window last-30d --json out.json

Env:
    DEEPGRAM_OWNER_API_KEY   Preferred. Deepgram management API key.
    DEEPGRAM_API_KEY         Fallback if the OWNER key is unset.

The key is read from the environment only — never printed, never passed in
argv, never written to a file.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

DEEPGRAM_API = "https://api.deepgram.com"
PROJECTS_PATH = "/v1/projects"

# Nova-3 MULTILINGUAL streaming, on-demand PAYG rate, in USD/hour — our current
# model + plan. Aura runs Nova-3 Multilingual in real-time (streaming) on the
# LiveKit voice path: $0.0058/min × 60 = $0.348/hour (deepgram.com/pricing,
# 2026-07-02). Monolingual would be $0.0048/min ($0.288/hr, ~17% less) and Growth
# multilingual $0.0050/min — see references/methodology.md. Used only to DERIVE
# USD when the billing endpoint doesn't expose actual dollars; labelled derived.
# Override with --rate-per-hour on a plan/model change.
DEFAULT_RATE_PER_HOUR = 0.348

WINDOW_CHOICES = ["yesterday", "today", "last-7d", "last-30d"]


# ---------------------------------------------------------------------------
# Time-window resolution
# ---------------------------------------------------------------------------


def resolve_window(
    window: str | None,
    since: str | None,
    until: str | None,
) -> tuple[str, str, str]:
    """Return (start_date, end_date, label) as YYYY-MM-DD strings (UTC).

    Deepgram's management API takes plain `start` / `end` date strings
    (YYYY-MM-DD). All windows are anchored to UTC calendar days. The `end`
    date is inclusive on Deepgram's side, so a one-day window (yesterday /
    today) uses the same date for start and end.
    """
    today = datetime.now(tz=timezone.utc).date()

    if since or until:
        if not (since and until):
            sys.exit("--since and --until must be provided together")
        _validate_date(since)
        _validate_date(until)
        return since, until, f"{since} → {until} (UTC)"

    if window == "yesterday":
        d = today - timedelta(days=1)
        return d.isoformat(), d.isoformat(), f"yesterday ({d.isoformat()}, UTC)"
    if window == "today":
        return today.isoformat(), today.isoformat(), f"today ({today.isoformat()}, UTC)"
    # Deepgram treats the `end` date as INCLUSIVE, so a rolling N-day window is
    # start = today-(N-1) .. end = today = exactly N calendar days (incl. today's
    # partial). Using today-N would span N+1 inclusive days — the off-by-one.
    if window == "last-7d":
        start = today - timedelta(days=6)
        return start.isoformat(), today.isoformat(), "last 7 days"
    if window == "last-30d":
        start = today - timedelta(days=29)
        return start.isoformat(), today.isoformat(), "last 30 days"

    sys.exit(f"Unknown window: {window!r}")


def _validate_date(s: str) -> None:
    try:
        datetime.strptime(s, "%Y-%m-%d")
    except ValueError:
        sys.exit(f"Bad date {s!r} — expected YYYY-MM-DD (interpreted as UTC)")


# ---------------------------------------------------------------------------
# Deepgram API helpers
# ---------------------------------------------------------------------------


class DeepgramScopeError(Exception):
    """Raised when the key can't see the requested project (NOT_FOUND /
    INSUFFICIENT_PERMISSIONS). Callers surface a clear message rather than
    treating it as a crash."""


def _request(path: str, api_key: str, params: dict | None = None) -> dict:
    """GET with retry on 429. Raises DeepgramScopeError on project-scope
    failures (404 NOT_FOUND / 403 INSUFFICIENT_PERMISSIONS) so the caller can
    print guidance about needing a usage:read key on that project."""
    url = f"{DEEPGRAM_API}{path}"
    headers = {"Authorization": f"Token {api_key}"}
    for attempt in range(5):
        # progress line to stderr — matches the sibling skill's house style
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
            if category in ("NOT_FOUND", "INSUFFICIENT_PERMISSIONS") or r.status_code in (403, 404):
                raise DeepgramScopeError(
                    f"{r.status_code} {category or r.reason} on {path}"
                )
        if not r.ok:
            sys.exit(f"Deepgram {r.status_code} on {path}: {r.text[:300]}")
        try:
            return r.json()
        except ValueError:
            sys.exit(f"Deepgram {path}: non-JSON response: {r.text[:300]}")
    sys.exit(f"Deepgram {path}: rate-limited after 5 retries")


def fetch_projects(api_key: str) -> list[dict]:
    print("# auth resolving projects", file=sys.stderr)
    data = _request(PROJECTS_PATH, api_key)
    return data.get("projects") or []


def resolve_project(api_key: str, project_id: str | None) -> dict:
    """Resolve which project to report on.

    - `--project-id` given → build a minimal project record (validated later
      when we fetch usage; if the key isn't a member it'll DeepgramScopeError).
    - Exactly one project visible → use it.
    - Multiple visible → require --project-id (ambiguous), unless exactly one
      shows usage — but we can't know that cheaply here, so we just ask.
    - None visible → fatal (key has no project membership at all).
    """
    if project_id:
        return {"project_id": project_id, "name": f"(project {project_id})"}
    projects = fetch_projects(api_key)
    if not projects:
        sys.exit(
            "This key is not a member of any Deepgram project. "
            "Check DEEPGRAM_OWNER_API_KEY / DEEPGRAM_API_KEY, or pass --project-id."
        )
    if len(projects) == 1:
        return projects[0]
    names = ", ".join(f"{p.get('name')!r} ({p.get('project_id')})" for p in projects)
    sys.exit(
        f"Multiple projects visible to this key — pass --project-id to disambiguate.\n"
        f"Available: {names}"
    )


def fetch_usage(api_key: str, pid: str, start: str, end: str) -> dict:
    return _request(
        f"{PROJECTS_PATH}/{pid}/usage", api_key, {"start": start, "end": end}
    )


def fetch_usage_breakdown(api_key: str, pid: str, start: str, end: str) -> dict:
    return _request(
        f"{PROJECTS_PATH}/{pid}/usage/breakdown", api_key, {"start": start, "end": end}
    )


def fetch_balances(api_key: str, pid: str) -> list[dict]:
    data = _request(f"{PROJECTS_PATH}/{pid}/balances", api_key)
    return data.get("balances") or []


def fetch_billing_breakdown(api_key: str, pid: str, start: str, end: str) -> dict | None:
    """Best-effort fetch of actual $ per period. Returns None if the endpoint
    isn't available for this project/plan (some plans don't expose it) — the
    caller then falls back to derived USD."""
    try:
        return _request(
            f"{PROJECTS_PATH}/{pid}/billing/breakdown", api_key,
            {"start": start, "end": end},
        )
    except DeepgramScopeError:
        # billing/breakdown may be gated separately from usage — don't let it
        # kill an otherwise-good usage run.
        print(
            "# billing/breakdown not accessible with this key — "
            "falling back to derived USD.",
            file=sys.stderr,
        )
        return None


# ---------------------------------------------------------------------------
# Aggregation — tolerant of Deepgram's response shape variations
# ---------------------------------------------------------------------------

# Deepgram usage results carry per-period metrics under keys that have drifted
# across API versions. We sum whichever hour/request keys are present so the
# report is robust to the exact shape (empty results → zeros).
_HOURS_KEYS = ("total_hours", "hours", "usd", "total")  # 'usd'/'total' filtered below
_REQUEST_KEYS = ("requests", "total_requests", "calls")


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def sum_usage(usage: dict) -> tuple[float, int, list[dict]]:
    """Collapse a usage/summary response into (total_hours, total_requests, per_day).

    Handles the documented shape:
      {"results": [{"start","end","hours"/"total_hours","requests", ...}, ...]}
    Empty results → (0.0, 0, []).
    """
    results = usage.get("results") or []
    total_hours = 0.0
    total_requests = 0
    per_day: list[dict] = []
    for row in results:
        hours = 0.0
        for k in ("total_hours", "hours"):
            if k in row:
                hours = _num(row.get(k))
                break
        requests_n = 0
        for k in _REQUEST_KEYS:
            if k in row:
                requests_n = int(_num(row.get(k)))
                break
        total_hours += hours
        total_requests += requests_n
        per_day.append(
            {
                "start": row.get("start"),
                "end": row.get("end"),
                "hours": hours,
                "requests": requests_n,
            }
        )
    return total_hours, total_requests, per_day


def sum_breakdown(breakdown: dict) -> list[dict]:
    """Extract per-model / per-feature rows from a usage/breakdown response.

    Deepgram nests model/feature usage under each result period. We flatten to
    a name → {hours, requests} map. Robust to empty results and to the exact
    nesting key ('models', 'features', 'tags', or a flat metric list)."""
    agg: dict[str, dict] = {}
    results = breakdown.get("results") or []
    for row in results:
        for group_key in ("models", "features", "tags"):
            for item in row.get(group_key) or []:
                name = (
                    item.get("name")
                    or item.get("model")
                    or item.get("feature")
                    or item.get("tag")
                    or "(unknown)"
                )
                bucket = agg.setdefault(name, {"hours": 0.0, "requests": 0})
                for k in ("total_hours", "hours"):
                    if k in item:
                        bucket["hours"] += _num(item.get(k))
                        break
                for k in _REQUEST_KEYS:
                    if k in item:
                        bucket["requests"] += int(_num(item.get(k)))
                        break
    return [
        {"name": name, "hours": v["hours"], "requests": v["requests"]}
        for name, v in sorted(agg.items(), key=lambda kv: kv[1]["hours"], reverse=True)
    ]


def sum_billing(billing: dict | None) -> tuple[float | None, list[dict]]:
    """Sum actual USD from a billing/breakdown response, if present.

    Returns (total_usd | None, per_period). None means the endpoint gave no
    usable dollar figures → caller uses derived USD. Robust to empty results."""
    if not billing:
        return None, []
    results = billing.get("results") or []
    if not results:
        return None, []
    total = 0.0
    per_period: list[dict] = []
    found_any = False
    for row in results:
        amount = None
        for k in ("amount", "usd", "total", "cost", "billed_amount"):
            if k in row:
                amount = _num(row.get(k))
                found_any = True
                break
        if amount is not None:
            total += amount
            per_period.append(
                {"start": row.get("start"), "end": row.get("end"), "usd": amount}
            )
    return (total if found_any else None), per_period


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _fmt_money(amount: float) -> str:
    return f"${amount:,.2f}"


def render_report(
    project: dict,
    label: str,
    total_hours: float,
    total_requests: int,
    per_day: list[dict],
    breakdown_rows: list[dict],
    balance_usd: float | None,
    actual_usd: float | None,
    rate_per_hour: float,
    *,
    prod_project_gap: bool,
) -> str:
    """Render the markdown report. Leads with actuals (hours/requests/balance);
    derived USD is secondary and clearly labelled."""
    name = project.get("name") or project.get("project_id")
    lines: list[str] = [f"# Deepgram STT Spend — {name}", ""]

    days = max(len(per_day), 1) if per_day else 1
    daily_hours = total_hours / days if days else 0.0

    # Cost line — prefer actual $ from billing/breakdown; else derive.
    if actual_usd is not None:
        cost_str = f"**{_fmt_money(actual_usd)}** (actual, from billing breakdown)"
        derived = False
    else:
        derived_usd = total_hours * rate_per_hour
        cost_str = (
            f"**~{_fmt_money(derived_usd)}** (derived: "
            f"{total_hours:,.2f} hrs × {_fmt_money(rate_per_hour)}/hr)"
        )
        derived = True

    balance_str = _fmt_money(balance_usd) if balance_usd is not None else "n/a"

    lines.append(f"**Window:** {label}")
    lines.append(
        f"**Usage:** {total_hours:,.2f} audio hours · {total_requests:,} requests"
        + (f" (~{daily_hours:,.2f} hrs/day)" if per_day else "")
    )
    lines.append(f"**Prepaid balance:** {balance_str} (actual)")
    lines.append(f"**Spend this window:** {cost_str}")
    lines.append("")

    if total_hours == 0 and total_requests == 0:
        lines.append(
            "> _No STT usage recorded for this project in this window._"
        )
        if prod_project_gap:
            lines.append("")
            lines.append(
                "> **Note:** zero STT usage in this window for the auto-resolved project. "
                "Either no LiveKit voice traffic has hit it yet / the deployed agent "
                "isn't on this project's `DEEPGRAM_API_KEY`, or production STT lives in "
                "another project this key can't see. Confirm the agent's key and this "
                "`DEEPGRAM_OWNER_API_KEY` share the same project (both should be the Aura "
                "STT project), then re-run once voice traffic has flowed — or pass "
                "`--project-id` to target a specific project."
            )
        lines.append("")

    # Per-model / per-feature breakdown table.
    if breakdown_rows:
        lines.append("## By model / feature")
        lines.append("")
        lines.append("| Model / feature | Hours | Requests | Derived USD |")
        lines.append("|---|---:|---:|---:|")
        for row in breakdown_rows:
            drv = row["hours"] * rate_per_hour
            lines.append(
                f"| {row['name']} | {row['hours']:,.2f} | {row['requests']:,} | "
                f"~{_fmt_money(drv)} |"
            )
        lines.append("")

    # Per-day table (only when there's usage worth showing).
    if per_day and total_hours > 0:
        lines.append("## By day")
        lines.append("")
        lines.append("| Day | Hours | Requests |")
        lines.append("|---|---:|---:|")
        for d in per_day:
            day = d.get("start") or "?"
            lines.append(f"| {day} | {d['hours']:,.2f} | {d['requests']:,} |")
        lines.append("")

    # Caveat line — always present, so the derived-vs-actual distinction is
    # never lost.
    if derived:
        lines.append(
            f"_Caveat: USD is **derived** (hours × {_fmt_money(rate_per_hour)}/hr, "
            "nova-3 streaming pay-as-you-go — see methodology.md), not billed "
            "dollars. The prepaid balance above is the only actual $ figure. "
            "Deepgram's billing breakdown did not expose per-period spend for "
            "this project/window._"
        )
    else:
        lines.append(
            "_Caveat: spend is actual (from Deepgram's billing breakdown). "
            "Derived-USD columns in the breakdown table use "
            f"{_fmt_money(rate_per_hour)}/hr for reference only._"
        )
    lines.append("")
    lines.append("Deepgram console: https://console.deepgram.com/")
    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")

    ap = argparse.ArgumentParser(
        description="Deepgram STT spend report for the Aura project(s)"
    )
    ap.add_argument(
        "--window",
        choices=WINDOW_CHOICES,
        default="last-7d",
        help="Time window (UTC). Default last-7d.",
    )
    ap.add_argument("--since", help="UTC start date (YYYY-MM-DD). Use with --until.")
    ap.add_argument("--until", help="UTC end date, inclusive (YYYY-MM-DD).")
    ap.add_argument(
        "--project-id",
        help="Deepgram project ID to report on. Overrides auto-resolution "
             "from GET /v1/projects.",
    )
    ap.add_argument(
        "--rate-per-hour",
        type=float,
        default=DEFAULT_RATE_PER_HOUR,
        help=f"USD per audio hour for DERIVING spend when actual $ is "
             f"unavailable. Default {DEFAULT_RATE_PER_HOUR} (Nova-3 Multilingual "
             "streaming PAYG, $0.0058/min — our current model; see methodology.md).",
    )
    ap.add_argument("--json", dest="json_out", help="Write structured JSON to this path.")
    args = ap.parse_args()

    api_key = os.environ.get("DEEPGRAM_OWNER_API_KEY") or os.environ.get("DEEPGRAM_API_KEY")
    if not api_key:
        sys.exit(
            "Missing DEEPGRAM_OWNER_API_KEY (preferred) or DEEPGRAM_API_KEY. "
            "Add a Deepgram management API key to ~/.claude/settings.json under "
            "the `env` block and restart Claude Code."
        )

    start, end, label = resolve_window(args.window, args.since, args.until)

    try:
        project = resolve_project(api_key, args.project_id)
        pid = project["project_id"]
        usage = fetch_usage(api_key, pid, start, end)
        breakdown = fetch_usage_breakdown(api_key, pid, start, end)
        balances = fetch_balances(api_key, pid)
        billing = fetch_billing_breakdown(api_key, pid, start, end)
    except DeepgramScopeError as e:
        target = args.project_id or "(resolved project)"
        sys.exit(
            f"# Deepgram project not accessible with this key ({e}).\n"
            f"The key is not a member of project {target}. To report on this "
            "project, use a Deepgram management key with usage:read access on "
            "it (the Aura PRODUCTION STT project is separate from the personal "
            "project this key can see). Set it as DEEPGRAM_OWNER_API_KEY and "
            "pass --project-id."
        )

    print(
        f"# fetched usage + breakdown + balances + billing for "
        f"project {project.get('project_id')}",
        file=sys.stderr,
    )

    total_hours, total_requests, per_day = sum_usage(usage)
    breakdown_rows = sum_breakdown(breakdown)
    actual_usd, billing_periods = sum_billing(billing)

    balance_usd = None
    for b in balances:
        if (b.get("units") or "").lower() == "usd":
            balance_usd = _num(b.get("amount"))
            break
    if balance_usd is None and balances:
        balance_usd = _num(balances[0].get("amount"))

    # Flag the production-project key gap only when the project was AUTO-RESOLVED
    # (no --project-id) AND shows no usage — i.e. we fell back to the personal
    # project. If the caller explicitly passed --project-id, they chose the
    # project, so a quiet window is just "no usage", not "prod lives elsewhere".
    prod_project_gap = (not args.project_id) and total_hours == 0 and total_requests == 0

    report = render_report(
        project,
        label,
        total_hours,
        total_requests,
        per_day,
        breakdown_rows,
        balance_usd,
        actual_usd,
        args.rate_per_hour,
        prod_project_gap=prod_project_gap,
    )
    print(report)

    if args.json_out:
        out = {
            "project": {
                "project_id": project.get("project_id"),
                "name": project.get("name"),
            },
            "window": {"start": start, "end": end, "label": label},
            "currency": "usd",
            "rate_per_hour_assumption": args.rate_per_hour,
            "total_hours": total_hours,
            "total_requests": total_requests,
            "prepaid_balance_usd": balance_usd,
            "actual_usd": actual_usd,
            "derived_usd": (
                None if actual_usd is not None else total_hours * args.rate_per_hour
            ),
            "usd_is_derived": actual_usd is None,
            "by_model_feature": breakdown_rows,
            "by_day": per_day,
            "billing_periods": billing_periods,
            "raw": {
                "usage": usage,
                "usage_breakdown": breakdown,
                "balances": balances,
                "billing_breakdown": billing,
            },
        }
        with open(args.json_out, "w") as f:
            json.dump(out, f, indent=2, default=str)


if __name__ == "__main__":
    main()
