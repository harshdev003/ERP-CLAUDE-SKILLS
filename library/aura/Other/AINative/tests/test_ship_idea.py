"""Guard tests for `product:ship-idea`.

The ship leg writes one status transition per command, and the transitions are
the log the whole loop exists to produce. A wrong one is not loud: the run
succeeds, the row reads plausibly, and the error only surfaces when somebody
later asks "when did this ship?" and the answer is wrong.

So these cover the pure decision functions — the transition guard, the version
shape, the permalink parse, and the request text — with no network.
"""

from __future__ import annotations

import os
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(REPO, "plugins", "product", "skills", "ship-idea", "scripts")
sys.path.insert(0, SCRIPTS)

import ship_idea as s  # noqa: E402
s_mod = s


def row(status, **kw):
    base = {
        "page_id": "p", "title": "Surface session streak in Settings",
        "fingerprint": "ffe899463174", "status": status,
        "screen": "settings_screen", "domain": "settings",
        "description": "Show the user's current streak on the Settings screen.",
        "hypothesis": "Visible progress raises weekly return rate.",
        "lever": "retention", "experiment": "aiSettingsStreak",
        "note": "", "slack": "", "linear": "", "pr": "",
    }
    base.update(kw)
    return base


class TransitionGuard(unittest.TestCase):
    """The machine branches now, so this is read from REQUIRED_FROM rather than
    from a position in a sequence. Position arithmetic would have to call
    shipped_dormant -> implementing a backward move and refuse it — and that move
    is the whole point of shipping dormant."""

    def test_each_status_accepts_exactly_its_predecessors(self):
        for to, froms in s.REQUIRED_FROM.items():
            for frm in froms:
                with self.subTest(frm=frm, to=to):
                    self.assertIsNone(s.guard_transition(row(frm), to))

    def test_a_dormant_idea_can_be_picked_back_up(self):
        """The one legal backward move. The code is already in the build, so the
        next round is an edit rather than a rebuild."""
        self.assertIsNone(s.guard_transition(row(s.DORMANT), s.IMPLEMENTING))

    def test_but_an_abandoned_one_cannot(self):
        """Abandoned is terminal on purpose — reviving a row would lose the record
        that it was dropped. A successor idea is the way back."""
        self.assertIsNotNone(s.guard_transition(row(s.ABANDONED), s.IMPLEMENTING))

    def test_live_is_only_reachable_from_implementing(self):
        for frm in ("approved", s.DORMANT, s.ABANDONED, "shared", "won"):
            with self.subTest(frm=frm):
                self.assertIsNotNone(s.guard_transition(row(frm), s.LIVE))

    def test_a_live_idea_cannot_be_unwound(self):
        """Once users are assigned, the exit is a result, not a gate decision."""
        self.assertIsNotNone(s.guard_transition(row(s.LIVE), s.DORMANT))

    def test_rerunning_the_same_step_is_named_as_a_no_op_not_an_error(self):
        msg = s.guard_transition(row(s.IMPLEMENTING), s.IMPLEMENTING)
        self.assertIn("already", msg)

    def test_a_refusal_says_what_the_row_is_waiting_for(self):
        """\"Wrong status\" sends the reader to the database. Naming what is owed
        sends them to the thread, which is where the answer is."""
        msg = s.guard_transition(row("shared"), s.LIVE)
        self.assertIn("approved it for implementation", msg)

    def test_every_status_in_the_vocabulary_has_a_waiting_hint(self):
        """A status with no hint produces a bare refusal, which is the version
        nobody can act on."""
        for status in s.REQUIRED_FROM:
            with self.subTest(status=status):
                self.assertIn(status, s.WAITING_ON)


class RoundTracking(unittest.TestCase):
    """An idea can go through the gate several times. \"We tried it and it was
    wrong\" is unreadable later without knowing which build \"it\" was."""

    def test_rounds_accumulate_rather_than_replace(self):
        note = ""
        for line in ("Build 1.30.0 (1) in TestFlight for review (round 1).",
                     "Not approved for release: copy reads abrupt.",
                     "Iterating: shorten the second line."):
            note = s.append_round(note, line)
        self.assertEqual(s.count_rounds(note), 3)
        self.assertIn("round 1", note)
        self.assertIn("abrupt", note)

    def test_an_existing_note_survives_the_first_round(self):
        """The note already carries the decision that got the idea built. Losing
        it to a status write is how a third attempt repeats the first."""
        note = s.append_round("Approved by Steve, quiz-results angle.", "Build 1.30.0 (1).")
        self.assertIn("Approved by Steve", note)

    def test_a_note_with_no_rounds_counts_zero(self):
        self.assertEqual(s.count_rounds("Approved by Steve."), 0)
        self.assertEqual(s.count_rounds(""), 0)


