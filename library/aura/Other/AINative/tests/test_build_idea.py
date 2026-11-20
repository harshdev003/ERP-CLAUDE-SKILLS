"""Guard tests for `product:build-idea`.

The build leg is mostly orchestration and mostly fails loudly. These cover the one
part that fails **silently**: forcing an experiment variant on a real account. The
run looks fine, the assignment persists server-side, and the damage is a metric
nobody can see until an experiment reads wrong weeks later.

No network — the account rules are pure functions over a string.
"""

from __future__ import annotations

import os
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(REPO, "plugins", "product", "skills", "build-idea", "scripts")
sys.path.insert(0, SCRIPTS)

import build_idea as b  # noqa: E402


class TestAccountRecognition(unittest.TestCase):
    """Both real shapes, checked against `users.json` in AuraConsumerApp.

    The sim-testing rule file documents `{prefix}+test+{unix}@aurahealth.io`, but
    `sim_test.sh` generates `test+{unix}@aurahealth.io`. Both are in the live
    fleet — 55 of the second, 3 of the first. A guard matching only one shape
    refuses real accounts, and a gate that blocks legitimate use is a gate
    somebody switches off.
    """

    GENERATED = [
        "<redacted-email>",
        "<redacted-email>",
        "<redacted-email>",
        "<redacted-email>",
        "<redacted-email>",
        "<redacted-email>",
    ]
    DOCUMENTED = [
        "<redacted-email>",
        "<redacted-email>",
    ]

    def test_accepts_the_generated_shape(self):
        for email in self.GENERATED:
            with self.subTest(email=email):
                self.assertTrue(b.is_test_account(email))

    def test_accepts_the_documented_shape(self):
        for email in self.DOCUMENTED:
            with self.subTest(email=email):
                self.assertTrue(b.is_test_account(email))


class RefusesEverythingElse(unittest.TestCase):
    def test_a_real_colleague(self):
        self.assertFalse(b.is_test_account("<redacted-email>"))
        self.assertFalse(b.is_test_account("<redacted-email>"))

    def test_the_google_account_used_for_sign_in_testing(self):
        """It is in `users.json`, but it is a genuine Google account with real
        credentials — never a legitimate target for a forced variant."""
        self.assertFalse(b.is_test_account("<redacted-email>"))

    def test_the_typod_domain_actually_present_in_users_json(self):
        self.assertFalse(b.is_test_account("<redacted-email>y"))

    def test_a_lookalike_domain(self):
        """Anchoring the pattern at both ends is what stops this."""
        self.assertFalse(b.is_test_account("<redacted-email>.evil.com"))
        self.assertFalse(b.is_test_account("<redacted-email>"))

    def test_more_than_one_prefix_segment(self):
        self.assertFalse(b.is_test_account("<redacted-email>"))

    def test_empty_and_whitespace(self):
        for junk in ("", "   ", None):
            with self.subTest(value=junk):
                self.assertFalse(b.is_test_account(junk))

    def test_leading_and_trailing_whitespace_is_stripped_not_exploited(self):
        self.assertTrue(b.is_test_account("  <redacted-email>  "))
        self.assertFalse(b.is_test_account("  <redacted-email>  "))


class GuardExitCodes(unittest.TestCase):
    """The CLI contract: 0 permits, 2 refuses. Anything calling this branches on
    the exit code, so the codes are the API."""

    @staticmethod
    def run_guard(email=None):
        import argparse
        args = argparse.Namespace(email=email, experiment="aiFoo", variant="a")
        return b.cmd_guard_variant(args)

    def test_test_account_permits(self):
        self.assertEqual(self.run_guard(email="<redacted-email>"), 0)

    def test_real_account_refuses(self):
        self.assertEqual(self.run_guard(email="<redacted-email>"), 2)

    def test_absent_email_refuses(self):
        """'No account supplied' must never read as 'no objection'."""
        self.assertEqual(self.run_guard(email=None), 2)
        self.assertEqual(self.run_guard(email="   "), 2)

    def test_there_is_no_override_flag(self):
        """Deliberate: forcing a variant on a real account is not something the
        loop should be able to do with a flag."""
        parser = b.build_parser()
        guard = parser.parse_args(["guard-variant", "--email", "<redacted-email>"])
        self.assertFalse(hasattr(guard, "override"))


