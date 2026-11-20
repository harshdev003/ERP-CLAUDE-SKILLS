#!/usr/bin/env python3
"""Group 6 (#guardrails-conversation): alert parsing and the pre-post thread re-check.

Two defects BugBot found on #1239, both silent:

* **Truncated ids.** The sweep parsed `*User:*` with `[A-Za-z0-9]+`, so an id
  containing `:`, `-`, `_` or `.` (a provider-prefixed `google:…` uid) came back as
  its first fragment. The export and `/users/:id` then ran against the wrong
  account — a real crisis conversation could read "not analysable", or the draft
  could carry someone else's email. Every field now runs to whitespace.
* **Double posting.** Two overlapping ticks both saw `reply_count == 0` and both
  posted a draft, a page and the account email. `thread_state.py` is the pre-post
  re-check; it must say "answered" when the analysis bot has replied and must never
  turn an unreadable thread into "unanswered".

A human reply never marks an alert handled: on 2026-10-17 a reviewer asked on a
memory-ingestion alert why nobody had followed up, and the `reply_count == 0` sweep
then skipped that alert for good. `SweepDedup` covers both guardrail sweeps.

The parsing cases are built from the producer's own format string
(Intelligence Service `handoffToGuardrails.postGuardrailSlackMessage`), not a
paraphrase of it. Stdlib unittest, no network.
"""
from __future__ import annotations

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from reader_harness import REPO_ROOT, load_reader  # noqa: E402

SWEEP = REPO_ROOT / "plugins/data/skills/run-conversation-guardrails-analysis-cadence/scripts/sweep_unanalyzed.py"
THREAD = REPO_ROOT / "plugins/eng/skills/investigate-conversation-guardrail/scripts/thread_state.py"


def alert(user, tx, conv):
    # Mirrors the producer's template line for line.
    return (
        "*Conversation guardrail triggered*\n"
        f"*User:* {user}\n"
        f"*Transaction:* {tx}\n"
        f"*Conversation:* {conv}\n"
        "_First guardrail trigger in this conversation. Later triggers are "
        "not posted, so review the whole conversation._"
    )


