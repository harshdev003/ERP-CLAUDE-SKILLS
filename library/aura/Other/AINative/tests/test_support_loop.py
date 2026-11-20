"""Guard tests for `eng:support-loop`'s thread sorter.

The sorter decides which #support threads the loop may answer. The failure that
matters is a false "pending": the loop then replies over a person who already
answered, or takes a request that has another owner (a privacy-law request, a thread
that tagged Aura AI). The first dry run hit exactly that: a request answered by a lead
who was not one of its assignees read as unanswered. No network: threads are fixtures.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from unittest import mock

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(REPO, "plugins", "eng", "skills", "support-loop", "scripts")
sys.path.insert(0, SCRIPTS)
_spec = importlib.util.spec_from_file_location("find_support_threads",
                                               os.path.join(SCRIPTS, "find_support_threads.py"))
fst = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fst)

# The other skill that posts in #support as Aura AI. Its sorter is loaded to check the two
# against each other on the same threads.
_fp_spec = importlib.util.spec_from_file_location(
    "find_pending",
    os.path.join(REPO, "plugins", "shared", "skills", "reply-to-aura-ai-mentions", "scripts", "find_pending.py"))
fp = importlib.util.module_from_spec(_fp_spec)
_fp_spec.loader.exec_module(fp)

ASHISH, NAVEED, JEROME, ELLORA, ROSE = "U064HNE8Q0Y", "U01CPPQ2MNJ", "UEW6170R3", "U01FWJNMR9R", "U019LLC8S5C"
OURS = {ASHISH, NAVEED}
UID = "abcdefghijklmnopqrstuvwxyz12"


def request(assignees=(NAVEED, ASHISH), body="The app does not save it.", uid=UID, by=ELLORA, ts="1.0"):
    """A root shaped like the "Add Support or Bug" workflow post."""
    who = ", ".join(f"<@{a}|name>" for a in assignees)
    text = (f"Add Support or Bug: Unfollow Coach\n\nHi Team!\n```{body}```\n\n*UserID*: `\n{uid}\n`\n\n"
            f"CoachID: `\n\n`\n\n*Assignee*\n{who}\n\nSubmitted by\n<@{by}|name>\n\n"
            "Tracker Link\n<https://auramobile.slack.com/lists/T/F?record_id=Rec1>")
    return {"ts": ts, "bot_id": "B08PHB33C0M", "text": text}


def human(user, text, ts):
    return {"ts": ts, "user": user, "text": text}


def aura(text, ts):
    return {"ts": ts, "user": fst.AURA_AI_USER, "bot_id": fst.AURA_AI_BOT, "text": text}


# A person posting through an app (a user token): their own id in `user`, plus the app's bot.
# Shaped like the answer on the first dry run's only pending thread.
APP_BOT = "B0OTHERAPP1"
LINEAR_BOT_USER, LINEAR_BOT = "U0LINEARBOT", "B0LINEARBOT"


def via_app(user, text, ts, **extra):
    return {"ts": ts, "user": user, "bot_id": APP_BOT, "text": text, **extra}


def linear(text, ts, **extra):
    return {"ts": ts, "user": LINEAR_BOT_USER, "bot_id": LINEAR_BOT, "text": text, **extra}


def people(*persons):
    """A users.info stand-in: True for the given ids, False for every other (a bot user)."""
    return lambda uid: uid in persons


def bucket(thread):
    return fst.classify(thread, OURS, 3)[0]


class ParseRequest(unittest.TestCase):
    def test_structured_fields(self):
        req = fst.parse_request(request())
        self.assertEqual(req["title"], "Unfollow Coach")
        self.assertEqual(req["user_id"], UID)
        self.assertIsNone(req["coach_id"])
        self.assertEqual(req["assignees"], sorted([NAVEED, ASHISH]))
        self.assertEqual(req["submitted_by"], ELLORA)
        self.assertTrue(req["tracker_link"].startswith("https://auramobile.slack.com/lists/"))

    def test_uid_wrapped_in_bold_still_parses(self):
        self.assertEqual(fst.parse_request(request(uid=f"*{UID}*"))["user_id"], UID)

    def test_channel_join_is_not_a_request(self):
        join = {"ts": "2.0", "user": "U0AUXA5TUDA", "subtype": "channel_join", "text": "<@U0AUXA5TUDA> has joined"}
        self.assertIsNone(fst.parse_request(join))

    def test_free_form_post_that_tags_someone(self):
        req = fst.parse_request(human(ROSE, f"Hi <@{ASHISH}>! User `{UID}` can't log in", "3.0"))
        self.assertFalse(req["structured"])
        self.assertEqual(req["assignees"], [ASHISH])
        self.assertEqual(req["user_id"], UID)
        self.assertEqual(req["title"], f"Hi @someone! User `{UID}` can't log in")

    def test_free_form_title_shows_names_not_ids(self):
        req = fst.parse_request(human(ROSE, f"Hi <@{JEROME}|jerome>! Emails in Spanish please", "3.0"))
        self.assertEqual(req["title"], "Hi @jerome! Emails in Spanish please")


class Classify(unittest.TestCase):
    def test_untouched_request_is_pending(self):
        self.assertEqual(bucket([request()]), "pending_new")

    def test_support_only_chatter_stays_pending(self):
        self.assertEqual(bucket([request(), human(ELLORA, "Adding a screenshot", "1.1")]), "pending_new")

    def test_answer_from_a_non_assignee_is_not_pending(self):
        # The dry-run defect: Jerome answered a request assigned to Naveed and Ashish.
        thread = [request(), human(JEROME, "Fixed: the unfollow now saves.", "1.1"), human(ELLORA, "Thank you!", "1.2")]
        self.assertEqual(bucket(thread), "skipped_engineer")

    def test_support_follow_up_to_a_person_is_left_to_that_person(self):
        thread = [request(), human(NAVEED, "Me tab → sidebar", "1.1"), human(ELLORA, "She still can't see it", "1.2")]
        self.assertEqual(bucket(thread), "skipped_engineer")
        self.assertIn("waiting", fst.classify(thread, OURS, 3)[1])

    def test_privacy_request_is_never_taken(self):
        thread = [request(assignees=(ASHISH,), body="Deletion request under the NJDPA for all personal data")]
        self.assertEqual(bucket(thread), "skipped_privacy")

    def test_someone_elses_request(self):
        self.assertEqual(bucket([request(assignees=(JEROME,))]), "skipped_not_ours")

    def test_tagging_aura_ai_hands_it_to_the_mention_skill(self):
        thread = [request(), human(ROSE, f"<@{fst.AURA_AI_USER}> can you check?", "1.1")]
        self.assertEqual(bucket(thread), "skipped_mention")

    def test_aura_ai_last_is_answered(self):
        self.assertEqual(bucket([request(), aura("Found it", "1.1")]), "skipped_answered")
        self.assertEqual(bucket([request(), aura("Found it", "1.1"), human(ELLORA, "Thanks!", "1.2")]),
                         "skipped_answered")

    def test_question_after_aura_ai_is_a_follow_up(self):
        thread = [request(), aura("Found it", "1.1"), human(ELLORA, "She says it still happens, any update?", "1.2")]
        self.assertEqual(bucket(thread), "pending_followup")

    def test_screenshot_after_aura_ai_is_a_follow_up(self):
        # BugBot on #1547: an uncaptioned screenshot read as a "thanks" and closed the thread.
        shot = {"ts": "1.2", "user": ELLORA, "text": "", "files": [{"id": "F1", "name": "image.png"}]}
        self.assertEqual(bucket([request(), aura("Found it", "1.1"), shot]), "pending_followup")
        self.assertFalse(fst.is_acknowledgement(shot))

    def test_sign_offs_are_acknowledgements(self):
        # QA on 2026-11-10: "Noted. Thank you @jerome!" read as a new question.
        for text in ("Thank you!", f"Noted. Thank you <@{JEROME}|jerome>!", "Will do, thanks",
                     "Thanks so much :pray:", "ok", ":white_check_mark:", "Got it. Much appreciated!"):
            self.assertTrue(fst.is_acknowledgement(human(ELLORA, text, "1.2")), text)

    def test_a_question_or_a_report_is_not_an_acknowledgement(self):
        for text in ("Thanks, but it's still broken", "Thank you! Is it fixed for Android too?",
                     "Still happening", "ok what about the refund?", "Yes", "yep"):
            self.assertFalse(fst.is_acknowledgement(human(ELLORA, text, "1.2")), text)

    def test_yes_after_aura_ai_asked_something_is_a_follow_up(self):
        # BugBot on #1547: "yes" answers a question Aura AI asked; it does not close the thread.
        thread = [request(), aura("Should I credit the coach for both donations?", "1.1"), human(ELLORA, "Yes", "1.2")]
        self.assertEqual(bucket(thread), "pending_followup")

    def test_noted_thank_you_after_aura_ai_closes_the_thread(self):
        thread = [request(), aura("Found it", "1.1"), human(ELLORA, f"Noted. Thank you <@{fst.AURA_AI_USER}>!", "1.2")]
        # A tag of Aura AI belongs to the mention skill; untagged, the thanks just closes it.
        untagged = [request(), aura("Found it", "1.1"), human(ELLORA, "Noted. Thank you!", "1.2")]
        self.assertEqual(bucket(thread), "skipped_mention")
        self.assertEqual(bucket(untagged), "skipped_answered")

    def test_screenshot_after_a_person_is_support_waiting(self):
        shot = {"ts": "1.2", "user": ELLORA, "text": "", "files": [{"id": "F1", "name": "image.png"}]}
        thread = [request(), human(NAVEED, "Should be fixed now", "1.1"), shot]
        self.assertIn("waiting", fst.classify(thread, OURS, 3)[1])

    def test_reply_cap(self):
        thread = [request()] + [aura("again", f"1.{i}") for i in range(1, 4)] + [human(ELLORA, "and?", "1.9")]
        self.assertEqual(bucket(thread), "skipped_cap")


class PostedThroughAnApp(unittest.TestCase):
    """The dry run on 2026-11-11: an answer a person posted through an app read as a bot's,
    so a thread that was answered and fixed came out pending."""

    def answered(self, **extra):
        return [request(), via_app(JEROME, "Fixed: the unfollow now saves.", "1.1", **extra),
                human(ELLORA, "Thank you!", "1.2")]

    def test_a_persons_answer_through_an_app_closes_the_request(self):
        thread = fst.mark_authors(self.answered(), people(JEROME))
        self.assertEqual(bucket(thread), "skipped_engineer")

    def test_an_author_nobody_looked_up_still_reads_as_a_bot(self):
        # --from-json without the field, or a failed lookup: the old reading, never a guess.
        self.assertEqual(bucket(self.answered()), "pending_new")
        self.assertEqual(bucket(fst.mark_authors(self.answered(), lambda uid: None)), "pending_new")

    def test_a_bot_users_reply_is_not_an_answer(self):
        thread = fst.mark_authors([request(), linear("ENGBACK-1 created", "1.1")], people(JEROME))
        self.assertEqual(bucket(thread), "pending_new")
        self.assertIs(thread[1]["author_is_person"], False)

    def test_from_json_can_say_a_person_posted_it(self):
        self.assertEqual(bucket(self.answered(author_is_person=True)), "skipped_engineer")

    def test_a_follow_up_through_an_app_after_aura_ai_is_pending(self):
        thread = fst.mark_authors(
            [request(), aura("Found it", "1.1"), via_app(ELLORA, "She says it still happens", "1.2")],
            people(ELLORA))
        self.assertEqual(bucket(thread), "pending_followup")

    def test_only_app_posted_authors_are_looked_up_once_each(self):
        seen = []
        thread = [request(), human(NAVEED, "On it", "1.1"), aura("Found it", "1.2"),
                  linear("ENGBACK-1 created", "1.3"), linear("ENGBACK-1 done", "1.4"),
                  via_app(JEROME, "Fixed", "1.5")]
        fst.mark_authors(thread, lambda uid: seen.append(uid) or uid == JEROME)
        # The workflow root has no user, the plain reply has no bot, Aura AI is known.
        self.assertEqual(sorted(set(seen)), sorted([LINEAR_BOT_USER, JEROME]))
        self.assertNotIn("author_is_person", thread[0])
        self.assertNotIn("author_is_person", thread[2])

    def test_live_lookup_asks_slack_once_per_user(self):
        calls = []

        def fake_get(method, params, *, token):
            calls.append((method, params["user"]))
            return {"user": {"id": params["user"], "is_bot": params["user"] == LINEAR_BOT_USER}}

        fake = types.SimpleNamespace(_get=fake_get, _token=lambda explicit: "xoxb-test")
        with mock.patch.dict(fst._PERSON, clear=True), mock.patch.dict(sys.modules, {"slack_read": fake}):
            self.assertIs(fst.slack_user_is_person(JEROME), True)
            self.assertIs(fst.slack_user_is_person(JEROME), True)
            self.assertIs(fst.slack_user_is_person(LINEAR_BOT_USER), False)
        self.assertEqual(calls, [("users.info", JEROME), ("users.info", LINEAR_BOT_USER)])

    def test_a_failed_live_lookup_is_unknown(self):
        def boom(method, params, *, token):
            raise RuntimeError("Slack error: missing_scope")

        fake = types.SimpleNamespace(_get=boom, _token=lambda explicit: "xoxb-test")
        with mock.patch.dict(fst._PERSON, clear=True), mock.patch.dict(sys.modules, {"slack_read": fake}):
            self.assertIsNone(fst.slack_user_is_person(JEROME))

    def test_a_tag_posted_through_an_app_stays_the_loops(self):
        # The mention skill drops every message with a bot_id, so it would never answer this
        # tag. Handing the thread over would leave it with nobody.
        tag = via_app(ROSE, f"<@{fst.AURA_AI_USER}> can you check?", "1.1", author_is_person=True)
        self.assertEqual(bucket([request(), tag]), "pending_new")


class SharedChannel(unittest.TestCase):
    """#support has two skills posting as Aura AI: this loop and shared:reply-to-aura-ai-mentions.

    A thread needs exactly one of them. Two is a second reply talking over the first. None is
    a tag of Aura AI that nobody answers, which is what excluding #support from the mention
    skill would cause.
    """

    def claims(self, thread):
        """(the loop takes it, the mention skill takes it), each by that skill's own sorter."""
        # The mention skill never reads a thread unless its search for the Aura AI tag returns it.
        found = any(fst.AURA_AI_USER in fp.mentions(m) for m in thread)
        return bucket(thread) in fst.PENDING, found and fp.classify(thread, 0.0, 5)[0] == "pending"

    def test_untagged_threads_are_the_loops(self):
        follow_up = [request(), aura("Found it", "1.1"), human(ELLORA, "She says it still happens, any update?", "1.2")]
        self.assertEqual(self.claims([request()]), (True, False))
        self.assertEqual(self.claims(follow_up), (True, False))

    def test_threads_where_a_person_tags_aura_ai_are_the_mention_skills(self):
        tag = f"<@{fst.AURA_AI_USER}> can you check her receipts?"
        self.assertEqual(self.claims([request(), human(ROSE, tag, "1.1")]), (False, True))
        self.assertEqual(self.claims([request(), aura("Found it", "1.1"), human(ROSE, tag, "1.2")]), (False, True))

    def test_support_stays_out_of_the_mention_skills_excluded_channels(self):
        # Excluded there, the tagged threads above would have no owner: the loop skips them
        # and the mention skill would never read them.
        self.assertNotIn(fst.SUPPORT_CHANNEL, fp.EXCLUDED_CHANNELS)

    def test_a_request_that_lists_aura_ai_is_still_worked(self):
        # The tag sits in the workflow post, not in a person's message. Skipping on it would
        # leave the request with nobody: the mention skill only answers people.
        listed = request(assignees=(ASHISH, fst.AURA_AI_USER))
        self.assertEqual(self.claims([listed]), (True, False))
        self.assertEqual(self.claims([listed, human(ELLORA, "Adding a screenshot", "1.1")]), (True, False))

    def test_a_tag_posted_through_an_app_has_one_owner(self):
        tag = via_app(ROSE, f"<@{fst.AURA_AI_USER}> can you check her receipts?", "1.1", author_is_person=True)
        self.assertEqual(self.claims([request(), tag]), (True, False))

    def test_two_claimants_still_give_one_answer(self):
        # After the loop's reply, both skills see an untagged follow-up as theirs. They post
        # as the same bot, so whichever answers first closes it for the other.
        listed = request(assignees=(ASHISH, fst.AURA_AI_USER))
        waiting = [listed, aura("Found it", "1.1"), human(ELLORA, "She says it still happens, any update?", "1.2")]
        self.assertEqual(self.claims(waiting), (True, True))
        self.assertEqual(self.claims(waiting + [aura("Looked again", "1.3")]), (False, False))


