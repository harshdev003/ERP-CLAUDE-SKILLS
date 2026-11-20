#!/usr/bin/env python3
"""Tests for the completions-arm predicate in `fetch_logs.py`.

Scoped deliberately narrow. `fetch_logs.py` is a large module with a live walk
over OpenAI's stored completions; this file covers the one piece the Responses
work broke and fixed — `_is_conversation_prompter`, the predicate that keeps
side-effect records out of a user-pivot walk.

That predicate is the whole of the #738 defect: the `Conversation` project
stores `insight-card-prompter` and `starters-prompter` records under the same
`metadata.user` as real turns, so a walk filtered on the user alone returned
them as conversations. Each carries only a system message, so each rendered as a
conversation with zero turns — 12 of 35 on a real 3-day sample, inflating the
turn count and the truncation count with it.

Set `AINATIVE_FETCH_LOGS_PATH` to check the suite against a historical file.
"""
from __future__ import annotations

import os
import pathlib
import sys
import unittest

_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from reader_harness import REPO_ROOT, load_reader  # noqa: E402

# The journal fork pivots on journal entries and passes `JOURNAL_PROMPTERS`
# explicitly, so it has no use for the conversation predicate. Skipped by name
# rather than by "does the symbol exist" — the latter would silently excuse any
# copy that lost it, which is the regression this file guards.
NO_CONVERSATION_PIVOT = {"analyze-user-journal"}


def _targets() -> list[pathlib.Path]:
    override = os.environ.get("AINATIVE_FETCH_LOGS_PATH")
    if override:
        return [pathlib.Path(override)]
    return sorted((REPO_ROOT / "plugins").rglob("fetch_logs.py"))


def _record(prompter: str, key: str = "name") -> dict:
    return {"metadata": {key: prompter, "user": "u1"}}


class FetchLogsPredicate(unittest.TestCase):
    path: pathlib.Path = None  # type: ignore[assignment]
    skill: str = ""

    def setUp(self):
        if self.skill in NO_CONVERSATION_PIVOT:
            self.skipTest(f"{self.skill} pivots on journal entries, not conversations")
        # `load_reader` owns the sys.path and sibling-module isolation — this
        # module's `from responses_reader import ...` must bind *this* copy's
        # reader, not whichever one loaded first.
        self.mod = load_reader(self.path)

    def test_predicate_exists(self):
        """Its absence *is* the #738 defect — a user-pivot walk with no
        prompter filter returns every side-effect record as a conversation."""
        self.assertTrue(
            hasattr(self.mod, "_is_conversation_prompter"),
            "no prompter predicate: the user-pivot walk filters on metadata.user "
            "alone, so insight cards come back as zero-turn conversations",
        )

    def test_conversation_prompters_accepted(self):
        for p in ("coach-prompter", "quick-processing-prompter", "chat-prompter"):
            with self.subTest(prompter=p):
                self.assertTrue(self.mod._is_conversation_prompter(_record(p)))

    def test_side_effect_prompters_rejected(self):
        for p in ("insight-card-prompter", "starters-prompter", "memory-prompter",
                  "summary-prompter", "profile-prompter"):
            with self.subTest(prompter=p):
                self.assertFalse(self.mod._is_conversation_prompter(_record(p)),
                                 f"{p} would render as a zero-turn conversation")

    def test_both_metadata_key_spellings_accepted(self):
        """OpenAI stored completions use `name`; Braintrust spans and the
        llmCall ledger use `prompterName`. Reading only one silently rejects
        every record from the other source."""
        self.assertTrue(self.mod._is_conversation_prompter(_record("coach-prompter", "name")))
        self.assertTrue(self.mod._is_conversation_prompter(_record("coach-prompter", "prompterName")))

    def test_missing_metadata_is_rejected_not_crashed(self):
        for item in ({}, {"metadata": None}, {"metadata": {}}):
            with self.subTest(item=item):
                self.assertFalse(self.mod._is_conversation_prompter(item))


class HarnessIsolation(unittest.TestCase):
    """Guards the harness's own contract, not the code under test.

    `fetch_logs.py` imports `responses_reader` by plain name, which caches under
    a name identical across copies. Without an explicit purge, every copy's
    `fetch_logs` binds whichever reader loaded first — and then sibling drift is
    invisible while the suite reports all-green. That is not hypothetical: it was
    the state of this file before the isolation was added, verified by breaking
    one copy's `CONVERSATION_PROMPTERS` and watching all 30 tests still pass.

    So: two copies must end up with genuinely distinct reader objects.
    """

    def test_each_copy_binds_its_own_responses_reader(self):
        paths = [p for p in _targets()
                 if p.parent.parent.name not in NO_CONVERSATION_PIVOT]
        if len(paths) < 2:
            self.skipTest("needs two conversation-pivot copies to compare")
        a, b = load_reader(paths[0]), load_reader(paths[1])
        self.assertIsNot(
            a.CONVERSATION_PROMPTERS, b.CONVERSATION_PROMPTERS,
            "both copies bound the same cached responses_reader — sibling drift "
            "is invisible and this suite's per-copy claim is false",
        )
        self.assertIsNot(a, b)


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromTestCase(HarnessIsolation))
    for path in _targets():
        skill = path.parent.parent.name
        cls = type(
            f"FetchLogs_{skill.replace('-', '_')}",
            (FetchLogsPredicate,),
            {"path": path, "skill": skill, "__module__": __name__},
        )
        suite.addTests(loader.loadTestsFromTestCase(cls))
    return suite


if __name__ == "__main__":
    unittest.main()
