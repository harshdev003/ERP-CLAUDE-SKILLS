#!/usr/bin/env python3
"""Resolve the target week for yawar-create-weekly-cards.

Given today's date (or an override), work out which Monday the cards belong to, the
Sunday before it, the five Sunday-to-Thursday monitoring dates, the Sunday-to-Saturday
"week of" label, and the exact titles of every card in the roster, in creation order.

The rule for the target Monday:

  * Monday to Thursday  -> the Monday of the current week
  * Friday to Sunday    -> the following Monday

The routine runs on Sunday evening or Monday, so both land on the same Monday. A run on
a Friday or Saturday also prepares the coming week rather than the one that is ending.

Dates in titles are M/D with no zero padding (9/7, 10/1), matching the cards Yawar has
created by hand. The week label is Sunday-to-Saturday: "9/20-9/26" for the cycle that
starts Monday 9/21.

Two different weeks appear in the roster, and they are one week apart:

  * The monitoring cards are dated inside the week the cycle STARTS -- that work happens
    during the week.
  * The two Growth ROI cards carry the week that just CLOSED, because the sheet cannot be
    updated for a week until it ends. A card filed Monday 9/21 covers 9/13-9/19, matching
    the Monday insight note `data:report-growth-roi` posts for that same week.

So `week_label` is the cycle's own week and `report_week_label` is the one before it. Only
the Growth ROI titles use the latter.

Stdlib only. Prints JSON to stdout.

Usage:
  python3 resolve_week.py                       # today, [Cycle N] left as a placeholder
  python3 resolve_week.py --cycle 46            # render "[Cycle 46]" into titles
  python3 resolve_week.py --today 2026-09-27    # simulate a Sunday run
  python3 resolve_week.py --monday 2026-09-28   # force the target Monday
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta

# Roster keys and title patterns. Metadata (parent, priority, estimate, labels, project,
# description template) lives in ../references/roster.md; only the date math and the
# resulting title strings live here, so the two never disagree on a date.
MONITORING_PARENT = "Experiment/Data Monitoring"
SHEET_PARENT = "Sheet Update/Creation/Changes"
INVESTIGATION_PARENT = "Data Investigation"


def md(d: date) -> str:
    """M/D with no zero padding."""
    return f"{d.month}/{d.day}"


def target_monday(today: date) -> date:
    weekday = today.weekday()  # Monday = 0
    if weekday <= 3:
        return today - timedelta(days=weekday)
    return today + timedelta(days=7 - weekday)


def build(today: date, monday: date | None, cycle: int | None) -> dict:
    monday = monday or target_monday(today)
    if monday.weekday() != 0:
        raise SystemExit(f"--monday must be a Monday, got {monday.isoformat()} ({monday:%A})")

    sunday = monday - timedelta(days=1)
    saturday = monday + timedelta(days=5)
    monitoring_days = [sunday + timedelta(days=i) for i in range(5)]  # Sun..Thu
    week_label = f"{md(sunday)}-{md(saturday)}"

    # The Growth ROI cards report the week that closed the day before the target Monday,
    # and their "week-late" columns lag one week further back again.
    report_sunday = sunday - timedelta(days=7)
    report_saturday = saturday - timedelta(days=7)
    report_week_label = f"{md(report_sunday)}-{md(report_saturday)}"
    report_prev_week_label = f"{md(sunday - timedelta(days=14))}-{md(saturday - timedelta(days=14))}"
    cycle_tag = f"[Cycle {cycle}]" if cycle is not None else "[Cycle N]"

    cards: list[dict] = [
        {
            "key": "data-monitoring",
            "parent_stem": MONITORING_PARENT,
            "title": f"Data Monitoring {md(sunday)}",
            "date": sunday.isoformat(),
        }
    ]
    for d in monitoring_days:
        cards.append(
            {
                "key": "experiment-monitoring",
                "parent_stem": MONITORING_PARENT,
                "title": f"Experiment Monitoring {md(d)}",
                "date": d.isoformat(),
            }
        )
    cards.append(
        {
            "key": "growth-roi-aura",
            "parent_stem": SHEET_PARENT,
            "title": f"Update and report the Aura Growth ROI sheet (week of {report_week_label})",
        }
    )
    cards.append(
        {
            "key": "growth-roi-aurie",
            "parent_stem": SHEET_PARENT,
            "title": f"Update and report the Aurie Growth ROI sheet (week of {report_week_label})",
        }
    )
    cards.append(
        {
            "key": "viktor-weekly-review",
            "parent_stem": INVESTIGATION_PARENT,
            "title": f"Viktor weekly review {cycle_tag}",
        }
    )

    return {
        "today": today.isoformat(),
        "target_monday": monday.isoformat(),
        "sunday_before": sunday.isoformat(),
        "week_start": sunday.isoformat(),
        "week_end": saturday.isoformat(),
        "week_label": week_label,
        "week_closing": saturday.isoformat(),
        "report_week_start": report_sunday.isoformat(),
        "report_week_end": report_saturday.isoformat(),
        "report_week_label": report_week_label,
        "report_week_closing": report_saturday.isoformat(),
        "report_prev_week_label": report_prev_week_label,
        "monitoring_dates": [md(d) for d in monitoring_days],
        "cycle": cycle,
        "cycle_tag": cycle_tag,
        "parents": {
            MONITORING_PARENT: f"{MONITORING_PARENT} {cycle_tag}",
            SHEET_PARENT: f"{SHEET_PARENT} {cycle_tag}",
            INVESTIGATION_PARENT: f"{INVESTIGATION_PARENT} {cycle_tag}",
        },
        "cards": cards,
    }


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--today", type=date.fromisoformat, default=date.today(), help="override today's date (YYYY-MM-DD)")
    parser.add_argument("--monday", type=date.fromisoformat, default=None, help="force the target Monday (YYYY-MM-DD)")
    parser.add_argument("--cycle", type=int, default=None, help="Product/Design cycle number to render into [Cycle N] titles")
    args = parser.parse_args()

    print(json.dumps(build(args.today, args.monday, args.cycle), indent=2))


if __name__ == "__main__":
    main()
