"""data:investigate-user-rating `feedback_dataset.py` and the shared `coach_calls.py`.

The network half was checked live on 2026-10-16: `gather` fetched the same 14
coach requests a hand-built prototype did, byte for byte, and `rows` produced the
same 7 rows. These tests pin which ledger row is the rated call, what is
counted rather than dropped, and that both copies of `coach_calls.py` behave the
same.
"""
from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import os
import pathlib
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from reader_harness import REPO_ROOT  # noqa: E402

SCRIPTS = REPO_ROOT / "plugins/data/skills/investigate-user-rating/scripts"
COPIES = [REPO_ROOT / "plugins/data/skills/analyze-conversation-guardrails/scripts/coach_calls.py",
          SCRIPTS / "coach_calls.py"]
sys.path.insert(0, str(SCRIPTS))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


fd = _load("feedback_dataset", SCRIPTS / "feedback_dataset.py")
SYSTEM = "## Role\n\nYou are Aurie.\n\n\n## Context\n\nUser name: Dana."


def _msg(role, text):
    return {"type": "message", "role": role, "content": [{"type": "input_text", "text": text}]}


class CoachCallsCopiesTests(unittest.TestCase):
    def test_copies_are_identical(self):
        self.assertEqual(COPIES[0].read_bytes(), COPIES[1].read_bytes())

    def test_deidentify_replaces_and_counts_in_every_copy(self):
        def post(_body):
            spans = {"spans": [{"text": "Dana Reyes", "type": "person"}, {"text": "Dana", "type": "person"},
                               {"text": "Boston", "type": "place"}]}
            return {"output": [{"type": "message", "content": [{"text": json.dumps(spans)}]}]}
        for i, path in enumerate(COPIES):
            cc = _load(f"coach_calls_{i}", path)
            out = cc.deidentify(post, "User name: Dana Reyes.", [{"role": "user", "content": "Dana in Boston"}],
                                ["Dana", "dana.reyes@x.com"])
            self.assertEqual("User name: Alex.", out["context"])
            self.assertEqual("Sam in the city", out["input"][0]["content"])
            self.assertEqual(0, out["residue"])
            self.assertEqual(3, out["replaced"])


class RatedCallTests(unittest.TestCase):
    def test_last_call_of_the_prompter_in_the_dialogue_any_casing(self):
        rows = [{"prompterName": "coach-prompter", "dialogueId": "abc-1", "createdAt": "2026-10-16T10:00:00Z", "openaiId": "a"},
                {"prompterName": "coach-prompter", "dialogueId": "ABC-1", "createdAt": "2026-10-16T10:00:05Z", "openaiId": "b"},
                {"prompterName": "memory-prompter", "dialogueId": "abc-1", "createdAt": "2026-10-16T10:01:00Z", "openaiId": "c"},
                {"prompterName": "coach-prompter", "dialogueId": "zzz", "createdAt": "2026-10-16T11:00:00Z", "openaiId": "d"}]
        self.assertEqual("b", fd.rated_call(rows, "Abc-1", "coach-prompter")["openaiId"])
        self.assertIsNone(fd.rated_call(rows, "nope", "coach-prompter"))

    def test_candidates_count_every_skip(self):
        c = {"shape": "chat", "prompter_name": "coach-prompter"}
        entries = [{"is_test": True}, {"is_infra": True}, {**c, "prompter_name": "quick-processing-prompter"},
                   {**c, "shape": "conversation", "dialogue_id": "d", "uid": "u1"}, c, {**c, "dialogue_id": "d", "uid": " ", "conversation_id": "c0"},
                   {**c, "dialogue_id": "d", "uid": "u1"}, {**c, "dialogue_id": "d", "uid": "u1", "conversation_id": "c1"}]
        keep, skipped = fd.candidates(entries, "coach-prompter")
        self.assertEqual(1, len(keep))
        self.assertEqual({"test_account": 1, "infra": 1, "other_prompter": 1, "not_a_reply_rating": 1,
                          "no_dialogue_id": 1, "no_conversation_id": 1, "no_uid": 1}, skipped)

    def test_a_blank_uid_never_asks_the_api_for_the_token_owner(self):
        orig = fd.requests.get
        fd.requests.get = lambda *a, **k: (_ for _ in ()).throw(AssertionError("called /users/ with a blank id"))
        key = os.environ.get("AURA_API_TOKEN")
        os.environ["AURA_API_TOKEN"] = "t"
        try:
            self.assertIsNone(fd._account(""))
            self.assertIsNone(fd._account("  "))
        finally:
            fd.requests.get = orig
            if key is None:
                os.environ.pop("AURA_API_TOKEN", None)
            else:
                os.environ["AURA_API_TOKEN"] = key


