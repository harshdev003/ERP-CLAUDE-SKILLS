#!/usr/bin/env python3
"""`reply-to-aura-ai-mentions` — which threads get an unattended reply as Aura AI.

The script auto-posts, so its classifier is the only thing between a teammate's message
and a bot reply. Both directions fail quietly. A false "pending" posts into a thread where
nobody asked: a reply to a message tagging @Viktor, a "you're welcome" to a "thanks", or a
reply to another bot that answers back forever. A false "skip" leaves a question
unanswered, and an unanswered question looks the same as one nobody asked.

The fixture is the real thread that motivated the skill (#ai-feedback, 2026-09-24, bodies
redacted). It holds each shape once: an Aura AI card, a human tagging another bot, that
bot's replies, a human tagging Aura AI, and a later human tagging two people. The
follow-up cases extend that real thread instead of building one from scratch. The run
wiring is exercised through stubbed `search_messages` / `conversations.info` /
`conversations.replies`, including the conversations Aura AI stays out of: DMs, private
channels and public channels it has not joined. The channel flags in those stubs are the
shape a real `search.messages` match and a bot-token `conversations.info` return.
"""
from __future__ import annotations

import copy
import importlib.util
import io
import json
import pathlib
import sys
import unittest
from contextlib import redirect_stdout
from unittest import mock

REPO = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = REPO / "plugins/shared/skills/reply-to-aura-ai-mentions/scripts/find_pending.py"
FIXTURE = REPO / "tests/fixtures/aura_ai_mention_thread.json"

spec = importlib.util.spec_from_file_location("find_pending", SCRIPT)
fp = importlib.util.module_from_spec(spec)
sys.path.insert(0, str(SCRIPT.parent))
spec.loader.exec_module(fp)

STEVE = "U1BCYETM2"
VIKTOR_USER, VIKTOR_BOT = "U0AV303B4VB", "B0B05979QU9"
CC_TS = "1790294465.342649"
DAY_START = 1790233200.0  # 2026-09-24 00:00 America/Phoenix
# conversations.info (bot token) for a public channel Aura AI has joined.
PUBLIC_MEMBER = {"is_channel": True, "is_im": False, "is_mpim": False, "is_private": False, "is_member": True}


def thread():
    return copy.deepcopy(json.loads(FIXTURE.read_text()))


def human(ts, text, user=STEVE, **extra):
    return {"type": "message", "user": user, "ts": ts, "text": text, **extra}


def aura(ts, text="answer"):
    return {"type": "message", "user": fp.AURA_AI_USER, "bot_id": fp.AURA_AI_BOT, "ts": ts, "text": text}


def viktor(ts, text="bot answer"):
    return {"type": "message", "user": VIKTOR_USER, "bot_id": VIKTOR_BOT, "ts": ts, "text": text}


def classify(msgs, since=DAY_START, cap=5):
    return fp.classify(msgs, since, cap)


class RealThread(unittest.TestCase):
    def test_the_cc_is_what_is_waiting(self):
        bucket, awaiting = classify(thread())
        self.assertEqual(bucket, "pending")
        # The later "@Rohan @Naveed can you create a card" is for them, not for Aura AI.
        self.assertEqual([m["ts"] for m in awaiting], [CC_TS])

    def test_the_other_bot_answering_does_not_close_it(self):
        # Viktor replied after the cc. Only an Aura AI message counts as handled.
        msgs = thread() + [viktor("1790297000.000001")]
        self.assertEqual(classify(msgs)[0], "pending")


