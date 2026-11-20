#!/usr/bin/env python3
"""The `Variants` value of a `Learnings/Experiments` row: arm names, or nothing.

The experiment server has no variant field. The names live in the free-text
`notes`, which engineers write as "a: new hero copy, b: control, current copy"
or "a = old prompt; b = control, …". A row gets "a: <name>" lines only when
the notes name every arm from `a` in order; otherwise `Variants` stays empty
for `analyze-experiment` to fill. It never gets bare letters ("a:\\nb:"), which
read as arms with no description.

Usage:
    python3 variants.py repair             # rows whose Variants is bare letters only, and the fix
    python3 variants.py repair --apply     # write it: arm names from notes, else clear
    python3 variants.py repair --title X --apply   # only the named rows (repeatable)
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ARM = re.compile(r"(?:^|(?<=[\s,;.(]))([a-h])\s*[:=]\s+")
SENTENCE_END = re.compile(r"(?<=[a-z0-9)%])\.\s+(?=[A-Z])")
BLANK_LABELS = re.compile(r"^\s*(?:[a-h]\s*:\s*)+$")


def arm_names(notes: Optional[str]) -> Optional[dict[str, str]]:
    """{letter: name} when the notes label arms a, b, … in order; else None."""
    text = (notes or "").replace("'\\''", "'")  # a shell-quoting artifact some notes carry
    marks = list(ARM.finditer(text))
    # Keep the first run that starts at `a` and steps one letter at a time.
    run = []
    for m in marks:
        want = chr(ord("a") + len(run))
        if m.group(1) == want:
            run.append(m)
        elif m.group(1) == "a":
            run = [m]
    if len(run) < 2:
        return None
    names = {}
    for i, m in enumerate(run):
        end = run[i + 1].start() if i + 1 < len(run) else len(text)
        value = text[m.end():end]
        if i + 1 == len(run):
            cut = SENTENCE_END.search(value)
            value = value[:cut.start()] if cut else value
        value = value.strip().rstrip(",;.").strip()
        if not value:
            return None
        names[m.group(1)] = value
    return names


def variants_value(notes: Optional[str]) -> Optional[str]:
    """The text to write, or None to leave Variants empty. Never bare letters."""
    names = arm_names(notes)
    if not names:
        return None
    return "\n".join(f"{k}: {v}" for k, v in names.items())


def is_blank_labels(text: Optional[str]) -> bool:
    """True for "a:", "a:\\nb:" and the like: letters with no description."""
    return bool(text) and bool(BLANK_LABELS.match(text))


def cmd_repair(args: argparse.Namespace) -> int:
    import reconcile
    from notion_rest import notion, title_text, rich_text_value, make_rich_text
    if not notion.available:
        sys.exit("NOTION_TOKEN is not set — cannot read Learnings/Experiments.")
    notes = {r.get("id"): r.get("notes") for r in reconcile.fetch_catalogue(args.catalogue)}
    todo = []
    for row in notion.query_data_source(reconcile.EXPERIMENTS_DS, page_size=100):
        current = rich_text_value(row, "Variants")
        title = title_text(row, "Title").strip()
        if is_blank_labels(current) and (not args.title or title in args.title):
            todo.append((title, row["id"], variants_value(notes.get(title))))
    named = sum(1 for t in todo if t[2])
    print(f"{len(todo)} row(s) with bare-letter Variants: {named} get arm names from the experiment "
          f"notes, {len(todo) - named} are cleared (no names in the notes, or not in the catalogue).")
    for title, _, value in todo:
        print(f"  {title}: {'names' if value else 'clear'}")
    if not args.apply or not todo:
        return 0
    failed = 0
    for title, page_id, value in todo:
        try:
            prop = make_rich_text(value) if value else {"rich_text": []}
            notion.update_page_props(page_id, {"Variants": prop})
        except Exception as e:  # noqa: BLE001 — one bad row must not strand the rest
            failed += 1
            print(f"  {title} FAILED — {e}", file=sys.stderr)
    print(f"{len(todo) - failed} written, {failed} failed.")
    reconcile._invalidate_cache()
    return 1 if failed else 0


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--catalogue", help="Read GET /experiments from this JSON file instead of the API.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("repair", help="Fix rows whose Variants is bare letters.")
    r.add_argument("--apply", action="store_true", help="Write the fix (default: print it).")
    r.add_argument("--title", action="append",
                   help="Only this experiment (repeatable). Without it, every row with bare letters.")
    args = ap.parse_args()
    return cmd_repair(args)


if __name__ == "__main__":
    sys.exit(main())
