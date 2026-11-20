#!/usr/bin/env python3
"""Voice barge-in turns in `fetch_logs.py`'s Responses-API transcript.

When a LiveKit voice user speaks again before the reply is spoken, the agent
drops that unspoken reply from history. The next call's `/input_items` then
carry two user turns back to back, and the transcript rendered the first with
`output: None` -- which reviewers read as the model returning an empty reply. It
had not: the barged-in call's own stored response carries the reply (131 and
158 output tokens on the two conversations that surfaced this; 0 zero-output
coach-prompter calls in 105,607 over 7 days).

The items fed to the builder come from `responses_reader`'s own producer
(`fetch_responses_for_conversation`) over a stubbed transport, not from
hand-built dicts, so the builder sees exactly the shape production gives it.

Runs against every copy of `fetch_logs.py` that carries the Responses builder.
"""
from __future__ import annotations

import pathlib
import sys
import unittest

_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from reader_harness import FakeTransport, load_reader, script_paths  # noqa: E402

CID = "FC1D9199-87CF-43D6-A986-3FC85C90EF7F"


def _targets() -> list[pathlib.Path]:
    return [p for p in script_paths("fetch_logs.py")
            if "def build_conversation_from_responses" in p.read_text()]


def _row(n: int, dialogue: str, prompter: str = "coach-prompter",
         second: int | None = None) -> dict:
    return {
        "openaiId": f"resp_{n}",
        "openaiAPI": "responses",
        "prompterName": prompter,
        "conversationId": CID,
        "dialogueId": dialogue,
        "userId": "u1",
        "createdAt": f"2026-10-07T01:26:{second or 10 + 2 * n:02d}Z",
    }


def _msg(role: str, text: str, n: int) -> dict:
    kind = "output_text" if role == "assistant" else "input_text"
    return {"id": f"msg_{n}", "type": "message", "role": role,
            "content": [{"type": kind, "text": text}]}


def _resp(n: int, text: str | None) -> dict:
    out = ([{"type": "message", "role": "assistant",
             "content": [{"type": "output_text", "text": text}]}]
           if text else [{"type": "function_call", "name": "storeInMemory"}])
    return {"id": f"resp_{n}", "model": "gpt-x", "output": out,
            "usage": {"output_tokens": 131}}


HI = _msg("user", "hi", 1)
HELLO = _msg("assistant", "hello", 2)
THERE = _msg("user", "There. I know it's only been three months...", 3)
HOLD = _msg("user", "hold on.", 4)


def _barge_in_fixture() -> dict:
    """c1 answers "hi"; c2's reply to THERE is barged-in; c3 answers HOLD."""
    return {
        "ledger_rows": [_row(1, "d1"), _row(2, "d2"), _row(3, "d3")],
        "responses": {
            "resp_1": _resp(1, "hello"),
            "resp_2": _resp(2, "That sounds like a lot to carry."),
            "resp_3": _resp(3, "Of course, take your time."),
        },
        # `/input_items` is newest-first.
        "input_items": {
            "resp_1": [HI],
            "resp_2": [THERE, HELLO, HI],
            "resp_3": [HOLD, THERE, HELLO, HI],
        },
    }