class BuildableStatus(unittest.TestCase):
    def test_only_approved_is_buildable(self):
        """`shared` and `pending` mean nobody has said yes; building one would be
        the loop deciding on the team's behalf."""
        self.assertEqual(b.BUILDABLE_STATUS, "approved")


class DomainMatchingIsWordwise(unittest.TestCase):
    """camelCase is the experiment-naming convention, so a bare substring test
    reads words nobody wrote. `webSignupChooseFeeDefaultZero` contains `feed`
    inside \"Choose **Fee D**efault\" — that put a web sign-up test on the
    home/feed domain and blocked a build there for no reason."""

    def test_the_real_false_positive_that_motivated_this(self):
        self.assertFalse(b._touches_domain("home/feed", "webSignupChooseFeeDefaultZero"))

    def test_camel_split_still_finds_the_words_that_are_there(self):
        self.assertTrue(b._touches_domain("onboarding", "webSignupChooseFeeDefaultZero"))
        self.assertTrue(b._touches_domain("paywall", "webPaymentCtaTryForZeroNoTrialFee"))
        self.assertTrue(b._touches_domain("conversation", "removeDoneScreenVoice"))

    def test_real_home_feed_experiments_still_match(self):
        for name in ("raisedFirstCardPosition", "dailyPractices2", "homeStreakBanner"):
            with self.subTest(name=name):
                self.assertTrue(b._touches_domain("home/feed", name), name)

    def test_multi_word_keywords_survive_the_split(self):
        self.assertTrue(b._touches_domain("conversation", "quickProcessingIntro"))
        self.assertTrue(b._touches_domain("onboarding", "onboardingValueProps"))

    def test_words_normaliser(self):
        self.assertEqual(b._words("webSignupChooseFeeDefaultZero"),
                         "web signup choose fee default zero")
        self.assertEqual(b._words("aiLiveKitIntegration4"), "ai live kit integration4")
        self.assertEqual(b._words(""), "")
        self.assertEqual(b._words(None), "")


class MissesAreTheDangerousDirection(unittest.TestCase):
    """Camel-splitting alone fixed the false positive and introduced three false
    negatives, which is strictly worse — an over-block gets read and overridden,
    a miss is silent. All three names below are live on the catalog today."""

    def test_a_generation_number_does_not_hide_the_keyword(self):
        self.assertTrue(b._touches_domain("paywall", "aurieCustomWebPaywall2"))
        self.assertTrue(b._touches_domain("settings", "userSettings2"))
        self.assertTrue(b._touches_domain("conversation", "doneScreen2"))

    def test_a_plural_does_not_hide_the_keyword(self):
        self.assertTrue(b._touches_domain("conversation", "aurieAnimations2"))
        self.assertTrue(b._touches_domain("notifications", "emailNotifications"))

    def test_a_compound_the_split_took_apart_is_rebuilt(self):
        """`LiveKit` splits to "live kit"; the keyword is `livekit`."""
        self.assertTrue(b._touches_domain("conversation", "aiLiveKitIntegration4"))

    def test_rebuilding_compounds_does_not_reopen_the_substring_hole(self):
        """The join is compared for equality, never containment — so "fee" +
        "default" can never satisfy the keyword `feed`."""
        self.assertFalse(b._touches_domain("home/feed", "webSignupChooseFeeDefaultZero"))

    def test_fold_strips_digits_and_simple_plurals_only(self):
        self.assertEqual(b._fold("paywall2"), "paywall")
        self.assertEqual(b._fold("animations"), "animation")
        self.assertEqual(b._fold("cta"), "cta")        # too short to de-pluralise
        self.assertEqual(b._fold("progress"), "progress")  # -ss is not a plural


