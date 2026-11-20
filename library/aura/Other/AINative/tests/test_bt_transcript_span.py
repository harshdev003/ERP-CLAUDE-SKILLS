#!/usr/bin/env python3
"""The Braintrust fallback must read the transcript off the span that has it.

`_bt_build_conversation` used to take `bt_rows[-1]` — the chronologically last
span — as the transcript source. The Conversation project stores side-effect and
bookkeeping spans in the same conversation, and they routinely land *after* the
real turns: on conversation `b388fa78-…` (2026-09-16) the three most recent of 23
spans were two `quick-processing-done-prompter`s and one with no `prompterName`,
all with `input.messages == []`, while the 20-message transcript sat in the
second-to-last span. The builder returned `turn_count: 0` for a fully recorded
conversation.

Why that deserves a test rather than a note: nothing raises. The conversation is
found, `completion_count` and `total_turn_count` are right, `source` says
`braintrust_fallback` — only `turns` is empty. `data:run-feedback-analysis-cadence`
Step 4c reads a 0-turn transcript on an entry older than an hour as **terminal**
and posts a "no transcript found" reply, which closes the thread permanently
(per `references/dedup.md`, the reply *is* the done-marker). So the defect does
not merely lose a transcript — it durably marks a live conversation unanalysable.

Runs against every copy of `fetch_logs.py` that carries the builder, discovered
by shape rather than by a hand-kept list, so a sixth copy is covered the day it
lands instead of silently dropping out of coverage.

Producer, not a hand-built fixture
----------------------------------
Per CLAUDE.md's *Verify the path production takes, not one that resembles it*,
the rows under test come out of the module's own `_bt_fetch_by_conversation_id`
over a stubbed BTQL transport — the same function production calls — rather than
dicts assembled here. `FixtureShapeMatchesProducer` additionally holds the
fixture's keys to the module's own BTQL `select` list, so a fixture describing a
row shape Braintrust never emits fails instead of passing.
"""
from __future__ import annotations

import pathlib
import re
import sys
import unittest

_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from reader_harness import load_reader, script_paths  # noqa: E402

# Copies expected to carry the Braintrust conversation builder. Declared
# explicitly and compared against the shape sweep in BOTH directions: a
# discovered-but-undeclared copy fails (a new fork joins coverage or is named),
# and a declared-but-undiscovered one fails (a copy that lost the builder, or a
# rename, cannot quietly leave). Deriving this from the sweep would make the
# check vacuous. `analyze-user-journal` pivots on journal entries and has no BT
# conversation builder at all — it is absent here on purpose.
EXPECTED_COPIES = {
    "analyze-user-conversations",   # canonical
    "enrich-cancellation-feedback",  # byte-identical tracked copy
    "investigate-user-rating",       # fork
    "run-feedback-analysis-cadence",  # copy of the investigate-user-rating fork
    "report-conversation-patterns",  # fork
}

_PROJECT_ID = "11111111-2222-3333-4444-555555555555"


def _builder_copies() -> list[pathlib.Path]:
    """Every `fetch_logs.py` that defines the BT conversation builder."""
    return [p for p in script_paths("fetch_logs.py")
            if "def _bt_build_conversation" in p.read_text(encoding="utf-8")]


def _skill(path: pathlib.Path) -> str:
    return path.parent.parent.name


# ---------------------------------------------------------------------------
# Fixture builders — shaped like a BTQL row, and held to that by
# FixtureShapeMatchesProducer below.
# ---------------------------------------------------------------------------

def span(span_id, created, *, messages=None, output=None, prompter="coach-prompter",
         dialogue=None):
    """One BTQL row, with the exact key set the module's `select` asks for."""
    metadata = {"prompterName": prompter, "conversationId": "CID-1"}
    if dialogue is not None:
        metadata["dialogueId"] = dialogue
    return {
        "id": span_id,
        "span_id": span_id,
        "metadata": metadata,
        "input": {"messages": list(messages or [])},
        "output": output,
        "created": created,
    }


