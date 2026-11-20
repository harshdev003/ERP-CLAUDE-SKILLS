#!/usr/bin/env python3
"""The crisis-line tables must be identical in every guardrail skill that carries them.

Two skills draft CS outreach to people at risk from a `references/resource_map.md`:
`eng:investigate-memory-ingestion-guardrail` (canonical) and
`eng:investigate-conversation-guardrail`. Each file has a skill-specific header and
mapping section, so a whole-file `diff -q` would be permanently red — but the part a
person dials, the country tables plus "Coverage gaps" and "Global backstops", must
never differ. A hotline corrected in one copy and not the other ships a wrong or dead
number to someone in crisis, and nothing else would notice: both files still parse,
render, and read correctly.

So the shared span — from `## Verification cadence` up to the skill-specific
`## Mapping …` heading — is compared byte for byte, and any new `resource_map.md`
under `plugins/` must join the family (or be declared out of it with a reason).

Stdlib unittest, no network.
"""
from __future__ import annotations

import pathlib
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
CANONICAL = REPO / "plugins/eng/skills/investigate-memory-ingestion-guardrail/references/resource_map.md"
COPIES = [
    REPO / "plugins/eng/skills/review-conversation-guardrails/references/resource_map.md",
    REPO / "plugins/eng/skills/investigate-conversation-guardrail/references/resource_map.md",
]

START = "## Verification cadence"
END = "## Mapping "


def shared_span(path: pathlib.Path) -> str:
    text = path.read_text(encoding="utf-8")
    start = text.index(START)
    end = text.index(END, start)
    return text[start:end]


class ResourceMapCopies(unittest.TestCase):
    def test_family_membership_is_complete(self):
        found = set(REPO.joinpath("plugins").rglob("resource_map.md"))
        self.assertEqual(found, {CANONICAL, *COPIES},
                         "a resource_map.md exists outside the tested family — add it to COPIES")

    def test_dialable_sections_match_the_canonical(self):
        canonical = shared_span(CANONICAL)
        self.assertIn("## 🇺🇸 United States", canonical)
        self.assertIn("## Global backstops", canonical)
        for copy in COPIES:
            with self.subTest(copy=str(copy.relative_to(REPO))):
                self.assertEqual(
                    shared_span(copy), canonical,
                    "the country tables / coverage gaps / global backstops drifted from the "
                    "canonical copy — edit the canonical first and copy the span verbatim",
                )

    def test_the_span_detector_can_fail(self):
        """A comparison that cannot see a changed digit is not a guard."""
        canonical = shared_span(CANONICAL)
        self.assertIn("988", canonical)
        self.assertNotEqual(canonical.replace("988", "989", 1), canonical)


if __name__ == "__main__":
    unittest.main()
