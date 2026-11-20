#!/usr/bin/env python3
"""Resolve a time window for the data:report-aurie-usage skill.

Prints a JSON object with ``from``, ``to``, ``label``, and ``days_in`` so the
SKILL.md workflow can pipe the values straight into a Mixpanel ``Run-Query``
payload and the formatter without parsing dates twice.

Both ``from`` and ``to`` are **inclusive** ``YYYY-MM-DD`` strings — that's the
shape Mixpanel's ``dateRange.absolute`` expects. (Internally we still compute
half-open UTC boundaries for ``days_in``; only the emitted JSON is inclusive.)

The default window is ``last-7d-complete`` — the seven most recent *complete*
days (ending at 00:00 UTC today). This is what almost every "weekly Aurie
usage" question wants: yesterday and the six days before it, never today's
partial bucket.

Usage:
    python3 resolve_window.py                    # default: last-7d-complete
    python3 resolve_window.py --window today
    python3 resolve_window.py --window yesterday
    python3 resolve_window.py --window wtd
    python3 resolve_window.py --window mtd
    python3 resolve_window.py --window prev-month
    python3 resolve_window.py --window last-7d
    python3 resolve_window.py --window last-7d-complete
    python3 resolve_window.py --window last-30d
    python3 resolve_window.py --window last-30d-complete
    python3 resolve_window.py --weekly           # most recent complete Sun-Sat
    python3 resolve_window.py --from 2026-04-01 --to 2026-05-01

Stdlib only — no third-party dependencies.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone


WINDOWS = (
    "today",
    "yesterday",
    "wtd",
    "mtd",
    "prev-month",
    "last-7d",
    "last-7d-complete",
    "last-30d",
    "last-30d-complete",
)


def resolve(window: str | None, since: str | None, until: str | None, weekly: bool) -> dict:
    now = datetime.now(tz=timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    if since or until:
        if not (since and until):
            sys.exit("--from and --to must be provided together")
        start_dt = _parse_date(since)
        # CLI ``--to`` is inclusive (matches the emitted ``to`` field). Bump by
        # one day to the exclusive half-open boundary used internally.
        end_dt = _parse_date(until) + timedelta(days=1)
        label = f"{since} → {until}"
    elif weekly:
        # Most recent complete Sun-Sat week.
        # today_start.weekday(): Mon=0 … Sat=5, Sun=6.
        # Saturday of the most recent completed week:
        days_since_sat = (today_start.weekday() - 5) % 7
        if days_since_sat == 0:
            # Today is Saturday — the most recent completed week ended last Saturday.
            days_since_sat = 7
        sat_end = today_start - timedelta(days=days_since_sat - 1)  # midnight after that Sat
        sun_start = sat_end - timedelta(days=7)
        start_dt = sun_start
        end_dt = sat_end
        label = f"week {sun_start.strftime('%Y-%m-%d')} → {(sat_end - timedelta(days=1)).strftime('%Y-%m-%d')} (Sun–Sat)"
    elif window == "today":
        start_dt = today_start
        end_dt = now
        label = f"today ({start_dt.strftime('%Y-%m-%d')})"
    elif window == "yesterday":
        end_dt = today_start
        start_dt = today_start - timedelta(days=1)
        label = f"yesterday ({start_dt.strftime('%Y-%m-%d')})"
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
    elif window == "last-7d-complete" or window is None:
        end_dt = today_start
        start_dt = today_start - timedelta(days=7)
        label = (
            f"last 7 complete days "
            f"({start_dt.strftime('%Y-%m-%d')} → {(end_dt - timedelta(days=1)).strftime('%Y-%m-%d')})"
        )
    elif window == "last-30d":
        end_dt = now
        start_dt = now - timedelta(days=30)
        label = "last 30 days"
    elif window == "last-30d-complete":
        end_dt = today_start
        start_dt = today_start - timedelta(days=30)
        label = (
            f"last 30 complete days "
            f"({start_dt.strftime('%Y-%m-%d')} → {(end_dt - timedelta(days=1)).strftime('%Y-%m-%d')})"
        )
    elif window == "mtd":
        start_dt = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end_dt = now
        label = f"month to date ({start_dt.strftime('%B %Y')})"
    else:
        sys.exit(f"Unknown --window {window!r}. Valid: {', '.join(WINDOWS)}")

    if start_dt >= end_dt:
        sys.exit(f"Empty window: from {start_dt.isoformat()} >= to {end_dt.isoformat()}")

    span = end_dt - start_dt
    days_in = max(1, span.days + (1 if span.seconds else 0))

    # Inclusive last day. ``end_dt`` is the half-open boundary used to compute
    # ``days_in``; the emitted ``to`` is one microsecond earlier so it lands on
    # the actual last day of the window (Mixpanel's ``to`` is inclusive).
    inclusive_to_date = (end_dt - timedelta(microseconds=1)).strftime("%Y-%m-%d")

    return {
        "from": start_dt.strftime("%Y-%m-%d"),
        "to": inclusive_to_date,
        "label": label,
        "days_in": days_in,
    }


def _parse_date(s: str) -> datetime:
    try:
        return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        sys.exit(f"Bad date {s!r} — expected YYYY-MM-DD (UTC)")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--window", choices=WINDOWS, help="Preset time window")
    p.add_argument("--weekly", action="store_true", help="Most recent complete Sun-Sat week")
    p.add_argument("--from", dest="since", help="Start date (YYYY-MM-DD, UTC) — pairs with --to")
    p.add_argument("--to", dest="until", help="End date (YYYY-MM-DD, UTC, exclusive) — pairs with --from")
    args = p.parse_args()

    if args.weekly and args.window:
        sys.exit("--weekly and --window are mutually exclusive")

    result = resolve(args.window, args.since, args.until, args.weekly)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
