#!/usr/bin/env python3
"""`harvest-knowledge` `walk_tree.py`: the Guide/Index walk reaches pages nested inside blocks.

**The incident.** Harvest run 2026-11-15-run1 regenerated Guide/Index with 455 paths where the live
tree had 475. The 19 missing pages lived inside a toggle or a column: their parent is a block, not a
page, and a walk that follows only `child_page` blocks never reaches them. Nothing failed; the index
just got shorter.

**Pinned here:** a page inside a toggle, a column (column_list → column), a callout and a synced block
is listed with its full path and URL, and a page nested under one of those is reached too. Databases
are listed and not descended. Table rows aren't opened. The exclusions hold. A collapsed walk (no
section, or more than max(3, 2%) fewer paths than the live index) is refused with exit 3 and writes
nothing.

Stdlib `unittest`, no network: Notion is a stubbed tree behind `children()` / `url_of()`.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import pathlib
import sys
import tempfile
import unittest
import uuid

REPO = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = REPO / "plugins/shared/skills/harvest-knowledge/scripts/walk_tree.py"


def load():
    name = f"_walk_tree_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def page(pid, title):
    return {"id": pid, "type": "child_page", "child_page": {"title": title}, "has_children": True}


def db(pid, title):
    return {"id": pid, "type": "child_database", "child_database": {"title": title}, "has_children": True}


def block(bid, kind):
    return {"id": bid, "type": kind, kind: {}, "has_children": True}


TREE = {
    "products": [page("aura", "Aura"), block("tog1", "toggle")],
    "aura": [page("features", "Features")],
    "features": [page("strategy", " Strategy (Product) "), block("cols", "column_list")],
    "strategy": [block("tog2", "toggle")],
    "tog2": [page("deep", "Product Deep Dives")],
    "deep": [page("romman", "Romman - Retention")],
    "cols": [block("colA", "column"), block("colB", "column")],
    "colA": [page("today", "Today row ideas")],
    "colB": [block("call", "callout")],
    "call": [page("incallout", "In a callout")],
    "tog1": [block("sync", "synced_block"), block("tbl", "table")],
    "sync": [page("insync", "In a synced block"), page("insync", "In a synced block")],
    "tbl": [page("never", "Never opened")],
    "romman": [], "today": [], "incallout": [], "insync": [], "never": [],
    "learnings": [db("exp", "Experiments")],
    "exp": [page("row", "A database row")],
    "guide": [page("index", "Index"), page("harvest", "Harvest"), page("tips", "Tips")],
    "harvest": [page("run1", "2026-11-15 run1")], "index": [], "tips": [],
    "signals": [page("conv", "Aurie Conversations")],
    "conv": [page("snap", "Producer — 2026-11-01 snapshot"), page("howto", "How to read")],
    "snap": [], "howto": [],
    "procedures": [page("retroA", "2022: June 8 - Retrospective"), page("retroB", "2022: June 8 - Retrospective"),
                   page("blank", "  ")],
    "retroA": [], "retroB": [], "blank": [page("underblank", "Child of an untitled page")], "underblank": [],
}
SECTIONS = {"Products": "products", "Learnings": "learnings", "Guide": "guide", "Signals": "signals",
            "Procedures": "procedures"}


class Walk(unittest.TestCase):
    def setUp(self):
        self.mod = load()
        self.opened = []

    def children(self, block_id):
        self.opened.append(block_id)
        return TREE.get(block_id, [])

    def url_of(self, kind, obj_id):
        return f"https://www.notion.so/aura/{obj_id}"

    def run_walk(self):
        return self.mod.walk(SECTIONS, self.children, self.url_of)

    def test_pages_inside_toggles_columns_callouts_and_synced_blocks_are_listed(self):
        got = {e["path"]: e for e in self.run_walk()["entries"]}
        for path in ("Products/Aura/Features/Strategy (Product)/Product Deep Dives",
                     "Products/Aura/Features/Strategy (Product)/Product Deep Dives/Romman - Retention",
                     "Products/Aura/Features/Today row ideas",
                     "Products/Aura/Features/In a callout",
                     "Products/In a synced block"):
            self.assertIn(path, got)
        deep = got["Products/Aura/Features/Strategy (Product)/Product Deep Dives"]
        self.assertEqual((deep["url"], deep["kind"], deep["in_block"]),
                         ("https://www.notion.so/aura/deep", "page", True))
        self.assertFalse(got["Products/Aura/Features/Strategy (Product)/Product Deep Dives/Romman - Retention"]["in_block"])

    def test_titles_are_stripped_and_duplicates_listed_once(self):
        paths = [e["path"] for e in self.run_walk()["entries"]]
        self.assertIn("Products/Aura/Features/Strategy (Product)", paths)
        self.assertEqual(paths.count("Products/In a synced block"), 1)
        self.assertEqual(paths, sorted(paths))

    def test_databases_listed_not_descended_and_tables_not_opened(self):
        got = {e["path"]: e for e in self.run_walk()["entries"]}
        self.assertEqual(got["Learnings/Experiments"]["kind"], "database")
        self.assertNotIn("Learnings/Experiments/A database row", got)
        self.assertNotIn("exp", self.opened)
        self.assertNotIn("tbl", self.opened)

    def test_exclusions(self):
        result = self.run_walk()
        paths = {e["path"] for e in result["entries"]}
        for gone in ("Guide/Index", "Guide/Harvest", "Guide/Harvest/2026-11-15 run1",
                     "Signals/Aurie Conversations/Producer — 2026-11-01 snapshot", "Products", "Guide"):
            self.assertNotIn(gone, paths)
        self.assertIn("Guide/Tips", paths)
        self.assertIn("Signals/Aurie Conversations/How to read", paths)
        self.assertNotIn("harvest", self.opened, "an excluded subtree is not walked")
        self.assertEqual(result["stats"]["excluded"], 3)

    def test_stats_count_what_sat_inside_blocks(self):
        s = self.run_walk()["stats"]
        self.assertEqual(s["paths"], 16)
        self.assertEqual(s["nested_in_blocks"], 4)

    def test_untitled_pages_are_kept_and_walked(self):
        result = self.run_walk()
        got = {e["path"]: e for e in result["entries"]}
        self.assertTrue(got["Procedures/(untitled)"]["untitled"])
        self.assertIn("Procedures/(untitled)/Child of an untitled page", got)
        self.assertEqual(result["stats"]["untitled"], 1)

    def test_pages_sharing_a_title_are_both_kept_and_flagged(self):
        result = self.run_walk()
        retros = [e for e in result["entries"] if e["path"] == "Procedures/2022: June 8 - Retrospective"]
        self.assertEqual(sorted(e["id"] for e in retros), ["retroA", "retroB"])
        self.assertTrue(all(e["duplicate_path"] for e in retros))
        self.assertEqual(result["stats"]["duplicate_paths"], 1)
        self.assertFalse(any(e["duplicate_path"] for e in result["entries"] if e not in retros))


class CollapseGuard(unittest.TestCase):
    def setUp(self):
        self.mod = load()

    def test_reasons(self):
        r = self.mod.collapse_reason
        self.assertIsNone(r({"sections": 10, "paths": 475}, 475))
        self.assertIsNone(r({"sections": 10, "paths": 466}, 475), "a drop of 9 is within 2%")
        self.assertIn("19 fewer", r({"sections": 10, "paths": 456}, 475), "the 11-15 loss is refused")
        self.assertIsNone(r({"sections": 10, "paths": 497}, 500))
        self.assertIsNone(r({"sections": 2, "paths": 7}, 10), "small indexes: a drop of 3 is allowed")
        self.assertIn("4 fewer", r({"sections": 2, "paths": 7}, 11) or "", "and a drop of 4 is not")
        self.assertIsNone(r({"sections": 2, "paths": 7}, None))
        self.assertEqual(r({"sections": 0, "paths": 0}, None), "no section resolved")

    def run_main(self, sections, *extra):
        tmp = pathlib.Path(self.enterContext(tempfile.TemporaryDirectory()))
        (tmp / "s.json").write_text(json.dumps(sections))
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = self.mod.main(["--sections", str(tmp / "s.json"), "--out", str(tmp / "w.json"), *extra],
                                 children=lambda b: TREE.get(b, []), url_of=lambda k, i: f"u/{i}")
        return code, (tmp / "w.json"), out.getvalue(), err.getvalue()

    def test_a_collapsed_walk_writes_nothing(self):
        code, path, _, err = self.run_main(SECTIONS, "--live-count", "44")
        self.assertEqual(code, self.mod.COLLAPSE_EXIT)
        self.assertFalse(path.exists())
        self.assertIn("REFUSED", err)
        code, path, _, _ = self.run_main({}, "--live-count", "0")
        self.assertEqual((code, path.exists()), (self.mod.COLLAPSE_EXIT, False))

    def test_a_good_walk_writes_and_allow_shrink_is_explicit(self):
        code, path, out, _ = self.run_main(SECTIONS, "--live-count", "16")
        self.assertEqual(code, 0)
        self.assertEqual(len(json.loads(path.read_text())["entries"]), 16)
        self.assertIn("4 inside a toggle, column or other block", out)
        code, path, _, _ = self.run_main(SECTIONS, "--live-count", "44", "--allow-shrink")
        self.assertEqual((code, path.exists()), (0, True))
        code, path, _, _ = self.run_main({}, "--allow-shrink")
        self.assertEqual(code, self.mod.COLLAPSE_EXIT, "no section is refused even with --allow-shrink")


if __name__ == "__main__":
    unittest.main()