class FollowUps(unittest.TestCase):
    def answered(self):
        return thread() + [aura("1790297000.000001")]

    def test_aura_speaking_last_closes_it(self):
        self.assertEqual(classify(self.answered())[0], "skipped_already_answered")

    def test_an_untagged_question_after_aura_is_a_follow_up(self):
        msgs = self.answered() + [human("1790297100.000001", "can it also check android?")]
        bucket, awaiting = classify(msgs)
        self.assertEqual((bucket, [m["ts"] for m in awaiting]), ("pending", ["1790297100.000001"]))

    def test_a_reply_tagging_only_someone_else_is_not_for_aura(self):
        msgs = self.answered() + [human("1790297100.000001", f"<@{VIKTOR_USER}> what do you think?")]
        self.assertEqual(classify(msgs)[0], "skipped_not_directed")

    def test_tagging_aura_alongside_someone_else_is_for_aura(self):
        msgs = self.answered() + [human("1790297100.000001", f"<@{VIKTOR_USER}> <@{fp.AURA_AI_USER}> thoughts?")]
        self.assertEqual(classify(msgs)[0], "pending")

    def test_a_thanks_closes_the_exchange(self):
        for text in ("thanks!", "Thank you so much :pray:", "👍", "ok got it"):
            with self.subTest(text=text):
                msgs = self.answered() + [human("1790297100.000001", text)]
                self.assertEqual(classify(msgs)[0], "skipped_acknowledgement")

    def test_a_tagged_thanks_still_counts(self):
        msgs = self.answered() + [human("1790297100.000001", f"thanks <@{fp.AURA_AI_USER}>")]
        self.assertEqual(classify(msgs)[0], "pending")

    def test_a_screenshot_with_no_text_is_not_an_ack(self):
        # Slack delivers an attachment as subtype file_share.
        msgs = self.answered() + [human("1790297100.000001", "", subtype="file_share", files=[{"id": "F1"}])]
        self.assertEqual(classify(msgs)[0], "pending")

    def test_a_file_share_with_a_question_is_a_follow_up(self):
        msgs = self.answered() + [human("1790297100.000001", "this one too?", subtype="file_share", files=[{"id": "F1"}])]
        self.assertEqual(classify(msgs)[0], "pending")

    def test_ok_after_aura_asked_is_an_answer(self):
        msgs = thread() + [aura("1790297000.000001", "Want me to open a card?"), human("1790297100.000001", "ok")]
        self.assertEqual(classify(msgs)[0], "pending")

    def test_yes_is_never_a_sign_off(self):
        msgs = self.answered() + [human("1790297100.000001", "yes")]
        self.assertEqual(classify(msgs)[0], "pending")

    def test_a_link_query_string_is_not_a_question(self):
        link = "<https://x.slack.com/archives/C1/p1?thread_ts=1.2|thread>"
        msgs = thread() + [aura("1790297000.000001", f"Done, see {link}"), human("1790297100.000001", "thanks")]
        self.assertEqual(classify(msgs)[0], "skipped_acknowledgement")

    def test_a_question_after_a_thanks_is_still_answered(self):
        msgs = self.answered() + [
            human("1790297100.000001", "thanks"),
            human("1790297200.000001", "also, when does it ship?"),
        ]
        self.assertEqual(classify(msgs)[0], "pending")

    def test_untagged_chatter_before_aura_ever_spoke_is_not_for_aura(self):
        msgs = [human("1790297000.000001", "morning all")]
        self.assertEqual(classify(msgs)[0], "skipped_not_directed")


class LoopAndWindow(unittest.TestCase):
    def test_bot_messages_never_trigger(self):
        msgs = [aura("1790297000.000001"), viktor("1790297100.000001", f"<@{fp.AURA_AI_USER}> over to you")]
        self.assertEqual(classify(msgs)[0], "skipped_already_answered")

    def test_a_directed_message_before_the_window_is_stale_not_dropped(self):
        bucket, awaiting = classify(thread(), since=float(CC_TS) + 1)
        self.assertEqual((bucket, [m["ts"] for m in awaiting]), ("skipped_stale", [CC_TS]))

    def test_the_reply_cap_stops_a_runaway_thread(self):
        msgs = []
        for i in range(3):
            msgs += [human(f"179029{7000 + i * 10}.000001", f"<@{fp.AURA_AI_USER}> q{i}"),
                     aura(f"179029{7005 + i * 10}.000001")]
        msgs.append(human("1790297999.000001", f"<@{fp.AURA_AI_USER}> again"))
        self.assertEqual(classify(msgs, cap=3)[0], "skipped_cap")
        self.assertEqual(classify(msgs, cap=4)[0], "pending")


