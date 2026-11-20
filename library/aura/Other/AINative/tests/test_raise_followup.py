"""`shared:raise-followup`: one role-neutral post per dedupe key, cards by kind, refusals write nothing.

Messages are never edited: a change is a reply in the post's thread (tests/test_slack_never_edits.py).

Slack is stubbed at `slack_post._api` (post, update, permalink) and `slack_read.history`; Linear at
`followup.linear`. The fake channel keeps what was posted, so a second run sees the first one.
"""

from __future__ import annotations

import importlib
import os
import pathlib
import sys
import unittest
from unittest import mock

SCRIPTS = pathlib.Path(__file__).resolve().parents[1] / "plugins/shared/skills/raise-followup/scripts"
sys.path.insert(0, str(SCRIPTS))
fu = importlib.import_module("followup")

CFG = fu.load_config()

GO = {"kind": "go-needed", "title": "Release IntelligenceService 2.3.1", "what": "Ships the cache fix to prod.",
      "why": "Cache misses cost about $40 a day.", "ask": "Give the release go.",
      "go_line": "go: release IntelligenceService 2.3.1", "go_session": "Backend", "go_type": "release",
      "rollback": "Redeploy 2.3.0.", "pr": "https://github.com/AuraHealth/IntelligenceService/pull/1",
      "key": "is-release-2.3.1", "session": "Backend"}


class FakeSlack:
    def __init__(self):
        self.messages: list[dict] = []
        self.replies: dict[str, list[dict]] = {}
        self.calls: list[str] = []
        self.fail: str | None = None

    def api(self, method, *, token, body=None, params=None, http="POST"):
        self.calls.append(method)
        if method == self.fail:
            return {"ok": False, "error": "channel_not_found"}
        if method == "chat.postMessage":
            ts = f"{1000 + len(self.messages) + len(self.replies)}.0001"
            if body.get("thread_ts"):
                parent = next(m for m in self.messages if m["ts"] == body["thread_ts"])
                parent["reply_count"] = parent.get("reply_count", 0) + 1
                self.replies.setdefault(body["thread_ts"], []).append({"ts": ts, "text": body["text"]})
            else:
                self.messages.insert(0, {"ts": ts, "text": body["text"]})
            return {"ok": True, "ts": ts}
        if method == "chat.getPermalink":
            return {"ok": True, "permalink": f"https://aura.slack.com/archives/C/p{params['message_ts']}"}
        raise AssertionError(method)

    def history(self, channel, **_kw):
        return list(self.messages)

    def thread(self, channel, ts, **_kw):
        parent = next(m for m in self.messages if m["ts"] == ts)
        return [parent] + self.replies.get(ts, [])


class FakeLinear:
    def __init__(self):
        self.cards: list[dict] = []
        self.comments: list[tuple[str, str]] = []
        self.fail = False

    def __call__(self, query, variables=None):
        if self.fail:
            raise RuntimeError("Linear HTTP 500")
        v = variables or {}
        if "issues(first" in query:
            hits = [c for c in self.cards if v["m"] in c["description"]]  # a prefix match, like Linear
            start = int(v.get("after") or 0)
            page = hits[start:start + 50]
            return {"issues": {"nodes": page, "pageInfo": {"hasNextPage": start + 50 < len(hits),
                                                          "endCursor": str(start + 50)}}}
        if "teams(" in query:
            return {"teams": {"nodes": [{"id": "team-eng"}]}}
        if "issueCreate" in query:
            card = {"id": f"id{len(self.cards)}", "identifier": f"ENG-{9000 + len(self.cards)}",
                    "url": "https://linear.app/x", "description": v["i"]["description"]}
            self.cards.append(card)
            return {"issueCreate": {"issue": card}}
        if "commentCreate" in query:
            self.comments.append((v["i"], v["b"]))
            return {"commentCreate": {"success": True}}
        if "issue(id" in query:
            return {"issue": {"id": "id-ref", "identifier": v["id"], "url": "https://linear.app/x"}}
        raise AssertionError(query)


class Base(unittest.TestCase):
    def setUp(self):
        self.slack, self.lin = FakeSlack(), FakeLinear()
        for p in (mock.patch.object(fu.slack_post, "_api", self.slack.api),
                  mock.patch.object(fu.slack_read, "history", self.slack.history),
                  mock.patch.object(fu.slack_read, "replies", self.slack.thread),
                  mock.patch.object(fu, "linear", self.lin),
                  mock.patch.dict(os.environ, {"SLACK_BOT_TOKEN": "xoxb-test"})):
            p.start()
            self.addCleanup(p.stop)

    def run_(self, **over):
        return fu.raise_followup({**GO, **over}, CFG)


class Refusals(Base):
    def test_a_refused_post_writes_nothing(self):
        cases = {"names a person": {"ask": "Jerome, give the go."},
                 "context": {"why": "As discussed yesterday."},
                 "email": {"what": "Mail sent to someone@example.com."},
                 "token": {"what": "Key xoxb-12345-abcdef leaked into the log."},
                 "IP address": {"what": "Probe from 203.0.113.7."},
                 "full id": {"what": "User Ab3dEf6hIj9kLm2nOp5qRs8tUv1w hit it."},
                 "go_line is required": {"go_line": ""},
                 "go_type must be": {"go_type": "whatever"},
                 "for must be a role": {"for": "Jerome"},
                 "go together": {"kind": "decision", "go_type": ""}}
        for want, over in cases.items():
            with self.subTest(want):
                code, out = self.run_(**over)
                self.assertEqual(code, 1)
                self.assertTrue(any(want in p for p in out["problems"]), out["problems"])
        self.assertEqual(self.slack.calls, [])
        self.assertEqual(self.lin.cards, [])

    def test_ids_inside_links_are_allowed(self):
        code, _ = self.run_(what="Shipped in https://github.com/AuraHealth/AINative/commit/" + "a1" * 20 + ".")
        self.assertEqual(code, 0)

    def test_the_post_names_roles_not_people(self):
        _code, out = fu.raise_followup(GO, CFG, dry_run=True)
        self.assertIn("a go from the approver (release), typed in the Backend session", out["text"])
        for person in fu.people(CFG):
            self.assertNotIn(person, out["text"])
        self.assertNotIn("@", out["text"].split("_Generated by")[1])  # no runner in the footer


