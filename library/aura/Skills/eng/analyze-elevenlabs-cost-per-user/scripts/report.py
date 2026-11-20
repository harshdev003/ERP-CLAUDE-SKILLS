#!/usr/bin/env python3
"""
ElevenLabs per-user cost report (Conversational AI / ElevenAgents).

Sibling of `eng:analyze-elevenlabs-costs`. Where the workspace skill answers
"how much have we burned this period?" off `/v1/usage/character-stats`, THIS
one answers "who's driving cost?" by walking `/v1/convai/conversations` and
aggregating credits and call minutes by the `userId` dynamic variable that
the Aurie integration passes on every call.

**Actuals only.** Per-user dollar cost is computed directly as
`call_duration_secs / 60 × ELABS rate` ($0.09/min in-plan, $0.19/min burst),
both API actuals. No credits→minutes derivation, no plan-packaging ratio.

## Critical retention constraint

The Aura workspace runs with a **10-day Conversations Retention Period** set
on the ElevenLabs Privacy panel. After 10 days, transcript + audio + dynamic
variables are deleted upstream — so per-user attribution **requires fetching
within 10 days of the call**.

This script defaults to **yesterday** (the previous full calendar day in UTC)
so it always lands inside retention. Older windows can be requested via
`--since`/`--until`/`--window {last-5d,last-7d,last-10d}`; the script warns
when the window starts before the retention cutoff.

## Privacy posture (see Compliance/Privacy in AI Knowledge)

  - **Firebase UIDs are truncated to 8 characters + `…`** in the rendered
    markdown (Top-N table, distribution rows, flags). The JSON payload
    retains full UIDs because that's the documented exception in
    `Compliance/Privacy` — opt-in body for exact-match lookups by other
    agents — but every markdown surface only ever shows the prefix.
  - Default render is **Top-N (default 10) + aggregate distribution**, never
    a full per-user dump. `--full` exists for ad-hoc investigation but the
    UIDs are still truncated.
  - `givenName` is read from the API response but **never** rendered or
    persisted by the script.
  - No local cache. The script makes the API calls fresh every run; if a
    multi-week trend renderer ever ships, it can introduce its own storage
    layer with its own privacy review.

## CLI

    # Default — yesterday's calls, Top-10 + distribution
    python report.py

    # Other windows
    python report.py --window last-24h
    python report.py --window last-7d           # default for --trend
    python report.py --window last-10d          # full retention window
    python report.py --since 2026-05-13 --until 2026-05-14

    # Ad-hoc: full per-UID table (still truncated UIDs)
    python report.py --full

    # Slicing
    python report.py --top 25
    python report.py --json /tmp/per-user.json
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Iterable
from zoneinfo import ZoneInfo

try:
    import requests
except ImportError:
    sys.exit("Missing dependency: pip3 install requests")


ELEVENLABS_API = "https://api.elevenlabs.io/v1"
LIST_PATH = "/convai/conversations"
DETAIL_PATH = "/convai/conversations/{conversation_id}"

# Pacific is the default time anchor across every ElevenLabs skill (matches
# the dashboard's viewer-TZ behaviour for the Pacific-based team and the
# report-aurie-weekly-costs voice column). See Tools/ElevenLabs § Timezone
# anchoring for the rationale.
PACIFIC = ZoneInfo("America/Los_Angeles")

# Cost rates from Tools/ElevenLabs (Business plan).
COST_PER_MIN_IN_PLAN = 0.09
COST_PER_MIN_OVERAGE = 0.10
COST_PER_MIN_BURST = 0.19

# Workspace retention configured in ElevenLabs Privacy panel.
# Bumped 5 → 10 days on 2026-05-14; keep this in sync with the Privacy setting
# (canonical write-up: Tools/ElevenLabs → Data retention in AI Knowledge).
RETENTION_DAYS = 10

# UID redaction (see Compliance/Privacy in AI Knowledge).
UID_DISPLAY_PREFIX = 8


# ---------------------------------------------------------------------------
# Time-window resolution
# ---------------------------------------------------------------------------


WINDOW_CHOICES = ["yesterday", "today", "last-24h", "last-5d", "last-7d", "last-10d"]


def resolve_window(
    window: str | None, since: str | None, until: str | None
) -> tuple[int, int, str]:
    """Resolve the keyword window or explicit date range to `(start_unix_s, end_unix_s, label)`.

    **Time anchor.** Calendar-bounded windows (`today`, `yesterday`,
    `--since/--until`) and the daily-resolution buckets inside any window are
    anchored at **Pacific midnight** — same convention as
    `report-aurie-weekly-costs` and `analyze-elevenlabs-costs`, matching the
    ElevenLabs dashboard the Pacific-based team uses to compare numbers.
    Rolling windows (`last-24h`, `last-5d`, `last-7d`, `last-10d`) are
    TZ-agnostic (they walk back from `now`).
    """
    now_utc = datetime.now(tz=timezone.utc)
    now_pt = now_utc.astimezone(PACIFIC)

    if since or until:
        if not (since and until):
            sys.exit("--since and --until must be provided together")
        start_dt = _parse_date_pt(since)
        end_dt = _parse_date_pt(until)
        label = f"{since} → {until} (PT)"
    elif window == "today":
        # Pacific calendar day so far.
        start_dt = now_pt.replace(hour=0, minute=0, second=0, microsecond=0)
        end_dt = now_utc
        label = f"today ({start_dt.strftime('%b')} {start_dt.day} PT, in progress)"
    elif window == "yesterday" or window is None:
        # Previous full Pacific calendar day. end_dt = today PT midnight.
        end_dt = now_pt.replace(hour=0, minute=0, second=0, microsecond=0)
        start_dt = end_dt - timedelta(days=1)
        label = f"yesterday ({start_dt.strftime('%b')} {start_dt.day} PT)"
    elif window == "last-24h":
        end_dt = now_utc
        start_dt = now_utc - timedelta(hours=24)
        label = "last 24h"
    elif window == "last-5d":
        end_dt = now_utc
        start_dt = now_utc - timedelta(days=5)
        label = "last 5 days"
    elif window == "last-7d":
        end_dt = now_utc
        start_dt = now_utc - timedelta(days=7)
        label = "last 7 days"
    elif window == "last-10d":
        end_dt = now_utc
        start_dt = now_utc - timedelta(days=10)
        label = f"last 10 days (full retention window)"
    else:
        sys.exit(f"Unknown window: {window!r}")

    if start_dt >= end_dt:
        sys.exit(f"Empty window: {start_dt.isoformat()} >= {end_dt.isoformat()}")
    return int(start_dt.timestamp()), int(end_dt.timestamp()), label


def _parse_date_pt(s: str) -> datetime:
    """Parse `YYYY-MM-DD` as Pacific midnight (the team-default anchor)."""
    try:
        return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=PACIFIC)
    except ValueError:
        sys.exit(f"Bad date {s!r} — expected YYYY-MM-DD (interpreted as Pacific midnight)")


# ---------------------------------------------------------------------------
# API access (with retry + pagination)
# ---------------------------------------------------------------------------


def _request(path: str, params: dict, api_key: str, *, ok_404: bool = False) -> dict | None:
    """GET with retry on 429. Returns None on 404 if ok_404."""
    url = f"{ELEVENLABS_API}{path}"
    headers = {"xi-api-key": api_key}
    for attempt in range(5):
        r = requests.get(url, headers=headers, params=params, timeout=60)
        if r.status_code == 429:
            wait = float(r.headers.get("Retry-After", 2 ** attempt))
            time.sleep(min(wait, 30))
            continue
        if r.status_code == 404 and ok_404:
            return None
        if not r.ok:
            sys.exit(
                f"ElevenLabs {r.status_code} on {path}: {r.text[:300]}\n"
                "Hint: the API key needs scopes Workspace Analytics + User Read + "
                "ElevenAgents Read. See Tools/ElevenLabs in AI Knowledge."
            )
        return r.json()
    sys.exit(f"ElevenLabs {path}: rate-limited after 5 retries")


def list_conversations(
    start_unix: int,
    end_unix: int,
    api_key: str,
) -> Iterable[dict]:
    """Yield conversation summaries in [start_unix, end_unix]. Cursor-paginated."""
    cursor: str | None = None
    while True:
        params: dict = {
            "page_size": 100,
            "call_start_after_unix": start_unix,
            "call_start_before_unix": end_unix,
        }
        if cursor:
            params["cursor"] = cursor
        page = _request(LIST_PATH, params, api_key) or {}
        items = page.get("conversations") or []
        if not items:
            return
        for c in items:
            ts = c.get("start_time_unix_secs") or 0
            if start_unix <= ts <= end_unix:
                yield c
        if not page.get("has_more") or not page.get("next_cursor"):
            return
        cursor = page["next_cursor"]


def fetch_detail(conversation_id: str, api_key: str) -> dict | None:
    """Fetch conversation detail. None on 404 (likely scrubbed by retention)."""
    return _request(DETAIL_PATH.format(conversation_id=conversation_id), {}, api_key, ok_404=True)


# ---------------------------------------------------------------------------
# Attribution row extraction
# ---------------------------------------------------------------------------


def extract_attribution(detail: dict) -> dict | None:
    """Pull the per-conversation attribution row from a detail blob.

    Returns None if the detail lacks a user_id (e.g. testing / agent-internal
    calls where Aurie didn't pass dynamic variables).

    NOTE: `givenName` is intentionally NOT included here — the script never
    reads or surfaces it. Other dynamic variables (`platform`, `locale`,
    `incognito`) are non-PII identifiers and are useful for segmentation.
    """
    if not isinstance(detail, dict):
        return None
    cicd = detail.get("conversation_initiation_client_data") or {}
    dyn = cicd.get("dynamic_variables") or {}
    user_id = detail.get("user_id") or dyn.get("userId")
    if not user_id:
        return None
    md = detail.get("metadata") or {}
    charging = md.get("charging") or {}
    incognito_raw = dyn.get("incognito")
    incognito = str(incognito_raw).lower() == "true" if incognito_raw is not None else False
    return {
        "conversation_id": detail.get("conversation_id"),
        "user_id": user_id,
        "agent_id": detail.get("agent_id"),
        "agent_name": detail.get("agent_name"),
        "start_unix": md.get("start_time_unix_secs") or 0,
        "duration_secs": float(md.get("call_duration_secs") or 0),
        "credits": float(md.get("cost") or charging.get("call_charge") or 0),
        "is_burst": bool(charging.get("is_burst")),
        "incognito": incognito,
        "platform": dyn.get("platform"),
        "locale": dyn.get("locale"),
    }


def duration_to_dollars(duration_secs: float, *, is_burst: bool) -> float:
    """Convert call duration directly to dollar cost at the ELABS per-minute rate.

    Both inputs are API actuals: `call_duration_secs` from
    `/v1/convai/conversations/{id}` metadata, and the per-minute rates ($0.09
    in-plan, $0.19 burst) from ELABS pricing. No credits→minutes derivation —
    the calculation is `minutes × rate`, full stop.

    Burst calls (`metadata.charging.is_burst`) use $0.19/min. All others use
    the in-plan rate $0.09/min — even on portions of workspace usage that
    may have crossed into overage, since we can't attribute the marginal
    overage minute to a specific call. The workspace skill
    (`eng:analyze-elevenlabs-costs`) reports the live `current_overage.amount`
    on the subscription endpoint when we're over.
    """
    minutes = duration_secs / 60.0
    rate = COST_PER_MIN_BURST if is_burst else COST_PER_MIN_IN_PLAN
    return minutes * rate


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def aggregate_by_user(rows: list[dict]) -> list[dict]:
    """Group attribution rows by user_id; return sorted descending by credits.

    Tracks `duration_secs` split by in_plan vs burst so the per-user dollar
    cost is computed directly from minutes × ELABS rate (both API actuals),
    no credits→minutes ratio in the math.
    """
    by_uid: dict[str, dict] = {}
    for r in rows:
        uid = r["user_id"]
        bucket = by_uid.setdefault(uid, {
            "user_id": uid,
            "conversations": 0,
            "duration_secs": 0.0,
            "duration_in_plan_secs": 0.0,
            "duration_burst_secs": 0.0,
            "credits_in_plan": 0.0,
            "credits_burst": 0.0,
            "incognito_conversations": 0,
            "platforms": set(),
        })
        bucket["conversations"] += 1
        bucket["duration_secs"] += r["duration_secs"]
        if r["is_burst"]:
            bucket["credits_burst"] += r["credits"]
            bucket["duration_burst_secs"] += r["duration_secs"]
        else:
            bucket["credits_in_plan"] += r["credits"]
            bucket["duration_in_plan_secs"] += r["duration_secs"]
        if r["incognito"]:
            bucket["incognito_conversations"] += 1
        if r["platform"]:
            bucket["platforms"].add(r["platform"])
    out = []
    for b in by_uid.values():
        out.append({
            **b,
            "platforms": sorted(b["platforms"]),
            "total_credits": b["credits_in_plan"] + b["credits_burst"],
        })
    out.sort(key=lambda x: x["total_credits"], reverse=True)
    return out


# ---------------------------------------------------------------------------
# Privacy-aware rendering helpers
# ---------------------------------------------------------------------------


def _truncate_uid(uid: str) -> str:
    """Redact a Firebase UID per Compliance/Privacy: first 8 chars + ellipsis.

    Full UIDs are only permitted in opt-in lookup surfaces (JSON payloads,
    Notion bodies). EVERY markdown / Slack surface in this script routes
    through this helper.
    """
    if not uid:
        return "?"
    if len(uid) <= UID_DISPLAY_PREFIX:
        return uid
    return f"{uid[:UID_DISPLAY_PREFIX]}…"


def _fmt_mins(secs: float) -> str:
    if secs < 60:
        return f"{secs:.0f}s"
    return f"{secs / 60:,.1f} min"


def _fmt_money(amount: float) -> str:
    return f"${amount:,.2f}"


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    if len(values) < 2:
        return values[0]
    return statistics.quantiles(values, n=100)[max(0, min(99, int(pct) - 1))]


def _user_cost_total(u: dict) -> float:
    """Sum a user's per-bucket cost from duration × ELABS rate. Pure actual."""
    return (
        duration_to_dollars(u["duration_in_plan_secs"], is_burst=False)
        + duration_to_dollars(u["duration_burst_secs"], is_burst=True)
    )


def _user_costs(by_user: list[dict]) -> list[float]:
    """Map aggregated per-user records → list of dollar amounts (one per UID)."""
    return [_user_cost_total(u) for u in by_user]


def compute_cost_distribution(amounts: list[float]) -> dict | None:
    """Distribution stats over a list of per-user dollar amounts.

    Lifted from analyze-openai-cost-per-user's _compute_stats — same shape
    so the two skills can be compared apples-to-apples. Uses the `inclusive`
    quantile method because the input is the whole population of users in
    the window, not a sample; this guarantees p99 <= max for small populations.
    Returns None for empty input; for n==1 returns a degenerate block with
    every percentile equal to the single value and stddev = 0.
    """
    n = len(amounts)
    if n == 0:
        return None
    if n == 1:
        v = amounts[0]
        return {
            "count": 1, "mean": v, "median": v,
            "p90": v, "p95": v, "p99": v, "max": v, "stddev": 0.0,
        }
    mean = statistics.fmean(amounts)
    stddev = statistics.stdev(amounts)
    centiles = statistics.quantiles(amounts, n=100, method="inclusive")
    return {
        "count": n,
        "mean": mean,
        "median": statistics.median(amounts),
        "p90": centiles[89],
        "p95": centiles[94],
        "p99": centiles[98],
        "max": max(amounts),
        "stddev": stddev,
    }


def render_cost_distribution(
    stats: dict | None,
    *,
    total_cost: float,
    by_user: list[dict],
    section_title: str = "Cost per user — distribution",
) -> list[str]:
    """Render the cost-per-user distribution section. Matches the framing
    `analyze-openai-cost-per-user` uses so the two reports read alike.

    Returns a list of markdown lines (empty if stats is None).
    """
    if not stats:
        return []
    lines = [f"### {section_title}", ""]
    lines.append("| Stat | Cost / user |")
    lines.append("|---|---:|")
    lines.append(f"| Users in window | {stats['count']:,} |")
    lines.append(f"| Mean   | {_fmt_money(stats['mean'])} |")
    lines.append(f"| Median | {_fmt_money(stats['median'])} |")
    lines.append(f"| p90    | {_fmt_money(stats['p90'])} |")
    lines.append(f"| p95    | {_fmt_money(stats['p95'])} |")
    lines.append(f"| p99    | {_fmt_money(stats['p99'])} |")
    lines.append(f"| Max    | {_fmt_money(stats['max'])} |")
    lines.append(f"| Stddev | {_fmt_money(stats['stddev'])} |")
    lines.append("")

    # Top-user framing — share of total + multiplier vs median.
    if by_user and total_cost > 0:
        top_cost = _user_cost_total(by_user[0])
        top_share = top_cost / total_cost * 100
        median = stats["median"]
        if median > 0:
            multiplier = top_cost / median
            lines.append(
                f"**Top user accounts for** {top_share:.1f}% of window cost "
                f"({_fmt_money(top_cost)}) — ~{multiplier:.0f}× the median user."
            )
        else:
            lines.append(
                f"**Top user accounts for** {top_share:.1f}% of window cost "
                f"({_fmt_money(top_cost)})."
            )
        lines.append("")
    return lines


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------


def render_report(
    window_label: str,
    rows: list[dict],
    *,
    top: int,
    show_full: bool,
) -> str:
    lines: list[str] = []
    lines.append(f"# ElevenLabs Per-User Cost — {window_label}")
    lines.append("")

    retention_window_end = datetime.now(tz=timezone.utc) - timedelta(days=RETENTION_DAYS)
    lines.append(
        f"> ⚠️ **{RETENTION_DAYS}-day retention.** ElevenLabs deletes conversation "
        f"details older than ~{RETENTION_DAYS} days (workspace Privacy setting). "
        f"Anything before {retention_window_end.strftime('%b')} {retention_window_end.day}, {retention_window_end.year} is no "
        f"longer available via the API."
    )
    lines.append("")

    if not rows:
        lines.append("_No conversations with a `userId` dynamic variable in this window._")
        return "\n".join(lines)

    by_user = aggregate_by_user(rows)
    total_conversations = len(rows)
    total_users = len(by_user)
    total_secs = sum(r["duration_secs"] for r in rows)
    total_secs_in_plan = sum(r["duration_secs"] for r in rows if not r["is_burst"])
    total_secs_burst = sum(r["duration_secs"] for r in rows if r["is_burst"])
    total_credits_in_plan = sum(r["credits"] for r in rows if not r["is_burst"])
    total_credits_burst = sum(r["credits"] for r in rows if r["is_burst"])
    total_credits = total_credits_in_plan + total_credits_burst
    cost_in_plan = duration_to_dollars(total_secs_in_plan, is_burst=False)
    cost_burst = duration_to_dollars(total_secs_burst, is_burst=True)
    cost_total = cost_in_plan + cost_burst

    lines.append(
        f"**{total_conversations:,} conversations** across **{total_users:,} unique users** · "
        f"{_fmt_mins(total_secs)} total · {int(total_credits):,} credits → "
        f"**{_fmt_money(cost_total)}** (minutes × ELABS rate — actual)"
    )
    if total_secs_burst > 0:
        lines.append(
            f"_Of which {_fmt_mins(total_secs_burst)} were burst-rate "
            f"(\\${COST_PER_MIN_BURST}/min) → {_fmt_money(cost_burst)}._"
        )
    lines.append("")

    # Cost-per-user distribution — primary signal for "how concentrated is spend?".
    # Matches the framing in eng:analyze-openai-cost-per-user so the two reports
    # read alike.
    user_cost_amounts = _user_costs(by_user)
    cost_stats = compute_cost_distribution(user_cost_amounts)
    lines.extend(render_cost_distribution(
        cost_stats,
        total_cost=cost_total,
        by_user=by_user,
    ))

    # Minutes-per-user distribution — secondary, volume thinking. Switched
    # from credits to minutes since minutes are the unit ELABS bills on and
    # are pure API actuals (no credits→minutes derivation in the math).
    per_user_secs = sorted([u["duration_secs"] for u in by_user], reverse=True)
    if len(per_user_secs) >= 2:
        median_secs = statistics.median(per_user_secs)
        p90_secs = _percentile(per_user_secs, 90)
        p99_secs = _percentile(per_user_secs, 99)
        max_secs = per_user_secs[0]
        top_share = (max_secs / total_secs * 100) if total_secs else 0
        lines.append("### Distribution (minutes per user)")
        lines.append("")
        lines.append(
            f"- Median: {_fmt_mins(median_secs)}\n"
            f"- p90: {_fmt_mins(p90_secs)}\n"
            f"- p99: {_fmt_mins(p99_secs)}\n"
            f"- Max: {_fmt_mins(max_secs)} — "
            f"**{top_share:.1f}% of all minutes in window**"
        )
        lines.append("")

    target_rows = by_user if show_full else by_user[:top]
    if show_full:
        lines.append(f"### All {total_users} users")
    else:
        lines.append(f"### Top {min(top, total_users)} users by credits")
    lines.append("")
    lines.append("| # | UID prefix | Convs | Total minutes | Credits | Est. cost | Flags |")
    lines.append("|---:|---|---:|---:|---:|---:|---|")
    for idx, u in enumerate(target_rows, start=1):
        flags: list[str] = []
        if u["incognito_conversations"] == u["conversations"] and u["conversations"] > 0:
            flags.append("incognito")
        elif u["incognito_conversations"] > 0:
            flags.append(f"{u['incognito_conversations']}/{u['conversations']} incognito")
        if u["credits_burst"] > 0:
            flags.append(f"burst {int(u['credits_burst']):,}c")
        if len(u["platforms"]) > 1:
            flags.append("+".join(u["platforms"]))
        elif u["platforms"]:
            flags.append(u["platforms"][0])
        cost = _user_cost_total(u)
        lines.append(
            f"| {idx} | `{_truncate_uid(u['user_id'])}` | {u['conversations']} | "
            f"{u['duration_secs'] / 60:,.1f} | {int(u['total_credits']):,} | "
            f"{_fmt_money(cost)} | {', '.join(flags) if flags else '—'} |"
        )
    lines.append("")
    lines.append(
        f"_UIDs truncated to first {UID_DISPLAY_PREFIX} characters per "
        f"[Compliance/Privacy](https://www.notion.so/35f75e23af20813ea4e7f4165c9e0844). "
        f"Full UIDs are available in `--json` output for lookups by other agents._"
    )
    lines.append("")

    convo_credits = sorted([r["credits"] for r in rows], reverse=True)
    if len(convo_credits) >= 5:
        lines.append("### Distribution (credits per conversation)")
        lines.append("")
        lines.append(
            f"- Median: {int(statistics.median(convo_credits)):,} credits "
            f"({_fmt_mins(statistics.median([r['duration_secs'] for r in rows]))})\n"
            f"- p90: {int(_percentile(convo_credits, 90)):,} credits\n"
            f"- p99: {int(_percentile(convo_credits, 99)):,} credits\n"
            f"- Max: {int(convo_credits[0]):,} credits"
        )
        lines.append("")

    lines.append(
        "_Cost basis: per-call **minutes × ELABS rate**, both API actuals "
        "(`metadata.call_duration_secs` × $0.09/min in-plan or $0.19/min burst). "
        "For workspace totals run `eng:analyze-elevenlabs-costs`._"
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Trend rendering (day-over-day within retention)
# ---------------------------------------------------------------------------


def _day_key(unix_secs: int) -> str:
    """Pacific calendar day key, sortable & display-friendly: 'YYYY-MM-DD'.

    Trend mode buckets by Pacific calendar day so day-over-day comparisons
    align with the dashboard and the weekly report skill. A call that starts
    at 23:30 PT and runs into the next UTC day still counts toward the
    Pacific day it began.
    """
    return datetime.fromtimestamp(unix_secs, tz=PACIFIC).strftime("%Y-%m-%d")


def _day_label(day_key: str) -> str:
    """Display label: 'Wed May 13'.

    Avoids POSIX-only `%-d` / Windows-only `%#d` — both fail on the other
    platform. Format the integer day manually to stay cross-platform.
    """
    d = datetime.strptime(day_key, "%Y-%m-%d").replace(tzinfo=PACIFIC)
    return f"{d.strftime('%a %b')} {d.day}"


def _bucket_by_day(rows: list[dict]) -> dict[str, list[dict]]:
    """Group attribution rows by Pacific calendar day (YYYY-MM-DD PT)."""
    buckets: dict[str, list[dict]] = {}
    for r in rows:
        buckets.setdefault(_day_key(int(r["start_unix"])), []).append(r)
    return buckets


def _all_day_keys(start_unix: int, end_unix: int) -> list[str]:
    """All Pacific calendar days fully or partially covered by [start_unix, end_unix]."""
    out: list[str] = []
    start_dt = datetime.fromtimestamp(start_unix, tz=PACIFIC).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    end_dt = datetime.fromtimestamp(end_unix, tz=PACIFIC)
    dt = start_dt
    while dt <= end_dt:
        out.append(dt.strftime("%Y-%m-%d"))
        dt += timedelta(days=1)
    return out


def _daily_summary(rows: list[dict]) -> dict:
    """Per-day rollup: convs, unique users, minutes, credits, cost (actual)."""
    if not rows:
        return {
            "conversations": 0, "users": 0, "duration_secs": 0.0,
            "credits": 0.0, "credits_burst": 0.0, "cost": 0.0,
            "top_user_share_pct": 0.0,
        }
    users = {r["user_id"] for r in rows}
    duration_secs = sum(r["duration_secs"] for r in rows)
    duration_in_plan = sum(r["duration_secs"] for r in rows if not r["is_burst"])
    duration_burst = sum(r["duration_secs"] for r in rows if r["is_burst"])
    credits_in_plan = sum(r["credits"] for r in rows if not r["is_burst"])
    credits_burst = sum(r["credits"] for r in rows if r["is_burst"])
    cost = (
        duration_to_dollars(duration_in_plan, is_burst=False)
        + duration_to_dollars(duration_burst, is_burst=True)
    )
    by_user = aggregate_by_user(rows)
    top_user_credits = by_user[0]["total_credits"] if by_user else 0.0
    total_credits = credits_in_plan + credits_burst
    top_share = (top_user_credits / total_credits * 100) if total_credits else 0.0
    return {
        "conversations": len(rows),
        "users": len(users),
        "duration_secs": duration_secs,
        "credits": total_credits,
        "credits_burst": credits_burst,
        "cost": cost,
        "top_user_share_pct": top_share,
    }


def render_trend_report(
    rows: list[dict],
    start_unix: int,
    end_unix: int,
    *,
    top: int,
) -> str:
    """Day-over-day trend within ElevenLabs' retention window.

    Three sections:
      1. Per-day rollup table — convs, users, minutes, credits, cost, ΔΔ
      2. Top-user trend table — top-N by total credits, per-day breakdown
      3. Heavy-hitter shifts — who entered/dropped the top-10 day-over-day,
         concentration change.
    """
    lines: list[str] = []
    day_keys = _all_day_keys(start_unix, end_unix)
    first_label = _day_label(day_keys[0]) if day_keys else "?"
    last_label = _day_label(day_keys[-1]) if day_keys else "?"
    lines.append(f"# ElevenLabs Per-User Cost — trend ({first_label} → {last_label}, {len(day_keys)} days)")
    lines.append("")

    retention_cutoff = datetime.now(tz=timezone.utc) - timedelta(days=RETENTION_DAYS)
    lines.append(
        f"> ⚠️ **{RETENTION_DAYS}-day retention.** ElevenLabs deletes conversation "
        f"details older than ~{RETENTION_DAYS} days; anything before "
        f"{retention_cutoff.strftime('%b')} {retention_cutoff.day}, {retention_cutoff.year} is no longer available via "
        f"the API. The trend window is bounded by this limit."
    )
    lines.append("")

    if not rows:
        lines.append("_No conversations with a `userId` dynamic variable in this window._")
        return "\n".join(lines)

    buckets = _bucket_by_day(rows)
    daily = {dk: _daily_summary(buckets.get(dk, [])) for dk in day_keys}

    # 1. Per-day rollup table.
    lines.append("### Per-day rollup")
    lines.append("")
    lines.append("| Day | Convs | Users | Minutes | Credits | Est. cost | ΔΔ cost |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    prev_cost: float | None = None
    for dk in day_keys:
        s = daily[dk]
        if prev_cost is None or prev_cost == 0:
            delta = "—"
        else:
            pct = (s["cost"] - prev_cost) / prev_cost * 100
            delta = f"{pct:+.1f}%"
        lines.append(
            f"| {_day_label(dk)} | {s['conversations']} | {s['users']} | "
            f"{s['duration_secs'] / 60:,.1f} | {int(s['credits']):,} | "
            f"{_fmt_money(s['cost'])} | {delta} |"
        )
        prev_cost = s["cost"] if s["conversations"] else prev_cost
    lines.append("")

    total_cost = sum(s["cost"] for s in daily.values())
    total_convs = sum(s["conversations"] for s in daily.values())
    union_users = len({r["user_id"] for r in rows})
    lines.append(
        f"**Window total:** {_fmt_money(total_cost)} across {total_convs:,} conversations · "
        f"{union_users:,} distinct users (union across days)."
    )
    lines.append("")

    # Cost-per-user distribution over the full window — answers
    # "how concentrated is per-user spend across the window?".
    by_user_window = aggregate_by_user(rows)
    window_user_cost_amounts = _user_costs(by_user_window)
    window_cost_stats = compute_cost_distribution(window_user_cost_amounts)
    lines.extend(render_cost_distribution(
        window_cost_stats,
        total_cost=total_cost,
        by_user=by_user_window,
        section_title="Cost per user — window distribution",
    ))

    # 2. Top-user trend table — top-N by TOTAL credits across the full window.
    overall = aggregate_by_user(rows)[:top]
    if overall:
        lines.append(f"### Top {len(overall)} users — daily breakdown")
        lines.append("")
        header_days = " | ".join(_day_label(dk).split(" ", 1)[-1] for dk in day_keys)
        lines.append(f"| # | UID prefix | {header_days} | Window total | Days present |")
        lines.append("|---:|---|" + "---:|" * len(day_keys) + "---:|---:|")

        # Per-user-per-day credit map.
        user_day_credits: dict[str, dict[str, float]] = {}
        for r in rows:
            uid = r["user_id"]
            dk = _day_key(int(r["start_unix"]))
            user_day_credits.setdefault(uid, {}).setdefault(dk, 0.0)
            user_day_credits[uid][dk] += r["credits"]

        for idx, u in enumerate(overall, start=1):
            uid = u["user_id"]
            per_day = user_day_credits.get(uid, {})
            day_cells: list[str] = []
            days_present = 0
            for dk in day_keys:
                c = per_day.get(dk, 0)
                if c > 0:
                    days_present += 1
                day_cells.append(f"{int(c):,}" if c else "—")
            total_credits = int(u["total_credits"])
            lines.append(
                f"| {idx} | `{_truncate_uid(uid)}` | "
                + " | ".join(day_cells)
                + f" | {total_credits:,} | {days_present}/{len(day_keys)} |"
            )
        lines.append("")
        lines.append(
            f"_UIDs truncated to first {UID_DISPLAY_PREFIX} characters per "
            f"[Compliance/Privacy](https://www.notion.so/35f75e23af20813ea4e7f4165c9e0844)._"
        )
        lines.append("")

    # 3. Heavy-hitter shifts — top-10 entries day-over-day.
    daily_top_uids: dict[str, list[str]] = {}
    for dk in day_keys:
        dk_rows = buckets.get(dk, [])
        if dk_rows:
            ranked = aggregate_by_user(dk_rows)[:10]
            daily_top_uids[dk] = [u["user_id"] for u in ranked]
        else:
            daily_top_uids[dk] = []

    shifts: list[str] = []
    for prev_dk, dk in zip(day_keys, day_keys[1:]):
        prev_set = set(daily_top_uids[prev_dk])
        cur_set = set(daily_top_uids[dk])
        entered = cur_set - prev_set
        dropped = prev_set - cur_set
        if entered or dropped:
            entered_str = ", ".join(f"`{_truncate_uid(u)}`" for u in sorted(entered)) or "—"
            dropped_str = ", ".join(f"`{_truncate_uid(u)}`" for u in sorted(dropped)) or "—"
            shifts.append(
                f"- **{_day_label(dk)}**: entered top-10 → {entered_str}; dropped from top-10 → {dropped_str}"
            )

    if shifts:
        lines.append("### Heavy-hitter shifts (top-10 entries / exits)")
        lines.append("")
        lines.extend(shifts)
        lines.append("")

    # Concentration trend.
    concentration_line = []
    for dk in day_keys:
        if daily[dk]["conversations"]:
            concentration_line.append(f"{_day_label(dk).split(' ', 1)[-1]}: {daily[dk]['top_user_share_pct']:.1f}%")
    if concentration_line:
        lines.append("### Concentration (top user's share of daily credits)")
        lines.append("")
        lines.append(" · ".join(concentration_line))
        lines.append("")

    lines.append(
        "_Cost basis: in-plan $0.09/min · burst $0.19/min · 1,562.72 credits/min "
        "(Business plan). Full UIDs available in `--json` output. For workspace "
        "totals + budget framing run `eng:analyze-elevenlabs-costs`._"
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(
        description="ElevenLabs per-user cost report (Conversational AI)",
    )
    ap.add_argument(
        "--window",
        choices=WINDOW_CHOICES,
        default="yesterday",
        help="Time window. Default `yesterday` = previous full calendar day UTC. "
             "ElevenLabs retention is ~10 days; use `last-10d` for the maximum "
             "live window. `--trend` mode forces `last-7d` by default.",
    )
    ap.add_argument("--since", help="UTC start date (YYYY-MM-DD). Use with --until.")
    ap.add_argument("--until", help="UTC end date, exclusive (YYYY-MM-DD).")
    ap.add_argument(
        "--top",
        type=int,
        default=10,
        help="How many users to list in the Top-N table. Default 10. UIDs are "
             "truncated regardless of this value.",
    )
    ap.add_argument(
        "--full",
        action="store_true",
        help="Render the full per-UID table instead of Top-N. UIDs are still "
             "truncated per Compliance/Privacy.",
    )
    ap.add_argument(
        "--trend",
        action="store_true",
        help="Day-over-day trend mode. Defaults to last-7d unless "
             "--since/--until or --window is set explicitly (use --window "
             "last-10d for the full retention window). Outputs per-day "
             "rollup with ΔΔ deltas, top-N daily breakdown, and "
             "heavy-hitter shifts. Slower (one serial detail fetch per "
             "conversation, ~6.6 min per day of window at 2026-08 volume: "
             "~45min for a 7-day window, ~65min for last-10d) but bounded "
             "by retention.",
    )
    ap.add_argument("--json", dest="json_out", help="Write structured JSON to this path.")
    args = ap.parse_args()

    api_key = os.environ.get("ELEVENLABS_ADMIN_API_KEY") or os.environ.get("ELEVENLABS_API_KEY")
    if not api_key:
        sys.exit(
            "Missing ELEVENLABS_ADMIN_API_KEY (preferred) or ELEVENLABS_API_KEY. "
            "See Tools/ElevenLabs in AI Knowledge for the required scopes "
            "(Workspace Analytics + User Read + ElevenAgents Read)."
        )

    # `--trend` overrides --window to last-7d (weeklong rolling, comfortably
    # inside the 10-day retention window) unless the caller passed an explicit
    # --since/--until. Honour explicit ranges so a narrower or wider window
    # (up to last-10d) is still supported.
    if args.trend and not (args.since or args.until):
        effective_window = "last-7d"
    else:
        effective_window = args.window
    start_unix, end_unix, label = resolve_window(effective_window, args.since, args.until)

    retention_cutoff = int((datetime.now(tz=timezone.utc) - timedelta(days=RETENTION_DAYS)).timestamp())
    if start_unix < retention_cutoff:
        days = (retention_cutoff - start_unix) / 86400
        print(
            f"# ⚠️ Window starts ~{days:.1f} days before retention cutoff. "
            f"Detail fetches for older conversations will 404.\n",
            file=sys.stderr,
        )

    rows: list[dict] = []
    listed = 0
    missing = 0
    t0 = time.time()
    for summary in list_conversations(start_unix, end_unix, api_key):
        listed += 1
        detail = fetch_detail(summary["conversation_id"], api_key)
        if detail is None:
            missing += 1
            continue
        if detail.get("status") not in ("done", "failed"):
            continue
        row = extract_attribution(detail)
        if row is not None:
            rows.append(row)
        if listed % 50 == 0:
            print(f"  …{listed} fetched", file=sys.stderr)
    elapsed = time.time() - t0
    print(
        f"# Fetched {listed} conversations in {elapsed:.1f}s "
        f"({missing} scrubbed/missing)\n",
        file=sys.stderr,
    )

    if args.trend:
        report = render_trend_report(
            rows,
            start_unix,
            end_unix,
            top=args.top,
        )
    else:
        report = render_report(
            label,
            rows,
            top=args.top,
            show_full=args.full,
        )
    print(report)

    if args.json_out:
        # JSON retains full UIDs — see Compliance/Privacy:
        # "Pasting the full 28-character UID is OK *only* in an opt-in body
        #  that other agents will need to look up by exact match."
        out = {
            "window_label": label,
            "start_unix": start_unix,
            "end_unix": end_unix,
            "rate_in_plan_per_min": COST_PER_MIN_IN_PLAN,
            "rate_overage_per_min": COST_PER_MIN_OVERAGE,
            "rate_burst_per_min": COST_PER_MIN_BURST,
            "retention_days": RETENTION_DAYS,
            "fetch_stats": {
                "listed": listed,
                "missing": missing,
                "elapsed_secs": elapsed,
            },
            "by_user": aggregate_by_user(rows),
            "cost_distribution": compute_cost_distribution(
                _user_costs(aggregate_by_user(rows))
            ),
            "conversations": rows,
        }
        if args.trend:
            buckets = _bucket_by_day(rows)
            out["by_day"] = {
                dk: {
                    "summary": _daily_summary(buckets.get(dk, [])),
                    "by_user": aggregate_by_user(buckets.get(dk, [])),
                }
                for dk in _all_day_keys(start_unix, end_unix)
            }
        with open(args.json_out, "w") as f:
            json.dump(out, f, indent=2, default=str)


if __name__ == "__main__":
    main()
