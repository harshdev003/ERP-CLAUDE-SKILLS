"""data:analyze-conversation-guardrails `reproduce.py`: the pure half of the guardrail-dataset flow.

The network half (ledger, stored responses, replays) was checked live on
2026-10-15: `rebuild` reproduced the hand-built requests byte for byte for all
9 rebuildable alerts. `cmd_rebuild`'s choice between the exact and rebuilt
paths is tested with the ledger and OpenAI stubbed. These tests pin the decisions that make a row safe and
honest: what is rebuilt, what counts as fired, what de-identification replaces,
and which alerts are allowed to become rows.
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from reader_harness import REPO_ROOT  # noqa: E402

SCRIPTS = REPO_ROOT / "plugins/data/skills/analyze-conversation-guardrails/scripts"
sys.path.insert(0, str(SCRIPTS))
_spec = importlib.util.spec_from_file_location("reproduce", SCRIPTS / "reproduce.py")
rp = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(rp)

BODY = "## Role\n\nYou are Aurie."
CONTEXT = "## Context\n\nUser name: Dana."
SYSTEM = BODY + "\n\n\n" + CONTEXT


def _msg(role, text):
    return {"type": "message", "role": role, "content": [{"type": "input_text", "text": text}]}


class RebuildTests(unittest.TestCase):
    def test_split_system_returns_body_and_context(self):
        self.assertEqual((BODY, CONTEXT), rp.split_system(SYSTEM))
        with self.assertRaises(ValueError):
            rp.split_system("no context here")

    def test_request_takes_settings_from_coach_and_turns_from_handler(self):
        coach_resp = {"model": "chat-latest", "top_p": 0.98, "tools": [{"name": "handoffToGuardrails"}],
                      "reasoning": {"effort": "medium"}, "output": ["not copied"], "user": "uid-not-copied",
                      "max_output_tokens": None}
        coach_items = [_msg("system", SYSTEM), _msg("user", "hi"), _msg("assistant", "hello")]
        handler_items = [_msg("system", "HANDLER PROMPT"), _msg("user", "hi"), _msg("assistant", "hello"),
                         _msg("user", "the message that fired")]
        req = rp.rebuild_request(coach_resp, coach_items, handler_items)
        self.assertEqual({"model", "top_p", "tools", "reasoning", "input"}, set(req))
        self.assertEqual({"role": "system", "content": SYSTEM}, req["input"][0])
        self.assertNotIn("HANDLER PROMPT", [m["content"] for m in req["input"]])
        self.assertEqual("the message that fired", req["input"][-1]["content"])

    def test_handler_input_must_end_with_the_user_message(self):
        with self.assertRaises(ValueError):
            rp.rebuild_request({"model": "m"}, [_msg("system", SYSTEM)], [_msg("user", "a"), _msg("assistant", "b")])

    def test_production_config_is_what_compare_prompt_versions_takes(self):
        tools, params = rp.production_config({"tools": [{"name": "t"}], "top_p": 0.98, "reasoning": {"effort": "m"},
                                              "model": "chat-latest", "temperature": None})
        self.assertEqual([{"name": "t"}], tools)
        self.assertEqual({"top_p": 0.98, "reasoning": {"effort": "m"}}, params)


TIME = "### Current Time\n\nIt is Tuesday, October 20, 2026 at 9:15AM."
HISTORY = [("user", "hi"), ("assistant", "hello"), ("user", "the message that fired")]


def _layout(version, history=HISTORY):
    """A coach call's input items (oldest first) as Intelligence Service `version` sent them."""
    conv = [_msg(r, t) for r, t in history]
    if version == "1.34":
        return [_msg("system", BODY + "\n\n\n" + CONTEXT + "\nIt is Tuesday.")] + conv
    if version == "1.35":
        return [_msg("system", SYSTEM)] + conv + [_msg("developer", TIME)]
    if version == "1.36":
        return [_msg("system", BODY), _msg("developer", CONTEXT)] + conv + [_msg("developer", TIME)]
    return ([_msg("system", BODY), _msg("developer", "---"), _msg("user", "---"), _msg("developer", CONTEXT)]
            + conv + [_msg("developer", TIME)])