class ThreadRoot(unittest.TestCase):
    def test_a_threaded_match_resolves_to_its_root(self):
        match = {"ts": CC_TS, "permalink": "https://x.slack.com/archives/C1/p1?thread_ts=1790287583.265029&cid=C1"}
        self.assertEqual(fp.thread_root(match), "1790287583.265029")

    def test_a_top_level_match_is_its_own_root(self):
        match = {"ts": CC_TS, "permalink": "https://x.slack.com/archives/C1/p1790294465342649"}
        self.assertEqual(fp.thread_root(match), CC_TS)


class RunWiring(unittest.TestCase):
    """main() end to end, with search and thread reads stubbed at their import site."""

    def run_main(self, matches, threads, infos=None, argv=()):
        infos = infos or {}
        self.reads, self.info_calls = [], []

        def fake_get(method, params, *, token, max_attempts=4):
            self.assertEqual(token, "xoxb-test")
            if method == "conversations.info":
                self.info_calls.append(params["channel"])
                result = infos.get(params["channel"], PUBLIC_MEMBER)
                if isinstance(result, Exception):
                    raise result
                return {"ok": True, "channel": result}
            self.assertEqual(method, "conversations.replies")
            self.reads.append(params["channel"])
            result = threads[(params["channel"], params["ts"])]
            if isinstance(result, Exception):
                raise result
            return {"ok": True, "messages": copy.deepcopy(result)}

        search = {"ok": True, "messages": {"matches": matches, "paging": {"total": len(matches)}}}
        env = {"SLACK_BOT_TOKEN": "xoxb-test", "SLACK_USER_TOKEN": "xoxp-test"}
        out = io.StringIO()
        with mock.patch.object(fp, "search_messages", return_value=search) as self.search, \
             mock.patch.object(fp, "_get", side_effect=fake_get), \
             mock.patch.dict("os.environ", env), \
             mock.patch.object(sys, "argv", ["find_pending.py", "--since", str(DAY_START), *argv]), \
             redirect_stdout(out):
            fp.main()
        return json.loads(out.getvalue())

    @staticmethod
    def match(channel, ts, root=None, **flags):
        link = f"https://x.slack.com/archives/{channel}/p{ts.replace('.', '')}"
        if root:
            link += f"?thread_ts={root}&cid={channel}"
        kind = {"is_channel": True, "is_im": False, "is_mpim": False, "is_private": False, **flags}
        return {"ts": ts, "channel": {"id": channel, "name": channel.lower(), **kind}, "permalink": link}

    def test_every_thread_lands_in_exactly_one_bucket(self):
        root = "1790287583.265029"
        answered = [human("1790290000.000001", f"<@{fp.AURA_AI_USER}> hi"), aura("1790290100.000001")]
        report = self.run_main(
            matches=[
                self.match("CFEED", CC_TS, root),
                self.match("CFEED", "1790296000.000001", root),   # second mention, same thread
                self.match("CDONE", "1790290000.000001"),
                self.match("DPAIR", "1790291000.000001", is_channel=False, is_im=True, is_private=True),
                self.match("CBROKE", "1790292000.000001"),
            ],
            threads={
                ("CFEED", root): thread(),
                ("CDONE", "1790290000.000001"): answered,
                ("CBROKE", "1790292000.000001"): fp.SlackReadError("conversations.replies failed: ratelimited"),
            },
        )
        counts = report["counts"]
        self.assertEqual(counts["threads"], 4)
        self.assertEqual(sum(counts[b] for b in fp.BUCKETS) + counts[fp.IGNORED], counts["threads"])
        self.assertEqual(
            {b: counts[b] for b in ("pending", "skipped_already_answered", fp.IGNORED, "failed")},
            {"pending": 1, "skipped_already_answered": 1, fp.IGNORED: 1, "failed": 1},
        )
        self.assertNotIn("DPAIR", self.reads + self.info_calls)
        self.assertNotIn(fp.IGNORED, report)
        pending = report["pending"][0]
        self.assertEqual((pending["channel"], pending["thread_ts"], pending["awaiting"]), ("CFEED", root, [CC_TS]))
        self.assertTrue(any(m["aura_ai"] for m in pending["thread"]))
        self.assertIn("ratelimited", report["failed"][0]["reason"])

    def test_a_long_thread_is_read_to_its_newest_page(self):
        root = "1790287583.265029"
        page1 = thread() + [aura("1790297000.000001")]
        page2 = [human("1790297100.000001", "one more thing: android?")]
        pages = iter([
            {"ok": True, "messages": page1, "response_metadata": {"next_cursor": "c2"}},
            {"ok": True, "messages": page2, "response_metadata": {"next_cursor": ""}},
        ])
        seen = []

        def fake_get(method, params, *, token, max_attempts=4):
            seen.append(params.get("cursor"))
            return next(pages)

        with mock.patch.object(fp, "_get", side_effect=fake_get), \
             mock.patch.dict("os.environ", {"SLACK_BOT_TOKEN": "xoxb-test"}):
            msgs = fp.read_thread("CFEED", root)
        self.assertEqual(seen, [None, "c2"])
        self.assertEqual(fp.classify(msgs, DAY_START, 5)[0], "pending")

    def test_dms_and_private_channels_are_ignored_before_any_read(self):
        root = "1790291000.000001"
        report = self.run_main(
            [self.match("DPAIR", root, is_channel=False, is_im=True, is_private=True),
             self.match("GTRIO", root, is_channel=False, is_mpim=True, is_private=True),
             # Private even though Aura AI is a member: still out of scope.
             self.match("CSECRET", root, is_private=True)],
            threads={},
        )
        self.assertEqual((report["counts"][fp.IGNORED], report["counts"]["failed"]), (3, 0))
        self.assertEqual((self.reads, self.info_calls), ([], []))

    def test_a_public_channel_aura_ai_has_not_joined_is_ignored(self):
        roots = ("1790291000.000001", "1790291500.000001")
        report = self.run_main(
            [self.match("CNOTIF", r) for r in roots],
            threads={},
            infos={"CNOTIF": {**PUBLIC_MEMBER, "is_member": False}},
        )
        self.assertEqual((report["counts"][fp.IGNORED], report["counts"]["failed"]), (2, 0))
        self.assertEqual((self.reads, self.info_calls), ([], ["CNOTIF"]))  # one lookup per channel

    def test_leaving_a_channel_between_the_check_and_the_read_is_still_ignored(self):
        root = "1790291000.000001"
        err = fp.SlackReadError("Slack conversations.replies failed: not_in_channel")
        report = self.run_main([self.match("CLEFT", root)], {("CLEFT", root): err})
        self.assertEqual((report["counts"][fp.IGNORED], report["counts"]["failed"]), (1, 0))

    def test_a_lost_scope_is_a_failure_not_an_ignore(self):
        # Otherwise a bot token missing channels:read would quietly ignore every channel.
        root = "1790291000.000001"
        err = fp.SlackReadError("Slack conversations.info failed: missing_scope")
        report = self.run_main([self.match("CFEED", root)], threads={}, infos={"CFEED": err})
        self.assertEqual((report["counts"][fp.IGNORED], report["counts"]["failed"]), (0, 1))
        self.assertIn("missing_scope", report["failed"][0]["reason"])

    def test_explicit_threads_apply_the_same_ignore_rule(self):
        root = "1790287583.265029"
        report = self.run_main(
            matches=[],
            threads={("CFEED", root): thread()},
            infos={
                "DPAIR": fp.SlackReadError("Slack conversations.info failed: channel_not_found"),
                "CSECRET": {**PUBLIC_MEMBER, "is_private": True},
                "CNOTIF": {**PUBLIC_MEMBER, "is_member": False},
            },
            argv=["--threads", f"CFEED:{root},DPAIR:{root},CSECRET:{root},CNOTIF:{root}"],
        )
        self.search.assert_not_called()
        self.assertEqual((report["counts"]["pending"], report["counts"][fp.IGNORED]), (1, 3))
        self.assertEqual(self.reads, ["CFEED"])

    def test_ai_loop_channels_are_excluded_before_any_read(self):
        # Each has its own loop agent; Aura AI is a member, so only the id list keeps it out.
        root = "1790291000.000001"
        loops = sorted(fp.EXCLUDED_CHANNELS)
        self.assertEqual(set(loops), {"C0C1JTK3YCC", "C0C0ZSMU7C7", "C0C29JPEUQG", "C0C0ZSMNZM5"})
        report = self.run_main(
            [self.match(c, root) for c in loops] + [self.match("CFEED", CC_TS, "1790287583.265029")],
            threads={("CFEED", "1790287583.265029"): thread()},
        )
        counts = report["counts"]
        self.assertEqual((counts[fp.EXCLUDED], counts[fp.IGNORED], counts["pending"]), (4, 0, 1))
        self.assertEqual(sum(counts[b] for b in fp.BUCKETS) + counts[fp.IGNORED] + counts[fp.EXCLUDED], counts["threads"])
        self.assertEqual((self.reads, self.info_calls), (["CFEED"], ["CFEED"]))

    def test_explicit_threads_apply_the_exclusion(self):
        root = "1790287583.265029"
        loop = next(iter(fp.EXCLUDED_CHANNELS))
        report = self.run_main([], {("CFEED", root): thread()}, argv=["--threads", f"CFEED:{root},{loop}:{root}"])
        self.assertEqual((report["counts"]["pending"], report["counts"][fp.EXCLUDED]), (1, 1))
        self.assertNotIn(loop, self.reads + self.info_calls)

    def test_the_pre_post_check_refuses_an_ai_loop_channel(self):
        loop = next(iter(fp.EXCLUDED_CHANNELS))
        out = io.StringIO()
        with mock.patch.object(fp, "_get", side_effect=AssertionError("read an excluded channel")), \
             mock.patch.dict("os.environ", {"SLACK_BOT_TOKEN": "xoxb-test"}), \
             mock.patch.object(sys, "argv", ["find_pending.py", "--check", f"{loop}:1790291000.000001"]), \
             redirect_stdout(out), self.assertRaises(SystemExit) as exit_:
            fp.main()
        self.assertEqual((exit_.exception.code, out.getvalue().strip()), (3, fp.EXCLUDED))

    def test_a_screenshot_reaches_the_replier_with_its_file(self):
        shot = human("1790297100.000001", "", subtype="file_share",
                     files=[{"id": "F1", "name": "crash.png", "mimetype": "image/png",
                             "permalink": "https://x.slack.com/files/U1/F1/crash.png", "url_private": "secret"}])
        slimmed = fp.slim(shot)
        self.assertEqual(slimmed["files"], [{"id": "F1", "name": "crash.png", "mimetype": "image/png",
                                             "permalink": "https://x.slack.com/files/U1/F1/crash.png"}])
        self.assertNotIn("files", fp.slim(human("1790297100.000002", "plain")))

    def test_explicit_threads_skip_the_search(self):
        root = "1790287583.265029"
        report = self.run_main([], {("CFEED", root): thread()}, argv=["--threads", f"CFEED:{root}"])
        self.search.assert_not_called()
        self.assertEqual((report["counts"]["pending"], report["truncated"]), (1, False))

    def test_the_search_asks_for_the_aura_ai_mention(self):
        with mock.patch.object(fp, "search_messages", return_value={"ok": True, "messages": {"matches": []}}) as s, \
             mock.patch.object(sys, "argv", ["find_pending.py", "--since", str(DAY_START)]), \
             redirect_stdout(io.StringIO()):
            fp.main()
        self.assertTrue(s.call_args.args[0].startswith(f"<@{fp.AURA_AI_USER}> after:"))


if __name__ == "__main__":
    unittest.main()
