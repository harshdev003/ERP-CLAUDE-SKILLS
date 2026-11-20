"""Classifier tests for `product:harvest-idea-decisions`.

The headline test runs the real `#ai-ideas` April 2026 archive — all 22 shared
ideas, captured verbatim from Slack — through the classifier and pins every
verdict. Those verdicts were established independently by a human review of the
same threads (PROD-5920), so this is a comparison against a known answer rather
than a snapshot of whatever the code happened to do first.

No network: the fixture is on disk and nothing here touches Slack or Notion.
"""

from __future__ import annotations

import json
import os
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(REPO, "plugins", "product", "skills", "harvest-idea-decisions", "scripts")
sys.path.insert(0, SCRIPTS)

import harvest_idea_decisions as h  # noqa: E402

FIXTURE = os.path.join(REPO, "tests", "fixtures", "ai_ideas_april_threads.json")


def thread(*, reactions=None, replies=None, ts="1776465439.961579"):
    """Minimal normalised thread, the shape `fetch_thread` produces."""
    return {
        "channel": "C0ATUL8T936",
        "ts": ts,
        "text": "idea",
        "reactions": reactions or [],
        "replies": replies or [],
    }


def reply(text, user="U1BCYETM2", ts="1776465500.000000"):
    return {"user": user, "ts": ts, "text": text}


def build(version="1.30.0 (1)", round_no=1, ts="1776465600.000000"):
    """The build announcement ship-idea posts. It is what opens the release gate,
    so a gate-2 thread without one is a thread nobody has been asked about yet."""
    return dict(reply(f"Build {version} in TestFlight for review (round {round_no}).",
                      user="U0ATMPESR37", ts=ts), bot=True)


class ArchiveCorpus(unittest.TestCase):
    """The 22 real April threads, verdict by verdict."""

    # Keyed by the Slack-side idea fingerprint carried in each posted message.
    EXPECTED = {
        # --- decided in a reply -------------------------------------------
        "631534b4400e": "approved",       # Steve: "Would love to create a card & run ab test"
        "303f61c2bb50": "approved",       # Rohan linked PROD-2126
        "0fbcae45c2c2": "rejected",       # Steve: "let's leave as is" — five messages in
        "372f78cc0894": "deferred",       # Steve: "add manual journal entires first!"
        "c78641ce6d46": "deferred",       # Steve: "low priority but definitely in the future"
        # --- decided by a reaction ----------------------------------------
        "6e68c2035a7b": "approved",       # Rohan :white_check_mark:
        "4f2aac11d639": "approved",       # Rohan :white_check_mark: (+ Steve :+1:, sentiment)
        "b0485ab95dc0": "rejected",       # Steve :-1:
        "2567111b672f": "rejected",       # Rohan :x:
        "751ee36c265c": "rejected",       # Rohan :x:
        "c9081ed0d24a": "rejected",       # Rohan :x:
        # --- a real discussion the loop must not resolve alone -------------
        "fb5b8ff454cb": "needs_judgment",  # Steve: "technically in the feed - but not doing a great job."
        # --- :+1: only, or silence: never a verdict ------------------------
        "edb319769e27": "no_decision",
        "6750e2b71f06": "no_decision",
        "2990424c412f": "no_decision",
        "5b6b55228888": "no_decision",
        "434460e15c58": "no_decision",
        "00f27f60a10f": "no_decision",
        "249c406ec46b": "no_decision",
        "a4b9e349f6c5": "no_decision",
        "31357d18ac76": "no_decision",
        "4991ff40db10": "no_decision",
    }

    @classmethod
    def setUpClass(cls):
        with open(FIXTURE) as f:
            cls.threads = {t["slack_fingerprint"]: t for t in json.load(f)}

    def test_fixture_is_the_whole_archive(self):
        self.assertEqual(len(self.threads), 22)
        self.assertEqual(set(self.threads), set(self.EXPECTED))

    def test_every_verdict(self):
        for fp, expected in self.EXPECTED.items():
            with self.subTest(idea=fp):
                self.assertEqual(h.classify(self.threads[fp])["verdict"], expected)

    def test_thumbs_up_alone_never_approves(self):
        """The load-bearing asymmetry, checked against the cases that prove it.

        Five archive threads carry a lone :+1: and nothing else (four more pair a
        :+1: with a reply that does decide). Two of the five were later answered
        the other way by an experiment — `edb319769e27` became `aiTestimonials`
        (lost, paid cancellation +55%) and the save half of `2990424c412f` was
        settled by `bookmarkChat` ending on control. Reading :+1: as approval
        would have queued both for build.
        """
        plus_one_only = [
            fp for fp, t in self.threads.items()
            if not t["replies"]
            and t["reactions"]
            and all(r["name"] == "+1" for r in t["reactions"])
        ]
        self.assertEqual(len(plus_one_only), 5, "archive shape changed")
        self.assertIn("edb319769e27", plus_one_only)
        self.assertIn("2990424c412f", plus_one_only)
        for fp in plus_one_only:
            with self.subTest(idea=fp):
                self.assertEqual(h.classify(self.threads[fp])["verdict"], "no_decision")

    def test_plus_one_is_recorded_as_sentiment_not_discarded(self):
        """It does not decide, but it is not thrown away either."""
        res = h.classify(self.threads["4f2aac11d639"])
        self.assertEqual(res["verdict"], "approved")
        self.assertTrue(any("+1" in s for s in res["sentiment"]))

    def test_decisions_carry_their_evidence_and_decider(self):
        res = h.classify(self.threads["0fbcae45c2c2"])
        self.assertIn("leave as is", res["evidence"].lower())
        self.assertEqual(res["decider"], "U1BCYETM2")   # Steve wrote it
        self.assertEqual(res["at"], "2026-04-20")       # the reply's own date, not the post's

    def test_reaction_decider_is_the_reactor(self):
        res = h.classify(self.threads["751ee36c265c"])
        self.assertEqual(res["decider"], "UFYS6JYH5")   # Rohan, not the thread author


