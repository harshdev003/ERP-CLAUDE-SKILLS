"""Both Braintrust coach-template shapes, through every reader and writer (PROD-7667).

On 2026-10-20 `Coach [Production]` / `[Development]` moved from the *appended*
shape (context appended to the system body) to the *delimited* one that mirrors
Intelligence Service 1.40's order. Every other prompt kept the appended shape,
so each reader must handle both, and no writer may put the old suffix back.

The fixtures are the live message lists with a short fake body.
"""
from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path

from reader_harness import REPO_ROOT

BODY = "## Role\n\nYou are Aurie."
TAG = "{{metadata.contextInstructions}}"
DELIMITED = [
    {"role": "system", "content": BODY},
    {"role": "system", "content": "---"},
    {"role": "user", "content": "---"},
    {"role": "system", "content": TAG},
    {"role": "system", "content": "## Conversation\n{{input}}"},
]
APPENDED = [
    {"role": "system", "content": BODY + "\n\n---\n\n" + TAG + "\n"},
    {"role": "system", "content": "## Conversation\n{{input}}"},
]
CTX = "## Context\n\nUser name: Alex."
TIME = "### Current Time\n\nIt is Tuesday."
METADATA = {"contextInstructions": CTX, "developerInstructions": TIME}
# A span logged since IS 1.40: the conversation ends with the time message.
LOGGED_INPUT = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"},
                {"role": "user", "content": "how are you"}, {"role": "developer", "content": TIME}]
# A dataset row that carries the time lines only in metadata.
PLAIN_INPUT = LOGGED_INPUT[:-1]

PRODUCTION = [("system", BODY), ("developer", "---"), ("user", "---"), ("developer", CTX),
              ("user", "hi"), ("assistant", "hello"), ("user", "how are you"), ("developer", TIME)]


def _load(name: str, path: Path):
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = mod  # dataclasses resolve their module by name
    spec.loader.exec_module(mod)
    return mod


QA = REPO_ROOT / "plugins/eng/skills/qa-prompt/scripts"
CPV = REPO_ROOT / "plugins/eng/skills/compare-prompt-versions/scripts"
rt = _load("bt_shapes_render_template", QA / "render_template.py")
run_comparison = _load("bt_shapes_run_comparison", CPV / "run_comparison.py")
audit = _load("bt_shapes_audit", REPO_ROOT / "plugins/eng/skills/audit-intelligence-prompts/scripts/audit.py")
BT_SCAFFOLD = REPO_ROOT / "plugins/eng/skills/update-intelligence-prompt/scripts/bt_scaffold.py"
bts = _load("bt_shapes_bt_scaffold", BT_SCAFFOLD)


def _pairs(messages):
    return [(m["role"], m["content"]) for m in messages]


class SharedRendererTests(unittest.TestCase):
    def test_the_copies_are_byte_identical(self):
        self.assertEqual((QA / "render_template.py").read_bytes(), (CPV / "render_template.py").read_bytes())

    def test_template_shape(self):
        self.assertEqual("delimited", rt.template_shape(DELIMITED))
        self.assertEqual("appended", rt.template_shape(APPENDED))
        self.assertEqual("none", rt.template_shape([{"role": "system", "content": BODY}]))

    def _render(self, template, inp):
        return rt.render_messages(template, {"input": copy.deepcopy(inp), "metadata": METADATA})[0]

    def test_both_shapes_reach_the_same_production_layout(self):
        for name, template in (("delimited", DELIMITED), ("appended", APPENDED)):
            for label, inp in (("logged", LOGGED_INPUT), ("plain", PLAIN_INPUT)):
                with self.subTest(shape=name, input=label):
                    out = rt.production_layout(self._render(template, inp), METADATA)
                    self.assertEqual(PRODUCTION, _pairs(out))

    def test_no_double_delimiters_or_time_message(self):
        out = rt.production_layout(self._render(DELIMITED, LOGGED_INPUT), METADATA)
        self.assertEqual(1, sum(1 for m in out if m["content"] == TIME))
        self.assertEqual(2, sum(1 for m in out if m["content"] == "---"))
        # Idempotent: a second pass changes nothing.
        self.assertEqual(out, rt.production_layout(out, METADATA))

    def test_qa_prompt_flavour_keeps_system_roles_and_can_skip_the_time(self):
        for template in (DELIMITED, APPENDED):
            out = rt.production_layout(self._render(template, []), METADATA,
                                       instruction_role="system", append_developer_instructions=False)
            self.assertEqual([("system", BODY), ("system", "---"), ("user", "---"), ("system", CTX)], _pairs(out))

    def test_unrecognised_templates_are_left_alone(self):
        rendered = [{"role": "system", "content": "prompt"}, {"role": "user", "content": "hi"}]
        self.assertEqual(rendered, rt.production_layout(rendered, METADATA))


