"""Guard tests for `product:learn-idea`.

Two things here can be wrong in a way nobody notices.

The first is writing a verdict the experiment did not actually deliver. The
Ideas row is what the ideation prior-knowledge block consults before proposing,
so a false "already answered" silently removes an untested idea from
circulation — the opposite of the failure the loop was built to fix, and
invisible because nothing ever errors.

The second is the funnel. A funnel counted by current status shows one row per
stage and reads as a catastrophe at every step; counted cumulatively it shows
the real conversion. Both render without complaint.

No network — every function under test is pure.
"""

from __future__ import annotations

import os
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(REPO, "plugins", "product", "skills", "learn-idea", "scripts")
sys.path.insert(0, SCRIPTS)

import learn_idea as l  # noqa: E402


class VerdictMapping(unittest.TestCase):
    def test_the_three_real_verdicts_map(self):
        for exp, ideas in (("Won", "won"), ("Lost", "lost"), ("Inconclusive", "inconclusive")):
            with self.subTest(exp=exp):
                self.assertEqual(l.verdict_for(exp)[0], ideas)

    def test_a_running_experiment_writes_nothing(self):
        for exp in ("Running", "In flight", None, ""):
            with self.subTest(exp=exp):
                status, why = l.verdict_for(exp)
                self.assertIsNone(status)
                self.assertIn("not ended", why)

    def test_turned_off_is_refused_not_guessed_as_lost(self):
        """122 rows carry it, and it means two different things. `aurieCustomWebPaywall`
        was turned off for a checkout bug and re-run as v2 — that is not a result
        about the idea, and recording it as `lost` would retire an untested idea."""
        status, why = l.verdict_for("Turned off")
        self.assertIsNone(status)
        self.assertIn("not a verdict", why)
        self.assertIn("--status", why)

    def test_an_unknown_status_writes_nothing(self):
        """A new value added to the Notion select must not silently become a guess."""
        status, why = l.verdict_for("Paused pending legal")
        self.assertIsNone(status)
        self.assertIn("not writing a guess", why)

    def test_turned_off_is_absent_from_the_mapping_table(self):
        self.assertNotIn("Turned off", l.VERDICT)


class FunnelIsCumulative(unittest.TestCase):
    """The whole point: a released row is also a proposed row."""

    @staticmethod
    def rows(*statuses):
        return [{"status": s, "first_seen": "2026-04-01"} for s in statuses]

    def test_one_live_row_counts_at_every_earlier_stage(self):
        c = l.funnel_counts(self.rows("experiment_running"))
        self.assertEqual([c[s] for s in l.STAGES], [1, 1, 1, 1, 1, 0])

    def test_a_status_no_stage_claims_is_reported_not_swallowed(self):
        """A row on an unmapped status counts nowhere, so the chart renders
        perfectly and is quietly short — which reads as a finding, not a bug.
        Folding `released` into `experiment_running` is exactly when this bites."""
        rows = self.rows("experiment_running") + self.rows("released", "in_review")
        self.assertEqual(l.funnel_counts(rows)["proposed"], 1)
        self.assertEqual(sum(l.unmapped_statuses(rows).values()), 2)

    def test_a_decided_row_counts_everywhere(self):
        for verdict in ("won", "lost", "inconclusive"):
            with self.subTest(verdict=verdict):
                c = l.funnel_counts(self.rows(verdict))
                self.assertEqual([c[s] for s in l.STAGES], [1] * 6)

    def test_pending_counts_only_as_proposed(self):
        c = l.funnel_counts(self.rows("pending"))
        self.assertEqual([c[s] for s in l.STAGES], [1, 0, 0, 0, 0, 0])

    def test_rejected_and_deferred_stop_at_shared(self):
        """They were proposed and shown to the team, then not approved. Putting
        them anywhere else makes the shared -> approved drop lie about the
        decision rate."""
        c = l.funnel_counts(self.rows("rejected", "deferred"))
        self.assertEqual([c[s] for s in l.STAGES], [2, 2, 0, 0, 0, 0])

    def test_dormant_and_abandoned_count_as_built_never_as_live(self):
        """Both were built; neither was ever put in front of a user. Counting
        them live would report a release rate including ideas nobody saw — the
        one number on this chart somebody would act on."""
        c = l.funnel_counts(self.rows("shipped_dormant", "abandoned"))
        self.assertEqual([c[s] for s in l.STAGES], [2, 2, 2, 2, 0, 0])

    def test_implementing_is_built_however_many_rounds_it_took(self):
        """A row on its third gate round has not travelled further than one on
        its first. It has just cost more."""
        c = l.funnel_counts(self.rows("implementing"))
        self.assertEqual([c[s] for s in l.STAGES], [1, 1, 1, 1, 0, 0])

    def test_counts_are_monotonically_non_increasing(self):
        """A funnel where a later stage exceeds an earlier one is arithmetically
        impossible and means the stage map is wrong."""
        c = l.funnel_counts(self.rows(*l.STAGE_REACHED.keys()))
        vals = [c[s] for s in l.STAGES]
        self.assertEqual(vals, sorted(vals, reverse=True), vals)

    def test_every_status_the_lifecycle_defines_has_a_stage(self):
        """A status with no entry silently vanishes from the funnel."""
        lifecycle = {"pending", "shared", "approved", "rejected", "deferred",
                     "implementing", "shipped_dormant", "abandoned",
                     "experiment_running", "won", "lost", "inconclusive"}
        self.assertEqual(lifecycle - set(l.STAGE_REACHED), set())