class TheGateIsNotAdvisory(unittest.TestCase):
    """`request-experiment` refuses until someone has approved the release.
    Without that check the second gate would be a suggestion: nothing would stop
    an experiment being requested for a build no human ever opened."""

    def test_an_unapproved_note_is_refused(self):
        self.assertFalse(s.release_approved(""))
        self.assertFalse(s.release_approved("• Build 1.30.0 (1) in TestFlight for review."))

    def test_the_marker_release_writes_is_the_marker_request_reads(self):
        note = s.append_round("", f"{s.RELEASE_APPROVED} (Steve). Ship it.")
        self.assertTrue(s.release_approved(note))

    def test_it_survives_later_rounds(self):
        note = s.append_round("", f"{s.RELEASE_APPROVED} (Steve).")
        note = s.append_round(note, "Build 1.30.1 (1) in TestFlight.")
        self.assertTrue(s.release_approved(note))

    def test_the_two_verdicts_do_not_contain_one_another(self):
        """An earlier pair was "Approved for release" and "Not approved for
        release", so a substring test read every rejection as an approval."""
        a, d = s.RELEASE_APPROVED.lower(), s.RELEASE_DECLINED.lower()
        self.assertNotIn(a, d)
        self.assertNotIn(d, a)

    def test_a_decline_is_never_read_as_an_approval(self):
        note = s.append_round("", f"{s.RELEASE_DECLINED}: copy reads abrupt. "
                                  f"Code shipped with the experiment off.")
        self.assertEqual(s.last_gate_decision(note), "declined")
        self.assertFalse(s.release_approved(note))

    def test_iterating_after_a_decline_does_not_reopen_the_gate(self):
        """The exact bypass: decline, iterate back to implementing, then request
        an experiment for something that was explicitly turned down."""
        note = s.append_round("", f"{s.RELEASE_DECLINED}: not shipping this.")
        note = s.append_round(note, "Iterating: shortened the second line.")
        note = s.append_round(note, "Build 1.30.1 (1) in TestFlight for review (round 2).")
        self.assertFalse(s.release_approved(note))

    def test_the_latest_verdict_wins_in_both_directions(self):
        declined_then_approved = s.append_round(
            s.append_round("", f"{s.RELEASE_DECLINED}: too abrupt."),
            f"{s.RELEASE_APPROVED} (Steve).")
        self.assertTrue(s.release_approved(declined_then_approved))

        approved_then_declined = s.append_round(
            s.append_round("", f"{s.RELEASE_APPROVED} (Steve)."),
            f"{s.RELEASE_DECLINED}: regression found in QA.")
        self.assertFalse(s.release_approved(approved_then_declined))


class TheMinimumVersionIsTheBuildThatPassed(unittest.TestCase):
    """Rounds accumulate, so a twice-gated note names two builds. Taking the
    first would send the REJECTED build as the experiment's minimum version —
    enabling it for the users who have the version that was turned down, and
    excluding the ones who have the fix."""

    def test_the_last_build_wins(self):
        note = s.append_round("", "Build 1.30.0 (1) in TestFlight for review (round 1).")
        note = s.append_round(note, f"{s.RELEASE_DECLINED}: copy reads abrupt.")
        note = s.append_round(note, "Iterating: shortened it.")
        note = s.append_round(note, "Build 1.30.1 (2) in TestFlight for review (round 2).")
        self.assertEqual(s._version_from_note(note), "1.30.1 (2)")

    def test_a_single_build_still_works(self):
        note = s.append_round("", "Build 1.30.0 (1) in TestFlight for review (round 1).")
        self.assertEqual(s._version_from_note(note), "1.30.0 (1)")

    def test_no_build_is_empty_not_a_guess(self):
        self.assertEqual(s._version_from_note("Approved by Steve."), "")
        self.assertEqual(s._version_from_note(""), "")


class VersionShape(unittest.TestCase):
    def test_accepts_what_the_release_skills_actually_produce(self):
        for v in ("1.30.0 (1)", "1.30.0(2)", "1.30.0-1", "3.19.150", "5.0.0 (12)"):
            with self.subTest(v=v):
                self.assertTrue(s.VERSION_RE.match(v), v)

    def test_refuses_free_text(self):
        """A later reader uses this to find the build; prose makes it useless."""
        for v in ("latest", "v1.30.0", "1.30", "next TestFlight", "1.30.0 build 1", ""):
            with self.subTest(v=v):
                self.assertFalse(s.VERSION_RE.match(v), v)


class PermalinkParse(unittest.TestCase):
    REAL = "https://auramobile.slack.com/archives/C0ATUL8T936/p1776464801853489"

    def test_splits_the_ts_at_ten_digits(self):
        """Slack rejects a thread_ts in any other shape, and rejects it with a
        generic error that looks like a permissions problem."""
        self.assertEqual(s.parse_permalink(self.REAL), ("C0ATUL8T936", "1776464801.853489"))

    def test_survives_a_thread_query_suffix(self):
        url = self.REAL + "?thread_ts=1776464801.853489&cid=C0ATUL8T936"
        self.assertEqual(s.parse_permalink(url), ("C0ATUL8T936", "1776464801.853489"))

    def test_returns_none_rather_than_guessing(self):
        for junk in ("", None, "https://auramobile.slack.com/archives/C0ATUL8T936",
                     "not a url", "https://example.com/archives/C1/p1"):
            with self.subTest(v=junk):
                self.assertIsNone(s.parse_permalink(junk))


