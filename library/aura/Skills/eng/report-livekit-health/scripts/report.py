#!/usr/bin/env python3
"""
report-livekit-health CLI entry point.

One-pane DAILY snapshot of Aurie voice-agent health for standup / go-live watch. Combines
two independent sources into a single Slack-pasteable report:

  1. WORKER HEALTH  — live deployed-agent status from the `lk` CLI (LiveKit Cloud): Status,
     CPU, Mem, Replicas (running/target/max), Version, Region, Deployed At. Degrades
     gracefully if `lk` is not installed or not authed — the report still renders its
     log-derived section.
  2. LOG-DERIVED    — usage + errors from the forwarded GCP logs
     (logName projects/firebase-auratech16/logs/aurie-voice-agent): sessions, peak
     concurrent, total/avg minutes, user turns, error lines, sessions-with-errors,
     dispatch failures, agent version(s) seen in logs, eou_delay_ms / llm_ttft_ms P95,
     the STT-gate suppression rate (finals dropped as low-confidence noise, as a share of
     user turns) and the effective noise guards (noise_cancellation, min_interruption_words)
     the sessions ran with.

A 7-day delta (window vs the prior-7-day daily average of sessions + minutes) is printed
when there's enough history.

Default window: yesterday (UTC). Also supports today / last-7d and custom --since/--until.

Read-only. No writes anywhere. UIDs are not surfaced here (health snapshot is aggregate);
full detail lives in the --json payload. stderr carries `# auth` / `# query` / `# fetched N`
progress lines. Sibling of analyze-livekit-usage (usage deep-dive) — this is the daily
health snapshot framed for the morning "is the voice agent healthy?" question.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import livekit_core as lk  # noqa: E402


# ---------------------------------------------------------------------------
# Arg parsing / window resolution
# ---------------------------------------------------------------------------


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="report livekit health",
        description="Daily worker-health + log-derived health snapshot for the Aurie LiveKit voice agent.",
    )
    p.add_argument("--window", default="yesterday",
                   choices=["yesterday", "today", "last-7d"],
                   help="Time window (default yesterday, full UTC day).")
    p.add_argument("--since", help="Custom window start YYYY-MM-DD (UTC). Overrides --window; pair with --until.")
    p.add_argument("--until", help="Custom window end YYYY-MM-DD (UTC, exclusive). Defaults to now.")
    p.add_argument("--project", help="Override the GCP project derived from the SA key.")
    p.add_argument("--agent-id", help="LiveKit agent ID for worker health (default: auto-detect the aurie agent).")
    p.add_argument("--no-worker-health", action="store_true",
                   help="Skip the `lk` CLI worker-health probe (log-derived section only).")
    p.add_argument("--no-delta", action="store_true", help="Skip the 7-day delta comparison.")
    p.add_argument("--max-entries", type=int, default=50000, help="Cap log entries fetched (default 50000).")
    p.add_argument("--json", dest="json_path", help="Also write the full payload to this path.")
    return p.parse_args(argv)


def resolve_window(args: argparse.Namespace) -> tuple[str, str, tuple[datetime, datetime]]:
    """Return (time_clause, label, (start, end)) for the requested window.

    (start, end) are UTC datetimes used by the 7-day delta baseline. For rolling
    windows the pair spans [now-window, now].
    """
    if args.since:
        start = datetime.strptime(args.since, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        end = (
            datetime.strptime(args.until, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            if args.until else datetime.now(timezone.utc)
        )
        label = f"{args.since} → {args.until or 'now'} (UTC)"
        return lk.range_filter(start, end), label, (start, end)
    if args.window == "yesterday":
        start, end, label = lk.utc_day_bounds(1)
        return lk.range_filter(start, end), label, (start, end)
    if args.window == "today":
        start, end, label = lk.utc_day_bounds(0)
        return lk.range_filter(start, end), label, (start, end)
    # last-7d
    window = lk.timedelta(days=7)
    end = datetime.now(timezone.utc)
    start = end - window
    return lk.since_filter(window), "last 7d", (start, end)


# ---------------------------------------------------------------------------
# Worker health via the `lk` CLI
# ---------------------------------------------------------------------------

# The forwarded log stream's Dispatch Name is the deployed agent's dispatch name, so we
# match the aurie agent by dispatch name == SERVICE_NAME rather than a hard-coded ID
# (agent IDs rotate on redeploy).
_AGENT_DISPATCH_NAME = lk.SERVICE_NAME  # "aurie-voice-agent"

# Box-drawing verticals used by the `lk` table renderer. Split on any of them, not awk -F,
# so a renderer tweak doesn't silently drop a column.
_BOX_VERTICAL_RE = re.compile(r"[│┃｜|]")


def _run_lk(cli_args: list[str], timeout: int = 25) -> tuple[int, str, str]:
    """Run an `lk` subcommand. Returns (returncode, stdout, stderr). 127 if lk is absent."""
    lk_bin = shutil.which("lk")
    if not lk_bin:
        return 127, "", "lk CLI not found on PATH"
    try:
        proc = subprocess.run(
            [lk_bin, *cli_args],
            capture_output=True, text=True, timeout=timeout,
        )
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        return 124, "", f"lk {' '.join(cli_args)} timed out after {timeout}s"
    except OSError as e:
        return 126, "", f"failed to exec lk: {e}"


def _parse_table_rows(text: str) -> list[list[str]]:
    """Parse an `lk` box-drawing table into a list of cell-lists (data rows only).

    Header + separator rows are dropped; only rows whose split yields >= 2 non-empty cells
    AND that aren't pure box-border lines are kept. Defensive against renderer noise
    (the "Using agent [...]" preamble, spinner residue) by requiring an interior vertical.
    """
    rows: list[list[str]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or _BOX_VERTICAL_RE.search(line) is None:
            continue
        # Border/separator rows are made entirely of box chars — skip them.
        if re.fullmatch(r"[\s─-╿|]+", line):
            continue
        cells = [c.strip() for c in _BOX_VERTICAL_RE.split(line)]
        cells = [c for c in cells if c != ""]
        if len(cells) >= 2:
            rows.append(cells)
    return rows


def _find_aurie_agent_id(explicit: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """Resolve the aurie agent ID. Returns (agent_id, error). explicit wins if given."""
    if explicit:
        return explicit, None
    rc, out, err = _run_lk(["agent", "list"])
    if rc != 0:
        return None, (err.strip() or f"`lk agent list` exited {rc}")
    rows = _parse_table_rows(out)
    if not rows:
        return None, "no agents returned by `lk agent list`"
    # First cell is ID, second is Dispatch Name. Header row (ID/Dispatch Name/…) is a data
    # row here since it contains no box border — skip any row whose first cell isn't an ID.
    for cells in rows:
        agent_id = cells[0]
        dispatch = cells[1] if len(cells) > 1 else ""
        if dispatch == _AGENT_DISPATCH_NAME:
            return agent_id, None
    # Fall back to the first CA_-prefixed id if the dispatch name didn't match.
    for cells in rows:
        if cells[0].startswith("CA_"):
            return cells[0], None
    return None, f"no agent with dispatch name {_AGENT_DISPATCH_NAME!r} found"


def get_worker_health(explicit_id: Optional[str]) -> dict:
    """Probe deployed-agent status via `lk agent status`. Never raises.

    Returns a dict with either the parsed status fields or {"available": False, "reason": ...}.
    """
    if not shutil.which("lk"):
        return {"available": False, "reason": "lk CLI not installed"}

    agent_id, err = _find_aurie_agent_id(explicit_id)
    if not agent_id:
        return {"available": False, "reason": f"could not resolve agent ({err})"}

    rc, out, serr = _run_lk(["agent", "status", "--id", agent_id])
    if rc != 0:
        reason = serr.strip() or f"`lk agent status` exited {rc}"
        # Common non-auth failure is "not configured" — surface the raw hint.
        return {"available": False, "reason": reason, "agent_id": agent_id}

    rows = _parse_table_rows(out)
    # Expected header order (from `lk` v2.16): ID, Version, Region, Status, CPU, Mem, Replicas, Deployed At.
    # Locate the header row to map columns positionally rather than assuming a fixed layout.
    header: Optional[list[str]] = None
    data: Optional[list[str]] = None
    for cells in rows:
        if any(c.lower() == "status" for c in cells) and any(c.lower() == "version" for c in cells):
            header = [c.lower() for c in cells]
            continue
        if cells[0].startswith("CA_"):
            data = cells
    if not data:
        return {"available": False, "reason": "could not parse `lk agent status` table", "agent_id": agent_id}

    def col(name: str, default: str = "?") -> str:
        if header and name in header:
            idx = header.index(name)
            if idx < len(data):
                return data[idx]
        return default

    # Replicas render as "running / target / max" (e.g. "1 / 1 / 8").
    replicas = col("replicas")
    running = target = maximum = None
    m = re.findall(r"\d+", replicas)
    if len(m) == 3:
        running, target, maximum = m

    return {
        "available": True,
        "agent_id": data[0],
        "version": col("version"),
        "region": col("region"),
        "status": col("status"),
        "cpu": col("cpu"),
        "mem": col("mem"),
        "replicas": replicas,
        "replicas_running": running,
        "replicas_target": target,
        "replicas_max": maximum,
        "deployed_at": col("deployed at"),
    }


# ---------------------------------------------------------------------------
# Dispatch-failure heuristic (same markers as analyze-livekit-usage)
# ---------------------------------------------------------------------------

# Messages that signal the agent failed to take/join a job or connect a room. Substrings,
# case-insensitive, so wording tweaks don't silently drop the signal.
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


# ---------------------------------------------------------------------------
# Server-side row selection
# ---------------------------------------------------------------------------

# The report reads only the rows it counts, never the whole stream. A busy day logs well over
# 100k rows, almost all of them per-turn detail this report never looks at; the selected rows
# grow with sessions and turns instead, so a full day stays far under the --max-entries cap.
#
# Session span comes from the first and last of a room's selected rows: `Session starting` and
# `Session config resolved` open it, `Session ended; reporting conversation metrics` closes it.
_SESSION_START_CLAUSES = (
    'jsonPayload.message:"Session starting"',
    'jsonPayload.message="Session config resolved"',
)
_SESSION_END_CLAUSE = 'jsonPayload.message="Session ended; reporting conversation metrics"'
_REPORTED_LATENCY_METRICS = ("eou_delay_ms", "llm_ttft_ms")


def _or(clauses) -> str:
    return "(" + " OR ".join(clauses) + ")"


def session_span_clause() -> str:
    """Rows that open and close a session: all the 7-day delta needs for sessions and minutes."""
    return _or((*_SESSION_START_CLAUSES, _SESSION_END_CLAUSE))


def session_span_rows(entries: list[dict]) -> list[dict]:
    """The window entries `session_span_clause()` would select, client-side.

    The 7-day delta builds both sides from these rows, so a session with no end row spans the
    same way in the window as in the baseline.
    """
    out = []
    for e in entries:
        msg = lk.payload(e).get("message") or ""
        if (
            msg.startswith("Session starting")
            or msg == "Session config resolved"
            or msg == "Session ended; reporting conversation metrics"
        ):
            out.append(e)
    return out


def window_rows_clause() -> str:
    """Every row the window's report counts, as one Cloud Logging OR clause.

    Each rendered metric has its source row listed here. A metric added to `render` without
    its row added here reads as zero.
    """
    metrics = " OR ".join(f'"{m}"' for m in _REPORTED_LATENCY_METRICS)
    clauses = [
        *_SESSION_START_CLAUSES,
        _SESSION_END_CLAUSE,
        f'(jsonPayload.message="Turn latency" AND jsonPayload.metric=({metrics}))',
        f'jsonPayload.message:"{_STT_GATE_MESSAGE}"',
        f'jsonPayload.message:"{_TURN_CONFIG_MESSAGE}"',
        "severity>=ERROR",
        'jsonPayload.level=("error" OR "crit" OR "critical")',
        *(f'jsonPayload.message:"{m}"' for m in _DISPATCH_FAILURE_MARKERS),
    ]
    return _or(clauses)


def count_dispatch_failures(entries: list[dict]) -> int:
    n = 0
    for e in entries:
        msg = (lk.payload(e).get("message") or "").lower()
        if any(marker in msg for marker in _DISPATCH_FAILURE_MARKERS):
            n += 1
    return n


# ---------------------------------------------------------------------------
# STT gate + noise guards (read off the same window entries — no extra query)
# ---------------------------------------------------------------------------

# The agent drops a short final transcript whose STT confidence is under the configured floor
# before it can become a caption or a user turn, and logs each drop as shape only (word_count,
# confidence, floor — never text). Its share of finals is the one number that says whether the
# floor is eating real quiet answers: measured at ~2.2% over 2026-08-25..09-15 (25,102 user turns
# plus all 237 suppressed finals; 2.16% in the Inference era alone, n=6,430 finals). Well above
# that means the floor is too high; near zero while phantom-turn reports continue means a
# mechanism it doesn't cover. The high bar is the daily mean plus three standard deviations
# (2.30% ± 1.02 over 11 days), so an ordinary noisy day doesn't flag. ENG-1295 has the
# derivation, including why a `User turn shape` estimate cannot be used for it.
_STT_GATE_MESSAGE = "suppressed low-confidence stt final"
STT_GATE_EXPECTED_PCT = 2.2
STT_GATE_HIGH_PCT = 5.0

# The per-session config row. noise_cancellation and min_interruption_words are the two guards
# between room noise and a user turn; a session whose row lacks them ran an agent that did not
# yet log them.
_TURN_CONFIG_MESSAGE = "turn handling configured"
_NOISE_GUARD_FIELDS = ("noise_cancellation", "min_interruption_words")


def _as_int_if_whole(value):
    """Cloud Logging returns JSON numbers as floats — render 1.0 as 1, leave the rest alone."""
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def stt_gate_stats(entries: list[dict], user_turns: int) -> dict:
    """Count suppressed finals and express them as a share of what the turns would have been.

    A suppressed final never becomes a turn, so the comparable denominator is the turns that
    happened plus the finals the gate removed — the base the expectation is measured against.
    """
    suppressed = 0
    confidences: list[float] = []
    floors: set = set()
    for e in entries:
        p = lk.payload(e)
        if (p.get("message") or "").lower() != _STT_GATE_MESSAGE:
            continue
        suppressed += 1
        if isinstance(p.get("confidence"), (int, float)):
            confidences.append(float(p["confidence"]))
        if p.get("floor") is not None:
            floors.add(p["floor"])
    base = user_turns + suppressed
    pct = (suppressed / base * 100.0) if base else None
    if pct is None:
        flag = None
    elif pct > STT_GATE_HIGH_PCT:
        flag = "high"
    else:
        flag = "ok"
    return {
        "suppressed": suppressed,
        "turns_plus_suppressed": base,
        "pct": None if pct is None else round(pct, 2),
        "expected_pct": STT_GATE_EXPECTED_PCT,
        "high_pct": STT_GATE_HIGH_PCT,
        "floor": sorted(floors)[0] if len(floors) == 1 else (sorted(floors) if floors else None),
        "median_confidence": round(sorted(confidences)[len(confidences) // 2], 3) if confidences else None,
        "flag": flag,
    }


def noise_guard_stats(entries: list[dict]) -> dict:
    """Tally the noise-guard values the window's sessions were configured with.

    One row per session. A single value per guard is the healthy shape; more than one in a
    window means a deploy or a secrets change landed mid-window, or a secret drifted. Rows
    without the fields come from an agent version that did not log them.
    """
    values: dict[str, dict] = {f: {} for f in _NOISE_GUARD_FIELDS}
    rows = 0
    rows_with_fields = 0
    for e in entries:
        p = lk.payload(e)
        if (p.get("message") or "").lower() != _TURN_CONFIG_MESSAGE:
            continue
        rows += 1
        if not any(f in p for f in _NOISE_GUARD_FIELDS):
            continue
        rows_with_fields += 1
        for f in _NOISE_GUARD_FIELDS:
            if f in p:
                key = str(_as_int_if_whole(p[f]))
                values[f][key] = values[f].get(key, 0) + 1
    return {
        "sessions": rows,
        "sessions_with_fields": rows_with_fields,
        "values": values,
        "drift": any(len(v) > 1 for v in values.values()),
    }


def _fmt_guard(counts: dict) -> str:
    if not counts:
        return "?"
    if len(counts) == 1:
        return next(iter(counts))
    return ", ".join(f"{k}×{n}" for k, n in sorted(counts.items(), key=lambda kv: -kv[1]))


# ---------------------------------------------------------------------------
# 7-day delta baseline
# ---------------------------------------------------------------------------


def compute_delta(auth, window_bounds: tuple[datetime, datetime], window_sessions: int,
                  window_minutes: float, max_entries: int) -> Optional[dict]:
    """Compare the window's per-day rate against the prior-7-day daily average.

    Baseline = the 7 full UTC days ending at the window start. window_sessions/window_minutes
    are normalized by the window's own span (in days) before comparing — otherwise a
    multi-day window (--window last-7d/last-30d) stacks N days of totals against the
    baseline's single-day average, inflating the delta. For a 1-day window (yesterday/today,
    the default) the normalization is a no-op. Returns None if there's not enough baseline
    data (no sessions) — the caller then skips the delta cleanly.
    """
    start, end = window_bounds
    window_days = max((end - start).total_seconds() / 86400.0, 1e-9)
    base_start = start - lk.timedelta(days=7)
    base_clause = lk.range_filter(base_start, start)
    filter_str = lk.base_filter(base_clause, session_span_clause())
    try:
        base_entries = list(lk.list_entries(auth, filter_str, max_entries=max_entries))
    except lk.TruncatedReadError:
        # A truncated BASELINE is worse than no baseline: the delta it produces is a
        # comparison against a window that was silently cut short, and it renders as a
        # normal figure. Decline to compute one.
        print(f"# baseline skipped: read truncated at {max_entries} entries", file=sys.stderr)
        return None
    except Exception:
        return None
    base_sessions = lk.build_sessions(base_entries)
    if not base_sessions:
        return None
    slist = list(base_sessions.values())
    # Daily averages over the 7-day baseline.
    daily_sessions = len(slist) / 7.0
    daily_minutes = sum(s.minutes for s in slist) / 7.0
    # Normalize the window's totals to a per-day rate so multi-day windows compare fairly
    # against the baseline's per-day average.
    window_daily_sessions = window_sessions / window_days
    window_daily_minutes = window_minutes / window_days

    def pct(cur: float, base: float) -> Optional[float]:
        if base <= 0:
            return None
        return (cur - base) / base * 100.0

    return {
        "baseline_days": 7,
        "baseline_daily_sessions": round(daily_sessions, 2),
        "baseline_daily_minutes": round(daily_minutes, 2),
        "window_daily_sessions": round(window_daily_sessions, 2),
        "window_daily_minutes": round(window_daily_minutes, 2),
        "sessions_delta_pct": pct(window_daily_sessions, daily_sessions),
        "minutes_delta_pct": pct(window_daily_minutes, daily_minutes),
    }


def _arrow(pct: Optional[float]) -> str:
    if pct is None:
        return "—"
    sign = "+" if pct >= 0 else ""
    arrow = "↑" if pct > 2 else ("↓" if pct < -2 else "→")
    return f"{arrow} {sign}{pct:.0f}%"


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def render(worker: dict, entries: list[dict], label: str, delta: Optional[dict]) -> tuple[str, dict]:
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

    eou = lk.percentiles(lk.latency_values(entries, "eou_delay_ms"))
    ttft = lk.percentiles(lk.latency_values(entries, "llm_ttft_ms"))
    gate = stt_gate_stats(entries, total_turns)
    guards = noise_guard_stats(entries)

    # --- Headline ---------------------------------------------------------
    if worker.get("available"):
        wstatus = worker.get("status", "?")
        badge = "✅" if wstatus.lower() == "running" else "⚠️"
        worker_head = f"Worker {wstatus} {badge} ({worker.get('replicas', '?')} replicas, {worker.get('version', '?')})"
    else:
        worker_head = "Worker health unavailable ⚠️"
    healthy = (
        worker.get("available") and worker.get("status", "").lower() == "running"
        and dispatch_fails == 0
    )
    verdict = "healthy" if healthy else ("degraded" if worker.get("available") else "log-only")
    headline = (
        f"Aurie voice health — {label}: {worker_head} | "
        f"{n_sessions} sessions, peak {peak}, {total_min:.0f} min | "
        f"{err_lines} err lines, {dispatch_fails} dispatch fails [{verdict}]"
    )

    lines = [headline, ""]

    # --- Worker health block ---------------------------------------------
    lines.append("Worker health (lk CLI):")
    if worker.get("available"):
        lines.extend([
            f"- Status:       {worker.get('status', '?')}",
            f"- Version:      {worker.get('version', '?')}",
            f"- Region:       {worker.get('region', '?')}",
            f"- Replicas:     {worker.get('replicas', '?')}  (running/target/max)",
            f"- CPU / Mem:    {worker.get('cpu', '?')}  /  {worker.get('mem', '?')}",
            f"- Deployed at:  {worker.get('deployed_at', '?')}",
        ])
    else:
        lines.append(f"- worker health unavailable ({worker.get('reason', 'lk not configured')})")
    lines.append("")

    # --- Log-derived block -----------------------------------------------
    lines.append("Log-derived (GCP forwarded logs):")
    lines.extend([
        f"- Sessions:            {n_sessions}",
        f"- Peak concurrent:     {peak}",
        f"- Total minutes:       {total_min:.1f}",
        f"- Avg session length:  {avg_min:.1f} min",
        f"- Total user turns:    {total_turns}",
        f"- Error log lines:     {err_lines}  ({sessions_with_errors} session(s) affected)",
        f"- Dispatch failures:   {dispatch_fails}",
        f"- Agent version(s):    {', '.join(versions) or '?'}",
        f"- eou_delay_ms P95:    {eou['p95']:.0f} ms  (n={int(eou['count'])})",
        f"- llm_ttft_ms P95:     {ttft['p95']:.0f} ms  (n={int(ttft['count'])})",
    ])
    if gate["pct"] is None:
        gate_line = f"- STT gate:            {gate['suppressed']} suppressed finals (no turns in window)"
    else:
        marker = " ⚠️ high — floor may be eating real turns" if gate["flag"] == "high" else ""
        floor = f", floor {gate['floor']}" if gate["floor"] is not None else ""
        gate_line = (
            f"- STT gate:            {gate['suppressed']} suppressed finals = {gate['pct']:.2f}% of turns "
            f"(expect ~{STT_GATE_EXPECTED_PCT}%{floor}){marker}"
        )
    lines.append(gate_line)
    if guards["sessions_with_fields"]:
        nc = _fmt_guard(guards["values"]["noise_cancellation"])
        mw = _fmt_guard(guards["values"]["min_interruption_words"])
        drift = " ⚠️ more than one value in window" if guards["drift"] else ""
        lines.append(
            f"- Noise guards:        noise_cancellation={nc}, min_interruption_words={mw} "
            f"(n={guards['sessions_with_fields']} sessions){drift}"
        )
    elif guards["sessions"]:
        lines.append("- Noise guards:        not logged by this agent version")
    lines.append("")

    # --- 7-day delta ------------------------------------------------------
    if delta:
        lines.append("7-day delta (vs prior 7-day daily avg):")
        lines.append(
            f"- Sessions:  {delta['window_daily_sessions']:.1f}/day vs "
            f"{delta['baseline_daily_sessions']:.1f}/day  {_arrow(delta['sessions_delta_pct'])}"
        )
        lines.append(
            f"- Minutes:   {delta['window_daily_minutes']:.0f}/day vs "
            f"{delta['baseline_daily_minutes']:.0f}/day  {_arrow(delta['minutes_delta_pct'])}"
        )
        lines.append("")

    if not n_sessions:
        lines.append("No sessions in this window. If unexpected, widen --window or check the worker is live "
                     "(worker-health block above / `lk agent status`).")

    payload = {
        "window": label,
        "worker_health": worker,
        "sessions": n_sessions,
        "peak_concurrent": peak,
        "total_minutes": round(total_min, 2),
        "avg_session_minutes": round(avg_min, 2),
        "total_turns": total_turns,
        "error_lines": err_lines,
        "sessions_with_errors": sessions_with_errors,
        "dispatch_failures": dispatch_fails,
        "versions": versions,
        "latency": {
            "eou_delay_ms": {"p95": round(eou["p95"], 1), "count": int(eou["count"])},
            "llm_ttft_ms": {"p95": round(ttft["p95"], 1), "count": int(ttft["count"])},
        },
        "stt_gate": gate,
        "noise_guards": guards,
        "delta_7d": delta,
        "verdict": verdict,
    }
    return "\n".join(lines).rstrip(), payload


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: Optional[list[str]] = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = parse_args(argv)

    try:
        time_clause, label, window_bounds = resolve_window(args)
    except ValueError as e:
        print(f"INPUT ERROR: {e}", file=sys.stderr)
        return 2

    # Worker health first — it's independent of GCP and degrades gracefully.
    if args.no_worker_health:
        worker = {"available": False, "reason": "skipped (--no-worker-health)"}
    else:
        worker = get_worker_health(args.agent_id)
    if worker.get("available"):
        print(f"# worker: status={worker.get('status')} replicas={worker.get('replicas')} "
              f"version={worker.get('version')}", file=sys.stderr)
    else:
        print(f"# worker health unavailable: {worker.get('reason')}", file=sys.stderr)

    try:
        auth = lk.get_client(project_override=args.project)
    except lk.AuthError as e:
        print(f"AUTH ERROR: {e}", file=sys.stderr)
        return 3
    print(f"# auth: source={auth.source} project={auth.project} principal={auth.principal}", file=sys.stderr)

    filter_str = lk.base_filter(time_clause, window_rows_clause())
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

    # The delta compares like with like: the window side uses the same start/end rows the baseline reads.
    delta = None
    if not args.no_delta:
        sessions = lk.build_sessions(session_span_rows(entries))
        slist = list(sessions.values())
        delta = compute_delta(
            auth, window_bounds, len(slist), sum(s.minutes for s in slist), args.max_entries
        )
        if delta:
            print("# fetched 7-day baseline for delta", file=sys.stderr)

    out, payload = render(worker, entries, label, delta)
    print(out)

    if args.json_path:
        with open(args.json_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        print(f"\n# wrote JSON payload → {args.json_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
