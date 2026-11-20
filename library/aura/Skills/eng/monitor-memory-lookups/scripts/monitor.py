#!/usr/bin/env python3
"""Daily check that coach replies still run memory lookups (PROD-8674).

Share per UTC day = findSimilarInMemory `Retrieving memory` log lines (one per tool call, GCP
Cloud Logging) / coach-prompter calls in the llmCall ledger (BigQuery `aura-main.llm.llm_call`).
Counts only: no ids, no text.

Alerts when yesterday's share is under half the median of the 7 days before it on a day with at
least MIN_CALLS coach calls, or when such a day has no lookup at all. A rise never alerts.

Exit codes: 0 no alert, 1 alert, 3 unmeasured (a read failed or came back incomplete), 2 bad args.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bq  # noqa: E402
import gcloud_sa  # noqa: E402

LOG_PROJECT = "firebase-auratech16"
# The tool logs this once per call, before it searches. A rename of the message, the component or
# the tool itself silently zeroes the numerator, so it must change here in the same release.
LOOKUP_FILTER = ('jsonPayload.component="findSimilarInMemory" '
                 'jsonPayload.message="Retrieving memory"')
COACH_PROMPTER = "coach-prompter"
DAYS = 8
MIN_CALLS = 5000
LOG_LIMIT = 200_000

LEDGER_SQL = """
SELECT CAST(DATE(createdAt) AS STRING) AS day, COUNT(*) AS calls
FROM `aura-main.llm.llm_call`
WHERE createdAt >= TIMESTAMP(@start) AND createdAt < TIMESTAMP(@end)
  AND prompterName = @prompter
GROUP BY day
"""


class Unmeasured(Exception):
    """A read failed or was cut short, so a count would be a guess."""


def window(today: dt.date) -> list[str]:
    """The last DAYS full UTC days, oldest first; the last one is yesterday."""
    return [(today - dt.timedelta(days=n)).isoformat() for n in range(DAYS, 0, -1)]


def lookups_by_day(start: str, end: str, run=gcloud_sa.run) -> dict[str, int]:
    flt = f'{LOOKUP_FILTER} timestamp>="{start}T00:00:00Z" timestamp<"{end}T00:00:00Z"'
    try:
        out = run(["logging", "read", flt, "--project", LOG_PROJECT, "--limit", str(LOG_LIMIT),
                   "--format", "json(timestamp)"], capture_output=True, text=True, encoding="utf-8",
                  errors="replace")
    except OSError as e:
        raise Unmeasured(f"gcloud could not run: {e}") from e
    if out.returncode:
        raise Unmeasured(f"logging read failed as {gcloud_sa.credential()}: "
                         f"{(out.stderr or '').strip()[-200:]}")
    try:
        entries = json.loads(out.stdout or "[]")
    except ValueError as e:
        raise Unmeasured(f"logging read returned unreadable output: {e}") from e
    if not isinstance(entries, list):
        raise Unmeasured("logging read returned something other than a list of entries")
    if len(entries) >= LOG_LIMIT:
        raise Unmeasured(f"logging read hit its {LOG_LIMIT:,} line limit; the count is incomplete")
    counts: dict[str, int] = {}
    for e in entries:
        day = str(e.get("timestamp", ""))[:10]
        counts[day] = counts.get(day, 0) + 1
    return counts


def calls_by_day(start: str, end: str, query=bq.query) -> dict[str, int]:
    try:
        rows = query(LEDGER_SQL, params={"start": start, "end": end, "prompter": COACH_PROMPTER})
    except Exception as e:  # noqa: BLE001 -- any failed read is unmeasured, never a drop
        raise Unmeasured(f"ledger read failed: {e}") from e
    try:
        return {r["day"]: int(r["calls"]) for r in rows}
    except (KeyError, TypeError, ValueError) as e:
        raise Unmeasured(f"ledger read returned unexpected rows: {e}") from e


def evaluate(days: list[str], calls: dict[str, int], lookups: dict[str, int]) -> dict:
    """Apply the rule to the window. Pure: every input is a count."""
    yesterday = days[-1]
    y_calls, y_lookups = calls.get(yesterday, 0), lookups.get(yesterday, 0)
    if y_calls == 0:
        raise Unmeasured(f"the ledger has no {COACH_PROMPTER} calls on {yesterday}")
    prior = [lookups.get(d, 0) / calls[d] for d in days[:-1] if calls.get(d)]
    median = statistics.median(prior) if prior else None
    share = y_lookups / y_calls
    reason = None
    if y_calls >= MIN_CALLS:
        if y_lookups == 0:
            reason = "no memory lookups"
        elif median and share < median / 2:
            reason = "under half the prior 7-day median"
    return {"day": yesterday, "calls": y_calls, "lookups": y_lookups, "share": share,
            "median": median, "prior_days": len(prior), "alert": reason is not None,
            "reason": reason}


def _pct(x):
    return "n/a" if x is None else f"{x:.2%}"


def summary(v: dict) -> str:
    return (f"{v['day']}: {_pct(v['share'])} of coach calls ran a memory lookup "
            f"({v['lookups']:,}/{v['calls']:,}); prior 7-day median {_pct(v['median'])} "
            f"over {v['prior_days']} days")


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--today", help="UTC date to run as (YYYY-MM-DD); yesterday is the day judged")
    ap.add_argument("--json", action="store_true", help="print the verdict as JSON")
    a = ap.parse_args(argv)
    try:
        today = dt.date.fromisoformat(a.today) if a.today else dt.datetime.now(dt.timezone.utc).date()
    except ValueError:
        ap.error("--today must be YYYY-MM-DD")
    days = window(today)
    try:
        lookups = lookups_by_day(days[0], today.isoformat())
        calls = calls_by_day(days[0], today.isoformat())
        v = evaluate(days, calls, lookups)
    except Exception as e:  # noqa: BLE001 -- exit 1 means a drop, so nothing else may reach it
        print(f"UNMEASURED {days[-1]}: {e}")
        return 3
    if a.json:
        print(json.dumps(v))
    elif v["alert"]:
        print(f"ALERT {v['reason']}: {summary(v)}")
    else:
        print(f"ok {summary(v)}")
    return 1 if v["alert"] else 0


if __name__ == "__main__":
    sys.exit(main())
