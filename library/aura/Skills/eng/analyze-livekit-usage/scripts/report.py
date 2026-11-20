#!/usr/bin/env python3
"""
analyze-livekit-usage CLI entry point.

Ad-hoc usage deep-dive for the Aurie LiveKit voice agent over a window: session count,
peak concurrent sessions, total/avg session minutes, per-user distribution, error and
dispatch-failure counts. Sourced entirely from the forwarded GCP logs
(logName projects/firebase-auratech16/logs/aurie-voice-agent) — see livekit_core.py.

Read-only. No writes anywhere. Aggregates by session/user; UIDs are truncated in all
markdown surfaces (full UID only in the optional --json payload).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import livekit_core as lk  # noqa: E402


# ---------------------------------------------------------------------------
# Arg parsing / window resolution
# ---------------------------------------------------------------------------


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="analyze livekit usage",
        description="Session / minutes / concurrency usage sweep for the Aurie LiveKit voice agent.",
    )
    p.add_argument("--window", default="yesterday",
                   choices=["yesterday", "today", "last-7d", "last-30d"],
                   help="Time window (default yesterday, full UTC day).")
    p.add_argument("--since", help="Custom window start YYYY-MM-DD (UTC). Overrides --window; pair with --until.")
    p.add_argument("--until", help="Custom window end YYYY-MM-DD (UTC, exclusive). Defaults to now.")
    p.add_argument("--project", help="Override the GCP project derived from the SA key.")
    p.add_argument("--top", type=int, default=10, help="Cap heavy-user rows in output (default 10).")
    p.add_argument("--max-entries", type=int, default=50000, help="Cap log entries fetched (default 50000).")
    p.add_argument("--json", dest="json_path", help="Also write the full payload (with untruncated UIDs) to this path.")
    return p.parse_args(argv)


def resolve_window(args: argparse.Namespace) -> tuple[str, str, str]:
    """Return (time_clause, label, iso_range) for the requested window."""
    if args.since:
        start = datetime.strptime(args.since, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        end = (
            datetime.strptime(args.until, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            if args.until else datetime.now(timezone.utc)
        )
        label = f"{args.since} → {args.until or 'now'} (UTC)"
        return lk.range_filter(start, end), label, f"{start.isoformat()}/{end.isoformat()}"
    if args.window == "yesterday":
        start, end, label = lk.utc_day_bounds(1)
        return lk.range_filter(start, end), label, f"{start.isoformat()}/{end.isoformat()}"
    if args.window == "today":
        start, end, label = lk.utc_day_bounds(0)
        return lk.range_filter(start, end), label, f"{start.isoformat()}/{end.isoformat()}"
    days = {"last-7d": 7, "last-30d": 30}[args.window]
    window = lk.timedelta(days=days)
    return lk.since_filter(window), f"last {days}d", f"since-{days}d"


# ---------------------------------------------------------------------------
# Dispatch-failure heuristic
# ---------------------------------------------------------------------------

# Messages that signal the agent failed to take/join a job or connect a room. Kept as
# substrings (case-insensitive) so wording tweaks don't silently drop the signal.
_DISPATCH_FAILURE_MARKERS = (
    "failed to connect",
    "room connect failed",
    "failed to join",
    "dispatch failed",
    "job failed",
    "worker failed",
    "metadata parse failed",
    "tts_init_failed",
    "stt_init_failed",
)


def count_dispatch_failures(entries: list[dict]) -> int:
    n = 0
    for e in entries:
        msg = (lk.payload(e).get("message") or "").lower()
        if any(marker in msg for marker in _DISPATCH_FAILURE_MARKERS):
            n += 1
    return n


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def render(entries: list[dict], label: str, top: int) -> tuple[str, dict]:
    sessions = lk.build_sessions(entries)
    slist = list(sessions.values())
    n_sessions = len(slist)
    total_min = sum(s.minutes for s in slist)
    avg_min = total_min / n_sessions if n_sessions else 0.0
    total_turns = sum(s.turn_count for s in slist)
    peak = lk.peak_concurrency(slist)
    err_lines = sum(1 for e in entries if lk.is_error(e))
    dispatch_fails = count_dispatch_failures(entries)
    sessions_with_errors = sum(1 for s in slist if s.error_count)
    versions = sorted({v for s in slist for v in s.versions})

    # Per-user rollup (heavy users by minutes).
    by_user: dict[str, dict] = defaultdict(lambda: {"sessions": 0, "minutes": 0.0, "turns": 0})
    for s in slist:
        u = s.user_id or "(no-user)"
        by_user[u]["sessions"] += 1
        by_user[u]["minutes"] += s.minutes
        by_user[u]["turns"] += s.turn_count
    ranked_users = sorted(by_user.items(), key=lambda kv: -kv[1]["minutes"])

    lines = [
        f"LiveKit usage — {label}",
        "",
        f"- Sessions:            {n_sessions}",
        f"- Peak concurrent:     {peak}",
        f"- Total minutes:       {total_min:.1f}",
        f"- Avg session length:  {avg_min:.1f} min",
        f"- Total user turns:    {total_turns}",
        f"- Error log lines:     {err_lines}  ({sessions_with_errors} session(s) affected)",
        f"- Dispatch failures:   {dispatch_fails}",
        f"- Agent version(s):    {', '.join(versions) or '?'}",
        "",
    ]

    if n_sessions:
        lines.append(f"Top {min(top, len(ranked_users))} users by minutes:")
        lines.append("")
        lines.append("| user | sessions | minutes | turns |")
        lines.append("|---|---:|---:|---:|")
        for uid, agg in ranked_users[:top]:
            lines.append(
                f"| {lk.short_uid(uid) if uid != '(no-user)' else '(no-user)'} "
                f"| {agg['sessions']} | {agg['minutes']:.1f} | {agg['turns']} |"
            )
    else:
        lines.append("No sessions in this window. If unexpected, widen --window or check the agent is live "
                     "(`lk agent status`).")

    payload = {
        "window": label,
        "sessions": n_sessions,
        "peak_concurrent": peak,
        "total_minutes": round(total_min, 2),
        "avg_session_minutes": round(avg_min, 2),
        "total_turns": total_turns,
        "error_lines": err_lines,
        "sessions_with_errors": sessions_with_errors,
        "dispatch_failures": dispatch_fails,
        "versions": versions,
        "by_user": [
            {"user_id": uid, **agg} for uid, agg in ranked_users  # full UID in JSON only
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
        time_clause, label, _iso = resolve_window(args)
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

    out, payload = render(entries, label, args.top)
    print(out)

    if args.json_path:
        with open(args.json_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        print(f"\n# wrote JSON payload → {args.json_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
