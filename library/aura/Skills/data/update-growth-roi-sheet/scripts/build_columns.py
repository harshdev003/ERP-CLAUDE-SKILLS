#!/usr/bin/env python3
"""Build Growth ROI tracker column values from the Mixpanel Growth ROI dashboard.

Primary path: one saved Get-Report result per column (--report-dir), pulled straight
from the dashboard. Fallback: CSV exports (--csv-dir), for a report whose date range
is still wide enough to hit Mixpanel's 200-breakdown-bucket cap.

Either way it maps each weekly bucket onto its spreadsheet row, applies the registry's
shift and maturity rules, and writes JSON ready for update_cells. It refuses to emit a
column whose week coverage has interior holes -- that is the cap silently truncating a
report, and it looks identical to a complete series.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import pathlib
import re
import sys

MONTHS = {m: i + 1 for i, m in enumerate(
    "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split())}

WEEK_RE = re.compile(r"^([A-Z][a-z]{2}) (\d{1,2}), (\d{4})\b")


class BuildError(Exception):
    """A problem that must stop the run rather than produce a partial column."""


def parse_week_start(label: str) -> dt.date:
    m = WEEK_RE.match(label.strip().strip('"'))
    if not m:
        raise BuildError(f"unparseable week label: {label!r}")
    return dt.date(int(m.group(3)), MONTHS[m.group(1)], int(m.group(2)))


def load_report(path: pathlib.Path, kind: str) -> dict[dt.date, tuple[int, ...]]:
    """Parse a saved Mixpanel Get-Report result into {week_start: counts}.

    The funnel result is {results: {series_name: {headers, rows}}}, where rows are flat
    arrays. Column ORDER VARIES BETWEEN REPORTS -- index by header name, never position.
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    # The MCP wraps the payload in {"result": ...}: a JSON string when the response
    # overflowed to a file, an object when it came back inline. A short current-year
    # report is the likelier of the two, so both have to unwrap.
    if isinstance(raw, dict) and "results" not in raw and "result" in raw:
        inner = raw["result"]
        raw = json.loads(inner) if isinstance(inner, str) else inner
    if not isinstance(raw, dict) or "results" not in raw:
        raise BuildError(f"{path.name}: not a Get-Report result (no 'results' key)")
    series = raw["results"]
    if len(series) != 1:
        raise BuildError(
            f"{path.name}: expected one series, found {len(series)}: {list(series)}. "
            "The report has a breakdown on it -- remove it or export a CSV instead.")
    block = next(iter(series.values()))
    headers, rows = block.get("headers"), block.get("rows")
    if not headers or rows is None:
        raise BuildError(f"{path.name}: result block has no headers/rows")
    try:
        i_break = headers.index("breakdown")
        i_step = headers.index("step")
        i_count = headers.index("count")
    except ValueError as exc:
        raise BuildError(f"{path.name}: unexpected headers {headers}") from exc

    want_steps = 1 if kind == "value" else 2
    per_week: dict[dt.date, dict[int, int]] = {}
    for row in rows:
        label = row[i_break]
        if label in ("$overall", "$other"):
            continue
        week = parse_week_start(label)
        m = re.match(r"(\d+)\.", str(row[i_step]))
        if not m:
            raise BuildError(f"{path.name}: unparseable step {row[i_step]!r}")
        per_week.setdefault(week, {})[int(m.group(1))] = int(row[i_count] or 0)

    out: dict[dt.date, tuple[int, ...]] = {}
    for week, steps in per_week.items():
        missing = [s for s in range(1, want_steps + 1) if s not in steps]
        if missing:
            # A week with no step-2 conversions still reports step 1; a missing step 1
            # means the bucket was dropped, which the coverage check should catch.
            if 1 in missing:
                continue
            for s in missing:
                steps[s] = 0
        out[week] = tuple(steps[s] for s in range(1, want_steps + 1))
    if not out:
        raise BuildError(f"{path.name}: no weekly buckets found")
    return out


