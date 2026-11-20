#!/usr/bin/env python3
"""
Cartesia (TTS provider) spend + usage report for the Aura workspace.

Reads ACTUAL usage from the Cartesia admin usage API and renders a markdown
report of **actuals only** — no end-of-period projection:

  - **Credit consumption** per window and per day, direct from
    `GET /usage/credits`. Credits are the truth (≈1 credit per TTS character);
    this is what Cartesia actually recorded.
  - **TTS USD is DERIVED**, not billed: credits / 1_000_000 × `--rate-per-million`
    (default 38.00 — the Cartesia **Scale** plan rate, $38 per 1M credits, from
    cartesia.ai/pricing. We are on Scale ($299/mo, 8M credits/mo) as of the LiveKit
    voice go-live; $38/M is the marginal/list rate — the real negotiated contract
    overage rate is still TBD. Other tiers: Startup $45/1M, Pro $65/1M — pass
    `--rate-per-million` when the plan or contract rate changes).
    There is no per-request TTS invoice from the API, so this figure is a plan-rate
    approximation, clearly labelled as such —
    same spirit as eng:analyze-elevenlabs-costs hiding the retail-equivalent
    figure so it isn't misread as a bill.
  - **Agent-calling USD is ACTUAL**: `GET /usage/agents` returns `cents`
    directly, so cents/100 = real USD. We use LiveKit for voice orchestration
    and Cartesia only for TTS, so this is currently 0 — but it's included so the
    report stays correct if that changes. Minutes + calls are shown alongside.

Windows are UTC. The API takes RFC3339 `start_ts` / `end_ts` and an optional
`interval` (day|week|month); we always request `interval=day` and sum
client-side so partial-day windows behave correctly and a per-day table is
available.

Usage:
    python report.py                                  # last 7 days (default)
    python report.py --window yesterday
    python report.py --window today
    python report.py --window last-30d
    python report.py --since 2026-06-01 --until 2026-07-01
    python report.py --rate-per-million 49.00          # override plan rate
    python report.py --json out.json

Env:
    CARTESIA_ADMIN_API_KEY  Required. The admin key (sk_car_admin_...). A regular
                            sk_car_ inference key returns 401 on /usage. Read from
                            the environment only — never printed, never in argv,
                            never written to any file.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

CARTESIA_API = "https://api.cartesia.ai"
CARTESIA_VERSION = "2026-03-01"
CREDITS_PATH = "/usage/credits"
AGENTS_PATH = "/usage/agents"

# Cartesia bills TTS in credits (≈1 credit per synthesised character). There is
# no per-request USD figure from the API, so we derive TTS dollars from the
# plan's per-credit rate. Default is the **Scale plan rate ($38 / 1M credits)** —
# the current plan, on since the LiveKit voice go-live (Scale = $299/mo, 8M
# credits/mo). $38/M is the marginal/list rate; the real negotiated contract
# overage rate is still TBD (list tiers: Startup $45/1M, Pro $65/1M, Scale $38/1M).
# Override with --rate-per-million on a plan or contract-rate change.
DEFAULT_RATE_PER_MILLION = 38.00
CREDITS_PER_MILLION = 1_000_000

WINDOW_CHOICES = ["yesterday", "today", "last-7d", "last-30d"]


# ---------------------------------------------------------------------------
# Time-window resolution
# ---------------------------------------------------------------------------


def resolve_window(
    window: str | None,
    since: str | None,
    until: str | None,
) -> tuple[str, str, str]:
    """Return (start_ts_rfc3339, end_ts_rfc3339, label). All UTC.

    Cartesia's usage API expects RFC3339 timestamps. `--since`/`--until` accept
    either a bare date (YYYY-MM-DD, interpreted as UTC midnight) or a full
    RFC3339 string. Named windows are anchored to UTC midnight so day buckets
    line up with the API's own day boundaries.
    """
    now = datetime.now(tz=timezone.utc)
    today_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)

    if since or until:
        if not (since and until):
            sys.exit("--since and --until must be provided together")
        start_dt = _parse_ts(since)
        end_dt = _parse_ts(until)
        label = f"{since} → {until} (UTC)"
    elif window == "yesterday":
        start_dt = today_midnight - timedelta(days=1)
        end_dt = today_midnight
        label = f"yesterday ({start_dt.strftime('%Y-%m-%d')}, UTC)"
    elif window == "today":
        start_dt = today_midnight
        end_dt = now
        label = f"today ({start_dt.strftime('%Y-%m-%d')}, UTC)"
    elif window == "last-7d":
        end_dt = now
        start_dt = now - timedelta(days=7)
        label = "last 7 days"
    elif window == "last-30d":
        end_dt = now
        start_dt = now - timedelta(days=30)
        label = "last 30 days"
    else:
        sys.exit(f"Unknown window: {window!r}")

    if start_dt >= end_dt:
        sys.exit(f"Empty window: start {start_dt.isoformat()} >= end {end_dt.isoformat()}")

    return _to_rfc3339(start_dt), _to_rfc3339(end_dt), label


def _parse_ts(s: str) -> datetime:
    """Parse a bare date (YYYY-MM-DD → UTC midnight) or a full RFC3339 string."""
    # Try a bare date first — the common CLI case.
    try:
        return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    # Fall back to full RFC3339. Accept a trailing Z (Python <3.11 rejects it).
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        sys.exit(f"Bad timestamp {s!r} — expected YYYY-MM-DD or RFC3339")


def _to_rfc3339(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Cartesia API helpers
# ---------------------------------------------------------------------------


def _request(path: str, params: dict, api_key: str) -> dict:
    """GET a usage endpoint with retry on 429.

    The admin key is sent as a Bearer token. Only the admin key (sk_car_admin_)
    is authorised for /usage — a regular sk_car_ inference key returns 401.
    """
    url = f"{CARTESIA_API}{path}"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Cartesia-Version": CARTESIA_VERSION,
    }
    for attempt in range(5):
        r = requests.get(url, headers=headers, params=params, timeout=60)
        if r.status_code == 429:
            wait = float(r.headers.get("Retry-After", 2 ** attempt))
            time.sleep(min(wait, 30))
            continue
        if r.status_code == 401:
            # Most common misconfiguration: an inference key instead of admin.
            sys.exit(
                f"Cartesia 401 on {path} — the key in CARTESIA_ADMIN_API_KEY is not "
                "authorised for /usage. Only the admin key (sk_car_admin_...) works; "
                "a regular sk_car_ inference key returns 401."
            )
        if not r.ok:
            sys.exit(f"Cartesia {r.status_code} on {path}: {r.text[:300]}")
        return r.json()
    sys.exit(f"Cartesia {path}: rate-limited after 5 retries")


def fetch_credits(start_ts: str, end_ts: str, api_key: str) -> list[dict]:
    """Per-day credit consumption. Returns the `data` array of daily buckets."""
    params = {"interval": "day", "start_ts": start_ts, "end_ts": end_ts}
    data = _request(CREDITS_PATH, params, api_key)
    return data.get("data") or []


def fetch_agents(start_ts: str, end_ts: str, api_key: str) -> list[dict]:
    """Per-day agent-calling usage (cents/minutes/calls). Returns `data` array."""
    params = {"interval": "day", "start_ts": start_ts, "end_ts": end_ts}
    data = _request(AGENTS_PATH, params, api_key)
    return data.get("data") or []


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def build_daily(credit_buckets: list[dict], agent_buckets: list[dict], rate_per_million: float) -> list[dict]:
    """Merge the credits and agents day buckets, keyed by start_ts.

    Each row carries actual credits, DERIVED TTS USD (credits × plan rate),
    ACTUAL agent USD (cents/100), agent minutes, and agent calls. Days present
    in only one series are still emitted (the other side defaults to 0).
    """
    rows: dict[str, dict] = {}
    for b in credit_buckets:
        ts = b.get("start_ts")
        if ts is None:
            continue
        credits = float(b.get("credits") or 0)
        rows.setdefault(ts, _empty_row(ts))
        rows[ts]["credits"] = credits
        rows[ts]["tts_usd_derived"] = credits / CREDITS_PER_MILLION * rate_per_million
    for b in agent_buckets:
        ts = b.get("start_ts")
        if ts is None:
            continue
        rows.setdefault(ts, _empty_row(ts))
        rows[ts]["agent_usd_actual"] = float(b.get("cents") or 0) / 100.0
        rows[ts]["agent_minutes"] = float(b.get("minutes") or 0)
        rows[ts]["agent_calls"] = int(b.get("calls") or 0)
    return [rows[ts] for ts in sorted(rows)]


def _empty_row(ts: str) -> dict:
    return {
        "start_ts": ts,
        "credits": 0.0,
        "tts_usd_derived": 0.0,
        "agent_usd_actual": 0.0,
        "agent_minutes": 0.0,
        "agent_calls": 0,
    }


def totals(daily: list[dict]) -> dict:
    return {
        "credits": sum(r["credits"] for r in daily),
        "tts_usd_derived": sum(r["tts_usd_derived"] for r in daily),
        "agent_usd_actual": sum(r["agent_usd_actual"] for r in daily),
        "agent_minutes": sum(r["agent_minutes"] for r in daily),
        "agent_calls": sum(r["agent_calls"] for r in daily),
    }


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _fmt_money(amount: float) -> str:
    return f"${amount:,.2f}"


def _fmt_credits_compact(n: float) -> str:
    """1_234_567 → '1.23M', 12_345 → '12.3k', 999 → '999'."""
    n = float(n)
    if abs(n) >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if abs(n) >= 1_000:
        return f"{n / 1_000:.1f}k"
    return f"{n:,.0f}"


def _day_label(ts: str) -> str:
    """Render a bucket's start_ts as YYYY-MM-DD for the per-day table."""
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).strftime("%Y-%m-%d")
    except (ValueError, AttributeError):
        return ts


