#!/usr/bin/env python3
"""
Report ElevenLabs Conversational AI cost for ONE specific user.

Point-lookup counterpart to the rank-N `eng:analyze-elevenlabs-cost-per-user`
skill. Walks `/v1/convai/conversations` over the window, fetches conversation
detail to read the `userId` dynamic variable IntelligenceService stamps on
each call, and sums up just this user's minutes (in-plan vs burst).

Per-call $ comes directly from `call_duration_secs / 60 × ELABS rate`:
$0.09/min in-plan, $0.19/min burst (both per Tools/ElevenLabs on the Business
plan). No credits→minutes derivation — calculation is `minutes × rate`,
full stop. Same source rates as the rank-N sibling.

**Two sources, split at the retention cutoff.** ElevenLabs purges
Conversational AI dynamic variables (including `userId`) after 10 days, so the
live walk can only attribute recent calls. The window is split at the first UTC
midnight after that cutoff:

  * whole days before the split come from the per-user daily cost store
    (`voice_11labs_agent` in `aura-main.aurie_cost.aurie_per_user_daily_cost`,
    written by eng:store-aurie-user-daily-cost) — a stored day with no row for
    the user is $0, a day the store has not written is PENDING, never $0;
  * everything from the split on (including today) comes from the live walk.

Each day is read from exactly one source. The store half needs GCP_KEY and
google-cloud-bigquery; without them it reports `unavailable`, named, and the
live half still runs.

Env:
  ELEVENLABS_ADMIN_API_KEY  (preferred) or ELEVENLABS_API_KEY  Required.
  GCP_KEY                   For windows reaching past the retention cutoff.

Usage:
  python3 report.py <UID>
  python3 report.py <UID> --window last-7d
  python3 report.py <UID> --window last-30d            # older days from the store
  python3 report.py <UID> --from 2026-05-20 --to 2026-05-27
  python3 report.py <UID> --no-by-bucket
  python3 report.py <UID> --json out.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

# Allow `from resolve_window import resolve` regardless of cwd
sys.path.insert(0, str(Path(__file__).resolve().parent))
from resolve_window import resolve  # noqa: E402

ELEVENLABS_API = "https://api.elevenlabs.io/v1"
LIST_PATH = "/convai/conversations"
DETAIL_PATH = "/convai/conversations/{conversation_id}"
COST_PER_MIN_IN_PLAN = 0.09
COST_PER_MIN_BURST = 0.19
RETENTION_DAYS = 10
PAGE_SIZE = 100
STORE_TABLE = "aura-main.aurie_cost.aurie_per_user_daily_cost"
# The ConvAI slice only — the same scope as the live walk. ElevenLabs TTS on LiveKit
# sessions is eng:report-livekit-user-cost's column; Intelligence Service TTS is
# booked on the `Other` row. tests/test_livekit_user_cost.py pins the split.
STORE_COLUMN = "voice_11labs_agent"


def split_point(now_unix: int) -> int:
    """First UTC midnight after the retention cutoff: store before, live from here."""
    cutoff = datetime.fromtimestamp(now_unix - RETENTION_DAYS * 86400, tz=timezone.utc)
    return int(datetime(cutoff.year, cutoff.month, cutoff.day, tzinfo=timezone.utc).timestamp()) + 86400


def store_dates(start_unix: int, end_unix: int, split_unix: int) -> list[str]:
    """UTC dates of the window that fall wholly before the split (read from the store)."""
    last = min(end_unix, split_unix) - 1
    if last < start_unix:
        return []
    d = datetime.fromtimestamp(start_unix, tz=timezone.utc).date()
    end = datetime.fromtimestamp(last, tz=timezone.utc).date()
    out = []
    while d <= end:
        out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _store_query():
    """bq.query — the store's single transport chokepoint, replaced in tests."""
    import bq
    return bq.query