class RequestText(unittest.TestCase):
    def test_carries_every_field_aurabots_grammar_needs(self):
        text = s.render_request(row("released"), "aiSettingsStreak", "1.30.0 (1)", "ai", "ios", "ab")
        for fragment in ("create experiment `aiSettingsStreak`", "with notes",
                         "and variants ab", "and minimum version 1.30.0 (1)",
                         "targeting platform ios", "targetApp ai"):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, text)

    def test_b_is_always_named_control(self):
        """Every real request in the channel names one arm `control`. Without it
        an analyst cannot tell which side is the baseline."""
        text = s.render_request(row("released"), "aiFoo", "1.0.0", "ai", "ios", "ab")
        self.assertIn("b: control", text)

    def test_target_app_is_omitted_entirely_for_aura_not_set_to_something_else(self):
        text = s.render_request(row("released"), "auraFoo", "3.19.150", "", "", "ab")
        self.assertNotIn("targetApp", text)
        self.assertNotIn("targeting platform", text)

    def test_addresses_viktor_not_aurabot(self):
        """Viktor reads prose and answers when a request is off; AuraBot parses a
        fixed grammar and fails silently on a malformed one."""
        text = s.render_request(row("released"), "aiFoo", "1.0.0", "ai", "ios", "ab")
        self.assertIn(f"<@{s.VIKTOR}>", text)

    def test_collapses_newlines_in_the_description(self):
        """The command is one line; a raw newline splits it and AuraBot sees half."""
        multi = row("released", description="line one\nline two\n\nline three")
        text = s.render_request(multi, "aiFoo", "1.0.0", "ai", "ios", "ab")
        self.assertIn("line one line two line three", text)


class NotesShape(unittest.TestCase):
    def test_reads_like_the_real_requests_summary_then_arms(self):
        got = s_mod.render_notes(row("released"))
        self.assertTrue(got.startswith("Surface session streak in Settings."), got)
        self.assertIn("a: Show the user's current streak on the Settings screen, b: control", got)

    def test_never_doubles_a_period(self):
        """The description usually ends in one already."""
        self.assertNotIn("..", s_mod.render_notes(row("released")))
        self.assertNotIn("..", s_mod.render_notes(row("released", description="Ends with a dot.")))

    def test_falls_back_to_the_title_when_there_is_no_description(self):
        got = s_mod.render_notes(row("released", description=""))
        self.assertIn("a: treatment, b: control", got)

    def test_explicit_arms_win(self):
        got = s_mod.render_notes(row("released"), arms="a: new copy, b: control, c: no header")
        self.assertIn("a: new copy, b: control, c: no header", got)


class UndocumentedArms(unittest.TestCase):
    """Auto-notes can only name a and b — nothing on the row says what c or d are.
    Requesting abcd anyway makes an experiment whose extra arms cannot be
    recovered from the result weeks later."""

    def test_ab_needs_nothing(self):
        self.assertEqual(s_mod.missing_arms("ab", None), [])

    def test_abc_and_abcd_are_caught(self):
        self.assertEqual(s_mod.missing_arms("abc", None), ["c"])
        self.assertEqual(s_mod.missing_arms("abcd", None), ["c", "d"])

    def test_explicit_arms_clear_it(self):
        self.assertEqual(s_mod.missing_arms("abcd", "a: x, b: control, c: y, d: z"), [])

    def test_partial_arms_are_still_caught(self):
        """Spelling out three of four is the easy mistake."""
        self.assertEqual(s_mod.missing_arms("abcd", "a: x, b: control, c: y"), ["d"])


class AuraBotFallback(unittest.TestCase):
    """`--via aurabot` for when Viktor is unavailable — and the likely default if
    Viktor is ever retired on cost. It must reproduce the grammar exactly, because
    AuraBot reads the literal text and fails silently on anything else."""

    def cmd(self, **kw):
        args = dict(idea=row("released"), experiment="aiSettingsStreak",
                    version="1.30.0 (1)", target_app="ai", platform="ios", variants="ab")
        args.update(kw)
        return s_mod.render_request(args["idea"], args["experiment"], args["version"],
                                    args["target_app"], args["platform"], args["variants"],
                                    via="aurabot")

    def test_matches_the_shape_of_a_real_request(self):
        """Compared against a real one from #support-bot, field for field."""
        got = self.cmd()
        self.assertTrue(got.startswith(f"<@{s_mod.AURABOT}> create experiment aiSettingsStreak with notes "))
        self.assertIn(" and variants ab and minimum version 1.30.0 (1)", got)
        self.assertTrue(got.endswith(" and targeting platform ios and targetApp ai"))

    def test_is_exactly_one_line(self):
        """A wrapped command is a different command — AuraBot parses the literal."""
        self.assertEqual(len(self.cmd().splitlines()), 1)

    def test_carries_no_markdown(self):
        """Viktor's prose backticks the experiment name; the grammar must not."""
        self.assertNotIn("`", self.cmd())

    def test_omits_both_qualifiers_for_aura(self):
        got = self.cmd(target_app="", platform="")
        self.assertNotIn("targetApp", got)
        self.assertNotIn("targeting platform", got)
        self.assertTrue(got.endswith("and minimum version 1.30.0 (1)"))

    def test_addresses_aurabot_not_viktor(self):
        got = self.cmd()
        self.assertIn(s_mod.AURABOT, got)
        self.assertNotIn(s_mod.VIKTOR, got)

    def test_the_two_paths_agree_on_every_field_that_matters(self):
        """The fallback must not quietly request a different experiment."""
        idea = row("released")
        viktor = s_mod.render_request(idea, "aiFoo", "1.0.0", "ai", "ios", "abc", via="viktor")
        aurabot = s_mod.render_request(idea, "aiFoo", "1.0.0", "ai", "ios", "abc", via="aurabot")
        for field in ("aiFoo", "and variants abc", "and minimum version 1.0.0",
                      "targeting platform ios", "targetApp ai", s_mod.render_notes(idea)):
            with self.subTest(field=field):
                self.assertIn(field, viktor.replace("`", ""))
                self.assertIn(field, aurabot)