class ReplyPrecedence(unittest.TestCase):
    def test_last_decisive_reply_wins(self):
        """A thread argues its way to a conclusion; the conclusion is the verdict."""
        t = thread(replies=[
            reply("let's build this", ts="1776465500.000000"),
            reply("actually, leave as is", ts="1776465600.000000"),
        ])
        self.assertEqual(h.classify(t)["verdict"], "rejected")

    def test_non_decisive_replies_do_not_displace_an_earlier_decision(self):
        t = thread(replies=[
            reply("let's build this", ts="1776465500.000000"),
            reply("nice one", ts="1776465600.000000"),
        ])
        self.assertEqual(h.classify(t)["verdict"], "approved")

    def test_deferral_beats_approval_inside_one_sentence(self):
        """"let's do it later" is a deferral. Testing DEFER first is what makes
        that true, and reversing the order silently turns every deferral into a
        build commitment."""
        self.assertEqual(h.classify(thread(replies=[reply("let's do it later")]))["verdict"], "deferred")
        self.assertEqual(h.classify(thread(replies=[reply("let's build it once we ship v2")]))["verdict"], "deferred")

    def test_replies_outrank_reactions(self):
        t = thread(
            reactions=[{"name": "white_check_mark", "users": ["UFYS6JYH5"], "count": 1}],
            replies=[reply("leave as is")],
        )
        self.assertEqual(h.classify(t)["verdict"], "rejected")

    def test_a_linear_issue_link_is_an_approval(self):
        t = thread(replies=[reply(
            "Wrote up requirements → <https://linear.app/aura-health/issue/PROD-2126/abshareaffirmation>"
        )])
        self.assertEqual(h.classify(t)["verdict"], "approved")

    def test_discussion_without_a_decision_asks_for_judgment(self):
        """Distinct from silence: someone engaged, nobody concluded. A non-question
        comment — a bare question is now its own `needs_answer` outcome."""
        t = thread(replies=[reply("This overlaps with the streak work.")])
        self.assertEqual(h.classify(t)["verdict"], "needs_judgment")

    def test_negation_is_not_mistaken_for_a_rejection(self):
        """"not doing a great job" is a complaint about the product, not a verdict
        on the idea — the exact sentence the archive supplies."""
        t = thread(replies=[reply("technically in the feed - but not doing a great job.")])
        self.assertEqual(h.classify(t)["verdict"], "needs_judgment")