class SharedMetricPromptOnEveryPass(unittest.TestCase):
    """An override is still a decision to build, so it needs the question this
    gate cannot answer just as much as a clean pass does."""

    def test_the_prompt_helper_names_the_domain(self):
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            b._print_shared_metric_prompt("settings")
        out = buf.getvalue()
        self.assertIn("settings", out)
        self.assertIn("#support-bot", out)
        self.assertIn("primary metric", out)


class LiveSetComesFromTheApi(unittest.TestCase):
    """The Notion mirror is wrong in both directions — measured 2026-09-14, it
    missed 7 live experiments and named 30 that had ended. An over-block is loud
    and trains people to `--override`; a miss is silent and is the exact failure
    the gate exists to stop."""

    def test_no_token_is_unknown_not_empty(self):
        saved = {k: os.environ.pop(k, None) for k in ("AURA_API_TOKEN", "AURA_ADMIN_TOKEN")}
        try:
            live, detail = b.live_aurie_from_api()
            self.assertIsNone(live, "unreachable must never read as 'nothing is running'")
            self.assertIn("AURA_API_TOKEN", detail)
        finally:
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v

    def test_the_url_requests_the_whole_catalog(self):
        """offset/page/cursor are silently ignored; a default request returns the
        first 100 of ~3,000 rows and looks complete."""
        self.assertIn("limit=10000", b.EXPERIMENTS_URL)

    def test_aurie_is_targetapp_ai(self):
        self.assertEqual(b.AURIE_TARGET_APP, "ai")


class CardBody(unittest.TestCase):
    IDEA = {
        "title": "Reframe greeting from problem to aspiration",
        "fingerprint": "0a05fffc41b9",
        "screen": "home_screen",
        "domain": "home/feed",
        "description": "Change the greeting copy.",
        "rationale": "Negative framing primes anxiety.",
        "hypothesis": "If we reframe, first-session engagement rises 8-15%.",
        "lever": "retention",
        "slack": "https://auramobile.slack.com/archives/C0ATUL8T936/p1776464801853489",
    }

    def test_title_uses_the_ab_convention(self):
        title, _ = b.render_card(self.IDEA, "aiPersonalizedGreeting")
        self.assertEqual(title, "Loop: ab:aiPersonalizedGreeting: Reframe greeting from problem to aspiration")

    def test_body_names_the_variants_and_the_control_contract(self):
        _, body = b.render_card(self.IDEA, "aiPersonalizedGreeting")
        self.assertIn("`a` = treatment, `b` / `nil` = control", body)
        self.assertIn("degrade", body)  # a failed assignment must fall back to today's behaviour

    def test_body_carries_the_exposure_stamping_lesson(self):
        """The dailyPractices2 trap is stated on the ticket, where the engineer
        implementing it will actually read it."""
        _, body = b.render_card(self.IDEA, "aiPersonalizedGreeting")
        self.assertIn("exposure event where the surface actually renders", body)
        self.assertIn("dailyPractices2", body)

    def test_missing_fields_degrade_visibly_rather_than_rendering_blank(self):
        sparse = dict(self.IDEA, description="", rationale="", hypothesis="", slack="")
        _, body = b.render_card(sparse, "aiFoo")
        self.assertIn("_(no description on the idea row)_", body)
        self.assertIn("_(not shared)_", body)


