#!/usr/bin/env python3
"""Plan one monthly update of the Product Metrics / Content & Marketplace sheet.

Reads the Mixpanel Get-Report results for every row in registry.json plus a
read of each tab's most recent columns, applies the per-row rules, and writes
plan.json holding a preview of every cell and the spreadsheets.batchUpdate
requests that perform the write.

Nothing here touches the sheet or Mixpanel. The agent fetches the inputs with
the Mixpanel and Google Sheets connectors and sends the requests itself.

Usage:
  plan_update.py --month 2026-10 --reports-dir reports \
      --pm-read pm_before.json --pm-first-col CN \
      --cm-read cm_before.json --cm-first-col CB \
      [--as-of 2026-11-17] [--out plan.json]

The two reads are google-sheets read_rows results (has_headers=false) that
start at row 1 and run at least through the last filled month column. They
should start 3 or more columns before it, so the backfill cells have their
current values for the preview.
"""
import argparse
import datetime as dt
import json
import os
import sys
from decimal import ROUND_HALF_UP, Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


class PlanError(Exception):
    pass


# ---------------------------------------------------------------- helpers

def col_to_idx(col):
    n = 0
    for ch in col.strip().upper():
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def idx_to_col(idx):
    s = ""
    idx += 1
    while idx:
        idx, r = divmod(idx - 1, 26)
        s = chr(65 + r) + s
    return s


def add_months(year, month, delta):
    total = year * 12 + (month - 1) + delta
    return total // 12, total % 12 + 1


def month_label(year, month):
    return f"{MONTHS[month - 1]}-{year % 100:02d}"


def last_day(year, month):
    ny, nm = add_months(year, month, 1)
    return dt.date(ny, nm, 1) - dt.timedelta(days=1)


def sheets_serial(d):
    return (d - dt.date(1899, 12, 30)).days


def half_up(value, places):
    q = Decimal(1).scaleb(-places)
    return float(Decimal(str(value)).quantize(q, rounding=ROUND_HALF_UP))


def unwrap(obj):
    """Accept a raw MCP payload or the {"result": "<json string>"} wrapper."""
    if isinstance(obj, dict) and set(obj) == {"result"} and isinstance(obj["result"], str):
        return json.loads(obj["result"])
    return obj


def load_json(path):
    with open(path, encoding="utf-8") as fh:
        return unwrap(json.load(fh))


# ---------------------------------------------------------------- formats

def format_value(raw, fmt):
    """Return (number written to the sheet, string the sheet displays)."""
    if fmt == "integer":
        v = int(half_up(raw, 0))
        return v, f"{v:,}"
    if fmt == "percent":
        v = half_up(raw, 2)
        return v, f"{int(half_up(v * 100, 0))}%"
    if fmt == "percent_points":
        v = half_up(raw / 100.0, 2)
        return v, f"{int(half_up(v * 100, 0))}%"
    if fmt == "decimal2":
        v = half_up(raw, 2)
        return v, f"{v:.2f}"
    if fmt == "decimal1":
        v = half_up(raw, 1)
        return v, f"{v:.1f}"
    raise PlanError(f"unknown format {fmt!r}")


# ---------------------------------------------------------------- reports

def load_reports(reports_dir):
    reports = {}
    for name in sorted(os.listdir(reports_dir)):
        if not name.endswith(".json"):
            continue
        data = load_json(os.path.join(reports_dir, name))
        if isinstance(data, dict) and "id" in data and "results" in data:
            reports[int(data["id"])] = data
    return reports


def series_rows(report, series=None):
    results = report.get("results") or {}
    if not results:
        raise PlanError(f"report {report.get('id')} has no results")
    if series is None:
        if len(results) != 1:
            raise PlanError(f"report {report['id']} has {len(results)} series "
                            f"({', '.join(results)}); set 'series' in registry.json")
        series = next(iter(results))
    if series not in results:
        raise PlanError(f"report {report['id']} has no series {series!r}")
    out = []
    for date_s, value in results[series]["rows"]:
        out.append((dt.date.fromisoformat(str(date_s)[:10]), float(value)))
    return out