LAYOUTS = {"1.34": ("system-context", CONTEXT + "\nIt is Tuesday.", ""),
           "1.35": ("system-context", CONTEXT, TIME),
           "1.36": ("developer-context", CONTEXT, TIME),
           "1.40": ("delimited-developer-context", CONTEXT, TIME)}


class LayoutTests(unittest.TestCase):
    """One fixture per coach request layout: <= 1.34, 1.35, 1.36-1.39, 1.40+."""

    def test_parts_are_read_from_every_layout(self):
        for version, (layout, context, time) in LAYOUTS.items():
            with self.subTest(version=version):
                req, others = rp.exact_request({"model": "chat-latest"}, _layout(version))
                parts = rp.coach_parts(req["input"])
                self.assertEqual(layout, parts["layout"])
                self.assertEqual(BODY, parts["body"])
                self.assertEqual(context, parts["context"])
                self.assertEqual(time, parts["developer"])
                self.assertEqual([{"role": r, "content": t} for r, t in HISTORY], parts["conversation"])
                self.assertEqual(0, others)

    def test_exact_request_keeps_production_order(self):
        for version in LAYOUTS:
            with self.subTest(version=version):
                items = _layout(version)
                req, _ = rp.exact_request({"model": "chat-latest"}, items)
                self.assertEqual([(i["role"], rp.item_text(i)) for i in items],
                                 [(m["role"], m["content"]) for m in req["input"]])
        req, _ = rp.exact_request({"model": "m"}, _layout("1.40"))
        self.assertEqual(["system", "developer", "user", "developer", "user", "assistant", "user", "developer"],
                         [m["role"] for m in req["input"]])

    def test_with_parts_swaps_context_and_conversation_in_place(self):
        new_conv = [{"role": r, "content": t.upper()} for r, t in HISTORY]
        for version in LAYOUTS:
            with self.subTest(version=version):
                req, _ = rp.exact_request({"model": "m"}, _layout(version))
                out = rp.with_parts(req["input"], context="## Context\n\nUser name: Alex.", conversation=new_conv)
                parts = rp.coach_parts(out)
                self.assertEqual("## Context\n\nUser name: Alex.", parts["context"])
                self.assertEqual(new_conv, parts["conversation"])
                self.assertEqual(BODY, parts["body"])
                self.assertEqual([m["role"] for m in req["input"]], [m["role"] for m in out])
                if version != "1.34":
                    self.assertEqual({"role": "developer", "content": TIME}, out[-1])

    def test_a_deidentified_replay_keeps_the_delimiters_and_the_time(self):
        req, _ = rp.exact_request({"model": "m"}, _layout("1.40"))
        rebuilt = {"1.0": {"request": req, "context_marker": rp.CONTEXT_MARKER, "meta": {"service_tier": "flex"}}}
        deid = {"1.0": {"context": "## Context\n\nUser name: Alex.",
                        "input": [{"role": r, "content": "x"} for r, _ in HISTORY]}}
        sent = rp._request_for("1.0", rebuilt, deid)
        self.assertEqual([("system", BODY), ("developer", "---"), ("user", "---"),
                          ("developer", "## Context\n\nUser name: Alex."), ("user", "x"), ("assistant", "x"),
                          ("user", "x"), ("developer", TIME)], [(m["role"], m["content"]) for m in sent["input"]])
        self.assertEqual((False, "flex"), (sent["store"], sent["service_tier"]))
        self.assertEqual(3, len(rp.conversation_for(rebuilt["1.0"])))

    def test_a_layout_without_a_context_block_is_refused(self):
        with self.assertRaisesRegex(ValueError, "no context block"):
            rp.exact_request({"model": "m"}, [_msg("system", BODY), _msg("user", "x")])

    def test_a_user_typing_the_delimiter_later_is_conversation(self):
        items = _layout("1.40")[:-1] + [_msg("user", "---"), _msg("developer", TIME)]
        req, _ = rp.exact_request({"model": "m"}, items)
        self.assertEqual("---", rp.coach_parts(req["input"])["conversation"][-1]["content"])

    def test_rebuild_keeps_the_previous_calls_context_and_time_around_the_handlers_turns(self):
        handler = [_msg("system", "HANDLER"), _msg("developer", "---"), _msg("user", "---"),
                   _msg("developer", "## Context\n\nHandler context.")] + [_msg(r, t) for r, t in HISTORY]
        for version in LAYOUTS:
            with self.subTest(version=version):
                prev = _layout(version, history=[("user", "an earlier message")])
                req = rp.rebuild_request({"model": "m"}, prev, handler)
                parts = rp.coach_parts(req["input"])
                self.assertEqual(LAYOUTS[version][1], parts["context"])
                self.assertEqual(LAYOUTS[version][2], parts["developer"])
                self.assertEqual([{"role": r, "content": t} for r, t in HISTORY], parts["conversation"])
                self.assertNotIn("HANDLER", [m["content"] for m in req["input"]])


