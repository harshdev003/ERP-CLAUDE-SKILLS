#!/usr/bin/env python3
"""
analyze-gcp-logs CLI entry point.

Broad warning/error **sweep** over Aura's prod GKE cluster with the standard
known-noise exclusion filter baked in, rendered as a ranked breakdown of
distinct message signatures + occurrence counts.

This is the broad-scan counterpart to `investigate-gcp-logs`:
  - analyze-gcp-logs  → "what is warning right now across the namespace?" (this script)
  - investigate-gcp-logs → "tell me everything about THIS one error / transaction / user"

The default filter lives in ../references/default-filter.md (first fenced block).
Each line there is a complete Cloud Logging filter fragment that is AND-ed onto
the base query. Override with --filter-file, or skip it entirely with
--no-default-filter.

Read-only. No writes anywhere.

PII guardrail (matches investigate-gcp-logs triage): the breakdown never echoes
the raw jsonPayload — only the canonical short message, service, counts, version,
and a truncated tx-slice / user-id prefix sample.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

# Skill-local imports — these scripts live alongside this file
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gcp import AuthError, get_client, list_entries  # noqa: E402
from normalize import Normalized, normalize  # noqa: E402

DEFAULT_CLUSTER = "aura-prod-cluster-a"
DEFAULT_NAMESPACE = "aura-services"
DEFAULT_SEVERITY = "WARNING"
DEFAULT_SINCE = "4h"
DEFAULT_MAX_ENTRIES = 5000
DEFAULT_TOP = 30

_SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_FILTER_FILE = os.path.join(_SKILL_ROOT, "references", "default-filter.md")

_SINCE_RE = re.compile(r"^(\d+)([smhd])$")
# The exclusion fragments live in the ```text fenced block. Other fenced blocks
# in the doc (e.g. the "How the query is assembled" example, which uses a plain
# ``` fence) must NOT be picked up.
_TEXT_FENCE_RE = re.compile(r"```text\n(.*?)```", re.DOTALL)
_ANY_FENCE_RE = re.compile(r"```[a-zA-Z0-9_-]*\n(.*?)```", re.DOTALL)

# Table widths (kept in sync with investigate-gcp-logs for visual parity)
_MSG_WIDTH = 60
_SVC_WIDTH = 26


# ---------------------------------------------------------------------------
# Arg parsing
# ---------------------------------------------------------------------------


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="analyze gcp logs",
        description="Broad warning/error sweep of Aura prod logs with the standard noise filter baked in.",
    )
    p.add_argument("--since", default=DEFAULT_SINCE,
                   help=f"Time window (e.g. 30m, 4h, 2d). Default {DEFAULT_SINCE}.")
    p.add_argument("--severity", default=DEFAULT_SEVERITY,
                   help=f"GCP severity threshold (default {DEFAULT_SEVERITY}).")
    p.add_argument("--namespace", default=DEFAULT_NAMESPACE,
                   help=f"resource.labels.namespace_name (default {DEFAULT_NAMESPACE}). Pass '' to scan all namespaces.")
    p.add_argument("--container", help="Narrow to one resource.labels.container_name (e.g. payment-service).")
    p.add_argument("--cluster", default=DEFAULT_CLUSTER,
                   help=f"resource.labels.cluster_name. Default {DEFAULT_CLUSTER}.")
    p.add_argument("--project", help="Override the project derived from the SA key.")
    p.add_argument("--top", type=int, default=DEFAULT_TOP,
                   help=f"Cap signature rows in output (default {DEFAULT_TOP}).")
    p.add_argument("--max-entries", type=int, default=DEFAULT_MAX_ENTRIES,
                   help=f"Cap entries fetched (default {DEFAULT_MAX_ENTRIES}).")
    p.add_argument("--filter-file", default=DEFAULT_FILTER_FILE,
                   help="Path to a file whose first fenced block holds exclusion fragments. "
                        "Default: the skill's references/default-filter.md.")
    p.add_argument("--no-default-filter", action="store_true",
                   help="Skip the baked-in noise filter entirely (raw severity sweep). "
                        "Useful to see what the filter is currently hiding.")
    return p.parse_args(argv)


# ---------------------------------------------------------------------------
# Filter assembly
# ---------------------------------------------------------------------------


def parse_since(since: str) -> tuple[timedelta, str]:
    """'4h' → (timedelta(hours=4), '4h'). Raises ValueError on bad input."""
    m = _SINCE_RE.match(since)
    if not m:
        raise ValueError(f"--since must be like 30m, 4h, 2d (got {since!r})")
    n, unit = int(m.group(1)), m.group(2)
    seconds = {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit] * n
    return timedelta(seconds=seconds), since


def time_filter(window: timedelta) -> str:
    cutoff = datetime.now(timezone.utc) - window
    return f'timestamp>="{cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")}"'


def load_exclusions(path: str) -> list[str]:
    """Read exclusion fragments from the ```text fenced code block of a markdown file.

    Prefers a ```text-tagged fence (the documented home for exclusions); falls back
    to the first fenced block if none is tagged, then to the whole file. Non-blank,
    non-comment (#) lines are returned verbatim. Missing file → [].
    """
    if not os.path.isfile(path):
        print(f"# WARNING: filter file not found at {path} — running with no exclusions", file=sys.stderr)
        return []
    with open(path, encoding="utf-8") as f:
        text = f.read()
    m = _TEXT_FENCE_RE.search(text) or _ANY_FENCE_RE.search(text)
    body = m.group(1) if m else text
    out: list[str] = []
    for line in body.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        out.append(s)
    return out


def build_filter(args: argparse.Namespace, window: timedelta, exclusions: list[str]) -> str:
    parts = [
        'resource.type="k8s_container"',
        f'resource.labels.cluster_name="{args.cluster}"',
    ]
    if args.namespace:
        parts.append(f'resource.labels.namespace_name="{args.namespace}"')
    if args.container:
        parts.append(f'resource.labels.container_name="{args.container}"')
    parts.append(f'severity>="{args.severity.upper()}"')
    parts.append(time_filter(window))
    parts.extend(exclusions)
    return " AND ".join(parts)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _short_ts(ts: str) -> str:
    """RFC3339 nanos → 2026-05-26T15:41:43Z (UTC)."""
    if not ts:
        return ""
    try:
        if "." in ts:
            head, frac = ts.split(".")
            frac = frac.rstrip("Z")[:6]
            iso = f"{head}.{frac}+00:00"
        else:
            iso = ts.rstrip("Z") + "+00:00"
        dt = datetime.fromisoformat(iso).astimezone(timezone.utc)
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, AttributeError):
        return ts[:20]


def _truncate(s: Optional[str], width: int) -> str:
    if not s:
        return ""
    return s if len(s) <= width else s[: width - 1] + "…"


def _escape_for_shell(s: str) -> str:
    return s.replace('"', '\\"')


def render_breakdown(
    entries: list[Normalized],
    *,
    severity: str,
    since_human: str,
    cluster: str,
    namespace: Optional[str],
    container: Optional[str],
    n_exclusions: int,
    cap_hit: bool,
    top: int,
) -> str:
    scope_bits = [f"≥ {severity}", f"last {since_human}", cluster]
    if namespace:
        scope_bits.append(f"namespace={namespace}")
    if container:
        scope_bits.append(f"container={container}")
    scope = " | ".join(scope_bits)
    filt_note = f"{n_exclusions} noise exclusions applied" if n_exclusions else "NO noise filter (raw sweep)"

    if not entries:
        return (
            f"0 entries matched | {scope} | {filt_note}\n\n"
            f"Nothing warning/erroring in this window after the noise filter — clean. "
            f"Try a wider --since, a lower --severity, or --no-default-filter to confirm the filter isn't over-excluding."
        )

    groups: dict[tuple[str, str], list[Normalized]] = defaultdict(list)
    for e in entries:
        msg_key = e.message or "(no message — textPayload-only or unparsed)"
        groups[(msg_key, e.container or "?")].append(e)

    ranked = sorted(groups.items(), key=lambda kv: -len(kv[1]))

    cap_note = " (CAP HIT — raise --max-entries or narrow --since)" if cap_hit else ""
    lines = [
        f"GCP log sweep | {scope} | {len(entries)} entries, {len(groups)} signatures | {filt_note}{cap_note}",
        "",
        f"{'#':>3}  {'message':<{_MSG_WIDTH}}  {'service':<{_SVC_WIDTH}}  "
        f"{'count':>5}  {'first':<20}  {'last':<20}  versions  sample",
        "-" * (10 + _MSG_WIDTH + _SVC_WIDTH + 80),
    ]

    for idx, ((msg, container_name), bucket) in enumerate(ranked[:top], start=1):
        bucket.sort(key=lambda e: e.timestamp)
        first_ts = _short_ts(bucket[0].timestamp)
        last_ts = _short_ts(bucket[-1].timestamp)
        versions = ",".join(sorted({b.service_version for b in bucket if b.service_version})) or "?"
        sample = bucket[-1]
        sample_bits = []
        if sample.tx_slice:
            sample_bits.append(f"tx={sample.tx_slice}")
        if sample.user_id:
            sample_bits.append(f"u={_truncate(sample.user_id, 8)}")
        sample_str = " ".join(sample_bits) or "(no tx/user)"
        lines.append(
            f"{idx:>3}  {_truncate(msg, _MSG_WIDTH):<{_MSG_WIDTH}}  "
            f"{_truncate(container_name, _SVC_WIDTH):<{_SVC_WIDTH}}  {len(bucket):>5}  "
            f"{first_ts:<20}  {last_ts:<20}  {_truncate(versions, 8):<8}  {sample_str}"
        )

    if len(ranked) > top:
        lines.append(f"... {len(ranked) - top} more signatures (re-run with --top {len(ranked)} to see all)")

    # ---- Handoff into investigate-gcp-logs ----
    top_msg, top_container = ranked[0][0]
    top_bucket = sorted(ranked[0][1], key=lambda e: e.timestamp)
    latest = top_bucket[-1]
    lines.append("")
    lines.append("→ Drill into a signature (deep-dive via investigate-gcp-logs):")
    lines.append(
        f'   investigate logs --message "{_escape_for_shell(top_msg)}" '
        f'--container {top_container} --since {since_human}'
    )
    if latest.transaction:
        lines.append(
            f"   investigate logs --transaction {latest.transaction}"
            f"   # trace the latest occurrence of signature #1"
        )
    lines.append("   (swap in any message/service/tx from the rows above to chase a different one)")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: Optional[list[str]] = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    args = parse_args(argv)

    try:
        window, since_human = parse_since(args.since)
    except ValueError as e:
        print(f"INPUT ERROR: {e}", file=sys.stderr)
        return 2

    exclusions = [] if args.no_default_filter else load_exclusions(args.filter_file)

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

    filter_str = build_filter(args, window, exclusions)
    print(f"# query: {filter_str}", file=sys.stderr)

    try:
        raw = list(list_entries(auth, filter_str, max_entries=args.max_entries))
    except Exception as e:
        print(f"QUERY ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        return 4

    entries = [normalize(e) for e in raw]
    cap_hit = len(raw) >= args.max_entries

    out = render_breakdown(
        entries,
        severity=args.severity.upper(),
        since_human=since_human,
        cluster=args.cluster,
        namespace=args.namespace or None,
        container=args.container,
        n_exclusions=len(exclusions),
        cap_hit=cap_hit,
        top=args.top,
    )
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
