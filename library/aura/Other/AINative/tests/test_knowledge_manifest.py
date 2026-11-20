"""Repo-wide shape checks on every SKILL.md ``knowledge:`` manifest.

Architecture principle 2 (CLAUDE.md → Knowledge manifest): every skill declares
``knowledge.reads`` with at least ``Guide/Index``; a skill that never writes omits the
``writes:`` key rather than declaring an empty list; nothing is declared twice.

These are the defects an audit found by hand on 2026-09-03 (two skills with no
``Guide/Index``, two with ``writes: []``) and the reason this exists is the same as for
every other guard in ``tests/``: a documented rule with nothing enforcing it does not hold.

Stdlib only, no network, parses the frontmatter slice — never the whole file, since
several skills document the frontmatter convention by example in their body.
"""

from __future__ import annotations

import pathlib
import re
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
SKILL_MDS = sorted(REPO.glob("plugins/*/skills/*/SKILL.md"))

ITEM = re.compile(r"^    - (.*?)\s*(?:#.*)?$")


def frontmatter(path: pathlib.Path) -> str:
    text = path.read_text(encoding="utf-8")
    parts = text.split("---", 2)
    if len(parts) < 3:
        raise AssertionError(f"{path}: no closed YAML frontmatter")
    return parts[1]


def knowledge_block(fm: str) -> dict:
    """Return {'reads': [...], 'writes': [...] | None} from the frontmatter slice."""
    out = {"reads": None, "writes": None}
    lines = fm.split("\n")
    try:
        start = next(i for i, l in enumerate(lines) if l.rstrip() == "knowledge:")
    except StopIteration:
        return out
    key = None
    for line in lines[start + 1:]:
        if line and not line.startswith(" "):
            break  # next top-level key
        m = re.match(r"^  (reads|writes):\s*(\[.*\])?\s*$", line)
        if m:
            key = m.group(1)
            if m.group(2) is not None:  # inline flow list, e.g. ["*"] or []
                out[key] = [x.strip().strip('"').strip("'") for x in m.group(2).strip("[]").split(",") if x.strip()]
                key = None
            else:
                out[key] = []
            continue
        if key and (im := ITEM.match(line)):
            out[key].append(im.group(1).strip().strip('"').strip("'"))
    return out


class KnowledgeManifestShape(unittest.TestCase):
    def test_corpus_is_non_trivial(self):
        self.assertGreater(len(SKILL_MDS), 100, "glob found suspiciously few SKILL.md files")

    def test_every_skill_declares_a_knowledge_block(self):
        missing = [str(p.relative_to(REPO)) for p in SKILL_MDS if knowledge_block(frontmatter(p))["reads"] is None]
        self.assertEqual(missing, [], f"skills without knowledge.reads: {missing}")

    def test_every_skill_reads_guide_index(self):
        missing = [
            str(p.relative_to(REPO))
            for p in SKILL_MDS
            if "Guide/Index" not in (knowledge_block(frontmatter(p))["reads"] or [])
        ]
        self.assertEqual(missing, [], f"skills without the baseline Guide/Index read: {missing}")

    def test_no_skill_declares_an_empty_writes_list(self):
        empty = [str(p.relative_to(REPO)) for p in SKILL_MDS if knowledge_block(frontmatter(p))["writes"] == []]
        self.assertEqual(empty, [], f"omit the writes: key instead of declaring []: {empty}")

    def test_no_path_is_declared_twice_in_one_list(self):
        dupes = []
        for p in SKILL_MDS:
            kb = knowledge_block(frontmatter(p))
            for key in ("reads", "writes"):
                items = kb[key] or []
                seen = {x for x in items if items.count(x) > 1}
                if seen:
                    dupes.append((str(p.relative_to(REPO)), key, sorted(seen)))
        self.assertEqual(dupes, [], f"duplicate declarations: {dupes}")

    def test_wildcard_is_never_the_only_read(self):
        # "*" is allowed for cross-cutting skills but must sit alongside Guide/Index (CLAUDE.md).
        bad = [
            str(p.relative_to(REPO))
            for p in SKILL_MDS
            if (r := knowledge_block(frontmatter(p))["reads"]) and "*" in r and "Guide/Index" not in r
        ]
        self.assertEqual(bad, [])


class ParserFailureInjection(unittest.TestCase):
    """The parser must see the defects it exists to catch — proven on synthetic frontmatter."""

    def test_missing_guide_index_is_detected(self):
        fm = "\nname: x\nknowledge:\n  reads:\n    - Tools/Slack   # c\n"
        self.assertNotIn("Guide/Index", knowledge_block(fm)["reads"])

    def test_empty_inline_writes_is_detected(self):
        fm = "\nknowledge:\n  reads:\n    - Guide/Index\n  writes: []\n"
        self.assertEqual(knowledge_block(fm)["writes"], [])

    def test_omitted_writes_is_none_not_empty(self):
        fm = "\nknowledge:\n  reads:\n    - Guide/Index\n"
        self.assertIsNone(knowledge_block(fm)["writes"])

    def test_inline_wildcard_and_comments_parse(self):
        fm = '\nknowledge:\n  reads:\n    - Guide/Index   # baseline\n    - "*"           # cross-cutting\n  writes: ["*"]\n'
        kb = knowledge_block(fm)
        self.assertEqual(kb["reads"], ["Guide/Index", "*"])
        self.assertEqual(kb["writes"], ["*"])

    def test_body_examples_are_not_read(self):
        # A documented example after the frontmatter fence must not leak into the parse.
        text = "---\nname: x\nknowledge:\n  reads:\n    - Guide/Index\n---\n\n```yaml\nknowledge:\n  writes: []\n```\n"
        self.assertIsNone(knowledge_block(text.split("---", 2)[1])["writes"])


if __name__ == "__main__":
    unittest.main()