class CallChainTests(unittest.TestCase):
    def test_the_coach_call_is_the_last_one_before_the_handler(self):
        row = {"callChain": '[{"openaiId": "c1", "prompterName": "coach-prompter"},'
                            ' {"openaiId": "c2", "prompterName": "coach-prompter"},'
                            ' {"openaiId": "h1", "prompterName": "guardrails-handler-prompter"}]'}
        self.assertEqual("c2", rp.chain_coach_id(row))

    def test_the_chain_may_arrive_already_parsed(self):
        row = {"callChain": [{"openaiId": "c1", "prompterName": "coach-prompter"},
                             {"openaiId": "h1", "prompterName": "guardrails-handler-prompter"}]}
        self.assertEqual("c1", rp.chain_coach_id(row))

    def test_no_chain_or_no_handoff_means_fall_back(self):
        self.assertIsNone(rp.chain_coach_id({}))
        self.assertIsNone(rp.chain_coach_id({"callChain": "not json"}))
        self.assertIsNone(rp.chain_coach_id({"callChain": [{"openaiId": "h1", "prompterName": "guardrails-handler-prompter"}]}))
        self.assertIsNone(rp.chain_coach_id({"callChain": [{"openaiId": "c1", "prompterName": "coach-prompter"}]}))

    def test_exact_request_keeps_messages_and_counts_tool_items(self):
        items = [_msg("system", SYSTEM), _msg("user", "hi"), _msg("assistant", "hello"), _msg("user", "help"),
                 {"type": "function_call", "name": "findSimilarInMemory", "call_id": "x", "arguments": "{}"},
                 {"type": "function_call_output", "call_id": "x", "output": "[]"}]
        req, others = rp.exact_request({"model": "chat-latest", "top_p": 0.98, "output": [1]}, items)
        self.assertEqual("chat-latest", req["model"])
        self.assertNotIn("output", req)
        self.assertEqual(["system", "user", "assistant", "user"], [m["role"] for m in req["input"]])
        self.assertEqual("help", req["input"][-1]["content"])
        self.assertEqual(2, others)

    def test_the_exact_call_category_wins_over_the_logs(self):
        self.assertEqual("crisis", rp.production_category({"meta": {"production_category": "crisis"}}, {"category": "unknown"}))
        self.assertEqual("treatment", rp.production_category({"meta": {}}, {"category": "treatment"}))
        self.assertIsNone(rp.production_category({}, None))

    def test_exact_request_must_end_with_the_user_message(self):
        with self.assertRaises(ValueError):
            rp.exact_request({"model": "m"}, [_msg("system", SYSTEM), _msg("user", "a"), _msg("assistant", "b")])