class AccountAndRetryTests(unittest.TestCase):
    def setUp(self):
        self._get, self._key = fd.requests.get, os.environ.get("AURA_API_TOKEN")
        os.environ["AURA_API_TOKEN"] = "t"

    def tearDown(self):
        fd.requests.get = self._get
        if self._key is None:
            os.environ.pop("AURA_API_TOKEN", None)
        else:
            os.environ["AURA_API_TOKEN"] = self._key

    def test_a_failed_lookup_is_none_never_an_exception(self):
        def boom(*a, **k):
            raise fd.requests.ConnectionError("down")
        fd.requests.get = boom
        self.assertIsNone(fd._account("u1"))

        class NotJson:
            status_code = 200

            def json(self):
                raise ValueError("not json")
        fd.requests.get = lambda *a, **k: NotJson()
        self.assertIsNone(fd._account("u1"))

    def test_an_account_with_no_name_or_email_is_none(self):
        class Resp:
            status_code = 200

            def json(self):
                return {"data": {"givenName": "", "email": None}}
        fd.requests.get = lambda *a, **k: Resp()
        self.assertIsNone(fd._account("u1"))

    def test_flex_falls_back_only_on_flex_refusals_in_every_copy(self):
        for i, path in enumerate(COPIES):
            cc = _load(f"coach_calls_retry_{i}", path)
            self.assertTrue(cc.flex_fallback(429, '{"error": {"code": "resource_unavailable"}}'))
            self.assertTrue(cc.flex_fallback(400, "Unsupported value for service_tier"))
            self.assertFalse(cc.flex_fallback(429, '{"error": {"code": "rate_limit_exceeded"}}'))
            self.assertFalse(cc.flex_fallback(500, "resource_unavailable"))


class SelectRowsTests(unittest.TestCase):
    def test_only_labelled_clean_ratings_become_rows_the_rest_is_counted(self):
        g = {t: {"polarity": "negative", "version": "1.32.2"} for t in ("1", "2", "3", "4")}
        deid = {"1": {"context": "c", "input": [{"role": "user", "content": "hi"}], "residue": 0},
                "2": {"context": "c", "input": [], "residue": 2}}
        labels = {"1": {"issue": "over-analysis", "rubric": "PASS if short"}, "2": {"issue": "x", "rubric": "y"},
                  "3": {"issue": "x", "rubric": "y"}, "4": {"issue": "x"}, "9": {"issue": "x", "rubric": "y"}}
        rows, skipped = fd.select_rows(g, deid, labels, "cfb")
        self.assertEqual(1, len(rows))
        self.assertEqual("PASS if short", rows[0]["expected"])
        self.assertEqual(["conversation-feedback", "over-analysis"], rows[0]["tags"])
        self.assertEqual("cfb-01", rows[0]["metadata"]["case_id"])
        self.assertTrue(rows[0]["metadata"]["deidentified"])
        self.assertEqual({"pii_residue": ["2"], "not_deidentified": ["3"], "no_issue_or_rubric": ["4"],
                          "not_gathered": ["9"]}, skipped)


TIME = "### Current Time\n\nIt is Tuesday."
BODY, CONTEXT = "## Role\n\nYou are Aurie.", "## Context\n\nUser name: Dana."
TURNS = [_msg("user", "hi"), _msg("assistant", "hello"), _msg("user", "help")]
LAYOUTS = {  # coach request input per Intelligence Service version, oldest first
    "1.34": [_msg("system", SYSTEM)] + TURNS,
    "1.35": [_msg("system", SYSTEM)] + TURNS + [_msg("developer", TIME)],
    "1.36": [_msg("system", BODY), _msg("developer", CONTEXT)] + TURNS + [_msg("developer", TIME)],
    "1.40": [_msg("system", BODY), _msg("developer", "---"), _msg("user", "---"), _msg("developer", CONTEXT)]
            + TURNS + [_msg("developer", TIME)],
}


class LayoutTests(unittest.TestCase):
    """Every copy of coach_calls reads all four coach layouts the same way."""

    def test_every_copy_reads_every_layout(self):
        for i, path in enumerate(COPIES):
            cc = _load(f"coach_calls_layout_{i}", path)
            for version, items in LAYOUTS.items():
                with self.subTest(copy=path.parent.parent.name, version=version):
                    req, _ = cc.exact_request({"model": "m"}, items)
                    self.assertEqual([(x["role"], cc.item_text(x)) for x in items],
                                     [(m["role"], m["content"]) for m in req["input"]])
                    parts = cc.coach_parts(req["input"])
                    self.assertEqual((BODY, CONTEXT), (parts["body"], parts["context"]))
                    self.assertEqual("" if version == "1.34" else TIME, parts["developer"])
                    self.assertEqual(["hi", "hello", "help"], [m["content"] for m in parts["conversation"]])

    def test_gathered_turns_skip_the_delimiters_and_the_time(self):
        req, _ = fd.exact_request({"model": "m"}, LAYOUTS["1.40"])
        g = {"request": req, "context_marker": "\n\n\n## Context"}
        self.assertEqual([("user", "hi"), ("assistant", "hello"), ("user", "help")],
                         [(m["role"], m["content"]) for m in fd.conversation_for(g)])
        row = fd.row_for("1.0", {**g, "developer": TIME}, {"context": CONTEXT, "input": []},
                         {"issue": "x", "rubric": "y"}, "cfb-01")
        self.assertEqual(TIME, row["metadata"]["developerInstructions"])


