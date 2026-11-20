#!/usr/bin/env python3
"""
check-livekit-health CLI entry point.

On-demand health / alert triage for the Aurie LiveKit voice agent. Runs a set of named
checks over a recent window and returns PASS / WARN / FAIL per check plus an overall
verdict, exiting non-zero when anything FAILs so it can later be wrapped in cron/CI.

This is NOT always-on alerting — it's the "is anything on fire right now?" go-live button
you run by hand (or from a scheduled wrapper). Persistent alerting belongs in GCP Monitoring
alert policies (a possible follow-up), not here.

Checks:
  1. error_rate       — error log lines / sessions over the window (N/A if 0 sessions).
  2. dispatch_failures — connect/join/init-failure lines (any failure FAILs by default).
  3. worker_health    — `lk agent status` (Running + replica headroom). SKIPs if lk unusable.
  4. cost_budget      — derived spend = session minutes × --rate; FAILs over --budget if set.

Log-derived checks (1, 2, 4) read the forwarded GCP logs
(logName projects/firebase-auratech16/logs/aurie-voice-agent) — see livekit_core.py.

Read-only. No writes anywhere (the JSON payload is the only optional output file).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import livekit_core as lk  # noqa: E402


# Status constants — ordered by severity so an overall verdict is just the worst check.
PASS, WARN, FAIL, SKIP, NA = "PASS", "WARN", "FAIL", "SKIP", "N/A"
_SEVERITY = {PASS: 0, NA: 0, SKIP: 0, WARN: 1, FAIL: 2}


# ---------------------------------------------------------------------------
# Arg parsing / window resolution
# ---------------------------------------------------------------------------


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="check livekit health",
        description="On-demand PASS/WARN/FAIL health check for the Aurie LiveKit voice agent.",
    )
    p.add_argument("--since", default="1h",
                   help="Rolling window like 30m, 1h, 6h, 24h (default 1h).")
    p.add_argument("--project", help="Override the GCP project derived from the SA key.")
    p.add_argument("--max-entries", type=int, default=50000,
                   help="Cap log entries fetched (default 50000).")
    # Per-check thresholds.
    p.add_argument("--max-error-rate", type=float, default=1.0,
                   help="error_rate FAILs above this (error lines / session). Default 1.0; WARN above half.")
    p.add_argument("--max-dispatch-failures", type=int, default=0,
                   help="dispatch_failures FAILs above this count. Default 0 → any failure FAILs.")
    p.add_argument("--rate", type=float, default=0.01,
                   help="Derived cost rate in USD per session minute (default 0.01).")
    p.add_argument("--budget", type=float,
                   help="cost_budget FAILs when the derived spend RATE (projected to a full day) "
                        "exceeds this daily USD ceiling. Omit → check SKIPs.")
    p.add_argument("--agent-id",
                   help="LiveKit agent ID for worker_health. Omit → resolved by dispatch name aurie-voice-agent.")
    p.add_argument("--json", dest="json_path", help="Also write the full verdict payload to this path.")
    return p.parse_args(argv)


# ---------------------------------------------------------------------------
# Check helpers
# ---------------------------------------------------------------------------


def _check(name: str, status: str, detail: str,
           value=None, threshold=None) -> dict:
    return {"name": name, "status": status, "detail": detail,
            "value": value, "threshold": threshold}


# Messages that signal the agent failed to take/join a job or connect a room. Kept as
# case-insensitive substrings so wording tweaks don't silently drop the signal. Mirrors
# the marker list in analyze-livekit-usage.
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


# Lines the LiveKit SDK logs at ERROR severity that are normal, recovered events — not agent
# failures. Excluded from error_rate so routine session teardown and STT idle-timeout reconnects
# don't trip a false WARN. Each entry is an all-of group of case-insensitive substrings that must
# co-occur in the message. The excluded count is always surfaced in the detail (never silently
# dropped). These originate in the SDK, so the agent can't downgrade them at the source.
_BENIGN_ERROR_MARKERS = (
    ("data channel", "closed unexpectedly"),        # rtc_engine data-channel teardown on any disconnect
    ("deepgram connection closed unexpectedly",),   # STT idle-timeout (1011, retryable) — plugin auto-reconnects
)


def _entry_message(entry: dict) -> str:
    """The entry's log message (jsonPayload.message, else textPayload), lowercased for matching."""
    return (lk.payload(entry).get("message") or entry.get("textPayload") or "").lower()


def _is_benign_error(entry: dict) -> bool:
    """True for SDK ERROR lines that are normal recovered events (see ``_BENIGN_ERROR_MARKERS``)."""
    msg = _entry_message(entry)
    return any(all(sub in msg for sub in group) for group in _BENIGN_ERROR_MARKERS)