class PrOpenedPost(unittest.TestCase):
    """`finish --post` announces the PR in the idea's thread. The permalink parse
    and the media-URL read are pure; the refusal path needs no network."""

    def test_permalink_splits_the_ts_at_ten_digits(self):
        got = b.parse_permalink("https://auramobile.slack.com/archives/C0ATUL8T936/p1776464801853489")
        self.assertEqual(got, ("C0ATUL8T936", "1776464801.853489"))

    def test_permalink_returns_none_rather_than_guessing(self):
        for junk in ("", None, "https://auramobile.slack.com/archives/C0ATUL8T936"):
            self.assertIsNone(b.parse_permalink(junk))

    def test_first_file_url_reads_the_first_file(self):
        row = {"properties": {"Screenshot": {"files": [
            {"file": {"url": "https://x/a.png"}},
            {"file": {"url": "https://x/b.png"}},
        ]}}}
        self.assertEqual(b._first_file_url(row, "Screenshot"), "https://x/a.png")

    def test_first_file_url_empty_when_the_property_is_unset(self):
        self.assertEqual(b._first_file_url({"properties": {}}, "Video"), "")

    def test_the_pr_post_is_refused_when_the_row_has_no_thread(self):
        # A PR opened for an idea nobody shared has no thread to announce it in.
        idea = {"slack": "", "fingerprint": "x", "screenshot_url": "", "video_url": ""}
        self.assertEqual(b._post_pr_opened(idea, "PR opened for review: …"), 2)

    def test_the_claim_names_only_the_media_the_row_carries(self):
        from unittest import mock  # noqa: PLC0415
        import slack_post  # noqa: PLC0415
        thread = "https://auramobile.slack.com/archives/C0ATUL8T936/p1776464801853489"
        posted = []

        def fake_post(**kw):
            posted.append(kw["text"])
            return {"ok": True, "ts": "1.2"}

        def idea(**kw):
            d = {"slack": thread, "fingerprint": "x", "screenshot_url": "", "video_url": ""}
            d.update(kw)
            return d

        with mock.patch.object(slack_post, "post_message", fake_post), \
                mock.patch.object(b, "_attach_media", lambda *a, **k: True):
            b._post_pr_opened(idea(screenshot_url="u"), "pr")
            self.assertIn("See the screenshots of both variants", posted[0])
            self.assertNotIn("and video", posted[0])  # never claim a type that is absent
            posted.clear()
            b._post_pr_opened(idea(video_url="v"), "pr")
            self.assertIn("See the video of both variants", posted[0])
            posted.clear()
            b._post_pr_opened(idea(), "pr")  # neither
            self.assertIn("on the card and the idea row", posted[0])


class IdeaIdAddressing(unittest.TestCase):
    """An idea is addressed by its Notion-minted Idea ID — the pure parse and the
    row read, which every skill's resolver shares."""

    def test_ref_accepts_the_id_forms(self):
        for ref in ("42", "#42", "IDEA-42", "idea-42", "idea 42", "  42  "):
            with self.subTest(ref=ref):
                self.assertEqual(b._ref_idea_num(ref), 42)

    def test_ref_rejects_a_fingerprint_and_junk(self):
        # A 12-hex fingerprint must never be read as an Idea number.
        self.assertIsNone(b._ref_idea_num("0a05fffc41b9"))
        self.assertIsNone(b._ref_idea_num("aiPersonalizedGreeting"))
        self.assertIsNone(b._ref_idea_num("1234567"))  # >6 digits — not an id
        self.assertIsNone(b._ref_idea_num(""))
        self.assertIsNone(b._ref_idea_num(None))

    def test_row_idea_reads_the_unique_id(self):
        row = {"properties": {"Idea ID": {"unique_id": {"prefix": "IDEA", "number": 42}}}}
        self.assertEqual(b._row_idea(row), (42, "IDEA-42"))

    def test_row_idea_empty_when_unset(self):
        self.assertEqual(b._row_idea({"properties": {}}), (None, ""))