def render_report(label: str, daily: list[dict], tot: dict, rate_per_million: float) -> str:
    lines: list[str] = []
    lines.append(f"# Cartesia Spend — {label}")
    lines.append("")

    if tot["credits"] == 0 and tot["agent_calls"] == 0 and tot["agent_usd_actual"] == 0:
        lines.append("_No usage recorded in this window._")
        return "\n".join(lines) + "\n"

    # Headline pairs the actual truth (credits) with the derived TTS $ and the
    # actual agent $. TTS $ is explicitly labelled as derived from the plan rate.
    lines.append(
        f"**Headline:** {_fmt_credits_compact(tot['credits'])} TTS credits → "
        f"**{_fmt_money(tot['tts_usd_derived'])}** derived (plan rate "
        f"{_fmt_money(rate_per_million)}/M credits) · "
        f"agents: {tot['agent_minutes']:,.1f} min across {tot['agent_calls']:,} calls → "
        f"**{_fmt_money(tot['agent_usd_actual'])}** actual."
    )
    lines.append("")

    lines.append("## Totals")
    lines.append("")
    lines.append(f"- **TTS credits (actual):** {int(tot['credits']):,}")
    lines.append(
        f"- **TTS USD (derived, plan rate {_fmt_money(rate_per_million)}/M):** "
        f"{_fmt_money(tot['tts_usd_derived'])}"
    )
    lines.append(f"- **Agent minutes (actual):** {tot['agent_minutes']:,.1f}")
    lines.append(f"- **Agent calls (actual):** {int(tot['agent_calls']):,}")
    lines.append(f"- **Agent USD (actual, from cents):** {_fmt_money(tot['agent_usd_actual'])}")
    lines.append("")

    lines.append("## Per day")
    lines.append("")
    lines.append("| Date | Credits | TTS $ (derived) | Agent min | Agent $ (actual) |")
    lines.append("|---|---:|---:|---:|---:|")
    for r in daily:
        lines.append(
            f"| {_day_label(r['start_ts'])} "
            f"| {int(r['credits']):,} "
            f"| {_fmt_money(r['tts_usd_derived'])} "
            f"| {r['agent_minutes']:,.1f} "
            f"| {_fmt_money(r['agent_usd_actual'])} |"
        )
    lines.append("")

    lines.append(
        "_Caveat: TTS $ is derived from the configured plan rate, not a Cartesia "
        "invoice; credits and agent $ are actual._"
    )
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    # Force UTF-8 stdout — rendered markdown uses → and — which crash on
    # Windows' default cp1252 encoding. See AINative CLAUDE.md skill-authoring.
    sys.stdout.reconfigure(encoding="utf-8")

    ap = argparse.ArgumentParser(description="Cartesia TTS spend + usage report for the Aura workspace")
    ap.add_argument(
        "--window",
        choices=WINDOW_CHOICES,
        default="last-7d",
        help="Time window (UTC). Default `last-7d`.",
    )
    ap.add_argument("--since", help="Start (YYYY-MM-DD or RFC3339, UTC). Use with --until.")
    ap.add_argument("--until", help="End, exclusive (YYYY-MM-DD or RFC3339, UTC).")
    ap.add_argument(
        "--rate-per-million",
        type=float,
        default=DEFAULT_RATE_PER_MILLION,
        help=f"USD per 1,000,000 credits used to DERIVE TTS $ (default {DEFAULT_RATE_PER_MILLION:.2f}, "
             "the Cartesia 'Scale' plan effective rate: $299 / 8,000,000 credits).",
    )
    ap.add_argument("--json", dest="json_out", help="Write structured JSON to this path.")
    args = ap.parse_args()

    # Read the admin key from the environment ONLY — never accept it in argv,
    # never print it, never write it to a file.
    api_key = os.environ.get("CARTESIA_ADMIN_API_KEY")
    if not api_key:
        sys.exit(
            "Missing CARTESIA_ADMIN_API_KEY. This must be the admin key "
            "(sk_car_admin_...) — a regular sk_car_ inference key returns 401 on "
            "/usage. Add it to ~/.claude/settings.json under the `env` block and "
            "restart Claude Code."
        )

    start_ts, end_ts, label = resolve_window(args.window, args.since, args.until)

    print(f"# auth: CARTESIA_ADMIN_API_KEY loaded (admin scope)", file=sys.stderr)
    print(f"# query: {CREDITS_PATH} + {AGENTS_PATH} interval=day {start_ts} → {end_ts}", file=sys.stderr)

    credit_buckets = fetch_credits(start_ts, end_ts, api_key)
    agent_buckets = fetch_agents(start_ts, end_ts, api_key)

    print(
        f"# fetched: {len(credit_buckets)} credit day(s), {len(agent_buckets)} agent day(s)",
        file=sys.stderr,
    )

    daily = build_daily(credit_buckets, agent_buckets, args.rate_per_million)
    tot = totals(daily)

    report = render_report(label, daily, tot, args.rate_per_million)
    print(report)

    if args.json_out:
        out = {
            "window": {"label": label, "start_ts": start_ts, "end_ts": end_ts},
            "rate_per_million_usd": args.rate_per_million,
            "totals": {
                "credits": tot["credits"],
                "tts_usd_derived": tot["tts_usd_derived"],
                "agent_usd_actual": tot["agent_usd_actual"],
                "agent_minutes": tot["agent_minutes"],
                "agent_calls": tot["agent_calls"],
            },
            "daily": daily,
            "raw": {"credits": credit_buckets, "agents": agent_buckets},
        }
        with open(args.json_out, "w") as f:
            json.dump(out, f, indent=2, default=str)


if __name__ == "__main__":
    main()