class BargeInTurns(unittest.TestCase):
    path: pathlib.Path = None  # type: ignore[assignment]

    def setUp(self):
        self.mod = load_reader(self.path)

    def _run(self, fixture: dict, *, fail: str | None = None, api_key="sk-test"):
        t = FakeTransport(self.mod, **fixture)
        if fail:
            t.fail(fail)
        # The reader's functions resolve `_get_json` and `_ledger` through their own module
        # globals, which `load_reader` has evicted from `sys.modules`.
        self.mod.fetch_response.__globals__["_get_json"] = t._get_json
        self.mod.fetch_response.__globals__["_ledger"] = t._ledger
        items = self.mod.fetch_responses_for_conversation(
            "sk-test", "proj_x", CID
        )["items"]
        if api_key is None:
            conv = self.mod.build_conversation_from_responses(CID, items)
        else:
            conv = self.mod._build_mixed_conversation(api_key, "proj_x", CID, items)
        return conv, t

    def _contents(self, conv):
        return [(t["input"]["content"], (t["output"] or {}).get("content"))
                for t in conv["turns"]]

    def test_reply_recovered_from_barged_in_call(self):
        conv, _ = self._run(_barge_in_fixture())
        turns = conv["turns"]
        self.assertEqual(len(turns), 3)
        self.assertEqual(turns[1]["output"]["content"], "That sounds like a lot to carry.")
        self.assertTrue(turns[1]["interrupted"])
        self.assertEqual(turns[1]["reply_source"], "resp_2")
        self.assertNotIn("interrupted", turns[0])
        self.assertNotIn("interrupted", turns[2])
        self.assertEqual(turns[2]["output"]["content"], "Of course, take your time.")
        self.assertEqual(conv["interrupted_turn_count"], 1)

    def test_no_turn_ever_renders_none(self):
        """The negative assertion is the point: `None` is what was misread."""
        for label, kwargs in (
            ("fetch fails", {"fail": "/responses/resp_2"}),
            ("no api key", {"api_key": None}),
        ):
            with self.subTest(label):
                conv, _ = self._run(_barge_in_fixture(), **kwargs)
                for turn in conv["turns"]:
                    self.assertIsNotNone(turn["output"], turn)
                self.assertEqual(conv["turns"][1]["output"]["content"],
                                 self.mod.BARGE_IN_PLACEHOLDER)
                self.assertTrue(conv["turns"][1]["interrupted"])
                self.assertIsNone(conv["turns"][1]["reply_source"])

    def test_mismatched_call_is_not_used(self):
        """Positional mapping is a guess; a call answering a different user
        message must never lend its reply to this turn."""
        fx = _barge_in_fixture()
        fx["input_items"]["resp_2"] = [_msg("user", "something else", 9), HELLO, HI]
        conv, _ = self._run(fx)
        self.assertEqual(conv["turns"][1]["output"]["content"],
                         self.mod.BARGE_IN_PLACEHOLDER)

    def test_tool_call_restart_reads_the_dialogues_last_call(self):
        """d2 spawns a tool-call first, then the reply -- same dialogueId."""
        fx = _barge_in_fixture()
        fx["ledger_rows"] = [_row(1, "d1"), _row(2, "d2"), _row(5, "d2"), _row(6, "d3")]
        fx["responses"]["resp_2"] = _resp(2, None)
        fx["responses"]["resp_5"] = _resp(5, "That sounds like a lot to carry.")
        fx["responses"]["resp_6"] = fx["responses"].pop("resp_3")
        fx["input_items"]["resp_5"] = [_msg("function_call_output", "", 8), THERE, HELLO, HI]
        fx["input_items"]["resp_6"] = fx["input_items"].pop("resp_3")
        conv, _ = self._run(fx)
        self.assertEqual(conv["turns"][1]["output"]["content"],
                         "That sounds like a lot to carry.")
        self.assertEqual(conv["turns"][1]["reply_source"], "resp_5")

    def test_sibling_prompter_is_not_the_barged_in_call(self):
        """A guardrails call on the same user text, logged between the barged-in
        coach call and the source, must not take the coach call's slot."""
        fx = _barge_in_fixture()
        fx["ledger_rows"].insert(2, _row(7, "g1", "guardrails-handler-prompter", second=15))
        fx["responses"]["resp_7"] = _resp(7, "GUARDRAIL OUTPUT")
        fx["input_items"]["resp_7"] = [THERE]
        conv, _ = self._run(fx)
        self.assertEqual(conv["turns"][1]["output"]["content"],
                         "That sounds like a lot to carry.")
        self.assertEqual(conv["turns"][1]["reply_source"], "resp_2")

    def test_no_barge_in_no_extra_fetch(self):
        fx = _barge_in_fixture()
        fx["ledger_rows"] = [_row(1, "d1"), _row(2, "d2")]
        conv, t = self._run(fx)
        self.assertEqual(conv["interrupted_turn_count"], 0)
        self.assertFalse(any("interrupted" in turn for turn in conv["turns"]))
        openai = [u for u, _ in t.calls if "/responses/" in u]
        self.assertEqual(sorted(openai), sorted([
            "https://api.openai.com/v1/responses/resp_2",
            "https://api.openai.com/v1/responses/resp_2/input_items",
        ]))


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    for path in _targets():
        skill = path.parent.parent.name
        cls = type(f"BargeInTurns_{skill.replace('-', '_')}", (BargeInTurns,),
                   {"path": path})
        suite.addTests(loader.loadTestsFromTestCase(cls))
    return suite


if __name__ == "__main__":
    unittest.main()
