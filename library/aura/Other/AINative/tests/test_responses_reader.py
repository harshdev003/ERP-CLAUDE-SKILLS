#!/usr/bin/env python3
"""Failure-injection tests for `responses_reader.py`.

Runs the whole suite against **every** copy in the repo, so a behavioural
divergence between copies fails here even if a byte-diff has been reconciled.

Set `AINATIVE_READER_PATH` to point the suite at one specific file — used to
check a test actually fails against a historical pre-fix commit (see
`tests/README.md`).

    python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import io
import os
import contextlib
import pathlib
import unittest

sys_path_added = pathlib.Path(__file__).resolve().parent
import sys
if str(sys_path_added) not in sys.path:
    sys.path.insert(0, str(sys_path_added))

from reader_harness import (  # noqa: E402
    LEDGER_URL, FakeTransport, conversation_items, input_item, ledger_row, load_reader,
    reader_paths, response_obj,
)


def _targets() -> list[pathlib.Path]:
    override = os.environ.get("AINATIVE_READER_PATH")
    if override:
        return [pathlib.Path(override)]
    paths = reader_paths()
    if not paths:
        raise AssertionError("no responses_reader.py found — wrong repo root?")
    return paths


class ResponsesReaderContract(unittest.TestCase):
    """One instance per copy; `path` is injected by `load_tests`."""

    path: pathlib.Path = None  # type: ignore[assignment]

    def setUp(self):
        self.mod = load_reader(self.path)

    def _run(self, transport, **kwargs):
        transport.install()
        return self.mod.fetch_responses_conversations(
            "sk-test", "proj_test", "u1", **kwargs
        )

    def _text(self, result) -> str:
        """Flatten every rendered message body in the result."""
        out = []
        for item in result["items"]:
            for m in item.get("messages") or []:
                c = m.get("content")
                out.append(c if isinstance(c, str) else str(c))
        return "\n".join(out)

    # -- 1. happy path ----------------------------------------------------

    def test_happy_path_one_response_and_input_items_per_conversation(self):
        t = FakeTransport(
            self.mod,
            ledger_rows=[ledger_row("resp_a", conversation="c1")],
            responses={"resp_a": response_obj("resp_a")},
            input_items={"resp_a": [input_item("i0", "user", "hello")]},
        )
        r = self._run(t)
        self.assertEqual(r["responses_row_count"], 1)
        self.assertEqual(r["unfetchable"], [])
        self.assertIn("hello", self._text(r))

    # -- 2. transcript source unreadable -> falls back --------------------

    def test_unreadable_transcript_source_falls_back_to_next_latest(self):
        """The #736 -> #737 defect.

        Fetching only the newest call per conversation means one unreadable
        response would otherwise leave the conversation as ledger stubs only —
        an empty transcript, which reads as "user said nothing" rather than as
        an error.
        """
        t = FakeTransport(
            self.mod,
            ledger_rows=[
                ledger_row("resp_new", conversation="c1", created="2026-08-01T12:00:00Z"),
                ledger_row("resp_old", conversation="c1", created="2026-08-01T11:00:00Z"),
            ],
            responses={"resp_old": response_obj("resp_old")},   # resp_new absent -> 404
            input_items={"resp_old": [input_item("i0", "user", "earlier history")]},
        ).fail("/responses/resp_new")
        r = self._run(t)
        self.assertIn("earlier history", self._text(r),
                      "fell through to ledger stubs instead of the next-latest call")
        self.assertTrue(any(u["openaiId"] == "resp_new" for u in r["unfetchable"]))

    # -- 3. every row unreadable -> recorded, not silently empty ----------

    def test_all_rows_unreadable_are_recorded_in_unfetchable(self):
        t = FakeTransport(
            self.mod,
            ledger_rows=[ledger_row("resp_a", conversation="c1"),
                         ledger_row("resp_b", conversation="c2")],
            responses={},
        ).fail("/responses/")
        r = self._run(t)
        self.assertEqual(len(r["unfetchable"]), 2,
                         "unreadable rows must be counted, not dropped silently")
        self.assertEqual(r["responses_row_count"], 2,
                         "the ledger knew about these rows — that must stay visible")

    # -- 4. input_items failure -> _messages_error, not silent -----------

    def test_input_items_failure_sets_messages_error_and_warns(self):
        """The #735 class: a failed `/input_items` yields an assistant-only
        transcript that looks complete. It must be marked and warned about."""
        t = FakeTransport(
            self.mod,
            ledger_rows=[ledger_row("resp_a", conversation="c1")],
            responses={"resp_a": response_obj("resp_a")},
            input_items={"resp_a": [input_item("i0", "user", "hello")]},
        ).fail("/input_items")
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            r = self._run(t)
        docs = [i for i in r["items"] if i.get("_messages_error")]
        self.assertTrue(docs, "a failed input_items fetch was not marked on the doc")
        self.assertIn("WARNING", buf.getvalue())

    # -- 5. pagination past the 100-item cap ------------------------------

    def test_input_items_paginates_past_the_page_cap(self):
        """The #734/#735 class. A single un-paginated request returns the first
        page and still looks complete — the transcript is silently truncated."""
        items = conversation_items(250)
        t = FakeTransport(
            self.mod,
            ledger_rows=[ledger_row("resp_a", conversation="c1")],
            responses={"resp_a": response_obj("resp_a")},
            input_items={"resp_a": items},
        )
        self._run(t)
        pages = t.urls("/input_items")
        self.assertGreaterEqual(len(pages), 3,
                                "250 items over a 100 cap needs >=3 requests")
        got = self.mod.fetch_input_items("sk-test", "proj_test", "resp_a")
        self.assertEqual(len(got), 250, "transcript truncated at the page cap")

    def test_pagination_stops_when_cursor_id_is_missing(self):
        """A page whose last item has no `id` gives no cursor to continue from.
        The loop must stop rather than re-request the same page forever."""
        items = [{"type": "message", "role": "user",
                  "content": [{"type": "input_text", "text": f"t{i}"}]}
                 for i in range(150)]                      # no `id` on any item
        t = FakeTransport(self.mod, input_items={"resp_a": items}).install()
        got = self.mod.fetch_input_items("sk-test", "proj_test", "resp_a")
        self.assertEqual(len(got), 100, "should stop at the uncursorable page")

    # -- 6. input_items_for="all" (journal path) --------------------------

    def test_input_items_for_all_fetches_every_row(self):
        """The journal path builds one record per call, so grouping by
        conversation would collapse distinct records."""
        rows = [ledger_row(f"resp_{i}", conversation="c1", prompter="journal-prompter")
                for i in range(3)]
        t = FakeTransport(
            self.mod,
            ledger_rows=rows,
            responses={f"resp_{i}": response_obj(f"resp_{i}") for i in range(3)},
            input_items={f"resp_{i}": [input_item(f"i{i}", "user", f"entry {i}")]
                         for i in range(3)},
        )
        r = self._run(t, prompters=self.mod.JOURNAL_PROMPTERS, input_items_for="all")
        self.assertEqual(len(t.urls("/input_items")), 3,
                         "'all' must fetch input items for every row")
        for i in range(3):
            self.assertIn(f"entry {i}", self._text(r))

    def test_default_mode_fetches_input_items_once_per_conversation(self):
        """The ENG-1022 optimisation: three calls in one conversation cost one
        `/input_items` fetch, because the newest carries the whole history."""
        rows = [ledger_row(f"resp_{i}", conversation="c1",
                           created=f"2026-08-01T1{i}:00:00Z") for i in range(3)]
        t = FakeTransport(
            self.mod,
            ledger_rows=rows,
            responses={f"resp_{i}": response_obj(f"resp_{i}") for i in range(3)},
            input_items={"resp_2": [input_item("i0", "user", "whole history")]},
        )
        r = self._run(t)
        self.assertEqual(len(t.urls("/input_items")), 1)
        self.assertIn("whole history", self._text(r))

    # -- 7. prompter filtering + case-insensitive conversation match -------

    def test_side_effect_prompters_excluded_from_the_user_pivot(self):
        """The #738 class. `memory-prompter` and friends share a conversationId
        but are not turns; rendering them inflates the conversation count and
        each reads as an empty transcript."""
        t = FakeTransport(
            self.mod,
            ledger_rows=[
                ledger_row("resp_real", conversation="c1", prompter="coach-prompter"),
                ledger_row("resp_mem", conversation="c1", prompter="memory-prompter"),
                ledger_row("resp_sum", conversation="c1", prompter="summary-prompter"),
                ledger_row("resp_prof", conversation="c1", prompter="profile-prompter"),
            ],
            responses={"resp_real": response_obj("resp_real")},
            input_items={"resp_real": [input_item("i0", "user", "hi")]},
        )
        r = self._run(t)
        self.assertEqual(r["ledger_row_count"], 4)
        self.assertEqual(r["responses_row_count"], 1,
                         "side-effect prompters leaked into the transcript")

    def test_user_visible_prompters_are_kept(self):
        """Every prompter whose reply reaches the user is a transcript turn.

        `quick-processing-sentinel-prompter` is the Quick Processing flavour
        IntelligenceService runs on Responses (`quickProcessingSentinel`). Absent
        from the set, the conversation pivot returns 0 turns with a "wrong ID or
        older than 30 days" hint and the user pivot drops the conversation.
        """
        visible = ("chat-prompter", "coach-prompter", "companion-prompter",
                   "guardrails-handler-prompter", "quick-processing-prompter",
                   "quick-processing-sentinel-prompter")
        rows = [ledger_row(f"resp_{i}", conversation="c1", prompter=p)
                for i, p in enumerate(visible)]
        picked = self.mod.responses_rows(rows)
        self.assertEqual([r["prompterName"] for r in picked], list(visible))

    def test_sentinel_only_conversation_yields_a_transcript(self):
        """End-to-end on the conversation pivot, the path fetch_logs takes."""
        cid = "QP-SENTINEL-1"
        rows = [ledger_row(f"resp_s{i}", conversation=cid,
                           prompter="quick-processing-sentinel-prompter")
                for i in range(4)]
        t = FakeTransport(
            self.mod,
            ledger_by_conversation={cid: rows},
            responses={r["openaiId"]: response_obj(r["openaiId"]) for r in rows},
            input_items={r["openaiId"]: [input_item("i0", "user", "hello qp")]
                         for r in rows},
        ).install()
        r = self.mod.fetch_responses_for_conversation("sk", "proj", cid)
        self.assertEqual(r["responses_row_count"], 4,
                         "quick-processing-sentinel-prompter turns were filtered out")
        self.assertIn("hello qp", self._text(r))

    def test_completions_rows_are_excluded(self):
        """`responses_rows` must drop anything that did not run on Responses —
        the completions arm reads those, and counting them twice double-books
        every turn."""
        rows = [
            ledger_row("resp_a", conversation="c1", api="responses"),
            ledger_row("chatcmpl_b", conversation="c1", api="chat.completions"),
        ]
        picked = self.mod.responses_rows(rows)
        self.assertEqual([r["openaiId"] for r in picked], ["resp_a"])

    def test_non_resp_ids_are_excluded(self):
        rows = [ledger_row("chatcmpl_x", conversation="c1", api="responses")]
        self.assertEqual(self.mod.responses_rows(rows), [])

    def test_conversation_ids_group_case_insensitively(self):
        """IntelligenceService writes mixed-case UUIDs; two casings of the same
        id are one conversation, not two."""
        t = FakeTransport(
            self.mod,
            ledger_rows=[
                ledger_row("resp_1", conversation="ABC-123", created="2026-08-01T12:00:00Z"),
                ledger_row("resp_2", conversation="abc-123", created="2026-08-01T11:00:00Z"),
            ],
            responses={f"resp_{i}": response_obj(f"resp_{i}") for i in (1, 2)},
            input_items={"resp_1": [input_item("i0", "user", "hi")]},
        )
        self._run(t)
        self.assertEqual(len(t.urls("/input_items")), 1,
                         "case variants were treated as two conversations")

    # -- ledger conversation lookup across casings ------------------------

    def _casing_transport(self):
        """A conversation whose rows are split across both id casings.

        Not contrived: the ledger's `conversationId` filter is exact-match, and
        different call sites within one session write different casings — so a
        single conversation really does hold rows under both. The lower-case
        half here is all side-effect prompters, which is what makes the bug
        silent: `responses_rows` filters them out and the caller sees an empty
        transcript rather than an error.
        """
        lo, up = "abc-1", "ABC-1"
        return lo, FakeTransport(
            self.mod,
            ledger_by_conversation={
                lo: [ledger_row("resp_side", conversation=lo, prompter="memory-prompter")],
                up: [ledger_row("resp_real", conversation=up, prompter="coach-prompter"),
                     ledger_row("resp_guard", conversation=up,
                                prompter="guardrails-handler-prompter")],
            },
            responses={"resp_real": response_obj("resp_real"),
                       "resp_guard": response_obj("resp_guard"),
                       "resp_side": response_obj("resp_side")},
            input_items={"resp_real": [input_item("i0", "user", "hi")],
                         "resp_guard": [input_item("i1", "user", "help")]},
        )

    def test_ledger_lookup_merges_across_casings(self):
        lo, t = self._casing_transport()
        t.install()
        rows = self.mod.fetch_ledger_rows_by_conversation(lo)
        self.assertEqual(len(rows), 3,
                         "returned one casing's rows instead of the union")
        self.assertEqual({r["openaiId"] for r in rows},
                         {"resp_side", "resp_real", "resp_guard"})

    def test_a_non_empty_first_casing_does_not_short_circuit(self):
        """The exact failure mode: the first variant queried returns rows, so a
        `if rows: return rows` loop stops there and never sees the rest."""
        lo, t = self._casing_transport()
        t.install()
        rows = self.mod.fetch_ledger_rows_by_conversation(lo)
        self.assertIn("resp_real", {r["openaiId"] for r in rows},
                      "short-circuited on the first non-empty casing")
        self.assertGreaterEqual(len(t.urls(LEDGER_URL)), 2,
                                "stopped querying after the first casing")

    def test_split_casing_conversation_yields_a_transcript(self):
        """End-to-end: the empty-transcript symptom is gone."""
        lo, t = self._casing_transport()
        t.install()
        r = self.mod.fetch_responses_for_conversation("sk", "proj", lo)
        self.assertEqual(r["ledger_row_count"], 3)
        self.assertEqual(r["responses_row_count"], 2, "coach + guardrails turns")
        self.assertIn("hi", self._text(r))

    def test_rows_are_deduped_on_openai_id(self):
        """A row returned under more than one casing must appear once."""
        lo, up = "dup-1", "DUP-1"
        shared = ledger_row("resp_same", conversation=lo)
        t = FakeTransport(
            self.mod,
            ledger_by_conversation={lo: [shared], up: [dict(shared)]},
            responses={"resp_same": response_obj("resp_same")},
            input_items={"resp_same": [input_item("i0", "user", "hi")]},
        ).install()
        rows = self.mod.fetch_ledger_rows_by_conversation(lo)
        self.assertEqual(len(rows), 1)

    def test_single_casing_conversation_is_unchanged(self):
        """The common case must not regress."""
        cid = "only-1"
        t = FakeTransport(
            self.mod,
            ledger_by_conversation={cid: [ledger_row("resp_a", conversation=cid)]},
            responses={"resp_a": response_obj("resp_a")},
            input_items={"resp_a": [input_item("i0", "user", "hi")]},
        ).install()
        rows = self.mod.fetch_ledger_rows_by_conversation(cid)
        self.assertEqual([r["openaiId"] for r in rows], ["resp_a"])

    def test_rows_without_an_openai_id_are_kept(self):
        """Dedup keys on `openaiId`; a row lacking one must not be dropped —
        a row the ledger returned is evidence even when it can't be keyed."""
        cid = "noid-1"
        row = ledger_row("resp_a", conversation=cid)
        del row["openaiId"]
        t = FakeTransport(
            self.mod, ledger_by_conversation={cid: [row]},
        ).install()
        rows = self.mod.fetch_ledger_rows_by_conversation(cid)
        self.assertEqual(len(rows), 1)

    # -- ledger failure ------------------------------------------------------

    def test_ledger_failure_raises_rather_than_reading_as_no_rows(self):
        """A BigQuery failure must surface as a reader error: an empty ledger
        reads as a user with no conversations."""
        def boom(sql, **kw):
            raise self.mod.llm_ledger.bq.BQError("403 access denied")
        old = self.mod.llm_ledger.bq.query
        self.mod.llm_ledger.bq.query = boom
        try:
            with self.assertRaises(self.mod.ResponsesReaderError) as ctx:
                self.mod.fetch_ledger_rows("u1")
            self.assertIn("llm.llm_call", str(ctx.exception))
        finally:
            self.mod.llm_ledger.bq.query = old

    # -- no network, no keys ---------------------------------------------

    def test_suite_needs_no_api_key(self):
        """Guard the harness's own contract: the module must not read a key at
        import time, or CI would need secrets to run these tests."""
        self.assertTrue(hasattr(self.mod, "fetch_responses_conversations"))


