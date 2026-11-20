"""The marks the loop leaves on an idea's thread, and how it reads them back.

Jerome, 2026-09-30: "for the ideas that were rejected can we add ❌ to the thread first
message as a reaction" — and a mark for the ones approved for implementation. The loop
puts them there itself when the row moves, and must then not mistake its own mark for
a person's signal: before this, a cross on a rejected thread would have come back
"somebody marked this abandoned — not a decision" on every tick, forever.
"""

from __future__ import annotations

import glob
import io
import os
import pathlib
import sys
import unittest
from types import SimpleNamespace

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CANONICAL = os.path.join(REPO, "plugins", "product", "skills", "manage-ideas", "scripts")
sys.path.insert(0, CANONICAL)
sys.path.insert(0, os.path.join(REPO, "plugins", "product", "skills", "harvest-idea-decisions", "scripts"))
sys.path.insert(0, os.path.join(REPO, "plugins", "product", "skills", "share-ideas", "scripts"))

import thread_marks as tm  # noqa: E402
import harvest_idea_decisions as h  # noqa: E402
import manage_ideas as m  # noqa: E402

LOOP = "C0C1JTK3YCC"
PERMALINK = "https://auramobile.slack.com/archives/C0C1JTK3YCC/p1790751812189799"


class CopiesAreIdentical(unittest.TestCase):
    def test_every_copy_matches_the_canonical(self):
        canonical = pathlib.Path(CANONICAL, "thread_marks.py").read_bytes()
        copies = glob.glob(os.path.join(REPO, "plugins", "**", "thread_marks.py"), recursive=True)
        self.assertGreaterEqual(len(copies), 2)
        for c in copies:
            self.assertEqual(pathlib.Path(c).read_bytes(), canonical, c)


class ThePermalinkNamesTheThread(unittest.TestCase):
    def test_parses(self):
        self.assertEqual(tm.thread_from_permalink(PERMALINK), ("C0C1JTK3YCC", "1790751812.189799"))

    def test_anything_else_is_none(self):
        for url in ("", None, "https://notion.so/x", "https://slack.com/archives/C1/p1"):
            self.assertIsNone(tm.thread_from_permalink(url), url)


class MarkThread(unittest.TestCase):
    def _stub(self, answers):
        calls = []

        def fake(method, token, **data):
            calls.append((method, data["name"]))
            return answers.get((method, data["name"]), {"ok": True})
        real = tm._call
        tm._call = fake
        self.addCleanup(setattr, tm, "_call", real)
        return calls

    def test_a_rejection_lands_a_cross_and_lifts_the_build_mark(self):
        calls = self._stub({})
        out = tm.mark_thread(PERMALINK, "rejected", token="t")
        self.assertIn(("reactions.add", "x"), calls)
        self.assertIn(("reactions.remove", "rocket"), calls)
        self.assertNotIn(("reactions.remove", "x"), calls)
        self.assertIn(":x: on", out)

    def test_an_approval_lands_the_build_mark_and_lifts_the_cross(self):
        calls = self._stub({})
        tm.mark_thread(PERMALINK, "approved", token="t")
        self.assertIn(("reactions.add", "rocket"), calls)
        self.assertIn(("reactions.remove", "x"), calls)

    def test_a_done_card_swaps_the_build_mark_for_a_check(self):
        calls = self._stub({})
        tm.mark_thread(PERMALINK, "done", token="t")
        self.assertIn(("reactions.add", "white_check_mark"), calls)
        self.assertIn(("reactions.remove", "rocket"), calls)

    def test_abandoned_is_a_cross_too(self):
        self.assertEqual(tm.MARKS["abandoned"], "x")

    def test_rerunning_changes_nothing_and_says_so(self):
        self._stub({("reactions.add", "x"): {"ok": False, "error": "already_reacted"},
                    ("reactions.remove", "rocket"): {"ok": False, "error": "no_reaction"},
                    ("reactions.remove", "white_check_mark"): {"ok": False, "error": "no_reaction"}})
        self.assertEqual(tm.mark_thread(PERMALINK, "rejected", token="t"), ":x: on")

    def test_a_missing_scope_is_reported_not_raised(self):
        self._stub({("reactions.add", "x"): {"ok": False, "error": "missing_scope"}})
        self.assertIn("not added (missing_scope)", tm.mark_thread(PERMALINK, "rejected", token="t"))

    def test_no_thread_no_token_no_mark(self):
        calls = self._stub({})
        self.assertIn("no Slack thread", tm.mark_thread("", "rejected", token="t"))
        real = os.environ.pop("SLACK_BOT_TOKEN", None)
        try:
            self.assertIn("SLACK_BOT_TOKEN", tm.mark_thread(PERMALINK, "rejected"))
        finally:
            if real is not None:
                os.environ["SLACK_BOT_TOKEN"] = real
        self.assertEqual(calls, [])

    def test_a_status_without_a_mark_does_nothing(self):
        calls = self._stub({})
        self.assertIn("no mark", tm.mark_thread(PERMALINK, "deferred", token="t"))
        self.assertEqual(calls, [])


