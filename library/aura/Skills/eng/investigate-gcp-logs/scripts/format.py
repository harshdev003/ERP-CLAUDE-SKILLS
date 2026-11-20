"""
Output formatters for the three modes — triage, focus, trace.

PII guardrail: Modes 1 (triage) and 2 (focus) NEVER echo Normalized.raw or
the un-truncated message of a Format-B-fallback entry. Only specifically
extracted canonical fields are surfaced. Mode 3 (trace) is the explicit
opt-in for full entries — by then the user has drilled into one transaction
or one user.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Iterable, Optional

from normalize import Normalized

# Per-row sample length for messages in the triage table
_TRIAGE_MSG_WIDTH = 60
_TRIAGE_SVC_WIDTH = 26  # accommodates names like neural-model-trainer-9
_USER_ID_DISPLAY = 28  # truncate Firebase UIDs in compact views


# ---------------------------------------------------------------------------
# Mode 1: triage
# ---------------------------------------------------------------------------


def render_triage(
    entries: list[Normalized],
    severity_filter: str,
    since_human: str,
    cluster: str,
    namespace_filter: Optional[str],
    container_filter: Optional[str],
    next_step_prefix: str = "investigate logs",
    cap_hit: bool = False,
    top: int = 25,
) -> str:
    """Group by (message, container) and render a ranked digest."""

    if not entries:
        return _empty_result(severity_filter, since_human, cluster, namespace_filter, container_filter)

    groups: dict[tuple[str, str], list[Normalized]] = defaultdict(list)
    for e in entries:
        # Treat empty messages as a single "no message" bucket per service so digest stays useful
        msg_key = e.message or "(no message — possibly textPayload-only or unparsed)"
        key = (msg_key, e.container or "?")
        groups[key].append(e)

    ranked = sorted(groups.items(), key=lambda kv: -len(kv[1]))

    lines = []
    scope_bits = [f"≥ {severity_filter}", f"last {since_human}", cluster]
    if namespace_filter:
        scope_bits.append(f"namespace={namespace_filter}")
    if container_filter:
        scope_bits.append(f"container={container_filter}")
    cap_note = " (CAP HIT — truncated; raise --max-entries or narrow --since/--container)" if cap_hit else ""
    lines.append(
        f"Errors {' | '.join(scope_bits)} | {len(entries)} entries, {len(groups)} signatures{cap_note}"
    )
    lines.append("")
    lines.append(
        f"{'#':>3}  {'message':<{_TRIAGE_MSG_WIDTH}}  {'service':<{_TRIAGE_SVC_WIDTH}}  "
        f"{'count':>5}  {'first':<20}  {'last':<20}  versions  sample"
    )
    lines.append("-" * (10 + _TRIAGE_MSG_WIDTH + _TRIAGE_SVC_WIDTH + 80))

    for idx, ((msg, container), bucket) in enumerate(ranked[:top], start=1):
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
            f"{idx:>3}  {_truncate(msg, _TRIAGE_MSG_WIDTH):<{_TRIAGE_MSG_WIDTH}}  "
            f"{_truncate(container, _TRIAGE_SVC_WIDTH):<{_TRIAGE_SVC_WIDTH}}  {len(bucket):>5}  "
            f"{first_ts:<20}  {last_ts:<20}  {_truncate(versions, 8):<8}  {sample_str}"
        )

    if len(ranked) > top:
        lines.append(f"... {len(ranked) - top} more signatures (re-run with --top {len(ranked)} to see all)")

    # Drill-in hint based on the top group
    top_msg, top_container = ranked[0][0]
    lines.append("")
    lines.append(
        f'→ Drill in: {next_step_prefix} --message "{_escape_for_shell(top_msg)}" '
        f'--container {top_container} --since {since_human}'
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Mode 2: focus
# ---------------------------------------------------------------------------


def render_focus(
    entries: list[Normalized],
    message_filter: str,
    container_filter: Optional[str],
    since_human: str,
    next_step_prefix: str = "investigate logs",
) -> str:
    if not entries:
        return f'No entries matched message="{message_filter}" container={container_filter or "*"} since={since_human}.'

    lines = [
        f'"{message_filter}" in {container_filter or "(any container)"} | last {since_human} | {len(entries)} entries',
        "",
    ]

    # Hour histogram
    hour_counts = Counter(_short_ts(e.timestamp)[:13] + "Z" for e in entries)  # YYYY-MM-DDTHH
    if hour_counts:
        lines.append("By hour:")
        max_count = max(hour_counts.values())
        for hour, count in sorted(hour_counts.items()):
            bar = _bar(count, max_count, width=20)
            lines.append(f"  {hour}  {bar}  {count}")
        lines.append("")

    # Version distribution
    versions = Counter(e.service_version or "?" for e in entries)
    lines.append("By version:")
    for v, c in versions.most_common():
        pct = 100.0 * c / len(entries)
        lines.append(f"  {v:<12}  {c:>5}  ({pct:.0f}%)")
    lines.append("")

    # Pod distribution
    pods = Counter(e.pod or "?" for e in entries)
    if len(pods) > 1:
        lines.append("By pod:")
        for p, c in pods.most_common(5):
            pct = 100.0 * c / len(entries)
            lines.append(f"  {p:<48}  {c:>5}  ({pct:.0f}%)")
        if len(pods) > 5:
            lines.append(f"  ... {len(pods) - 5} more")
        lines.append("")

    # Component.method
    sites = Counter(f"{e.component or '?'}.{e.method or '?'}" for e in entries)
    lines.append("By component:")
    for site, c in sites.most_common(5):
        pct = 100.0 * c / len(entries)
        lines.append(f"  {site:<48}  {c:>5}  ({pct:.0f}%)")
    lines.append("")

    # Affected users with provenance
    user_buckets: dict[str, list[Normalized]] = defaultdict(list)
    for e in entries:
        if e.user_id:
            user_buckets[e.user_id].append(e)
    if user_buckets:
        ranked_users = sorted(user_buckets.items(), key=lambda kv: -len(kv[1]))
        lines.append(f"Affected users (top 5 of {len(user_buckets)}):")
        non_canonical_paths_seen = set()
        for uid, ev in ranked_users[:5]:
            paths = sorted({e.user_id_path for e in ev if e.user_id_path})
            path_str = paths[0] if len(paths) == 1 else ",".join(paths)
            if path_str and path_str != "userId":
                non_canonical_paths_seen.add(path_str)
            label = f" (userId path: {path_str})" if path_str and path_str != "userId" else ""
            lines.append(f"  {uid:<32}  {len(ev):>4} hits{label}")
        if non_canonical_paths_seen:
            lines.append(
                f"  (note: non-canonical userId paths surfaced — code-quality signal worth noting)"
            )
        lines.append("")
    else:
        lines.append("Affected users: none extractable (no userId in any entry's known paths)")
        lines.append("")

    # Recent transactions
    by_tx: dict[str, Normalized] = {}
    for e in sorted(entries, key=lambda x: x.timestamp, reverse=True):
        if e.transaction and e.transaction not in by_tx:
            by_tx[e.transaction] = e
        if len(by_tx) >= 5:
            break
    if by_tx:
        lines.append(f"Recent transactions ({len(by_tx)} of {sum(1 for e in entries if e.transaction)} with tx):")
        for tx, e in by_tx.items():
            lines.append(f"  {tx}   {_short_ts(e.timestamp)}")
        lines.append("")
        # Drill-in hint
        first_tx = next(iter(by_tx))
        lines.append(f"→ Drill in: {next_step_prefix} --transaction {first_tx}")
    else:
        lines.append("No transactions extractable from these entries.")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Mode 3: trace
# ---------------------------------------------------------------------------


def render_trace(
    entries: list[Normalized],
    pivot_label: str,  # "Transaction <uuid>" or "User <uid>"
    next_step_prefix: str = "investigate logs",
    next_step_hint: Optional[str] = None,
) -> str:
    if not entries:
        return f'No entries matched {pivot_label}. Try widening --since.'

    entries.sort(key=lambda e: e.timestamp)
    services = sorted({e.container for e in entries if e.container})
    lines = [
        f"{pivot_label} | {len(entries)} entries across {len(services)} service(s) [{', '.join(services) or '?'}]",
        "",
    ]
    for e in entries:
        ts = _short_ts(e.timestamp, with_ms=True)
        site = f"{e.container or '?'}.{e.component or '?'}.{e.method or '?'}".strip(".")
        # In trace mode, we DO show the full message — user has explicitly drilled in.
        # But we still cap it to keep the timeline readable.
        msg = _truncate(e.message, 120)
        extra_bits = []
        if e.user_id and "user" not in pivot_label.lower():
            extra_bits.append(f"user={_truncate(e.user_id, _USER_ID_DISPLAY)}")
        extra = f"  ({'; '.join(extra_bits)})" if extra_bits else ""
        lines.append(f"{ts}  {e.severity:<7}  {site:<60}  {msg}{extra}")

    lines.append("")
    if next_step_hint:
        lines.append(f"→ Drill in: {next_step_hint}")
    else:
        lines.append("→ Done — last mode in the chain")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _empty_result(severity, since, cluster, namespace, container) -> str:
    bits = [f"≥ {severity}", f"last {since}", cluster]
    if namespace:
        bits.append(f"namespace={namespace}")
    if container:
        bits.append(f"container={container}")
    return (
        f"0 entries matched: {' | '.join(bits)}\n"
        f"\n"
        f"Try broadening:\n"
        f"  - --since 24h (current: {since})\n"
        f"  - --severity WARNING (current: {severity})\n"
        + ("  - drop --container\n" if container else "")
        + ("  - drop --namespace\n" if namespace else "")
    )


def _short_ts(ts: str, with_ms: bool = False) -> str:
    """RFC3339 nanos → 2026-05-06T15:41:43[.638]Z (UTC)."""
    if not ts:
        return ""
    # Strip nanos beyond ms, normalise to Z
    try:
        # Cloud Logging emits nanosecond precision; Python parses up to microsecond
        # 2026-05-06T15:41:43.638899709Z → cut at 6 digits + Z
        if "." in ts:
            head, frac = ts.split(".")
            frac = frac.rstrip("Z")
            frac = frac[:6]  # microseconds
            iso = f"{head}.{frac}+00:00"
        else:
            iso = ts.rstrip("Z") + "+00:00"
        dt = datetime.fromisoformat(iso).astimezone(timezone.utc)
        if with_ms:
            return dt.strftime("%H:%M:%S.") + f"{dt.microsecond // 1000:03d}"
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, AttributeError):
        return ts[:25]


def _truncate(s: Optional[str], width: int) -> str:
    if not s:
        return ""
    return s if len(s) <= width else s[: width - 1] + "…"


def _bar(value: int, max_value: int, width: int = 20) -> str:
    if max_value <= 0:
        return ""
    filled = int(round(width * value / max_value))
    return "█" * filled + "·" * (width - filled)


def _escape_for_shell(s: str) -> str:
    """Escape a message string so it can be pasted as a --message "..." argument."""
    return s.replace('"', '\\"')