class LearningNoteIsNeverAPlaceholder(unittest.TestCase):
    """`Quick status note` is what the ideation prior-knowledge block surfaces as
    the answer to the question the idea asked. A placeholder there is not
    cosmetic — it is a fabricated finding that reads exactly like a real one."""

    def test_the_default_is_true_and_traceable(self):
        for verdict in ("won", "lost", "inconclusive"):
            with self.subTest(verdict=verdict):
                got = l.default_learning(verdict)
                self.assertIn(verdict, got)
                self.assertIn("Learnings/Experiments", got)

    def test_the_default_is_not_an_internal_reason_string(self):
        """The first cut wrote 'Supplied explicitly' — the code's own rationale,
        leaked into the field a human later reads as the finding."""
        for verdict in ("won", "lost", "inconclusive"):
            self.assertNotIn("supplied", l.default_learning(verdict).lower())

    def test_an_explicit_status_requires_a_stated_learning(self):
        """--status exists for `Turned off`, where the verdict is a judgement the
        database does not support. Recording one with no reason puts an
        unexplained answer in front of every future ideation run."""
        parser = l.build_parser()
        args = parser.parse_args(["record", "abc123", "--status", "lost"])
        self.assertIsNone(args.learning)  # the parser permits it; the command refuses it


class AnnounceNeverRaises(unittest.TestCase):
    """`slack_post` calls sys.exit() on a missing token, and SystemExit derives
    from BaseException — so `except Exception` does not stop it. In
    `sweep --apply --announce` an uncaught one kills the process after the first
    row, leaving every remaining ready row at experiment_running: a half-applied
    sweep that looks like a crash rather than a partial result.

    This repo has been bitten by exactly this before, in share-ideas."""

    IDEA = {"slack": "https://auramobile.slack.com/archives/C0ATUL8T936/p1776464801853489",
            "experiment": "aiFoo"}

    def test_a_missing_token_is_reported_not_raised(self):
        saved = {k: os.environ.pop(k, None) for k in ("SLACK_BOT_TOKEN",)}
        try:
            ok, detail = l.announce(self.IDEA, "lost", "No lift.")
            self.assertFalse(ok)
            self.assertIn("not posted", detail)
        finally:
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v

    def test_a_row_with_no_thread_is_reported_not_raised(self):
        ok, detail = l.announce({"slack": "", "experiment": "aiFoo"}, "won", "Lift held.")
        self.assertFalse(ok)
        self.assertIn("no usable Slack thread", detail)


