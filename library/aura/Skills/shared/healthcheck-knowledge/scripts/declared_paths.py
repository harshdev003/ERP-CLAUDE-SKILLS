#!/usr/bin/env python3
"""Detector #14 — a declared knowledge path that no longer resolves.

Reads every ``knowledge:`` manifest under ``plugins/`` (skills and agents alike),
resolves each declared path against the live AI Knowledge tree, and reports the ones
that resolve nowhere.

A path is dead only when BOTH resolution routes miss: the ``Guide/Index`` lookup and
the About -> section -> children walk. The index alone would report every page created
since the last regeneration; the walk alone would miss nothing but costs a full crawl,
so the index is consulted first and the walk decides.

Self-contained per Architecture principle 1 — the frontmatter parse is duplicated from
``retrieve-knowledge/scripts/manifest.py`` rather than imported across skills.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import re
import sys
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ABOUT_PAGE = "9ee75e23af20830aa49101a40466a789"
GUIDE_INDEX_PATH = "Guide/Index"

_ITEM = re.compile(r"^    - (.*?)\s*(?:#.*)?$")
_KEY = re.compile(r"^  (reads|writes):\s*(\[.*\])?\s*$")
_NOTION_ID = re.compile(r"([0-9a-f]{32})", re.I)
_SNAPSHOT_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


# --------------------------------------------------------------------------- manifests

def split_frontmatter(text: str) -> Optional[str]:
    parts = text.split("---", 2)
    if len(parts) < 3 or parts[0].strip():
        return None
    return parts[1]


def _unquote(item: str) -> str:
    item = item.strip()
    if len(item) >= 2 and item[0] == item[-1] and item[0] in "\"'":
        return item[1:-1]
    return item


def parse_knowledge(frontmatter: str) -> Dict[str, Optional[List[str]]]:
    out: Dict[str, Optional[List[str]]] = {"reads": None, "writes": None}
    lines = frontmatter.split("\n")
    try:
        start = next(i for i, l in enumerate(lines) if l.rstrip() == "knowledge:")
    except StopIteration:
        return out
    key: Optional[str] = None
    for line in lines[start + 1:]:
        if line and not line.startswith(" "):
            break
        m = _KEY.match(line)
        if m:
            key = m.group(1)
            if m.group(2) is not None:
                inner = m.group(2).strip()[1:-1]
                out[key] = [_unquote(x) for x in inner.split(",") if x.strip()]
                key = None
            else:
                out[key] = []
            continue
        if key is not None:
            im = _ITEM.match(line)
            if im:
                out[key].append(_unquote(im.group(1)))
            elif line.strip():
                key = None
    return out


def declared_paths(plugins_root: str) -> List[dict]:
    """[{owner, kind, file, direction, path}] for every declared, resolvable-in-principle path.

    Skips the wildcard and the universal Guide/Index baseline — neither is a routing
    claim about a specific page.
    """
    out: List[dict] = []
    for plugin in sorted(os.listdir(plugins_root)):
        pdir = os.path.join(plugins_root, plugin)
        if not os.path.isdir(pdir):
            continue
        targets: List[Tuple[str, str, str]] = []
        skills_dir = os.path.join(pdir, "skills")
        if os.path.isdir(skills_dir):
            for skill in sorted(os.listdir(skills_dir)):
                md = os.path.join(skills_dir, skill, "SKILL.md")
                if os.path.isfile(md):
                    targets.append((f"{plugin}:{skill}", "skill", md))
        agents_dir = os.path.join(pdir, "agents")
        if os.path.isdir(agents_dir):
            for agent in sorted(os.listdir(agents_dir)):
                if agent.endswith(".md"):
                    md = os.path.join(agents_dir, agent)
                    targets.append((f"{plugin}:{agent[:-3]}", "agent", md))
        for owner, kind, md in targets:
            try:
                with open(md, encoding="utf-8") as fh:
                    fm = split_frontmatter(fh.read())
            except OSError:
                continue
            if fm is None:
                continue
            k = parse_knowledge(fm)
            for direction in ("reads", "writes"):
                for p in (k.get(direction) or []):
                    if p == "*" or p == GUIDE_INDEX_PATH:
                        continue
                    out.append({"owner": owner, "kind": kind, "file": md,
                                "direction": direction, "path": p})
    return out


# ------------------------------------------------------------------- live tree (Notion)

def _sections(notion) -> Dict[str, str]:
    """{section title -> page id} from About's BODY LINKS, not its child pages."""
    from notion_rest import title_text
    ids: List[str] = []
    seen = set()

    def walk(blocks):
        for b in blocks:
            if b["type"] == "child_page":
                continue  # a nested page's links are its own, not About's
            if b["type"] == "link_to_page":
                t = (b["link_to_page"].get("page_id") or "").replace("-", "")
                if t and t not in seen:
                    seen.add(t); ids.append(t)
            payload = b.get(b["type"], {})
            if isinstance(payload, dict):
                for rt in payload.get("rich_text", []):
                    href = rt.get("href") or ""
                    if "notion.so" not in href and not href.startswith("/"):
                        continue
                    found = _NOTION_ID.findall(href.split("#")[0].split("?")[0])
                    if found and found[-1] not in seen:
                        seen.add(found[-1]); ids.append(found[-1])
            walk(b.get("_children", []))

    walk(notion.get_block_children(ABOUT_PAGE, recursive=True))
    return {title_text(notion.get_page(pid), "title"): pid for pid in ids}


