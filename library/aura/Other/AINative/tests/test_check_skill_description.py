#!/usr/bin/env python3
"""Tests for `scripts/check-skill-description.py`, plus a repo-wide sweep.

Three things are worth pinning here, and the first two are the ones a naive
implementation gets wrong:

**The character count must match the loader's.** A `description` is almost always
a folded YAML block (`>`), so its stored form is multi-line and indented while
the loader measures the *joined* string. Counting raw bytes, or forgetting to
strip the two-space indent, gives a number that disagrees with the marketplace —
and a gate that disagrees with the thing it is guarding is worse than no gate,
because it produces confident wrong verdicts in both directions. The extractor
tests below are the guard on that.

**The tag rule must not fire on prose.** `<500` is a real tier label in the Aura
cost skills and `a < b` is ordinary text; neither is markup. A rule that flags
them would be permanently red on descriptions that are perfectly valid, which is
how a gate gets switched off. The negative cases are the load-bearing half.

**The repo starts clean and must stay clean.** ENG-1236 brought all 144
offenders into compliance in one pass, so unlike the sibling line gate there is
nothing to grandfather — `RepoIsClean` asserts the true state directly rather
than trusting a diff.

Stdlib only, no git, no network.
"""
from __future__ import annotations

import importlib.util
import pathlib
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "check-skill-description.py"


