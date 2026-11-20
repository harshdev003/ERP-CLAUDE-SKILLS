"""`shared:check-followups` reads what `shared:raise-followup` posts and checks it live.

The posts are made by raise-followup's real `render`, so a format change on the producer side
fails here (CLAUDE.md, "Verify the path production takes"). Slack, gh and Linear are stubbed.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import pathlib
import subprocess
import sys
import unittest
from unittest import mock

PLUGINS = pathlib.Path(__file__).resolve().parents[1] / "plugins/shared/skills"


def load(name: str, path: pathlib.Path):
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


fu = load("followup_for_checker", PLUGINS / "raise-followup/scripts/followup.py")
ck = load("checker", PLUGINS / "check-followups/scripts/checker.py")
CFG = fu.load_config()
REAL_CARD_STATE = ck.card_state

PR_READY = {"kind": "pr-ready", "title": "AINative#1600: a skill", "what": "Adds a skill.", "why": "It was missing.",
            "ask": "Review and merge.", "pr": "https://github.com/AuraHealth/AINative/pull/1600",
            "card": "ENG-1", "card_url": "https://linear.app/aura-health/issue/ENG-1",
            "key": "ainative-pr-1600", "session": "AINative"}
GO = {"kind": "go-needed", "title": "Release IntelligenceService 2.3.1", "what": "Ships the cache fix.",
      "why": "Misses cost money.", "ask": "Give the release go.", "go_line": "go: release IntelligenceService 2.3.1",
      "go_session": "Backend", "go_type": "release", "key": "is-release-2.3.1", "session": "Backend"}
LIVE_OK = {"state": "OPEN", "draft": False, "merge": "CLEAN", "head": "abcd1234", "ci": "green",
           "bugbot": "completed success", "open_threads": 0}


def post(fields, ts="100.1", **extra):
    return {"ts": ts, "text": fu.render(fields, CFG), **extra}


class ParsesTheProducersPost(unittest.TestCase):
    def test_pr_ready(self):
        p = ck.parse(fu.render(PR_READY, CFG))
        self.assertEqual((p["key"], p["kind"], p["from"], p["role"]), ("ainative-pr-1600", "pr-ready", "AINative", "reviewer"))
        self.assertEqual((p["pr"]["repo"], p["pr"]["number"], p["card"]), ("AuraHealth/AINative", 1600, "ENG-1"))
        self.assertEqual(p["title"], "AINative#1600: a skill")

    def test_go_needed(self):
        p = ck.parse(fu.render(GO, CFG))
        self.assertEqual((p["role"], p["go_type"], p["pr"], p["card"]), ("approver", "release", None, None))

    def test_other_messages_are_ignored(self):
        self.assertIsNone(ck.parse("A message from Lead about something else."))


class ScanBase(unittest.TestCase):
    def setUp(self):
        self.msgs, self.replies = [], {}
        for p in (mock.patch.object(ck.slack_read, "history", lambda *_a, **_k: list(self.msgs)),
                  mock.patch.object(ck.slack_read, "replies", lambda _c, ts, **_k: self.replies.get(ts, [])),
                  mock.patch.object(ck, "pr_state", lambda *_a: dict(self.live)),
                  mock.patch.object(ck, "card_state", lambda _c: self.card)):
            p.start()
            self.addCleanup(p.stop)
        self.live, self.card = dict(LIVE_OK), "In Review (started)"

    def scan(self):
        return [ck.check(i) for i in ck.followups("C", 14)]


class Scan(ScanBase):
    def test_a_ready_pr_is_open_with_no_problems(self):
        self.msgs = [post(PR_READY)]
        [i] = self.scan()
        self.assertEqual((i["verdict"], i["problems"]), ("open", []))

    def test_a_not_ready_pr_says_why(self):
        self.msgs = [post(PR_READY)]
        self.live.update(draft=True, bugbot="not run on head", open_threads=2)
        [i] = self.scan()
        self.assertEqual(i["verdict"], "open")
        self.assertEqual(i["problems"], ["still a draft", "BugBot not run on head", "2 open review threads"])

    def test_merged_pr_or_done_card_is_stale(self):
        self.msgs = [post(PR_READY)]
        self.live["state"] = "MERGED"
        self.assertEqual(self.scan()[0]["verdict"], "stale")
        self.live["state"], self.card = "OPEN", "Done (completed)"
        self.assertEqual(self.scan()[0]["verdict"], "stale")

    def test_a_failed_live_check_is_unverified_not_open(self):
        self.msgs = [post(PR_READY)]
        with mock.patch.object(ck, "pr_state", side_effect=RuntimeError("gh pr view failed")):
            [i] = self.scan()
        self.assertEqual(i["verdict"], "unverified")

    def test_handled_posts_are_skipped_and_an_older_post_for_the_key_is_superseded(self):
        self.msgs = [post(PR_READY, ts="200.1", reactions=[{"name": "white_check_mark"}]), post(PR_READY, ts="100.1")]
        self.assertEqual(self.scan(), [])

    def test_the_newest_thread_reply_wins(self):
        newer = dict(GO, risk="Medium.")
        self.msgs = [post(GO, reply_count=1)]
        self.replies["100.1"] = [self.msgs[0], {"ts": "100.2", "text": fu.render(newer, CFG)}]
        [i] = self.scan()
        self.assertEqual(i["version"], fu.version_of(fu.render(newer, CFG)))

    def test_a_go_is_reported_pending_and_nothing_is_written(self):
        self.msgs = [post(GO)]
        with mock.patch.object(ck.slack_post, "_api", side_effect=AssertionError("scan wrote to Slack")):
            [i] = self.scan()
        self.assertEqual(i["verdict"], "open")
        self.assertIn("go is pending", i["why"])
        self.assertIn("never taken from Slack", i["why"])


class LinearFailuresAreUnverified(ScanBase):
    def test_a_card_only_item_is_unverified_when_linear_fails(self):
        self.msgs = [post(dict(GO, card="ENG-1", card_url="https://linear.app/aura-health/issue/ENG-1"))]
        with mock.patch.object(ck, "card_state", side_effect=RuntimeError("Linear HTTP 500")):
            [i] = self.scan()
        self.assertEqual(i["verdict"], "unverified")

    def test_card_state_raises_without_a_key(self):
        with mock.patch.dict(os.environ, {"LINEAR_API_KEY": ""}):
            with self.assertRaises(RuntimeError):
                REAL_CARD_STATE("ENG-1")


class GhPagination(unittest.TestCase):
    """--paginate --jq prints one result per page; the parse must survive a second page."""

    def fake_run(self, cmd, **kw):
        self.assertEqual(kw.get("encoding"), "utf-8")
        args = " ".join(cmd)
        if "pr view" in args:
            out = json.dumps({"state": "OPEN", "isDraft": False, "mergeStateStatus": "CLEAN", "headRefOid": "a" * 40})
        elif "check-runs" in args:
            page = [json.dumps({"name": f"check {i}", "status": "completed", "conclusion": "success"}) for i in range(30)]
            out = "\n".join(page + [json.dumps({"name": "Cursor Bugbot", "status": "completed", "conclusion": "success"}),
                                    json.dumps({"name": "test ✓", "status": "completed", "conclusion": "failure"})])
        else:  # review threads: one count per page
            out = "0\n2\n"
        return subprocess.CompletedProcess(cmd, 0, out, "")

    def test_counts_cross_pages(self):
        with mock.patch.object(ck.subprocess, "run", self.fake_run):
            p = ck.pr_state("AuraHealth/AINative", 1)
        self.assertEqual((p["ci"], p["bugbot"], p["open_threads"]), ("failing", "completed success", 2))


class MarkHandled(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.msgs = [post(PR_READY)]
        for p in (mock.patch.object(ck.slack_read, "history", lambda *_a, **_k: list(self.msgs)),
                  mock.patch.object(ck.slack_post, "_api", self.api),
                  mock.patch.object(ck.slack_post, "_token", lambda _t: "xoxb-test")):
            p.start()
            self.addCleanup(p.stop)

    def api(self, method, *, token, body=None, **_k):
        self.calls.append((method, body))
        return {"ok": True, "ts": "100.9"}

    def test_reacts_and_replies_with_the_role_and_card(self):
        out = ck.mark_handled("C", "ainative-pr-1600", "monitor", "ENG-1", 14)
        self.assertEqual(out["action"], "handled")
        self.assertEqual([c[0] for c in self.calls], ["reactions.add", "chat.postMessage"])
        reply = self.calls[1][1]
        self.assertEqual(reply["thread_ts"], "100.1")
        self.assertIn("Handled by the monitor", reply["text"])
        self.assertIn("ENG-1", reply["text"])

    def test_an_already_handled_post_is_left_alone(self):
        self.msgs[0]["reactions"] = [{"name": "white_check_mark"}]
        self.assertEqual(ck.mark_handled("C", "ainative-pr-1600", "monitor", None, 14)["action"], "already-handled")
        self.assertEqual(self.calls, [])

    def test_the_cli_prints_json_when_slack_is_unusable(self):
        def no_token(_t):
            raise SystemExit("SLACK_BOT_TOKEN is not set")
        buf = io.StringIO()
        with mock.patch.object(ck.slack_post, "_token", no_token), \
                mock.patch.object(sys, "argv", ["checker.py", "mark-handled", "--key", "ainative-pr-1600", "--by", "monitor"]), \
                contextlib.redirect_stdout(buf), self.assertRaises(SystemExit) as code:
            ck.main()
        self.assertEqual(code.exception.code, 2)
        self.assertFalse(json.loads(buf.getvalue())["ok"])

    def test_an_unknown_key_writes_nothing(self):
        self.assertFalse(ck.mark_handled("C", "no-such-key", "monitor", None, 14)["ok"])
        self.assertEqual(self.calls, [])


class Window(unittest.TestCase):
    def test_reads_as_far_back_as_raise_followup_dedupes(self):
        src = (PLUGINS / "check-followups/scripts/checker.py").read_text()
        self.assertIn(f'default={CFG["defaults"]["dedupe_window_days"]})', src)


if __name__ == "__main__":
    unittest.main()