class RebuildCommandTests(unittest.TestCase):
    """cmd_rebuild with the ledger and OpenAI stubbed: which path each alert takes."""

    def setUp(self):
        import tempfile
        self.dir = pathlib.Path(tempfile.mkdtemp())
        (self.dir / "cache").mkdir()
        for ts in ("1.0", "2.0"):
            (self.dir / "cache" / f"{ts}.json").write_text(
                f'{{"conversation_id": "conv-{ts}", "alert_transaction": "TX-{ts}"}}', encoding="utf-8")
        handoff = [{"type": "function_call", "name": "handoffToGuardrails", "arguments": '{"category": "crisis"}'}]
        self.responses = {"exact-coach": {"model": "chat-latest", "service_tier": "default", "output": handoff},
                          "no-handoff": {"model": "chat-latest", "output": []},
                          "prev-coach": {"model": "chat-latest", "service_tier": "default", "output": []}}
        self.items = {"exact-coach": [_msg("user", "help"), _msg("system", SYSTEM)],
                      "no-handoff": [_msg("user", "help"), _msg("system", SYSTEM)],
                      "prev-coach": [_msg("system", SYSTEM)],
                      "handler-old": [_msg("user", "help"), _msg("assistant", "hi"), _msg("user", "hey")]}
        self.ledger = {
            "conv-1.0": [{"transaction": "tx-1.0", "prompterName": rp.HANDLER, "openaiId": "handler-new",
                          "createdAt": "2026-10-16T10:00:00Z", "version": "1.32.2",
                          "callChain": '[{"openaiId": "exact-coach", "prompterName": "coach-prompter"},'
                                       ' {"openaiId": "handler-new", "prompterName": "guardrails-handler-prompter"}]'}],
            "conv-2.0": [{"transaction": "x", "prompterName": rp.COACH, "openaiId": "prev-coach",
                          "createdAt": "2026-10-14T09:59:00Z", "version": "1.32.1"},
                         {"transaction": "tx-2.0", "prompterName": rp.HANDLER, "openaiId": "handler-old",
                          "createdAt": "2026-10-14T10:00:00Z", "version": "1.32.1"}]}
        self._orig = (rp.rr.fetch_ledger_rows_by_conversation, rp.rr.fetch_response, rp.rr.fetch_input_items)
        rp.rr.fetch_ledger_rows_by_conversation = lambda cid: self.ledger[cid]
        rp.rr.fetch_response = lambda key, proj, rid: self.responses[rid]
        rp.rr.fetch_input_items = lambda key, proj, rid: self.items[rid]
        import os
        self._key = os.environ.get("OPENAI_CONVERSATION_API_KEY")
        os.environ["OPENAI_CONVERSATION_API_KEY"] = "test"

    def tearDown(self):
        import os, shutil
        rp.rr.fetch_ledger_rows_by_conversation, rp.rr.fetch_response, rp.rr.fetch_input_items = self._orig
        if self._key is None:
            os.environ.pop("OPENAI_CONVERSATION_API_KEY", None)
        else:
            os.environ["OPENAI_CONVERSATION_API_KEY"] = self._key
        shutil.rmtree(self.dir)

    def _run(self):
        import argparse, contextlib, io, json
        a = argparse.Namespace(cache_dir=str(self.dir / "cache"), out=str(self.dir / "out.json"),
                               tools_out=str(self.dir / "t.json"), params_out=str(self.dir / "p.json"),
                               context_marker=rp.CONTEXT_MARKER)
        with contextlib.redirect_stdout(io.StringIO()):
            rp.cmd_rebuild(a)
        return json.loads((self.dir / "out.json").read_text(encoding="utf-8"))

    def test_a_chained_row_is_fetched_exact_and_an_old_row_is_rebuilt(self):
        out = self._run()
        self.assertEqual("exact", out["1.0"]["meta"]["source"])
        self.assertEqual(0, out["1.0"]["meta"]["system_age_s"])
        self.assertEqual("1.32.2", out["1.0"]["meta"]["coach_version"])
        self.assertEqual("crisis", out["1.0"]["meta"]["production_category"])
        self.assertEqual("rebuilt", out["2.0"]["meta"]["source"])
        self.assertEqual(60, out["2.0"]["meta"]["system_age_s"])

    def test_a_chain_whose_coach_call_did_not_hand_off_is_an_error_not_a_row(self):
        self.ledger["conv-1.0"][0]["callChain"] = self.ledger["conv-1.0"][0]["callChain"].replace("exact-coach", "no-handoff")
        out = self._run()
        self.assertIn("did not call handoffToGuardrails", out["1.0"]["error"])
        self.assertNotIn("request", out["1.0"])


class FiredTests(unittest.TestCase):
    def test_reads_the_category(self):
        out = [{"type": "reasoning"},
               {"type": "function_call", "name": "handoffToGuardrails", "arguments": '{"category": "crisis"}'}]
        self.assertEqual((True, "crisis"), rp.fired(out))

    def test_other_tools_and_text_are_not_a_trigger(self):
        out = [{"type": "function_call", "name": "searchTracks", "arguments": "{}"},
               {"type": "message", "content": [{"type": "output_text", "text": "hi"}]}]
        self.assertEqual((False, None), rp.fired(out))