def load_csv(path: pathlib.Path, kind: str) -> dict[dt.date, tuple[int, ...]]:
    """Return {week_start: counts}. Skips the $overall row."""
    want = 1 if kind == "value" else 2
    out: dict[dt.date, tuple[int, ...]] = {}
    with path.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.reader(fh))
    if not rows:
        raise BuildError(f"{path.name}: empty file")
    for row in rows[1:]:
        if not row or not row[0] or row[0].startswith("$"):
            continue
        if len(row) < 1 + want:
            raise BuildError(f"{path.name}: row has too few columns: {row!r}")
        week = parse_week_start(row[0])
        if week in out:
            raise BuildError(f"{path.name}: duplicate week {week}")
        try:
            counts = tuple(int(float(c)) for c in row[1:1 + want])
        except ValueError as exc:
            raise BuildError(f"{path.name}: non-numeric count in {row!r}") from exc
        out[week] = counts
    if not out:
        raise BuildError(f"{path.name}: no weekly rows found")
    return out


def week_rows(first_week: dt.date, first_row: int, last_week: dt.date) -> dict[dt.date, int]:
    rows: dict[dt.date, int] = {}
    week, row = first_week, first_row
    while week <= last_week:
        rows[week] = row
        week += dt.timedelta(weeks=1)
        row += 1
    return rows


def format_value(counts: tuple[int, ...], kind: str) -> str:
    if kind == "value":
        return str(counts[0])
    n1, n2 = counts
    if n1 == 0:
        return ""
    return f"{n2 / n1 * 100:.2f}%"