def exchange(user_text, assistant_text):
    return [{"role": "user", "content": user_text},
            {"role": "assistant", "content": assistant_text}]


def unanswered(user_text):
    """A trailing user message with no assistant reply in the span's input.

    This is the real shape: a span's `input` is what was sent TO the model, so
    it never contains that span's own answer — which is why the builder attaches
    the span's `output` to the trailing turn afterwards.
    """
    return [{"role": "user", "content": user_text}]


class FakeBraintrust:
    """Stubs `requests` at the module boundary the BT arm actually uses.

    `_bt_resolve_project_id` issues a GET and `_bt_run_query` a POST; stubbing
    the module's `requests` attribute covers both with no live token, and keeps
    the test going through `_bt_fetch_by_conversation_id` — the real producer —
    rather than around it.
    """

    class _Resp:
        def __init__(self, payload, status=200):
            self._payload = payload
            self.status_code = status
            self.ok = status < 400

        def json(self):
            return self._payload

    def __init__(self, rows_by_cid: dict[str, list[dict]]):
        self.rows_by_cid = rows_by_cid
        self.queries: list[str] = []

    def get(self, url, **kwargs):
        return self._Resp({"objects": [{"id": _PROJECT_ID}]})

    def post(self, url, **kwargs):
        query = (kwargs.get("json") or {}).get("query", "")
        self.queries.append(query)
        match = re.search(r"metadata\.conversationId = '([^']*)'", query)
        cid = match.group(1) if match else None
        return self._Resp({"data": list(self.rows_by_cid.get(cid, []))})