class GatherCommandTests(unittest.TestCase):
    """cmd_gather with the ledger, OpenAI and the Aura API stubbed."""

    def setUp(self):
        self.dir = pathlib.Path(tempfile.mkdtemp())
        c = {"prompter_name": "coach-prompter", "shape": "chat", "polarity": "negative"}
        entries = [{**c, "ts": "1.0", "uid": "u1", "conversation_id": "c1", "dialogue_id": "D1", "feedback": "This isn't helpful"},
                   {**c, "ts": "2.0", "uid": "u2", "conversation_id": "c2", "dialogue_id": "D2"},
                   {**c, "ts": "3.0", "uid": "u3", "conversation_id": "c3", "dialogue_id": "D3"},
                   {**c, "ts": "4.0", "uid": "u4", "conversation_id": "c4", "dialogue_id": "D4"},
                   {**c, "ts": "5.0", "uid": "u5", "conversation_id": "c5", "dialogue_id": "D5"}]
        (self.dir / "r.json").write_text(json.dumps({"entries": entries}), encoding="utf-8")
        ledger = {"c1": [{"prompterName": "coach-prompter", "dialogueId": "d1", "openaiId": "r1", "createdAt": "x", "version": "1.32.2"}],
                  "c2": [], "c3": [{"prompterName": "coach-prompter", "dialogueId": "d3", "openaiId": "r3", "createdAt": "x"}],
                  "c4": [{"prompterName": "coach-prompter", "dialogueId": "d4", "openaiId": "r1", "createdAt": "x"}]}
        resp = {"model": "chat-latest", "top_p": 0.98, "tools": [{"name": "t"}],
                "output": [{"type": "message", "content": [{"text": "I'm here."}]}]}
        items = {"r1": [_msg("user", "help"), _msg("system", SYSTEM)], "r3": [_msg("user", "help")]}
        self._orig = (fd.rr.fetch_ledger_rows_by_conversation, fd.rr.fetch_response, fd.rr.fetch_input_items, fd._account)
        def ledger_read(cid):
            if cid == "c5":
                raise fd.rr.ResponsesReaderError("ledger 503")
            return ledger[cid]
        fd.rr.fetch_ledger_rows_by_conversation = ledger_read
        fd.rr.fetch_response = lambda key, proj, rid: resp
        fd.rr.fetch_input_items = lambda key, proj, rid: items[rid]
        fd._account = lambda uid: None if uid == "u4" else ["Dana"]
        self._env = {k: os.environ.get(k) for k in ("OPENAI_CONVERSATION_API_KEY", "AURA_API_TOKEN")}
        os.environ.update(OPENAI_CONVERSATION_API_KEY="test", AURA_API_TOKEN="test")

    def tearDown(self):
        (fd.rr.fetch_ledger_rows_by_conversation, fd.rr.fetch_response, fd.rr.fetch_input_items, fd._account) = self._orig
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.dir)

    def test_gathers_the_rated_call_and_counts_the_rest(self):
        a = argparse.Namespace(ratings=[str(self.dir / "r.json")], prompter="coach-prompter", out=str(self.dir / "g.json"),
                               tools_out=str(self.dir / "t.json"), params_out=str(self.dir / "p.json"),
                               context_marker="\n\n\n## Context")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            fd.cmd_gather(a)
        g = json.loads((self.dir / "g.json").read_text(encoding="utf-8"))
        self.assertEqual(["1.0"], list(g))
        self.assertEqual("I'm here.", g["1.0"]["reply"])
        self.assertEqual("## Context\n\nUser name: Dana.", g["1.0"]["context"])
        self.assertEqual(["system", "user"], [m["role"] for m in g["1.0"]["request"]["input"]])
        summary = json.loads(buf.getvalue().strip().splitlines()[-1])
        self.assertEqual({"no_ledger_row": 1, "unparseable_call": 1, "no_account": 1, "ledger_error": 1}, summary["skipped"])
        self.assertEqual({"top_p": 0.98}, json.loads((self.dir / "p.json").read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()