class CardTransitions(unittest.TestCase):
    """The feature card moves as the build does — In Progress on start, In Review
    on finish — on the ENGMOB board, best-effort so a Linear miss never fails the
    Notion write."""

    def test_state_ids_are_the_engmob_states(self):
        self.assertEqual(b.ENGMOB_STATE_IN_PROGRESS, "2f3af5c5-ed04-459b-84b0-f377d75141e2")
        self.assertEqual(b.ENGMOB_STATE_IN_REVIEW, "7b782b06-d9c5-41ba-8588-35b193d928ae")

    def test_card_identifier_from_a_linear_url(self):
        self.assertEqual(
            b._card_identifier("https://linear.app/aura-health/issue/ENGMOB-9/some-slug"), "ENGMOB-9")
        self.assertEqual(
            b._card_identifier("https://linear.app/aura-health/issue/prod-9/x"), "PROD-9")
        self.assertEqual(b._card_identifier("not a url"), "")

    def test_move_card_resolves_the_uuid_then_updates_the_state(self):
        from unittest import mock  # noqa: PLC0415
        calls = []

        def fake(*argv):
            calls.append(argv[0])
            return {"id": "uuid-1", "identifier": "ENGMOB-9", "url": "x"}

        with mock.patch.object(b, "_linear", side_effect=fake):
            b.move_card("https://linear.app/aura-health/issue/ENGMOB-9/x",
                        b.ENGMOB_STATE_IN_REVIEW, "In Review")
        self.assertEqual(calls, ["get-issue", "update-issue"])  # resolve, then move

    def test_move_card_is_a_noop_without_a_card(self):
        from unittest import mock  # noqa: PLC0415
        with mock.patch.object(b, "_linear") as m:
            b.move_card("", b.ENGMOB_STATE_IN_PROGRESS, "In Progress")
        m.assert_not_called()

    def _finish(self, draft, **idea_kw):
        from unittest import mock  # noqa: PLC0415
        import argparse  # noqa: PLC0415
        idea = {"status": "implementing", "page_id": "p", "fingerprint": "abc",
                "title": "t", "linear": "https://linear.app/aura-health/issue/ENGMOB-9/x",
                "screenshot_url": "", "video_url": "", "slack": ""}
        idea.update(idea_kw)
        with mock.patch.object(type(b.notion), "available", new_callable=mock.PropertyMock, return_value=True), \
                mock.patch.object(b, "find_idea", return_value=idea), \
                mock.patch.object(b.notion, "update_page_props"), \
                mock.patch.object(b, "_pr_is_draft", return_value=draft), \
                mock.patch.object(b, "move_card") as mv:
            b.cmd_finish(argparse.Namespace(data_source_id="x", ref="9",
                                            pr="https://gh/pr/1", note=None, post=False))
        return mv

    def test_finish_holds_in_progress_while_the_pr_is_a_draft(self):
        # A draft PR is not ready to review — the card must not jump to In Review.
        self._finish(draft=True).assert_not_called()

    def test_finish_holds_when_the_draft_state_is_unreadable(self):
        # Conservative: if gh can't say, don't mark review prematurely.
        self._finish(draft=None).assert_not_called()

    def test_finish_moves_to_review_when_the_pr_is_already_ready(self):
        mv = self._finish(draft=False)
        mv.assert_called_once()
        self.assertEqual(mv.call_args[0][1], b.ENGMOB_STATE_IN_REVIEW)

    def test_ready_moves_the_card_to_in_review(self):
        from unittest import mock  # noqa: PLC0415
        import argparse  # noqa: PLC0415
        idea = {"fingerprint": "abc", "title": "t",
                "linear": "https://linear.app/aura-health/issue/ENGMOB-9/x"}
        with mock.patch.object(type(b.notion), "available", new_callable=mock.PropertyMock, return_value=True), \
                mock.patch.object(b, "find_idea", return_value=idea), \
                mock.patch.object(b, "move_card") as mv:
            rc = b.cmd_ready(argparse.Namespace(data_source_id="x", ref="9"))
        self.assertEqual(rc, 0)
        mv.assert_called_once()
        self.assertEqual(mv.call_args[0][1], b.ENGMOB_STATE_IN_REVIEW)

    def test_ready_refuses_without_a_card(self):
        from unittest import mock  # noqa: PLC0415
        import argparse  # noqa: PLC0415
        with mock.patch.object(type(b.notion), "available", new_callable=mock.PropertyMock, return_value=True), \
                mock.patch.object(b, "find_idea", return_value={"fingerprint": "abc", "linear": ""}):
            rc = b.cmd_ready(argparse.Namespace(data_source_id="x", ref="9"))
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
