#!/usr/bin/env python3
"""Transcript readers on Intelligence Service 1.40's coach layout ([PROD-7728]).

From 1.40 a coach call's input is: system prompt · developer `---` · user `---`
· developer context · history · user · developer `### Current Time`. The `---`
pair is a prompt-cache delimiter (IS `utils/buildContextMessages.js`), not a
turn, so every `messages_to_turns` copy must drop it — and only there: a user
typing `---` mid-conversation is a real turn. Copies are found by glob, so a new
copy is covered without editing this file.
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys
import unittest

_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from reader_harness import REPO_ROOT, load_reader  # noqa: E402

CONTEXT = "## Context\n\nUser name: Alex."
TIME = "### Current Time\n\nIt is Tuesday."
V140 = [
    {"role": "system", "content": "## Role\n\nYou are Aurie."},
    {"role": "developer", "content": "---"},
    {"role": "user", "content": "---"},
    {"role": "developer", "content": CONTEXT},
    {"role": "user", "content": "hi"},
    {"role": "assistant", "content": "hello"},
    {"role": "user", "content": "---"},
    {"role": "assistant", "content": "a divider?"},
    {"role": "user", "content": "help"},
    {"role": "developer", "content": TIME},
]


def _turn_copies() -> list[pathlib.Path]:
    return [p for p in sorted((REPO_ROOT / "plugins").rglob("fetch_logs.py"))
            if "def messages_to_turns(" in p.read_text(encoding="utf-8")]


class MessagesToTurns(unittest.TestCase):
    def test_there_are_copies_to_check(self):
        self.assertGreaterEqual(len(_turn_copies()), 5)

    def test_the_delimiter_pair_and_developer_messages_are_not_turns(self):
        for path in _turn_copies():
            with self.subTest(copy=path.parent.parent.name):
                turns = load_reader(path).messages_to_turns(V140, None, {})
                self.assertEqual([("hi", "hello"), ("---", "a divider?"), ("help", None)],
                                 [(t["input"]["content"], (t["output"] or {}).get("content")) for t in turns])

    def test_older_layouts_are_unchanged(self):
        old = [{"role": "system", "content": "prompt\n\n\n" + CONTEXT}, {"role": "user", "content": "hi"},
               {"role": "assistant", "content": "hello"}, {"role": "user", "content": "help"}]
        for path in _turn_copies():
            with self.subTest(copy=path.parent.parent.name):
                turns = load_reader(path).messages_to_turns(old, None, {})
                self.assertEqual(["hi", "help"], [t["input"]["content"] for t in turns])


class JournalInput(unittest.TestCase):
    def test_the_context_is_kept_and_the_delimiters_dropped(self):
        mod = load_reader(REPO_ROOT / "plugins/data/skills/analyze-user-journal/scripts/fetch_logs.py")
        self.assertEqual(["## Role\n\nYou are Aurie.", CONTEXT, "hi", "---", "help", TIME],
                         mod._input_texts(V140))


class StoredResponsesSystemText(unittest.TestCase):
    def test_the_developer_context_is_part_of_the_probed_text(self):
        path = REPO_ROOT / "plugins/eng/skills/create-prompt-dataset/scripts/stored_responses.py"
        spec = importlib.util.spec_from_file_location("stored_responses_ctx", path)
        mod = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(mod)
        inp = {"data": [{"role": m["role"], "content": [{"type": "input_text", "text": m["content"]}]} for m in V140]}
        self.assertEqual("\n\n".join(["## Role\n\nYou are Aurie.", CONTEXT, TIME]), mod.system_text(inp))


if __name__ == "__main__":
    unittest.main()