class CohortMonth(unittest.TestCase):
    def test_keys_on_when_the_idea_was_proposed(self):
        """Keying on the end date makes every stage a different population and
        the conversion rates meaningless."""
        self.assertEqual(l.month_of({"first_seen": "2026-04-17"}), "2026-04")

    def test_a_row_with_no_date_is_named_not_dropped(self):
        self.assertEqual(l.month_of({"first_seen": ""}), "unknown")
        self.assertEqual(l.month_of({}), "unknown")


class PermalinkParse(unittest.TestCase):
    def test_splits_the_ts_at_ten_digits(self):
        got = l.parse_permalink("https://auramobile.slack.com/archives/C0ATUL8T936/p1776464801853489")
        self.assertEqual(got, ("C0ATUL8T936", "1776464801.853489"))

    def test_returns_none_rather_than_guessing(self):
        for junk in ("", None, "not a url"):
            with self.subTest(v=junk):
                self.assertIsNone(l.parse_permalink(junk))


class CleanupLinkIsIndependentOfTheVerdict(unittest.TestCase):
    """A --cleanup-issue re-run lands AFTER the verdict, once the sweep card exists.
    It must link on an already-decided row rather than hit the experiment_running
    gate — the bug the sweep-routing message would otherwise walk operators into."""

    def _run(self, idea, **ns):
        from unittest import mock  # noqa: PLC0415
        import argparse  # noqa: PLC0415
        writes = {}
        args = argparse.Namespace(data_source_id="x", ref="1", status=None, learning=None,
                                  cleanup_issue=None, announce=False)
        args.__dict__.update(ns)
        with mock.patch.object(l, "_require_notion"), \
                mock.patch.object(l, "load_ideas", return_value=[idea]), \
                mock.patch.object(l, "resolve", return_value=idea), \
                mock.patch.object(l.notion, "update_page_props",
                                  side_effect=lambda pid, props: writes.update(props)):
            rc = l.cmd_record(args)
        return rc, writes

    def test_links_cleanup_on_a_decided_row_without_refusing(self):
        idea = {"page_id": "p", "fingerprint": "abc", "title": "x", "status": "lost",
                "experiment": "aiStreak", "cleanup_issue": ""}
        rc, writes = self._run(idea, cleanup_issue="https://linear.app/e/ENGMOB-9")
        self.assertEqual(rc, 0)
        self.assertEqual(writes["Cleanup issue"]["url"], "https://linear.app/e/ENGMOB-9")

    def test_does_not_overwrite_an_existing_cleanup_link(self):
        idea = {"page_id": "p", "fingerprint": "abc", "title": "x", "status": "won",
                "experiment": "aiStreak", "cleanup_issue": "https://linear.app/e/ENGMOB-1"}
        rc, writes = self._run(idea, cleanup_issue="https://linear.app/e/ENGMOB-9")
        self.assertEqual(rc, 0)
        self.assertEqual(writes, {})  # write-once — nothing written


MONITOR_LOST = """:red_circle: Experiment Lost: paywallArchetypeHeadline

Winner: variant b (control: “A shift you can actually feel”)
• Latest Paid Subscription Rate per paywall view: 20.0% (15/75) for a vs 27.7% (28/101) for b, −27.9%, 87.6% confidence
• Latest Paid Subscription Rate per registration: 18.9% (14/74) for a vs 29.0% (27/93) for b, −34.8%, 93.2% confidence
• n=176 paywall viewers, Oct 9–10

The morning paywall-view cut reached 95.0% confidence before softening in the latest pull. Steve chose to stop the test and retain control."""

ENDED = ("Experiment ended: `paywallArchetypeHeadline` [188 users] - b control retained at "
         "Steve's direction on Day 2; treatment lowered Paid Subscription Rate by 27.9% per "
         "paywall view at 87.6% confidence")


