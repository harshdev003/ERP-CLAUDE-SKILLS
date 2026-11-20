"""Guard tests for `product:record-ideas` — the fingerprint identity + rename dedup.

The fingerprint is derived from editable content but minted once and frozen as an
idea's address. These cover the consequence: a renamed idea, coming back through
record-ideas with a new title, must fold back onto its existing row instead of
minting a second one — and a genuinely different idea on the same screen must not
be mistaken for a rename.

No network — `plan_writes` classifies pure Idea objects against an in-memory map.
"""

from __future__ import annotations

import os
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(REPO, "plugins", "product", "skills", "record-ideas", "scripts")
sys.path.insert(0, SCRIPTS)

import record_ideas as r  # noqa: E402


def mk(title: str, screen: str = "home_screen", category: str = "copy", **kw) -> "r.Idea":
    """An Idea with the required `description` filled in — the tests only care about
    screen / category / title, which is what the fingerprint and rename detector read."""
    return r.Idea(title=title, description="", screen=screen, category=category, **kw)


def existing_row(idea: "r.Idea", **kw) -> dict:
    """A load_existing-shaped row for one Idea, keyed by its frozen fingerprint."""
    base = {
        "page_id": "p1", "title": idea.title, "status": "approved",
        "screen": idea.screen, "category": idea.category, "reason": "",
        "note": "", "slack_thread": "", "linear_issue": "", "last_seen": "",
    }
    base.update(kw)
    return base


class RenameDedup(unittest.TestCase):
    ORIGINAL = None  # set in setUp

    def setUp(self):
        self.original = mk("Reframe greeting from problem to aspiration")
        self.fp = self.original.fingerprint()
        self.existing = {self.fp: existing_row(self.original)}

    def test_a_renamed_idea_is_flagged_not_created(self):
        # Same screen, a re-worded title — a different fingerprint, but the same idea.
        renamed = mk("Reframe the greeting toward aspiration instead of the problem")
        self.assertNotEqual(renamed.fingerprint(), self.fp)  # would-be CREATE
        plan = r.plan_writes([renamed], self.existing)
        self.assertEqual(plan.creates, [])  # not minted as a duplicate
        self.assertEqual(len(plan.renames), 1)
        flagged_idea, prior_fp, prior_title = plan.renames[0]
        self.assertIs(flagged_idea, renamed)
        self.assertEqual(prior_fp, self.fp)  # points back at the row it belongs to
        self.assertIn("Reframe greeting", prior_title)

    def test_allow_similar_records_it_as_new(self):
        renamed = mk("Reframe the greeting toward aspiration instead of the problem")
        plan = r.plan_writes([renamed], self.existing, allow_similar=True)
        self.assertEqual(plan.renames, [])
        self.assertIn(renamed, plan.creates)

    def test_a_different_idea_on_the_same_screen_is_not_a_rename(self):
        other = mk("Add a daily check-in card")
        plan = r.plan_writes([other], self.existing)
        self.assertEqual(plan.renames, [])
        self.assertIn(other, plan.creates)

    def test_a_rename_that_also_changed_the_screen_is_missed_by_design(self):
        # The detector requires the same screen, so a screen rename falls through to
        # CREATE. Documented limitation — the surface is the stable anchor.
        moved = mk("Reframe greeting from problem to aspiration slightly reworded", screen="dashboard_screen")
        plan = r.plan_writes([moved], self.existing)
        self.assertEqual(plan.renames, [])
        self.assertIn(moved, plan.creates)

    def test_an_exact_re_observation_still_updates_not_renames(self):
        # Unchanged title -> same fingerprint -> a normal UPDATE path, never a rename.
        again = mk(self.original.title, last_seen="2099-01-01")
        plan = r.plan_writes([again], self.existing)
        self.assertEqual(plan.renames, [])
        self.assertEqual(plan.creates, [])
        self.assertEqual(len(plan.updates), 1)


