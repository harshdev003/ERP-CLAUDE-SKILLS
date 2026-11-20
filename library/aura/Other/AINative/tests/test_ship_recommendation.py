"""Flag-name rules for product:ship-recommendation.

The defect these guard against is silent in every direction: ContentService
assigns a non-compliant flag to nobody, nothing errors, the experiment runs to
completion, and the readout is a dead heat indistinguishable from a real null
result. There is no log line to notice and no alert to fire, which is exactly
why the rule belongs in a tested function rather than in a careful reader.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(__file__), "..", "plugins", "product", "skills",
    "ship-recommendation", "scripts"))

import flag_rules as fr  # noqa: E402


class TestFlagCheck(unittest.TestCase):
    def test_a_content_prefixed_flag_is_assignable(self):
        ok, problems = fr.check_flag("contentRecommendationEngine")
        self.assertTrue(ok)
        self.assertEqual(problems, [])

    def test_the_real_prod_5985_name_is_refused(self):
        # PROD-5985 shipped as `ab:newRecommendationEngine`. As named it would
        # have been assigned to nobody.
        ok, problems = fr.check_flag("newRecommendationEngine")
        self.assertFalse(ok)
        self.assertIn("NOBODY", problems[0])

    def test_the_include_list_is_a_valid_escape_hatch(self):
        ok, problems = fr.check_flag("legacyRanker", include_list=["legacyRanker"])
        self.assertTrue(ok)
        self.assertTrue(problems[0].startswith("NOTE:"))

    def test_the_include_list_note_is_advice_not_a_failure(self):
        # A NOTE must not block a ship — it is a nudge toward the convention.
        ok, _p = fr.check_flag("legacyRanker", include_list=["legacyRanker"])
        self.assertTrue(ok)

    def test_an_unrelated_include_list_does_not_rescue_the_flag(self):
        ok, _p = fr.check_flag("newRanker", include_list=["somethingElse"])
        self.assertFalse(ok)

    def test_an_empty_name_is_refused(self):
        ok, problems = fr.check_flag("")
        self.assertFalse(ok)
        self.assertIn("no experiment name", problems[0])

    def test_a_malformed_name_is_refused_even_with_the_prefix(self):
        for bad in ("content-recs", "content recs", "content.recs", "1contentRecs"):
            ok, _p = fr.check_flag(bad)
            self.assertFalse(ok, bad)

    def test_the_prefix_match_is_case_sensitive(self):
        # ContentService does a literal startsWith("content"). "Content…" fails.
        ok, _p = fr.check_flag("ContentRecommendationEngine")
        self.assertFalse(ok)


class TestSuggestion(unittest.TestCase):
    def test_it_prefixes_and_preserves_camel_case(self):
        self.assertEqual(fr.suggest_flag("newRecommendationEngine"),
                         "contentNewRecommendationEngine")

    def test_an_already_compliant_flag_is_returned_unchanged(self):
        self.assertEqual(fr.suggest_flag("contentFoo"), "contentFoo")

    def test_an_empty_name_suggests_nothing(self):
        self.assertEqual(fr.suggest_flag(""), "")


class TestCardTitle(unittest.TestCase):
    def test_it_reads_the_shared_ab_card_convention(self):
        self.assertEqual(
            fr.flag_from_card_title("ab:contentRecsV2: Rank the Today row by similarity"),
            "contentRecsV2")

    def test_a_title_without_the_convention_yields_nothing(self):
        self.assertEqual(fr.flag_from_card_title("Rank the Today row"), "")

    def test_surrounding_whitespace_is_tolerated(self):
        self.assertEqual(fr.flag_from_card_title("  ab:contentX:  Some title  "), "contentX")

    def test_a_flag_extracted_from_a_card_is_then_checked(self):
        # The path that matters end to end: card title -> flag -> verdict.
        flag = fr.flag_from_card_title("ab:newRecommendationEngine: Build a new algorithm")
        ok, _p = fr.check_flag(flag)
        self.assertFalse(ok)
        self.assertEqual(fr.suggest_flag(flag), "contentNewRecommendationEngine")


if __name__ == "__main__":
    unittest.main()
