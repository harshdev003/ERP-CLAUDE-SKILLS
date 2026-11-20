#!/usr/bin/env python3
"""
investigate-gcp-logs CLI entry point.

Three modes:
  Triage:  --severity {ERROR|WARNING|...} --since <duration>
  Focus:   --message "..." [--container <name>] [--namespace <name>] --since <duration>
  Trace:   --transaction <uuid>

Plus link-follow:
  --url "<cloudlogging.app.goo.gl/... or console.cloud.google.com/logs/query;...>"

Defaults: cluster=aura-prod-cluster-a, severity=WARNING, since=1h.
Trace mode ignores --severity (you want INFO/DEBUG context too).
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from typing import Optional

# Skill-local imports — these scripts live alongside this file
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import format as fmt  # noqa: E402  (skill-local module)
from gcp import (  # noqa: E402
    AuthError,
    decode_logs_explorer_url,
    get_client,
    iso_duration_to_seconds,
    list_entries,
)
from normalize import normalize  # noqa: E402

DEFAULT_CLUSTER = "aura-prod-cluster-a"
DEFAULT_SEVERITY = "WARNING"
DEFAULT_SINCE = "1h"
DEFAULT_MAX_ENTRIES = 5000
DEFAULT_TOP = 25

# Triage requests cap entries; trace can blow past it for a long-lived transaction.
TRACE_MAX_ENTRIES = 2000

_SINCE_RE = re.compile(r"^(\d+)([smhd])$")
_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


# ---------------------------------------------------------------------------
# Arg parsing
# ---------------------------------------------------------------------------


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="investigate logs",
        description="Investigate Aura prod GCP logs in three chained modes.",
    )
    # Mode discriminators (mutually exclusive at the conceptual level — checked at runtime)
    p.add_argument("--message", help="Mode 2 (focus): exact jsonPayload.message to filter on")
    p.add_argument("--transaction", help="Mode 3 (trace): full UUID")
    p.add_argument("--url", help="Decode a Logs Explorer URL (short or full) and run the embedded query")

    # Filters
    p.add_argument("--severity", default=DEFAULT_SEVERITY,
                   help=f"GCP severity threshold (default {DEFAULT_SEVERITY}). Ignored in trace mode.")
    p.add_argument("--since", default=DEFAULT_SINCE,
                   help=f"Time window (e.g. 30m, 4h, 2d). Default {DEFAULT_SINCE}.")
    p.add_argument("--container", help="Filter by resource.labels.container_name (e.g. user-service)")
    p.add_argument("--namespace", help="Filter by resource.labels.namespace_name (e.g. aura-services)")
    p.add_argument("--cluster", default=DEFAULT_CLUSTER,
                   help=f"Filter by resource.labels.cluster_name. Default {DEFAULT_CLUSTER}.")
    p.add_argument("--project", help="Override the project derived from the SA key.")

    # Tuning
    p.add_argument("--top", type=int, default=DEFAULT_TOP,
                   help=f"Triage: cap signature rows in output (default {DEFAULT_TOP}).")
    p.add_argument("--max-entries", type=int, default=DEFAULT_MAX_ENTRIES,
                   help=f"Cap entries fetched (default {DEFAULT_MAX_ENTRIES}).")

    return p.parse_args(argv)


# ---------------------------------------------------------------------------
# Filter builders
# ---------------------------------------------------------------------------


def parse_since(since: str) -> tuple[timedelta, str]:
    """'4h' → (timedelta(hours=4), '4h'). Raises ValueError on bad input."""
    m = _SINCE_RE.match(since)
    if not m:
        raise ValueError(f"--since must be like 30m, 4h, 2d (got {since!r})")
    n, unit = int(m.group(1)), m.group(2)
    seconds = {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit] * n
    return timedelta(seconds=seconds), since


def base_resource_filter(args: argparse.Namespace) -> str:
    parts = [
        'resource.type="k8s_container"',
        f'resource.labels.cluster_name="{args.cluster}"',
    ]
    if args.namespace:
        parts.append(f'resource.labels.namespace_name="{args.namespace}"')
    if args.container:
        parts.append(f'resource.labels.container_name="{args.container}"')
    return " AND ".join(parts)


def time_filter(window: timedelta) -> str:
    cutoff = datetime.now(timezone.utc) - window
    return f'timestamp>="{cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")}"'


# ---------------------------------------------------------------------------
# Mode handlers
# ---------------------------------------------------------------------------


def mode_triage(args, auth) -> str:
    delta, since_human = parse_since(args.since)
    filter_str = " AND ".join([
        base_resource_filter(args),
        f'severity>="{args.severity.upper()}"',
        time_filter(delta),
    ])
    print(f"# query: {filter_str}", file=sys.stderr)
    raw = list(list_entries(auth, filter_str, max_entries=args.max_entries))
    entries = [normalize(e) for e in raw]
    cap_hit = len(raw) >= args.max_entries
    return fmt.render_triage(
        entries,
        severity_filter=args.severity.upper(),
        since_human=since_human,
        cluster=args.cluster,
        namespace_filter=args.namespace,
        container_filter=args.container,
        cap_hit=cap_hit,
        top=args.top,
    )


def mode_focus(args, auth) -> str:
    delta, since_human = parse_since(args.since)
    # Escape any quotes the user pasted in the message for the LQL filter
    safe_msg = args.message.replace('"', '\\"')
    filter_str = " AND ".join([
        base_resource_filter(args),
        f'jsonPayload.message="{safe_msg}"',
        time_filter(delta),
    ])
    print(f"# query: {filter_str}", file=sys.stderr)
    entries = [normalize(e) for e in list_entries(
        auth, filter_str, max_entries=args.max_entries
    )]
    return fmt.render_focus(
        entries,
        message_filter=args.message,
        container_filter=args.container,
        since_human=since_human,
    )


def mode_trace_transaction(args, auth) -> str:
    if not _UUID_RE.match(args.transaction):
        return (
            f"--transaction expects a full UUID (got: {args.transaction}). "
            f"Example: 619D571B-7328-47DF-89CC-C66F70C35540"
        )
    # Case-insensitive: query both upper and lower forms
    delta, since_human = parse_since(args.since)
    upper = args.transaction.upper()
    lower = args.transaction.lower()
    filter_str = " AND ".join([
        base_resource_filter(args),
        f'(jsonPayload.transaction="{upper}" OR jsonPayload.transaction="{lower}")',
        time_filter(delta),
    ])
    print(f"# query: {filter_str}", file=sys.stderr)
    entries = [normalize(e) for e in list_entries(
        auth, filter_str, max_entries=TRACE_MAX_ENTRIES, order_by="timestamp asc"
    )]

    # If nothing matches in the strict filter, suggest the inner-meta path
    next_hint = None
    if entries:
        # Suggest a user pivot via the sibling search-gcp-by-user skill.
        latest = max(entries, key=lambda e: e.timestamp)
        if latest.user_id:
            next_hint = (f"search gcp by user --user {latest.user_id} "
                         f"--since {since_human}  (eng:search-gcp-by-user)")

    return fmt.render_trace(
        entries,
        pivot_label=f"Transaction {args.transaction}",
        next_step_hint=next_hint,
    )


def mode_url(args, auth) -> str:
    decoded = decode_logs_explorer_url(args.url)
    print(f"# decoded query: {decoded.filter_str}", file=sys.stderr)
    if decoded.project:
        print(f"# decoded project: {decoded.project}", file=sys.stderr)
    if decoded.duration_iso:
        print(f"# decoded duration: {decoded.duration_iso}", file=sys.stderr)

    # Use decoded duration if present, else fall back to --since
    if decoded.duration_iso:
        seconds = iso_duration_to_seconds(decoded.duration_iso)
        delta = timedelta(seconds=seconds)
        since_human = decoded.duration_iso  # PT12H etc.
    else:
        delta, since_human = parse_since(args.since)

    # Run the decoded query verbatim, scoped to time
    filter_str = f"({decoded.filter_str}) AND {time_filter(delta)}"
    entries = [normalize(e) for e in list_entries(
        auth, filter_str, max_entries=args.max_entries
    )]

    # Render as triage (most general; users typically share triage links)
    return fmt.render_triage(
        entries,
        severity_filter="(from URL)",
        since_human=since_human,
        cluster=args.cluster,
        namespace_filter=None,
        container_filter=None,
        top=args.top,
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: Optional[list[str]] = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = parse_args(argv)

    # Mutual-exclusion check
    mode_flags = [args.message, args.transaction, args.url]
    set_count = sum(1 for f in mode_flags if f)
    if set_count > 1:
        print(
            "ERROR: --message, --transaction, and --url are mutually exclusive.",
            file=sys.stderr,
        )
        return 2

    # Auth
    try:
        auth = get_client(project_override=args.project)
    except AuthError as e:
        print(f"AUTH ERROR: {e}", file=sys.stderr)
        return 3
    print(
        f"# auth: source={auth.source} project={auth.project} principal={auth.principal}",
        file=sys.stderr,
    )

    # Dispatch
    try:
        if args.url:
            out = mode_url(args, auth)
        elif args.transaction:
            out = mode_trace_transaction(args, auth)
        elif args.message:
            out = mode_focus(args, auth)
        else:
            out = mode_triage(args, auth)
    except ValueError as e:
        print(f"INPUT ERROR: {e}", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"QUERY ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        return 4

    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
