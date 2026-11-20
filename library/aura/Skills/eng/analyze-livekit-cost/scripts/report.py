#!/usr/bin/env python3
"""
analyze-livekit-cost CLI entry point.

DERIVED cost estimate for the Aurie LiveKit voice agent over a window. There is NO LiveKit
billing/usage API — the `lk` CLI exposes none, and spend lives only in the LiveKit Cloud
dashboard. So cost here is DERIVED, not billed: total session minutes × a configured
per-minute rate (--rate, default $0.01/min — the LiveKit Scale plan agent-session-minute
OVERAGE rate; see references/methodology.md). Scale ($500/mo) includes 50,000 agent
minutes/month, so while total monthly minutes stay under that allotment the true marginal
cost is ~$0 (covered by the flat fee) and this derived figure is an overage-equivalent
UPPER BOUND, not incremental spend.

This is LiveKit's OWN cost (realtime transport + Cloud Agents hosting) — a SEPARATE line
item from TTS (Cartesia/ElevenLabs) and LLM (OpenAI), which the analyze-elevenlabs-* and
analyze-openai-* skills cover. Do NOT add these numbers to those — they are different
providers and would double-count nothing here, but summing them across skills is only valid
when each provider is counted once.

Minutes are the same log-span proxy analyze-livekit-usage bills against: (last log ts −
first log ts) per room. Sourced entirely from the forwarded GCP logs
(logName projects/firebase-auratech16/logs/aurie-voice-agent) — see livekit_core.py.

Read-only. No writes anywhere. Aggregates by session/user; UIDs are truncated in all
markdown surfaces (full UID only in the optional --json payload).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
import pathlib
import re
from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import livekit_core as lk
import livekit_plan  # the plan of record + rate card, shared with report-livekit-quota


def _ua_who() -> str:
    """Login name of whoever runs this, for the user agent: AINATIVE_UA_WHO, else the global git email's local part, git user.name, the OS account, or "unknown"."""
    who = re.sub(r"^\d+\+", "", os.environ.get("AINATIVE_UA_WHO", "").strip().partition("@")[0])
    if not who:
        if not hasattr(_ua_who, "found"):
            import getpass, subprocess
            found = ""
            for key in ("user.email", "user.name"):
                try:
                    val = subprocess.run(["git", "config", "--global", key], capture_output=True, encoding="utf-8", errors="replace", timeout=5).stdout.strip()
                except (OSError, subprocess.SubprocessError):
                    val = ""
                found = re.sub(r"^\d+\+", "", val.partition("@")[0]) if key == "user.email" else val
                if found:
                    break
            if not found:
                try:
                    found = getpass.getuser()
                except Exception:
                    found = ""
            _ua_who.found = found
        who = _ua_who.found
    return re.sub(r"[^A-Za-z0-9._-]+", "-", who).strip("-.") or "unknown"