class BTTranscriptSpan(unittest.TestCase):
    """Parameterised per copy by `load_tests`."""

    path: pathlib.Path = None  # type: ignore[assignment]
    skill: str = ""

    def setUp(self):
        self.mod = load_reader(self.path)

    def _build(self, rows):
        """Produce rows the way production does, then build the conversation."""
        self.mod.requests = FakeBraintrust({"CID-1": rows})
        fetched = self.mod._bt_fetch_by_conversation_id("tok", "CID-1")
        self.assertEqual(len(fetched), len(rows),
                         "the producer did not return the stubbed rows")
        return self.mod._bt_build_conversation("CID-1", fetched)

    # -- the defect ------------------------------------------------------

    def test_trailing_empty_spans_do_not_blank_the_transcript(self):
        """The reported repro: bookkeeping spans land after the real turns."""
        rows = [
            span("s1", "2026-09-16T15:50:00.000Z",
                 messages=exchange("hello", "hi there"), dialogue="d1"),
            span("s2", "2026-09-16T15:58:34.717Z",
                 messages=exchange("how do I sleep better?", "try a wind-down"),
                 output="try a wind-down routine",
                 prompter="quick-processing-sentinel-prompter", dialogue="d2"),
            span("s3", "2026-09-16T15:58:40.000Z",
                 prompter="quick-processing-done-prompter", dialogue="d3"),
            span("s4", "2026-09-16T15:58:41.000Z",
                 prompter="quick-processing-done-prompter", dialogue="d4"),
            span("s5", "2026-09-16T15:58:42.000Z", prompter=None, dialogue="d5"),
        ]
        conv = self._build(rows)
        self.assertTrue(
            conv["turns"],
            "zero turns from a conversation whose transcript is right there in "
            "span s2 — the cadence closes this thread out as unanalysable",
        )
        self.assertEqual(conv["turn_count"], len(conv["turns"]))
        self.assertEqual(
            [t["input"]["content"] for t in conv["turns"]],
            ["how do I sleep better?"],
        )

    def test_span_set_extent_still_comes_from_every_row(self):
        """Only the transcript source moved; the conversation's extent did not.

        A fix that also narrowed `started_at` / `ended_at` / `completion_count`
        to the transcript span would quietly shorten every conversation and
        under-report `total_turn_count`, which is what `transcript_truncated`
        is computed from.
        """
        rows = [
            span("s1", "2026-09-16T15:50:00.000Z", dialogue="d1"),
            span("s2", "2026-09-16T15:58:34.717Z",
                 messages=exchange("q", "a"), dialogue="d2"),
            span("s3", "2026-09-16T16:10:00.000Z", dialogue="d3"),
        ]
        conv = self._build(rows)
        self.assertEqual(conv["started_at"], "2026-09-16T15:50:00.000Z")
        self.assertEqual(conv["ended_at"], "2026-09-16T16:10:00.000Z")
        self.assertEqual(conv["completion_count"], 3)
        self.assertEqual(conv["total_turn_count"], 3)
        self.assertTrue(conv["transcript_truncated"])

    def test_output_comes_from_the_span_the_turns_came_from(self):
        """`_bt_attach_latest_output` must follow the transcript, not the clock.

        Left on `bt_rows[-1]` it staples a bookkeeping span's output onto the
        trailing turn — a well-formed transcript whose last assistant line is
        something the user was never shown, which is worse than the empty one
        because nothing about it looks wrong.
        """
        rows = [
            span("s1", "2026-09-16T15:58:34.717Z",
                 messages=unanswered("q"),
                 output="the real reply", dialogue="d1"),
            span("s2", "2026-09-16T15:58:40.000Z",
                 output="MEMORY_WRITE_OK",
                 prompter="quick-processing-done-prompter", dialogue="d2"),
        ]
        conv = self._build(rows)
        self.assertEqual(conv["turns"][-1]["output"]["content"], "the real reply")
        self.assertEqual(conv["latest_completion_id"], "s1")

    # -- negative cases: stop the rule degenerating -----------------------

    def test_latest_message_bearing_span_wins_not_the_earliest(self):
        """"Has messages" is not enough — it must be the LATEST such span.

        A fix that scanned forwards would pin every conversation to its opening
        span and drop everything said afterwards. Same empty-looking-correct
        failure, one turn long instead of zero.
        """
        rows = [
            span("s1", "2026-09-16T15:50:00.000Z",
                 messages=exchange("first", "early reply"), dialogue="d1"),
            span("s2", "2026-09-16T15:58:34.717Z",
                 messages=exchange("first", "early reply")
                 + exchange("second", "later reply"), dialogue="d2"),
            span("s3", "2026-09-16T15:58:40.000Z",
                 prompter="quick-processing-done-prompter", dialogue="d3"),
        ]
        conv = self._build(rows)
        self.assertEqual(
            [t["input"]["content"] for t in conv["turns"]], ["first", "second"]
        )

    def test_last_span_is_used_when_it_has_the_messages(self):
        """The ordinary case must not regress: nothing to skip, nothing skipped."""
        rows = [
            span("s1", "2026-09-16T15:50:00.000Z",
                 messages=exchange("old", "old reply"), dialogue="d1"),
            span("s2", "2026-09-16T15:58:34.717Z",
                 messages=exchange("newest", "newest reply"),
                 output="newest reply", dialogue="d2"),
        ]
        conv = self._build(rows)
        self.assertEqual([t["input"]["content"] for t in conv["turns"]], ["newest"])
        self.assertEqual(conv["latest_completion_id"], "s2")

    def test_all_spans_empty_reports_the_same_shape_as_before(self):
        """A genuinely empty span set is a real outcome, not a crash or a lie.

        The fallback keeps the old behaviour so callers that key off `turns: []`
        for "nothing recorded here" keep working — the change narrows what
        produces that state, it does not remove it.
        """
        rows = [
            span("s1", "2026-09-16T15:50:00.000Z", dialogue="d1"),
            span("s2", "2026-09-16T15:58:40.000Z",
                 prompter="quick-processing-done-prompter", dialogue="d2"),
        ]
        conv = self._build(rows)
        self.assertEqual(conv["turns"], [])
        self.assertEqual(conv["turn_count"], 0)
        self.assertEqual(conv["completion_count"], 2)
        self.assertEqual(conv["source"], "braintrust_fallback")

    def test_messages_key_absent_is_not_a_crash(self):
        """A span with no `input` at all must be skipped like an empty one."""
        rows = [
            span("s1", "2026-09-16T15:50:00.000Z", messages=exchange("q", "a")),
            {"id": "s2", "span_id": "s2", "metadata": {}, "input": None,
             "output": None, "created": "2026-09-16T15:58:40.000Z"},
        ]
        conv = self._build(rows)
        self.assertEqual([t["input"]["content"] for t in conv["turns"]], ["q"])


