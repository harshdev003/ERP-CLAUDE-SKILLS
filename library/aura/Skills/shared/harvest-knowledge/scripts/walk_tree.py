#!/usr/bin/env python3
"""Walk the AI Knowledge tree for `Guide/Index`: every page and database under each section, to the leaves.

    python3 walk_tree.py --sections sections.json --out walk.json [--live-count N] [--allow-shrink]

`sections.json` is the `{section title: page id}` map from references/about-sections.md. The walk lists
each page's children and descends into **every block that has children** (toggles, columns, callouts,
synced blocks, list items, quotes), because a child page can sit inside any of them: its parent is then
a block, not a page, and a walk that only follows `child_page` blocks drops it. It records `child_page`
and `child_database` blocks at any depth, with their path and the URL the API returns. Databases are
listed but not descended (their rows are not index entries).

One entry per page id. A page with no title is kept as `(untitled)` (its children are walked under that
path); two pages sharing a title under one parent are both kept, each flagged `duplicate_path`. So the
count is the real number of pages and databases, not the number of distinct paths.

Exclusions (references/guide-index.md, step 2): the section roots themselves, `Guide/Index`, the
`Guide/Harvest` and `Guide/Health Check` subtrees, and dated `Signals/<topic>` snapshot pages.

Collapse guard (step 6): with `--live-count`, the walk refuses (exit 3, nothing for the caller to
write) when no section resolved, or when it found fewer paths than the live index by more than
max(3, 2%). A walk that loses pages and a real restructure look the same; `--allow-shrink` is the
explicit acknowledgement of the second.

Token: NOTION_TOKEN (read-only).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# Blocks whose children cannot hold a page: table rows are cells, and a database's rows are not
# index entries. Everything else with has_children is opened.
NO_DESCEND = {"table", "table_row", "child_database", "unsupported"}
EXCLUDED_SUBTREES = ("Guide/Harvest", "Guide/Health Check")
EXCLUDED_PATHS = {"Guide/Index"}
DATED = re.compile(r"\d{4}-\d{2}-\d{2}")
COLLAPSE_EXIT = 3
UNTITLED = "(untitled)"


def excluded(path: str) -> bool:
    if path in EXCLUDED_PATHS or any(path == s or path.startswith(s + "/") for s in EXCLUDED_SUBTREES):
        return True
    parts = path.split("/")
    # A dated snapshot child of a Signals topic; the date sits anywhere in the title.
    return parts[0] == "Signals" and len(parts) >= 3 and bool(DATED.search(parts[-1]))


def walk(sections: dict, children, url_of) -> dict:
    """{"entries": [...], "stats": {...}}. `children(block_id)` lists a block's children (all pages);
    `url_of(kind, id)` returns the API's url for a page or database."""
    entries, seen = [], set()
    stats = {"sections": len(sections), "blocks_opened": 0, "nested_in_blocks": 0, "excluded": 0, "untitled": 0}

    def visit(block_id, parent_path, inside_block):
        for b in children(block_id):
            kind = b.get("type")
            if kind in ("child_page", "child_database"):
                if b["id"] in seen:
                    continue  # a synced copy of a block already walked: the same page, listed once
                seen.add(b["id"])
                title = ((b.get(kind) or {}).get("title") or "").strip()
                # An untitled page is still a page, and its children still need a path: keep it.
                stats["untitled"] += not title
                path = f"{parent_path}/{title or UNTITLED}"
                if excluded(path):
                    stats["excluded"] += 1
                    continue  # an excluded page's subtree is excluded with it
                entries.append({"path": path, "id": b["id"], "kind": "page" if kind == "child_page" else "database",
                                "url": url_of(kind, b["id"]), "in_block": inside_block, "untitled": not title})
                stats["nested_in_blocks"] += bool(inside_block)
                if kind == "child_page":
                    visit(b["id"], path, False)
            elif b.get("has_children") and kind not in NO_DESCEND:
                stats["blocks_opened"] += 1
                visit(b["id"], parent_path, True)

    for title, page_id in sections.items():
        visit(page_id, title.strip(), False)
    entries.sort(key=lambda e: (e["path"], e["id"]))
    # Two pages can share a title under one parent: both are real, so both are kept (one entry per
    # page id) and flagged, since a path alone can't tell them apart.
    counts = {}
    for e in entries:
        counts[e["path"]] = counts.get(e["path"], 0) + 1
    for e in entries:
        e["duplicate_path"] = counts[e["path"]] > 1
    stats["paths"] = len(entries)
    stats["duplicate_paths"] = sum(1 for n in counts.values() if n > 1)
    return {"entries": entries, "stats": stats}


def collapse_reason(stats: dict, live_count: int | None) -> str | None:
    """Why this walk must not replace the live index, or None."""
    if not stats.get("sections"):
        return "no section resolved"
    if live_count:
        margin = max(3, int(live_count * 0.02))
        if live_count - stats["paths"] > margin:
            return (f"the walk found {stats['paths']} paths, {live_count - stats['paths']} fewer than the "
                    f"{live_count} on the live index (allowed: {margin})")
    return None


def _notion_children(block_id):
    from notion_rest import notion
    return notion.get_block_children(block_id)


def _notion_url(kind, obj_id):
    from notion_rest import notion
    path = f"/pages/{obj_id}" if kind == "child_page" else f"/databases/{obj_id}"
    return notion._request("GET", path).get("url")


def main(argv=None, children=None, url_of=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sections", required=True, help="JSON file: {section title: page id}")
    ap.add_argument("--out", required=True, help="where to write the walk (JSON)")
    ap.add_argument("--live-count", type=int, help="paths on the live Guide/Index, for the collapse guard")
    ap.add_argument("--allow-shrink", action="store_true", help="the tree really shrank: skip the count check")
    a = ap.parse_args(argv)
    with open(a.sections, encoding="utf-8") as f:
        sections = json.load(f)
    result = walk(sections, children or _notion_children, url_of or _notion_url)
    s = result["stats"]
    print(f"walk: {s['sections']} sections, {s['paths']} pages and databases ({s['nested_in_blocks']} inside a "
          f"toggle, column or other block; {s['untitled']} untitled; {s['duplicate_paths']} path(s) shared by "
          f"more than one page), {s['blocks_opened']} blocks opened, {s['excluded']} excluded")
    reason = collapse_reason(s, None if a.allow_shrink else a.live_count)
    if not s.get("sections"):
        reason = "no section resolved"
    if reason:
        print(f"REFUSED: {reason}. Leave Guide/Index as it is (references/guide-index.md, step 6).",
              file=sys.stderr)
        return COLLAPSE_EXIT
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=1, ensure_ascii=False)
    print(f"-> {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