class ComparePromptVersionsTests(unittest.TestCase):
    def _row(self, inp):
        return {"input": copy.deepcopy(inp), "metadata": dict(METADATA)}

    def test_render_row_for_both_shapes(self):
        for template in (DELIMITED, APPENDED):
            bundle = types.SimpleNamespace(messages=copy.deepcopy(template))
            for inp in (LOGGED_INPUT, PLAIN_INPUT):
                with self.subTest(shape=rt.template_shape(template), logged=inp is LOGGED_INPUT):
                    self.assertEqual(PRODUCTION, _pairs(run_comparison.render_row(bundle, self._row(inp))))

    def test_template_layout_of_a_delimited_template_differs_only_in_roles(self):
        bundle = types.SimpleNamespace(messages=copy.deepcopy(DELIMITED))
        out = run_comparison.render_row(bundle, self._row(LOGGED_INPUT), layout="template")
        self.assertEqual([c for _, c in PRODUCTION], [m["content"] for m in out])
        self.assertEqual(["system", "system", "user", "system"], [m["role"] for m in out[:4]])


class AuditTests(unittest.TestCase):
    def _info(self, messages):
        return audit._extract_info({"id": "x", "name": "Coach [Production]", "slug": "coach-production",
                                    "prompt_data": {"prompt": {"messages": copy.deepcopy(messages)}}})

    def test_body_is_the_same_for_both_shapes(self):
        self.assertEqual(BODY, self._info(DELIMITED).body)
        self.assertEqual(BODY, self._info(APPENDED).body)

    def test_scaffold_shape_is_reported(self):
        self.assertEqual("delimited", self._info(DELIMITED).scaffold)
        self.assertEqual("appended", self._info(APPENDED).scaffold)
        self.assertEqual("none", audit.scaffold_shape([{"role": "system", "content": BODY}]))


class UpdateIntelligencePromptTests(unittest.TestCase):
    def test_body_and_round_trip_keep_each_shape(self):
        for template in (DELIMITED, APPENDED):
            with self.subTest(shape=bts.shape(template)):
                prompt = {"messages": copy.deepcopy(template)}
                self.assertEqual(BODY, bts.body(prompt["messages"]))
                edited = bts.set_body(prompt, BODY + "\n\nBe brief.\n")
                self.assertEqual(BODY + "\n\nBe brief.", bts.body(edited["messages"]))
                self.assertEqual([], bts.check(edited["messages"], like=template))
                self.assertEqual(template[1:], edited["messages"][1:])
                self.assertEqual(template, bts.set_body(edited, BODY)["messages"])

    def test_the_old_suffix_is_never_written_onto_a_delimited_body(self):
        edited = bts.set_body({"messages": copy.deepcopy(DELIMITED)}, BODY + "\n\n---\n\n" + TAG)
        self.assertEqual(BODY, edited["messages"][0]["content"])
        reverted = copy.deepcopy(DELIMITED)
        reverted[0]["content"] = BODY + "\n\n---\n\n" + TAG
        self.assertTrue(bts.check(reverted, like=DELIMITED))

    def test_the_post_merge_check_passes_a_delimited_commit(self):
        """messages[0] has no tag in the delimited shape; the old jq check failed here."""
        with tempfile.TemporaryDirectory() as d:
            ref = Path(d) / "src.json"
            ref.write_text(json.dumps({"messages": DELIMITED}))
            after = json.dumps({"prompt_data": {"prompt": {"messages": DELIMITED}}})
            run = subprocess.run([sys.executable, str(BT_SCAFFOLD), "check", "--like", str(ref)],
                                 input=after, capture_output=True, text=True)
            self.assertEqual(0, run.returncode, run.stderr)
            self.assertIn("delimited", run.stdout)


