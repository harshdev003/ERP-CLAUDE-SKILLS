#!/usr/bin/env python3
"""Load spend-report CSVs already downloaded from the claude.ai UI into BigQuery.

The credential-free half of this skill. `harvest.py` needs a session cookie;
this needs nothing but the files, because a human already authenticated by
clicking Export in the browser.

**Why this path exists.** claude.ai offers no API key on Team plans, and every
programmatic route out of the browser is blocked: a blob download does not land
even behind a real click gesture, the site's CSP blocks posting to localhost,
and navigating straight to the export URL does not trigger an attachment. The
app's own Export button is the only mechanism that produces a file. So the
division of labour is: the browser downloads, this script loads.

**The date comes from the filename, and that is load-bearing.** The export
embeds its range as `spend-report-<org>-<start>-to-<end>.csv`, and the CSV
itself carries no date column at all. So a file's partition is knowable only
from its name. A renamed file is therefore unloadable rather than
mis-loadable — the parse fails loudly instead of guessing a date.

**Only single-day files reach the daily table.** A multi-day export is a single
aggregate over its range with no way to split it back into days. Loading one
into a day-partition would book a week of usage onto one date, which is wrong in
a way no later query could detect. Those files are reported and skipped.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import bq  # noqa: E402
from claude_admin import (  # noqa: E402
    ClaudeAdminError, parse_members_csv, parse_spend_csv,
)
from harvest import (  # noqa: E402
    LOG_TABLE, PROJECT, SEAT_ORDER, SEAT_SCHEMA, SEAT_TABLE,
    USAGE_ORDER, USAGE_SCHEMA, USAGE_TABLE, _rows_to_csv, record_harvest,
)

SPEND_RE = re.compile(
    r"^spend-report-(?P<org>[0-9a-f-]{36})-(?P<start>\d{4}-\d{2}-\d{2})"
    r"-to-(?P<end>\d{4}-\d{2}-\d{2})\.csv$"
)
MEMBERS_RE = re.compile(
    r"^members-(?P<org>[0-9a-f-]{36})-(?P<as_of>\d{4}-\d{2}-\d{2})\.csv$"
)

# Chrome appends " (1)" to a repeat download. Such a file is a byte-identical
# duplicate of one already named correctly, so ignoring it is right — but it is
# reported rather than silently skipped, because "ignored" and "loaded" must not
# look the same in the output.
DUPLICATE_RE = re.compile(r" \(\d+\)\.csv$")


def harvested_days() -> set:
    """Days harvest_log already records, read once.

    The log is the coverage record precisely because a zero-row day is
    indistinguishable from an unwritten one in the fact table — so an idle day
    that was genuinely read is in here, and skipping it is correct.
    """
    rows = bq.query(
        f"SELECT DISTINCT harvest_date FROM `{LOG_TABLE}`", PROJECT)
    return {str(r["harvest_date"]) for r in rows}


def classify(path: str) -> dict:
    """Decide what a downloaded file is, from its name alone."""
    name = os.path.basename(path)
    m = SPEND_RE.match(name)
    if m:
        start, end = m.group("start"), m.group("end")
        return {
            "kind": "spend_day" if start == end else "spend_range",
            "name": name, "path": path, "start": start, "end": end,
            "org": m.group("org"),
        }
    m = MEMBERS_RE.match(name)
    if m:
        return {"kind": "members", "name": name, "path": path,
                "as_of": m.group("as_of"), "org": m.group("org")}
    if DUPLICATE_RE.search(name) and name.startswith(("spend-report-", "members-")):
        return {"kind": "duplicate", "name": name}
    return {"kind": "unknown", "name": name}


def load_day(info: dict, stamp: str, *, dry_run: bool = False) -> dict:
    with open(info["path"], encoding="utf-8") as fh:
        rows = parse_spend_csv(fh.read())   # raises on a shape change
    day = info["start"]
    for r in rows:
        r["usage_date"] = day
        r["harvested_at"] = stamp
    out = {
        "date": day, "rows": len(rows),
        "tokens": sum(r["prompt_tokens"] + r["completion_tokens"] for r in rows),
        "billed": round(sum(float(r["net_spend_usd"]) for r in rows), 2),
        "users": len({r["user_email"] for r in rows if r["user_email"]}),
    }
    if dry_run:
        out["loaded"] = None
        return out
    out["loaded"] = bq.load_partition(
        USAGE_TABLE, _rows_to_csv(rows, USAGE_ORDER), USAGE_SCHEMA, day, PROJECT
    )
    # Assert the day was covered even when it held no usage — a zero-row
    # partition is invisible to every query, so coverage lives in harvest_log.
    record_harvest(day, out, "download", stamp)
    return out


def load_members(path: str, as_of: str, stamp: str, *, dry_run: bool = False) -> dict:
    with open(path, encoding="utf-8") as fh:
        rows = parse_members_csv(fh.read())
    if not rows:
        raise ClaudeAdminError(f"{path} has no member rows — refusing to write an empty snapshot")
    for r in rows:
        r["as_of_date"] = as_of
        r["harvested_at"] = stamp
    tiers = {}
    for r in rows:
        tiers[r["seat_tier"]] = tiers.get(r["seat_tier"], 0) + 1
    out = {"date": as_of, "members": len(rows), "tiers": tiers}
    if dry_run:
        out["loaded"] = None
        return out
    out["loaded"] = bq.load_partition(
        SEAT_TABLE, _rows_to_csv(rows, SEAT_ORDER), SEAT_SCHEMA, as_of, PROJECT
    )
    return out


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(
        description="Load already-downloaded claude.ai spend-report CSVs into BigQuery")
    p.add_argument("--dir", default=os.path.expanduser("~/Downloads"),
                   help="Where the browser put the files (default ~/Downloads)")
    p.add_argument("--members", help="Path to a members CSV to load as a seat snapshot")
    p.add_argument("--members-as-of", help="Snapshot date for --members (default: today UTC)")
    p.add_argument("--dry-run", action="store_true", help="Parse and summarise, write nothing")
    p.add_argument("--reload", action="store_true",
                   help="Re-load days harvest_log already records (default: skip them). "
                        "The write is idempotent either way; this only decides whether to "
                        "spend the time.")
    p.add_argument("--keep", action="store_true",
                   help="Leave loaded files in place (default: leave them; this flag is a no-op "
                        "kept for explicitness — nothing is ever deleted)")
    a = p.parse_args()

    if not os.path.isdir(a.dir):
        print(f"no such directory: {a.dir}", file=sys.stderr)
        return 3

    found = [classify(os.path.join(a.dir, n)) for n in sorted(os.listdir(a.dir))]
    days = [f for f in found if f["kind"] == "spend_day"]
    ranges = [f for f in found if f["kind"] == "spend_range"]
    members = [f for f in found if f["kind"] == "members"]
    dupes = [f for f in found if f["kind"] == "duplicate"]

    if not days and not ranges and not members and not a.members:
        print(f"No spend-report CSVs in {a.dir}.\n"
              "Export them from claude.ai: Analytics > How much is Claude costing? > "
              "Export spend report > Custom, with From and To set to the SAME day.")
        return 0

    if dupes:
        print(f"! ignoring {len(dupes)} repeat download(s): "
              f"{', '.join(d['name'] for d in dupes[:3])}"
              f"{'' if len(dupes) <= 3 else ' …'}", file=sys.stderr)

    if ranges:
        print(f"! skipping {len(ranges)} multi-day file(s) — a range export is one aggregate "
              f"and cannot be split into days:", file=sys.stderr)
        for r in ranges:
            print(f"    {r['name']}  ({r['start']} → {r['end']})", file=sys.stderr)

    # A download directory accumulates: after a backfill it holds every day ever
    # exported, and re-parsing and re-writing all of them costs minutes for no
    # change. harvest_log already records which days landed, so consult it and
    # load only what is new. The partition replace stays idempotent — this is a
    # time saving, not a correctness one, which is why --reload can override it
    # and why a log that cannot be read degrades to loading everything rather
    # than skipping blind.
    skipped_known = []
    if days and not a.reload:
        try:
            known = harvested_days()
        except Exception as e:  # noqa: BLE001 — see below
            # Deliberately broad. This lookup is a time saving, never a
            # correctness one, so ANY failure must fall back to loading
            # everything rather than abort the load or — worse — skip blind.
            # Naming the client's exception types here would couple the
            # degrade path to a library's private taxonomy, and the first
            # version did exactly that and crashed on the one it missed.
            print(f"! could not read harvest_log ({e}); loading every file",
                  file=sys.stderr)
            known = set()
        if known:
            keep = [d for d in days if d["start"] not in known]
            skipped_known = [d for d in days if d["start"] in known]
            days = keep

    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    if skipped_known:
        print(f"skipping {len(skipped_known)} day(s) already in harvest_log "
              f"({skipped_known[0]['start']} … {skipped_known[-1]['start']}) — "
              f"pass --reload to redo them")
    print(f"loading {len(days)} single-day file(s) from {a.dir}"
          f"{' [dry run]' if a.dry_run else ''}\n")
    if not days:
        print("nothing new to load.")

    tot_rows = tot_tok = 0
    tot_usd = 0.0
    failed = []
    for info in days:
        try:
            s = load_day(info, stamp, dry_run=a.dry_run)
        except (ClaudeAdminError, bq.BQError, OSError) as e:
            failed.append((info["name"], str(e)))
            print(f"  {info['start']}  ERROR  {e}", file=sys.stderr)
            continue
        tot_rows += s["rows"]; tot_tok += s["tokens"]; tot_usd += s["billed"]
        flag = "" if s["rows"] else "  (no activity)"
        print(f"  {s['date']}  rows={s['rows']:>4}  users={s['users']:>3}  "
              f"tokens={s['tokens']:>15,}  billed=${s['billed']:>8,.2f}{flag}")

    # Auto-discovered members exports, plus an explicitly passed one. The
    # filename carries its own as_of date, so no flag is needed for the normal
    # case; --members stays for a file that was renamed or lives elsewhere.
    seat_jobs = [(m["path"], m["as_of"]) for m in members]
    if a.members:
        seat_jobs.append((a.members,
                          a.members_as_of
                          or dt.datetime.now(dt.timezone.utc).date().isoformat()))
    for path, as_of in seat_jobs:
        try:
            m = load_members(path, as_of, stamp, dry_run=a.dry_run)
            print(f"\nmembers snapshot {m['date']}: {m['members']} members {m['tiers']}")
        except (ClaudeAdminError, bq.BQError, OSError) as e:
            failed.append((os.path.basename(path), str(e)))
            print(f"  members  ERROR  {e}", file=sys.stderr)

    print(f"\ntotal: {tot_rows:,} rows  {tot_tok:,} tokens  ${tot_usd:,.2f} billed overage")
    if ranges:
        print(f"({len(ranges)} multi-day file(s) skipped — see above)")
    if failed:
        print(f"FAILED ({len(failed)}): " + ", ".join(n for n, _ in failed), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