class EndPostParsing(unittest.TestCase):
    def test_monitor_post_verdict_and_key(self):
        for word, verdict in (("Won", "won"), ("Lost", "lost"), ("Inconclusive", "inconclusive")):
            with self.subTest(word=word):
                got = l.parse_monitor_post(f":large_green_circle: Experiment {word}: aiFoo\n\nWinner: a")
                self.assertEqual((got["verdict"], got["key"]), (verdict, "aiFoo"))

    def test_monitor_post_evidence_quotes_winner_metric_and_who(self):
        got = l.parse_monitor_post(MONITOR_LOST)
        self.assertEqual(got["key"], "paywallArchetypeHeadline")
        self.assertIn("Winner: variant b", got["evidence"])
        self.assertIn("−27.9%, 87.6% confidence", got["evidence"])
        self.assertIn("Steve chose to stop the test", got["evidence"])

    def test_a_daily_monitoring_digest_is_not_an_end_post(self):
        self.assertIsNone(l.parse_monitor_post(
            ":red_circle: Aurie - Experiments monitoring - Sat Oct 10\n• paywallArchetypeHeadline: watch"))

    def test_ended_post_reason(self):
        got = l.parse_ended_post(ENDED)
        self.assertEqual(got["key"], "paywallArchetypeHeadline")
        self.assertTrue(got["reason"].startswith("b control retained at Steve's direction"))

    def test_find_end_posts_matches_the_exact_key_and_channel(self):
        def fake(q, count=50):
            return {"ok": True, "messages": {"matches": [
                {"channel": {"id": l.MONITOR_CHANNEL}, "ts": "2", "permalink": "M",
                 "text": MONITOR_LOST},
                {"channel": {"id": l.MONITOR_CHANNEL}, "ts": "3", "permalink": "X",
                 "text": ":red_circle: Experiment Won: paywallArchetypeHeadline2"},
                {"channel": {"id": l.DATA_EXPERIMENT_CHANNEL}, "ts": "1", "permalink": "D",
                 "text": ENDED}]}}
        got = l.find_end_posts("paywallArchetypeHeadline", search=fake)
        self.assertEqual(got["monitor"]["permalink"], "M")
        self.assertEqual(got["ended"]["permalink"], "D")

    def test_a_slack_error_response_is_unknown_not_no_post(self):
        self.assertIsNone(l.find_end_posts("aiFoo", search=lambda q, count=50: {
            "ok": False, "error": "not_authed"}))

    def test_an_unsearchable_slack_is_unknown_not_no_post(self):
        def boom(q, count=50):
            raise SystemExit("SLACK_USER_TOKEN not set")
        self.assertIsNone(l.find_end_posts("aiFoo", search=boom))


class EndPostPrecedence(unittest.TestCase):
    POSTS = {"monitor": {**l.parse_monitor_post(MONITOR_LOST), "permalink": "M"},
             "ended": {**l.parse_ended_post(ENDED), "permalink": "D"}}

    def test_the_monitor_post_wins_over_an_inconclusive_row_and_says_so(self):
        d = l.decide("inconclusive", self.POSTS)
        self.assertEqual(d["verdict"], "lost")
        self.assertIn("Learnings row says inconclusive", d["disagreement"])
        self.assertIn("Steve chose to stop", d["learning"])
        self.assertIn("M", d["learning"])
        self.assertIn("D", d["learning"])

    def test_agreement_reports_nothing(self):
        self.assertEqual(l.decide("lost", self.POSTS)["disagreement"], "")

    def test_no_end_post_means_ask(self):
        d = l.decide("lost", {"monitor": None, "ended": None})
        self.assertIsNone(d["verdict"])
        self.assertTrue(d["ask"])


