"""Detector #14 — a declared knowledge path that no longer resolves.

`CLAUDE.md` promised this check for as long as the knowledge manifest has
existed ("`healthcheck-knowledge` flags skills whose declared paths no longer
exist in Notion") and nothing implemented it. Detector #3 greps skills to
decide whether a *page* is orphaned, which is the reverse direction; no other
guard reads a declared path at all. Two dead paths were found by hand on
2026-09-19 — a moved `Tools/Google Sheets (API access)` and an unlinked
`Procedures/AI Native/Features Loop` — and neither was reported by anything.

The load-bearing test is `TestRealCorpus`: it runs the detector over the real
manifests and a tree fetched from the live teamspace on 2026-09-19, and asserts
the four dead paths that first run found come back. A detector for known misses
that cannot find them is worth nothing, and a synthetic fixture cannot make
that assertion — so the fixture is the real producer's output ([ENG-1353]).
"""

import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(
    0, os.path.join(ROOT, "plugins/shared/skills/healthcheck-knowledge/scripts")
)

import declared_paths as dp  # noqa: E402

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def _load(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as fh:
        return json.load(fh)


# The paths the first live run reported, kept as literals so a fixture refresh
# that loses them fails loudly rather than passing on whatever it happens to hold.
#
# The fixtures are a snapshot of 2026-09-19 and are deliberately NOT refreshed as
# the world is fixed: two of these four are already repointed on main (ENG-1354 --
# `Procedures/PR Process` moved under Engineering, and the `Tools/11Labs` page was
# renamed to `ElevenLabs`, which makes the declaration right and the page title the
# thing that had been wrong). Refreshing would delete the only corpus that
# demonstrates the detector finding a real miss.
DEAD_WITH_SUCCESSOR = {"Procedures/PR Process": "Procedures/Engineering/PR Process"}
DEAD_WITHOUT_SUCCESSOR = {
    "Tools/Anthropic",
    "Tools/Aura API/Coach & Track Endpoints",
    "Tools/ElevenLabs",
}


class TestRealCorpus(unittest.TestCase):
    """The four dead paths the detector found on its first live run."""

    @classmethod
    def setUpClass(cls):
        cls.tree = _load("ai_knowledge_tree.json")
        cls.listed = set(_load("ai_knowledge_index_paths.json"))
        cls.declared = _load("declared_knowledge_paths.json")
        cls.findings = dp.find_findings(cls.declared, cls.tree, cls.listed)
        cls.by_path = {f["subcategory"]: f for f in cls.findings}

    def test_every_known_dead_path_is_found(self):
        expected = set(DEAD_WITH_SUCCESSOR) | DEAD_WITHOUT_SUCCESSOR
        self.assertEqual(expected, set(self.by_path), "the known dead paths must come back")

    def test_a_move_gets_its_successor_named(self):
        for dead, live in DEAD_WITH_SUCCESSOR.items():
            self.assertIn(live, self.by_path[dead]["suggested_fix"])

    def test_a_renamed_page_is_not_guessed_at(self):
        # Tools/ElevenLabs is really Tools/11Labs. Different leaf title, so the
        # successor heuristic must decline rather than invent a repoint.
        for dead in DEAD_WITHOUT_SUCCESSOR:
            self.assertIn("No successor found", self.by_path[dead]["suggested_fix"])

    def test_the_corpus_is_big_enough_to_mean_something(self):
        self.assertGreater(len({d["owner"] for d in self.declared}), 150)
        self.assertGreater(len(self.tree), 200)

    def test_live_paths_are_not_reported(self):
        for live in ("Tools/Slack", "Tools/11Labs", "Procedures/Engineering/PR Process"):
            self.assertIn(live, self.tree)
            self.assertNotIn(live, self.by_path)

    def test_every_finding_is_high(self):
        # A dead read degrades silently — there is no lesser version of it.
        for f in self.findings:
            self.assertEqual("High", f["severity"])

    def test_findings_name_the_files_that_declared_them(self):
        for f in self.findings:
            self.assertTrue(f["related_pages"])
            for path in f["related_pages"]:
                self.assertTrue(path.startswith("plugins/"), path)


class TestResolutionNeedsBothRoutes(unittest.TestCase):
    """A path in the tree but not yet on Guide/Index is live, not dead."""

    def setUp(self):
        self.declared = [{"owner": "x:y", "kind": "skill", "file": "plugins/x/skills/y/SKILL.md",
                          "direction": "reads", "path": "Tools/Brand New"}]

    def test_index_only_resolution_would_report_a_live_page(self):
        # The index lags: harvest regenerates it, sibling skills and humans do not.
        tree = {"Tools/Brand New": "id1"}
        self.assertEqual([], dp.find_findings(self.declared, tree, set()))

    def test_tree_only_resolution_would_report_a_page_the_walk_missed(self):
        # Depth caps and transient failures can truncate a walk; the index acquits.
        self.assertEqual([], dp.find_findings(self.declared, {}, {"Tools/Brand New"}))

    def test_missing_from_both_is_dead(self):
        self.assertEqual(1, len(dp.find_findings(self.declared, {}, set())))


class TestWhatIsNotAClaimAboutAPage(unittest.TestCase):
    def test_the_wildcard_is_skipped(self):
        self.assertEqual([], dp.find_findings(
            [{"owner": "a:b", "kind": "skill", "file": "f", "direction": "reads", "path": "*"}],
            {}, set()))

    def test_guide_index_is_skipped_by_the_collector(self):
        # The universal baseline; every skill declares it and it always resolves.
        paths = [d["path"] for d in dp.declared_paths(os.path.join(ROOT, "plugins"))]
        self.assertNotIn("Guide/Index", paths)
        self.assertNotIn("*", paths)


class TestSuccessor(unittest.TestCase):
    def test_a_unique_leaf_match_is_the_successor(self):
        tree = {"Tools/Google Sheets/Google Sheets (API access)": "i"}
        self.assertEqual(
            "Tools/Google Sheets/Google Sheets (API access)",
            dp.successor_for("Tools/Google Sheets (API access)", tree))

    def test_an_ambiguous_leaf_yields_no_successor(self):
        tree = {"A/Screens": "1", "B/Screens": "2"}
        self.assertIsNone(dp.successor_for("Old/Screens", tree))

    def test_no_leaf_match_yields_no_successor(self):
        self.assertIsNone(dp.successor_for("Tools/ElevenLabs", {"Tools/11Labs": "i"}))


class TestCollector(unittest.TestCase):
    def test_agent_files_are_scanned(self):
        """`plugins/<plugin>/agents/*.md` is in the corpus.

        No agent declares a `knowledge:` block today — which is itself a gap,
        since CLAUDE.md says they must — so asserting against the live tree
        would assert the gap instead of the capability. Build a plugin dir.
        """
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            agents = os.path.join(tmp, "p", "agents")
            os.makedirs(agents)
            with open(os.path.join(agents, "a.md"), "w", encoding="utf-8") as fh:
                fh.write("---\nname: a\nknowledge:\n  reads:\n    - Tools/X\n---\nbody\n")
            found = dp.declared_paths(tmp)
        self.assertEqual(
            [{"owner": "p:a", "kind": "agent",
              "file": os.path.join(tmp, "p", "agents", "a.md"),
              "direction": "reads", "path": "Tools/X"}],
            found)

    def test_agent_manifests_are_collected_from_the_real_tree(self):
        """Agents declare `knowledge:` like skills (CLAUDE.md, Principle 4).

        implementation-agent was the first to declare one, so the corpus
        now holds both kinds and the agent's path is checked like any other.
        """
        found = dp.declared_paths(os.path.join(ROOT, "plugins"))
        self.assertEqual({"skill", "agent"}, {d["kind"] for d in found})
        self.assertIn(("eng:implementation-agent", "Compliance/Security/Code Review Checklist"),
                      {(d["owner"], d["path"]) for d in found if d["kind"] == "agent"})

    def test_writes_are_collected_as_well_as_reads(self):
        dirs = {d["direction"] for d in dp.declared_paths(os.path.join(ROOT, "plugins"))}
        self.assertEqual({"reads", "writes"}, dirs)


class TestFrontmatterParse(unittest.TestCase):
    def test_body_examples_do_not_leak_into_the_manifest(self):
        # Several skills document the convention by example in their body.
        text = ("---\nname: x\nknowledge:\n  reads:\n    - Tools/Real\n---\n"
                "# Body\n```yaml\nknowledge:\n  reads:\n    - Tools/Fake\n```\n")
        k = dp.parse_knowledge(dp.split_frontmatter(text))
        self.assertEqual(["Tools/Real"], k["reads"])

    def test_inline_flow_list_and_comments(self):
        text = ('---\nname: x\nknowledge:\n  reads:\n    - Guide/Index   # baseline\n'
                '    - "Tools/Quoted"\n  writes: ["*"]\n---\nbody\n')
        k = dp.parse_knowledge(dp.split_frontmatter(text))
        self.assertEqual(["Guide/Index", "Tools/Quoted"], k["reads"])
        self.assertEqual(["*"], k["writes"])

    def test_absent_writes_is_none_not_empty(self):
        text = "---\nname: x\nknowledge:\n  reads:\n    - Guide/Index\n---\nbody\n"
        self.assertIsNone(dp.parse_knowledge(dp.split_frontmatter(text))["writes"])

    def test_a_file_without_frontmatter_is_skipped_not_crashed(self):
        self.assertIsNone(dp.split_frontmatter("# just a doc\n"))


class TestFindingShape(unittest.TestCase):
    def test_subcategory_is_the_path_so_step5_dedupes_per_path(self):
        declared = [
            {"owner": "a:b", "kind": "skill", "file": "f1", "direction": "reads", "path": "Dead/One"},
            {"owner": "c:d", "kind": "skill", "file": "f2", "direction": "reads", "path": "Dead/One"},
        ]
        findings = dp.find_findings(declared, {}, set())
        self.assertEqual(1, len(findings), "one path, one finding, however many declarers")
        self.assertEqual("Dead/One", findings[0]["subcategory"])
        self.assertIn("a:b", findings[0]["description"])
        self.assertIn("c:d", findings[0]["description"])

    def test_findings_are_ordered_deterministically(self):
        declared = [{"owner": "a:b", "kind": "skill", "file": "f", "direction": "reads",
                     "path": p} for p in ("Z/one", "A/two", "M/three")]
        paths = [f["subcategory"] for f in dp.find_findings(declared, {}, set())]
        self.assertEqual(sorted(paths), paths)


if __name__ == "__main__":
    unittest.main()