def thread(reactions):
    return {"text": "*IDEA-29 · …*", "reactions": reactions, "replies": []}


class TheHarvesterReadsItsOwnMarksAsMarks(unittest.TestCase):
    def test_the_bots_cross_is_not_a_signal(self):
        out = h.classify(thread([{"name": "x", "users": [tm.LOOP_BOT_USER], "count": 1}]),
                         channel=LOOP)
        self.assertEqual(out["verdict"], "no_decision")

    def test_the_bots_build_mark_is_not_a_signal(self):
        out = h.classify(thread([{"name": "rocket", "users": [tm.LOOP_BOT_USER], "count": 1}]),
                         channel=LOOP)
        self.assertEqual(out["verdict"], "no_decision")

    def test_a_person_sharing_the_mark_still_counts(self):
        out = h.classify(thread([{"name": "x", "users": [tm.LOOP_BOT_USER, "U9"], "count": 2}]),
                         channel=LOOP)
        self.assertEqual(out["verdict"], "needs_judgment")
        self.assertIn("loop's own mark", out["evidence"])

    def test_a_persons_build_mark_is_the_wrong_signal_and_says_so(self):
        out = h.classify(thread([{"name": "rocket", "users": ["U9"], "count": 1}]),
                         channel=LOOP)
        self.assertEqual(out["verdict"], "needs_judgment")
        self.assertIn("approved for implementation", out["evidence"])

    def test_the_archive_strips_the_bot_too(self):
        out = h.classify(thread([{"name": "x", "users": [tm.LOOP_BOT_USER], "count": 1}]),
                         channel="C08KQGJ0V1B")
        self.assertEqual(out["verdict"], "no_decision")


class ManageIdeasMarksAfterTheWrite(unittest.TestCase):
    def test_reject_reports_the_thread_line(self):
        order = []
        real = (m._resolve_ref, m.notion, m.mark_thread)

        class Stub:
            available = True

            @staticmethod
            def update_page_props(page_id, props):
                order.append("notion")

        def fake_mark(permalink, status):
            order.append(("mark", permalink, status))
            return ":x: on"
        m._resolve_ref = lambda ds, ref: dict(page_id="p", fingerprint="abc", status="shared",
                                              title="t", status_note="", decided_by=[],
                                              decided_at=None, slack_thread=PERMALINK)
        m.notion, m.mark_thread = Stub(), fake_mark
        ns = SimpleNamespace(data_source_id="ds", ref="IDEA-29", reason="low impact", note=None,
                             by=None, by_id=None, decided_in=None, at=None)
        buf, real_out = io.StringIO(), sys.stdout
        try:
            sys.stdout = buf
            m._set_status(ns, "rejected")
        finally:
            sys.stdout = real_out
            m._resolve_ref, m.notion, m.mark_thread = real
        self.assertEqual(order, ["notion", ("mark", PERMALINK, "rejected")])
        self.assertIn("thread: :x: on", buf.getvalue())


if __name__ == "__main__":
    unittest.main()


class TheProtocolNamesTheMarks(unittest.TestCase):
    """The channel topic is the one place a reader learns what a mark means, and
    `DECISION_PROTOCOL` is its canonical wording."""

    def test_every_mark_is_named(self):
        import share_ideas  # noqa: PLC0415
        for emoji in (":rocket:", ":x:", ":white_check_mark:"):
            self.assertIn(emoji, share_ideas.DECISION_PROTOCOL, emoji)

    def test_the_marks_match_the_code(self):
        import share_ideas  # noqa: PLC0415
        for name in set(tm.MARKS.values()):
            self.assertIn(f":{name}:", share_ideas.DECISION_PROTOCOL, name)

    def test_it_fits_in_a_slack_topic(self):
        """It is not documentation about the topic, it is the topic — and Slack rejects
        one over 250 characters, so a protocol that does not fit cannot be posted."""
        import share_ideas  # noqa: PLC0415
        self.assertLessEqual(len(share_ideas.DECISION_PROTOCOL), share_ideas.TOPIC_MAX)