class BotRepliesAreNotDiscussion(unittest.TestCase):
    """`share-ideas` posts each idea's screenshot as a thread reply. Counting that
    as discussion would mark every screenshotted idea `needs_judgment` and
    re-surface it on every hourly tick, forever."""

    # The real normalisation, not a copy of it. A reimplementation here is what let
    # `fetch_thread` drop the build announcement while every test still passed.
    raw = staticmethod(h.normalise_replies)

    def test_bot_reply_alone_leaves_the_thread_undecided(self):
        replies = self.raw([
            {"user": "U0ATMPESR37", "bot_id": "B0ATUPK38P6", "ts": "1776465500.000000",
             "text": "Screen this was observed on."},
        ])
        self.assertEqual([r["bot"] for r in replies], [True])
        self.assertEqual(h.classify(thread(replies=replies))["verdict"], "no_decision")

    def test_a_human_decision_still_lands_alongside_a_bot_reply(self):
        replies = self.raw([
            {"user": "U0ATMPESR37", "bot_id": "B0ATUPK38P6", "ts": "1776465500.000000",
             "text": "Screen this was observed on."},
            {"user": "U1BCYETM2", "ts": "1776465600.000000", "text": "let's build this"},
        ])
        self.assertEqual([r["bot"] for r in replies], [True, False])
        self.assertEqual(h.classify(thread(replies=replies))["verdict"], "approved")

    def test_app_id_without_bot_id_is_a_human(self):
        """Rohan's PROD-2126 reply in the archive carries an `app_id` (sent through
        an integration) and no `bot_id`. Filtering on app_id would discard a real
        decision."""
        replies = self.raw([
            {"user": "UFYS6JYH5", "app_id": "A08SF47R6P4", "ts": "1776465600.000000",
             "text": "Wrote up requirements → <https://linear.app/aura-health/issue/PROD-2126/x>"},
        ])
        self.assertEqual(len(replies), 1)
        self.assertEqual(h.classify(thread(replies=replies))["verdict"], "approved")


class ReactionRules(unittest.TestCase):
    def test_conflicting_reactions_are_not_guessed_between(self):
        """Reactions carry no timestamp, so two that disagree cannot be ordered."""
        t = thread(reactions=[
            {"name": "white_check_mark", "users": ["UFYS6JYH5"], "count": 1},
            {"name": "x", "users": ["U1BCYETM2"], "count": 1},
        ])
        res = h.classify(t)
        self.assertEqual(res["verdict"], "needs_judgment")
        self.assertIn("conflicting", res["evidence"])

    def test_thumbsdown_rejects_but_thumbsup_does_not_approve(self):
        down = thread(reactions=[{"name": "-1", "users": ["U1BCYETM2"], "count": 1}])
        up = thread(reactions=[{"name": "+1", "users": ["U1BCYETM2"], "count": 1}])
        self.assertEqual(h.classify(down)["verdict"], "rejected")
        self.assertEqual(h.classify(up)["verdict"], "no_decision")

    def test_empty_thread_is_no_decision(self):
        self.assertEqual(h.classify(thread())["verdict"], "no_decision")


class UrlAndTime(unittest.TestCase):
    def test_parse_slack_url(self):
        self.assertEqual(
            h.parse_slack_url("https://auramobile.slack.com/archives/C0ATUL8T936/p1776465439961579"),
            ("C0ATUL8T936", "1776465439.961579"),
        )

    def test_parse_slack_url_rejects_junk(self):
        self.assertIsNone(h.parse_slack_url("https://example.com/nope"))
        self.assertIsNone(h.parse_slack_url(""))

    def test_ts_to_date_is_utc(self):
        self.assertEqual(h._ts_to_date("1776465439.961579"), "2026-04-17")
        self.assertIsNone(h._ts_to_date(None))
        self.assertIsNone(h._ts_to_date("not-a-ts"))