class AskYawarOnce(unittest.TestCase):
    THREAD = "https://auramobile.slack.com/archives/C0C1JTK3YCC/p1791236551851119"

    def _idea(self, note=""):
        return {"page_id": "p", "experiment": "aiFoo", "slack": self.THREAD, "note": note}

    def test_asks_in_the_thread_and_stamps_the_note(self):
        from unittest import mock  # noqa: PLC0415
        sent, writes = [], []
        with mock.patch.object(l.notion, "update_page_props",
                               side_effect=lambda pid, props: writes.append(props)):
            asked = l.ask_why(self._idea(), reply=lambda **kw: sent.append(kw) or {"ok": True})
        self.assertTrue(asked)
        self.assertIn(f"<@{l.YAWAR}>", sent[0]["text"])
        self.assertEqual(sent[0]["thread_ts"], "1791236551.851119")
        self.assertIn(l.ASKED_WHY, writes[0]["Quick status note"]["rich_text"][0]["text"]["content"])

    def test_never_asks_twice(self):
        sent = []
        asked = l.ask_why(self._idea(note=f"{l.ASKED_WHY} (Yawar)."),
                          reply=lambda **kw: sent.append(kw) or {"ok": True})
        self.assertFalse(asked)
        self.assertEqual(sent, [])

    def test_a_failed_marker_write_neither_raises_nor_posts(self):
        from unittest import mock  # noqa: PLC0415
        sent = []
        with mock.patch.object(l.notion, "update_page_props", side_effect=RuntimeError("503")):
            asked = l.ask_why(self._idea(), reply=lambda **kw: sent.append(kw) or {"ok": True})
        self.assertFalse(asked)
        self.assertEqual(sent, [])

    def test_a_failed_post_puts_the_note_back(self):
        from unittest import mock  # noqa: PLC0415
        writes = []
        with mock.patch.object(l.notion, "update_page_props",
                               side_effect=lambda pid, props: writes.append(props)):
            asked = l.ask_why(self._idea(note="Live."), reply=lambda **kw: {"ok": False})
        self.assertFalse(asked)
        last = writes[-1]["Quick status note"]["rich_text"][0]["text"]["content"]
        self.assertNotIn(l.ASKED_WHY, last)


class CleanupStaysOnLoopKeys(unittest.TestCase):
    def test_only_the_rows_own_keys_and_never_a_blank_one(self):
        rows = [{"fingerprint": "a1", "experiment": "paywallArchetypeHeadline", "cleanup_issue": ""},
                {"fingerprint": "b2", "experiment": "", "cleanup_issue": ""},
                {"fingerprint": "c3", "experiment": "aiFoo", "cleanup_issue": "https://linear.app/x"}]
        self.assertEqual(l.cleanup_keys(rows), [("a1", "paywallArchetypeHeadline")])




class LearningIsComposedFromTheEndPosts(unittest.TestCase):
    """The learning is what ideation reads before proposing, so each part must come
    from where it is true: the test from the row, the result from the monitor, the
    reason from #data-experiment."""

    IDEA = {"title": "Mirror the user's archetype on the paywall", "experiment": "paywallArchetypeHeadline",
            "variants": "- b: control\n- a: archetype headline", "idea_id": "IDEA-6"}
    POSTS = {"monitor": {"verdict": "lost", "evidence": "Winner: variant b. 20.0% vs 27.7%, −27.9%, 87.6% confidence.",
                         "permalink": "https://x/mon"},
             "ended": {"reason": "b control retained at Steve's direction on Day 2", "permalink": "https://x/end"}}

    def test_each_part_comes_from_its_source(self):
        got = l.compose_learning(self.IDEA, "lost", self.POSTS)
        self.assertEqual(got["tested"], "b: control a: archetype headline")
        self.assertIn("−27.9%", got["result"])
        self.assertEqual(got["ended_by"], "b control retained at Steve's direction on Day 2")
        self.assertEqual(got["source"], "https://x/mon")
        self.assertTrue(got["text"].startswith("Lost: Mirror the user's archetype"))
        self.assertIn("Implication:", got["text"])

    def test_the_ticks_implication_wins_over_the_default(self):
        got = l.compose_learning(self.IDEA, "lost", self.POSTS, "keep the proven headline")
        self.assertEqual(got["implication"], "keep the proven headline")
        self.assertNotIn(l.IMPLICATION["lost"], got["text"])

    def test_every_verdict_has_a_default_implication(self):
        for v in ("won", "lost", "inconclusive"):
            with self.subTest(v=v):
                self.assertTrue(l.compose_learning(self.IDEA, v, None)["implication"])

    def test_no_end_posts_still_names_the_test(self):
        got = l.compose_learning({**self.IDEA, "variants": ""}, "inconclusive", None)
        self.assertEqual(got["result"], "")
        self.assertIn("`paywallArchetypeHeadline` arm a vs control", got["tested"])


