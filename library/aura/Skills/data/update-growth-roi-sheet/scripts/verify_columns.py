#!/usr/bin/env python3
"""Verify a Growth ROI tracker write landed exactly as intended.

Takes the sheet read back after writing and the JSON that build_columns.py produced,
and diffs them cell by cell. Also confirms nothing outside the target columns moved,
if a pre-write snapshot is supplied.

Exits non-zero on any mismatch, so it can gate a "verified" claim.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

A = ord("A")


def col_index(letter: str) -> int:
    """AM -> 38 (zero-based)."""
    n = 0
    for ch in letter.strip().upper():
        if not ("A" <= ch <= "Z"):
            raise ValueError(f"bad column letter: {letter!r}")
        n = n * 26 + (ord(ch) - A + 1)
    return n - 1


def load_rows(path: pathlib.Path) -> list[list[str]]:
    """Accept either a bare [[...]] grid or the Sheets MCP {result: "json"} wrapper."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        if "result" in raw and isinstance(raw["result"], str):
            raw = json.loads(raw["result"])
        raw = raw.get("rows", raw)
    if not isinstance(raw, list):
        raise ValueError(f"{path}: expected a list of rows")
    if raw and isinstance(raw[0], dict):
        raise ValueError(
            f"{path}: rows are dict-shaped, which means the read used has_headers=true. "
            "Re-read the range with has_headers=false so rows come back as flat arrays.")
    return raw


def cell(rows: list[list[str]], r: int, i: int) -> str:
    row = rows[r] if r < len(rows) else []
    return row[i] if i < len(row) else ""


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sheet", type=pathlib.Path, required=True,
                    help="rows read back after writing (grid starting at the anchor row)")
    ap.add_argument("--expected", type=pathlib.Path, required=True,
                    help="JSON from build_columns.py")
    ap.add_argument("--before", type=pathlib.Path, default=None,
                    help="optional pre-write snapshot, to prove nothing else changed")
    ap.add_argument("--first-column", type=str, default="A",
                    help="leftmost column of the read-back grid (default A). Set this if "
                         "you read a narrower range than the full-width block.")
    ap.add_argument("--first-row", type=int, default=3,
                    help="sheet row the read-back's first line corresponds to (default 3, "
                         "the grid origin). The build's start_row is offset against this.")
    args = ap.parse_args()

    now = load_rows(args.sheet)
    payload = json.loads(args.expected.read_text(encoding="utf-8"))
    row_offset = 0
    if isinstance(payload, dict) and "columns" in payload:
        expected = payload["columns"]
        start_row = int(payload.get("start_row", args.first_row))
        row_offset = start_row - args.first_row
        if row_offset < 0:
            raise ValueError(
                f"build starts at row {start_row} but the read-back starts at row "
                f"{args.first_row}; re-read from row {start_row} or earlier.")
        width = max(len(v) for v in expected.values())
        if row_offset + width > len(now):
            raise ValueError(
                f"read-back has {len(now)} rows from row {args.first_row}, too few to "
                f"cover rows {start_row}..{start_row + width - 1}. Re-read the range.")
        print(f"comparing rows {start_row}..{start_row + width - 1} "
              f"({payload.get('first_week')}..{payload.get('last_week')})")
    else:
        expected = payload
    origin = col_index(args.first_column)
    widest = max(len(r) for r in now) if now else 0
    for col in expected:
        if not 0 <= col_index(col) - origin < max(widest, 1):
            raise ValueError(
                f"column {col} falls outside a grid that starts at {args.first_column} "
                f"and is {widest} wide. Re-read starting at column A, or pass "
                "--first-column to match the range you actually read.")
    targets = {col_index(c) - origin for c in expected}

    failures = 0
    populated = 0
    for col, values in sorted(expected.items()):
        i = col_index(col) - origin
        diffs = []
        for r, want in enumerate(values):
            got = cell(now, r + row_offset, i)
            if got != want:
                diffs.append((r, got, want))
            elif want:
                populated += 1
        status = "ok" if not diffs else f"{len(diffs)} MISMATCH"
        print(f"{col}: {status}")
        for d in diffs[:5]:
            print(f"    row={args.first_row + row_offset + d[0]} "
                  f"sheet={d[1]!r} expected={d[2]!r}")
        failures += len(diffs)

    if args.before:
        before = load_rows(args.before)
        spill = 0
        width = max((len(r) for r in before + now), default=0)
        for r in range(min(len(before), len(now))):
            for i in range(width):
                if i in targets:
                    continue
                if cell(before, r, i) != cell(now, r, i):
                    spill += 1
                    if spill <= 5:
                        print(f"    SPILL row_offset={r} col_index={i}: "
                              f"{cell(before, r, i)!r} -> {cell(now, r, i)!r}")
        print(f"cells changed outside target columns: {spill}")
        failures += spill

    print(f"populated cells written: {populated}")
    if failures:
        print(f"FAILED: {failures} problem(s)", file=sys.stderr)
        sys.exit(1)
    print("All checks passed.")


if __name__ == "__main__":
    try:
        main()
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)