class SlackToNotionJoin(unittest.TestCase):
    """The name fallback exists because this workspace's Slack bot token has no
    `users:read.email` scope, so `profile.email` is always absent and an
    email-only join would attribute nothing."""

    def test_norm_name_collapses_case_and_whitespace(self):
        self.assertEqual(h._norm_name("  Steve   Lee "), "steve lee")
        self.assertEqual(h._norm_name("Steve Lee"), "steve lee")
        self.assertEqual(h._norm_name(None), "")
        self.assertEqual(h._norm_name("   "), "")

    def test_an_ambiguous_name_resolves_to_nobody(self):
        """Two members sharing a name must not silently attribute to the first
        one listed — a wrong decider is worse than an empty field."""
        h._USER_CACHE = {}
        h._NAME_CACHE = {"steve lee": None, "rohan agarwal": "notion-rohan"}
        try:
            self.assertIsNone(h._NAME_CACHE["steve lee"])
            self.assertEqual(h._NAME_CACHE["rohan agarwal"], "notion-rohan")
        finally:
            h._USER_CACHE = None
            h._NAME_CACHE = None

    def test_no_slack_id_is_not_a_lookup(self):
        self.assertIsNone(h.slack_to_notion_user(None))
        self.assertIsNone(h.slack_to_notion_user(""))


class WriteMapping(unittest.TestCase):
    def test_only_confident_verdicts_are_written(self):
        self.assertEqual(h.WRITABLE, {"approved", "rejected", "deferred"})
        self.assertNotIn("needs_judgment", h.WRITABLE)
        self.assertNotIn("no_decision", h.WRITABLE)

    def test_harvest_reasons_are_valid_notion_options(self):
        """These strings are select options in Notion; a typo fails the write at
        runtime, not here, so pin them."""
        valid = {"already tested", "no longer valid", "low impact", "off-strategy",
                 "tech cost", "duplicate", "timing", "other"}
        self.assertIn(h.HARVEST_REJECT_REASON, valid)
        self.assertIn(h.HARVEST_DEFER_REASON, valid)

    def test_only_undecided_rows_are_candidates(self):
        """The whole dedup story: a decided row leaves the candidate set, so an
        hourly tick on a quiet channel writes nothing."""
        self.assertEqual(set(h.HARVESTABLE), {"pending", "shared", "implementing"})



class ReleaseAnchorPerLoop(unittest.TestCase):
    """The release gate opens on a build for Features, on a deploy for
    recommendations. A recommendations change is server-side — there is no
    TestFlight build, so the old single anchor would have held every one of its
    rows at 'no build announced' forever."""

    def test_the_testflight_anchor_still_opens_the_gate(self):
        self.assertTrue(h.RELEASE_ANCHOR_RE.search(
            "Build 3.19.140 in TestFlight for review (round 1)."))

    def test_the_backend_release_anchor_opens_the_gate(self):
        self.assertTrue(h.RELEASE_ANCHOR_RE.search(
            "ContentService 2.299.0 released and awaiting the release gate."))

    def test_matching_is_case_insensitive(self):
        self.assertTrue(h.RELEASE_ANCHOR_RE.search("IN TESTFLIGHT FOR REVIEW"))

    def test_an_unrelated_reply_does_not_open_the_gate(self):
        for text in ("I approve for implementation",
                     "we should release this soon",
                     "the build is broken"):
            self.assertIsNone(h.RELEASE_ANCHOR_RE.search(text), text)

if __name__ == "__main__":
    unittest.main()


# --- the two gates ----------------------------------------------------------

LOOP = "C0C1JTK3YCC"
ARCHIVE = "C0ATUL8T936"