def check_error_rate(entries: list[dict], n_sessions: int, max_rate: float) -> dict:
    errors = [e for e in entries if lk.is_error(e)]
    benign = sum(1 for e in errors if _is_benign_error(e))
    err_lines = len(errors) - benign
    benign_note = f", {benign} benign SDK line(s) excluded" if benign else ""
    if n_sessions == 0:
        # No sessions → rate is undefined. Report raw error lines but don't pass/fail on a
        # ratio we can't compute (a quiet window shouldn't read as healthy or broken).
        return _check("error_rate", NA,
                      f"no sessions in window ({err_lines} error line(s){benign_note})",
                      value=None, threshold=max_rate)
    rate = err_lines / n_sessions
    if rate > max_rate:
        status = FAIL
    elif rate > max_rate / 2:
        status = WARN
    else:
        status = PASS
    return _check("error_rate", status,
                  f"{rate:.2f} err/session ({err_lines} lines / {n_sessions} sessions{benign_note})",
                  value=round(rate, 4), threshold=max_rate)


def check_dispatch_failures(entries: list[dict], max_failures: int) -> dict:
    n = 0
    for e in entries:
        msg = _entry_message(e)
        if any(marker in msg for marker in _DISPATCH_FAILURE_MARKERS):
            n += 1
    status = FAIL if n > max_failures else PASS
    return _check("dispatch_failures", status,
                  f"{n} connect/join/init-failure line(s)",
                  value=n, threshold=max_failures)


# Matches a Replicas cell like "1 / 1 / 8" (current / desired / max) tolerant of spacing.
_REPLICAS_RE = re.compile(r"(\d+)\s*/\s*(\d+)\s*/\s*(\d+)")
_AGENT_DISPATCH_NAME = lk.SERVICE_NAME  # "aurie-voice-agent"


