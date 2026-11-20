#!/usr/bin/env python3
"""
search-gcp-by-user CLI entry point.

Find the GCP (GKE) log entries for one Firebase user id, oldest→newest, in
Aura's prod cluster. Read-only. This is the user-pivot companion to
eng:investigate-gcp-logs (triage / focus / trace-by-transaction); its output
ends with an `investigate logs --transaction <id>` hint so you can pivot from a
user into a specific transaction trace.

Usage:
  search.py --user <firebase-uid> [--since 24h] [--container <name>]
            [--namespace <name>] [--cluster <name>] [--project <id>]
  search.py --user <firebase-uid> --auth [--since 24h] [--project <id>]

userId can live under more than one payload path, so the query ORs the two that
are logged (`jsonPayload.userId`, the caller's uid from the request context, and
`jsonPayload.signInUid`, set by user-service verifyToken) and a client-side check
drops any post-normalize false positives.

--auth searches the Identity Platform user activity logs instead (sign-ins,
sign-ups, account lookups) and prints only allowlisted fields — see auth_logs.py.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from typing import Optional

# Skill-local imports — these scripts live alongside this file.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import auth_logs  # noqa: E402  (skill-local module)
import format as fmt  # noqa: E402  (skill-local module)
from gcp import AuthError, get_client, list_entries  # noqa: E402
from normalize import normalize  # noqa: E402

DEFAULT_CLUSTER = "aura-prod-cluster-a"
DEFAULT_SINCE = "24h"  # broader than the triage default — a user lookup wants recent history
MAX_ENTRIES = 2000     # a single user's entries in the window; bounded by the user filter

_SINCE_RE = re.compile(r"^(\d+)([smhd])$")


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="search-gcp-by-user",
        description="Find Aura prod GCP logs for one Firebase user id.",
    )
    p.add_argument("--user", required=True, help="Firebase user id to search for.")
    p.add_argument("--since", default=DEFAULT_SINCE,
                   help=f"Time window (e.g. 30m, 4h, 2d). Default {DEFAULT_SINCE}.")
    p.add_argument("--container",
                   help="Filter by resource.labels.container_name (e.g. user-service).")
    p.add_argument("--namespace",
                   help="Filter by resource.labels.namespace_name (e.g. aura-services).")
    p.add_argument("--cluster", default=DEFAULT_CLUSTER,
                   help=f"Filter by resource.labels.cluster_name. Default {DEFAULT_CLUSTER}.")
    p.add_argument("--project", help="Override the project derived from the SA key.")
    p.add_argument("--auth", action="store_true",
                   help="Search the Identity Platform auth logs (sign-ins) instead of the "
                        "GKE service logs. Prints no email, name or IP.")
    p.add_argument("--max-entries", type=int, default=MAX_ENTRIES,
                   help=f"Cap entries fetched (default {MAX_ENTRIES}).")
    return p.parse_args(argv)


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


def main(argv: Optional[list[str]] = None) -> int:
    # Force UTF-8 stdout so the digest's arrows/em-dashes (→ —) don't raise
    # UnicodeEncodeError on Windows consoles defaulting to cp1252.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    args = parse_args(argv)

    try:
        auth = get_client(project_override=args.project)
    except AuthError as e:
        print(f"AUTH ERROR: {e}", file=sys.stderr)
        return 3
    print(f"# auth: source={auth.source} project={auth.project} "
          f"principal={auth.principal}", file=sys.stderr)

    try:
        delta, since_human = parse_since(args.since)
        uid = args.user
        if args.auth:
            filter_str = " AND ".join([
                auth_logs.AUTH_RESOURCE_FILTER, auth_logs.uid_filter(uid), time_filter(delta),
            ])
            print(f"# query: {filter_str}", file=sys.stderr)
            # Newest first so the cap drops the oldest events, not the latest sign-in.
            events = [auth_logs.summarize(e) for e in list_entries(
                auth, filter_str, max_entries=args.max_entries, order_by="timestamp desc"
            )]
            print(auth_logs.render(events, uid, since_human, auth.project,
                                   cap_hit=len(events) >= args.max_entries))
            return 0
        filter_str = " AND ".join([
            base_resource_filter(args),
            f'(jsonPayload.userId="{uid}" OR jsonPayload.signInUid="{uid}")',
            time_filter(delta),
        ])
        print(f"# query: {filter_str}", file=sys.stderr)
        entries = [normalize(e) for e in list_entries(
            auth, filter_str, max_entries=args.max_entries, order_by="timestamp asc"
        )]
        # Defense-in-depth: drop false positives if the normalized user_id differs.
        # A verifyToken line names the caller in userId and the signed-in user in
        # signInUid, so a match on signInUid is kept whoever called.
        entries = [e for e in entries if e.user_id in (uid, None)
                   or ((e.raw.get("jsonPayload") or {}).get("signInUid") == uid)]

        # Suggest a transaction trace off the most-recent error/warning.
        next_hint = None
        for e in sorted(entries, key=lambda x: x.timestamp, reverse=True):
            if e.severity in ("ERROR", "WARNING") and e.transaction:
                next_hint = f"investigate logs --transaction {e.transaction}"
                break

        out = fmt.render_trace(entries, pivot_label=f"User {uid}",
                               next_step_hint=next_hint)
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
