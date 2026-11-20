#!/usr/bin/env python3
"""Probe the store-vs-warehouse contract before anything writes a day.

Two questions, both cheap, both asked of BigQuery's own metadata:

1. **Schema drift.** Does every column the store declares (`bq_sink.STORES[*]
   ["schema"]`) exist in the warehouse table? A column present locally and absent
   in BigQuery makes every future partition load 400 with
   `Cannot add fields (field: <name>)` — the partition load carries no
   schema-update option, so the only remedy is `bq_sink.py --recreate`. This is a
   **refusal**: there is no benign cause and no day can sync until it is fixed.

2. **Max-date divergence.** Where does each store sit locally (the CSV) versus in
   the warehouse, and do the three warehouse tables agree with each other? A gap
   is **reported, never refused** — a store legitimately skips a date it has no
   rows for, so divergence has benign causes that drift does not. Reporting is
   still the point: the ENG-1306/`voice_fish` incident ran for days because the
   per-user table sat at 2026-09-17 while the other two reached 09-19 and nothing
   said so, while every `chart-aurie-*` read of the `_prod` view stayed clean,
   plausible and understated.

Exit codes follow `manifest.py --check-write`: **0** clean (divergence may still
be reported), **1** refused (schema drift), **2** the probe could not run.

Stdlib + google-cloud-bigquery only.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bq_sink  # noqa: E402  (STORES is the declared contract; _client is the auth ladder)

EXIT_OK, EXIT_REFUSE, EXIT_UNRESOLVABLE = 0, 1, 2

# Point at the bq_sink.py sitting next to THIS file, so the remedy is runnable
# wherever the skill was loaded from — the in-repo tree, another checkout, or the
# plugin install cache, where a repo-relative path does not exist at all.
RECREATE_HINT = f"python3 {os.path.join(os.path.dirname(os.path.abspath(__file__)), 'bq_sink.py')} --recreate"


# --- pure logic (no BigQuery) -------------------------------------------------

def diff_schema(local_cols, warehouse_cols) -> dict:
    """Compare declared store columns against the warehouse's own column list.

    `local_only` is the refusal condition — those columns break partition loads.
    `warehouse_only` is reported but benign: the load names its columns, so an
    extra warehouse column is simply never written.
    """
    wh = set(warehouse_cols)
    loc = set(local_cols)
    return {
        "local_only": [c for c in local_cols if c not in wh],
        "warehouse_only": [c for c in warehouse_cols if c not in loc],
    }


def build_report(probes: dict) -> dict:
    """Fold per-store probe results into a verdict.

    `probes` maps store key -> {"local_cols", "warehouse_cols", "local_max",
    "warehouse_max", "table", "missing_table"}. Returns the same shape enriched
    with the drift diff, plus `refuse`, `reasons` and the cross-table divergence.
    """
    stores, reasons, refuse = {}, [], False
    for key, p in probes.items():
        if p.get("missing_table"):
            # An absent table is not drift — the sync creates it on first write.
            stores[key] = dict(p, local_only=[], warehouse_only=[], drifted=False)
            continue
        d = diff_schema(p["local_cols"], p["warehouse_cols"])
        drifted = bool(d["local_only"])
        if drifted:
            refuse = True
            reasons.append(
                f"{key} ({p['table']}): column(s) {d['local_only']} declared by the store but "
                f"absent in BigQuery — every partition load will 400 "
                f"(Cannot add fields). Remedy: {RECREATE_HINT}"
            )
        stores[key] = dict(p, drifted=drifted, **d)

    # Store-vs-warehouse gaps, and the mixed state across the three tables.
    behind = {k: (s["warehouse_max"], s["local_max"]) for k, s in stores.items()
              if s.get("local_max") and s.get("warehouse_max")
              and s["warehouse_max"] < s["local_max"]}
    wh_maxes = {k: s["warehouse_max"] for k, s in stores.items() if s.get("warehouse_max")}
    divergent = len(set(wh_maxes.values())) > 1

    return {
        "stores": stores,
        "refuse": refuse,
        "reasons": reasons,
        "behind": behind,          # warehouse older than the local store
        "warehouse_maxes": wh_maxes,
        "tables_divergent": divergent,
    }


def render(report: dict) -> str:
    """The plan lines the cadence pastes into its 'what's due' block."""
    out = []
    for key, s in report["stores"].items():
        if s.get("missing_table"):
            out.append(f"- {key} ({s['table']}): table absent in BigQuery — will be created on first write")
            continue
        drift = (f"DRIFT local-only {s['local_only']}" if s["local_only"]
                 else f"schema OK ({len(s['local_cols'])} cols)")
        if s["warehouse_only"]:
            drift += f"; warehouse-only {s['warehouse_only']} (ignored by the load)"
        gap = ""
        if key in report["behind"]:
            wh, loc = report["behind"][key]
            gap = f"  ** BEHIND by store: warehouse {wh} < local {loc} **"
        out.append(
            f"- {key} ({s['table']}): store max {s.get('local_max') or '-'} | "
            f"warehouse max {s.get('warehouse_max') or '-'} | {drift}{gap}"
        )
    if report["tables_divergent"]:
        out.append(f"- ** MIXED STATE: the three warehouse tables disagree on max date: "
                   f"{report['warehouse_maxes']} **")
    if report["refuse"]:
        out.append("")
        out.append("REFUSE — schema drift blocks every partition load:")
        out.extend(f"  * {r}" for r in report["reasons"])
    return "\n".join(out)