class FingerprintIsMintedFromContent(unittest.TestCase):
    def test_same_content_hashes_the_same_across_sources(self):
        a = mk("Show the streak", screen="settings_screen", category="ui")
        b = mk("  Show the Streak  ", screen="settings_screen", category="ui")
        self.assertEqual(a.fingerprint(), b.fingerprint())  # trim + lowercase

    def test_a_reworded_title_changes_the_computed_fingerprint(self):
        a = mk("Show the streak", screen="settings_screen", category="ui")
        b = mk("Show the streak count", screen="settings_screen", category="ui")
        self.assertNotEqual(a.fingerprint(), b.fingerprint())


class IdeaIdIsTheAddress(unittest.TestCase):
    def test_reads_the_notion_auto_id(self):
        row = {"properties": {"Idea ID": {"unique_id": {"prefix": "IDEA", "number": 42}}}}
        self.assertEqual(r.idea_id_str(row), "IDEA-42")

    def test_number_without_a_prefix(self):
        row = {"properties": {"Idea ID": {"unique_id": {"prefix": None, "number": 7}}}}
        self.assertEqual(r.idea_id_str(row), "7")

    def test_empty_when_unset(self):
        self.assertEqual(r.idea_id_str({"properties": {}}), "")


class ProposalFields(unittest.TestCase):
    """The fields `brainstorm-ideas` emits beyond the core idea reach the row."""

    RAW = {
        "title": "Offer a method after the first heavy session",
        "description": "d", "screen": "conversation_screen", "category": "feature",
        "proposedBy": "Claude", "portfolio": "evidence", "betSize": "Medium",
        "confidence": "medium", "evidenceGrade": "b",
        "variants": ["b: control", "a: method card after session"],
        "testPlan": {
            "primaryMetric": "W1 retention", "direction": "up",
            "guardrails": ["session rating", "cost-to-serve"],
            "instrumentation": "exposure on card render",
            "decisionWindow": "14 days", "falsification": "W1 flat or down",
        },
    }

    def test_coerced_and_written(self):
        idea = r._coerce(self.RAW)
        self.assertEqual(idea.validate(), [])
        props = r._build_props(idea)
        self.assertEqual(props["Proposed by"], {"select": {"name": "claude"}})
        self.assertEqual(props["Bet size"], {"select": {"name": "medium"}})
        self.assertEqual(props["Evidence grade"], {"select": {"name": "B"}})
        plan = props["Test plan"]["rich_text"][0]["text"]["content"]
        self.assertIn("Guardrails: session rating; cost-to-serve", plan)
        self.assertIn("Falsified if: W1 flat or down", plan)
        self.assertIn("- a: method card after session",
                      props["Variants"]["rich_text"][0]["text"]["content"])

    def test_a_bad_enum_is_refused(self):
        idea = r._coerce({**self.RAW, "portfolio": "safe-bet"})
        self.assertTrue(any("portfolio" in e for e in idea.validate()))

    def test_absent_fields_write_nothing(self):
        props = r._build_props(r._coerce({"title": "t", "description": "d"}))
        for name in ("Proposed by", "Portfolio", "Variants", "Test plan"):
            self.assertNotIn(name, props)

    def test_rich_text_is_capped(self):
        idea = r._coerce({**self.RAW, "testPlan": {"falsification": "x" * 5000}})
        self.assertLessEqual(len(idea.test_plan), r.RICH_TEXT_MAX)


class ListFilter(unittest.TestCase):
    """A route counts its own week against the cap."""

    ROWS = [
        {"title": "a", "proposed_by": "claude", "first_seen": "2026-10-01"},
        {"title": "b", "proposed_by": "viktor", "first_seen": "2026-10-02"},
        {"title": "c", "proposed_by": "claude", "first_seen": "2026-09-20"},
        {"title": "d", "proposed_by": "", "first_seen": "2026-10-03"},
    ]

    def test_route_and_week(self):
        got = r.filter_rows(self.ROWS, proposed_by="claude", since="2026-09-29")
        self.assertEqual([x["title"] for x in got], ["a"])

    def test_no_filter_returns_everything(self):
        self.assertEqual(len(r.filter_rows(self.ROWS)), 4)


if __name__ == "__main__":
    unittest.main()