class ExperimentExistenceIsTriState(unittest.TestCase):
    def test_no_token_is_unknown_not_absent(self):
        """The dangerous collapse: 'could not check' read as 'not created yet'
        leaves the loop polling a row that is actually live."""
        saved = {k: os.environ.pop(k, None) for k in ("AURA_API_TOKEN", "AURA_ADMIN_TOKEN")}
        try:
            exists, detail = s.experiment_exists("aiFoo")
            self.assertIsNone(exists)
            self.assertIn("AURA_API_TOKEN", detail)
        finally:
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v


class LiveMinimumPrefersTheLaterVersion(unittest.TestCase):
    """The server record decides who is assigned, so a minimum Viktor raised
    past the row's must hold the row back until that release ships."""

    def test_server_raised_minimum_wins(self):
        self.assertEqual(s.live_minimum("1.31.9", "1.32.0"), "1.32.0")

    def test_row_minimum_stands_when_the_server_is_lower_or_missing(self):
        self.assertEqual(s.live_minimum("1.32.0", "1.31.9"), "1.32.0")
        self.assertEqual(s.live_minimum("1.31.9", ""), "1.31.9")

    def test_server_fills_an_empty_row_minimum(self):
        self.assertEqual(s.live_minimum("", "1.32.0"), "1.32.0")

    def test_not_live_while_the_store_is_between_the_two(self):
        self.assertFalse(s.released("1.31.9", s.live_minimum("1.31.9", "1.32.0")))


class PinnedRecordIsNotAnExperiment(unittest.TestCase):
    """A one-arm `active` gives every user that arm, so release must not read as live."""

    def test_single_arm_is_pinned(self):
        self.assertEqual(s.pinned_arm({"active": "a"}), "a")

    def test_a_split_or_missing_record_is_not_pinned(self):
        for rec in ({"active": "ab"}, {"active": "abc"}, {"active": ""}, {}, None):
            with self.subTest(rec=rec):
                self.assertEqual(s.pinned_arm(rec), "")

    def test_the_reply_names_the_arm_and_the_way_back(self):
        text = s.pinned_text("aiFoo", "a", "1.32.0")
        self.assertIn("`aiFoo`", text)
        self.assertIn("pinned to arm `a`", text)
        self.assertIn("back to the split", text)

    def test_the_marker_is_once_per_round(self):
        note = s.append_round("", f"{s.EXPERIMENT_PINNED} to arm a (aiFoo) at release.")
        self.assertTrue(s._since_last_verdict(note, s.EXPERIMENT_PINNED))


