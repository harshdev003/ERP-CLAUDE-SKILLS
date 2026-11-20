"""`build-idea` hold / unhold, and `ready` requesting no reviewer.

`gh` and Linear are stubbed at their one chokepoint, `subprocess.run`.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from unittest import mock

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "plugins", "product", "skills", "build-idea", "scripts"))

import build_idea as b  # noqa: E402

PR = "https://github.com/AuraHealth/AuraConsumerApp/pull/1776"


def done(stdout="", code=0, stderr=""):
    return subprocess.CompletedProcess([], code, stdout=stdout, stderr=stderr)


def idea(**kw):
    base = {"page_id": "p", "idea_id": "IDEA-9", "fingerprint": "f", "title": "t",
            "status": "approved", "note": "", "linear": "", "slack": "", "experiment": "",
            "screen": "welcome", "domain": "onboarding"}
    base.update(kw)
    return base


class ReadyRequestsNoReviewer(unittest.TestCase):
    def test_ready_has_no_reviewer_request(self):
        self.assertFalse(hasattr(b, "request_codeowner_review"))


class ArchitectureHold(unittest.TestCase):
    """A build that needs an architecture decision is held before any code, and nothing
    downstream can start it until the decision is recorded."""

    def setUp(self):
        self.notion = mock.patch.object(b, "notion").start()
        self.notion.available = True
        self.addCleanup(mock.patch.stopall)

    def _args(self, **kw):
        import argparse
        return argparse.Namespace(data_source_id="ds", ref="IDEA-9", post=False, **kw)

    def test_hold_writes_the_marker_and_keeps_the_prior_note(self):
        with mock.patch.object(b, "find_idea", return_value=idea(note="Steve: go")):
            self.assertEqual(b.cmd_hold(self._args(decision="Pre-consent. Options: 1, 2")), 0)
        props = self.notion.update_page_props.call_args.args[1]
        text = props["Quick status note"]["rich_text"][0]["text"]["content"]
        self.assertTrue(text.startswith(b.ARCH_HOLD))
        self.assertIn("Steve: go", text)

    def test_re_hold_replaces_the_questions_and_keeps_the_history(self):
        held = idea(note=f"{b.ARCH_HOLD} — old questions | Steve: go")
        with mock.patch.object(b, "find_idea", return_value=held):
            self.assertEqual(b.cmd_hold(self._args(decision="new questions")), 0)
        text = self.notion.update_page_props.call_args.args[1]["Quick status note"]["rich_text"][0]["text"]["content"]
        self.assertEqual(text, f"{b.ARCH_HOLD} — new questions | Steve: go")

    def test_hold_refuses_a_row_that_is_not_approved(self):
        with mock.patch.object(b, "find_idea", return_value=idea(status="implementing")):
            self.assertEqual(b.cmd_hold(self._args(decision="x")), 2)
        self.notion.update_page_props.assert_not_called()

    def test_start_and_plan_refuse_a_held_row(self):
        held = idea(note=f"{b.ARCH_HOLD} — pre-consent")
        with mock.patch.object(b, "find_idea", return_value=held):
            self.assertEqual(b.cmd_start(self._args(linear="https://linear.app/x/issue/ENGMOB-1",
                                                    experiment=None)), 2)
            self.assertEqual(b.cmd_plan(self._args(experiment=None)), 2)
        self.notion.update_page_props.assert_not_called()

    def test_unhold_records_the_decision_and_releases(self):
        held = idea(note=f"{b.ARCH_HOLD} — pre-consent | Steve: go")
        with mock.patch.object(b, "find_idea", return_value=held):
            self.assertEqual(b.cmd_unhold(self._args(decision="Option 2 — Jerome")), 0)
        text = self.notion.update_page_props.call_args.args[1]["Quick status note"]["rich_text"][0]["text"]["content"]
        self.assertFalse(b.is_held(text))
        self.assertTrue(text.startswith("Architecture decision: Option 2"))
        self.assertIn("Steve: go", text)

    def test_unhold_refuses_a_row_that_is_not_held(self):
        with mock.patch.object(b, "find_idea", return_value=idea()):
            self.assertEqual(b.cmd_unhold(self._args(decision="x")), 2)



class CardCommentEncoding(unittest.TestCase):
    def test_non_ascii_decision_reaches_linear_as_utf8_and_the_file_is_removed(self):
        seen = {}

        def fake_linear(*argv):
            if argv[0] == "get-issue":
                return {"id": "uuid"}
            path = argv[argv.index("--body-file") + 1]
            with open(path, encoding="utf-8") as fh:
                seen["body"], seen["path"] = fh.read(), path
            return {"ok": True}

        # The process default encoding is set below Python (cp1252 on Windows, ascii on a
        # bare C locale), so it cannot be forced here — assert the file is opened UTF-8.
        real = b.tempfile.NamedTemporaryFile
        opened = {}

        def spy(*args, **kw):
            opened.update(kw)
            return real(*args, **kw)

        with mock.patch.object(b, "_linear", side_effect=fake_linear), \
                mock.patch.object(b.tempfile, "NamedTemporaryFile", side_effect=spy):
            b._comment_on_card("https://linear.app/a/issue/ENGMOB-1/x", "Option 2 — Jerome → go")
        self.assertEqual(opened.get("encoding"), "utf-8")
        self.assertEqual(seen["body"], "Option 2 — Jerome → go")
        self.assertFalse(os.path.exists(seen["path"]))


if __name__ == "__main__":
    unittest.main()
