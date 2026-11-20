#!/usr/bin/env python3
"""Tests for `scripts/check-skill-length.py`.

The `verdict()` cases are the whole point of the gate, and one of them is easy to
get backwards: an **over-budget file that shrinks must pass**. A naive "is it
under N" check fails that case, which would block every PR touching one of
today's over-budget files — and a gate that blocks unrelated work is a gate
someone disables.

Stdlib only, no git, no network: `verdict()` is pure, so the rules are testable
without building repo fixtures.
"""
from __future__ import annotations

import importlib.util
import pathlib
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "check-skill-length.py"


def _load():
    """Import the checker despite its hyphenated, non-importable filename."""
    spec = importlib.util.spec_from_file_location("check_skill_length", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Verdict(unittest.TestCase):
    BUDGET = 500

    def setUp(self):
        self.mod = _load()
        self.ABSENT = self.mod.ABSENT

    def v(self, before, after):
        return self.mod.verdict(before, after, self.BUDGET)

    # -- the case that must not regress -----------------------------------

    def test_over_budget_and_shrinking_passes(self):
        """594 -> 560 is progress. Blocking it would make the gate hostile to
        exactly the PRs that fix the problem."""
        failed, _ = self.v(594, 560)
        self.assertFalse(failed)

    def test_over_budget_and_unchanged_passes(self):
        """A PR that edits an over-budget file without changing its length —
        a typo fix, a reworded line — must not be blocked."""
        failed, _ = self.v(594, 594)
        self.assertFalse(failed)

    def test_over_budget_and_growing_fails(self):
        failed, reason = self.v(594, 600)
        self.assertTrue(failed)
        self.assertIn("may only shrink", reason)

    def test_over_budget_shrinking_below_budget_passes(self):
        failed, _ = self.v(594, 480)
        self.assertFalse(failed)

    # -- crossing the line ------------------------------------------------

    def test_crossing_the_budget_fails(self):
        failed, reason = self.v(497, 515)
        self.assertTrue(failed)
        self.assertIn("crossed", reason)

    def test_landing_exactly_on_the_budget_passes(self):
        """`under ~500` is read as `<= 500`. The boundary is stated once here so
        it can't drift between the script and the docs."""
        failed, _ = self.v(497, self.BUDGET)
        self.assertFalse(failed)

    def test_one_over_the_budget_fails(self):
        failed, _ = self.v(497, self.BUDGET + 1)
        self.assertTrue(failed)

    def test_growth_below_the_budget_passes(self):
        failed, _ = self.v(200, 400)
        self.assertFalse(failed)

    # -- new files --------------------------------------------------------

    def test_new_file_over_budget_fails(self):
        failed, reason = self.v(self.ABSENT, 620)
        self.assertTrue(failed)
        self.assertIn("new file", reason)

    def test_new_file_under_budget_passes(self):
        failed, _ = self.v(self.ABSENT, 300)
        self.assertFalse(failed)


class BudgetSource(unittest.TestCase):
    """The number lives in the best-practices file, not in the script."""

    def setUp(self):
        self.mod = _load()

    def test_budget_is_read_from_best_practices(self):
        self.assertEqual(self.mod._read_budget(), 500)

    def test_budget_source_actually_exists(self):
        """Guards the pointer, not the value — if the file moves, `_read_budget`
        silently falls back and the gate keeps running on a stale number."""
        self.assertTrue((REPO / self.mod.BUDGET_SOURCE).is_file(),
                        f"{self.mod.BUDGET_SOURCE} is gone; _read_budget would fall back")

    def test_pattern_matches_the_live_statement(self):
        text = (REPO / self.mod.BUDGET_SOURCE).read_text(encoding="utf-8")
        self.assertIsNotNone(self.mod.BUDGET_PATTERN.search(text),
                             "the best-practices wording changed; the parser no longer matches")


class LineCount(unittest.TestCase):
    """`_count` must agree with `wc -l`, which is what a human will check with."""

    def setUp(self):
        self.mod = _load()

    def test_trailing_newline_does_not_add_a_line(self):
        self.assertEqual(self.mod._count("a\nb\nc\n"), 3)

    def test_missing_trailing_newline_still_counts_the_last_line(self):
        self.assertEqual(self.mod._count("a\nb\nc"), 3)

    def test_empty_file_is_zero(self):
        self.assertEqual(self.mod._count(""), 0)


class RepoState(unittest.TestCase):
    """A live read of the repo, so the ratchet's progress is visible in CI."""

    def setUp(self):
        self.mod = _load()

    def test_no_skill_md_exceeds_the_worst_known_offender(self):
        """Not a budget assertion — the point of the ratchet is that existing
        offenders are allowed. This only catches a *new* file blowing past the
        current worst, which would mean the gate was bypassed entirely."""
        budget = self.mod._read_budget()
        lengths = {
            p.relative_to(REPO).as_posix(): self.mod._count(p.read_text(encoding="utf-8"))
            for p in (REPO / "plugins").rglob("SKILL.md")
        }
        self.assertTrue(lengths, "no SKILL.md found — wrong repo root?")
        worst = max(lengths.values())
        self.assertLessEqual(
            worst, 600,
            f"a SKILL.md is now {worst} lines (budget ~{budget}); "
            f"the ratchet is meant to move this down, not up",
        )


if __name__ == "__main__":
    unittest.main()
