#!/usr/bin/env python3
"""`save_row.py --build` must pick a span that can actually build a row.

`pick_source_span` matched the parent task span by the literal name
`streamChat`. The Responses migration renamed that span to `streamResponse`, so
the match went empty and selection fell through to the next branch -- the latest
`llm` child, which never carries `metadata.contextInstructions`. The clause that
would have caught it ("any span with both input and contextInstructions") sat
*after* the `llm` branch and was therefore unreachable: a weaker fallback
shadowing a stronger one.

Why this needed a test rather than a one-line rename. The failure surfaced as

    Source span for '<id>' has empty `metadata.contextInstructions`.

which reads as *Braintrust stopped recording the context* -- a producer problem
in another repo -- when the context was present the whole time, 5509 characters
of it, on a span the selector had stopped recognising. That misdirection is the
expensive part: the investigation it came from (ENG-1372) proposed fixing the
IntelligenceService span population, which would have been the wrong repo.

The consequence was total, not partial: NO production conversation could be
turned into a Braintrust eval case, and nothing monitors that the eval corpus
has stopped growing.

Fixture, not hand-built dicts
----------------------------
Per CLAUDE.md's *Verify the path production takes, not one that resembles it*,
the fixture is the real 11-span shape returned by `fetch_conversation_spans` for
a production conversation on 2026-09-23 -- three `streamResponse` parents with
context, interleaved with and *followed by* `llm` children without it. That
trailing order is the whole defect: a selector that takes the last `llm` span
lands on one that cannot build a row.

Conversation text is scrubbed (the source conversation is sensitive family
material) because the defect is structural -- span names, types, and which spans
carry `contextInstructions`. None of the assertions below read message content.
"""
from __future__ import annotations

import json
import pathlib
import re
import sys
import unittest

_HERE = pathlib.Path(__file__).resolve().parent
_REPO = _HERE.parent
_SCRIPTS = _REPO / "plugins/data/skills/save-to-braintrust-dataset/scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import save_row  # noqa: E402

FIXTURE = _HERE / "fixtures" / "bt_row_source_spans.json"


def load_spans() -> list[dict]:
    return json.loads(FIXTURE.read_text())


def span_name(span: dict | None) -> str | None:
    return ((span or {}).get("span_attributes") or {}).get("name")


class RealProductionShape(unittest.TestCase):
    """The regression, against the span shape production actually emits."""

    def setUp(self) -> None:
        self.spans = load_spans()

    def test_fixture_still_has_the_shape_that_triggered_the_defect(self):
        # Guards the fixture itself: if a future edit removes the trailing
        # context-less llm spans, every assertion below passes vacuously.
        self.assertTrue(
            any(span_name(s) == "streamResponse" for s in self.spans),
            "fixture lost its Responses-API parent spans",
        )
        self.assertFalse(
            save_row.can_build_row(self.spans[-1]),
            "fixture's last span must NOT be usable, or it cannot show the bug",
        )
        usable = [s for s in self.spans if save_row.can_build_row(s)]
        self.assertTrue(usable, "fixture must contain at least one usable span")

    def test_picks_a_span_that_can_build_a_row(self):
        picked = save_row.pick_source_span(self.spans)
        self.assertIsNotNone(picked)
        self.assertTrue(
            save_row.can_build_row(picked),
            "selected a span missing input or contextInstructions",
        )

    def test_does_not_fall_through_to_a_trailing_llm_child(self):
        # The exact old behaviour: last `llm` span wins and carries no context.
        picked = save_row.pick_source_span(self.spans)
        llms = [
            s for s in self.spans
            if (s.get("span_attributes") or {}).get("type") == "llm"
        ]
        self.assertTrue(llms, "fixture should contain llm children")
        self.assertIsNot(picked, llms[-1])

    def test_prefers_the_latest_parent_span(self):
        picked = save_row.pick_source_span(self.spans)
        self.assertEqual(span_name(picked), "streamResponse")
        parents = [
            s for s in self.spans
            if span_name(s) in save_row.PARENT_SPAN_NAMES
            and save_row.can_build_row(s)
        ]
        self.assertIs(picked, parents[-1], "should take the latest usable parent")

    def test_build_row_succeeds_end_to_end(self):
        # The thing that was broken was not span choice in the abstract -- it
        # was that `--build` exited. Drive the real builder.
        row = save_row.build_row(
            self.spans,
            conversation_id="16EA51C5-C1D9-47A2-9AD9-156CACA84FE8",
            case_id="case", pattern="pattern", severity="low",
            observed_rating=None, source="http://example.invalid",
            source_date="2026-09-23", expected="must not withhold", tags=[],
        )
        self.assertTrue(row["metadata"]["contextInstructions"].strip())
        self.assertTrue(row["input"], "row must carry conversation turns")
        self.assertEqual(row["metadata"]["prompterName"], "coach-prompter")


