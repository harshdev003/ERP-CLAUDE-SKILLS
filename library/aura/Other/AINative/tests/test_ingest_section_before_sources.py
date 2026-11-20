"""ingest-knowledge and harvest-knowledge put a new section above the page's Sources, and its source line
into that list (ENG-1709, ENG-1710).

`append_block_children` always appends at the end, so distill-sessions-daily's unattended writes
landed below `# Sources` on Tools/Firebase, Tools/Linear and Tools/GitHub (2026-10-29). The fake
page here inserts the way Notion does: `after` places the batch right after that block, and the
response's `results` ends with the parent's tail, not the last block inserted. Every test runs against
every copy of `insert_section.py`; the copies are byte-identical (docs/canonical-sources.md).
"""
import importlib.util
import itertools
import json
import pathlib
import sys
import unittest

PLUGINS = pathlib.Path(__file__).resolve().parents[1] / "plugins"
SKILL = PLUGINS / "shared/skills/ingest-knowledge"
CANONICAL = SKILL / "scripts/insert_section.py"
COPIES = sorted(PLUGINS.glob("*/skills/*/scripts/insert_section.py"))


def load(path):
    sys.path.insert(0, str(path.parent))
    try:
        spec = importlib.util.spec_from_file_location(f"{path.parent.parent.name}_{path.stem}_under_test", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path.remove(str(path.parent))


nr = load(SKILL / "scripts/notion_rest.py")
ins = load(CANONICAL)


class EveryCopy(unittest.TestCase):
    """Runs each test below once per copy, swapping the module-level `ins`."""

    def run(self, result=None):
        global ins
        saved = ins
        try:
            for path in COPIES:
                ins = load(path)
                with self.subTest(copy=str(path.relative_to(PLUGINS))):
                    super().run(result)
        finally:
            ins = saved


class FakePage:
    def __init__(self, blocks):
        self.ids = itertools.count(1)
        self.children = [self._stored(b) for b in blocks]
        self.calls = []

    def _stored(self, block):
        return {**block, "id": f"b{next(self.ids)}"}

    def get_block_children(self, page_id):
        self.calls.append(("get", None))
        return [dict(b) for b in self.children]

    def append_block_children(self, page_id, children):
        self.calls.append(("append", None))
        self.children += [self._stored(c) for c in children]
        return {"results": self.children[-len(children):]}

    def _request(self, method, path, data=None, **kw):
        body = json.loads(data)
        self.calls.append(("insert", body.get("after")))
        pos = next(i for i, b in enumerate(self.children) if b["id"] == body["after"]) + 1
        self.children[pos:pos] = [self._stored(c) for c in body["children"]]
        return {"results": self.children[pos:pos + len(body["children"])] + [self.children[-1]]}

    def texts(self):
        return [ins._text(b) for b in self.children]


def page(*extra):
    return [nr.block_heading("Overview", 1), nr.block_paragraph("Body."),
            nr.block_heading("Related", 1), nr.block_bullet("[Parent](https://notion.so/p)"),
            nr.block_heading("Sources", 1), nr.block_bullet("[Old doc](https://example.com/old) — doc"),
            *extra]


SECTION = [nr.block_heading("New convention", 2), nr.block_paragraph("Do the thing.")]
SOURCE = [nr.block_bullet("distill-sessions-daily 2026-10-30 — sessions d4833d8d")]


class SectionGoesAboveSources(EveryCopy):
    def test_order(self):
        p = FakePage(page())
        out = ins.add_section(p, "page", SECTION, SOURCE)
        self.assertEqual(p.texts(), [
            "Overview", "Body.", "Related", "Parent",
            "New convention", "Do the thing.",
            "Sources", "Old doc — doc", "distill-sessions-daily 2026-10-30 — sessions d4833d8d",
        ])
        self.assertEqual(out, {"placement": "before_sources", "sources_added": 1, "sources_skipped": 0})

    def test_one_read_before_any_write_and_no_append(self):
        p = FakePage(page())
        ins.add_section(p, "page", SECTION, SOURCE)
        self.assertEqual([c[0] for c in p.calls], ["get", "insert", "insert"])
        self.assertEqual([c[1] for c in p.calls[1:]], ["b4", "b6"])

    def test_a_listed_source_is_skipped(self):
        p = FakePage(page())
        out = ins.add_section(p, "page", SECTION, [nr.block_bullet("[Again](https://example.com/old)")])
        self.assertEqual(out["sources_skipped"], 1)
        self.assertEqual(p.texts()[-1], "Old doc — doc")
        self.assertEqual(len([c for c in p.calls if c[0] == "insert"]), 1)

    def test_a_heading_after_sources_ends_the_list(self):
        p = FakePage(page(nr.block_heading("Appendix", 1), nr.block_paragraph("Tail.")))
        ins.add_section(p, "page", SECTION, SOURCE)
        t = p.texts()
        self.assertLess(t.index("Do the thing."), t.index("Sources"))
        self.assertEqual(t.index("distill-sessions-daily 2026-10-30 — sessions d4833d8d"), t.index("Appendix") - 1)

    def test_empty_sources_list_anchors_on_the_heading(self):
        p = FakePage(page()[:5])
        ins.add_section(p, "page", SECTION, SOURCE)
        self.assertEqual(p.texts()[-2:], ["Sources", "distill-sessions-daily 2026-10-30 — sessions d4833d8d"])

    def test_no_sources_heading_appends_and_adds_one(self):
        p = FakePage(page()[:4])
        out = ins.add_section(p, "page", SECTION, SOURCE)
        self.assertEqual(out["placement"], "end")
        self.assertEqual(p.texts()[-4:], ["New convention", "Do the thing.", "Sources",
                                          "distill-sessions-daily 2026-10-30 — sessions d4833d8d"])

    def test_sources_first_or_oversized_is_refused_before_writing(self):
        p = FakePage(page()[4:])
        with self.assertRaises(ValueError):
            ins.add_section(p, "page", SECTION, SOURCE)
        with self.assertRaises(ValueError):
            ins.add_section(FakePage(page()), "page", [nr.block_paragraph("x")] * 101, SOURCE)
        self.assertEqual([c[0] for c in p.calls], ["get"])

    def test_a_sources_line_alone_needs_no_section(self):
        for blocks in (page(), page()[4:]):
            p = FakePage(blocks)
            out = ins.add_section(p, "page", [], SOURCE)
            self.assertEqual(p.texts()[-1], "distill-sessions-daily 2026-10-30 — sessions d4833d8d")
            self.assertEqual(out["sources_added"], 1)
            self.assertEqual([c[0] for c in p.calls], ["get", "insert"])

    def test_hash_prefixed_heading_text_counts(self):
        p = FakePage(page()[:4] + [nr.block_heading("# Sources", 2)])
        self.assertEqual(ins.find_sources(p.children), (4, 4))


class Copies(unittest.TestCase):
    def test_both_writers_ship_a_byte_identical_copy(self):
        self.assertEqual({p.parent.parent.name for p in COPIES}, {"ingest-knowledge", "harvest-knowledge"})
        for path in COPIES:
            self.assertEqual(path.read_bytes(), CANONICAL.read_bytes(), path)


class SkillSaysSo(unittest.TestCase):
    def test_no_after_the_heading_append_guidance(self):
        text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        self.assertNotIn("append_block_children` after the `# Sources` heading", text)
        self.assertIn("insert_section.py", text)

    def test_harvest_no_longer_appends_a_section(self):
        text = (PLUGINS / "shared/skills/harvest-knowledge/SKILL.md").read_text(encoding="utf-8")
        self.assertNotIn("append_block_children` for section edits", text)
        self.assertNotIn("# add content / a # Sources line", text)
        self.assertIn("insert_section.py", text)


if __name__ == "__main__":
    unittest.main()
