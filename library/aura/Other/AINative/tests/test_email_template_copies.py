#!/usr/bin/env python3
"""The reviewer's copy of the conversation-guardrail email template must match the triage skill's.

`eng:investigate-conversation-guardrail` drafts the CS email from
`references/email_template.md` (canonical). `eng:review-conversation-guardrails`
checks a reviewer's corrected draft against the same rules, so it carries a
byte-identical copy — skills are self-contained and may not read each other's
files at run time.

If the copies drift, a reviewer's correction is judged by different rules than the
draft was written by: a correction the triage skill would never produce can pass,
or the reviewer is blocked from a wording the template requires. Both files still
parse and render, so nothing else notices.

`eng:investigate-memory-ingestion-guardrail` has its own `email_template.md`, a
deliberate fork for a passive upload rather than a live conversation; it is out of
this family by design.

Stdlib unittest, no network.
"""
from __future__ import annotations

import pathlib
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
CANONICAL = REPO / "plugins/eng/skills/investigate-conversation-guardrail/references/email_template.md"
COPIES = [
    REPO / "plugins/eng/skills/review-conversation-guardrails/references/email_template.md",
]
FORKS = {
    REPO / "plugins/eng/skills/investigate-memory-ingestion-guardrail/references/email_template.md",
}


class EmailTemplateCopies(unittest.TestCase):
    def test_family_membership_is_complete(self):
        found = set(REPO.joinpath("plugins").rglob("email_template.md"))
        self.assertEqual(found, {CANONICAL, *COPIES, *FORKS},
                         "an email_template.md exists outside the tested family — add it to "
                         "COPIES, or to FORKS with a reason")

    def test_copies_match_the_canonical(self):
        canonical = CANONICAL.read_text(encoding="utf-8")
        self.assertIn("## Final validation checklist", canonical)
        for copy in COPIES:
            with self.subTest(copy=str(copy.relative_to(REPO))):
                self.assertEqual(
                    copy.read_text(encoding="utf-8"), canonical,
                    "the reviewer's email template drifted from the triage skill's — edit the "
                    "canonical first and copy the whole file",
                )

    def test_the_comparison_can_fail(self):
        """A comparison that cannot see a changed word is not a guard."""
        canonical = CANONICAL.read_text(encoding="utf-8")
        self.assertIn("You don't need to reply to this email.", canonical)
        self.assertNotEqual(canonical.replace("You don't need", "You do need", 1), canonical)


LEVELS_CANONICAL = REPO / "plugins/eng/skills/investigate-conversation-guardrail/references/outreach_levels.md"
LEVELS_COPIES = [
    REPO / "plugins/eng/skills/review-conversation-guardrails/references/outreach_levels.md",
]


class OutreachLevelsCopies(unittest.TestCase):
    """The triage assigns the level and the reviewer applies the same rules to send or not.

    If the copies drift, a case the triage marks "no email" can be one the reviewer would
    send, or the reverse, and a person at risk falls between them with nothing failing.
    """

    def test_family_membership_is_complete(self):
        found = set(REPO.joinpath("plugins").rglob("outreach_levels.md"))
        self.assertEqual(found, {LEVELS_CANONICAL, *LEVELS_COPIES},
                         "an outreach_levels.md exists outside the tested family — add it to LEVELS_COPIES")

    def test_copies_match_the_canonical(self):
        canonical = LEVELS_CANONICAL.read_text(encoding="utf-8")
        self.assertIn("### Always an email", canonical)
        for copy in LEVELS_COPIES:
            with self.subTest(copy=str(copy.relative_to(REPO))):
                self.assertEqual(
                    copy.read_text(encoding="utf-8"), canonical,
                    "the reviewer's outreach levels drifted from the triage skill's — edit the "
                    "canonical first and copy the whole file",
                )


if __name__ == "__main__":
    unittest.main()