class OnePostPerKey(Base):
    def test_first_run_posts_and_files_a_card(self):
        code, out = self.run_()
        self.assertEqual((code, out["action"], out["card_action"]), (0, "posted", "filed"))
        self.assertIn("followup-key: is-release-2.3.1", self.lin.cards[0]["description"])
        self.assertTrue(out["permalink"].startswith("https://"))

    def test_same_text_again_does_nothing(self):
        self.run_()
        calls = len(self.slack.calls)
        code, out = self.run_()
        self.assertEqual((code, out["action"]), (0, "unchanged"))
        self.assertEqual(self.slack.calls[calls:], ["chat.getPermalink"])
        self.assertEqual((len(self.lin.cards), self.lin.comments, out["card"]), (1, [], "ENG-9000"))

    def test_changed_text_is_a_thread_reply_and_a_comment_on_the_same_card(self):
        self.run_()
        code, out = self.run_(risk="Medium: touches the cache key.")
        self.assertEqual((code, out["action"], out["card_action"]), (0, "replied", "commented"))
        self.assertEqual(len(self.slack.messages), 1)
        self.assertNotIn("Medium", self.slack.messages[0]["text"])  # the post itself is never edited
        self.assertIn("Medium", self.slack.replies[self.slack.messages[0]["ts"]][0]["text"])
        self.assertEqual(len(self.lin.cards), 1)
        # The reply is now the latest text: the same change again does nothing.
        code, out = self.run_(risk="Medium: touches the cache key.")
        self.assertEqual(out["action"], "unchanged")
        self.assertEqual(len(self.slack.replies[self.slack.messages[0]["ts"]]), 1)

    def test_a_handled_post_gets_a_new_post_when_it_changes(self):
        self.run_()
        self.slack.messages[0]["reactions"] = [{"name": "white_check_mark", "count": 1}]
        _code, out = self.run_(risk="Medium.")
        self.assertEqual(out["action"], "reposted")
        self.assertEqual(len(self.slack.messages), 2)

    def test_fyi_gets_no_card(self):
        code, out = self.run_(kind="fyi", go_line="", go_session="", go_type="", why="", ask="")
        self.assertEqual((code, out["card_action"]), (0, "none"))
        self.assertEqual(self.lin.cards, [])

    def test_pr_ready_comments_only_on_the_named_card(self):
        _code, out = self.run_(kind="pr-ready", go_line="", go_session="", go_type="", card="ENG-1896",
                               card_url="https://linear.app/aura-health/issue/ENG-1896")
        self.assertEqual((out["card"], out["card_action"]), ("ENG-1896", "commented"))
        self.assertEqual(self.lin.cards, [])


class CardKeyIsExact(Base):
    def test_a_shorter_key_does_not_reuse_a_longer_keys_card(self):
        self.run_(key="is-release-2.3.1")
        _code, out = self.run_(key="is-release-2.3", title="Release IntelligenceService 2.3")
        self.assertEqual((out["card_action"], len(self.lin.cards)), ("filed", 2))
        self.assertEqual(self.lin.comments, [])


    def test_the_exact_card_is_found_past_the_first_page(self):
        self.run_()
        exact = self.lin.cards.pop()
        self.lin.cards += [dict(exact, id=f"x{i}", identifier=f"ENG-{8000 + i}",
                                description=exact["description"].replace("is-release-2.3.1", f"is-release-2.3.1{i}"))
                           for i in range(60)] + [exact]
        _code, out = self.run_(risk="Medium.")
        self.assertEqual((out["card"], out["card_action"]), (exact["identifier"], "commented"))


class Failures(Base):
    def test_slack_failure_touches_no_card(self):
        self.slack.fail = "chat.postMessage"
        code, out = self.run_()
        self.assertEqual((code, out["action"]), (2, "failed"))
        self.assertEqual(self.lin.cards, [])

    def test_linear_failure_after_the_post_reports_the_permalink_and_a_rerun_recovers(self):
        self.lin.fail = True
        code, out = self.run_()
        self.assertEqual((code, out["action"], out["card_action"]), (2, "posted", "failed"))
        self.assertTrue(out["permalink"])
        self.lin.fail = False
        code, out = self.run_()
        self.assertEqual((code, out["action"], out["card_action"]), (0, "unchanged", "filed"))
        self.assertEqual((len(self.slack.messages), len(self.lin.cards), self.lin.comments), (1, 1, []))


class Config(unittest.TestCase):
    def test_shipped_config_is_valid(self):
        self.assertEqual(fu.check_config(CFG), [])

    def test_channel_default_is_claude_monitoring_and_fyi_has_no_card(self):
        self.assertEqual(CFG["defaults"]["channel_name"], "#claude-monitoring")
        self.assertEqual(CFG["kinds"]["fyi"]["card"], "none")


if __name__ == "__main__":
    unittest.main()