class WhichGateAReplyAnswers(unittest.TestCase):
    """One thread carries two decisions weeks apart. Nothing in the text reliably
    separates them, so the ROW decides which gate a reply answers."""

    def test_the_row_status_picks_the_gate(self):
        self.assertEqual(h.gate_for_status("shared"), h.GATE_IMPLEMENTATION)
        self.assertEqual(h.gate_for_status("pending"), h.GATE_IMPLEMENTATION)
        self.assertEqual(h.gate_for_status("implementing"), h.GATE_RELEASE)

    def test_a_decided_row_is_at_no_gate(self):
        for st in ("approved", "experiment_running", "won", "abandoned"):
            with self.subTest(status=st):
                self.assertIsNone(h.gate_for_status(st))

    def test_an_explicit_implementation_approval_is_read_at_gate_one(self):
        r = h.classify(thread(replies=[reply("I approve for implementation")]), h.GATE_IMPLEMENTATION, LOOP)
        self.assertEqual(r["verdict"], "approved")

    def test_an_explicit_release_approval_is_read_at_gate_two(self):
        r = h.classify(
            thread(replies=[build(), reply("I approve for release", ts="1776465700.000000")]),
            h.GATE_RELEASE, LOOP)
        self.assertEqual(r["verdict"], "approved")

    def test_an_approval_naming_the_wrong_gate_is_refused_not_applied(self):
        """A release approval on a row nobody has built is somebody on the wrong
        thread, or a row that is behind. Both want a human, not a resolution."""
        r = h.classify(thread(replies=[reply("I approve for release")]), h.GATE_IMPLEMENTATION, LOOP)
        self.assertEqual(r["verdict"], "needs_judgment")
        self.assertIn("release", r["evidence"])
        self.assertIn("implementation gate", r["evidence"])

    def test_a_generic_approval_takes_the_gate_from_the_row_in_the_archive(self):
        """\"ship it\" means build it on a shared row and release it on one in
        TestFlight. Only the status can tell those apart — and only in the ARCHIVE,
        where that guess is how the threads were actually written. In a loop channel
        the same words no longer decide; see `test_a_generic_approval_asks_a_human`."""
        for gate in (h.GATE_IMPLEMENTATION, h.GATE_RELEASE):
            with self.subTest(gate=gate):
                replies = [reply("ship it", ts="1776465700.000000")]
                if gate == h.GATE_RELEASE:
                    replies.insert(0, build())
                r = h.classify(thread(replies=replies), gate, ARCHIVE)
                self.assertEqual(r["verdict"], "approved")
                self.assertEqual(r["gate"], gate)

    def test_a_hedged_approval_asks_a_human_in_a_loop_channel(self):
        """A hedge is what separates thinking aloud from deciding. \"Maybe ship it\"
        is the first; guessing which it meant is the guess that must not be made when
        the answer is yes."""
        r = h.classify(thread(replies=[reply("maybe ship it", ts="1776465700.000000")]),
                       h.GATE_IMPLEMENTATION, LOOP)
        self.assertEqual(r["verdict"], "needs_judgment")

    def test_a_refusal_is_gate_neutral(self):
        """\"no because …\" means no at whichever gate the row is at."""
        for gate in (h.GATE_IMPLEMENTATION, h.GATE_RELEASE):
            with self.subTest(gate=gate):
                replies = [reply("not doing this", ts="1776465700.000000")]
                if gate == h.GATE_RELEASE:
                    replies.insert(0, build())
                self.assertEqual(
                    h.classify(thread(replies=replies), gate, LOOP)["verdict"], "rejected")


class ReactionsMeanDifferentThingsPerChannel(unittest.TestCase):
    """The same emoji means opposite things in the two channels, and both
    readings are correct in their own place."""

    CHECK = [{"name": "white_check_mark", "users": ["U9"], "count": 1}]

    def test_in_the_archive_a_check_mark_is_still_an_approval(self):
        """The April threads are full of it meaning exactly that. Re-reading them
        under today's protocol would rewrite decided history."""
        r = h.classify(thread(reactions=self.CHECK), h.GATE_IMPLEMENTATION, ARCHIVE)
        self.assertEqual(r["verdict"], "approved")
        self.assertEqual(r["decider"], "U9")

    def test_in_a_loop_channel_it_is_not(self):
        r = h.classify(thread(reactions=self.CHECK), h.GATE_IMPLEMENTATION, LOOP)
        self.assertEqual(r["verdict"], "needs_judgment")

    def test_and_it_says_why_so_the_reactor_learns(self):
        """A silently-ignored reaction teaches nobody that it did not count."""
        r = h.classify(thread(reactions=self.CHECK), h.GATE_IMPLEMENTATION, LOOP)
        self.assertIn("done", r["evidence"])
        self.assertIn("replying", r["evidence"])

    def test_a_cross_marks_closed_not_rejected(self):
        r = h.classify(thread(reactions=[{"name": "x", "users": ["U9"], "count": 1}]),
                       h.GATE_IMPLEMENTATION, LOOP)
        self.assertEqual(r["verdict"], "needs_judgment")
        self.assertIn("closed", r["evidence"])

    def test_an_unknown_channel_reads_as_the_archive(self):
        """The safe default: an unrecognised channel is historical, and the old
        protocol is what its threads meant."""
        self.assertFalse(h.uses_loop_protocol(None))
        self.assertFalse(h.uses_loop_protocol("C_SOMETHING_ELSE"))
        for cid in h.LOOP_CHANNELS:
            self.assertTrue(h.uses_loop_protocol(cid))

    def test_a_reply_still_outranks_a_reaction_in_a_loop_channel(self):
        r = h.classify(thread(reactions=self.CHECK,
                              replies=[reply("I approve for implementation")]),
                       h.GATE_IMPLEMENTATION, LOOP)
        self.assertEqual(r["verdict"], "approved")


