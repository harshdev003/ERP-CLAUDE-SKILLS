#!/usr/bin/env python3
"""The prompt audit must compare tool *parameters*, not just tool names.

A guard that answers "are the names the same?" cannot answer "is this the same
tool?". `handoffToGuardrails` gained a required `category` parameter under an
unchanged name; production picked it up, Braintrust's copy did not, and nothing
in the repo could see the difference.

Four separate faults had to line up for that to be invisible, and each is
pinned below because each on its own is enough to re-blind the audit:

1. `_normalize_tools` reduced the tools field to a sorted list of names.
2. The `config↔prod` tool comparison the SKILL.md table advertises was never
   implemented -- tools appeared in the comparison only prod↔dev.
3. `_parse_prompter_block` read config.js line by line, so a multi-line
   `tools: [` array coerced to the string `"["` and every tool name was
   dropped. The repo side of a tool comparison was therefore empty even once
   the comparison existed.
4. `main()` required `.git` to be a directory, which rejects every git
   worktree -- the way this org works.

No network: Braintrust prompt objects are represented as the literal shapes the
API returns (verified against `Coach [Production]` and the two shared
`handoffToGuardrails` function objects on 2026-09-23).
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile
import textwrap
import unittest

_HERE = pathlib.Path(__file__).resolve().parent
_SCRIPTS = _HERE.parent / "plugins/eng/skills/audit-intelligence-prompts/scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import audit  # noqa: E402


class ParamFingerprint(unittest.TestCase):
    """What counts as a difference, and what is deliberately ignored."""

    def test_added_parameter_is_drift(self):
        before = {"type": "object", "properties": {}}
        after = {
            "type": "object",
            "properties": {"category": {"type": "string"}},
            "required": ["category"],
        }
        self.assertNotEqual(
            audit._param_fingerprint(before), audit._param_fingerprint(after)
        )

    def test_requiredness_change_alone_is_drift(self):
        a = {"type": "object", "properties": {"x": {}}, "required": []}
        b = {"type": "object", "properties": {"x": {}}, "required": ["x"]}
        self.assertNotEqual(audit._param_fingerprint(a), audit._param_fingerprint(b))

    def test_braintrust_only_keys_are_not_drift(self):
        # BT stores `$schema` and `additionalProperties`; the repo JSON does
        # not. Comparing literally would make every tool report false drift,
        # which is the fastest way to get a check switched off.
        repo = {"type": "object", "properties": {"category": {}}, "required": ["category"]}
        bt = {
            "type": "object",
            "$schema": "http://json-schema.org/draft-07/schema#",
            "properties": {"category": {}},
            "required": ["category"],
            "additionalProperties": False,
        }
        self.assertEqual(audit._param_fingerprint(repo), audit._param_fingerprint(bt))

    def test_description_text_is_not_drift(self):
        # BT often carries no descriptions at all. Treating prose as drift
        # would bury the structural change that matters.
        a = {"type": "object", "properties": {"category": {"description": "one"}}}
        b = {"type": "object", "properties": {"category": {"description": "two"}}}
        self.assertEqual(audit._param_fingerprint(a), audit._param_fingerprint(b))


class ToolParamDrift(unittest.TestCase):
    def test_reports_the_tool_that_differs(self):
        left = {"handoffToGuardrails": {"type": "object", "properties": {"category": {}}}}
        right = {"handoffToGuardrails": {"type": "object", "properties": {}}}
        drift = audit._tool_param_drift(left, right)
        self.assertIn("handoffToGuardrails", drift)

    def test_identical_tools_report_nothing(self):
        same = {"t": {"type": "object", "properties": {"a": {}}, "required": ["a"]}}
        self.assertEqual(audit._tool_param_drift(same, dict(same)), {})

    def test_a_tool_only_one_side_declares_is_not_param_drift(self):
        # That is a NAME difference, already reported by the normalized name
        # lists. Counting it twice makes the report read as two problems.
        drift = audit._tool_param_drift({"a": {"properties": {}}}, {})
        self.assertEqual(drift, {})


class ToolTextDrift(unittest.TestCase):
    """The text the model reads is compared too (ENG-1574).

    The fingerprint above ignores descriptions on purpose, which is how the
    Braintrust `handoffToGuardrails` kept a one-line description while the repo
    told the model `category` is telemetry. Braintrust runs triggered 3 times in
    5 where production triggered 23 in 25. Strings below are the real
    2026-10-15 repo and Braintrust values.
    """

    REPO_DESC = ("Handoff to guardrails prompt for generating response in a crisis situation. Report which "
                 "guardrail condition matched via `category` so the routing decision is auditable; the category "
                 "is telemetry only and never reaches the user.")
    BT_DESC = "Handoff to guardrails prompt for generating response in a crisis situation."

    def _params(self, limit_desc):
        return {"findSimilarInMemory": {"type": "object", "properties": {
            "limit": {"type": "integer", "description": limit_desc}, "recall": {"type": "string"}}}}

    def test_tool_description_change_is_reported_with_the_differing_part(self):
        drift = audit._tool_text_drift({"handoffToGuardrails": {}}, {"handoffToGuardrails": {}},
                                       {"handoffToGuardrails": self.REPO_DESC}, {"handoffToGuardrails": self.BT_DESC})
        left, right = drift["handoffToGuardrails"]
        self.assertIn("Report which guardrail", left["description"], "the excerpt must show the change")
        self.assertTrue(right["description"].endswith("a crisis situation."))

    def test_a_description_missing_on_one_side_is_drift(self):
        drift = audit._tool_text_drift({"handoffToGuardrails": {}}, {"handoffToGuardrails": {}},
                                       {"handoffToGuardrails": self.REPO_DESC}, {})
        self.assertEqual((None,), (drift["handoffToGuardrails"][1]["description"],))

    def test_property_description_change_is_reported(self):
        drift = audit._tool_text_drift(
            self._params("Optional maximum number of memories to return. Use 10 for memory comparison."),
            self._params("The maximum number of memories to return. Use 10 for memory comparison."), {}, {})
        self.assertEqual(["limit.description"], list(drift["findSimilarInMemory"][0]))

    def test_whitespace_and_storage_keys_are_not_drift(self):
        left = self._params("Max  memories.")
        right = self._params("Max memories.")
        right["findSimilarInMemory"]["$schema"] = "http://json-schema.org/draft-07/schema#"
        right["findSimilarInMemory"]["additionalProperties"] = False
        self.assertEqual({}, audit._tool_text_drift(left, right, {"t": "a  b"}, {"t": "a b"}))

    def test_repo_descriptions_come_from_the_bundled_tool_json(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "src", "tools"))
            with open(os.path.join(d, "src", "tools", "handoffToGuardrails.json"), "w") as fh:
                json.dump({"type": "function", "function": {"name": "handoffToGuardrails",
                                                            "description": self.REPO_DESC, "parameters": {}}}, fh)
            self.assertEqual({"handoffToGuardrails": self.REPO_DESC},
                             audit._repo_tool_descriptions(d, ["handoffToGuardrails", "notOnDisk"]))

    def test_inline_tool_descriptions_are_read(self):
        inline = json.dumps([{"type": "function", "function": {"name": "t", "description": "d", "parameters": {}}}])
        self.assertEqual({"t": "d"}, audit._tool_descriptions(inline))


class ConfigParsesMultilineToolArrays(unittest.TestCase):
    """Fault 3: the repo side of the comparison was silently empty."""

    CONFIG = textwrap.dedent("""\
        const config = {
          prompt: {
            coach: {
              key: 'coach',
              model: 'chat-latest',
              name: 'coach-prompter',
              tools: [
                'findSimilarInMemory',
                'handoffToGuardrails',
                'storeInMemory',
              ],
              temperature: 1,
            },
            feed: {
              key: 'feed',
              name: 'feed-prompter',
              tools: ['recommendTracks'],
            },
          },
        };
        """)

    def parse(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "src"))
            with open(os.path.join(d, "src", "config.js"), "w") as fh:
                fh.write(self.CONFIG)
            return audit.parse_config_prompters(d)

    def test_multiline_array_parses_to_a_list(self):
        got = self.parse()["coach"]["tools"]
        self.assertEqual(
            got, ["findSimilarInMemory", "handoffToGuardrails", "storeInMemory"]
        )

    def test_multiline_array_is_not_the_string_bracket(self):
        # The exact old failure: `tools: [` coerced to "[", so the repo
        # declared no tools and every comparison against it was vacuous.
        self.assertNotEqual(self.parse()["coach"]["tools"], "[")

    def test_single_line_array_still_parses(self):
        self.assertEqual(self.parse()["feed"]["tools"], ["recommendTracks"])

    def test_keys_after_the_array_are_not_swallowed(self):
        # The join must stop at the closing bracket, or following keys vanish.
        self.assertEqual(self.parse()["coach"]["temperature"], 1)


class OneSidedToolsAreReported(unittest.TestCase):
    """A tool on only one side must not read as clean on the config↔prod axis.

    `_tool_param_drift` inspects only tools both sides declare, on the basis
    that a name difference is reported elsewhere. That holds for prod↔dev,
    which has its own name comparison. It did NOT hold for config↔prod, which
    had none — so a tool added to or removed from config.js was invisible,
    which is the same "documented check that never runs" shape this whole
    change exists to close.
    """

    def test_param_drift_alone_does_not_cover_a_one_sided_tool(self):
        # The gap, stated directly: this helper is silent by design, so the
        # caller owes a name comparison beside it.
        self.assertEqual(
            audit._tool_param_drift({"onlyInRepo": {"properties": {}}}, {}), {}
        )

    def test_config_prod_compares_names(self):
        source = (_SCRIPTS / "audit.py").read_text()
        self.assertIn('cfg_drift["tools"] = (cfg_tool_names, prod.tools_normalized)', source)


class RepoToolParams(unittest.TestCase):
    def test_reads_parameters_from_the_bundled_tool_json(self):
        # Production sends these schemas (`toolManager.getSchema()`), and
        # config.js lists only names -- so the repo side must come from here.
        with tempfile.TemporaryDirectory() as d:
            tools = os.path.join(d, "src", "tools")
            os.makedirs(tools)
            with open(os.path.join(tools, "handoffToGuardrails.json"), "w") as fh:
                json.dump(
                    {
                        "function": {
                            "name": "handoffToGuardrails",
                            "parameters": {
                                "type": "object",
                                "properties": {"category": {"type": "string"}},
                                "required": ["category"],
                            },
                        }
                    },
                    fh,
                )
            got = audit._repo_tool_params(d, ["handoffToGuardrails", "notOnDisk"])
        self.assertEqual(audit._param_fingerprint(got["handoffToGuardrails"]),
                         (("category",), ("category",)))
        self.assertNotIn("notOnDisk", got)

    def test_a_string_tools_value_yields_nothing_rather_than_crashing(self):
        # Defensive: before fault 3 was fixed, `tools` arrived as "[".
        self.assertEqual(audit._repo_tool_params("/nonexistent", "["), {})


class WorktreeIsAValidRepo(unittest.TestCase):
    def test_dot_git_file_is_accepted(self):
        # In a git worktree `.git` is a FILE. Requiring a directory rejected
        # every worktree, which is this org's standard working mode.
        source = (_SCRIPTS / "audit.py").read_text()
        self.assertIn('os.path.exists(os.path.join(args.repo, ".git"))', source)
        self.assertNotIn('os.path.isdir(os.path.join(args.repo, ".git"))', source)


if __name__ == "__main__":
    unittest.main()