def value_for_month(spec, report, year, month, as_of):
    """Apply the row's rule for one calendar month. Returns (raw, note)."""
    rows = series_rows(report, spec.get("series"))
    rule = spec["rule"]
    if rule in ("monthly", "cohort"):
        for d, v in rows:
            if d == dt.date(year, month, 1):
                return v, ""
        raise PlanError(f"row {spec['row']} ({spec['label']}): report "
                        f"{spec['report_id']} has no {month_label(year, month)} bucket")
    if rule == "daily_max":
        days = [(d, v) for d, v in rows if d.year == year and d.month == month]
        if len(days) < last_day(year, month).day:
            raise PlanError(f"row {spec['row']} ({spec['label']}): only {len(days)} "
                            f"days of {month_label(year, month)} in the report")
        d, v = max(days, key=lambda dv: dv[1])
        return v, f"max day {d.isoformat()}"
    if rule == "weekly_second_highest":
        # A week (Sunday start) belongs to the month that holds most of its
        # seven days, i.e. the month of its Wednesday.
        weeks = [(d, v) for d, v in rows
                 if (d + dt.timedelta(days=3)).year == year
                 and (d + dt.timedelta(days=3)).month == month]
        open_weeks = [d for d, _ in weeks if d + dt.timedelta(days=7) > as_of]
        if open_weeks:
            raise PlanError(f"row {spec['row']} ({spec['label']}): week of "
                            f"{open_weeks[0]} has not finished as of {as_of}")
        if len(weeks) < 4:
            raise PlanError(f"row {spec['row']} ({spec['label']}): only {len(weeks)} "
                            f"weeks of {month_label(year, month)} in the report")
        ranked = sorted(weeks, key=lambda dv: dv[1], reverse=True)
        d, v = ranked[1]
        return v, f"2nd-highest of {len(weeks)} weeks (week of {d.isoformat()})"
    raise PlanError(f"unknown rule {rule!r}")


# ---------------------------------------------------------------- sheet

class TabGrid:
    def __init__(self, name, read, first_col):
        self.name = name
        self.first = col_to_idx(first_col)
        rows = read.get("rows", read) if isinstance(read, dict) else read
        self.rows = [list(r) for r in rows]
        if not self.rows:
            raise PlanError(f"{name}: the read is empty")

    def get(self, row, col_idx):
        r = row - 1
        c = col_idx - self.first
        if r < 0 or r >= len(self.rows) or c < 0 or c >= len(self.rows[r]):
            return ""
        v = self.rows[r][c]
        return "" if v is None else str(v)

    def last_header_idx(self):
        header = self.rows[0]
        last = max((i for i, v in enumerate(header) if str(v).strip()), default=None)
        if last is None:
            raise PlanError(f"{self.name}: row 1 of the read has no month headers")
        return self.first + last


# ---------------------------------------------------------------- planning

def blank_cell():
    return {}


def number_cell(v):
    return {"userEnteredValue": {"numberValue": v}}


def update_req(sheet_id, row, col_idx, cell, fields):
    return {"updateCells": {
        "start": {"sheetId": sheet_id, "rowIndex": row - 1, "columnIndex": col_idx},
        "rows": [{"values": [cell]}],
        "fields": fields,
    }}


