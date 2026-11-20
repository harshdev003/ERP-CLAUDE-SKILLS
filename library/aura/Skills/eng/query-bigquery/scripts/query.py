#!/usr/bin/env python3
"""Run ad-hoc BigQuery against the Aura warehouse (`aura-main`), read-only by default.

  query.py --sql "SELECT date, total_cost FROM aurie_cost.aurie_daily_unit_costs ORDER BY date DESC LIMIT 5"
  query.py --file report.sql --json
  query.py --list                      # tables in aura-main:aurie_cost
  query.py --describe aurie_cost.aurie_per_user_daily_cost
  query.py --sql "..." --dry-run       # price the scan without running it

Defaults: project `aura-main`. A bare `dataset.table` in SQL resolves against
`aura-main`. DML/DDL (INSERT/UPDATE/DELETE/MERGE/CREATE/DROP/TRUNCATE/ALTER) is
refused unless `--allow-write` is passed. Every query is capped at
`--max-gb` (default 10) of scanned bytes so a runaway scan fails fast.

Auth: the `GCP_KEY` service account (no BigQuery MCP exists — API-first).
"""
from __future__ import annotations

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bq import (  # noqa: E402
    query, list_tables, describe_table, BQError, BQUnavailable, PROJECT, DATASET,
)

# Read-only allowlist: a single statement that STARTS with one of these.
_READ_STARTERS = ("select", "with")
# DML/DDL keywords that must never appear (as whole words) in a read-only query —
# catches `WITH cte AS (...) DELETE ...` and friends.
_WRITE_WORDS = ("insert", "update", "delete", "merge", "create", "drop",
                "truncate", "alter", "grant", "revoke")


def _strip_literals(sql: str) -> str:
    """Drop comments + string/backtick-identifier contents so the keyword scan
    can't be fooled by a keyword inside a string or an identifier (and won't
    false-refuse a legit SELECT that merely mentions one)."""
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)                      # block comments
    sql = "\n".join(re.split(r"--|#", ln, 1)[0] for ln in sql.splitlines())  # line comments
    sql = re.sub(r"'(?:[^'\\]|\\.)*'", "''", sql)                        # single-quoted strings
    sql = re.sub(r'"(?:[^"\\]|\\.)*"', '""', sql)                        # double-quoted strings
    sql = re.sub(r"`[^`]*`", "``", sql)                                   # backtick identifiers
    return sql


def _is_write(sql: str) -> bool:
    """Conservative read-only guard. Refuses anything that isn't a single
    SELECT/WITH statement free of DML/DDL keywords. Errs toward refusing (safe):
    a false refuse just needs --allow-write; a false allow could run a mutation.
    Blocks the first-keyword-only bypasses (WITH … DELETE, multi-statement
    scripts, BEGIN/DECLARE/EXECUTE/CALL/EXPORT/LOAD …)."""
    clean = _strip_literals(sql).strip()
    if not clean:
        return False
    stmts = [s for s in clean.split(";") if s.strip()]
    if len(stmts) > 1:
        return True  # multi-statement script — not read-only
    stmt = stmts[0]
    first = (stmt.split(None, 1)[0] if stmt.split() else "").lower()
    if first not in _READ_STARTERS:
        return True  # starts with anything but SELECT/WITH (INSERT/BEGIN/DECLARE/CALL/…)
    return bool(re.search(r"\b(" + "|".join(_WRITE_WORDS) + r")\b", stmt, flags=re.I))


def _print_table(rows: list) -> None:
    if not rows:
        print("(0 rows)")
        return
    cols = list(rows[0].keys())
    widths = {c: max(len(c), *(len(str(r.get(c, ""))) for r in rows)) for c in cols}
    line = "  ".join(c.ljust(widths[c]) for c in cols)
    print(line)
    print("  ".join("-" * widths[c] for c in cols))
    for r in rows:
        print("  ".join(str(r.get(c, "")).ljust(widths[c]) for c in cols))
    print(f"\n({len(rows)} row{'s' if len(rows) != 1 else ''})")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Ad-hoc BigQuery against aura-main (read-only by default).")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--sql", help="SQL to run")
    g.add_argument("--file", help="path to a .sql file to run")
    g.add_argument("--list", nargs="?", const=DATASET, metavar="DATASET",
                   help=f"list tables in a dataset (default {DATASET})")
    g.add_argument("--describe", metavar="TABLE", help="show a table's schema (dataset.table)")
    ap.add_argument("--project", default=os.environ.get("AURIE_BQ_PROJECT", PROJECT))
    ap.add_argument("--json", action="store_true", help="emit JSON instead of a text table")
    ap.add_argument("--dry-run", action="store_true", help="price the scan without running")
    ap.add_argument("--allow-write", action="store_true", help="permit DML/DDL (default: refuse)")
    ap.add_argument("--max-gb", type=float, default=10.0, help="cap scanned GB (default 10)")
    args = ap.parse_args()

    import json as _json

    def _qualify(t: str) -> str:
        return t if t.count(".") >= 2 else f"{args.project}.{t}"

    try:
        if args.list is not None:
            tables = list_tables(args.list, args.project)
            print(_json.dumps(tables) if args.json else "\n".join(tables) or "(no tables)")
            return 0
        if args.describe:
            info = describe_table(_qualify(args.describe), args.project)
            print(_json.dumps(info, indent=2, default=str) if args.json else _fmt_describe(info))
            return 0

        sql = args.sql if args.sql else open(args.file).read()
        if _is_write(sql) and not args.allow_write:
            print("Refusing a write/DDL statement (INSERT/UPDATE/DELETE/MERGE/CREATE/DROP/…). "
                  "Pass --allow-write if you really mean it.", file=sys.stderr)
            return 2

        rows = query(sql, args.project, dry_run=args.dry_run,
                     max_bytes=int(args.max_gb * 1024 ** 3))
        if args.dry_run:
            gb = (rows[0]["bytes"] or 0) / 1024 ** 3
            print(f"dry-run: ~{gb:.3f} GB scanned (~${gb * 6.25 / 1000:.4f} at $6.25/TB)")
            return 0
        if args.json:
            print(_json.dumps(rows, indent=2, default=str))
        else:
            _print_table(rows)
        return 0
    except BQUnavailable as e:
        print(f"BigQuery unavailable: {e}", file=sys.stderr)
        return 1
    except BQError as e:
        print(f"BigQuery error: {e}", file=sys.stderr)
        return 1


def _fmt_describe(info: dict) -> str:
    out = [f"{info['table']}  ({info['rows']} rows)",
           f"  partition: {info['partitioning'] or '(none)'}   cluster: {info['clustering'] or '(none)'}",
           "  columns:"]
    out += [f"    {n}  {t}  {m}" for n, t, m in info["schema"]]
    return "\n".join(out)


if __name__ == "__main__":
    raise SystemExit(main())