def _run_lk(args: list[str]) -> tuple[int, str]:
    """Run an `lk` subcommand, returning (returncode, combined stdout+stderr).

    Merges stderr into stdout because `lk` prints its spinner + the harmless "nvm is not
    compatible" shell noise there; we parse the box-drawing table out of the combined text.
    """
    try:
        proc = subprocess.run(
            ["lk", *args],
            capture_output=True, text=True, timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        raise RuntimeError(str(e))
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def _resolve_agent_id() -> Optional[str]:
    """Find the aurie-voice-agent's ID via `lk agent list` (matched on Dispatch Name)."""
    rc, out = _run_lk(["agent", "list"])
    if rc != 0:
        return None
    for line in out.splitlines():
        if _AGENT_DISPATCH_NAME in line and "│" in line:
            cells = [c.strip() for c in line.split("│")]
            # First non-empty cell is the ID (CA_...); guard against header rows.
            for c in cells:
                if c.startswith("CA_"):
                    return c
    return None


def check_worker_health(agent_id_override: Optional[str]) -> dict:
    """Query `lk agent status` and assert the worker is Running with replica headroom.

    Degrades gracefully to SKIP whenever lk is missing, unauthed, or its output can't be
    parsed — a health check must never crash on an unconfigured CLI.
    """
    if shutil.which("lk") is None:
        return _check("worker_health", SKIP, "lk CLI not found on PATH")

    # `lk agent status --help` has no --json flag today; if a future lk adds one we prefer it.
    supports_json = False
    try:
        _rc, help_out = _run_lk(["agent", "status", "--help"])
        supports_json = "--json" in help_out
    except RuntimeError:
        supports_json = False

    agent_id = agent_id_override or _resolve_agent_id()
    if not agent_id:
        return _check("worker_health", SKIP,
                      "lk not configured (no agent ID resolvable — unauthed or wrong project)")

    status_args = ["agent", "status", "--id", agent_id]
    if supports_json:
        status_args.append("--json")
    try:
        rc, out = _run_lk(status_args)
    except RuntimeError as e:
        return _check("worker_health", SKIP, f"lk agent status failed to run ({e})")
    if rc != 0:
        return _check("worker_health", SKIP, "lk agent status returned non-zero (likely unauthed)")

    # Preferred path: structured JSON, if a future lk emits it.
    if supports_json:
        try:
            data = json.loads(_extract_json(out))
            return _worker_from_json(data, agent_id)
        except (ValueError, KeyError):
            pass  # fall through to table parsing

    return _worker_from_table(out, agent_id)


def _extract_json(text: str) -> str:
    """Pull the first {...} or [...] block out of noisy CLI output."""
    start = min((i for i in (text.find("{"), text.find("[")) if i >= 0), default=-1)
    if start < 0:
        raise ValueError("no JSON found")
    return text[start:]


def _worker_from_json(data, agent_id: str) -> dict:
    """Interpret a hypothetical `lk agent status --json` payload defensively."""
    row = data[0] if isinstance(data, list) and data else data
    status = str(row.get("status", "")).strip()
    cur = row.get("replicas_current")
    desired = row.get("replicas_desired")
    return _judge_worker(status, cur, desired, agent_id)


def _worker_from_table(out: str, agent_id: str) -> dict:
    """Parse the plain box-drawing status table (the only format lk emits today)."""
    status = None
    replicas = None
    for line in out.splitlines():
        if "│" not in line:
            continue
        cells = [c.strip() for c in line.split("│")]
        if agent_id not in cells:
            continue
        # Data row for our agent. Status is the cell equal to a known state word; the
        # replicas cell matches "cur / desired / max".
        for c in cells:
            if c in ("Running", "Stopped", "Starting", "Failed", "Unknown", "Error"):
                status = c
            m = _REPLICAS_RE.search(c)
            if m:
                replicas = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
        break
    if status is None and replicas is None:
        return _check("worker_health", SKIP,
                      "could not parse lk agent status table (format may have changed)")
    cur = replicas[0] if replicas else None
    desired = replicas[1] if replicas else None
    return _judge_worker(status, cur, desired, agent_id)


def _judge_worker(status: Optional[str], cur, desired, agent_id: str) -> dict:
    """Shared PASS/WARN/FAIL logic for both JSON and table paths."""
    rep_str = f"{cur}/{desired}" if cur is not None and desired is not None else "?"
    detail_bits = [f"status={status or '?'}", f"replicas={rep_str}", f"id={agent_id}"]
    if status and status != "Running":
        return _check("worker_health", FAIL, ", ".join(detail_bits),
                      value=status, threshold="Running")
    # Running (or unknown-but-present) — WARN only if we're short of desired replicas.
    if cur is not None and desired is not None and cur < desired:
        return _check("worker_health", WARN,
                      ", ".join(detail_bits) + " (below desired)",
                      value=rep_str, threshold=f">={desired}")
    return _check("worker_health", PASS, ", ".join(detail_bits),
                  value=status or "Running", threshold="Running")


def check_cost_budget(total_minutes: float, rate: float, budget: Optional[float], window_days: float) -> dict:
    """FAIL when the window's derived spend, projected to a full day, exceeds --budget.

    --budget is a daily USD ceiling, but this check's window is typically short (--since
    defaults to 1h). Normalizing spend/window_days to a projected daily rate avoids two
    failure modes: comparing a sub-day window's raw spend against a full-day budget would
    never trip (under-warns), while comparing a multi-day window's raw total would
    false-trigger (over-warns) — the daily-rate projection is correct for both.
    """
    spend = total_minutes * rate
    daily_rate_spend = spend / window_days
    if budget is None:
        return _check("cost_budget", SKIP,
                      f"derived spend ${spend:.2f} (no --budget set)",
                      value=round(spend, 2), threshold=None)
    status = FAIL if daily_rate_spend > budget else PASS
    return _check("cost_budget", status,
                  f"derived spend rate ${daily_rate_spend:.2f}/day (budget ${budget:.2f}/day, "
                  f"{total_minutes:.1f} min × ${rate}/min over the window)",
                  value=round(daily_rate_spend, 2), threshold=budget)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def verdict_of(checks: list[dict]) -> str:
    worst = max((_SEVERITY[c["status"]] for c in checks), default=0)
    return {0: PASS, 1: WARN, 2: FAIL}[worst]


def render(checks: list[dict], label: str) -> tuple[str, str]:
    verdict = verdict_of(checks)
    lines = [f"LiveKit health — {label}", ""]
    for c in checks:
        thr = f" (threshold {c['threshold']})" if c["threshold"] is not None else ""
        lines.append(f"[{c['status']}] {c['name']}: {c['detail']}{thr}")
    lines.append("")
    lines.append(f"VERDICT: {verdict}")
    return "\n".join(lines), verdict


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: Optional[list[str]] = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = parse_args(argv)

    try:
        window, label = lk.parse_since(args.since)
    except ValueError as e:
        print(f"INPUT ERROR: {e}", file=sys.stderr)
        return 2
    window_days = max(window.total_seconds() / 86400.0, 1e-9)

    try:
        auth = lk.get_client(project_override=args.project)
    except lk.AuthError as e:
        print(f"AUTH ERROR: {e}", file=sys.stderr)
        return 3
    print(f"# auth: source={auth.source} project={auth.project} principal={auth.principal}", file=sys.stderr)

    filter_str = lk.base_filter(lk.since_filter(window))
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

    sessions = lk.build_sessions(entries)
    slist = list(sessions.values())
    n_sessions = len(slist)
    total_minutes = sum(s.minutes for s in slist)

    checks = [
        check_error_rate(entries, n_sessions, args.max_error_rate),
        check_dispatch_failures(entries, args.max_dispatch_failures),
        check_worker_health(args.agent_id),
        check_cost_budget(total_minutes, args.rate, args.budget, window_days),
    ]

    out, verdict = render(checks, f"last {label}")
    print(out)

    if args.json_path:
        payload = {"window": f"last {label}", "checks": checks, "verdict": verdict}
        with open(args.json_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        print(f"\n# wrote JSON payload → {args.json_path}", file=sys.stderr)

    # Exit non-zero only on a hard FAIL so this is cron/CI-usable; WARN stays exit 0.
    return 1 if verdict == FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