class Summary(unittest.TestCase):
    def test_cards_and_prs_are_listed_so_the_loop_reuses_them(self):
        thread = [request(), human(ASHISH, "PROD-8140 https://github.com/AuraHealth/Aura/pull/7516", "1.1")]
        item = fst.summarize("C7KS8TWAG", thread, OURS, 3)
        self.assertEqual(item["cards"], ["PROD-8140"])
        self.assertEqual(item["prs"], ["https://github.com/AuraHealth/Aura/pull/7516"])

    def test_only_pending_items_carry_the_thread(self):
        self.assertIn("thread", fst.summarize("C", [request()], OURS, 3))
        self.assertNotIn("thread", fst.summarize("C", [request(assignees=(JEROME,))], OURS, 3))


class Cli(unittest.TestCase):
    def setUp(self):
        threads = [
            {"channel": "C7KS8TWAG", "messages": [request(ts="1.0")]},
            {"channel": "C7KS8TWAG", "messages": [request(ts="2.0"), aura("Found it", "2.1")]},
        ]
        fd, self.path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(threads, fh)

    def tearDown(self):
        os.unlink(self.path)

    def run_cli(self, *argv):
        out = io.StringIO()
        with mock.patch.object(sys, "argv", ["find_support_threads.py", "--from-json", self.path, *argv]), \
                redirect_stdout(out):
            try:
                fst.main()
                code = 0
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue()

    def test_report_counts(self):
        code, out = self.run_cli()
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["counts"], {"pending_new": 1, "skipped_answered": 1})

    def test_check_blocks_a_post_into_an_answered_thread(self):
        self.assertEqual(self.run_cli("--check", "C7KS8TWAG:1.0")[0], 0)
        self.assertEqual(self.run_cli("--check", "C7KS8TWAG:2.0")[0], 3)
        self.assertEqual(self.run_cli("--check", "C7KS8TWAG:9.9")[0], 3)

    def test_check_with_a_malformed_target_blocks(self):
        code, out = self.run_cli("--check", "C7KS8TWAG")
        self.assertEqual((code, out.strip()), (3, "check_failed"))

    def test_report_says_how_many_threads_it_read(self):
        self.assertEqual(json.loads(self.run_cli()[1])["threads_read"], 2)


