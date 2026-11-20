#!/usr/bin/env python3
"""eng:create-prompt-dataset row contract: `check_rows.py` and `deidentify.py`.

Prompt-dataset rows now share the conversation-dataset shape (PROD-7699): turns in
`input` (`[]` for a single-response prompter), the context block in
`metadata.contextInstructions`, the raw inputs in `metadata.structured`, and
`expected = {rubric, items: {min, max}}`.

The failure worth pinning is the silent one. A row left in the old shape
(`input: {contextInstructions, structured}`) still bulk-inserts, because the
validator accepts any object `input`, and a prompt templated on
`{{metadata.contextInstructions}}` then renders an empty context without
erroring. So the old shape must be refused by name, and de-identification must
rewrite the fields the prompt actually reads. The transport is stubbed: no
network, no keys.
"""
from __future__ import annotations

import copy
import importlib.util
import json
import pathlib
import sys
import unittest

_REPO = pathlib.Path(__file__).resolve().parent.parent
_SCRIPTS = _REPO / "plugins/eng/skills/create-prompt-dataset/scripts"
_SAVE_ROW = _REPO / "plugins/data/skills/save-to-braintrust-dataset/scripts"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


check_rows = _load("cpd_check_rows", _SCRIPTS / "check_rows.py")
deidentify = _load("cpd_deidentify", _SCRIPTS / "deidentify.py")
if str(_SAVE_ROW) not in sys.path:
    sys.path.insert(0, str(_SAVE_ROW))
import save_row  # noqa: E402

CONTEXT = ("## Input Data\n\n### User Information\nGiven name is Priya. Lives in Pune.\n\n"
           "### Past Conversations\n- Talked about her sister Meera's wedding.")


def row(**over):
    r = {
        "input": [],
        "expected": {"rubric": "Slice criteria.\n\nExpected item count: 0 to 3.", "items": {"min": 0, "max": 3}},
        "metadata": {
            "contextInstructions": CONTEXT,
            "structured": {"asOf": "2026-09-20T07:26:06.712Z", "userInformation": "Given name is Priya. Lives in Pune.",
                           "summaries": [{"summary": "Sister Meera's wedding in Pune."}], "cards": {"open": []}},
            "case_id": "steerAway_2_8vQoALm1", "pattern": "steerAway", "severity": "medium",
            "asOf": "2026-09-20T07:26:06.712Z", "userId": "8vQoALm1abcdefghijklmnopqrst",
            "mustNotRepeat": ["Plan for Meera's wedding"], "steerAwayFrom": [],
        },
        "tags": ["conversationStartersFeed", "steerAway"],
    }
    for path, value in over.items():
        target, *keys = path.split("__")
        node = r
        for k in [target, *keys][:-1]:
            node = node[k]
        last = [target, *keys][-1]
        if value is DELETE:
            node.pop(last, None)
        else:
            node[last] = value
    return r


DELETE = object()


def problems(r, deid=False):
    return check_rows.validate([r], deid)


class NewShape(unittest.TestCase):
    def test_single_response_row_passes(self):
        self.assertEqual(problems(row()), [])

    def test_turns_pass(self):
        self.assertEqual(problems(row(input=[{"role": "user", "content": "hi"},
                                             {"role": "assistant", "content": "hello"}])), [])

    def test_passes_the_bulk_insert_validator(self):
        # the loader is a different skill; its contract is what decides whether the row lands
        self.assertEqual(save_row.validate_bulk_row(row(), 0), [])


class Refusals(unittest.TestCase):
    def assertNamed(self, r, needle):
        found = problems(r)
        self.assertTrue(any(needle in p for p in found), f"{needle!r} not in {found}")

    def test_null_input(self):
        self.assertNamed(row(input=None), "input must be a list")

    def test_old_shape_input_is_named(self):
        old = {"contextInstructions": CONTEXT, "structured": {"asOf": "x"}}
        self.assertNamed(row(input=old), "old shape")

    def test_bad_turn(self):
        self.assertNamed(row(input=[{"role": "user"}]), "input[0]")

    def test_string_expected_is_named(self):
        self.assertNamed(row(expected="rubric text"), "expected is a string")

    def test_expected_cards_is_named(self):
        self.assertNamed(row(metadata__expectedCards={"min": 0, "max": 3}), "expectedCards")

    def test_items_range(self):
        self.assertNamed(row(expected__items={"min": 4, "max": 1}), "above max")
        self.assertNamed(row(expected__items={"min": True, "max": 3}), "non-negative integers")
        self.assertNamed(row(expected__items=DELETE), "non-negative integers")

    def test_missing_rubric(self):
        self.assertNamed(row(expected__rubric=" "), "expected.rubric")

    def test_context_and_structured_live_in_metadata(self):
        self.assertNamed(row(metadata__contextInstructions=DELETE), "metadata.contextInstructions")
        self.assertNamed(row(metadata__structured={}), "metadata.structured missing")
        self.assertNamed(row(metadata__structured__summaries=None), "metadata.structured.summaries is null")

    def test_require_deidentified(self):
        self.assertTrue(any("not de-identified" in p for p in problems(row(), deid=True)))


class Deidentify(unittest.TestCase):
    SPANS = [{"text": "Priya", "type": "person"}, {"text": "Meera", "type": "person"},
             {"text": "Pune", "type": "place"}]

    def run_row(self, r):
        sent = []

        def fake_post(body, key):
            sent.append(body["input"][1]["content"])
            return {"output": [{"type": "message", "content": [{"text": json.dumps({"spans": self.SPANS})}]}]}

        original = deidentify.post
        deidentify.post = fake_post
        try:
            new, replaced, left = deidentify.deidentify_row(copy.deepcopy(r), "k", "m")
        finally:
            deidentify.post = original
        return new, replaced, left, sent

    def test_rewrites_every_field_the_prompt_and_scorers_read(self):
        r = row(input=[{"role": "user", "content": "Meera is in Pune this week."}])
        new, replaced, left, sent = self.run_row(r)
        self.assertEqual(left, 0)
        self.assertEqual(replaced, 3)
        # the span finder saw the turns as well as the context
        self.assertIn("Meera is in Pune this week.", sent[0])
        blob = json.dumps({"input": new["input"], "metadata": new["metadata"]})
        for real in ("Priya", "Meera", "Pune"):
            self.assertNotIn(real, blob)
        self.assertNotIn("Pune", new["metadata"]["contextInstructions"])
        self.assertNotIn("Meera", new["metadata"]["structured"]["summaries"][0]["summary"])
        self.assertNotIn("Meera", new["metadata"]["mustNotRepeat"][0])
        self.assertEqual(new["metadata"]["userId"], "8vQoALm1")
        self.assertEqual(new["expected"], r["expected"])
        self.assertEqual(problems(new, deid=True), [])

    def test_single_response_row_keeps_empty_input(self):
        new, _, left, sent = self.run_row(row())
        self.assertEqual(new["input"], [])
        self.assertEqual(left, 0)
        self.assertNotIn("## Conversation", sent[0])

    def test_old_shape_row_is_held_back_not_sent(self):
        r = row(metadata__contextInstructions=DELETE)
        with self.assertRaises(KeyError):
            self.run_row(r)


if __name__ == "__main__":
    unittest.main()
