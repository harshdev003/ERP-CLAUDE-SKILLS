#!/usr/bin/env python3
"""Compute per-day voice usage vs the backend daily/weekly limits.

Mirrors IntelligenceService `getLimits` semantics:
- buckets `createdAt` by the user's timezone-local day,
- week starts Sunday in the user's tz,
- strict `>` against the limit (the gate blocks the call that crosses it),
- usage is recorded at call END with full duration (rows as-is).
"""

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo

# Last-resort fallback only. Prefer --config-js (parses the live defaults from
# IntelligenceService src/config.js) or explicit --daily-ms / --weekly-ms.
DAILY_MS_DEFAULT = 5_400_000      # 1.5h
WEEKLY_MS_DEFAULT = 48_600_000    # 13.5h


def parse_config_js(path):
    """Extract the voice limit defaults from IntelligenceService src/config.js.

    Matches the `|| <number>` fallback on the `dailyMs:` / `weeklyMs:` lines,
    e.g. `dailyMs: parseInt(DAILY_LIMIT_VOICE, 10) || 5400000,`.
    Returns (dailyMs, weeklyMs); either may be None if not found.

    NOTE: this reads the code default. If the deployed environment overrides
    DAILY_LIMIT_VOICE / WEEKLY_LIMIT_VOICE, pass the real value via the explicit
    flags instead — no endpoint exposes the running numbers.
    """
    txt = open(path).read()
    daily = re.search(r"dailyMs:[^\n]*?\|\|\s*(\d+)", txt)
    weekly = re.search(r"weeklyMs:[^\n]*?\|\|\s*(\d+)", txt)
    return (int(daily.group(1)) if daily else None,
            int(weekly.group(1)) if weekly else None)


def _hms(ms: int) -> str:
    s = round(ms / 1000)
    return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"


def _rows_from(payload):
    if isinstance(payload, list):
        return payload
    for key in ("result", "results", "usage", "data"):
        v = payload.get(key) if isinstance(payload, dict) else None
        if isinstance(v, list):
            return v
    raise ValueError("Could not find a usage row list in the JSON payload")