class DeidentifyTests(unittest.TestCase):
    def test_full_name_is_replaced_before_its_first_name(self):
        mapping = rp.build_mapping([{"text": "Dana", "type": "person"}, {"text": "Dana Reyes", "type": "person"},
                                    {"text": "Portland", "type": "place"}, {"text": "x", "type": "person"}])
        self.assertNotIn("x", mapping)
        out = rp.apply_mapping("Dana Reyes met Dana in Portland.", mapping)
        self.assertNotIn("Dana", out)
        self.assertNotIn("Reyes", out)
        self.assertIn("the city", out)
        # one person per distinct span, so the two references stay distinguishable
        self.assertEqual(2, len({mapping["Dana"], mapping["Dana Reyes"]}))

    def test_replacement_respects_word_boundaries(self):
        self.assertEqual("Alex and Danae", rp.apply_mapping("Dana and Danae", {"Dana": "Alex"}))

    def test_residue_checks_the_account_word_by_word_and_spans_whole(self):
        self.assertEqual(1, rp.residue("thanks dana", ["Dana Reyes"], []), "a first name alone is identifying")
        self.assertGreater(rp.residue("mail dana.reyes@x.io", [None, "dana.reyes@x.io"], []), 0)
        # the email's local part stands in for the last name the cache doesn't carry
        self.assertGreater(rp.residue("Reyes said hi", [None, "dana_reyes88@x.io"], []), 0)
        self.assertEqual(0, rp.residue("ok", ["Dana", "dana_reyes88@x.io"], []))
        self.assertEqual(0, rp.residue("sent from my gmail", [None, "<redacted-email>"], []), "a domain is not residue")
        self.assertGreater(rp.residue("my handle is dana_reyes88", [None, "dana_reyes88@x.io"], []), 0,
                           "a leftover username is residue even without its domain")
        # "New" from a replaced "New York" is an ordinary word, not residue
        self.assertEqual(0, rp.residue("a New start in the city", [], ["New York"]))
        self.assertEqual(1, rp.residue("back in New York", [], ["New York"]))


class SelectRowsTests(unittest.TestCase):
    def _rebuilt(self):
        return {"meta": {"coach_version": "1.32.0"}, "request": {"input": [{"role": "system", "content": SYSTEM}]}}

    def _deid(self, residue=0):
        return {"context": "## Context\n\nUser name: Alex.", "input": [{"role": "user", "content": "x"}],
                "residue": residue}

    def test_only_reproduced_clean_unchanged_alerts_become_rows_the_rest_is_counted(self):
        ok = {"reproduced": True}
        labels = {t: {"expected": "no_trigger", "pattern": "p", "note": "n"} for t in "abcdef"}
        labels["a"]["expected"] = "substance"
        rebuilt = {t: self._rebuilt() for t in "abcde"}            # f not rebuilt
        replay = {t: ok for t in "abde"}                             # c did not reproduce
        deid = {"a": self._deid(), "b": self._deid(residue=1), "e": self._deid()}  # d not de-identified
        deid_replay = {"a": ok, "e": {"reproduced": False}}         # e changed after de-identification
        turns = {"a": {"category": "substance"}}
        rows, skipped = rp.select_rows(rebuilt, replay, deid_replay, deid, labels, turns, "gfp")
        self.assertEqual(["gfp-01"], [r["metadata"]["case_id"] for r in rows])
        self.assertEqual({"not_rebuilt": ["f"], "not_reproduced": ["c"], "pii_residue": ["b"],
                          "not_deidentified": ["d"], "changed_after_deid": ["e"]}, skipped)
        row = rows[0]
        self.assertEqual({"category": "substance"}, row["expected"])
        self.assertEqual("## Context\n\nUser name: Alex.", row["metadata"]["contextInstructions"])
        self.assertIn("true-positive", row["tags"])
        self.assertNotIn("Dana", str(row))


if __name__ == "__main__":
    unittest.main()