def plan(registry, reports, reads, month, as_of):
    year, mon = month
    if as_of < dt.date(*add_months(year, mon, 1), 1):
        raise PlanError(f"{month_label(year, mon)} has not closed as of {as_of}")
    prev_label = month_label(*add_months(year, mon, -1))
    new_label = month_label(year, mon)

    out = {"month": new_label, "as_of": as_of.isoformat(), "tabs": {},
           "cells": [], "requests": [], "warnings": []}
    copy_reqs, cell_reqs, backfill_reqs = [], [], []

    grids = {}
    for tab, cfg in registry["tabs"].items():
        grid = reads[tab]
        last = grid.last_header_idx()
        labels = [str(v).strip() for v in grid.rows[0]]
        if new_label in labels:
            raise PlanError(f"{tab}: a {new_label} column already exists; "
                            "this skill adds one new month, it does not redo one")
        if grid.get(1, last).strip() != prev_label:
            raise PlanError(f"{tab}: last filled header is {grid.get(1, last)!r}, "
                            f"expected {prev_label!r}. Is the month right, and does "
                            "the read reach the last column?")
        new_idx = last + 1
        grids[tab] = (grid, new_idx)
        out["tabs"][tab] = {"prev_col": idx_to_col(last), "new_col": idx_to_col(new_idx)}
        sid = cfg["sheet_id"]
        copy_reqs.append({"copyPaste": {
            "source": {"sheetId": sid, "startRowIndex": 0, "endRowIndex": cfg["template_rows"],
                       "startColumnIndex": last, "endColumnIndex": last + 1},
            "destination": {"sheetId": sid, "startRowIndex": 0, "endRowIndex": cfg["template_rows"],
                            "startColumnIndex": new_idx, "endColumnIndex": new_idx + 1},
            "pasteType": "PASTE_NORMAL"}})
        serial = sheets_serial(dt.date(year, mon, 1))
        cell_reqs.append(update_req(sid, 1, new_idx, number_cell(serial), "userEnteredValue"))
        out["cells"].append({"tab": tab, "cell": f"{idx_to_col(new_idx)}1", "row": 1,
                             "label": "header", "old": "", "new": new_label,
                             "expect": new_label, "kind": "header", "note": ""})

    def col_for(tab, y, m):
        _, new_idx = grids[tab]
        return new_idx - ((year - y) * 12 + (mon - m))

    def record(tab, row, col_idx, label, new_display, kind, note, old=None):
        grid, _ = grids[tab]
        out["cells"].append({
            "tab": tab, "cell": f"{idx_to_col(col_idx)}{row}", "row": row, "label": label,
            "old": grid.get(row, col_idx) if old is None else old,
            "new": new_display, "expect": new_display, "kind": kind, "note": note})

    by_tab_row = {(s["tab"], s["row"]): s for s in registry["rows"]}

    # The new column: every non-formula row is either a value or explicitly blank,
    # so nothing hard-coded in the copied column (notes, PMF, retention) leaks in.
    for tab, cfg in registry["tabs"].items():
        _, new_idx = grids[tab]
        sid = cfg["sheet_id"]
        for row in range(2, cfg["template_rows"] + 1):
            if row in cfg["formula_rows"]:
                record(tab, row, new_idx, "(formula)", "(formula)", "formula", "copied from the previous month")
                continue
            spec = by_tab_row.get((tab, row))
            fill = (spec is not None and spec["rule"] != "cohort"
                    and ("only_months" not in spec or mon in spec["only_months"]))
            if not fill:
                cell_reqs.append(update_req(sid, row, new_idx, blank_cell(), "userEnteredValue"))
                record(tab, row, new_idx, spec["label"] if spec else "", "", "blank",
                       "not filled this month" if spec else "")
                continue
            report = reports.get(spec["report_id"])
            if report is None:
                raise PlanError(f"no Get-Report result for report {spec['report_id']} "
                                f"({tab} row {row}, {spec['label']})")
            raw, note = value_for_month(spec, report, year, mon, as_of)
            v, disp = format_value(raw, spec["format"])
            if spec.get("maturity_days"):
                ready = last_day(year, mon) + dt.timedelta(days=spec["maturity_days"])
                if as_of < ready:
                    note = (note + "; " if note else "") + f"provisional until {ready}"
            if spec.get("backfill_months"):
                note = (note + "; " if note else "") + "provisional, re-pulled next runs"
            cell_reqs.append(update_req(sid, row, new_idx, number_cell(v), "userEnteredValue"))
            record(tab, row, new_idx, spec["label"], disp, "value", note)

    # Backfills into earlier columns.
    pct = {"numberFormat": {"type": "PERCENT", "pattern": "0%"}}
    for spec in registry["rows"]:
        tab = spec["tab"]
        sid = registry["tabs"][tab]["sheet_id"]
        grid, _ = grids[tab]
        report = reports.get(spec["report_id"])
        if spec.get("backfill_months"):
            for back in range(1, spec["backfill_months"] + 1):
                y, m = add_months(year, mon, -back)
                c = col_for(tab, y, m)
                raw, _ = value_for_month(spec, report, y, m, as_of)
                v, disp = format_value(raw, spec["format"])
                old = grid.get(spec["row"], c)
                if old.strip() == disp:
                    continue
                backfill_reqs.append(update_req(sid, spec["row"], c, number_cell(v), "userEnteredValue"))
                record(tab, spec["row"], c, spec["label"], disp, "backfill",
                       f"{month_label(y, m)} re-pulled, matured since last run")
        if spec["rule"] == "cohort":
            y, m = add_months(year, mon, -spec["cohort_lag_months"])
            ready = last_day(y, m) + dt.timedelta(days=7 * (spec["retention_weeks"] + 1))
            if as_of < ready:
                out["warnings"].append(f"{spec['label']}: {month_label(y, m)} cohort is not "
                                       f"complete until {ready}; left for the next run")
                continue
            c = col_for(tab, y, m)
            old = grid.get(spec["row"], c)
            raw, _ = value_for_month(spec, report, y, m, as_of)
            v, disp = format_value(raw, spec["format"])
            if old.strip():
                if old.strip() != disp:
                    out["warnings"].append(
                        f"{spec['label']}: {idx_to_col(c)}{spec['row']} already holds {old}, "
                        f"Mixpanel now says {disp}; not overwritten")
                continue
            backfill_reqs.append(update_req(sid, spec["row"], c,
                                            {"userEnteredValue": {"numberValue": v},
                                             "userEnteredFormat": pct},
                                            "userEnteredValue,userEnteredFormat.numberFormat"))
            record(tab, spec["row"], c, spec["label"], disp, "cohort",
                   f"{month_label(y, m)} cohort, W{spec['retention_weeks']} now complete")
            rr = spec["refund_row"]
            col = idx_to_col(c)
            formula = f"={col}{spec['row']}*1.15"
            backfill_reqs.append(update_req(sid, rr, c,
                                            {"userEnteredValue": {"formulaValue": formula},
                                             "userEnteredFormat": pct},
                                            "userEnteredValue,userEnteredFormat.numberFormat"))
            refund_disp = f"{int(half_up(v * 1.15 * 100, 0))}%"
            record(tab, rr, c, f"{spec['label']} (excl refunds 15%)", refund_disp, "cohort",
                   f"{formula}")

    # Copy first (it carries formulas and formats), then the values over it.
    out["requests"] = copy_reqs + cell_reqs + backfill_reqs
    return out


