#!/usr/bin/env python3
"""Tests for `scripts/check-memory-length.py`.

The gate's whole value is in the middle band, and two of its cases are easy to
get backwards:

* a file **over** the limit that shrinks but is still over must still FAIL —
  unlike the SKILL.md ratchet next door, where shrinking is always progress.
  Here the limit is the harness's, not ours: a file at 151,000 chars is not
  being loaded, and "it's smaller than it was" does not change that.
* a file **inside the warn band** that holds or shrinks must PASS. A gate that
  blocks a typo fix on a large CLAUDE.md is a gate someone switches off.

The unit is characters, not bytes. That distinction is load-bearing at this
size and this file's prose: the real CLAUDE.md was 154,044 characters and
155,113 bytes, so a byte count fires about a thousand characters early and
would report a number that does not match what the harness tells the user.

Stdlib only, no git, no network: `verdict()` is pure, so the rules are testable
without building repo fixtures.
"""
from __future__ import annotations

import importlib.util
import pathlib
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "check-memory-length.py"


def _load():
    """Import the checker despite its hyphenated, non-importable filename."""
    spec = importlib.util.spec_from_file_location("check_memory_length", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Verdict(unittest.TestCase):
    def setUp(self):
        self.mod = _load()
        self.ABSENT = self.mod.ABSENT
        self.LIMIT = self.mod.LIMIT
        self.WARN = self.mod.WARN_AT

    def v(self, before, after):
        return self.mod.verdict(before, after)

    # -- the cases that must not regress ----------------------------------

    def test_over_limit_but_shrinking_still_fails(self):
        """Unlike the SKILL.md ratchet, shrinking is not enough here. The limit
        belongs to the harness: at 151,000 the file is not being loaded, and a
        PR that leaves it there has not fixed anything."""
        failed, _, reason = self.v(self.LIMIT + 5_000, self.LIMIT + 1_000)
        self.assertTrue(failed)
        self.assertIn("stop loading it", reason)

    def test_inside_warn_band_and_holding_passes(self):
        """A typo fix on a large CLAUDE.md must not be blocked."""
        n = self.WARN + 3_000
        failed, warned, _ = self.v(n, n)
        self.assertFalse(failed)
        self.assertTrue(warned)

    def test_inside_warn_band_and_shrinking_passes(self):
        failed, warned, _ = self.v(self.WARN + 5_000, self.WARN + 1_000)
        self.assertFalse(failed)
        self.assertTrue(warned)

    def test_inside_warn_band_and_growing_fails(self):
        failed, _, reason = self.v(self.WARN + 1_000, self.WARN + 1_100)
        self.assertTrue(failed)
        self.assertIn("may only shrink", reason)

    # -- the free band -----------------------------------------------------

    def test_below_warn_band_may_grow_freely(self):
        """Ordinary edits below the warn threshold carry no rule at all —
        otherwise the gate is a tax on every documentation change."""
        failed, warned, _ = self.v(10_000, self.WARN - 1)
        self.assertFalse(failed)
        self.assertFalse(warned)

    def test_growing_from_free_band_into_warn_band_passes_with_a_warning(self):
        """The PR that first crosses into the band is the one that should hear
        about it, and it should not be blocked — the runway is the point."""
        failed, warned, reason = self.v(self.WARN - 1_000, self.WARN + 500)
        self.assertFalse(failed)
        self.assertTrue(warned)
        self.assertIn("not grow", reason)

    def test_crossing_the_limit_fails(self):
        failed, _, reason = self.v(self.WARN, self.LIMIT + 1)
        self.assertTrue(failed)
        self.assertIn("over the", reason)

    def test_exactly_at_the_limit_fails(self):
        """The harness reported a 154,044-char file as over a 150.0k limit, so
        the boundary is inclusive on the failing side."""
        failed, _, _ = self.v(100_000, self.LIMIT)
        self.assertTrue(failed)

    # -- new files ---------------------------------------------------------

    def test_new_file_under_the_warn_band_passes_silently(self):
        failed, warned, _ = self.v(self.ABSENT, 20_000)
        self.assertFalse(failed)
        self.assertFalse(warned)

    def test_new_file_inside_the_warn_band_warns_but_passes(self):
        """There is no `before` to ratchet against, so the band can only warn."""
        failed, warned, _ = self.v(self.ABSENT, self.WARN + 2_000)
        self.assertFalse(failed)
        self.assertTrue(warned)

    def test_new_file_over_the_limit_fails(self):
        failed, _, _ = self.v(self.ABSENT, self.LIMIT + 1)
        self.assertTrue(failed)


class Counting(unittest.TestCase):
    """The unit is characters, matching what the harness reports."""

    def setUp(self):
        self.mod = _load()

    def test_counts_characters_not_bytes(self):
        text = "→" * 1000          # 1000 chars, 3000 bytes in UTF-8
        self.assertEqual(self.mod._count(text), 1000)
        self.assertEqual(len(text.encode("utf-8")), 3000)

    def test_real_claude_md_character_count_is_the_reported_one(self):
        """Regression anchor for the unit: the live CLAUDE.md must measure in
        characters, and must currently be under the limit."""
        n = self.mod._count((REPO / "CLAUDE.md").read_text(encoding="utf-8"))
        self.assertLess(n, self.mod.LIMIT)


class Bands(unittest.TestCase):
    """The thresholds themselves."""

    def setUp(self):
        self.mod = _load()

    def test_warn_band_leaves_real_runway(self):
        """A band narrower than a few thousand characters would fire only once
        the extraction is already urgent, which is the failure mode the band
        exists to prevent."""
        self.assertLess(self.mod.WARN_AT, self.mod.LIMIT)
        self.assertGreaterEqual(self.mod.LIMIT - self.mod.WARN_AT, 10_000)

    def test_both_memory_filenames_are_covered(self):
        self.assertIn("CLAUDE.md", self.mod.MEMORY_NAMES)
        self.assertIn("AGENTS.md", self.mod.MEMORY_NAMES)


if __name__ == "__main__":
    unittest.main()