class LiveCheck(unittest.TestCase):
    """The pre-post re-check must read the one thread fresh, and fail closed."""

    def run_check(self, **patches):
        out = io.StringIO()
        argv = ["find_support_threads.py", "--check", "C7KS8TWAG:1.0"]
        # Stub the script's own reader, not a slack_read module: other suites load another
        # skill's slack_read first, so the module the script imports is not this file's.
        with mock.patch.object(sys, "argv", argv), redirect_stdout(out), \
                mock.patch.object(fst, "fetch_thread", **patches) as replies, \
                mock.patch.object(fst, "load_threads") as history:
            try:
                fst.main()
                code = 0
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue().strip(), replies, history

    def test_reads_the_thread_itself_not_the_history_sweep(self):
        # A reply that landed during the tick: history's reply_count was still 0.
        code, out, replies, history = self.run_check(
            return_value=[request(), human(NAVEED, "On it", "1.1")])
        self.assertEqual((code, out), (3, "skipped_engineer"))
        replies.assert_called_once_with("C7KS8TWAG", "1.0")
        history.assert_not_called()

    def test_still_pending_allows_the_post(self):
        code, out, _, _ = self.run_check(return_value=[request()])
        self.assertEqual((code, out), (0, "pending_new"))

    def test_a_read_failure_blocks_the_post(self):
        code, out, _, _ = self.run_check(side_effect=RuntimeError("Slack error: ratelimited"))
        self.assertEqual((code, out), (3, "check_failed"))

    def test_a_missing_token_blocks_the_post(self):
        code, out, _, _ = self.run_check(side_effect=RuntimeError("SLACK_BOT_TOKEN not set"))
        self.assertEqual((code, out), (3, "check_failed"))

    def test_an_empty_read_blocks_the_post(self):
        code, out, _, _ = self.run_check(return_value=[])
        self.assertEqual((code, out), (3, "not_found"))

    def test_an_author_the_lookup_could_not_place_blocks_the_post(self):
        # It may be a person who already answered through an app.
        code, out, _, _ = self.run_check(return_value=[request(), via_app(JEROME, "Fixed", "1.1")])
        self.assertEqual((code, out), (3, "check_failed"))

    def test_a_persons_answer_through_an_app_blocks_the_post(self):
        thread = [request(), via_app(JEROME, "Fixed", "1.1", author_is_person=True)]
        code, out, _, _ = self.run_check(return_value=thread)
        self.assertEqual((code, out), (3, "skipped_engineer"))

    def test_a_bot_users_reply_does_not_block_the_post(self):
        thread = [request(), linear("ENGBACK-1 created", "1.1", author_is_person=False)]
        code, out, _, _ = self.run_check(return_value=thread)
        self.assertEqual((code, out), (0, "pending_new"))

    def test_the_live_read_marks_authors(self):
        # fetch_thread itself, with Slack stubbed: the post check sees the lookup's verdict.
        fake = types.SimpleNamespace(replies=lambda channel, ts: [request(), via_app(JEROME, "Fixed", "1.1")])
        with mock.patch.dict(sys.modules, {"slack_read": fake}), \
                mock.patch.object(fst, "slack_user_is_person", people(JEROME)):
            thread = fst.fetch_thread("C7KS8TWAG", "1.0")
        self.assertIs(thread[1]["author_is_person"], True)
        self.assertEqual(bucket(thread), "skipped_engineer")


if __name__ == "__main__":
    unittest.main()
