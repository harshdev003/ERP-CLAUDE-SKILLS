#!/usr/bin/env python3
"""Harvest claude.ai subscription usage into `aura-main.anthropic_usage`.

One BigQuery day-partition per calendar day, replaced wholesale (WRITE_TRUNCATE)
so a re-run is idempotent and a late-arriving revision simply overwrites.

Two facts drive the defaults:

* **The export lags by a day.** Spend data "refreshes daily and has a one-day
  delay", so today's partition would be partial. `--through` defaults to
  yesterday (UTC) and the script refuses a future end date.
* **Late revisions happen.** Each run re-pulls a trailing window (default 7
  days) rather than only new days, so a day that settled after first harvest is
  corrected rather than frozen wrong.

Empty days are real (weekends exist) and are written as an explicit zero-row
partition, which is different from *not harvested*. What is never written is a
partition derived from a failed read — auth and shape errors raise.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import bq  # noqa: E402
from claude_admin import (  # noqa: E402
    ClaudeAdmin, ClaudeAdminAuthError, ClaudeAdminError,
    parse_members_csv, parse_spend_csv,
)

PROJECT = "aura-main"
DATASET = "anthropic_usage"
USAGE_TABLE = f"{PROJECT}.{DATASET}.daily_user_usage"
SEAT_TABLE = f"{PROJECT}.{DATASET}.member_seat"
LOG_TABLE = f"{PROJECT}.{DATASET}.harvest_log"

# The first day the export has any data for this org. Probing back to 2024
# returned zero rows before this date, so a backfill that starts earlier is
# just wasted round-trips, not missing history.
EARLIEST_DATA_DATE = "2026-03-02"

USAGE_SCHEMA = [
    ("usage_date", "DATE"), ("user_email", "STRING"), ("account_uuid", "STRING"),
    ("user_id", "STRING"), ("product", "STRING"), ("model", "STRING"),
    ("slack_channel_id", "STRING"), ("teams_channel_id", "STRING"),
    ("requests", "INT64"), ("prompt_tokens", "INT64"), ("completion_tokens", "INT64"),
    ("uncached_input_tokens", "INT64"), ("cache_read_tokens", "INT64"),
    ("cache_write_5m_tokens", "INT64"), ("cache_write_1h_tokens", "INT64"),
    ("web_search_count", "INT64"), ("net_spend_usd", "NUMERIC"),
    ("gross_spend_usd", "NUMERIC"), ("harvested_at", "TIMESTAMP"),
]
USAGE_ORDER = [c for c, _ in USAGE_SCHEMA]

SEAT_SCHEMA = [
    ("as_of_date", "DATE"), ("user_email", "STRING"), ("display_name", "STRING"),
    ("role", "STRING"), ("status", "STRING"), ("seat_tier", "STRING"),
    ("harvested_at", "TIMESTAMP"),
]
SEAT_ORDER = [c for c, _ in SEAT_SCHEMA]

# Written for every day we successfully read, including days with zero usage.
#
# This table exists because writing a zero-row partition to `daily_user_usage`
# achieves nothing observable: a partition holding no rows is indistinguishable,
# to every query, from a partition that was never written. So "we harvested this
# day and there was no activity" cannot be a statement made *in* the fact table
# — it has to be asserted somewhere that has a row either way.
LOG_SCHEMA = [
    ("harvest_date", "DATE"), ("rows_loaded", "INT64"), ("tokens", "INT64"),
    ("users", "INT64"), ("source", "STRING"), ("harvested_at", "TIMESTAMP"),
]
LOG_ORDER = [c for c, _ in LOG_SCHEMA]


def record_harvest(day: str, summary: dict, source: str, stamp: str) -> int:
    """Assert that `day` was read. Idempotent per day, like the fact partitions."""
    row = {
        "harvest_date": day,
        "rows_loaded": summary.get("rows", 0),
        "tokens": summary.get("tokens", 0),
        "users": summary.get("users", 0),
        "source": source,
        "harvested_at": stamp,
    }
    return bq.load_partition(
        LOG_TABLE, _rows_to_csv([row], LOG_ORDER), LOG_SCHEMA, day, PROJECT
    )


def _utc_today() -> dt.date:
    return dt.datetime.now(dt.timezone.utc).date()


def _rows_to_csv(rows: list, order: list) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    for r in rows:
        w.writerow([r.get(c, "") for c in order])
    return buf.getvalue().encode("utf-8")


def harvest_day(api: ClaudeAdmin, day: str, stamp: str, *, dry_run: bool = False) -> dict:
    """Fetch and load one day. Returns a summary dict.

    Any read failure propagates. The load only happens on a successful parse, so
    a partition is never replaced with the product of a broken read.
    """
    raw = api.spend_report(day, day)
    rows = parse_spend_csv(raw)          # raises on a shape change
    for r in rows:
        r["usage_date"] = day
        r["harvested_at"] = stamp
    tokens = sum(r["prompt_tokens"] + r["completion_tokens"] for r in rows)
    spend = sum(float(r["net_spend_usd"]) for r in rows)
    summary = {
        "date": day, "rows": len(rows), "tokens": tokens,
        "billed_usd": round(spend, 2),
        "users": len({r["user_email"] for r in rows if r["user_email"]}),
    }
    if dry_run:
        summary["loaded"] = None
        return summary
    summary["loaded"] = bq.load_partition(
        USAGE_TABLE, _rows_to_csv(rows, USAGE_ORDER), USAGE_SCHEMA, day, PROJECT
    )
    record_harvest(day, summary, "api", stamp)
    return summary


def harvest_members(api: ClaudeAdmin, day: str, stamp: str, *, dry_run: bool = False) -> dict:
    rows = parse_members_csv(api.members())
    if not rows:
        raise ClaudeAdminError("members export returned no rows — refusing to write an empty seat snapshot")
    for r in rows:
        r["as_of_date"] = day
        r["harvested_at"] = stamp
    tiers = {}
    for r in rows:
        tiers[r["seat_tier"]] = tiers.get(r["seat_tier"], 0) + 1
    out = {"date": day, "members": len(rows), "tiers": tiers}
    if dry_run:
        out["loaded"] = None
        return out
    out["loaded"] = bq.load_partition(
        SEAT_TABLE, _rows_to_csv(rows, SEAT_ORDER), SEAT_SCHEMA, day, PROJECT
    )
    return out


def daterange(start: dt.date, end: dt.date):
    d = start
    while d <= end:
        yield d
        d += dt.timedelta(days=1)


def find_gaps(since: dt.date, through: dt.date) -> dict:
    """Which days in [since, through] were never harvested. Reads BigQuery only.

    Deliberately needs **no session key**. The thing most likely to go wrong with
    this pipeline is the cookie expiring and nobody noticing, and a staleness
    check that itself depends on the cookie cannot report that. So this answers
    "is the warehouse current" from the warehouse alone.

    Reads `harvest_log`, not the fact table. A genuinely idle day writes no usage
    rows, so asking the fact table which days it holds cannot tell an idle
    Saturday from a day the harvest never ran — it would report every quiet
    weekend as a gap and hide a real one behind a busy neighbour.
    """
    rows = bq.query(
        f"SELECT DISTINCT harvest_date AS d FROM `{LOG_TABLE}` "
        "WHERE harvest_date BETWEEN DATE(@s) AND DATE(@e)",
        PROJECT, params={"s": since.isoformat(), "e": through.isoformat()},
    )
    have = {r["d"].isoformat() if hasattr(r["d"], "isoformat") else str(r["d"])
            for r in rows}
    want = [d.isoformat() for d in daterange(since, through)]
    missing = [d for d in want if d not in have]
    return {"want": len(want), "have": len(have), "missing": missing,
            "latest": max(have) if have else None}


def cmd_check(a) -> int:
    """Report freshness and gaps. Exit 1 if stale beyond --max-age-days.

    Why a lapse is recoverable rather than fatal: the export is a re-readable
    archive back to the first data day, not a decaying log. Any day missed while
    the cookie was dead can be fetched later with `--since`, and the partition
    replace makes that idempotent. So the cost of an expired cookie is a delay,
    never lost history — provided somebody notices, which is what this mode is
    for.
    """
    today = _utc_today()
    through = today - dt.timedelta(days=1)
    since = (dt.date.fromisoformat(a.since) if a.since
             else through - dt.timedelta(days=a.window - 1))
    g = find_gaps(since, through)
    print(f"window {since} → {through}: {g['have']}/{g['want']} days present")
    print(f"latest harvested day: {g['latest'] or 'NONE'}")
    if g["missing"]:
        show = g["missing"][:10]
        more = "" if len(g["missing"]) <= 10 else f" (+{len(g['missing'])-10} more)"
        print(f"missing {len(g['missing'])} day(s): {', '.join(show)}{more}")
    if not g["latest"]:
        print("STALE: nothing harvested in this window. Refresh "
              "CLAUDE_ADMIN_SESSION_KEY and re-run with --since.", file=sys.stderr)
        return 1
    age = (through - dt.date.fromisoformat(g["latest"])).days
    print(f"lag behind settled data: {age} day(s)")

    # Two independent ways for this dataset to be wrong, and a check that tests
    # only one of them is half a monitor. Lag catches "the harvest stopped
    # running"; missing days catch "the harvest has holes" — a run that died
    # mid-window, or days nobody ever backfilled. Reporting the holes but
    # exiting 0 because the newest day happens to be present is exactly the
    # silent-success failure this mode exists to prevent.
    bad = False
    if age > a.max_age_days:
        print(f"STALE: latest day is {age} days behind (limit {a.max_age_days}). "
              f"The session key has most likely expired — refresh it and re-run "
              f"`--since {g['latest']}` to close the gap.", file=sys.stderr)
        bad = True
    if g["missing"]:
        print(f"GAPS: {len(g['missing'])} of {g['want']} days in the window were "
              f"never harvested, earliest {g['missing'][0]}. Re-run "
              f"`--since {g['missing'][0]} --through {g['missing'][-1]}` to close them.",
              file=sys.stderr)
        bad = True
    return 1 if bad else 0


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description="Harvest claude.ai usage into BigQuery")
    p.add_argument("--since", help="First day, YYYY-MM-DD (default: --window days back)")
    p.add_argument("--through", help="Last day, YYYY-MM-DD (default: yesterday UTC)")
    p.add_argument("--window", type=int, default=7,
                   help="Trailing days to re-harvest when --since is absent (default 7)")
    p.add_argument("--backfill", action="store_true",
                   help=f"Harvest everything from {EARLIEST_DATA_DATE}")
    p.add_argument("--skip-members", action="store_true", help="Skip the seat snapshot")
    p.add_argument("--dry-run", action="store_true", help="Fetch and summarise, write nothing")
    p.add_argument("--check", action="store_true",
                   help="Report freshness/gaps from BigQuery only (no session key needed); "
                        "exit 1 if stale")
    p.add_argument("--max-age-days", type=int, default=3,
                   help="With --check, how far behind settled data is tolerable (default 3)")
    a = p.parse_args()

    if a.check:
        try:
            return cmd_check(a)
        except bq.BQError as e:
            print(f"freshness check failed: {e}", file=sys.stderr)
            return 3

    today = _utc_today()
    yesterday = today - dt.timedelta(days=1)

    through = dt.date.fromisoformat(a.through) if a.through else yesterday
    if through > yesterday:
        print(f"! --through {through} is not settled yet (export lags one day). "
              f"Clamping to {yesterday}.", file=sys.stderr)
        through = yesterday

    if a.backfill:
        since = dt.date.fromisoformat(EARLIEST_DATA_DATE)
    elif a.since:
        since = dt.date.fromisoformat(a.since)
    else:
        since = through - dt.timedelta(days=a.window - 1)
    earliest = dt.date.fromisoformat(EARLIEST_DATA_DATE)
    if since < earliest:
        print(f"! --since {since} predates the first available data; clamping to {earliest}.",
              file=sys.stderr)
        since = earliest
    if since > through:
        print(f"Nothing to do: since {since} is after through {through}.")
        return 0

    try:
        api = ClaudeAdmin()
        pre = api.check_auth()
    except ClaudeAdminAuthError as e:
        print(f"AUTH FAILED — nothing written.\n{e}", file=sys.stderr)
        return 2
    except ClaudeAdminError as e:
        print(f"Pre-flight failed — nothing written.\n{e}", file=sys.stderr)
        return 3
    print(f"auth ok ({pre['members']} members visible)")

    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    days = list(daterange(since, through))
    print(f"harvesting {len(days)} day(s): {since} → {through}"
          f"{' [dry run]' if a.dry_run else ''}")

    tot_rows = tot_tok = 0
    tot_usd = 0.0
    failed = []
    for d in days:
        ds = d.isoformat()
        try:
            s = harvest_day(api, ds, stamp, dry_run=a.dry_run)
        except ClaudeAdminAuthError as e:
            # Mid-run expiry: stop immediately. Continuing would write empty
            # partitions over real data for every remaining day.
            print(f"\nAUTH FAILED at {ds} — stopping. Days already written are valid.\n{e}",
                  file=sys.stderr)
            return 2
        except ClaudeAdminError as e:
            failed.append((ds, str(e)))
            print(f"  {ds}  ERROR  {e}", file=sys.stderr)
            continue
        except bq.BQUnavailable as e:
            # Systemic, not per-day: a missing GCP_KEY or library fails every
            # remaining write identically. Auth to claude.ai still works, so
            # without this clause a backfill would politely fetch all 201 days
            # and discard every one. Must be caught BEFORE BQError, which it
            # subclasses.
            print(f"\nBIGQUERY UNAVAILABLE at {ds} — stopping; every remaining "
                  f"write would fail the same way.\n{e}", file=sys.stderr)
            return 3
        except bq.BQError as e:
            # A single failed load is per-day and must not abandon the window.
            # Uncaught, one bad load aborted every remaining day *and* the
            # members snapshot, and the traceback made a partial run look like
            # a crash rather than a recoverable gap.
            failed.append((ds, f"BigQuery: {e}"))
            print(f"  {ds}  BQ ERROR  {e}", file=sys.stderr)
            continue
        tot_rows += s["rows"]; tot_tok += s["tokens"]; tot_usd += s["billed_usd"]
        flag = "" if s["rows"] else "  (no activity)"
        print(f"  {ds}  rows={s['rows']:>4}  users={s['users']:>3}  "
              f"tokens={s['tokens']:>15,}  billed=${s['billed_usd']:>8,.2f}{flag}")

    if not a.skip_members:
        try:
            m = harvest_members(api, through.isoformat(), stamp, dry_run=a.dry_run)
            print(f"members snapshot {m['date']}: {m['members']} members {m['tiers']}")
        except ClaudeAdminError as e:
            failed.append(("members", str(e)))
            print(f"  members  ERROR  {e}", file=sys.stderr)

    print(f"\ntotal: {tot_rows:,} rows  {tot_tok:,} tokens  ${tot_usd:,.2f} billed overage")
    if failed:
        print(f"FAILED ({len(failed)}): " + ", ".join(d for d, _ in failed), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