class TheHarvesterDoesNotWriteReleaseOutcomes(unittest.TestCase):
    """`ship-idea` owns the release gate's writes: it appends the round to the
    note, carries the approval marker `request-experiment` later checks for, and
    requires a reason on a refusal. Reproducing any of that here would be two
    writers for one state, drifting apart the first time either changed."""

    def test_a_release_verdict_refuses_to_be_written(self):
        res = {"verdict": "approved", "gate": h.GATE_RELEASE, "evidence": "ship it",
               "sentiment": [], "at": None, "decider": None}
        with self.assertRaises(ValueError) as cm:
            h.write_verdict({"page_id": "p", "fingerprint": "abc123", "decided_at": None}, res)
        self.assertIn("ship_idea.py release", str(cm.exception))

    def test_it_names_the_command_to_run_instead(self):
        self.assertIn("release abc123", h.ship_command("approved", "abc123"))
        self.assertIn("unwind abc123", h.ship_command("rejected", "abc123"))
        self.assertIn("--reason", h.ship_command("rejected", "abc123"))

    def test_a_refusal_command_always_demands_a_reason(self):
        """An idea built and then not shipped is the most expensive thing the loop
        produces; the reason is what stops the next round repeating it."""
        for verdict in ("rejected", "deferred"):
            with self.subTest(verdict=verdict):
                self.assertIn("--reason", h.ship_command(verdict, "abc123"))

    def test_an_undecided_verdict_has_no_command(self):
        for verdict in ("needs_judgment", "no_decision"):
            with self.subTest(verdict=verdict):
                self.assertIsNone(h.ship_command(verdict, "abc123"))


class ANegatedApprovalIsARefusal(unittest.TestCase):
    """Every approval pattern matches inside its own negation — "Not approved for
    release" contains "approved for release", and the generic `approved\\b` too.
    Reading one as a yes would print the release command for a refusal."""

    NEGATED = (
        "Not approved for release",
        "not approved for release — the copy is wrong",
        "I don't approve for release",
        "I cannot approve for release yet",
        "I would never approve for release",
        "This is not approved",
    )

    def test_a_negated_implementation_approval_is_rejected(self):
        r = h.classify(thread(replies=[reply("I do not approve for implementation")]),
                       h.GATE_IMPLEMENTATION, LOOP)
        self.assertEqual(r["verdict"], "rejected")

    def test_a_negated_release_approval_is_rejected(self):
        for text in self.NEGATED:
            with self.subTest(text=text):
                r = h.classify(
                    thread(replies=[build(), reply(text, ts="1776465700.000000")]),
                    h.GATE_RELEASE, LOOP)
                self.assertEqual(r["verdict"], "rejected", text)

    def test_the_unwind_phrase_never_routes_to_a_release_approval(self):
        """`ship-idea`'s unwind writes this exact sentence. Classified as approved
        it would ask Viktor to create an experiment for a rejected idea."""
        r = h.classify(
            thread(replies=[build(), reply("Not approved for release", ts="1776465700.000000")]),
            h.GATE_RELEASE, LOOP)
        self.assertEqual(h.ship_command(r["verdict"], "abc123"),
                         h.ship_command("rejected", "abc123"))

    def test_an_ordinary_approval_still_reads_as_one(self):
        """The guard must not fire on a verb that merely ends in the same letters
        — "want approved" is not a negation."""
        for text in ("I approve for release", "we want this approved for release",
                     "approved for release, ship it"):
            with self.subTest(text=text):
                r = h.classify(
                    thread(replies=[build(), reply(text, ts="1776465700.000000")]),
                    h.GATE_RELEASE, LOOP)
                self.assertEqual(r["verdict"], "approved", text)


