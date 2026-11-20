"""The loop's Slack vocabulary, pinned across the skills that emit it.

Four marks and two gates, split across three skills that never import one
another. Nothing fails when they drift — the posts still send, the rows still
write — and the damage is a person reading a mark that means the opposite of
what the poster intended. A lost experiment marked ❌ reads as abandoned: an
answered question filed as one nobody ever asked.

So the vocabulary is asserted here, in one place, rather than trusted to three
docstrings agreeing.
"""

from __future__ import annotations

import os
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SK = os.path.join(REPO, "plugins", "product", "skills")
for s in ("share-ideas", "learn-idea", "build-idea"):
    sys.path.insert(0, os.path.join(SK, s, "scripts"))

import share_ideas as share  # noqa: E402
import learn_idea as learn   # noqa: E402
import build_idea as build   # noqa: E402

# Reserved for endings. Never a result.
DONE = ":white_check_mark:"
ABANDONED = ":x:"


class ResultMarksAreNotTheTerminalMarks(unittest.TestCase):
    """✅ is done and ❌ is abandoned. A result must use neither."""

    def test_no_result_uses_a_terminal_mark(self):
        for status, mark in learn.RESULT_EMOJI.items():
            with self.subTest(status=status):
                self.assertNotIn(mark, (DONE, ABANDONED))

    def test_lost_is_not_the_abandoned_mark(self):
        """The one pairing that inverts meaning: a loss is an answer, abandoned
        is the absence of one."""
        self.assertNotEqual(learn.RESULT_EMOJI["lost"], ABANDONED)

    def test_won_is_not_the_done_mark(self):
        """Won and done are different moments — an experiment can win and still
        need its flag taken out before the thread closes."""
        self.assertNotEqual(learn.RESULT_EMOJI["won"], DONE)

    def test_every_verdict_has_a_mark(self):
        self.assertEqual(set(learn.VERDICT.values()), set(learn.RESULT_EMOJI))

    def test_marks_are_distinct(self):
        marks = list(learn.RESULT_EMOJI.values())
        self.assertEqual(len(marks), len(set(marks)))


class SharedIdeasStatesTheProtocol(unittest.TestCase):
    """The protocol's wording lives in `DECISION_PROTOCOL` — the channel intro / topic
    text. It is deliberately NOT repeated in every idea post (it buried the idea;
    Jerome, 2026-09-17), so these tests pin the wording, and one pins its absence
    from the rendered post."""

    def test_it_names_the_gate_rather_than_saying_yes(self):
        """There are two gates on one thread, weeks apart. A bare "yes" cannot
        say which one it means."""
        self.assertIn("I approve for implementation", share.DECISION_PROTOCOL)
        self.assertNotIn("reply *yes*", share.DECISION_PROTOCOL)

    def test_it_requires_a_reason_for_a_refusal(self):
        self.assertIn("no because", share.DECISION_PROTOCOL)

    def test_it_says_a_thumbs_up_is_not_a_decision(self):
        """Five April ideas stalled on exactly this."""
        self.assertIn(":+1:", share.DECISION_PROTOCOL)

    def test_the_protocol_stays_out_of_the_rendered_post(self):
        """The post is the idea and nothing else; the channel intro carries the
        protocol. Repeating it under every idea buried the idea."""
        body = share.render_message({
            "screen": "settings_screen", "title": "t", "fingerprint": "f",
            "effort": "S", "impact": "high", "category": "c", "lever": "l",
            "description": "d", "rationale": "r", "hypothesis": "h"})
        self.assertNotIn("I approve for implementation", body)
        self.assertNotIn(":+1:", body)
        self.assertTrue(body.startswith("*f · t*"))


class NoSkillDerivesAVerdictFromActive(unittest.TestCase):
    """`active` having one letter means "not splitting", and covers a
    pre-release force, a rollout, a disabled flag and a permanent kill switch
    alike. The collision gate may use it; nothing may read a result from it."""

    def test_the_explanation_does_not_claim_a_rollout(self):
        doc = build.live_aurie_from_api.__doc__ or ""
        self.assertNotIn("winner was rolled out", doc)
        self.assertIn("splitting", doc)

    def test_the_explanation_warns_against_a_derived_verdict(self):
        doc = build.live_aurie_from_api.__doc__ or ""
        self.assertIn("verdict", doc.lower())

    def test_the_live_test_itself_is_unchanged(self):
        """The behaviour was right; only the reason was wrong."""
        rows = [
            {"id": "splitting",   "endedAt": None, "active": "ab",  "targetApp": "ai"},
            {"id": "one-letter",  "endedAt": None, "active": "a",   "targetApp": "ai"},
            {"id": "ended",       "endedAt": "2026-01-01", "active": "ab", "targetApp": "ai"},
            {"id": "aura",        "endedAt": None, "active": "ab",  "targetApp": None},
        ]
        live = {r["id"] for r in rows
                if r.get("id") and not r.get("endedAt")
                and len(str(r.get("active") or "")) > 1
                and r.get("targetApp") == build.AURIE_TARGET_APP}
        self.assertEqual(live, {"splitting"})


if __name__ == "__main__":
    unittest.main()