class GoingLiveSpeaksInTheThread(unittest.TestCase):
    """The flip to experiment_running replies in the idea's thread once, with the release link."""

    POST = "https://auramobile.slack.com/archives/C0B056LKRAB/p1791532748663819"
    THREAD = "https://auramobile.slack.com/archives/C0C1JTK3YCC/p1791236551851119"

    def test_live_text_links_the_release_post(self):
        self.assertEqual(s.live_text("aiFoo", "1.32.0", post_url=self.POST),
                         f"Live as experiment `aiFoo` in 1.32.0. Release: {self.POST}")

    def test_live_text_reads_the_link_back_from_the_note(self):
        note = f"Live as experiment aiFoo (released 1.32.0 — {self.POST})."
        self.assertIn(self.POST, s.live_text("aiFoo", "1.32.0", note))

    def test_live_text_without_a_known_post_has_no_link(self):
        self.assertEqual(s.live_text("aiFoo", "1.32.0"), "Live as experiment `aiFoo` in 1.32.0.")

    def _flip(self, note=""):
        from unittest import mock  # noqa: PLC0415
        import argparse  # noqa: PLC0415
        idea = row(s.IMPLEMENTING, note=note, slack=self.THREAD, experiment="aiFoo",
                   experiment_rel=[])
        writes, replies = [], []
        with mock.patch.object(s, "find_idea", return_value=idea), \
                mock.patch.object(s, "_require_notion"), \
                mock.patch.object(s, "release_approved", return_value=True), \
                mock.patch.object(s, "experiment_record",
                                  return_value=(True, "HTTP 200", {"active": "ab",
                                                                   "minVersion": "1.32.0"})), \
                mock.patch.object(s, "app_store_version", return_value="1.32.0"), \
                mock.patch.object(s, "find_release_post", return_value=("1.32.0", self.POST)), \
                mock.patch.object(s, "find_experiment_row", return_value=None), \
                mock.patch.object(s, "mark_if_done"), \
                mock.patch.object(s, "reply_in_thread",
                                  side_effect=lambda i, t: replies.append(t) or 0), \
                mock.patch.object(s.notion, "update_page_props",
                                  side_effect=lambda pid, props: writes.append(props)):
            rc = s.cmd_check_experiment(argparse.Namespace(
                data_source_id="x", ref="1", experiment=None,
                target_app=s.TARGET_APP_AURIE, note=None))
        return rc, writes, replies

    def test_the_flip_announces_once_with_the_release_link(self):
        rc, writes, replies = self._flip()
        self.assertEqual(rc, 0)
        self.assertEqual(replies, [f"Live as experiment `aiFoo` in 1.32.0. Release: {self.POST}"])
        last_note = writes[-1]["Quick status note"]["rich_text"][0]["text"]["content"]
        self.assertIn(s.LIVE_ANNOUNCED, last_note)

    def test_an_already_announced_row_is_not_announced_again(self):
        _, _, replies = self._flip(note=f"Approved. {s.LIVE_ANNOUNCED}.")
        self.assertEqual(replies, [])

    def _live_row(self, note, post_rc=0, raises=False):
        from unittest import mock  # noqa: PLC0415
        idea = row(s.LIVE, note=note, slack=self.THREAD, experiment="aiFoo")
        writes, replies = [], []

        def fake_reply(i, t):
            replies.append(t)
            if raises:
                raise SystemExit("curl failed")
            return post_rc
        with mock.patch.object(s, "reply_in_thread", side_effect=fake_reply), \
                mock.patch.object(s.notion, "update_page_props",
                                  side_effect=lambda pid, props: writes.append(props)):
            posted = s.announce_live(idea, "aiFoo", note)
        return posted, writes, replies

    def test_a_failed_post_leaves_the_row_unstamped_so_the_next_check_retries(self):
        for kw in ({"post_rc": 2}, {"raises": True}):
            with self.subTest(**kw):
                posted, writes, replies = self._live_row("Live as experiment aiFoo (released 1.32.0).", **kw)
                self.assertFalse(posted)
                self.assertEqual(len(replies), 1)
                self.assertEqual(writes, [])

    def test_a_live_row_never_announced_is_announced_on_the_next_check(self):
        posted, writes, _ = self._live_row("Live as experiment aiFoo (released 1.32.0).")
        self.assertTrue(posted)
        self.assertIn(s.LIVE_ANNOUNCED, writes[-1]["Quick status note"]["rich_text"][0]["text"]["content"])

    def test_announce_does_not_repost_once_the_thread_has_it(self):
        from unittest import mock  # noqa: PLC0415
        import argparse  # noqa: PLC0415
        idea = row(s.LIVE, note=f"Live. {s.LIVE_ANNOUNCED}.", slack=self.THREAD, experiment="aiFoo")
        with mock.patch.object(s, "find_idea", return_value=idea), \
                mock.patch.object(s, "_require_notion"), \
                mock.patch.object(s, "reply_in_thread") as reply:
            rc = s.cmd_announce(argparse.Namespace(data_source_id="x", ref="1", experiment=None,
                                                   version=None, post=True))
        self.assertEqual(rc, 0)
        reply.assert_not_called()


class VersionFromNote(unittest.TestCase):
    def test_reads_back_what_released_wrote(self):
        self.assertEqual(s._version_from_note("Released in 1.30.0 (1)."), "1.30.0 (1)")
        self.assertEqual(s._version_from_note("Released in 1.30.0-1."), "1.30.0-1")

    def test_absent_is_empty_not_a_guess(self):
        self.assertEqual(s._version_from_note("PR merged: https://github.com/x/y/pull/1"), "")
        self.assertEqual(s._version_from_note(""), "")