def read_store(uid: str, dates: list[str], query) -> dict:
    """Sum voice_11labs_agent over `dates`; unwritten days are pending, never $0."""
    params = {"f": dates[0], "l": dates[-1]}
    stored = {str(r["date"]) for r in query(
        f"SELECT DISTINCT date FROM `{STORE_TABLE}` WHERE date BETWEEN DATE(@f) AND DATE(@l)",
        params=params)}
    rows = {str(r["date"]): float(r[STORE_COLUMN] or 0.0) for r in query(
        f"SELECT date, {STORE_COLUMN} FROM `{STORE_TABLE}` "
        f"WHERE user_id = @u AND date BETWEEN DATE(@f) AND DATE(@l)",
        params={**params, "u": uid})}
    pending = [d for d in dates if d not in stored]
    measured = [d for d in dates if d in stored]
    cost = sum(rows.get(d, 0.0) for d in measured)
    return {
        "status": "pending" if not measured else ("partial" if pending else "ok"),
        "dates": dates,
        "stored_days": len(measured),
        "pending_dates": pending,
        "cost_usd": round(cost, 4) if measured else None,
        "source": STORE_TABLE,
    }


def unavailable_store(dates: list[str], kind: str, err: Exception) -> dict:
    status = "unavailable" if kind in ("BQUnavailable", "ImportError", "ModuleNotFoundError") else "error"
    return {"status": status, "dates": dates, "stored_days": 0, "pending_dates": [],
            "cost_usd": None, "source": STORE_TABLE, "reason": f"{kind}: {err}"}


def _request(path: str, params: dict, api_key: str, *, ok_404: bool = False) -> dict | None:
    url = f"{ELEVENLABS_API}{path}"
    for attempt in range(5):
        try:
            r = requests.get(url, headers={"xi-api-key": api_key}, params=params, timeout=60)
        except requests.RequestException as e:
            if attempt == 4:
                sys.exit(f"ElevenLabs {path}: {e}")
            time.sleep(2 ** attempt)
            continue
        if r.status_code == 404 and ok_404:
            return None
        if r.status_code == 429 or 500 <= r.status_code < 600:
            time.sleep(2 ** attempt)
            continue
        if not r.ok:
            sys.exit(f"ElevenLabs {path}: HTTP {r.status_code}: {r.text[:300]}")
        return r.json()
    sys.exit(f"ElevenLabs {path}: rate-limited after 5 retries")


def list_conversations(
    start_unix: int, end_unix: int, api_key: str, *, user_id: str | None = None
):
    """Yield conversation summaries in [start_unix, end_unix]. Cursor-paginated.

    `user_id` is applied server-side, which is what makes this walk O(that user)
    rather than O(workspace voice volume). It does NOT remove the need for the
    caller's own per-detail uid check: ElevenLabs ignores unknown query params
    silently (a bogus param returns 200 with a full unfiltered page), so if this
    one is ever renamed the filter stops filtering without erroring. The caller
    keeps the client-side check as the correctness guard and reports when it
    starts rejecting — see `main()`.
    """
    cursor: str | None = None
    while True:
        params: dict = {
            "page_size": PAGE_SIZE,
            "call_start_after_unix": start_unix,
            "call_start_before_unix": end_unix,
        }
        if user_id:
            params["user_id"] = user_id
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
    return _request(DETAIL_PATH.format(conversation_id=conversation_id), {}, api_key, ok_404=True)


def _userid_from_detail(detail: dict | None) -> str | None:
    if not isinstance(detail, dict):
        return None
    cicd = detail.get("conversation_initiation_client_data") or {}
    dyn = cicd.get("dynamic_variables") or {}
    return detail.get("user_id") or dyn.get("userId")


def _row_from_detail(detail: dict, conversation_id: str) -> dict:
    md = detail.get("metadata") or {}
    charging = md.get("charging") or {}
    cicd = detail.get("conversation_initiation_client_data") or {}
    dyn = cicd.get("dynamic_variables") or {}
    return {
        "conversation_id": conversation_id,
        "start_unix": md.get("start_time_unix_secs") or 0,
        "duration_secs": float(md.get("call_duration_secs") or 0),
        "credits": float(md.get("cost") or charging.get("call_charge") or 0),
        "is_burst": bool(charging.get("is_burst")),
        "platform": dyn.get("platform"),
        "locale": dyn.get("locale"),
        "agent_id": detail.get("agent_id"),
        "agent_name": detail.get("agent_name"),
    }