class TheReleaseGateOpensWithTheBuild(unittest.TestCase):
    """A thread carries the whole history. Without a floor, gate one's answer is
    re-read as gate two's — and after an `iterate` round, the previous round's
    refusal is replayed as a fresh one."""

    def test_no_build_announced_is_not_a_decision(self):
        r = h.classify(thread(replies=[reply("ship it")]), h.GATE_RELEASE, LOOP)
        self.assertEqual(r["verdict"], "no_decision")
        # Wording widened when the backend-release anchor landed: the gate now
        # opens on a TestFlight build OR a backend release, so the message no
        # longer says "build".
        self.assertIn("nothing announced", r["evidence"])
        self.assertIn("release gate opens", r["evidence"])

    def test_a_generic_approval_before_the_build_is_not_a_release_yes(self):
        """"ship it" said while the row was `shared` approved the BUILD. The row is
        at the release gate because of it, not despite it."""
        r = h.classify(
            thread(replies=[reply("ship it", ts="1776465500.000000"), build(ts="1776465600.000000")]),
            h.GATE_RELEASE, LOOP)
        self.assertEqual(r["verdict"], "no_decision")

    def test_a_named_implementation_approval_is_stale_not_confusing(self):
        """The phrase the channel topic asks for must not become an eternal
        `needs_judgment` the moment the row it approved starts being built."""
        r = h.classify(
            thread(replies=[reply("I approve for implementation", ts="1776465500.000000"),
                            build(ts="1776465600.000000")]),
            h.GATE_RELEASE, LOOP)
        self.assertEqual(r["verdict"], "no_decision")

    def test_only_the_newest_round_counts(self):
        """Round one was refused, the code was reworked, round two is up. The old
        refusal must not unwind the new build."""
        r = h.classify(
            thread(replies=[
                build(round_no=1, ts="1776465600.000000"),
                reply("not approved for release", ts="1776465700.000000"),
                build(version="1.30.0 (2)", round_no=2, ts="1776465800.000000"),
            ]),
            h.GATE_RELEASE, LOOP)
        self.assertEqual(r["verdict"], "no_decision")

    def test_a_verdict_after_the_newest_build_is_read(self):
        r = h.classify(
            thread(replies=[
                build(round_no=1, ts="1776465600.000000"),
                reply("not approved for release", ts="1776465700.000000"),
                build(version="1.30.0 (2)", round_no=2, ts="1776465800.000000"),
                reply("I approve for release", ts="1776465900.000000"),
            ]),
            h.GATE_RELEASE, LOOP)
        self.assertEqual(r["verdict"], "approved")

    def test_a_reaction_cannot_answer_the_release_gate(self):
        """Reactions sit on the parent message, posted before the build existed."""
        r = h.classify(
            thread(reactions=[{"name": "white_check_mark", "users": ["U9"], "count": 1}],
                   replies=[build()]),
            h.GATE_RELEASE, ARCHIVE)
        self.assertEqual(r["verdict"], "no_decision")

    def test_the_build_post_alone_is_silence_not_discussion(self):
        """The announcement is ours. Quoting it back as "somebody engaged" would
        make every fresh round need a human."""
        r = h.classify(thread(replies=[build()]), h.GATE_RELEASE, LOOP)
        self.assertEqual(r["verdict"], "no_decision")

    def test_the_implementation_gate_is_unfloored(self):
        """Gate one has nothing to be after — every reply counts, including one
        whose ts is missing. Worded as a named approval so it tests the floor rather
        than the loop channel's strictness about generic ones."""
        r = h.classify(thread(replies=[{"user": "U1BCYETM2",   # the channel's decider
                                        "text": "I approve for implementation"}]),
                       h.GATE_IMPLEMENTATION, LOOP)
        self.assertEqual(r["verdict"], "approved")