def print_preview(p, stream=sys.stdout):
    print(f"Month {p['month']} (as of {p['as_of']})", file=stream)
    for tab, cols in p["tabs"].items():
        print(f"\n{tab}: new column {cols['new_col']} after {cols['prev_col']}", file=stream)
        print(f"  {'cell':<6} {'row label':<52} {'old':>12} {'new':>12}  note", file=stream)
        for c in p["cells"]:
            if c["tab"] != tab or c["kind"] in ("formula",) or (c["kind"] == "blank" and not c["label"]):
                continue
            label = (c["label"] or "")[:52]
            print(f"  {c['cell']:<6} {label:<52} {c['old']:>12} {c['new']:>12}  {c['note']}",
                  file=stream)
    if p["warnings"]:
        print("\nWarnings:", file=stream)
        for w in p["warnings"]:
            print(f"  - {w}", file=stream)
    print(f"\n{len(p['requests'])} requests in plan.json", file=stream)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--month", required=True, help="YYYY-MM of the month to add")
    ap.add_argument("--reports-dir", required=True)
    ap.add_argument("--pm-read", required=True)
    ap.add_argument("--pm-first-col", required=True)
    ap.add_argument("--cm-read", required=True)
    ap.add_argument("--cm-first-col", required=True)
    ap.add_argument("--as-of", default=dt.date.today().isoformat())
    ap.add_argument("--registry", default=os.path.join(HERE, "registry.json"))
    ap.add_argument("--out", default="plan.json")
    a = ap.parse_args(argv)

    with open(a.registry, encoding="utf-8") as fh:
        registry = json.load(fh)
    y, m = (int(x) for x in a.month.split("-"))
    reads = {
        "Product Metrics": TabGrid("Product Metrics", load_json(a.pm_read), a.pm_first_col),
        "Content & Marketplace": TabGrid("Content & Marketplace", load_json(a.cm_read), a.cm_first_col),
    }
    try:
        p = plan(registry, load_reports(a.reports_dir), reads, (y, m),
                 dt.date.fromisoformat(a.as_of))
    except PlanError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(p, fh, indent=1)
    print_preview(p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