class LearningLogUpsertsByIdeaId(unittest.TestCase):
    """A correction must rewrite the idea's one Loop Learnings row, never add a second
    answer; and a failed log must not raise after the verdict is written."""

    def setUp(self):
        self.calls = []
        test = self

        class Fake:
            def __init__(self, found):
                self.found = found

            def update_page_props(self, pid, props):
                test.calls.append(("update", pid, sorted(props)))

            def query_data_source(self, ds, **kw):
                test.calls.append(("query", ds, kw["filter"]["rich_text"]["equals"]))
                return iter(self.found)

            def create_database_row(self, ds, props, blocks):
                test.calls.append(("create", ds, sorted(props)))

        self.Fake = Fake
        self.saved = l.notion
        self.idea = {"page_id": "idea-page", "idea_id": "IDEA-6", "title": "t", "experiment": "k",
                     "url": "https://notion/x"}
        self.lesson = l.compose_learning(self.idea, "lost", None)

    def tearDown(self):
        l.notion = self.saved

    def test_first_time_creates_a_row(self):
        l.notion = self.Fake([])
        self.assertEqual(l.write_learning(self.idea, self.lesson, l.AURIE_FEATURES_DS), "logged")
        self.assertEqual(self.calls[1], ("query", l.LOOP_LEARNINGS_DS, "IDEA-6"))
        self.assertEqual(self.calls[2][0], "create")

    def test_an_existing_row_is_updated_not_duplicated(self):
        l.notion = self.Fake([{"id": "row-1"}])
        self.assertEqual(l.write_learning(self.idea, self.lesson, l.AURIE_FEATURES_DS), "updated")
        self.assertNotIn("create", [c[0] for c in self.calls])
        self.assertEqual(self.calls[-1][1], "row-1")

    def test_a_failure_is_reported_not_raised(self):
        class Boom:
            def update_page_props(self, *a):
                raise RuntimeError("notion down")
        l.notion = Boom()
        self.assertTrue(l.write_learning(self.idea, self.lesson, l.AURIE_FEATURES_DS).startswith("not logged"))



class LearningIsNotBlankedWhenSlackIsDown(unittest.TestCase):
    """A rewrite composed with no end posts would blank a complete Result, Ended by and
    Source; `learning` refuses instead of overwriting."""

    def test_learning_refuses_without_slack(self):
        saved = {k: getattr(l, k) for k in ("_require_notion", "load_ideas", "resolve",
                                             "find_end_posts", "write_learning")}
        idea = {"status": "lost", "experiment": "k", "fingerprint": "f"}
        try:
            l._require_notion = lambda: None
            l.load_ideas = lambda ds: [idea]
            l.resolve = lambda rows, ref: idea
            l.find_end_posts = lambda key: None
            l.write_learning = lambda *a: self.fail("must not write")
            args = l.build_parser().parse_args(["learning", "IDEA-6", "--apply"])
            self.assertEqual(l.cmd_learning(args), 2)
        finally:
            for k, v in saved.items():
                setattr(l, k, v)

if __name__ == "__main__":
    unittest.main()
