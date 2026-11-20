#!/usr/bin/env python3
"""Parse-side invariants of `data:investigate-pmf-survey`'s `fetch_pmf_responses.py`.

Two independent rules, both of which fail silently — the entry parses, the run
succeeds, and only the Sean-Ellis arithmetic is wrong. Footer identity first
(ENG-1141), then blockquote normalisation (ENG-1312) at the bottom of the file.

Footer-derived identity + internal classification.

The rule under test: the PMF footer's name slot sometimes holds an email address
rather than a first name, and when it does the internal-account rules must fire
from THAT address — independent of whether `GET /users/<id>` resolved. Two
`@aurahealth.io` QA entries were counted as real "Very disappointed" respondents
on the 2026-08-11 sweep precisely because the domain rule only ran against the
Aura API record, which 404s on a QA account (ENG-1141).

Three properties are load-bearing and each is asserted negatively as well as
positively, because every failure here is silent — the entry parses, the run
succeeds, and the account simply lands in the Sean-Ellis denominator:

  1. An address in the name slot never survives into `first_name` (PII).
  2. The Aura API join is ADDITIVE — neither the 404 branch nor the found branch
     may clear a flag the footer already set.
  3. The test-alias match is per token, so `contest@…` is not a test account.

Stdlib unittest, no network: the classification path is pure, and the enrichment
path is exercised by driving `enrich_entries` against a stubbed session.
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = REPO / "plugins/data/skills/investigate-pmf-survey/scripts/fetch_pmf_responses.py"


def _load():
    """Import the script by path without importing its `requests`-using siblings."""
    spec = importlib.util.spec_from_file_location("fetch_pmf_responses", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["fetch_pmf_responses"] = mod
    spec.loader.exec_module(mod)
    return mod


pmf = _load()

# The exact footer slot Slack delivered for the two ENG-1141 entries.
QA_SLOT = (
    "<mailto:<redacted-email>"
    "|<redacted-email>>"
)


class TestParseFooterName(unittest.TestCase):
    def test_mailto_wrapped_address_yields_email_and_no_display_name(self):
        display, email = pmf.parse_footer_name(QA_SLOT)
        self.assertEqual(email, "<redacted-email>")
        self.assertEqual(display, "")

    def test_plain_first_name_is_untouched(self):
        display, email = pmf.parse_footer_name("Farhan")
        self.assertEqual(display, "Farhan")
        self.assertEqual(email, "")

    def test_labelled_mailto_keeps_the_label_as_the_name(self):
        display, email = pmf.parse_footer_name("<mailto:a@b.com|Alex>")
        self.assertEqual(display, "Alex")
        self.assertEqual(email, "a@b.com")

    def test_bare_unwrapped_address_is_still_detected(self):
        display, email = pmf.parse_footer_name("<redacted-email>")
        self.assertEqual(email, "<redacted-email>")
        self.assertEqual(display, "")

    def test_no_slot_ever_carries_an_at_sign_forward(self):
        # The negative property: whatever the slot held, the display name that
        # reaches a Slack card must not be an address.
        for slot in (QA_SLOT, "<redacted-email>", "<mailto:a@b.com|a@b.com>"):
            with self.subTest(slot=slot):
                display, _ = pmf.parse_footer_name(slot)
                self.assertNotIn("@", display)
                self.assertNotIn("mailto", display)


class TestClassifyFooterEmail(unittest.TestCase):
    def test_internal_domain(self):
        internal, reason = pmf.classify_footer_email("<redacted-email>")
        self.assertTrue(internal)
        self.assertEqual(reason, "email_domain=aurahealth.io")

    def test_yawar_test_alias_on_an_external_domain(self):
        # Team/Internal Accounts: "match the email pattern, not just the listed UIDs".
        internal, reason = pmf.classify_footer_email("<redacted-email>")
        self.assertTrue(internal)
        self.assertEqual(reason, "email_test_alias")

    def test_ordinary_user_is_not_internal(self):
        for email in ("<redacted-email>", "<redacted-email>", "", "not-an-email"):
            with self.subTest(email=email):
                internal, reason = pmf.classify_footer_email(email)
                self.assertFalse(internal, email)
                self.assertEqual(reason, "")

    def test_test_matches_per_token_not_substring(self):
        # `contest`/`testa` contain "test" but are not test aliases; matching as a
        # substring would drop real respondents from the denominator.
        for email in ("<redacted-email>", "<redacted-email>", "protest@x.io"):
            with self.subTest(email=email):
                self.assertFalse(pmf.classify_footer_email(email)[0], email)
        for email in ("<redacted-email>", "<redacted-email>", "<redacted-email>"):
            with self.subTest(email=email):
                self.assertTrue(pmf.classify_footer_email(email)[0], email)


class TestApplyFooterIdentity(unittest.TestCase):
    def _entry(self, slot):
        return {"uid": "6KZhQh8CTOa20v4YzXF7O71fIsJ3", "first_name": slot,
                "sentiment": "Very disappointed"}

    def test_qa_entry_is_flagged_and_keeps_domain_only(self):
        e = self._entry(QA_SLOT)
        pmf.apply_footer_identity([e])
        self.assertTrue(e["is_internal"])
        self.assertEqual(e["internal_reason"], "email_domain=aurahealth.io")
        self.assertEqual(e["email_domain"], "aurahealth.io")
        self.assertEqual(e["first_name"], "")

    def test_full_address_is_never_retained_anywhere_on_the_entry(self):
        e = self._entry(QA_SLOT)
        pmf.apply_footer_identity([e])
        self.assertNotIn("farhan", repr(e).lower())

    def test_real_respondent_is_left_alone(self):
        e = self._entry("Marta")
        pmf.apply_footer_identity([e])
        self.assertFalse(e["is_internal"])
        self.assertEqual(e["first_name"], "Marta")
        self.assertEqual(e["email_domain"], "")


class _StubResponse:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload
        self.headers = {}

    def json(self):
        return self._payload


class _StubSession:
    """Stands in for `requests.Session` — no network, scripted per-endpoint replies."""

    def __init__(self, user_status, user_payload):
        self.headers = {}
        self._user = (user_status, user_payload)

    def get(self, url, timeout=None):
        return _StubResponse(*self._user)

    def post(self, url, json=None, timeout=None):
        return _StubResponse(200, [])


class TestEnrichmentIsAdditive(unittest.TestCase):
    """The regression itself: enrichment must never clear the footer verdict."""

    def _run(self, entry, user_status, user_payload, monkey):
        monkey(_StubSession(user_status, user_payload))
        pmf.enrich_entries([entry], token="stub", base_url="https://stub.invalid")

    def setUp(self):
        self._real_session = pmf.requests.Session
        self._real_sleep = pmf.time.sleep
        pmf.time.sleep = lambda *_: None

    def tearDown(self):
        pmf.requests.Session = self._real_session
        pmf.time.sleep = self._real_sleep

    def _entry(self):
        e = {"uid": "6KZhQh8CTOa20v4YzXF7O71fIsJ3", "first_name": QA_SLOT,
             "sentiment": "Very disappointed", "ts": "1786458051.837909"}
        pmf.apply_footer_identity([e])
        return e

    def test_404_leaves_the_footer_verdict_standing(self):
        e = self._entry()
        self._run(e, 404, {"message": "not found"},
                  lambda s: setattr(pmf.requests, "Session", lambda: s))
        self.assertFalse(e["aura_found"])
        self.assertEqual(e["enrichment_error"], "users_endpoint_status=404")
        self.assertTrue(e["is_internal"], "404 branch cleared the footer verdict")
        self.assertEqual(e["internal_reason"], "email_domain=aurahealth.io")

    def test_found_record_ors_the_two_verdicts(self):
        e = self._entry()
        self._run(e, 200, {"role": "user", "createdAt": "2026-01-01T00:00:00Z"},
                  lambda s: setattr(pmf.requests, "Session", lambda: s))
        self.assertTrue(e["aura_found"])
        self.assertTrue(e["is_internal"], "found branch cleared the footer verdict")
        self.assertEqual(e["internal_reason"], "email_domain=aurahealth.io")

    def test_api_only_signal_still_flags_a_plain_name_entry(self):
        e = {"uid": "aaaaaaaaaaaaaaaaaaaaaaaa", "first_name": "Marta",
             "sentiment": "Not disappointed", "ts": "1786458051.837909"}
        pmf.apply_footer_identity([e])
        self._run(e, 200, {"role": "admin", "createdAt": "2026-01-01T00:00:00Z"},
                  lambda s: setattr(pmf.requests, "Session", lambda: s))
        self.assertTrue(e["is_internal"])
        self.assertEqual(e["internal_reason"], "role=admin")


class TestBenchmarkUnresolvedResidue(unittest.TestCase):
    def test_unresolved_respondents_stay_in_the_blend_but_are_counted(self):
        entries = [
            {"sentiment": "Very disappointed", "aura_found": True,
             "content_anchored": False, "tenure_bucket": "30+"},
            {"sentiment": "Not disappointed", "aura_found": False,
             "content_anchored": None, "tenure_bucket": "unknown"},
        ]
        b = pmf.build_benchmark(entries)
        self.assertEqual(b["blended_n"], 2)
        self.assertEqual(b["n_blended_resolved"], 1)
        self.assertEqual(b["n_blended_unresolved"], 1)
        self.assertEqual(b["blended_very_pct"], 50.0)
        self.assertEqual(b["blended_resolved_very_pct"], 100.0)



# --- Blockquote normalisation (ENG-1312) ------------------------------------
#
# Every message body below is a VERBATIM `text` field from Slack's
# `conversations.history` for #ai-feedback, not a hand-written feedback string.
# The escaping is exactly what the bug turns on, so a fixture that skips it tests
# a path production never takes (CLAUDE.md -> "Verify the path production takes,
# not one that resembles it"). Slack escapes `&`, `<` and `>` across the whole
# line, so the k8bot blockquote arrives as a literal `&gt; ` prefix.
#
# k8bot switched the PMF body to a Slack blockquote on 2026-09-15 18:43 UTC.
# Both shapes are in the live window, so both must parse.

# New shape, canned survey answer. Reported as `real_with_feedback_text` before the fix.
QUOTED_DEFAULT = (
    "New PMF Survey Response\n\n"
    "&gt; *Somewhat disappointed if could no longer use Aurie*\n"
    "&gt; No Feedback\n\n"
    "Renee - Day 4 - Ds4HgLQIJbgeeLEzXloCtDaxpZ82"
)

# New shape, genuine free text — prefixed too, so the marker also reached the
# card's quoted line and the `steering` string handed to `data:profile-user`.
QUOTED_FREE_TEXT = (
    "New PMF Survey Response\n\n"
    "&gt; *Very disappointed if could no longer use Aurie*\n"
    "&gt; I always feel much better after my discussions with Aurie.\n\n"
    "Kelsey - Day 144 - mSHbiLM7UuTNKYk6BOuWmGHaNcD2"
)

# Pre-2026-09-15 shape: no blockquote anywhere. Still in every window > 1 day.
PLAIN_DEFAULT = (
    "New PMF Survey Response\n\n"
    "*Somewhat disappointed if could no longer use Aurie*\n\n"
    "No Feedback\n\n"
    "Omar - QEi1jU5xSGWuwLc2YOFGyRIfYE22"
)

PLAIN_FREE_TEXT = (
    "New PMF Survey Response\n\n"
    "*Somewhat disappointed if could no longer use Aurie*\n\n"
    "No improvements to suggest\n\n"
    "Patrice - 20DP6gTWe6N0pK3QZ3rRs6tmnYT2"
)


def _msg(text, ts="1789575304.805129"):
    """The two fields `parse_entry` reads, shaped as `conversations.history` returns them."""
    return {"type": "message", "bot_id": "BGXNL41F1", "ts": ts, "text": text}


def _parse(text):
    e = pmf.parse_entry(_msg(text), "C0AENS8D48N")
    assert e is not None, "fixture failed to parse at all"
    return e


class TestBlockquotedDefaultsAreRecognised(unittest.TestCase):
    """The defect: a `&gt; `-prefixed canned survey answer read as real free text.

    Both consequences are silent. `counts.real_with_feedback_text` /
    `real_without_feedback_text` were 26/33 on the 2026-09-16 `--window 7d` run where
    the truth is 22/37; and `mode=cadence` posted a full card quoting
    `"> No Feedback"` while `mode=interactive` deep-profiled the respondent
    through the whole `data:profile-user` chain for writing nothing.
    """

    def test_quoted_default_is_a_default(self):
        e = _parse(QUOTED_DEFAULT)
        self.assertTrue(e["feedback_is_default"])

    def test_quoted_default_keeps_no_marker_in_the_stored_text(self):
        # The stored value is what the cadence card quotes, so it must be clean.
        e = _parse(QUOTED_DEFAULT)
        self.assertEqual(e["feedback"], "No Feedback")

    def test_the_other_observed_defaults_normalise_too(self):
        for default in ("No Feedback", "only_tried_briefly", "other", "Not sure yet"):
            with self.subTest(default=default):
                body = QUOTED_DEFAULT.replace("&gt; No Feedback", f"&gt; {default}")
                e = _parse(body)
                self.assertTrue(e["feedback_is_default"], default)
                self.assertEqual(e["feedback"], default)

    def test_a_literal_unescaped_marker_is_handled_too(self):
        # Slack escapes `>` today, but the detector must not depend on that.
        e = _parse(QUOTED_DEFAULT.replace("&gt;", ">"))
        self.assertTrue(e["feedback_is_default"])
        self.assertEqual(e["feedback"], "No Feedback")

    def test_plain_shape_still_works(self):
        # The fix must not regress the 404 of 413 messages in the window that
        # predate the template change.
        e = _parse(PLAIN_DEFAULT)
        self.assertTrue(e["feedback_is_default"])
        self.assertEqual(e["feedback"], "No Feedback")


class TestGenuineFreeTextSurvives(unittest.TestCase):
    """The negatives. Each one, inverted, is a real respondent silently erased."""

    def test_quoted_free_text_is_not_a_default_and_loses_its_marker(self):
        e = _parse(QUOTED_FREE_TEXT)
        self.assertFalse(e["feedback_is_default"])
        self.assertEqual(
            e["feedback"], "I always feel much better after my discussions with Aurie."
        )

    def test_plain_free_text_is_not_a_default(self):
        e = _parse(PLAIN_FREE_TEXT)
        self.assertFalse(e["feedback_is_default"])
        self.assertEqual(e["feedback"], "No improvements to suggest")

    def test_an_answer_that_merely_starts_with_a_quote_character_is_not_a_default(self):
        # Old shape, respondent typed `>` themselves — Slack escapes it identically
        # to a blockquote marker. The sentiment line is NOT quoted, which is the
        # only thing distinguishing the two, so the character is content: keep it.
        body = PLAIN_DEFAULT.replace("\n\nNo Feedback\n\n", "\n\n&gt; 5 minutes would help\n\n")
        e = _parse(body)
        self.assertFalse(e["feedback_is_default"])
        self.assertEqual(e["feedback"], "> 5 minutes would help")

    def test_a_respondents_own_marker_survives_inside_a_quoted_message(self):
        # New shape AND a respondent-typed `>`. Exactly one marker is the render's;
        # the `^` anchor is what limits the strip to it.
        body = QUOTED_DEFAULT.replace("&gt; No Feedback", "&gt; &gt;5 minutes would help")
        e = _parse(body)
        self.assertFalse(e["feedback_is_default"])
        self.assertEqual(e["feedback"], ">5 minutes would help")

    def test_a_default_inside_a_longer_sentence_is_not_a_default(self):
        for filler in (
            "No Feedback is a strange option to be offered",
            "I picked other because nothing fit",
            "honestly, not sure yet",
        ):
            with self.subTest(filler=filler):
                e = _parse(QUOTED_DEFAULT.replace("&gt; No Feedback", f"&gt; {filler}"))
                self.assertFalse(e["feedback_is_default"], filler)
                self.assertEqual(e["feedback"], filler)

    def test_a_quoted_test_entry_still_trips_the_test_filter(self):
        # `apply_test_filter` reads the stored `feedback`, so an un-normalised
        # `&gt; test` would have slipped a QA entry into the denominator.
        e = _parse(QUOTED_DEFAULT.replace("&gt; No Feedback", "&gt; test"))
        pmf.apply_test_filter([e])
        self.assertTrue(e["is_test"])
        self.assertEqual(e["test_reason"], "feedback-text-test")


class TestEntitiesBeyondTheMarker(unittest.TestCase):
    """Slack escapes the whole line, so `&` and `<` reach `feedback` too.

    Not observed in the 45-day corpus (only `&gt;` appears, 18 times = 9 messages
    x 2 lines), so this is defensive rather than currently load-bearing — but it
    is the same escaping, and it corrupts a quoted card line when it happens.
    """

    def test_ampersand_and_angle_brackets_are_unescaped(self):
        body = QUOTED_FREE_TEXT.replace(
            "&gt; I always feel much better after my discussions with Aurie.",
            "&gt; Aurie &amp; I talk daily &lt;3",
        )
        e = _parse(body)
        self.assertEqual(e["feedback"], "Aurie & I talk daily <3")

    def test_entities_are_unescaped_in_the_plain_shape_too(self):
        body = PLAIN_DEFAULT.replace("\n\nNo Feedback\n\n", "\n\nme &amp; Aurie\n\n")
        e = _parse(body)
        self.assertEqual(e["feedback"], "me & Aurie")

    def test_unescaping_happens_exactly_once(self):
        # A respondent who literally typed `&gt;` arrives as `&amp;gt;`. One pass
        # gives back what they typed; two would manufacture a marker.
        body = PLAIN_DEFAULT.replace("\n\nNo Feedback\n\n", "\n\n&amp;gt; not a quote\n\n")
        e = _parse(body)
        self.assertEqual(e["feedback"], "&gt; not a quote")


class TestNormalizeQuotedLine(unittest.TestCase):
    """The helper directly, including the ordering the docstring commits to."""

    def test_marker_strip_is_gated_on_the_quoted_flag(self):
        self.assertEqual(pmf.normalize_quoted_line("&gt; No Feedback", True), "No Feedback")
        self.assertEqual(pmf.normalize_quoted_line("&gt; No Feedback", False), "> No Feedback")

    def test_strip_runs_before_unescape(self):
        # `&amp;gt;` must NOT become a marker and then be eaten.
        self.assertEqual(pmf.normalize_quoted_line("&amp;gt; hi", True), "&gt; hi")

    def test_a_line_with_no_marker_is_returned_intact(self):
        self.assertEqual(pmf.normalize_quoted_line("No Feedback", True), "No Feedback")



class TestDayNFooterSegment(unittest.TestCase):
    """The `- Day <N> -` segment the same 2026-09-15 template change introduced.

    Coupled to the blockquote in 9/9 observed messages and 0 carrying either
    alone, so it is one deliberate k8bot change, not a defect — but the greedy
    `^(.+) - (UID)$` swallowed it into `first_name`, putting "Renee - Day 4" in
    the name slot of every card. Optional, because the pre-change shape is still
    404 of 413 messages in a 45-day window.
    """

    def test_day_segment_is_captured_not_swallowed_into_the_name(self):
        e = _parse(QUOTED_DEFAULT)
        self.assertEqual(e["first_name"], "Renee")
        self.assertEqual(e["day_n"], 4)
        self.assertEqual(e["uid"], "Ds4HgLQIJbgeeLEzXloCtDaxpZ82")

    def test_three_digit_day_parses(self):
        e = _parse(QUOTED_FREE_TEXT)
        self.assertEqual(e["first_name"], "Kelsey")
        self.assertEqual(e["day_n"], 144)

    def test_old_footer_has_no_day_and_keeps_its_whole_name(self):
        e = _parse(PLAIN_DEFAULT)
        self.assertEqual(e["first_name"], "Omar")
        self.assertIsNone(e["day_n"])

    def test_a_name_containing_a_dash_survives_the_non_greedy_match(self):
        # Non-greedy `.+?` must still backtrack to the full name when the shortest
        # candidate leaves the rest unmatchable. Real footer from the corpus.
        body = PLAIN_DEFAULT.replace(
            "Omar - QEi1jU5xSGWuwLc2YOFGyRIfYE22",
            "Mi Prieta - 3KkXB7gcMbPBKtQgbif3guTrd3u2",
        )
        e = _parse(body)
        self.assertEqual(e["first_name"], "Mi Prieta")
        self.assertIsNone(e["day_n"])

    def test_a_mailto_slot_is_unaffected_by_the_optional_segment(self):
        # The ENG-1141 path: the name slot holds an address, no Day segment.
        body = PLAIN_DEFAULT.replace(
            "Omar - QEi1jU5xSGWuwLc2YOFGyRIfYE22",
            f"{QA_SLOT} - 6KZhQh8CTOa20v4YzXF7O71fIsJ3",
        )
        e = _parse(body)
        self.assertEqual(e["first_name"], QA_SLOT)
        self.assertIsNone(e["day_n"])
        pmf.apply_footer_identity([e])
        self.assertTrue(e["is_internal"])
        self.assertEqual(e["first_name"], "")

    def test_a_provider_prefixed_uid_still_parses_with_a_day_segment(self):
        body = QUOTED_DEFAULT.replace(
            "Renee - Day 4 - Ds4HgLQIJbgeeLEzXloCtDaxpZ82",
            "Renee - Day 4 - google:111564839201847362514",
        )
        e = _parse(body)
        self.assertEqual(e["first_name"], "Renee")
        self.assertEqual(e["day_n"], 4)
        self.assertEqual(e["uid"], "google:111564839201847362514")

    def test_day_n_does_not_become_a_second_source_of_tenure(self):
        # `tenure_bucket` stays derived from the Aura API's `aiCreatedAt`. Capturing
        # k8bot's count must not quietly start driving segmentation.
        e = _parse(QUOTED_DEFAULT)
        self.assertNotIn("tenure_bucket", e)



# --- Producer-generated fixtures --------------------------------------------
#
# The ones above are verbatim Slack reads, which can only cover shapes that
# happened to occur. These are generated by running the PRODUCER's own code —
# IntelligenceService `src/models/survey.js`, the `[givenName, day, id]
# .filter(Boolean)` footer join and the per-line `> ` quote map — then applying
# Slack's `& < >` escaping. That reaches branches the channel has not yet
# produced. The generator's output for a nil-feedback survey is byte-identical
# to the real Slack message for the same inputs, which is what licenses the rest.

PRODUCED = {
    # Every line of a multi-line answer carries its own marker (the producer maps
    # over `.split("\n")`), so the strip has to be per line, not once per body.
    "multiline_free_text": (
        "New PMF Survey Response\n\n"
        "&gt; *Somewhat disappointed if could no longer use Aurie*\n"
        "&gt; Being able to talk to\n"
        "&gt; Someone.\n\n"
        "Cristina - Day 12 - eJ9M49SFg0bzgpvD6hlPPGDqHN83"
    ),
    # `aiDayCount` absent -> the day part drops out of the join entirely.
    "no_day_count": (
        "New PMF Survey Response\n\n"
        "&gt; *Very disappointed if could no longer use Aurie*\n"
        "&gt; It helps\n\n"
        "Jan - 00liQEFZvbMvdlLewcRmUmQ8Fmg2"
    ),
    # Empty `givenName` -> `filter(Boolean)` drops the NAME, not the day.
    "empty_given_name": (
        "New PMF Survey Response\n\n"
        "&gt; *Not disappointed if could no longer use Aurie*\n"
        "&gt; other\n\n"
        "Day 5 - QEi1jU5xSGWuwLc2YOFGyRIfYE22"
    ),
    # Neither name nor day: the footer is a bare id.
    "bare_uid": (
        "New PMF Survey Response\n\n"
        "&gt; *Not disappointed if could no longer use Aurie*\n"
        "&gt; other\n\n"
        "QEi1jU5xSGWuwLc2YOFGyRIfYE22"
    ),
    "ampersand": (
        "New PMF Survey Response\n\n"
        "&gt; *Very disappointed if could no longer use Aurie*\n"
        "&gt; me &amp; Aurie &lt;3\n\n"
        "Kim - Day 9 - jaY6R6EOgfc9BAx7eBEn6GDVzde2"
    ),
}


class TestProducerGeneratedShapes(unittest.TestCase):
    def test_every_line_of_a_multiline_answer_is_stripped(self):
        e = _parse(PRODUCED["multiline_free_text"])
        self.assertEqual(e["feedback"], "Being able to talk to\nSomeone.")
        self.assertEqual(e["first_name"], "Cristina")
        self.assertEqual(e["day_n"], 12)

    def test_missing_day_count_drops_only_the_day(self):
        e = _parse(PRODUCED["no_day_count"])
        self.assertEqual(e["first_name"], "Jan")
        self.assertIsNone(e["day_n"])

    def test_empty_given_name_is_not_read_as_a_person_called_day_5(self):
        # Both leading parts are independently optional. Without the lookahead the
        # name group eats "Day 5", and the card renders it as the respondent's name.
        e = _parse(PRODUCED["empty_given_name"])
        self.assertEqual(e["first_name"], "")
        self.assertEqual(e["day_n"], 5)
        self.assertEqual(e["uid"], "QEi1jU5xSGWuwLc2YOFGyRIfYE22")

    def test_a_bare_uid_footer_still_yields_an_entry(self):
        # A respondent with no display name and no day must not vanish from the
        # Sean-Ellis denominator — the silent failure this shape used to cause.
        e = _parse(PRODUCED["bare_uid"])
        self.assertEqual(e["first_name"], "")
        self.assertIsNone(e["day_n"])
        self.assertEqual(e["uid"], "QEi1jU5xSGWuwLc2YOFGyRIfYE22")

    def test_escaped_entities_round_trip_back_to_what_was_typed(self):
        e = _parse(PRODUCED["ampersand"])
        self.assertEqual(e["feedback"], "me & Aurie <3")



# --- Survey choice values (ENG-1313) ----------------------------------------

def _produced(feedback, level="Not disappointed", name="Manuel", day=3,
              uid="LkLSxbyQwhgs3rRUf7nTBdFKaKq2"):
    """Build a body the way IntelligenceService `src/models/survey.js` does.

    Quote map is per line, footer joins the non-empty parts, then Slack escapes
    `&`, `<` and `>`. Same generator the PRODUCED fixtures above came from, whose
    output was checked byte-identical against a real Slack message.
    """
    quoted = "\n".join(
        f"> {ln}" for ln in f"*{level} if could no longer use Aurie*\n{feedback}".split("\n")
    )
    footer = " - ".join(x for x in (name, f"Day {day}" if day is not None else None, uid) if x)
    body = f"New PMF Survey Response\n\n{quoted}\n\n{footer}"
    return body.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class TestSurveyChoiceValues(unittest.TestCase):
    """Picking a canned option is not writing free text.

    `did_not_help` (25 occurrences in 857 messages over 4.5 months),
    `did_not_understand` (10) and `did_not_need` (4) were absent from the set, so
    39 respondents — 4.6% of the corpus — were counted as having written real
    feedback. Same two silent consequences as the blockquote defect: wrong
    Sean-Ellis split, and an expensive `data:profile-user` deep profile for
    someone who wrote nothing.
    """

    def test_the_set_is_exactly_the_five_options_the_app_offers(self):
        # Source of truth: AuraConsumerApp PMFNotDisappointedOption.swift. Pinned
        # as an exact set so a silent widening OR narrowing fails here, rather
        # than showing up as a drifting Sean-Ellis denominator.
        self.assertEqual(
            pmf.SURVEY_CHOICE_VALUES,
            {"did_not_help", "did_not_need", "did_not_understand",
             "only_tried_briefly", "other"},
        )

    def test_every_choice_value_is_a_default(self):
        for value in sorted(pmf.SURVEY_CHOICE_VALUES):
            with self.subTest(value=value):
                e = _parse(_produced(value))
                self.assertTrue(e["feedback_is_default"], value)
                self.assertEqual(e["feedback"], value)

    def test_the_three_that_were_missing_are_the_regression(self):
        for value in ("did_not_help", "did_not_understand", "did_not_need"):
            with self.subTest(value=value):
                self.assertTrue(_parse(_produced(value))["feedback_is_default"])

    def test_the_blank_placeholder_is_a_default_on_every_sentiment_path(self):
        # `${result.feedback || 'No Feedback'}` — not a choice value, so unlike the
        # five it is not confined to the "Not disappointed" path.
        for level in ("Not disappointed", "Somewhat disappointed", "Very disappointed"):
            with self.subTest(level=level):
                self.assertTrue(_parse(_produced("No Feedback", level=level))["feedback_is_default"])

    def test_the_placeholder_is_not_filed_as_a_choice_value(self):
        # Keeping the provenance separate is the point: one comes from the app's
        # enum, the other from the backend's template.
        self.assertNotIn("no feedback", pmf.SURVEY_CHOICE_VALUES)
        self.assertIn("no feedback", pmf.NON_FREE_TEXT_ANSWERS)

    def test_prose_that_merely_resembles_a_choice_value_is_real_feedback(self):
        # The machine values are underscored tokens; a human writing the same idea
        # in words has written real feedback and must stay in the denominator.
        for typed in (
            "it did not help",
            "did not help me at all",
            "did_not_help because the voice kept cutting out",
            "other apps do this better",
        ):
            with self.subTest(typed=typed):
                e = _parse(_produced(typed))
                self.assertFalse(e["feedback_is_default"], typed)
                self.assertEqual(e["feedback"], typed)

    def test_matching_is_exact_after_normalisation_not_substring(self):
        e = _parse(_produced("I chose other but really did_not_need it"))
        self.assertFalse(e["feedback_is_default"])

    def test_a_choice_value_is_recognised_regardless_of_sentiment_path(self):
        # The app only offers these on "Not disappointed", but the parser must not
        # depend on that — a producer change would silently un-default them.
        e = _parse(_produced("did_not_help", level="Very disappointed"))
        self.assertTrue(e["feedback_is_default"])

    def test_a_choice_value_is_not_a_test_entry(self):
        e = _parse(_produced("did_not_help"))
        pmf.apply_test_filter([e])
        self.assertFalse(e["is_test"])



# --- Legacy user-id shapes + the unparsed tripwire (ENG-1315) ---------------

class TestLegacyUserIdShapes(unittest.TestCase):
    """A 2017 account's id is a UUID, and the footer pattern only knew Firebase.

    One respondent in 857 — `Katharine`, an Aura account created 2017-02-20 who
    joined Aurie in 2026 — was dropped outright. Not a test account (`role: null`,
    `isAIReviewer: false`, `test: null`, real email and name), and exactly the
    tenure-mature respondent the Sean-Ellis benchmark weights most.
    """

    # Verbatim `text` from Slack for the dropped message.
    UUID_FOOTER_MSG = (
        "New PMF Survey Response\n\n"
        "*Not disappointed if could no longer use Aurie*\n\n"
        "other\n\n"
        "Katharine - 399e2b2e-b125-41ae-9a5e-e0dead9c90c3"
    )

    def test_a_legacy_uuid_respondent_is_not_dropped(self):
        e = _parse(self.UUID_FOOTER_MSG)
        self.assertEqual(e["uid"], "399e2b2e-b125-41ae-9a5e-e0dead9c90c3")
        self.assertEqual(e["first_name"], "Katharine")
        self.assertEqual(e["sentiment"], "Not disappointed")

    def test_a_uuid_still_works_with_the_day_segment(self):
        body = self.UUID_FOOTER_MSG.replace(
            "Katharine - 399e", "Katharine - Day 7 - 399e")
        e = _parse(body)
        self.assertEqual(e["day_n"], 7)
        self.assertEqual(e["uid"], "399e2b2e-b125-41ae-9a5e-e0dead9c90c3")

    def test_the_other_two_id_shapes_still_parse(self):
        for uid in ("Ds4HgLQIJbgeeLEzXloCtDaxpZ82", "google:111564839201847362514"):
            with self.subTest(uid=uid):
                body = self.UUID_FOOTER_MSG.replace(
                    "399e2b2e-b125-41ae-9a5e-e0dead9c90c3", uid)
                self.assertEqual(_parse(body)["uid"], uid)

    def test_the_id_pattern_stays_validating_not_permissive(self):
        # The id is the last field on the line, so a loose pattern would turn a
        # malformed footer into a fabricated user id rather than a reported failure.
        for footer in ("Katharine - not-an-id", "Katharine", "Katharine - 12345"):
            with self.subTest(footer=footer):
                body = self.UUID_FOOTER_MSG.replace(
                    "Katharine - 399e2b2e-b125-41ae-9a5e-e0dead9c90c3", footer)
                self.assertIsNone(pmf.parse_entry(_msg(body), "C0AENS8D48N"))


class TestUnparsedTripwire(unittest.TestCase):
    """The reason both defects stayed hidden: a parse failure left no trace.

    A dropped message makes the run look clean and merely shorter, so neither the
    UUID respondent nor the 2026-09-15 template change produced any signal.
    """

    def test_a_header_bearing_message_that_fails_to_parse_is_reported(self):
        bad = _msg("New PMF Survey Response\n\n*Very disappointed if could no longer "
                   "use Aurie*\n\nnice\n\nSomeone - not-a-valid-id", ts="1789575304.805129")
        entries, unparsed = pmf.partition_messages([bad], "C0AENS8D48N")
        self.assertEqual(entries, [])
        self.assertEqual(len(unparsed), 1)
        self.assertEqual(unparsed[0]["footer"], "Someone - not-a-valid-id")
        self.assertIn("1789575304", unparsed[0]["permalink"])

    def test_a_good_message_is_not_reported_as_unparsed(self):
        # Without this the tripwire could degenerate into always firing.
        entries, unparsed = pmf.partition_messages([_msg(QUOTED_DEFAULT)], "C0AENS8D48N")
        self.assertEqual(len(entries), 1)
        self.assertEqual(unparsed, [])

    def test_other_channel_traffic_is_not_a_parse_failure(self):
        # #ai-feedback carries non-PMF messages; those are not failures.
        entries, unparsed = pmf.partition_messages(
            [_msg("New Cancellation Feedback\n\nsomething else")], "C0AENS8D48N")
        self.assertEqual((entries, unparsed), ([], []))

    def test_main_uses_partition_messages_rather_than_its_own_loop(self):
        # The wiring half: a tripwire main() does not call reports 0 forever, which
        # is indistinguishable from a clean run.
        src = SCRIPT.read_text()
        main_body = src[src.index("def main("):]
        self.assertIn("partition_messages(raw, args.channel)", main_body)
        self.assertNotIn("for m in raw:", main_body)



class TestCountsKeyNames(unittest.TestCase):
    """The `counts` block is the run's summary; a key rename is silent to a reader.

    `real_typeform_default` was renamed to `real_without_feedback_text` — the
    survey is native in AuraConsumerApp, never a Typeform form, and the old name
    sent an investigation looking for one. Pinned so the pair cannot drift apart
    or quietly revert.
    """

    def test_the_counts_block_names_the_complementary_pair(self):
        src = SCRIPT.read_text()
        counts = src[src.index('"counts": {'):]
        counts = counts[:counts.index("},")]
        self.assertIn('"real_with_feedback_text"', counts)
        self.assertIn('"real_without_feedback_text"', counts)
        self.assertNotIn("typeform", counts.lower())

    def test_the_two_halves_partition_the_real_entries(self):
        # Whatever they are called, they must still sum to `real` — the property a
        # rename could break without any test noticing.
        src = SCRIPT.read_text()
        self.assertIn('"real_with_feedback_text": len(with_text)', src)
        self.assertIn('"real_without_feedback_text": len(real) - len(with_text)', src)


if __name__ == "__main__":
    unittest.main()
