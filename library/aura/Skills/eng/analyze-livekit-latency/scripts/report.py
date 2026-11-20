#!/usr/bin/env python3
"""
analyze-livekit-latency CLI entry point.

Turn-latency deep-dive for the Aurie LiveKit voice agent over a window: P50/P90/P95/P99/Max/mean
for each per-turn latency metric the agent emits. Sourced entirely from the forwarded GCP logs
(logName projects/firebase-auratech16/logs/aurie-voice-agent) — see livekit_core.py.

The agent emits five per-turn latency metrics (livekit_core.LATENCY_METRICS):
  - e2e_latency_ms — end-to-end felt latency: time from the user finishing speaking until the
                     agent begins responding. The single number a user actually experiences.
  - eou_delay_ms  — end-of-utterance delay: time from the user stopping speaking until the agent
                    decides the turn ended. Values near ~3000ms are the configured max-silence
                    fallback firing (the agent waited out the silence timeout), not model slowness.
  - transcription_delay_ms — STT lag: time to obtain the transcript after the user stops speaking.
  - llm_ttft_ms   — LLM time-to-first-token: how fast the agent starts generating its reply.
  - tts_ttfb_ms   — TTS time-to-first-byte: how fast the first audio chunk comes back once text
                    is flowing (a component of e2e_latency, not additive with the others).

A secondary per-session view reads the once-per-session "Turn-latency summary" line (emitted on
shutdown) for the agent's own averages, when present.

Read-only. No writes anywhere. Percentiles are computed client-side over per-turn values. Aggregates
by metric; no UIDs, transcripts, or free-text content are surfaced.
"""

from __future__ import annotations

import argparse
import json
import os
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
        prog="analyze livekit latency",
        description="Per-turn latency percentiles (e2e_latency_ms, eou_delay_ms, "
        "transcription_delay_ms, llm_ttft_ms, tts_ttfb_ms) for the Aurie LiveKit voice agent.",
    )
    p.add_argument("--window", default="yesterday",
                   choices=["yesterday", "today", "last-7d", "last-30d"],
                   help="Time window (default yesterday, full UTC day).")
    p.add_argument("--since", help="Custom window start YYYY-MM-DD (UTC). Overrides --window; pair with --until.")
    p.add_argument("--until", help="Custom window end YYYY-MM-DD (UTC, exclusive). Defaults to now.")
    p.add_argument("--project", help="Override the GCP project derived from the SA key.")
    p.add_argument("--max-entries", type=int, default=50000, help="Cap log entries fetched (default 50000).")
    p.add_argument("--json", dest="json_path", help="Also write the full percentile payload to this path.")
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
# Per-session summary view ("Turn-latency summary" line, emitted on shutdown)
# ---------------------------------------------------------------------------


def session_summaries(entries: list[dict]) -> list[dict]:
    """Pull the once-per-session "Turn-latency summary" lines the agent logs on shutdown.

    Each carries the agent's OWN per-session averages (eou_delay_avg_ms / llm_ttft_avg_ms and their
    *_count) — a cheap secondary view that doesn't require re-deriving percentiles. method=="shutdown"
    guards against any non-shutdown emission of the same message.
    """
    out: list[dict] = []
    for e in entries:
        p = lk.payload(e)
        if p.get("message") == "Turn-latency summary" and p.get("method") == "shutdown":
            out.append(p)
    return out


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _fmt(v: float) -> str:
    return f"{v:,.0f}"


def render(entries: list[dict], label: str) -> tuple[str, dict]:
    metrics = {m: lk.percentiles(lk.latency_values(entries, m)) for m in lk.LATENCY_METRICS}
    total_turns = int(metrics.get("eou_delay_ms", {}).get("count", 0.0))

    # Headline anchors on the two user-facing signals: how long the agent waits to end a turn (eou)
    # and how fast it starts replying (llm ttft), both at P95.
    eou = metrics.get("eou_delay_ms", {})
    ttft = metrics.get("llm_ttft_ms", {})
    lines = [
        f"LiveKit turn latency — {label}",
        "",
        f"- Turns measured:  {total_turns}",
        f"- EOU delay P95:   {_fmt(eou.get('p95', 0.0))} ms",
        f"- LLM TTFT P95:    {_fmt(ttft.get('p95', 0.0))} ms",
        "",
    ]

    if total_turns:
        lines.append("| metric | p50 | p90 | p95 | p99 | max | mean | n |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|---:|")
        for m in lk.LATENCY_METRICS:
            s = metrics[m]
            lines.append(
                f"| {m} | {_fmt(s['p50'])} | {_fmt(s['p90'])} | {_fmt(s['p95'])} | {_fmt(s['p99'])} "
                f"| {_fmt(s['max'])} | {_fmt(s['mean'])} | {int(s['count'])} |"
            )
        lines.append("")
        lines.append("_All values in ms. Percentiles computed client-side (nearest-rank) over per-turn samples._")
    else:
        lines.append("No turn-latency lines in this window. If unexpected, widen --window or check the agent "
                     "is emitting metrics (`lk agent status`).")

    # Secondary view: per-session averages the agent logs itself on shutdown.
    summaries = session_summaries(entries)
    if summaries:
        lines.append("")
        lines.append(f"Per-session averages (agent's own \"Turn-latency summary\", {len(summaries)} session(s)):")
        lines.append("")
        lines.append("| session eou_delay_avg_ms | eou n | llm_ttft_avg_ms | ttft n |")
        lines.append("|---:|---:|---:|---:|")
        for p in summaries:
            lines.append(
                f"| {_fmt(float(p.get('eou_delay_avg_ms') or 0))} | {int(p.get('eou_delay_count') or 0)} "
                f"| {_fmt(float(p.get('llm_ttft_avg_ms') or 0))} | {int(p.get('llm_ttft_count') or 0)} |"
            )

    payload = {
        "window": label,
        "turns_measured": total_turns,
        "metrics": {m: {k: round(v, 2) for k, v in metrics[m].items()} for m in lk.LATENCY_METRICS},
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

    out, payload = render(entries, label)
    print(out)

    if args.json_path:
        with open(args.json_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        print(f"\n# wrote JSON payload → {args.json_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
