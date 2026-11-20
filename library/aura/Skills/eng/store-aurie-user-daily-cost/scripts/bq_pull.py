#!/usr/bin/env python3
"""Rebuild the local daily-cost CSVs FROM BigQuery (the source of truth).

BigQuery (`aura-main.aurie_cost.*`) is authoritative; the local CSVs are a
disposable cache. This regenerates them from BQ — use it on a fresh machine, to
repair a corrupted/partial CSV, or after a schema `--recreate`. Whole table by
default, or a `--from` / `--to` date range (writes only those rows).

Column order + table names come from `bq_sink.STORES` so the two stay in lockstep.
Hard-requires BigQuery (per `GCP_KEY`) — there is no CSV to fall back to; that's
the point.

Stdlib + google-cloud-bigquery only.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bq import describe_table, query, table_exists, BQUnavailable, BQError, PROJECT, DATASET  # noqa: E402
from bq_sink import STORES  # noqa: E402

# csv defaults to \r\n. These stores are read by shell tooling (awk -F, / cut) as
# often as by the csv module, and a CRLF file leaves a stray \r on the LAST column
# — activity_source reads as "ambient\\r" to awk while csv strips it. Write LF.
LF = "\n"


def _fmt(v) -> str:
    if v is None:
        return ""
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()[:10] if isinstance(v, dt.date) and not isinstance(v, dt.datetime) else v.isoformat()
    return str(v)


def _fetch(spec: dict, project: str, dataset: str, date_from, date_to):
    cols = [n for n, _ in spec["schema"]]
    table = f"{project}.{dataset}.{spec['table']}"
    if not table_exists(table, project):
        return cols, None
    # SELECT only columns the table actually has. A schema addition (e.g. ENG-982's
    # voice_11labs_intelligence) lands in the local schema BEFORE the BigQuery
    # migration runs, and selecting a not-yet-existing column 400s — which would
    # break the very pull-then-recreate flow used to perform that migration.
    # Missing columns are emitted blank and filled by the CSV migration downstream.
    try:
        have = {f["name"] for f in describe_table(table, project)["schema"]}
        sel = [c for c in cols if c in have]
        if len(sel) != len(cols):
            print(f"  ! {spec['table']}: {sorted(set(cols) - have)} not in BigQuery yet — "
                  "pulled blank (run bq_sink.py --recreate to add them)", file=sys.stderr)
    except Exception:  # noqa: BLE001 — fall back to the full list
        sel = cols
    clauses, params = [], {}
    if date_from:
        clauses.append("date >= @f"); params["f"] = date_from
    if date_to:
        clauses.append("date <= @t"); params["t"] = date_to
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    order = "date, user_id" if "user_id" in sel else "date"
    sql = f"SELECT {', '.join(sel)} FROM `{table}`{where} ORDER BY {order}"
    # Return the columns actually PULLED, not the full local schema. The written
    # header must describe the data: emitting the current header with a blank
    # not-yet-migrated column makes _migrate_old_csv see a current-schema file and
    # skip the ENG-982 Other-row move, so the pull-then-recreate backfill would
    # reload the mis-booked history unchanged. With `sel` the file carries the
    # recognised OLD header and the migration fires as intended.
    return sel, query(sql, project, params=params or None)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Rebuild the local daily-cost CSVs from BigQuery.")
    ap.add_argument("--from", dest="date_from", help="YYYY-MM-DD lower bound (inclusive)")
    ap.add_argument("--to", dest="date_to", help="YYYY-MM-DD upper bound (inclusive)")
    ap.add_argument("--all", action="store_true",
                    help="pull the whole table (the default when no --from/--to is given)")
    ap.add_argument("--project", default=os.environ.get("AURIE_BQ_PROJECT", PROJECT))
    ap.add_argument("--dataset", default=os.environ.get("AURIE_BQ_DATASET", DATASET))
    ap.add_argument("--csv", help="per-user CSV path (else $AURIE_COST_CSV / default)")
    ap.add_argument("--unit-csv", help="unit-cost CSV path (else $AURIE_UNIT_COST_CSV / default)")
    ap.add_argument("--kpi-csv", help="KPI CSV path (else $AURIE_KPI_CSV / default)")
    args = ap.parse_args()

    # --flag > $AURIE_*_CSV > cache-dir default; expanduser so a legacy env var
    # with ~ resolves to the same path the writer scripts (via paths.resolve) use.
    paths = {
        "per_user": os.path.expanduser(args.csv or os.environ.get(STORES["per_user"]["env"]) or STORES["per_user"]["default_csv"]),
        "unit_costs": os.path.expanduser(args.unit_csv or os.environ.get(STORES["unit_costs"]["env"]) or STORES["unit_costs"]["default_csv"]),
        "eng_kpis": os.path.expanduser(args.kpi_csv or os.environ.get(STORES["eng_kpis"]["env"]) or STORES["eng_kpis"]["default_csv"]),
    }

    try:
        wrote_any = False
        for key, spec in STORES.items():
            path = paths.get(key)
            if not path:
                print(f"  - {key}: no output path (unset) — skipped")
                continue
            cols, rows = _fetch(spec, args.project, args.dataset, args.date_from, args.date_to)
            if rows is None:
                print(f"  - {key}: table {spec['table']} not found in {args.project}:{args.dataset} — skipped")
                continue
            out = [{c: _fmt(r.get(c)) for c in cols} for r in rows]
            # Ranged pull: preserve local rows OUTSIDE the range instead of
            # truncating the file to just the queried dates.
            ranged = bool(args.date_from or args.date_to)
            if ranged and os.path.exists(path):
                with open(path, newline="") as f:
                    for er in csv.DictReader(f):
                        d = er.get("date", "")
                        in_range = ((not args.date_from or d >= args.date_from)
                                    and (not args.date_to or d <= args.date_to))
                        if not in_range:
                            out.append({c: er.get(c, "") for c in cols})
            key_fn = (lambda r: (r.get("date", ""), r.get("user_id", ""))) if "user_id" in cols \
                else (lambda r: r.get("date", ""))
            out.sort(key=key_fn)
            os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)  # cache dir may not exist yet
            tmp = path + ".tmp"
            with open(tmp, "w", newline="") as f:
                w = csv.writer(f, lineterminator=LF)
                w.writerow(cols)
                for r in out:
                    w.writerow([r.get(c, "") for c in cols])
            os.replace(tmp, path)  # atomic
            print(f"  + {key}: wrote {len(rows)} pulled row(s)"
                  f"{f' (merged; {len(out)} total)' if ranged else ''} -> {path}")
            wrote_any = True
        if not wrote_any:
            print("bq_pull wrote nothing (no output paths / no tables found).", file=sys.stderr)
            return 1
        return 0
    except BQUnavailable as e:
        print(f"BigQuery unavailable: {e} — cannot rebuild from the source of truth.", file=sys.stderr)
        return 1
    except BQError as e:
        print(f"BigQuery error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