class AlertParsing(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_reader(SWEEP, siblings=())

    def parse(self, text):
        m = self.mod
        return tuple(r.search(text).group(1) for r in (m.UID_RE, m.TX_RE, m.CONV_RE))

    def test_full_values_with_punctuation_survive(self):
        cases = [
            ("tyGqRZUBdAQ9kvZK096ySEcbeba2", "4D921891-35E3-4262-9A08-D3A4DB532D0B",
             "FE3502B4-8EEE-4154-92EE-F1325C32F411"),
            ("google:112233445566778899", "tx_with_underscores.v2", "conv-id_with.dots:1"),
            ("apple:001234.abcd-ef_gh", "a:b-c_d.e", "lower-case-uuid-0000"),
        ]
        for user, tx, conv in cases:
            with self.subTest(user=user):
                self.assertEqual(self.parse(alert(user, tx, conv)), (user, tx, conv))

    def test_value_stops_at_end_of_line(self):
        user, tx, conv = self.parse(alert("uid-1", "tx-1", "conv-1"))
        self.assertNotIn("\n", user + tx + conv)
        self.assertEqual(conv, "conv-1")

    def test_marker_matches_the_producer(self):
        self.assertIn(self.mod.ALERT_MARKER, alert("u", "t", "c"))


BOT = "UBOT"
MEMORY_SWEEP = REPO_ROOT / "plugins/data/skills/run-guardrails-analysis-cadence/scripts/sweep_unanalyzed.py"


class ThreadRecheck(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_reader(THREAD, siblings=())

    def state(self, data):
        return self.mod.thread_state("C1", "100.1", "tok", BOT, get=lambda *a: data)

    def test_unanswered(self):
        s = self.state({"ok": True, "messages": [{"ts": "100.1", "reply_count": 0}]})
        self.assertFalse(s["has_reply"])

    def test_bot_reply_on_parent(self):
        s = self.state({"ok": True, "messages": [{"ts": "100.1", "reply_count": 1, "reply_users": [BOT]}]})
        self.assertTrue(s["has_reply"])

    def test_bot_reply_present_even_when_count_lags(self):
        s = self.state({"ok": True, "messages": [{"ts": "100.1"}, {"ts": "100.2", "thread_ts": "100.1", "user": BOT}]})
        self.assertTrue(s["has_reply"])

    def test_human_reply_does_not_hide_the_alert(self):
        # A reviewer asking "why has nobody followed up?" left the alert looking handled.
        s = self.state({"ok": True, "messages": [
            {"ts": "100.1", "reply_count": 2, "reply_users": ["UHUMAN1", "UHUMAN2"]},
            {"ts": "100.2", "user": "UHUMAN1"}, {"ts": "100.3", "user": "UHUMAN2"}]})
        self.assertFalse(s["has_reply"])
        self.assertEqual(s["reply_count"], 2)

    def test_unreadable_thread_raises_rather_than_reading_as_unanswered(self):
        with self.assertRaises(RuntimeError):
            self.state({"ok": False, "error": "missing_scope"})

    def test_unresolvable_bot_raises(self):
        with self.assertRaises(RuntimeError):
            self.mod.bot_id("tok", get=lambda *a: {"ok": False, "error": "invalid_auth"})


class SweepDedup(unittest.TestCase):
    """Both guardrail sweeps: only the analysis bot's reply marks an alert handled."""

    def each(self):
        for path in (SWEEP, MEMORY_SWEEP):
            yield path.parent.parent.name, load_reader(path, siblings=())

    def test_no_replies_is_unhandled(self):
        for name, mod in self.each():
            with self.subTest(name):
                self.assertFalse(mod._bot_replied({"ts": "1.1"}, "C1", BOT, "tok"))

    def test_bot_in_reply_users_is_handled(self):
        for name, mod in self.each():
            with self.subTest(name):
                m = {"ts": "1.1", "reply_count": 3, "reply_users": ["UHUMAN", BOT]}
                self.assertTrue(mod._bot_replied(m, "C1", BOT, "tok"))

    def test_human_only_replies_stay_unhandled(self):
        for name, mod in self.each():
            with self.subTest(name):
                m = {"ts": "1.1", "reply_count": 2, "reply_users": ["UHUMAN1", "UHUMAN2"]}
                self.assertFalse(mod._bot_replied(m, "C1", BOT, "tok"))

    def test_missing_reply_users_reads_the_thread(self):
        for name, mod in self.each():
            with self.subTest(name):
                calls = []
                def fake(method, params, token, calls=calls):
                    calls.append(method)
                    return {"ok": True, "messages": [{"ts": "1.1"}, {"ts": "1.2", "user": "UHUMAN"}]}
                mod._slack_get = fake
                self.assertFalse(mod._bot_replied({"ts": "1.1", "reply_count": 1}, "C1", BOT, "tok"))
                self.assertEqual(calls, ["conversations.replies"])

    def test_truncated_reply_users_reads_the_thread(self):
        # Slack lists at most five repliers; the bot can be the sixth.
        for name, mod in self.each():
            with self.subTest(name):
                calls = []
                def fake(method, params, token, calls=calls):
                    calls.append(method)
                    return {"ok": True, "messages": [{"ts": "1.1"}, {"ts": "1.7", "user": BOT}]}
                mod._slack_get = fake
                m = {"ts": "1.1", "reply_count": 6, "reply_users_count": 6,
                     "reply_users": ["U1", "U2", "U3", "U4", "U5"]}
                self.assertTrue(mod._bot_replied(m, "C1", BOT, "tok"))
                self.assertEqual(calls, ["conversations.replies"])

    def test_unresolvable_bot_raises(self):
        for name, mod in self.each():
            with self.subTest(name):
                mod._slack_get = lambda *a: {"ok": False, "error": "invalid_auth"}
                with self.assertRaises(RuntimeError):
                    mod._bot_id("tok")


if __name__ == "__main__":
    unittest.main()