def _week_start(d: date) -> date:
    # Backend weekly window: Sunday 00:00 in the user's tz. Postgres
    # DATE_TRUNC('week') is Monday-based, so the backend shifts +1 day before
    # truncating and -1 day after; (weekday() + 1) % 7 is the same walk-back.
    return d - timedelta(days=(d.weekday() + 1) % 7)  # Monday=0 .. Sunday=6


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    ap = argparse.ArgumentParser()
    ap.add_argument("--usage", required=True, help="JSON file from POST /ai/usage/list/:userId")
    ap.add_argument("--timezone", default="America/New_York", help="IANA tz (backend default America/New_York)")
    ap.add_argument("--daily-ms", type=int, default=None,
                    help="explicit daily limit (ms); overrides --config-js")
    ap.add_argument("--weekly-ms", type=int, default=None,
                    help="explicit weekly limit (ms); overrides --config-js")
    ap.add_argument("--config-js", default=None,
                    help="path to IntelligenceService src/config.js — parse the live "
                         "dailyMs/weeklyMs defaults so limits track the latest config")
    ap.add_argument("--gate-active-date", default=None,
                    help="YYYY-MM-DD local date the user's app reached v1.20.0+ (gate live)")
    ap.add_argument("--modal-json", default=None,
                    help='JSON map {"YYYY-MM-DD": count} of AI Usage Limit Modal Seen events')
    ap.add_argument("--user-id", default=None)
    ap.add_argument("--out-json", default=None)
    args = ap.parse_args()

    # Resolve limits: explicit flag > --config-js (live defaults) > fallback const.
    cfg_daily = cfg_weekly = None
    if args.config_js:
        cfg_daily, cfg_weekly = parse_config_js(args.config_js)
    daily_limit = args.daily_ms if args.daily_ms is not None else (
        cfg_daily if cfg_daily is not None else DAILY_MS_DEFAULT)
    weekly_limit = args.weekly_ms if args.weekly_ms is not None else (
        cfg_weekly if cfg_weekly is not None else WEEKLY_MS_DEFAULT)

    def _src(flag, cfg):
        if flag is not None:
            return "flag"
        if cfg is not None:
            return "config.js"
        return "fallback-default"
    limit_source = f"daily<-{_src(args.daily_ms, cfg_daily)}, weekly<-{_src(args.weekly_ms, cfg_weekly)}"

    tz = ZoneInfo(args.timezone)
    payload = json.load(open(args.usage))
    rows = _rows_from(payload)

    modal = {}
    if args.modal_json:
        modal = json.load(open(args.modal_json))
    gate_date = date.fromisoformat(args.gate_active_date) if args.gate_active_date else None

    # Per-local-day aggregation (voice only, non-deleted, numeric duration)
    per_day_ms = defaultdict(int)
    per_day_calls = defaultdict(int)
    for r in rows:
        if r.get("mode") and r.get("mode") != "voice":
            continue
        if r.get("deletedAt"):
            continue
        dur = r.get("duration")
        if not isinstance(dur, (int, float)):
            continue
        ts = r.get("createdAt")
        if not ts:
            continue
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(tz)
        d = dt.date()
        per_day_ms[d] += int(dur)
        per_day_calls[d] += 1

    # Union usage days with modal-seen days so blocked-but-no-usage days
    # (the strongest "gate worked" signal) still appear.
    modal_days = {date.fromisoformat(k) for k in modal}
    days = sorted(set(per_day_ms) | modal_days)
    # weekly-to-date: sum of all days in same week up to and including day d
    week_to_date = {}
    for d in days:
        ws = _week_start(d)
        week_to_date[d] = sum(per_day_ms[x] for x in days if ws <= x <= d)

    table = []
    for d in days:
        daily = per_day_ms[d]
        wtd = week_to_date[d]
        daily_hit = daily > daily_limit
        weekly_hit = wtd > weekly_limit
        gated = (gate_date is not None and d >= gate_date)
        row = {
            "date": d.isoformat(),
            "calls": per_day_calls[d],
            "dailyMs": daily,
            "daily": _hms(daily),
            "dailyHit": daily_hit,
            "weeklyToDateMs": wtd,
            "weeklyToDate": _hms(wtd),
            "weeklyHit": weekly_hit,
            "wouldBlock": daily_hit or weekly_hit,
            "gateActive": gated,
            "modalSeen": int(modal.get(d.isoformat(), 0)),
        }
        table.append(row)

    # ---- Render ----
    uid = (args.user_id[:8] + "…") if args.user_id else "(user)"
    print(f"## Usage-limit analysis — {uid}")
    print(f"tz={args.timezone} · week=Sun-start · "
          f"daily={_hms(daily_limit)} · weekly={_hms(weekly_limit)} ({limit_source})"
          + (f" · gate live {args.gate_active_date}" if gate_date else ""))
    print()
    print("| Date | Calls | Daily used | Daily Limit Reached | Week-to-date | Weekly Limit Reached | Gate live | Usage Limit Modal Seen Count |")
    print("|------|-------|------------|---------------------|--------------|----------------------|-----------|------------------------------|")
    for r in table:
        print(f"| {r['date']} | {r['calls']} | {r['daily']} | "
              f"{'✅' if r['dailyHit'] else '—'} | {r['weeklyToDate']} | "
              f"{'✅' if r['weeklyHit'] else '—'} | "
              f"{'✅' if r['gateActive'] else '✗'} | {r['modalSeen'] or '—'} |")

    would = [r for r in table if r["wouldBlock"]]
    unrestricted = [r for r in would if not r["gateActive"]]
    enforced = [r for r in would if r["gateActive"]]
    total_ms = sum(per_day_ms.values())
    print()
    print("### Summary")
    print(f"- Days with usage: **{len(days)}** · total voice **{_hms(total_ms)}**")
    print(f"- Days the limit would trigger (daily or weekly): **{len(would)}** "
          f"({', '.join(r['date'] for r in would) or 'none'})")
    print(f"- …of those, gate was **live**: **{len(enforced)}** "
          f"(modal-seen total {sum(r['modalSeen'] for r in enforced)})")
    print(f"- …gate **not live** (unrestricted overage): **{len(unrestricted)}** "
          f"({', '.join(r['date'] for r in unrestricted) or 'none'})")
    first_weekly = next((r['date'] for r in table if r['weeklyHit']), None)
    print(f"- First weekly-limit breach: **{first_weekly or 'never'}**")

    if args.out_json:
        json.dump({"rows": table, "totalMs": total_ms}, open(args.out_json, "w"), indent=2)


if __name__ == "__main__":
    main()