# --- BigQuery probes ----------------------------------------------------------

def _warehouse_columns(client, project: str, dataset: str) -> dict:
    """table_name -> ordered column list, straight from INFORMATION_SCHEMA."""
    sql = (f"SELECT table_name, column_name FROM `{project}.{dataset}.INFORMATION_SCHEMA.COLUMNS` "
           "ORDER BY table_name, ordinal_position")
    cols: dict = {}
    for row in client.query(sql).result():
        cols.setdefault(row["table_name"], []).append(row["column_name"])
    return cols


def _warehouse_max_dates(client, project: str, dataset: str, tables: list) -> dict:
    """table_name -> max(date) as YYYY-MM-DD, or None when the table is empty."""
    present = set()
    for row in client.query(
        f"SELECT table_name FROM `{project}.{dataset}.INFORMATION_SCHEMA.TABLES` "
        "WHERE table_type = 'BASE TABLE'").result():
        present.add(row["table_name"])
    out = {}
    for t in tables:
        if t not in present:
            out[t] = None
            continue
        row = list(client.query(
            f"SELECT CAST(MAX(date) AS STRING) AS mx FROM `{project}.{dataset}.{t}`").result())[0]
        out[t] = row["mx"]
    return out


def _csv_max_date(path: str):
    if not path or not os.path.exists(path):
        return None
    mx = None
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            d = r.get("date")
            if d and (mx is None or d > mx):
                mx = d
    return mx


def probe(project: str, dataset: str, csv_paths: dict | None = None) -> dict:
    """Run both probes against the live warehouse and return `build_report`'s shape."""
    client = bq_sink._client(project)
    wh_cols = _warehouse_columns(client, project, dataset)
    tables = [spec["table"] for spec in bq_sink.STORES.values()]
    wh_max = _warehouse_max_dates(client, project, dataset, tables)
    csv_paths = csv_paths or {
        k: os.path.expanduser(os.environ.get(v.get("env", "")) or v["default_csv"])
        for k, v in bq_sink.STORES.items()
    }
    probes = {}
    for key, spec in bq_sink.STORES.items():
        t = spec["table"]
        probes[key] = {
            "table": t,
            "missing_table": t not in wh_cols,
            "local_cols": [n for n, _ in spec["schema"]],
            "warehouse_cols": wh_cols.get(t, []),
            "local_max": _csv_max_date(csv_paths.get(key)),
            "warehouse_max": wh_max.get(t),
        }
    return build_report(probes)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(
        description="Probe store-vs-warehouse schema drift and max-date divergence.")
    ap.add_argument("--project", default=os.environ.get("AURIE_BQ_PROJECT", bq_sink.PROJECT))
    ap.add_argument("--dataset", default=os.environ.get("AURIE_BQ_DATASET", bq_sink.DATASET))
    ap.add_argument("--json", action="store_true", help="emit the raw report as JSON")
    args = ap.parse_args()

    try:
        report = probe(args.project, args.dataset)
    except bq_sink.BQUnavailable as e:
        # Unresolvable is its own exit code: a probe that could not run must not
        # read as a clean one (the whole point of the card).
        print(f"preflight could not run: {e}", file=sys.stderr)
        return EXIT_UNRESOLVABLE
    except Exception as e:  # noqa: BLE001
        print(f"preflight could not run: {type(e).__name__}: {e}", file=sys.stderr)
        return EXIT_UNRESOLVABLE

    print(json.dumps(report, indent=2, default=str) if args.json else render(report))
    return EXIT_REFUSE if report["refuse"] else EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