class FixtureShapeMatchesProducer(unittest.TestCase):
    """The fixture may only use keys the module's own BTQL query selects.

    A consumer test is worth nothing if it is fed a row the producer could never
    emit — the branch it claims to cover stays unreachable on real data while the
    suite reports green. So the fixture's key set is checked against the `select`
    clause in the module's real `_bt_fetch_by_conversation_id` query, and a
    narrowing of that clause fails here rather than showing up as a transcript
    that silently stops resolving.
    """

    def test_fixture_keys_are_a_subset_of_the_selected_columns(self):
        for path in _builder_copies():
            with self.subTest(skill=_skill(path)):
                mod = load_reader(path)
                fake = FakeBraintrust({})
                mod.requests = fake
                mod._bt_fetch_by_conversation_id("tok", "CID-1")
                self.assertTrue(fake.queries, "producer issued no BTQL query")
                selected = {
                    c.strip() for c in
                    re.search(r"select (.+?) from", fake.queries[0], re.S)
                    .group(1).split(",")
                }
                self.assertLessEqual(
                    set(span("s", "2026-09-16T00:00:00Z")), selected,
                    "the fixture describes a row shape the producer never emits",
                )

    def test_input_and_created_are_selected(self):
        """The two columns the builder cannot work without.

        Dropping either from the `select` returns rows that parse fine and build
        a conversation with no turns — the same silent shape as the defect.
        """
        for path in _builder_copies():
            with self.subTest(skill=_skill(path)):
                mod = load_reader(path)
                fake = FakeBraintrust({})
                mod.requests = fake
                mod._bt_fetch_by_conversation_id("tok", "CID-1")
                for column in ("input", "created", "output"):
                    self.assertIn(column, fake.queries[0])


class BuilderCopyMembership(unittest.TestCase):
    """Membership is asserted by shape, in both directions.

    A `diff -q` over `fetch_logs.py` cannot make this claim: three of the five
    copies are behavioural forks that legitimately differ, so a byte check is
    permanently red and tells you nothing about who carries the builder. Only
    two of the five are named in CLAUDE.md's "Canonical source for
    `fetch_logs.py`" section as tracking the canonical, and
    `report-conversation-patterns` carried an unnamed copy — the same
    green-guard-but-incomplete-list shape recorded there for `search-gcp-by-user`
    and `report-livekit-quota`.
    """

    def test_every_copy_with_the_builder_is_declared(self):
        found = {_skill(p) for p in _builder_copies()}
        self.assertEqual(
            found, EXPECTED_COPIES,
            "the set of fetch_logs.py copies carrying _bt_build_conversation "
            "changed; add the new copy to EXPECTED_COPIES (it is now covered) "
            "or explain the removal",
        )

    def test_the_journal_fork_is_not_silently_in_the_family(self):
        """It defines no BT conversation builder, and must not grow one by
        accident — a journal pivot reconstructing conversations would be a
        different bug wearing this fix's clothes."""
        journal = [p for p in script_paths("fetch_logs.py")
                   if _skill(p) == "analyze-user-journal"]
        for path in journal:
            self.assertNotIn("def _bt_build_conversation",
                             path.read_text(encoding="utf-8"))


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromTestCase(BuilderCopyMembership))
    suite.addTests(loader.loadTestsFromTestCase(FixtureShapeMatchesProducer))
    for path in _builder_copies():
        skill = _skill(path)
        cls = type(
            f"BTTranscriptSpan_{skill.replace('-', '_')}",
            (BTTranscriptSpan,),
            {"path": path, "skill": skill, "__module__": __name__},
        )
        suite.addTests(loader.loadTestsFromTestCase(cls))
    return suite


if __name__ == "__main__":
    unittest.main()