class ThreadReplyGuard(unittest.TestCase):
    """A status change speaks in the idea's thread — but only when there is one,
    and only when asked to (`--post`). The refusal path is pure; the post path is
    exercised against a stubbed Slack."""

    THREAD = "https://auramobile.slack.com/archives/C0ATUL8T936/p1776464801853489"

    def test_a_reply_is_refused_when_the_row_has_no_thread(self):
        # An idea never shared has no thread the team is watching — nothing to reply to.
        self.assertEqual(s.reply_in_thread(row(s.IMPLEMENTING, slack=""), "Approved."), 2)

    def test_a_reply_posts_under_the_idea_thread_when_present(self):
        from unittest import mock  # noqa: PLC0415
        import slack_post  # noqa: PLC0415
        captured = {}

        def fake_post(**kw):
            captured.update(kw)
            return {"ok": True, "ts": "1.2"}

        with mock.patch.object(slack_post, "post_message", fake_post):
            rc = s.reply_in_thread(row(s.IMPLEMENTING, slack=self.THREAD),
                                   "Approved for release.")
        self.assertEqual(rc, 0)
        self.assertEqual(captured["channel"], "C0ATUL8T936")
        self.assertEqual(captured["thread_ts"], "1776464801.853489")
        self.assertEqual(captured["text"], "Approved for release.")
        # Attributed to the loop skill, not the shared poster.
        self.assertEqual(captured["footer_skill"], "product:ship-idea")


class CleanupCard(unittest.TestCase):
    """Abandon files its own cleanup card — the one trigger the sweep can't cover,
    since the experiment may never have run."""

    IDEA = {"experiment": "aiSettingsStreak", "screen": "settings_screen",
            "fingerprint": "ffe899463174", "idea_id": "IDEA-42",
            "slack": "https://auramobile.slack.com/archives/C0C1JTK3YCC/p1"}

    def test_the_card_names_the_experiment_flag_and_treatment(self):
        title, body = s.render_cleanup_card(self.IDEA, "Superseded by streak v2.")
        self.assertIn("aiSettingsStreak", title)
        self.assertIn("Experiment flag", body)
        self.assertIn("Treatment path", body)
        self.assertIn("Removal checklist", body)
        self.assertIn("Superseded by streak v2.", body)
        self.assertIn("IDEA-42", body)  # addressed by its stable id, not the fingerprint

    def test_it_lands_in_the_mobile_team_and_aurie_project(self):
        # Same team/project the sweep uses for mobile cleanups, so they match.
        self.assertEqual(s.CLEANUP_CARD_TEAM, "5497338e-312b-4ddf-ac5f-3339399c956d")
        self.assertEqual(s.CLEANUP_CARD_PROJECT, "50b657cd-f184-4068-aec5-a1dbcac49d22")

    def test_file_cleanup_card_returns_the_url_on_success(self):
        from unittest import mock  # noqa: PLC0415
        import subprocess  # noqa: PLC0415

        class R:
            returncode = 0
            stdout = '{"id":"x","identifier":"ENGMOB-1","url":"https://linear.app/e/ENGMOB-1"}'
            stderr = ""
        with mock.patch.object(subprocess, "run", return_value=R()):
            self.assertEqual(s.file_cleanup_card("t", "b"), "https://linear.app/e/ENGMOB-1")

    def test_file_cleanup_card_returns_none_on_failure(self):
        from unittest import mock  # noqa: PLC0415
        import subprocess  # noqa: PLC0415

        class R:
            returncode = 1
            stdout = '{"error":"no_key","message":"...","fallback":"mcp"}'
            stderr = ""
        with mock.patch.object(subprocess, "run", return_value=R()):
            self.assertIsNone(s.file_cleanup_card("t", "b"))

    def test_abandon_reason_is_recovered_from_the_note(self):
        self.assertEqual(s._abandon_reason_from_note("Round 1. Abandoned: superseded by v2."),
                         "superseded by v2.")
        self.assertEqual(s._abandon_reason_from_note("no marker here"), "")

    def test_abandon_retries_the_cleanup_on_an_already_abandoned_row(self):
        # The forward-only guard must NOT block re-filing after a Linear miss.
        from unittest import mock  # noqa: PLC0415
        import argparse  # noqa: PLC0415
        idea = row(s.ABANDONED, note="Abandoned: superseded.", cleanup_issue="",
                   experiment="aiStreak")
        writes = {}
        with mock.patch.object(s, "find_idea", return_value=idea), \
                mock.patch.object(s, "_require_notion"), \
                mock.patch.object(s, "file_cleanup_card",
                                  return_value="https://linear.app/e/ENGMOB-9") as filed, \
                mock.patch.object(s.notion, "update_page_props",
                                  side_effect=lambda pid, props: writes.update(props)):
            rc = s.cmd_abandon(argparse.Namespace(
                data_source_id="x", ref="1", reason=None, file_cleanup=True, post=False))
        self.assertEqual(rc, 0)                       # not refused
        filed.assert_called_once()                    # the card was filed
        self.assertEqual(writes["Cleanup issue"]["url"], "https://linear.app/e/ENGMOB-9")

    def test_abandon_retry_does_not_refile_when_already_linked(self):
        from unittest import mock  # noqa: PLC0415
        import argparse  # noqa: PLC0415
        idea = row(s.ABANDONED, note="Abandoned: x.",
                   cleanup_issue="https://linear.app/e/ENGMOB-1", experiment="aiStreak")
        with mock.patch.object(s, "find_idea", return_value=idea), \
                mock.patch.object(s, "_require_notion"), \
                mock.patch.object(s, "file_cleanup_card") as filed:
            rc = s.cmd_abandon(argparse.Namespace(
                data_source_id="x", ref="1", reason=None, file_cleanup=True, post=False))
        self.assertEqual(rc, 0)
        filed.assert_not_called()                     # idempotent — no second card


