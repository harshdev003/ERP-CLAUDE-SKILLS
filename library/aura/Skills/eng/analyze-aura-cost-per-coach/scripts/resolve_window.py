#!/usr/bin/env python3
"""
Resolve a time window for the Aura cost / listens skill family.

Canonical copy: plugins/eng/skills/analyze-listens/scripts/resolve_window.py.
Every other copy is byte-identical to it (skills are self-contained, so each
ships its own), and tests/test_resolve_window_copies.py fails when one is not.
Edit the canonical, then copy it over every sibling in the same PR.

Prints a JSON object with `from`, `to`, `label`, `from_iso`, `to_iso`, and
`days_in` so the SKILL.md workflow can pipe the values into a Mixpanel
`Run-Query` payload (or, for sibling copies of this script, into the
PaymentService aggregate call) without parsing dates twice.

All boundaries are UTC, half-open: `from <= t < to`. ISO timestamps are
emitted with Z-suffix (RFC 3339 canonical for UTC), sub-second precision
dropped for stability across runs.

Window presets:

- UTC-aligned: `today`, `wtd`, `mtd`, `prev-month`, `last-7d`, `last-30d`
- PT-aligned (DST-safe via `zoneinfo`): `yesterday-pt`, `today-pt`
- Custom: `--from YYYY-MM-DD --to YYYY-MM-DD` (UTC, half-open)

Usage:
    python3 resolve_window.py --window mtd
    python3 resolve_window.py --window today
    python3 resolve_window.py --window yesterday-pt
    python3 resolve_window.py --window today-pt
    python3 resolve_window.py --window wtd
    python3 resolve_window.py --window prev-month
    python3 resolve_window.py --window last-7d
    python3 resolve_window.py --window last-30d
    python3 resolve_window.py --from 2026-04-01 --to 2026-05-01

Stdlib only — no third-party dependencies.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


WINDOWS = (
    "today", "wtd", "mtd", "prev-month", "last-7d", "last-30d",
    # PT-aligned daily presets (DST-safe via zoneinfo)
    "yesterday-pt", "today-pt",
)

PT = ZoneInfo("America/Los_Angeles")


def resolve(window: str | None, since: str | None, until: str | None) -> dict:
    now = datetime.now(tz=timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    if since or until:
        if not (since and until):
            sys.exit("--from and --to must be provided together")
        start_dt = _parse_date(since)
        end_dt = _parse_date(until)
        label = f"{since} → {until}"
    elif window == "today":
        start_dt = today_start
        end_dt = now
        label = f"today ({start_dt.strftime('%Y-%m-%d')})"
    elif window == "yesterday-pt":
        now_pt = datetime.now(tz=PT)
        today_pt_midnight = now_pt.replace(hour=0, minute=0, second=0, microsecond=0)
        yesterday_pt_midnight = today_pt_midnight - timedelta(days=1)
        start_dt = yesterday_pt_midnight.astimezone(timezone.utc)
        end_dt = today_pt_midnight.astimezone(timezone.utc)
        label = f"yesterday Pacific ({yesterday_pt_midnight.strftime('%Y-%m-%d')})"
    elif window == "today-pt":
        now_pt = datetime.now(tz=PT)
        today_pt_midnight = now_pt.replace(hour=0, minute=0, second=0, microsecond=0)
        start_dt = today_pt_midnight.astimezone(timezone.utc)
        end_dt = now
        label = f"today Pacific ({today_pt_midnight.strftime('%Y-%m-%d')})"
    elif window == "wtd":
        # Monday-anchored week
        start_dt = today_start - timedelta(days=today_start.weekday())
        end_dt = now
        label = f"week to date (from {start_dt.strftime('%Y-%m-%d')})"
    elif window == "prev-month":
        first_of_this = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        prev_any_day = first_of_this - timedelta(days=1)
        start_dt = prev_any_day.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end_dt = first_of_this
        label = f"previous month ({start_dt.strftime('%B %Y')})"
    elif window == "last-7d":
        end_dt = now
        start_dt = now - timedelta(days=7)
        label = "last 7 days"
    elif window == "last-30d":
        end_dt = now
        start_dt = now - timedelta(days=30)
        label = "last 30 days"
    elif window == "mtd" or window is None:
        start_dt = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end_dt = now
        label = f"month to date ({start_dt.strftime('%B %Y')})"
    else:
        sys.exit(f"Unknown --window {window!r}. Valid: {', '.join(WINDOWS)}")

    if start_dt >= end_dt:
        sys.exit(f"Empty window: from {start_dt.isoformat()} >= to {end_dt.isoformat()}")

    days_in = max(1, (end_dt - start_dt).days + (1 if (end_dt - start_dt).seconds else 0))

    return {
        "from": start_dt.strftime("%Y-%m-%d"),
        "to": end_dt.strftime("%Y-%m-%d"),
        "from_iso": _iso_z(start_dt),
        "to_iso": _iso_z(end_dt),
        "label": label,
        "days_in": days_in,
    }


def _parse_date(s: str) -> datetime:
    try:
        return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        sys.exit(f"Bad date {s!r} — expected YYYY-MM-DD (UTC)")


def _iso_z(dt: datetime) -> str:
    """Format a UTC datetime as `YYYY-MM-DDTHH:MM:SSZ`. Drops sub-seconds."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--window", choices=WINDOWS, help="Preset time window")
    p.add_argument("--from", dest="since", help="Start date (YYYY-MM-DD, UTC) — pairs with --to")
    p.add_argument("--to", dest="until", help="End date (YYYY-MM-DD, UTC, exclusive) — pairs with --from")
    args = p.parse_args()

    result = resolve(args.window, args.since, args.until)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
