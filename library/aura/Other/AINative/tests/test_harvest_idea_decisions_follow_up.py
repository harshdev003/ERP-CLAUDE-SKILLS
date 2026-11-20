"""The follow-up pass: a human reply after the loop's last reply is never left unread."""

from __future__ import annotations

import os
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "plugins", "product", "skills",
                                "harvest-idea-decisions", "scripts"))

import harvest_idea_decisions as h  # noqa: E402


def reply(ts, text, bot=False, seen=False):
    return {"user": "UBOT" if bot else "U1", "ts": ts, "text": text, "bot": bot, "seen": seen}


class FollowUp(unittest.TestCase):
    def test_a_question_after_the_loops_reply_is_unaddressed(self):
        t = {"replies": [reply("1.0", "plan", bot=True),
                         reply("2.0", "we do store a mixpanel event when the referral is credited?")]}
        self.assertEqual(h.follow_up(t)["verdict"], "needs_follow_up")

    def test_an_instruction_is_unaddressed_too(self):
        t = {"replies": [reply("1.0", "answer", bot=True),
                         reply("2.0", "Sounds good we can go ahead and create the PRs")]}
        res = h.follow_up(t)
        self.assertEqual(res["verdict"], "needs_follow_up")
        self.assertEqual(res["ts"], "2.0")

    def test_a_loop_reply_after_it_addresses_it(self):
        t = {"replies": [reply("2.0", "a question?"), reply("3.0", "the answer", bot=True)]}
        self.assertEqual(h.follow_up(t)["verdict"], "no_decision")

    def test_a_reply_marked_seen_is_addressed(self):
        t = {"replies": [reply("1.0", "x", bot=True), reply("2.0", "thanks!", seen=True)]}
        self.assertEqual(h.follow_up(t)["verdict"], "no_decision")

    def test_seen_is_read_from_the_loops_own_eyes_reaction(self):
        msgs = [{"ts": "2.0", "user": "U1", "text": "thanks",
                 "reactions": [{"name": "eyes", "users": [h.LOOP_BOT_USER]}]},
                {"ts": "3.0", "user": "U1", "text": "hm",
                 "reactions": [{"name": "eyes", "users": ["U2"]}]}]
        got = h.normalise_replies(msgs)
        self.assertTrue(got[0]["seen"])
        self.assertFalse(got[1]["seen"])

    def test_approved_rows_are_read(self):
        self.assertIn("approved", h.FOLLOW_UP_ONLY)
        self.assertIn("needs_follow_up", h.VERDICTS)
        self.assertNotIn("needs_follow_up", h.WRITABLE)

    def test_a_decision_carries_the_ts_of_the_reply_that_made_it(self):
        t = {"text": "idea", "reactions": [],
             "replies": [{"user": "U1BCYETM2", "ts": "5.0", "text": "I approve for implementation",
                          "bot": False, "seen": False}]}
        res = h.classify(t, h.GATE_IMPLEMENTATION, "C0C1JTK3YCC")
        self.assertEqual(res["verdict"], "approved")
        self.assertEqual(res["ts"], "5.0")


if __name__ == "__main__":
    unittest.main()