if __name__ == "__main__":
    unittest.main()


class TheMergeIsTheReleaseApproval(unittest.TestCase):
    """`merged` records the approval; `release_version_for` picks the version."""

    def _run(self, tags, containing):
        from unittest import mock  # noqa: PLC0415
        import json  # noqa: PLC0415
        import subprocess  # noqa: PLC0415

        def fake(cmd, **_kw):
            class R:
                returncode = 0
                stderr = ""
            r = R()
            if "--paginate" in cmd:
                r.stdout = "\n".join(tags)
            else:
                tag = cmd[2].split("...")[-1]
                r.stdout = json.dumps({"status": "ahead" if tag in containing else "diverged"})
            return r
        with mock.patch.object(subprocess, "run", side_effect=fake):
            return s.release_version_for("https://github.com/o/r/pull/1", "abc")

    def test_before_the_release_is_cut_it_is_the_next_patch(self):
        # Name order puts v-prefixed tags first; version order must win.
        self.assertEqual(self._run(["v1.29.0-1", "1.31.8-1", "1.31.3-1"], set()), "1.31.9")

    def test_once_tagged_it_is_the_earliest_release_carrying_the_merge(self):
        self.assertEqual(self._run(["1.32.0-1", "1.31.9-1", "1.31.8-1"],
                                   {"1.32.0-1", "1.31.9-1"}), "1.31.9")

    def test_an_unreadable_compare_is_empty_not_a_guess(self):
        from unittest import mock  # noqa: PLC0415
        import subprocess  # noqa: PLC0415

        def fake(cmd, **_kw):
            class R:
                stderr = ""
            r = R()
            r.returncode, r.stdout = (0, "1.31.8-1") if "--paginate" in cmd else (1, "")
            return r
        with mock.patch.object(subprocess, "run", side_effect=fake):
            self.assertEqual(s.release_version_for("https://github.com/o/r/pull/1", "abc"), "")

    def test_the_merge_round_satisfies_the_gate_and_carries_the_version(self):
        note = s.append_round("", f"{s.RELEASE_APPROVED} (PR merged by jc on 2026-10-06). "
                                  f"Ships in 1.31.9.")
        self.assertTrue(s.release_approved(note))
        self.assertEqual(s._version_from_note(note), "1.31.9")

    def test_a_request_is_recorded_once(self):
        note = s.append_round("", f"{s.RELEASE_APPROVED} (PR merged).")
        self.assertFalse(s.experiment_requested(note))
        note = s.append_round(note, f"{s.EXPERIMENT_REQUESTED} (x, minimum version 1.31.9).")
        self.assertTrue(s.experiment_requested(note))
        self.assertTrue(s.release_approved(note))

    def test_a_new_approval_needs_a_new_request(self):
        note = s.append_round("", f"{s.RELEASE_APPROVED} (PR merged).")
        note = s.append_round(note, f"{s.EXPERIMENT_REQUESTED} (x, minimum version 1.31.9).")
        note = s.append_round(note, f"{s.RELEASE_DECLINED}: copy reads abrupt.")
        note = s.append_round(note, f"{s.RELEASE_APPROVED} (PR merged).")
        self.assertFalse(s.experiment_requested(note))


class CreatedIsNotLive(unittest.TestCase):
    def test_the_app_store_must_reach_the_minimum_version(self):
        self.assertFalse(s.released("1.31.3", "1.31.9"))
        self.assertTrue(s.released("1.31.9", "1.31.9"))
        self.assertTrue(s.released("1.32.0", "1.31.9"))

    def test_unreadable_is_not_released(self):
        self.assertIsNone(s.released("", "1.31.9"))
        self.assertIsNone(s.released("1.31.9", ""))

    def test_the_created_round_does_not_move_the_minimum_version(self):
        note = s.append_round("", f"{s.RELEASE_APPROVED} (PR merged). Ships in 1.31.9.")
        note = s.append_round(note, f"{s.EXPERIMENT_CREATED} on the server (x); live once "
                                    f"1.31.9 is on the App Store.")
        self.assertEqual(s._version_from_note(note), "1.31.9")
        self.assertTrue(s.experiment_created(note))


class AQaBugIsANewRound(unittest.TestCase):
    def test_the_fix_merge_needs_a_new_request_that_raises_the_minimum(self):
        note = s.append_round("", f"{s.RELEASE_APPROVED} (PR merged). Ships in 1.31.9.")
        note = s.append_round(note, f"{s.EXPERIMENT_REQUESTED} (x, minimum version 1.31.9).")
        note = s.append_round(note, f"{s.EXPERIMENT_CREATED} on the server (x).")
        note = s.append_round(note, f"{s.RELEASE_DECLINED}: QA found a bug — crash on tap")
        self.assertFalse(s.release_approved(note))
        note = s.append_round(note, f"{s.RELEASE_APPROVED} (PR merged). Ships in 1.32.0.")
        self.assertFalse(s.experiment_requested(note))
        self.assertFalse(s.experiment_created(note))
        self.assertTrue(s.qa_fix_round(note))
        self.assertEqual(s._version_from_note(note), "1.32.0")

    def test_the_release_post_is_parsed_from_the_real_shape(self):
        m = s.RELEASE_POST_RE.search("New Aurie Release\n## v1.31.3 (1) - 2026-09-24\n\n*Bugs*")
        self.assertEqual(m.group(1), "1.31.3")


