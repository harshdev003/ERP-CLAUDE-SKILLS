#!/usr/bin/env python3
"""Check a written monthly update against its plan.

Reads plan.json plus a read-back of each tab taken after the write (the same
range as the pre-write read) and, optionally, the pre-write reads. Checks:

  * every planned value, backfill and cohort cell shows exactly the planned text;
  * every formula row in the new column shows a value (the copy carried it);
  * every planned blank is empty;
  * with --pm-before/--cm-before, no other cell in the read range changed.

Usage:
  verify_update.py --plan plan.json \
      --pm-read pm_after.json --pm-first-col CN \
      --cm-read cm_after.json --cm-first-col CB \
      [--pm-before pm_before.json --cm-before cm_before.json]
Exit code 0 when everything matches, 1 otherwise.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from plan_update import TabGrid, col_to_idx, load_json  # noqa: E402

TABS = ("Product Metrics", "Content & Marketplace")


def split_cell(ref):
    i = 0
    while i < len(ref) and ref[i].isalpha():
        i += 1
    return col_to_idx(ref[:i]), int(ref[i:])


def verify(plan, after, before=None):
    problems = []
    planned = set()
    for c in plan["cells"]:
        tab = c["tab"]
        col, row = split_cell(c["cell"])
        planned.add((tab, row, col))
        got = after[tab].get(row, col).strip()
        if c["kind"] == "formula":
            if not got:
                problems.append(f"{tab} {c['cell']}: formula row is empty after the copy")
        elif c["kind"] == "blank":
            if got:
                problems.append(f"{tab} {c['cell']}: expected blank, found {got!r}")
        elif got != c["expect"]:
            problems.append(f"{tab} {c['cell']} ({c['label']}): expected {c['expect']!r}, found {got!r}")

    if before:
        for tab in TABS:
            b, a = before[tab], after[tab]
            for r, row in enumerate(b.rows, start=1):
                for i, old in enumerate(row):
                    col = b.first + i
                    if (tab, r, col) in planned:
                        continue
                    new = a.get(r, col)
                    if str(old).strip() != new.strip():
                        problems.append(f"{tab} row {r} col {col}: changed {old!r} -> {new!r} "
                                        "but was not in the plan")
    return problems


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--plan", required=True)
    ap.add_argument("--pm-read", required=True)
    ap.add_argument("--pm-first-col", required=True)
    ap.add_argument("--cm-read", required=True)
    ap.add_argument("--cm-first-col", required=True)
    ap.add_argument("--pm-before")
    ap.add_argument("--cm-before")
    a = ap.parse_args(argv)

    plan = load_json(a.plan)
    after = {
        TABS[0]: TabGrid(TABS[0], load_json(a.pm_read), a.pm_first_col),
        TABS[1]: TabGrid(TABS[1], load_json(a.cm_read), a.cm_first_col),
    }
    before = None
    if a.pm_before and a.cm_before:
        before = {
            TABS[0]: TabGrid(TABS[0], load_json(a.pm_before), a.pm_first_col),
            TABS[1]: TabGrid(TABS[1], load_json(a.cm_before), a.cm_first_col),
        }
    problems = verify(plan, after, before)
    checked = sum(1 for c in plan["cells"] if c["kind"] != "formula")
    if problems:
        print(f"{len(problems)} problem(s):")
        for p in problems:
            print(f"  - {p}")
        return 1
    spill = "and nothing else in the read range changed" if before else "(spill check skipped: no --*-before reads)"
    print(f"OK: {checked} planned cells match, formula rows populated, {spill}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