class QaPromptSetupTests(unittest.TestCase):
    """setup_run's real _build_state, with only the network stubbed."""

    @classmethod
    def setUpClass(cls):
        cls.setup_run = _load("bt_shapes_setup_run", QA / "setup_run.py")
        cls.btp = sys.modules["braintrust_prompt"]
        cls.ctx_mod = cls.setup_run.braintrust_context

    def _state(self, template, mode, inp, layout="production"):
        bundle = self.btp.PromptBundle(prompt_id="p", slug="coach-production", name="Coach [Production]",
                                       project_id="proj", project_name="Conversation", model="gpt-5.4",
                                       provider="openai", params={}, messages=copy.deepcopy(template),
                                       tools=[], version="1", raw_response={})
        context = self.ctx_mod.Context(input_messages=copy.deepcopy(inp), metadata=dict(METADATA),
                                       source="paste", warnings=[])
        sr = self.setup_run
        orig = (sr.fetch_prompt, sr.parse_prompt_url, self.ctx_mod.resolve)
        sr.fetch_prompt = lambda *_a, **_k: bundle
        sr.parse_prompt_url = lambda url: url
        self.ctx_mod.resolve = lambda *_a, **_k: context
        try:
            args = argparse.Namespace(prompt_url="p", version=None, mode=mode, scenario="s", context="paste",
                                      no_context=False, dataset_id=None, user_id=None, api="chat",
                                      provider=None, state_file=None, layout=layout)
            return sr._build_state(args, "flex")
        finally:
            sr.fetch_prompt, sr.parse_prompt_url, self.ctx_mod.resolve = orig

    def test_dataset_replay_renders_production_order_for_both_shapes(self):
        want = [("system" if r == "developer" and c != TIME else r, c) for r, c in PRODUCTION]
        for template in (DELIMITED, APPENDED):
            for inp in (LOGGED_INPUT, PLAIN_INPUT):
                with self.subTest(shape=rt.template_shape(template), logged=inp is LOGGED_INPUT):
                    state = self._state(template, "dataset", inp)
                    got = [(r if c != TIME else "developer", c) for r, c in _pairs(state["messages"])]
                    self.assertEqual(want, got)
                    self.assertEqual(rt.template_shape(template), state["template_shape"])

    def test_interactive_gets_no_time_message(self):
        state = self._state(APPENDED, "interactive", LOGGED_INPUT)
        self.assertEqual([("system", BODY), ("system", "---"), ("user", "---"), ("system", CTX)],
                         _pairs(state["messages"]))

    def test_template_layout_keeps_the_render(self):
        state = self._state(APPENDED, "interactive", [], layout="template")
        self.assertEqual(1, len(state["messages"]))
        self.assertTrue(state["messages"][0]["content"].rstrip().endswith(CTX))


class SendTurnTranslatorTests(unittest.TestCase):
    """The rendered production layout survives every provider's translator."""

    @classmethod
    def setUpClass(cls):
        stubbed = importlib.util.find_spec("openai") is None  # CI installs only requests + pillow
        if stubbed:
            stub = types.ModuleType("openai")
            stub.OpenAI = object
            stub.OpenAIError = Exception
            sys.modules["openai"] = stub
        try:
            cls.st = _load("bt_shapes_send_turn", QA / "send_turn.py")
        finally:
            if stubbed:  # never leak the stub into later test modules
                sys.modules.pop("openai", None)

    def _messages(self):
        rendered = rt.render_messages(DELIMITED, {"input": copy.deepcopy(LOGGED_INPUT), "metadata": METADATA})[0]
        return rt.production_layout(rendered, METADATA, instruction_role="system")

    def test_responses_keeps_the_logged_developer_time_message(self):
        items = self.st._to_responses_input(self._messages())
        self.assertEqual(("developer", TIME), (items[-1]["role"], items[-1]["content"][0]["text"]))

    def test_gemini_and_anthropic_get_no_leading_user_delimiter(self):
        contents, system = self.st._to_gemini_contents(self._messages())
        self.assertEqual("hi", contents[0]["parts"][0]["text"])
        self.assertIn(TIME, system)
        msgs, system = self.st._to_anthropic_messages(self._messages())
        self.assertEqual("hi", msgs[0]["content"][0]["text"])
        self.assertIn(TIME, system)


class DraftPromptCandidateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dpc = _load(
            "bt_shapes_draft_prompt_candidate",
            REPO_ROOT / "plugins/product/skills/draft-prompt-candidate/scripts/draft_prompt_candidate.py")

    def test_the_scaffold_follows_the_base_shape(self):
        new = BODY + "\n\nBe brief.\n"
        self.assertEqual(new, self.dpc.with_scaffold(DELIMITED[0]["content"], new))
        self.assertEqual(BODY + "\n\nBe brief.\n\n---\n\n" + TAG + "\n",
                         self.dpc.with_scaffold(APPENDED[0]["content"], new))
        self.assertEqual(APPENDED[0]["content"], self.dpc.with_scaffold(APPENDED[0]["content"],
                                                                        APPENDED[0]["content"]))


if __name__ == "__main__":
    unittest.main()