class HarnessFailsFast(unittest.TestCase):
    """Guards the harness's own contract, not the code under test.

    If the reader's `last_id` stop guard regresses, the client stops advancing
    `after`. A naive stub answers `has_more=True` for page 0 forever, so the test
    spins until the CI job's timeout — turning the exact regression this suite
    exists to catch into a ~10-minute hang that reads like an infra flake rather
    than a failure with a stack trace. Verified before the guard was added: the
    suite ran indefinitely against a reader with the stop guard removed.
    """

    def test_non_advancing_cursor_raises_instead_of_looping(self):
        mod = load_reader(_targets()[0])
        t = FakeTransport(mod, input_items={"resp_a": conversation_items(250)})
        items = t.input_items["resp_a"]
        # Same offset requested repeatedly — what a non-advancing client does.
        with self.assertRaises(AssertionError) as ctx:
            for _ in range(t._MAX_REPEATS + 2):
                t._page(items, {"limit": 100})
        self.assertIn("pagination did not advance", str(ctx.exception))

    def test_a_correctly_paginating_client_is_not_tripped(self):
        """The guard must not fire on legitimate pagination — 250 items over a
        100 cap is three distinct offsets, each requested once."""
        mod = load_reader(_targets()[0])
        t = FakeTransport(
            mod,
            ledger_rows=[ledger_row("resp_a", conversation="c1")],
            responses={"resp_a": response_obj("resp_a")},
            input_items={"resp_a": conversation_items(250)},
        ).install()
        self.assertEqual(len(mod.fetch_input_items("k", "p", "resp_a")), 250)


def load_tests(loader, tests, pattern):
    """Instantiate the contract once per `responses_reader.py` copy."""
    suite = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromTestCase(HarnessFailsFast))
    for path in _targets():
        skill = path.parent.parent.name
        cls = type(
            f"ResponsesReader_{skill.replace('-', '_')}",
            (ResponsesReaderContract,),
            {"path": path, "__module__": __name__},
        )
        suite.addTests(loader.loadTestsFromTestCase(cls))
    return suite


if __name__ == "__main__":
    unittest.main()