def user_agent() -> str:
    """`AuraClaude/<who>/<plugin>:<skill>@<version> (session <id>)` -- labels this skill's calls to RTDB, GCP and the Aura API.

    Same format as gcloud_sa.user_agent(). A label, not proof: anyone can send it.
    """
    skill = pathlib.Path(__file__).resolve().parents[1]
    root = skill.parents[1]
    try:
        plugin = json.loads((root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["name"]
    except (OSError, ValueError, KeyError, TypeError):
        plugin = root.name
    try:
        front = (skill / "SKILL.md").read_text(encoding="utf-8").split("---", 2)[1]
        version = re.search(r'version:\s*"([^"]+)"', front).group(1)
    except (OSError, IndexError, AttributeError):
        version = "unknown"
    session = os.environ.get("AINATIVE_SESSION_NAME") or (os.environ.get("CLAUDE_CODE_SESSION_ID") or "")[:8]
    session = re.sub(r"[^A-Za-z0-9._-]+", "-", session).strip("-") or "none"
    return f"AuraClaude/{_ua_who()}/{plugin}:{skill.name}@{version} (session {session})"


# The plan's OVERAGE rate, not its cost per minute — see livekit_plan.effective_rate_per_min.
LIVEKIT_OVERAGE_PER_MIN = livekit_plan.overage_per_unit("agent_session_minutes", livekit_plan.DEFAULT_PLAN)  # noqa: E402


# ---------------------------------------------------------------------------
# Arg parsing / window resolution
# ---------------------------------------------------------------------------


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="analyze livekit cost",
        description="Derived spend estimate (minutes × rate) for the Aurie LiveKit voice agent.",
    )
    p.add_argument("--window", default="yesterday",
                   choices=["yesterday", "today", "last-7d", "last-30d"],
                   help="Time window (default yesterday, full UTC day).")
    p.add_argument("--since", help="Custom window start YYYY-MM-DD (UTC). Overrides --window; pair with --until.")
    p.add_argument("--until", help="Custom window end YYYY-MM-DD (UTC, exclusive). Defaults to now.")
    p.add_argument("--project", help="Override the GCP project derived from the SA key.")
    p.add_argument("--rate", type=float, default=LIVEKIT_OVERAGE_PER_MIN,
                   help="USD per session-minute (default 0.01 — the LiveKit Scale plan "
                        "agent-minute overage rate; upper bound while under the 50k-min/mo "
                        "included allotment, see references/methodology.md).")
    p.add_argument("--budget", type=float,
                   help="Optional daily USD budget. If derived spend exceeds it, a WARN line is printed.")
    p.add_argument("--top", type=int, default=10, help="Cap heavy-user rows in output (default 10).")
    p.add_argument("--max-entries", type=int, default=50000, help="Cap log entries fetched (default 50000).")
    p.add_argument("--json", dest="json_path", help="Also write the full payload (with untruncated UIDs) to this path.")
    return p.parse_args(argv)


def resolve_window(args: argparse.Namespace) -> tuple[str, str, str, float]:
    """Return (time_clause, label, iso_range, window_days) for the requested window.

    window_days is the actual span of the window (fractional for a partial "today"), used to
    normalize derived spend to a per-day rate before comparing against --budget (a daily
    ceiling) — otherwise a multi-day window like --window last-7d/last-30d would stack N days
    of spend against a one-day budget and false-trigger.
    """
    now = datetime.now(timezone.utc)
    if args.since:
        start = datetime.strptime(args.since, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        end = (
            datetime.strptime(args.until, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            if args.until else now
        )
        label = f"{args.since} → {args.until or 'now'} (UTC)"
        days = max((end - start).total_seconds() / 86400.0, 1e-9)
        return lk.range_filter(start, end), label, f"{start.isoformat()}/{end.isoformat()}", days
    if args.window == "yesterday":
        start, end, label = lk.utc_day_bounds(1)
        return lk.range_filter(start, end), label, f"{start.isoformat()}/{end.isoformat()}", 1.0
    if args.window == "today":
        start, end, label = lk.utc_day_bounds(0)
        days = max((end - start).total_seconds() / 86400.0, 1e-9)
        return lk.range_filter(start, end), label, f"{start.isoformat()}/{end.isoformat()}", days
    days = {"last-7d": 7, "last-30d": 30}[args.window]
    window = lk.timedelta(days=days)
    return lk.since_filter(window), f"last {days}d", f"since-{days}d", float(days)


# ---------------------------------------------------------------------------
# Subscription-tier annotation (optional — Aura PaymentService)
# ---------------------------------------------------------------------------

_AURA_SUBS_URL = "https://services.aurahealth.io/subscriptions/list/{uid}"


def _derive_tier(rows: list[dict]) -> str:
    """Collapse the subscription rows to one short tier label, preferring the AI dimension.

    Deliberately coarse — this is a cost-report annotation, not a billing readout. For the
    full state machine (refunded / revoked / trial-expired disambiguation) see
    report-elevenlabs-costs/references/methodology.md.
    """
    if not rows:
        return "Never premium"
    ai = [r for r in rows if r.get("type") == "ai"]
    dim = ai or rows
    active = [r for r in dim if not r.get("deletedAt")]
    if any(r.get("lifetime") for r in active):
        return "AI Lifetime" if ai else "Lifetime"
    for r in active:
        if r.get("trial"):
            return ("AI " if ai else "") + ("Trial" if r.get("autoRenew") else "Trial (cancelled)")
    if active:
        return ("AI " if ai else "") + ("Paid" if any(r.get("autoRenew") for r in active) else "Cancelled (active)")
    # Only deleted rows remain.
    return ("AI " if ai else "") + "Expired/Refunded"


def fetch_tier(uid: str, token: str) -> Optional[str]:
    """Best-effort tier lookup. Returns None on any failure (never raises)."""
    url = _AURA_SUBS_URL.format(uid=uid)
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {token}", "User-Agent": user_agent(), "Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, json.JSONDecodeError, ValueError, OSError):
        return None
    rows = body if isinstance(body, list) else (body.get("subscriptions") or body.get("data") or [])
    if not isinstance(rows, list):
        return None
    return _derive_tier(rows)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def render(entries: list[dict], label: str, rate: float, budget: Optional[float],
           top: int, tiers: dict[str, str], window_days: float) -> tuple[str, dict]:
    sessions = lk.build_sessions(entries)
    slist = list(sessions.values())
    n_sessions = len(slist)
    total_min = sum(s.minutes for s in slist)
    derived_spend = total_min * rate
    # --budget is a DAILY ceiling; normalize the window's total spend to a per-day rate before
    # comparing, so a multi-day window (--window last-7d/last-30d) doesn't stack N days of
    # spend against a one-day budget and false-trigger (or a partial "today" window projects
    # its trajectory instead of under-warning until late in the day).
    daily_rate_spend = derived_spend / window_days

    # Per-user rollup (heavy users by derived cost).
    by_user: dict[str, dict] = defaultdict(lambda: {"sessions": 0, "minutes": 0.0})
    for s in slist:
        u = s.user_id or "(no-user)"
        by_user[u]["sessions"] += 1
        by_user[u]["minutes"] += s.minutes
    for u, agg in by_user.items():
        agg["cost"] = agg["minutes"] * rate
    ranked_users = sorted(by_user.items(), key=lambda kv: -kv[1]["cost"])

    lines = [
        f"LiveKit cost (DERIVED) — {label}",
        "",
        f"- Sessions:        {n_sessions}",
        f"- Total minutes:   {total_min:.1f}",
        f"- Rate:            ${rate:.4f}/min (assumption — see methodology)",
        f"- Derived spend:   ${derived_spend:.2f}",
        "",
        "⚠ Derived, not billed. LiveKit exposes no usage/billing API — this is "
        "session minutes × the configured rate, NOT an invoice. Reconcile against the "
        "LiveKit Cloud dashboard. Excludes TTS + LLM + STT cost (separate providers).",
        "",
    ]

    if budget is not None:
        if daily_rate_spend > budget:
            lines.append(f"WARN: derived spend rate ${daily_rate_spend:.2f}/day exceeds daily budget "
                         f"${budget:.2f} (over by ${daily_rate_spend - budget:.2f}/day).")
        else:
            lines.append(f"OK: derived spend rate ${daily_rate_spend:.2f}/day within daily budget "
                         f"${budget:.2f} (${budget - daily_rate_spend:.2f}/day headroom).")
        lines.append("")

    if n_sessions:
        show_tier = bool(tiers)
        lines.append(f"Top {min(top, len(ranked_users))} users by derived cost:")
        lines.append("")
        header = "| user | sessions | minutes | cost |" + (" tier |" if show_tier else "")
        sep = "|---|---:|---:|---:|" + ("---|" if show_tier else "")
        lines.append(header)
        lines.append(sep)
        for uid, agg in ranked_users[:top]:
            label_uid = lk.short_uid(uid) if uid != "(no-user)" else "(no-user)"
            row = f"| {label_uid} | {agg['sessions']} | {agg['minutes']:.1f} | ${agg['cost']:.2f} |"
            if show_tier:
                row += f" {tiers.get(uid, '?')} |"
            lines.append(row)
    else:
        lines.append("No sessions in this window. If unexpected, widen --window or check the agent "
                     "is live (`lk agent status`).")

    payload = {
        "window": label,
        "window_days": round(window_days, 4),
        "rate_usd_per_min": rate,
        "rate_is_assumption": True,
        "sessions": n_sessions,
        "total_minutes": round(total_min, 2),
        "derived_spend_usd": round(derived_spend, 2),
        "derived_spend_usd_per_day": round(daily_rate_spend, 2),
        "derived_not_billed": True,
        "budget_usd_per_day": budget,
        "over_budget": (budget is not None and daily_rate_spend > budget),
        "excludes": ["tts (cartesia/elevenlabs)", "llm (openai)", "stt"],
        "by_user": [
            {
                "user_id": uid,  # full UID in JSON only
                "sessions": agg["sessions"],
                "minutes": round(agg["minutes"], 2),
                "cost_usd": round(agg["cost"], 2),
                "tier": tiers.get(uid),
            }
            for uid, agg in ranked_users
        ],
    }
    return "\n".join(lines), payload


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: Optional[list[str]] = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = parse_args(argv)

    try:
        time_clause, label, _iso, window_days = resolve_window(args)
    except ValueError as e:
        print(f"INPUT ERROR: {e}", file=sys.stderr)
        return 2

    try:
        auth = lk.get_client(project_override=args.project)
    except lk.AuthError as e:
        print(f"AUTH ERROR: {e}", file=sys.stderr)
        return 3
    print(f"# auth: source={auth.source} project={auth.project} principal={auth.principal}", file=sys.stderr)

    filter_str = lk.base_filter(time_clause)
    print(f"# query: {filter_str}", file=sys.stderr)

    try:
        entries = list(lk.list_entries(auth, filter_str, max_entries=args.max_entries))
    except lk.TruncatedReadError as exc:
        # Explicit, and BEFORE the broad handler: a bare `except Exception` would turn a
        # truncated-but-usable read into a hard failure, which is worse than the silent
        # undercount this replaces. Keep the entries, say loudly that they are partial.
        entries = exc.entries
        print(f"> \u26a0\ufe0f **Truncated read** \u2014 hit the {args.max_entries}-entry cap, so this window shows only its most recent {len(entries)} entries. Session and minute totals below are a **LOWER BOUND**. Narrow the window or raise --max-entries.\n")
        print(f"# WARNING: read truncated at {args.max_entries} entries \u2014 totals UNDERCOUNT", file=sys.stderr)
    except Exception as e:
        print(f"QUERY ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        return 4
    print(f"# fetched {len(entries)} log entries", file=sys.stderr)

    # Optional subscription-tier annotation for the top-N cost users. Graceful degrade:
    # skip cleanly if no token, and never hard-fail on a bad lookup.
    tiers: dict[str, str] = {}
    token = os.environ.get("AURA_API_TOKEN")
    if token:
        sessions = lk.build_sessions(entries)
        by_cost: dict[str, float] = defaultdict(float)
        for s in sessions.values():
            if s.user_id:
                by_cost[s.user_id] += s.minutes * args.rate
        top_uids = [uid for uid, _ in sorted(by_cost.items(), key=lambda kv: -kv[1])[:args.top]]
        for uid in top_uids:
            tier = fetch_tier(uid, token)
            if tier:
                tiers[uid] = tier
        print(f"# tier: annotated {len(tiers)}/{len(top_uids)} top user(s) via Aura API", file=sys.stderr)
    else:
        print("# tier: AURA_API_TOKEN not set — skipping subscription-tier annotation", file=sys.stderr)

    out, payload = render(entries, label, args.rate, args.budget, args.top, tiers, window_days)
    print(out)

    if args.json_path:
        with open(args.json_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        print(f"\n# wrote JSON payload → {args.json_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
