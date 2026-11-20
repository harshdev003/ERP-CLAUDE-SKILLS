"""Tests for `data:update-conversations-top10`'s row filter.

The defect these pin: a sweep-recorded row carries every canonical heading with
a templated one-liner under each, so a heading-presence check waves it into the
clustering pool with nothing to cluster on. The load-bearing assertions are the
negative ones — that a full-heading stub is rejected, and that it stays rejected
when only one of the four gates would have caught it.

No network: `classify` and `parse_sections` are pure, and the Notion block shapes
are built by hand.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = (
    REPO
    / "plugins/data/skills/update-conversations-top10/scripts/classify_rows.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("classify_rows", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["classify_rows"] = mod
    spec.loader.exec_module(mod)
    return mod


cr = _load()


def heading(text: str, level: int = 2) -> dict:
    return {
        "type": f"heading_{level}",
        f"heading_{level}": {"rich_text": [{"plain_text": text}]},
    }


def para(text: str) -> dict:
    return {"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": text}]}}


def bullet(text: str) -> dict:
    return {
        "type": "bulleted_list_item",
        "bulleted_list_item": {"rich_text": [{"plain_text": text}]},
    }


# The exact shape the conversation harvest sweep writes: five headings present
# (four canonical + Sources), every body a templated one-liner.
TEMPLATE_STUB_BLOCKS = [
    heading("Headline"),
    para("User explored their feelings with Aurie."),
    heading("What worked"),
    para("None notable."),
    heading("What didn't"),
    para("May have lacked the specific insight or validation the user needed."),
    heading("Recommended follow-up"),
    para("None."),
    heading("Sources", level=1),
    bullet("[OpenAI — conversation logs](https://example.invalid/a)"),
]


def real_analysis_sections() -> dict[str, str]:
    return {
        cr.HEADLINE: (
            "tWL0ZSuz… — brand-new user went 35 min deep on an affair topic, "
            "then rated Neutral with no feedback after a 35s deliberation. "
            "In-session care looks high-quality; the rating most plausibly "
            "reflects exit shape, not content."
        ),
        cr.WHAT_WORKED: (
            "- Somatic anchoring early (hand on chest, slow breath) — user "
            "reports 'a tad bit lighter' by exchange 4\n"
            "- Sharp validating reframes land the emotional core"
        ),
        cr.WHAT_DIDNT: (
            "- ~30 of 35 Aurie turns end with a question — for a new user in "
            "acute grief that reads as excavation\n"
            "- User signals closure and Aurie returns warmth but no forward handle"
        ),
        cr.HYPOTHESES: (
            "- Question-saturation correlates with Neutral ratings on "
            "coach-prompter — falsifiable on other rated logs\n"
            "- First-conversation-of-trial users need a 'we did something' "
            "artefact at exit"
        ),
        cr.FOLLOW_UP: (
            "- Add a Braintrust eval case for question-saturation on "
            "first-conversation rated logs\n"
            "- Prototype a 'we did something' exit artefact for first-trial users"
        ),
    }


class ParseSectionsTests(unittest.TestCase):
    def test_headings_keyed_by_markdown_form(self):
        got = cr.parse_sections(TEMPLATE_STUB_BLOCKS)
        self.assertIn(cr.HEADLINE, got)
        self.assertIn(cr.WHAT_DIDNT, got)
        self.assertEqual(got[cr.HEADLINE], "User explored their feelings with Aurie.")

    def test_sources_is_level_one_and_not_a_canonical_section(self):
        got = cr.parse_sections(TEMPLATE_STUB_BLOCKS)
        self.assertIn("# Sources", got)
        self.assertNotIn("## Sources", got)

    def test_bullets_keep_their_marker(self):
        got = cr.parse_sections(
            [heading("Hypotheses to validate"), bullet("a hypothesis")]
        )
        self.assertEqual(got[cr.HYPOTHESES], "- a hypothesis")

    def test_content_before_the_first_heading_is_dropped(self):
        got = cr.parse_sections([para("orphan"), heading("Headline"), para("real")])
        self.assertEqual(got[cr.HEADLINE], "real")


class BoilerplateTests(unittest.TestCase):
    def test_empty_markers(self):
        for text in ("None.", "None notable.", "  none  ", "N/A", "-", ""):
            self.assertTrue(cr.is_boilerplate(text), text)

    def test_templated_vertical_slot(self):
        for vertical in ("grief", "anxiety", "self_worth", "unknown"):
            self.assertTrue(
                cr.is_boilerplate(f"User explored {vertical} with Aurie."), vertical
            )
            self.assertTrue(
                cr.is_boilerplate(
                    f"User's {vertical} concerns were not adequately addressed."
                ),
                vertical,
            )

    def test_raw_rater_quote_is_not_an_analysis(self):
        self.assertTrue(cr.is_boilerplate("Aurie: hearing a voice reply to me"))

    def test_canned_sentences(self):
        self.assertTrue(
            cr.is_boilerplate("Session touched topic but lacked direction or clear breakthrough.")
        )
        self.assertTrue(cr.is_boilerplate("- Response felt slightly off-target or unclear."))

    def test_real_analysis_text_is_not_boilerplate(self):
        for text in real_analysis_sections().values():
            self.assertFalse(cr.is_boilerplate(text), text[:60])


class ClassifyTests(unittest.TestCase):
    def test_full_heading_template_stub_is_rejected(self):
        # The regression. Every canonical heading the old filter looked for is
        # present except Hypotheses, and the bodies are boilerplate.
        got = cr.classify(cr.parse_sections(TEMPLATE_STUB_BLOCKS))
        self.assertEqual(got["verdict"], cr.VERDICT_TEMPLATE_STUB)

    def test_real_analysis_passes_every_gate(self):
        got = cr.classify(real_analysis_sections())
        self.assertEqual(got["verdict"], cr.VERDICT_SYNTHESISABLE)
        self.assertTrue(all(got["gates"].values()), got["gates"])
        self.assertEqual(got["reasons"], [])

    def test_stub_that_grows_a_hypotheses_heading_still_fails(self):
        # A future sweep template emitting a stub Hypotheses section must not
        # slip through: the substance + boilerplate gates are what catch it.
        sections = cr.parse_sections(TEMPLATE_STUB_BLOCKS)
        sections[cr.HYPOTHESES] = "- None identified from this session summary."
        got = cr.classify(sections)
        self.assertEqual(got["verdict"], cr.VERDICT_TEMPLATE_STUB)
        self.assertFalse(got["gates"]["boilerplate"])
        self.assertFalse(got["gates"]["substance"])

    def test_padded_stub_that_clears_the_char_floor_still_fails_on_boilerplate(self):
        # Length alone is not evidence of content.
        sections = cr.parse_sections(TEMPLATE_STUB_BLOCKS)
        sections[cr.HYPOTHESES] = "- " + ("padding words that say nothing " * 20)
        got = cr.classify(sections)
        self.assertTrue(got["gates"]["substance"])
        self.assertFalse(got["gates"]["boilerplate"])
        self.assertEqual(got["verdict"], cr.VERDICT_TEMPLATE_STUB)

    def test_real_analysis_missing_a_heading_is_reported_separately(self):
        sections = real_analysis_sections()
        del sections[cr.FOLLOW_UP]
        got = cr.classify(sections)
        self.assertEqual(got["verdict"], cr.VERDICT_MISSING_SECTIONS)
        self.assertFalse(got["gates"]["sections"])

    def test_one_boilerplate_section_alone_does_not_condemn_a_real_row(self):
        # A genuine analysis is allowed to say "None notable." under one heading.
        sections = real_analysis_sections()
        sections[cr.WHAT_WORKED] = "None notable."
        got = cr.classify(sections)
        self.assertEqual(got["verdict"], cr.VERDICT_SYNTHESISABLE)

    def test_reasons_name_the_failing_gates(self):
        got = cr.classify(cr.parse_sections(TEMPLATE_STUB_BLOCKS))
        joined = " ".join(got["reasons"])
        self.assertIn("missing sections", joined)
        self.assertIn("narrative chars", joined)

    def test_thresholds_are_caller_overridable(self):
        sections = real_analysis_sections()
        got = cr.classify(sections, min_body_chars=10_000)
        self.assertEqual(got["verdict"], cr.VERDICT_TEMPLATE_STUB)
        self.assertFalse(got["gates"]["substance"])


class RowSummaryTests(unittest.TestCase):
    """Step 2 tells the agent not to re-fetch, so every field it names must be here."""

    ROW = {
        "id": "page-1",
        "url": "https://www.notion.so/abc",
        "properties": {
            "Title": {"title": [{"plain_text": "tWL0ZSuz… — Neutral after 35m"}]},
            "Conversation ID": {
                "rich_text": [{"plain_text": "3F0CA47A-A4C1-40D7-BED1-40CEAF59717B"}]
            },
            "User ID": {"rich_text": [{"plain_text": "tWL0ZSuzArUoLTK3LuKnPhOmr2B3"}]},
            "Rating": {"select": {"name": "Neutral"}},
            "Source Type": {"select": {"name": "Conversation Rated"}},
            "Conversation Type": {"select": {"name": "Coaching"}},
            "Prompt Version": {"rich_text": [{"plain_text": "coach-prompter:0.235.4"}]},
            "Vertical": {"select": {"name": "relationships"}},
            "Themes": {"multi_select": [{"name": "affair"}, {"name": "abandonment"}]},
        },
    }

    def test_every_clustering_field_is_extracted(self):
        got = cr._row_summary(self.ROW)
        self.assertEqual(got["conversation_id"], "3F0CA47A-A4C1-40D7-BED1-40CEAF59717B")
        self.assertEqual(got["vertical"], "relationships")
        self.assertEqual(got["rating"], "Neutral")
        self.assertEqual(got["source_type"], "Conversation Rated")
        self.assertEqual(got["conversation_type"], "Coaching")
        self.assertEqual(got["prompt_version"], "coach-prompter:0.235.4")
        self.assertEqual(got["themes"], ["affair", "abandonment"])
        self.assertEqual(got["url"], "https://www.notion.so/abc")

    def test_full_uid_is_never_emitted(self):
        # The synthesis writes to a shared page and clusters on none of it.
        got = cr._row_summary(self.ROW)
        self.assertNotIn("tWL0ZSuzArUoLTK3LuKnPhOmr2B3", json.dumps(got))

    def test_absent_optional_properties_do_not_raise(self):
        got = cr._row_summary({"id": "page-2", "url": None, "properties": {}})
        self.assertIsNone(got["vertical"])
        self.assertEqual(got["conversation_id"], "")
        self.assertEqual(got["themes"], [])


class BylineTests(unittest.TestCase):
    """The byline must account for every row in `total`, in one bucket each."""

    class _Args:
        rating_filter = "all"
        min_body_chars = cr.DEFAULT_MIN_BODY_CHARS
        min_hypothesis_chars = cr.DEFAULT_MIN_HYPOTHESIS_CHARS
        max_boilerplate_sections = cr.DEFAULT_MAX_BOILERPLATE_SECTIONS

    def _rows(self, real: int, stubs: int, unrated_real: int = 0):
        rows = []
        for i in range(real):
            rows.append({
                "id": f"real-{i}", "url": None, "title": f"real {i}",
                "rating": "Negative", "source_type": "Conversation Rated",
                "conversation_type": "Coaching", "prompt_version": "",
                "themes": [], "sections": real_analysis_sections(),
            })
        for i in range(unrated_real):
            rows.append({
                "id": f"unrated-{i}", "url": None, "title": f"unrated {i}",
                "rating": "N/A", "source_type": "Manual",
                "conversation_type": "Other", "prompt_version": "",
                "themes": [], "sections": real_analysis_sections(),
            })
        for i in range(stubs):
            rows.append({
                "id": f"stub-{i}", "url": None, "title": f"stub {i}",
                "rating": "N/A", "source_type": "Manual",
                "conversation_type": "Other", "prompt_version": "",
                "themes": [], "sections": cr.parse_sections(TEMPLATE_STUB_BLOCKS),
            })
        return rows

    def test_buckets_sum_to_total(self):
        report = cr.build_report(self._rows(23, 459), self._Args())
        self.assertEqual(report["total"], 482)
        self.assertEqual(report["synthesisable"], 23)
        self.assertEqual(report["template_stubs"], 459)
        self.assertEqual(
            report["synthesisable"]
            + report["template_stubs"]
            + report["missing_sections"]
            + report["dropped_by_rating_filter"],
            report["total"],
        )

    def test_rating_filter_drops_are_named_in_the_byline(self):
        args = self._Args()
        args.rating_filter = "rated"
        report = cr.build_report(self._rows(12, 459, unrated_real=11), args)
        self.assertEqual(report["synthesisable"], 12)
        self.assertEqual(report["dropped_by_rating_filter"], 11)
        # 12 + 459 + 11 = 482 — every row accounted for, in the byline too.
        self.assertIn("from 12 of 482 rows below", report["byline"])
        self.assertIn("459 are sweep-recorded", report["byline"])
        self.assertIn("11 carry an analysis but are unrated", report["byline"])

    def test_only_synthesisable_rows_carry_bodies(self):
        report = cr.build_report(self._rows(2, 3), self._Args())
        kept = [r for r in report["rows"] if r["verdict"] == cr.VERDICT_SYNTHESISABLE]
        dropped = [r for r in report["rows"] if r["verdict"] != cr.VERDICT_SYNTHESISABLE]
        self.assertTrue(all("sections" in r for r in kept))
        self.assertTrue(all("sections" not in r for r in dropped))

    def test_each_gate_reports_its_own_count(self):
        report = cr.build_report(self._rows(23, 459), self._Args())
        self.assertEqual(
            report["rows_passing_each_gate"],
            {"sections": 23, "hypotheses": 23, "boilerplate": 23, "substance": 23},
        )

    def test_gate_counts_describe_the_corpus_not_the_filtered_slice(self):
        # The gate counts are the "did the corpus shape move?" diagnostic, so the
        # rating filter must not shrink them — otherwise the check gets weaker
        # exactly when a filter is on, which is the same distortion as above.
        args = self._Args()
        args.rating_filter = "rated"
        report = cr.build_report(self._rows(12, 459, unrated_real=11), args)
        self.assertEqual(report["synthesisable"], 12)
        self.assertEqual(report["dropped_by_rating_filter"], 11)
        self.assertEqual(
            report["rows_passing_each_gate"],
            {"sections": 23, "hypotheses": 23, "boilerplate": 23, "substance": 23},
        )


if __name__ == "__main__":
    unittest.main()