def walk_tree(notion, max_depth: int = 8) -> Dict[str, str]:
    """{path -> page id} for every page and database under every section, to the leaves."""
    sections = _sections(notion)
    if not sections:
        raise SystemExit(
            "About resolved 0 sections — the walk is broken or About was restructured. "
            "Aborting: every declared path would be reported dead."
        )

    def children_of(pid: str):
        out = []
        for b in notion.get_block_children(pid):
            if b["type"] == "child_page":
                out.append((b["child_page"]["title"], b["id"]))
            elif b["type"] == "child_database":
                out.append((b["child_database"]["title"], b["id"]))
        return out

    tree: Dict[str, str] = {name: pid for name, pid in sections.items()}
    level = list(sections.items())
    depth = 0
    while level and depth < max_depth:
        with cf.ThreadPoolExecutor(8) as ex:
            results = list(ex.map(lambda t: (t[0], children_of(t[1])), level))
        nxt = []
        for parent_path, kids in results:
            for title, cid in kids:
                p = f"{parent_path}/{title}"
                tree[p] = cid
                nxt.append((p, cid))
        level = nxt
        depth += 1
    return tree


def index_paths(notion, tree: Dict[str, str]) -> set:
    """Paths listed on Guide/Index, read from each bullet's backticked code span.

    Never split a bullet on its first em-dash: real paths contain them
    (`Procedures/Engineering/Monthly Tasks/Coach Payout — Step by Step`).
    """
    idx_id = tree.get(GUIDE_INDEX_PATH)
    if not idx_id:
        return set()
    out = set()
    for b in notion.get_block_children(idx_id):
        if b["type"] != "bulleted_list_item":
            continue
        rts = b["bulleted_list_item"]["rich_text"]
        code = [x for x in rts if x.get("annotations", {}).get("code")]
        if code:
            out.add("".join(x["plain_text"] for x in code).strip())
    return out


# ------------------------------------------------------------------------------ verdict

def successor_for(dead: str, tree: Dict[str, str]) -> Optional[str]:
    """A live path with the same leaf title — the shape a page-move leaves behind."""
    leaf = dead.rsplit("/", 1)[-1]
    hits = [p for p in tree if p.rsplit("/", 1)[-1] == leaf]
    return sorted(hits)[0] if len(hits) == 1 else None


def find_findings(declared: List[dict], tree: Dict[str, str], listed: set) -> List[dict]:
    live = set(tree)
    dead_by_path: Dict[str, List[dict]] = {}
    for d in declared:
        p = d["path"]
        # Re-checked here, not only in declared_paths(): find_findings is the
        # entry point a fixture or a future caller reaches directly, and a
        # wildcard reported as a dead path is a confident false positive.
        if p == "*" or p == GUIDE_INDEX_PATH:
            continue
        if p in live or p in listed:
            continue
        dead_by_path.setdefault(p, []).append(d)

    findings = []
    for path, owners in sorted(dead_by_path.items()):
        succ = successor_for(path, tree)
        who = sorted({o["owner"] for o in owners})
        dirs = sorted({o["direction"] for o in owners})
        if succ:
            fix = (f"Repoint to `{succ}` — same leaf title, so this is a page move. "
                   f"Grep every spelling before declaring the sweep complete: the slash "
                   f"form, the `›` breadcrumb form the knowledge skills write, and prose.")
        else:
            fix = ("No successor found — do not guess one. Confirm whether the page was "
                   "deleted or renamed, then either repoint to the page that genuinely "
                   "covers it or drop the declaration.")
        findings.append({
            "severity": "High",
            "category": "dead-declared-path",
            "subcategory": path,
            "page_title": path,
            "page_url": "",
            "description": (
                f"`{path}` is declared in `knowledge.{'`/`'.join(dirs)}` by "
                f"{len(who)} manifest(s) ({', '.join(who)}) and resolves nowhere in "
                f"AI Knowledge — not in the live tree, not on `{GUIDE_INDEX_PATH}`. "
                f"A dead read is a silent degrade: `retrieve-knowledge` returns nothing "
                f"and the skill runs on less context than it declared, with no error."
            ),
            "suggested_fix": fix,
            "related_pages": [o["file"] for o in owners],
        })
    return findings


def main(argv: Optional[List[str]] = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--plugins", default="plugins", help="path to the plugins/ root")
    ap.add_argument("--tree", help="JSON {path: id} to use instead of walking Notion")
    ap.add_argument("--index", help="JSON [paths] listed on Guide/Index (with --tree)")
    ap.add_argument("--json", action="store_true", help="emit findings as JSON")
    args = ap.parse_args(argv)

    declared = declared_paths(args.plugins)

    if args.tree:
        with open(args.tree, encoding="utf-8") as fh:
            tree = json.load(fh)
        listed = set()
        if args.index:
            with open(args.index, encoding="utf-8") as fh:
                listed = set(json.load(fh))
        source = "fixture"
    else:
        from notion_rest import notion
        if not notion.available:
            print("NOTION_TOKEN unset — detector #14 skipped (coverage: NONE).",
                  file=sys.stderr)
            return 2
        tree = walk_tree(notion)
        listed = index_paths(notion, tree)
        source = "live"

    findings = find_findings(declared, tree, listed)
    unique = {d["path"] for d in declared}
    coverage = {
        "scanned": len(unique),
        "total": len(unique),
        "tree_paths": len(tree),
        "index_paths": len(listed),
        "source": source,
        "note": (f"Resolved {len(unique)} distinct declared paths from "
                 f"{len({d['owner'] for d in declared})} manifests against a "
                 f"{len(tree)}-path tree ({source})."),
    }

    if args.json:
        print(json.dumps({"findings": findings, "coverage": coverage}, indent=2))
    else:
        print(coverage["note"])
        if not findings:
            print("No dead declared paths.")
        for f in findings:
            print(f"\n[{f['severity']}] {f['page_title']}")
            print(f"  {f['description']}")
            print(f"  fix: {f['suggested_fix']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