def _load():
    """Import the checker despite its hyphenated, non-importable filename."""
    spec = importlib.util.spec_from_file_location("check_skill_description", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


MOD = _load()


def skill_md(description_block: str) -> str:
    return f"---\nname: demo\n{description_block}metadata:\n  version: \"1.0.0\"\n---\n\n# Demo\n\nBody.\n"


class Extractor(unittest.TestCase):
    """The count the gate reports has to be the count the loader sees."""

    def test_folded_block_joins_lines_with_single_spaces(self):
        got = MOD.extract_description(skill_md(
            "description: >\n"
            "  Alpha beta\n"
            "  gamma delta\n"
        ))
        self.assertEqual(got, "Alpha beta gamma delta")

    def test_folded_block_length_is_the_joined_length_not_the_raw_bytes(self):
        """The stored form carries an indent and newlines the loader never sees.
        Measuring the file instead of the value overstates every description."""
        raw_block = "description: >\n  Alpha beta\n  gamma delta\n"
        got = MOD.extract_description(skill_md(raw_block))
        self.assertEqual(len(got), len("Alpha beta gamma delta"))
        self.assertLess(len(got), len(raw_block))

    def test_folded_block_keeps_a_blank_line_as_a_newline(self):
        got = MOD.extract_description(skill_md(
            "description: >\n"
            "  First para.\n"
            "\n"
            "  Second para.\n"
        ))
        self.assertEqual(got, "First para.\nSecond para.")

    def test_literal_block_preserves_its_own_line_breaks(self):
        got = MOD.extract_description(skill_md(
            "description: |\n"
            "  Line one\n"
            "  Line two\n"
        ))
        self.assertEqual(got, "Line one\nLine two")

    def test_plain_inline_scalar(self):
        got = MOD.extract_description(skill_md("description: Just one line.\n"))
        self.assertEqual(got, "Just one line.")

    def test_stops_at_the_next_frontmatter_key(self):
        """A greedy read would swallow `metadata:` and inflate the count."""
        got = MOD.extract_description(skill_md(
            "description: >\n"
            "  Only this.\n"
        ))
        self.assertNotIn("version", got)
        self.assertEqual(got, "Only this.")

    def test_missing_description_returns_none(self):
        self.assertIsNone(MOD.extract_description(
            "---\nname: demo\n---\n\n# Demo\n"))

    def test_missing_frontmatter_returns_none(self):
        self.assertIsNone(MOD.extract_description("# Demo\n\nNo frontmatter.\n"))


class LengthRule(unittest.TestCase):
    LIMIT = 1024

    def test_exactly_at_the_limit_passes(self):
        """`at most 1024` includes 1024 — several real skills sit on the line."""
        self.assertEqual(MOD.violations("x" * 1024, self.LIMIT), [])

    def test_one_over_the_limit_fails(self):
        reasons = MOD.violations("x" * 1025, self.LIMIT)
        self.assertEqual(len(reasons), 1)
        self.assertIn("1025", reasons[0])
        self.assertIn("trim 1", reasons[0])

    def test_well_under_passes(self):
        self.assertEqual(MOD.violations("A short description.", self.LIMIT), [])


class TagRule(unittest.TestCase):
    """The negative cases are the point: a rule that flags ordinary prose is a
    rule that gets switched off."""

    LIMIT = 1024

    def _tag_reasons(self, desc):
        return [r for r in MOD.violations(desc, self.LIMIT) if "XML" in r]

    # -- must fire --------------------------------------------------------

    def test_flags_a_lowercase_placeholder(self):
        self.assertTrue(self._tag_reasons("Logs for user <uid> today."))

    def test_flags_a_camelcase_placeholder(self):
        self.assertTrue(self._tag_reasons("Fetch <userId> from the API."))

    def test_flags_a_closing_tag(self):
        self.assertTrue(self._tag_reasons("Ends with </name> somewhere."))

    def test_flags_a_placeholder_inside_backticks(self):
        """The loader scans the raw string; backticks are not an escape."""
        self.assertTrue(self._tag_reasons("Call `GET /users/<id>` for the record."))

    def test_reports_each_distinct_tag(self):
        reason = self._tag_reasons("From <plugin> to <skill> and back to <plugin>.")[0]
        self.assertIn("<plugin>", reason)
        self.assertIn("<skill>", reason)

    # -- must NOT fire ----------------------------------------------------

    def test_numeric_tier_label_is_not_a_tag(self):
        """`<500` is a real payout tier in the Aura cost skills."""
        self.assertEqual(self._tag_reasons("Tiers: `<500`, `500-1999`, `2000-4999`."), [])

    def test_bare_comparison_is_not_a_tag(self):
        self.assertEqual(self._tag_reasons("Fails when net < 0 and cost > 5."), [])

    def test_curly_placeholder_is_the_accepted_form(self):
        self.assertEqual(self._tag_reasons("Logs for user {uid} on {date}."), [])

    def test_arrow_is_not_a_tag(self):
        self.assertEqual(self._tag_reasons("Runs harvest -> report -> publish."), [])

    def test_email_style_angle_brackets_without_a_leading_letter(self):
        self.assertEqual(self._tag_reasons("Windows use <-from>/<-to>."), [])


class BothRules(unittest.TestCase):
    def test_a_description_can_violate_both_at_once(self):
        reasons = MOD.violations("<uid> " + "x" * 1200, 1024)
        self.assertEqual(len(reasons), 2)


class LimitSource(unittest.TestCase):
    """The limit is stated once, in the best-practices file — the same
    point-don't-restate rule the line gate follows. If that statement is reworded
    away, the gate silently falls back to a hardcoded number, so pin the link."""

    def test_limit_is_parsed_from_the_canonical_best_practices(self):
        self.assertEqual(MOD._read_limit(REPO), 1024)

    def test_fallback_is_used_when_the_source_is_unreadable(self):
        """Unreadable source degrades to the last known number rather than
        taking the gate offline."""
        self.assertEqual(MOD._read_limit(pathlib.Path("/nonexistent")),
                         MOD.LIMIT_FALLBACK)


class RepoIsClean(unittest.TestCase):
    """No grandfathering: every skill in the repo satisfies both rules today."""

    def test_every_skill_description_is_valid(self):
        limit = MOD._read_limit(REPO)
        skills = sorted((REPO / "plugins").glob("*/skills/*/SKILL.md"))
        self.assertGreater(len(skills), 100, "skill sweep found suspiciously few files")

        failures = []
        for path in skills:
            desc = MOD.extract_description(path.read_text(encoding="utf-8"))
            rel = path.relative_to(REPO).as_posix()
            if desc is None:
                failures.append(f"{rel}: no description")
                continue
            for reason in MOD.violations(desc, limit):
                failures.append(f"{rel}: {reason}")

        self.assertEqual(failures, [], "\n".join(failures))

    def test_every_skill_has_a_description(self):
        """The extractor returning None must mean 'absent', not 'unparsed shape'
        — otherwise a new YAML form would read as a missing field."""
        for path in sorted((REPO / "plugins").glob("*/skills/*/SKILL.md")):
            with self.subTest(skill=path.parent.name):
                self.assertIsNotNone(
                    MOD.extract_description(path.read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()
