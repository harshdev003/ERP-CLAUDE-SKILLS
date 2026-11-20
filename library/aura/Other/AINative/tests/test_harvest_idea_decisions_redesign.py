"""`needs_redesign` — "I like the idea but not the design" is a request, not a verdict.

It must never write a status, never read as a rejection of an idea the reviewer just
endorsed, and never swallow an explicit gate decision that happens to mention design.
"""

from __future__ import annotations

import os
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(REPO, "plugins", "product", "skills", "harvest-idea-decisions", "scripts")
sys.path.insert(0, SCRIPTS)

import harvest_idea_decisions as h  # noqa: E402

LOOP = "C0C1JTK3YCC"


def thread(*replies, reactions=None):
    out = []
    for i, r in enumerate(replies):
        if isinstance(r, str):
            r = {"text": r}
        # The loop channel decides by one person; these tests are about wording, so
        # a reply with no explicit author speaks as them.
        out.append({"text": r.get("text", ""), "user": r.get("user", "U1BCYETM2"),
                    "ts": r.get("ts", f"{1700000000 + i}.000100"), "bot": r.get("bot", False)})
    return {"text": "💡 *IDEA-1 · Add social proof*", "reactions": reactions or [], "replies": out}


class AsksForRedesign(unittest.TestCase):
    def test_idea_yes_design_no_sentences(self):
        for t in (
            "I like the idea but not the design",
            "Love the idea, the badge should be smaller though",
            "Good idea — but a different placement please",
            "like the idea, not sold on the design",
        ):
            self.assertTrue(h._asks_for_redesign(t), t)

    def test_plain_redesign_asks(self):
        for t in (
            "Can we try a different placement?",
            "This needs a redesign",
            "The placement doesn't work for me",
            "design could use another pass",
        ):
            self.assertTrue(h._asks_for_redesign(t), t)

    def test_decisions_and_neutral_replies_are_not_redesigns(self):
        for t in (
            "let's build it",
            "approved",
            "no because we tested this in April",
            "Have we tested this before?",
            "I like the idea",
            "The design is great, ship it",
        ):
            self.assertFalse(h._asks_for_redesign(t), t)

    def test_a_named_gate_decision_wins_over_a_design_note(self):
        self.assertFalse(h._asks_for_redesign(
            "I approve for implementation — but move the badge lower"))
        self.assertFalse(h._asks_for_redesign(
            "Not approved for release: the design isn't right yet"))

    def test_any_explicit_verdict_wins_over_a_design_note(self):
        # Generic approvals and rejections that mention the design are still verdicts —
        # they must never be swallowed into a redesign request.
        for t in (
            "Approved, though the design isn't right yet",
            "Let's build it — the placement doesn't work for me but ship it",
            "No thanks, and the design isn't right either",
            "Leave it as is; a different placement won't save this idea",
        ):
            self.assertFalse(h._asks_for_redesign(t), t)

    def test_a_deferral_phrasing_is_still_a_redesign_ask(self):
        # "first" trips DEFER_RE, but "fix the design first" is the canonical redesign ask.
        self.assertTrue(h._asks_for_redesign("Like the idea, let's fix the design first"))


class Classify(unittest.TestCase):
    def test_redesign_request_is_its_own_verdict(self):
        res = h.classify(thread("I like the idea but not the design"), channel=LOOP)
        self.assertEqual(res["verdict"], "needs_redesign")
        self.assertIn("not the design", res["evidence"])
        self.assertEqual(res["decider"], "U1BCYETM2")

    def test_redesign_is_never_writable(self):
        self.assertNotIn("needs_redesign", getattr(h, "WRITABLE", ()))
        self.assertIn("needs_redesign", h.VERDICTS)

    def test_a_bot_reply_after_the_request_means_handled(self):
        res = h.classify(thread(
            "I like the idea but not the design",
            {"text": "Revised Current vs Proposed attached.", "bot": True},
        ), channel=LOOP)
        self.assertNotEqual(res["verdict"], "needs_redesign")

    def test_a_later_request_after_a_revision_is_flagged_again(self):
        res = h.classify(thread(
            "I like the idea but not the design",
            {"text": "Revised Current vs Proposed attached.", "bot": True},
            "Still not sold on the placement — can we try a different placement?",
        ), channel=LOOP)
        self.assertEqual(res["verdict"], "needs_redesign")
        self.assertEqual(res["decider"], "U1BCYETM2")

    def test_explicit_decision_beats_a_redesign_request(self):
        res = h.classify(thread(
            "I like the idea but not the design",
            "I approve for implementation",
        ), channel=LOOP)
        self.assertEqual(res["verdict"], "approved")

    def test_a_rejection_that_mentions_the_design_is_written_as_rejected(self):
        res = h.classify(thread("No thanks — and the design isn't right either"), channel=LOOP)
        self.assertEqual(res["verdict"], "rejected")

    def test_a_generic_approval_that_mentions_the_design_is_approved(self):
        """A decision beats a design note in the same sentence — it must not come back
        as `needs_redesign`. No hedge here, so it decides."""
        res = h.classify(thread("Let's build it, the placement doesn't work but ship it"),
                         channel=LOOP)
        self.assertEqual(res["verdict"], "approved")

    def test_a_design_note_inside_an_approval_is_still_an_approval(self):
        res = h.classify(thread(
            "I approve for implementation — but move the badge lower"), channel=LOOP)
        self.assertEqual(res["verdict"], "approved")

    def test_redesign_request_is_not_read_as_a_deferral(self):
        # "fix the design first" would trip DEFER_RE if the request reached the scan
        res = h.classify(thread("Like the idea, let's fix the design first"), channel=LOOP)
        self.assertEqual(res["verdict"], "needs_redesign")

    def test_redesign_ask_phrased_as_a_question_is_not_needs_answer(self):
        res = h.classify(thread("Can we try a different placement?"), channel=LOOP)
        self.assertEqual(res["verdict"], "needs_redesign")

    def test_a_real_question_still_needs_answer(self):
        res = h.classify(thread("Have we tested this before?"), channel=LOOP)
        self.assertEqual(res["verdict"], "needs_answer")


if __name__ == "__main__":
    unittest.main()