class OnboardingAndVersionFromGit(unittest.TestCase):
    SWIFT = """enum Experiments {
    enum Onboarding {
        static let valuePropsExplicitCTA = "valuePropsExplicitCTA"
        enum Nested { static let x = "nestedOnb" }
    }
    enum Home {
        static let paywallArchetypeHeadline = "paywallArchetypeHeadline"
    }
}"""

    def test_onboarding_names_are_only_inside_the_onboarding_enum(self):
        names = s.onboarding_names(self.SWIFT)
        self.assertIn("valuePropsExplicitCTA", names)
        self.assertIn("nestedOnb", names)
        self.assertNotIn("paywallArchetypeHeadline", names)

    def test_onboarding_uses_the_literal_aurabot_reads(self):
        q = s._qualifiers("ai", "ios", onboarding=True)
        self.assertEqual(q, " and targeting onboarding true and platform ios and targetApp ai")
        self.assertEqual(s._qualifiers("ai", "ios"), " and targeting platform ios and targetApp ai")

    def test_marketing_version_reads_project_yml(self):
        self.assertEqual(s.marketing_version("        MARKETING_VERSION: 1.32.0\n"), "1.32.0")
        self.assertEqual(s.marketing_version("nothing"), "")


class CreatedReply(unittest.TestCase):
    def test_names_the_experiment_and_the_version_without_a_request(self):
        text = s.created_text("", "x", "1.31.9")
        self.assertIn("`x` created in #support-bot", text)
        self.assertIn("1.31.9 is on the App Store", text)


class OnlyAQaFixIsAMinimumVersionUpdate(unittest.TestCase):
    def test_a_first_request_and_a_re_ask_are_creates(self):
        note = s.append_round("", f"{s.RELEASE_APPROVED} (PR merged).")
        self.assertFalse(s.qa_fix_round(note))
        note = s.append_round(note, f"{s.EXPERIMENT_REQUESTED} (x, minimum version 1.31.9).")
        self.assertFalse(s.qa_fix_round(note))

    def test_an_approval_after_unwind_is_a_create(self):
        note = s.append_round("", f"{s.RELEASE_APPROVED} (PR merged).")
        note = s.append_round(note, f"{s.EXPERIMENT_REQUESTED} (x).")
        note = s.append_round(note, f"{s.RELEASE_DECLINED}: copy reads abrupt.")
        note = s.append_round(note, f"{s.RELEASE_APPROVED} (Steve).")
        self.assertFalse(s.qa_fix_round(note))

    def test_a_qa_decline_with_no_earlier_request_is_a_create(self):
        note = s.append_round("", f"{s.RELEASE_APPROVED} (PR merged).")
        note = s.append_round(note, f"{s.QA_DECLINE} a bug — crash")
        note = s.append_round(note, f"{s.RELEASE_APPROVED} (PR merged).")
        self.assertFalse(s.qa_fix_round(note))

    def test_a_release_lookup_failure_is_empty(self):
        from unittest import mock  # noqa: PLC0415
        with mock.patch.object(s, "_find_release_post", side_effect=RuntimeError("blip")):
            self.assertEqual(s.find_release_post("1.31.9"), ("", ""))


class TheDeclinedPrIsOnTheNote(unittest.TestCase):
    def test_every_verdict_round_names_the_pr_it_judged(self):
        import inspect  # noqa: PLC0415
        for fn in (s.cmd_release, s.cmd_qa_bug, s.cmd_unwind, s.cmd_merged):
            self.assertIn("idea['pr']" if fn is not s.cmd_merged else "pr_url",
                          inspect.getsource(fn), fn.__name__)


class TheCheckFollowsTheCard(unittest.TestCase):
    def _run(self, state):
        from unittest import mock  # noqa: PLC0415
        import json  # noqa: PLC0415
        import subprocess  # noqa: PLC0415

        class R:
            returncode = 0
            stderr = ""
            stdout = json.dumps({"id": "u", "state": state})
        with mock.patch.object(subprocess, "run", return_value=R()):
            return s.card_done("https://linear.app/a/issue/ENGMOB-1/x")

    def test_done_is_the_completed_state(self):
        self.assertTrue(self._run({"name": "Done", "type": "completed"}))
        self.assertFalse(self._run({"name": "To Be Released", "type": "started"}))

    def test_no_card_is_unknown(self):
        self.assertIsNone(s.card_done(""))

    def test_the_loop_never_moves_the_card(self):
        import inspect  # noqa: PLC0415
        self.assertNotIn("update-issue", inspect.getsource(s.card_done))
        self.assertFalse(hasattr(s, "move_card"))
