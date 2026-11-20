"""The decider's bare "Skip" is a refusal; the same word from anyone else is input.

Real reply, IDEA-30, 2026-10-05: Steve (the loop channel's decider) wrote "Skip " and
the harvest returned `needs_judgment`. A bare skip/pass is the decider's shortest and
most common no. It is whole-clause and a statement: "Skip?" asks, and a skip inside a
longer instruction ("skip the badge, ship the copy") is not a verdict on the idea.
"""

from __future__ import annotations

import os
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(
    REPO, "plugins", "product", "skills", "harvest-idea-decisions", "scripts"))

import harvest_idea_decisions as h  # noqa: E402

LOOP = "C0C1JTK3YCC"
STEVE = "U1BCYETM2"
JEROME = "UEW6170R3"


def thread(text, user=STEVE):
    return {"text": "💡 *IDEA-30 · …*", "reactions": [],
            "replies": [{"text": text, "user": user, "ts": "1791243401.145689"}]}


class BareSkipFromTheDecider(unittest.TestCase):
    def test_the_real_reply_rejects(self):
        self.assertEqual(h.classify(thread("Skip "), channel=LOOP)["verdict"], "rejected")

    def test_bare_forms_reject(self):
        for t in ("skip", "Skip.", "Pass", "pass!", "Skip it", "pass on this",
                  "Skip this one", "Thanks. Skip"):
            self.assertEqual(h.classify(thread(t), channel=LOOP)["verdict"], "rejected", t)

    def test_the_same_text_from_anyone_else_needs_judgment(self):
        for t in ("Skip ", "Pass"):
            self.assertEqual(
                h.classify(thread(t, user=JEROME), channel=LOOP)["verdict"],
                "needs_judgment", t)

    def test_a_question_is_not_a_decision(self):
        for t in ("Skip?", "Pass?", "should we skip?"):
            self.assertNotIn(h.classify(thread(t), channel=LOOP)["verdict"],
                             ("rejected", "approved"), t)

    def test_a_skip_inside_an_instruction_is_not_a_verdict(self):
        for t in ("skip the badge and ship the copy", "pass the flag to the view"):
            self.assertNotIn(h.classify(thread(t), channel=LOOP)["verdict"],
                             ("rejected",), t)


if __name__ == "__main__":
    unittest.main()