def build_column(spec: dict, data: dict[dt.date, tuple[int, ...]],
                 rows: dict[dt.date, int], report: list[str]) -> list[str]:
    col = spec["column"]
    kind = spec.get("kind", "ratio")
    shift = int(spec.get("shift_weeks", 0))
    maturity = int(spec.get("maturity_weeks", 0))

    first_week, last_week = min(rows), max(rows)
    for week in data:
        if (week - first_week).days % 7:
            raise BuildError(
                f"{col}: week {week} is not on the sheet's weekly grid "
                f"(expected every 7 days from {first_week})")

    # Buckets outside the window are ignored, not errors: past the end is the
    # in-progress week, before the start is history this run is not rewriting.
    # Either way, one source running longer than the others must not stretch the
    # grid and false-fail every other column's coverage check.
    ahead = sorted(w for w in data if w > last_week)
    behind = sorted(w for w in data if w < first_week)

    missing = sorted(set(rows) - set(data))
    if missing:
        tail = [last_week - dt.timedelta(weeks=i) for i in range(len(missing))]
        if set(missing) == set(tail):
            raise BuildError(
                f"{col}: source stops at {max(set(data) & set(rows), default=first_week)}, "
                f"{len(missing)} week(s) before the last closed week {last_week}. "
                "The report or export is stale -- refresh it, or pass --as-of "
                "to build as of an earlier date.")
        raise BuildError(
            f"{col}: source covers {len(set(data) & set(rows))} of {len(rows)} weeks; "
            f"{len(missing)} missing, first {missing[0]}, last {missing[-1]}. "
            "Interior holes are Mixpanel's 200-bucket cap: narrow the report's date "
            "range (a current-year start is ~37 weeks) or fall back to --csv-dir.")

    ordered = sorted(rows)
    # The trailing rows a shift cannot reach, plus the metric's own maturity lag.
    blank_from = len(ordered) - max(shift + maturity, 0)
    values: list[str] = []
    blanks = 0
    for idx, week in enumerate(ordered):
        source = week + dt.timedelta(weeks=shift)
        if idx >= blank_from or source not in data:
            values.append("")
            blanks += 1
            continue
        val = format_value(data[source], kind)
        if kind == "ratio" and val:
            pct = float(val[:-1])
            if not 0.0 <= pct <= 100.0:
                raise BuildError(f"{col}: rate {val} out of range at {week}")
        if val == "":
            blanks += 1
        values.append(val)

    notes = []
    if ahead:
        notes.append(f"ignored {len(ahead)} in-progress week(s)")
    if behind:
        notes.append(f"ignored {len(behind)} week(s) before the build window")
    note = (", " + ", ".join(notes)) if notes else ""
    report.append(
        f"{col}: {len(values) - blanks} written, {blanks} blank "
        f"(shift {shift}w, maturity {maturity}w), weeks {ordered[0]}..{ordered[-1]}{note}")
    return values


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    here = pathlib.Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(description=__doc__)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--report-dir", type=pathlib.Path,
                     help="directory of saved Get-Report results, named AM_*.json etc "
                          "(the primary path -- pull each report from the dashboard)")
    src.add_argument("--csv-dir", type=pathlib.Path,
                     help="fallback: directory of per-column CSV exports, named AM_*.csv")
    ap.add_argument("--out", type=pathlib.Path, required=True,
                    help="where to write the {column: [values]} JSON")
    ap.add_argument("--registry", type=pathlib.Path, default=here / "registry.json")
    ap.add_argument("--from-week", type=str, default=None,
                    help="first week start to build, YYYY-MM-DD. Defaults to the earliest "
                         "week the reports actually cover, so a report that starts Jan 1 "
                         "of this year updates only this year's rows and leaves history "
                         "alone. Must land on the sheet's weekly grid.")
    ap.add_argument("--as-of", type=str, default=None,
                    help="evaluate maturity as of this date, YYYY-MM-DD (default: today). "
                         "The grid ends at the last week that had fully elapsed by then.")
    ap.add_argument("--last-week", type=str, default=None,
                    help="override the grid's last week start, YYYY-MM-DD. Only use this "
                         "when you deliberately want a different end than --as-of implies.")
    ap.add_argument("--columns", type=str, default=None,
                    help="comma-separated subset of columns to build")
    ap.add_argument("--report", action="store_true", help="print a per-column summary")
    args = ap.parse_args()

    registry = json.loads(args.registry.read_text(encoding="utf-8"))
    first_week = dt.date.fromisoformat(registry["first_week"])
    first_row = int(registry["first_row"])

    wanted = {c.strip().upper() for c in args.columns.split(",")} if args.columns else None
    specs = [s for s in registry["columns"]
             if wanted is None or s["column"].upper() in wanted]
    if not specs:
        raise BuildError("no registry columns selected")

    src_dir = args.report_dir or args.csv_dir
    ext, loader = ("json", load_report) if args.report_dir else ("csv", load_csv)
    loaded: dict[str, dict[dt.date, tuple[int, ...]]] = {}
    for spec in specs:
        col = spec["column"]
        matches = sorted(src_dir.glob(f"{col}_*.{ext}")) or \
            sorted(src_dir.glob(f"{col}.{ext}"))
        if not matches:
            raise BuildError(f"{col}: no .{ext} found in {src_dir}")
        if len(matches) > 1:
            raise BuildError(f"{col}: {len(matches)} files match; keep exactly one")
        loaded[col] = loader(matches[0], spec.get("kind", "ratio"))

    # Maturity is measured against the calendar, not against how far the CSV happens to
    # run. An export taken mid-week includes the in-progress week; anchoring to it would
    # blank only that partial week and still write a last-closed week whose conversion
    # window is open -- exactly the half-elapsed number this skill exists to keep out.
    as_of = dt.date.fromisoformat(args.as_of) if args.as_of else dt.date.today()
    elapsed = as_of - dt.timedelta(days=7)
    last_closed = elapsed - dt.timedelta(days=(elapsed - first_week).days % 7)
    if last_closed < first_week:
        raise BuildError(f"--as-of {as_of} is before the sheet's first complete week")

    last_week = dt.date.fromisoformat(args.last_week) if args.last_week else last_closed

    # A report that starts Jan 1 of this year simply has no 2022-2025 buckets. Build only
    # the rows it covers rather than reporting all of history as missing.
    if args.from_week:
        from_week = dt.date.fromisoformat(args.from_week)
    else:
        from_week = max([first_week] + [min(d) for d in loaded.values()])
    if (from_week - first_week).days % 7:
        raise BuildError(f"--from-week {from_week} is not on the weekly grid from {first_week}")
    if from_week > last_week:
        raise BuildError(f"reports start {from_week}, after the last closed week {last_week}")

    start_row = first_row + (from_week - first_week).days // 7
    rows = week_rows(from_week, start_row, last_week)

    report: list[str] = []
    cols = {spec["column"]: build_column(spec, loaded[spec["column"]], rows, report)
            for spec in specs}
    out = {
        "start_row": start_row,
        "first_week": from_week.isoformat(),
        "last_week": last_week.isoformat(),
        "columns": cols,
    }

    args.out.write_text(json.dumps(out, indent=1), encoding="utf-8")
    end_row = start_row + len(rows) - 1
    print(f"Wrote {args.out} -- {len(cols)} column(s), "
          f"rows {start_row}..{end_row} ({len(rows)} weeks, "
          f"{from_week}..{last_week})")
    if from_week > first_week:
        print(f"  NOTE: rows {first_row}..{start_row - 1} are outside the reports' range "
              "and are left untouched.")
    if args.report:
        for line in report:
            print("  " + line)


if __name__ == "__main__":
    try:
        main()
    except BuildError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