def _duration_dollars(duration_secs: float, *, is_burst: bool) -> float:
    minutes = duration_secs / 60.0
    rate = COST_PER_MIN_BURST if is_burst else COST_PER_MIN_IN_PLAN
    return minutes * rate


def _fmt_money(x: float) -> str:
    if x == 0:
        return "—"
    return f"${x:,.2f}" if abs(x) >= 0.01 else f"${x:.4f}"


def _fmt_secs(secs: float) -> str:
    if secs == 0:
        return "—"
    m, s = divmod(int(round(secs)), 60)
    if m < 1:
        return f"{s}s"
    return f"{m}m {s:02d}s"


def _store_line(store: dict) -> str:
    span = f"{store['dates'][0]} → {store['dates'][-1]}"
    if store["status"] in ("unavailable", "error"):
        return f"| Cost store ({span}) | **{store['status']}** | not in total |"
    if store["cost_usd"] is None:
        return f"| Cost store ({span}) | **pending** | not yet stored |"
    extra = f", {len(store['pending_dates'])}d pending" if store["pending_dates"] else ""
    return f"| Cost store ({span}) | {_fmt_money(store['cost_usd']) if store['cost_usd'] else '$0.00'} | {store['stored_days']} stored day(s){extra} |"


def render_markdown(report: dict, show_by_bucket: bool) -> str:
    lines: list[str] = []
    uid_short = report["user_id"][:8] + "…"
    label = report["window"]["label"]
    days_in = report["window"]["days_in"]
    total_cost = report["total"]["cost_usd"]
    total_calls = report["total"]["call_count"]
    total_secs = report["total"]["duration_secs"]
    store = report["sources"]["store"]
    live = report["sources"]["live"]

    def _caveats():
        if report.get("notes"):
            lines.append("**Caveats:**")
            lines.extend(f"- {n}" for n in report["notes"])

    if total_cost is None:
        lines.append(f"## ElevenLabs cost — `{uid_short}` ({label}) — **{report['status']}**")
        lines.append("")
        _caveats()
        return "\n".join(lines).rstrip() + "\n"

    if total_cost == 0 and total_calls == 0 and (store is None or store["status"] == "ok"):
        lines.append(f"## ElevenLabs cost — `{uid_short}`  ({label})")
        lines.append("")
        lines.append("No Conversational AI cost for this user in this window.")
        lines.append("")
        _caveats()
        return "\n".join(lines).rstrip() + "\n"

    per_day = total_cost / max(days_in, 1)
    calls = f", {total_calls} live call{'s' if total_calls != 1 else ''}, {_fmt_secs(total_secs)}" if total_calls else ""
    lines.append(
        f"## ElevenLabs cost — `{uid_short}`  "
        f"({_fmt_money(total_cost) if total_cost else '$0.00'}{calls} over {days_in}d, "
        f"{_fmt_money(per_day) if per_day else '$0.00'}/day)"
    )
    lines.append("")

    if store is not None:
        lines.append("| Source | Cost | Notes |")
        lines.append("|---|---:|---|")
        lines.append(_store_line(store))
        if live["cost_usd"] is not None:
            since = live["from_iso"][:10]
            lines.append(f"| Live ConvAI walk (from {since}) | {_fmt_money(live['cost_usd']) if live['cost_usd'] else '$0.00'} | {live['call_count']} call(s) |")
        lines.append("")

    if show_by_bucket and (report["in_plan"]["call_count"] or report["burst"]["call_count"]):
        lines.append("| Bucket (live) | Calls | Minutes | Rate | Cost |")
        lines.append("|---|---:|---:|---:|---:|")
        for key, blabel in (("in_plan", "In-plan"), ("burst", "Burst")):
            b = report[key]
            if not b["call_count"]:
                continue
            mins = b["duration_secs"] / 60.0
            rate = COST_PER_MIN_IN_PLAN if key == "in_plan" else COST_PER_MIN_BURST
            lines.append(
                f"| {blabel} | {b['call_count']} | "
                f"{mins:,.2f} | ${rate:.2f}/min | "
                f"{_fmt_money(b['cost_usd'])} |"
            )
        lines.append("")

    _caveats()
    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("user_id", help="Firebase UID")
    p.add_argument("--window", help="Preset: mtd | prev-month | last-7d | last-30d | wtd | today | yesterday-pt | today-pt")
    p.add_argument("--from", dest="since", help="Custom start (YYYY-MM-DD, UTC)")
    p.add_argument("--to", dest="until", help="Custom end (YYYY-MM-DD, UTC, exclusive)")
    p.add_argument("--by-bucket", dest="by_bucket", action="store_true", default=True,
                   help="Show in-plan vs burst breakdown (default on)")
    p.add_argument("--no-by-bucket", dest="by_bucket", action="store_false",
                   help="Suppress per-bucket breakdown; show total only")
    p.add_argument("--json", dest="json_out", help="Write structured report JSON to this path")
    args = p.parse_args()

    api_key = os.environ.get("ELEVENLABS_ADMIN_API_KEY") or os.environ.get("ELEVENLABS_API_KEY")
    if not api_key:
        sys.exit("Missing ELEVENLABS_ADMIN_API_KEY (preferred) or ELEVENLABS_API_KEY. See CLAUDE.md → 'Token inventory'.")

    win = resolve(args.window, args.since, args.until)
    start_unix = int(datetime.strptime(win["from_iso"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp())
    end_unix = int(datetime.strptime(win["to_iso"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp())

    notes: list[str] = []
    now_unix = int(datetime.now(tz=timezone.utc).timestamp())
    split_unix = split_point(now_unix)
    split_iso = datetime.fromtimestamp(split_unix, tz=timezone.utc).strftime("%Y-%m-%d")

    print(f"# Resolving window: {win['label']} ({win['from']} → {win['to']}, {win['days_in']}d)", file=sys.stderr)

    # Store half: whole UTC days before the split. Never touched on a recent window.
    s_dates = store_dates(start_unix, end_unix, split_unix)
    store = None
    if s_dates:
        print(f"# Reading {len(s_dates)} pre-retention day(s) from the cost store…", file=sys.stderr)
        try:
            store = read_store(args.user_id, s_dates, _store_query())
        except Exception as e:  # noqa: BLE001 — classified and named, never dropped
            store = unavailable_store(s_dates, type(e).__name__, e)
            print(f"# ElevenLabs store half {store['status']}: {store['reason']}", file=sys.stderr)
        span = f"{s_dates[0]} → {s_dates[-1]}"
        if store["status"] in ("unavailable", "error"):
            notes.append(
                f"Days {span} are past ElevenLabs' 10-day retention and are read from the per-user cost store, "
                f"which is {store['status']} ({store['reason']}). Their cost is NOT in this figure."
            )
        else:
            notes.append(f"Days {span} are past ElevenLabs' 10-day retention: read from the per-user cost store (`{STORE_COLUMN}`).")
            if store["pending_dates"]:
                notes.append(
                    f"{len(store['pending_dates'])} of those day(s) are not in the store yet "
                    f"({', '.join(store['pending_dates'])}) — pending, not $0."
                )
        if datetime.fromtimestamp(start_unix, tz=timezone.utc).strftime("%H:%M:%S") != "00:00:00":
            notes.append("The store is daily, so the window's partial first day counts as a whole day.")

    # Live half: from the split (or the window start, if later) to the window end.
    live_start = max(start_unix, split_unix)
    walk = live_start < end_unix
    if walk:
        print(f"# Walking /convai/conversations from {split_iso if live_start == split_unix else win['from']}…", file=sys.stderr)

    in_plan_secs = 0.0
    burst_secs = 0.0
    in_plan_calls = 0
    burst_calls = 0
    matched: list[dict] = []
    seen_total = 0
    scrubbed = 0
    rejected = 0

    for c in (list_conversations(live_start, end_unix, api_key, user_id=args.user_id) if walk else ()):
        seen_total += 1
        cid = c.get("conversation_id")
        if not cid:
            continue
        detail = fetch_detail(cid, api_key)
        if detail is None:
            scrubbed += 1
            continue
        uid = _userid_from_detail(detail)
        if uid != args.user_id:
            rejected += 1
            continue
        row = _row_from_detail(detail, cid)
        matched.append(row)
        if row["is_burst"]:
            burst_secs += row["duration_secs"]
            burst_calls += 1
        else:
            in_plan_secs += row["duration_secs"]
            in_plan_calls += 1

    print(f"# Walked {seen_total} conversation{'s' if seen_total != 1 else ''} ({scrubbed} 404/scrubbed). Matched {len(matched)} for this user.", file=sys.stderr)

    # The list call was pre-filtered to this user server-side, so every candidate
    # should belong to them. Sustained rejections mean the param stopped filtering
    # (ElevenLabs ignores unknown params silently) — the walk is then still correct
    # via this same check, just back to O(workspace). Say so rather than slowing
    # down mutely.
    #
    # Measure against the conversations we actually got a verdict on, NOT seen_total.
    # A retention 404 increments seen_total but can never increment `rejected` (there
    # is no detail to read a userId from), so on a scrubbed-heavy window — any wide
    # one, since attribution only survives 10 days — the ratio against seen_total is
    # diluted by exactly the rows that carry no signal, and an ignored filter can sit
    # under the threshold and go unreported. That is this guard failing in the
    # direction it exists to catch, so the denominator has to exclude the scrubbed.
    decidable = seen_total - scrubbed
    server_filter_effective = not (decidable >= 25 and rejected > decidable // 2)
    if not server_filter_effective:
        notes.append(
            f"Server-side user_id filter appears to be ignored by ElevenLabs: {rejected} of {decidable} attributable conversations belonged to another user and were dropped by the client-side check ({scrubbed} more were scrubbed by retention and carry no userId either way). The cost figure is still correct, but the walk ran at O(workspace voice volume) and will be slow. Check whether /convai/conversations renamed its user_id parameter."
        )
        print(f"# WARNING: server-side user_id filter looks ineffective ({rejected}/{decidable} attributable rejected) — walk degraded to O(workspace).", file=sys.stderr)

    in_plan_cost = _duration_dollars(in_plan_secs, is_burst=False)
    burst_cost = _duration_dollars(burst_secs, is_burst=True)
    live_cost = in_plan_cost + burst_cost
    total_calls = in_plan_calls + burst_calls
    total_secs = in_plan_secs + burst_secs

    if scrubbed:
        notes.append(
            f"{scrubbed} conversation{'s' if scrubbed != 1 else ''} 404'd on detail fetch inside the retention window. Per-user attribution unavailable for those calls."
        )

    store_cost = (store or {}).get("cost_usd")
    store_ok = store is None or store["status"] == "ok"
    measured_any = walk or store_cost is not None
    total_cost = live_cost + (store_cost or 0.0)
    status = "ok" if store_ok else ("pending" if not walk and store_cost is None and store["status"] == "pending" else "partial")
    if store and store["status"] in ("unavailable", "error") and not walk:
        status = store["status"]

    report = {
        "user_id": args.user_id,
        "window": win,
        "scanned_conversations": seen_total,
        "scrubbed_conversations": scrubbed,
        "server_side_user_filter": True,
        "server_side_user_filter_effective": server_filter_effective,
        "rejected_by_uid_check": rejected,
        "status": status,
        "total": {
            # null only when nothing in the window was measured
            "cost_usd": round(total_cost, 4) if measured_any else None,
            "call_count": total_calls,
            "duration_secs": total_secs,
        },
        "sources": {
            "live": {
                "from_iso": datetime.fromtimestamp(live_start, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if walk else None,
                "cost_usd": round(live_cost, 4) if walk else None,
                "call_count": total_calls,
            },
            "store": store,
        },
        "in_plan": {
            "cost_usd": round(in_plan_cost, 4),
            "call_count": in_plan_calls,
            "duration_secs": in_plan_secs,
        },
        "burst": {
            "cost_usd": round(burst_cost, 4),
            "call_count": burst_calls,
            "duration_secs": burst_secs,
        },
        "calls": matched,
        "notes": notes,
    }
    print(render_markdown(report, show_by_bucket=args.by_bucket))

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(report, indent=2))
        print(f"# Wrote structured report to {args.json_out}", file=sys.stderr)
    if store is not None and store["status"] in ("unavailable", "error"):
        sys.exit(2)


if __name__ == "__main__":
    main()