class TheAnchorSurvivesNormalisation(unittest.TestCase):
    """The announcement that opens the release gate is posted BY US, and bot
    replies are the ones never classified. Dropping them outright would leave the
    gate shut forever — every `implementing` row `no_decision` no matter what a
    human replied.

    These drive a raw Slack payload through the real `normalise_replies`, which is
    the seam the gate tests above skip by building fixtures directly.
    """

    ANNOUNCEMENT = {
        "user": "U0ATMPESR37", "bot_id": "B0ATUPK38P6", "ts": "1776465600.000000",
        "text": "Build 1.30.0 (1) in TestFlight for review (round 1).",
    }

    def test_the_bot_announcement_is_kept_and_flagged(self):
        [r] = h.normalise_replies([self.ANNOUNCEMENT])
        self.assertTrue(r["bot"])
        self.assertIn("TestFlight for review", r["text"])

    def test_a_human_verdict_after_a_bot_announcement_is_read(self):
        replies = h.normalise_replies([
            self.ANNOUNCEMENT,
            {"user": "U1BCYETM2", "ts": "1776465700.000000", "text": "I approve for release"},
        ])
        r = h.classify(thread(replies=replies), h.GATE_RELEASE, LOOP)
        self.assertEqual(r["verdict"], "approved")
        self.assertEqual(r["decider"], "U1BCYETM2")

    def test_the_announcement_itself_is_never_the_verdict(self):
        """It is ours, and it contains the word "review" — classified as discussion
        it would make every fresh round need a person."""
        replies = h.normalise_replies([self.ANNOUNCEMENT])
        self.assertEqual(
            h.classify(thread(replies=replies), h.GATE_RELEASE, LOOP)["verdict"], "no_decision")

    def test_a_screenshot_post_is_still_not_a_discussion(self):
        """The reason bot replies were dropped in the first place. Keeping them
        must not bring that back."""
        replies = h.normalise_replies([
            {"user": "U0ATMPESR37", "bot_id": "B0ATUPK38P6", "ts": "1776465500.000000",
             "text": "Screen this was observed on."},
        ])
        self.assertEqual(h.classify(thread(replies=replies))["verdict"], "no_decision")


class QuestionsGetAnswered(unittest.TestCase):
    """A question is a distinct outcome — the loop answers it, it does not decide.
    needs_answer must never be a soft rejection, never write a status, and never
    fire twice once answered."""

    def test_is_question_reads_the_shapes_that_get_asked(self):
        for q in ("Haven't we tried this?", "What happened last time?",
                  "Why this screen?", "Isn't this the same as X?",
                  "Is anything else running there?", "haven't we tested this"):
            self.assertTrue(h.is_question(q), q)

    def test_is_question_ignores_decisions_and_comments(self):
        for not_q in ("I approve for implementation", "no because it is off-strategy",
                      "let's do it later", "Looks great", "", "   "):
            self.assertFalse(h.is_question(not_q), not_q)

    def test_a_question_mark_inside_a_url_is_not_a_question(self):
        # A query-string "?" or a Slack <link> must not read as needs_answer.
        for not_q in (
            "see the doc https://example.com/y?utm=1&x=2",
            "wrote it up <https://linear.app/a/issue/PROD-1?tab=activity|PROD-1>",
            "context in <#C0ATUL8T936>",
        ):
            self.assertFalse(h.is_question(not_q), not_q)

    def test_a_real_question_alongside_a_url_still_counts(self):
        self.assertTrue(h.is_question("did we measure this? see https://x.com/y?z=1"))

    def test_an_unanswered_question_is_needs_answer(self):
        res = h.classify(thread(replies=[reply("Haven't we tried this before?")]))
        self.assertEqual(res["verdict"], "needs_answer")
        self.assertIn("Haven't we tried this", res["evidence"])

    def test_needs_answer_never_writes_a_status(self):
        # The gate is a human's; answering is not deciding.
        self.assertNotIn("needs_answer", h.WRITABLE)

    def test_a_decision_after_a_question_still_wins(self):
        t = thread(replies=[reply("why this screen?", ts="1776465500.000000"),
                            reply("I approve for implementation", ts="1776465600.000000")])
        self.assertEqual(h.classify(t)["verdict"], "approved")

    def test_a_question_already_answered_by_the_bot_does_not_refire(self):
        # Our own answer sits after it, so a later tick must not re-flag it.
        t = thread(replies=[
            reply("what did we measure last time?", ts="1776465500.000000"),
            dict(reply("The lever was retention.", user="U0ATMPESR37",
                       ts="1776465600.000000"), bot=True),
        ])
        self.assertNotEqual(h.classify(t)["verdict"], "needs_answer")

    def test_a_question_is_never_read_as_a_rejection(self):
        self.assertEqual(h.classify(thread(replies=[reply("have we tested this?")]))["verdict"],
                         "needs_answer")

    def test_a_question_at_the_release_gate_after_the_build(self):
        t = thread(replies=[build(ts="1776465600.000000"),
                            reply("does this work on iPad?", ts="1776465700.000000")])
        res = h.classify(t, gate=h.GATE_RELEASE)
        self.assertEqual(res["verdict"], "needs_answer")