class NameIsAPreferenceNotAGate(unittest.TestCase):
    """Both API generations work, and an unknown name is not disqualifying."""

    @staticmethod
    def parent(name: str) -> dict:
        return {
            "span_attributes": {"name": name, "type": "task"},
            "input": [{"role": "user", "content": "hi"}],
            "metadata": {"contextInstructions": "ctx", "prompterName": "coach-prompter"},
        }

    @staticmethod
    def llm_child() -> dict:
        return {
            "span_attributes": {"name": "openaiStreamResponse", "type": "llm"},
            "input": [{"role": "user", "content": "hi"}],
            "metadata": {"prompterName": "coach-prompter"},
        }

    def test_chat_completions_era_parent_still_selected(self):
        spans = [self.parent("streamChat"), self.llm_child()]
        self.assertEqual(span_name(save_row.pick_source_span(spans)), "streamChat")

    def test_responses_era_parent_selected(self):
        spans = [self.parent("streamResponse"), self.llm_child()]
        self.assertEqual(span_name(save_row.pick_source_span(spans)), "streamResponse")

    def test_a_future_rename_still_yields_a_usable_span(self):
        # The durability property. The next rename must degrade to "picked a
        # usable span" rather than back to "picked one that cannot build a row"
        # -- otherwise this fix only buys time until the span is renamed again.
        spans = [self.parent("streamWhateverComesNext"), self.llm_child()]
        picked = save_row.pick_source_span(spans)
        self.assertTrue(save_row.can_build_row(picked))

    def test_no_usable_span_returns_something_for_the_specific_error(self):
        # When nothing can build a row the caller should still reach the
        # downstream message naming what is missing, not a bare "no span".
        spans = [self.llm_child()]
        self.assertIsNotNone(save_row.pick_source_span(spans))

    def test_no_spans_at_all_is_none(self):
        self.assertIsNone(save_row.pick_source_span([]))


class FixtureShapeMatchesProducer(unittest.TestCase):
    """A fixture describing a row shape Braintrust never emits proves nothing."""

    def test_fixture_keys_are_a_subset_of_the_selected_columns(self):
        source = pathlib.Path(save_row.__file__).read_text()
        query = re.search(
            r"def fetch_conversation_spans.*?SELECT(.*?)FROM",
            source,
            re.S,
        )
        self.assertIsNotNone(query, "could not locate the BTQL SELECT list")
        selected = {
            c.strip() for c in query.group(1).replace("\n", " ").split(",") if c.strip()
        }
        for span in load_spans():
            self.assertLessEqual(set(span.keys()), selected)

    def test_span_attributes_and_metadata_are_selected(self):
        # The two columns selection reads. If either left the SELECT list the
        # fixture would keep passing while production returned nothing usable.
        source = pathlib.Path(save_row.__file__).read_text()
        query = re.search(
            r"def fetch_conversation_spans.*?SELECT(.*?)FROM", source, re.S
        )
        self.assertIn("span_attributes", query.group(1))
        self.assertIn("metadata", query.group(1))


if __name__ == "__main__":
    unittest.main()
