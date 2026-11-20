"""eng:compare-prompt-versions scoring + data:save-to-braintrust-dataset bulk plan.

The summary is fed the records `score_one` actually produces (with the model call
stubbed at `run_row`), not hand-built dicts, so a field the producer renames
breaks here rather than in a readout that silently shows n/a.
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
import unittest

from reader_harness import REPO_ROOT

SCRIPTS = REPO_ROOT / "plugins/eng/skills/compare-prompt-versions/scripts"
sys.path.insert(0, str(SCRIPTS))

import scorers  # noqa: E402
import summarize  # noqa: E402


def _load(name: str, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


run_comparison = _load("run_comparison", SCRIPTS / "run_comparison.py")
save_row = _load("save_row", REPO_ROOT / "plugins/data/skills/save-to-braintrust-dataset/scripts/save_row.py")
btp = sys.modules["braintrust_prompt"]

TOOL = "handoffToGuardrails"
ENUM = ["crisis", "diagnosis", "treatment", "complex_condition", "substance", "boundary"]


def _call(category=None, name=TOOL):
    return {"name": name, "arguments": {"category": category} if category else {}}


class LabelTests(unittest.TestCase):
    def test_no_trigger_spellings(self):
        for raw in (None, False, "", "none", "No Trigger", "no-trigger", "must_not_route", "NO_TRIGGER"):
            with self.subTest(raw=raw):
                self.assertEqual((False, None), scorers.normalize_label(raw))

    def test_trigger_only_spellings(self):
        for raw in (True, "trigger", "must_route", "YES"):
            with self.subTest(raw=raw):
                self.assertEqual((True, None), scorers.normalize_label(raw))

    def test_a_value_is_folded(self):
        self.assertEqual((True, "complex_condition"), scorers.normalize_label("Complex Condition"))

    def test_non_string_label_is_an_error(self):
        with self.assertRaises(scorers.LabelError):
            scorers.normalize_label(3)

    def test_lookup_parses_json_string_expected(self):
        row = {"expected": '{"category": "crisis"}', "metadata": {"x": {"y": 1}}}
        self.assertEqual("crisis", scorers.lookup(row, "expected.category"))
        self.assertEqual(1, scorers.lookup(row, "metadata.x.y"))
        self.assertIsNone(scorers.lookup(row, "metadata.missing.deeper"))


class ToolCallScoreTests(unittest.TestCase):
    def score(self, calls, label):
        return scorers.score_tool_call(calls, label, TOOL, "category")

    def test_true_positive_right_category(self):
        s = self.score([_call("crisis")], "crisis")["scores"]
        self.assertEqual({"trigger_correct": 1.0, "exact": 1.0, "category_correct": 1.0}, s)

    def test_true_positive_wrong_category(self):
        s = self.score([_call("boundary")], "crisis")["scores"]
        self.assertEqual((1.0, 0.0, 0.0), (s["trigger_correct"], s["category_correct"], s["exact"]))

    def test_a_miss_is_not_counted_twice(self):
        """Category only applies when the model triggered; a miss is trigger_correct's job."""
        s = self.score([], "crisis")["scores"]
        self.assertEqual(0.0, s["trigger_correct"])
        self.assertIsNone(s["category_correct"])
        self.assertEqual(0.0, s["exact"])

    def test_false_alarm(self):
        r = self.score([_call("crisis")], "no_trigger")
        self.assertEqual(0.0, r["scores"]["trigger_correct"])
        self.assertIsNone(r["scores"]["category_correct"])

    def test_true_negative_ignores_other_tools(self):
        r = self.score([_call(name="searchTracks")], "no_trigger")
        self.assertEqual(1.0, r["scores"]["exact"])
        self.assertFalse(r["actual_trigger"])

    def test_trigger_only_label_scores_trigger_not_category(self):
        s = self.score([_call("boundary")], "must_route")["scores"]
        self.assertEqual((1.0, None, 1.0), (s["trigger_correct"], s["category_correct"], s["exact"]))


class JudgeParseTests(unittest.TestCase):
    def test_parses_json_inside_prose(self):
        self.assertEqual((1.0, "ok"), scorers.parse_judge('Sure: {"score": 1, "reason": "ok"}'))

    def test_unreadable_is_none_not_zero(self):
        for text in ("PASS", '{"score": "high"}', '{"score": 2}', ""):
            with self.subTest(text=text):
                self.assertIsNone(scorers.parse_judge(text)[0])


def _bundle():
    return btp.PromptBundle(
        prompt_id="p", slug="coach", name="Coach", project_id="x", project_name=None,
        model="m", provider="openai", params={}, messages=[{"role": "system", "content": "{{input}}"}],
        tools=[btp.ToolEntry(name=TOOL, description="", returns_schema=None, function_id=None,
                             parameters={"type": "object", "properties": {
                                 "category": {"type": "string", "enum": ENUM}}})],
        version="1", raw_response={},
    )


def _args(**kw):
    base = dict(scorer="tool-call", tool=TOOL, arg="category", label_field="expected.category",
                stub="{}", max_hops=1, rubric_file=None, judge_model="j", api="chat", params_override={}, tools_override=None, trials=1,
                group_field="metadata.issue")
    base.update(kw)
    return argparse.Namespace(**base)


def _row(rid, label, case=None):
    return {"id": rid, "input": [{"role": "user", "content": "hi"}],
            "expected": {"category": label}, "metadata": {"case_id": case or rid}}


class ProducerToSummaryTests(unittest.TestCase):
    """score_one → summarize, with only the model call replaced."""

    def setUp(self):
        self._orig = run_comparison.run_row

    def tearDown(self):
        run_comparison.run_row = self._orig

    def _run(self, outputs: dict[str, list]):
        def fake_run_row(client, bundle, row, tier, target, stub, hops, **_kw):
            calls = outputs[row["id"]]
            if calls == "boom":
                raise RuntimeError("model down")
            return {"text": "", "tool_calls": calls, "usage": {}, "tier": "flex"}
        run_comparison.run_row = fake_run_row
        rows = [_row("a", "crisis"), _row("b", "no_trigger"), _row("c", "substance"), _row("d", "crisis")]
        return [run_comparison.score_one(None, _bundle(), r, _args(), "flex", None) for r in rows]

    def test_metrics_from_real_records(self):
        recs = self._run({"a": [_call("crisis")], "b": [_call("crisis")], "c": [], "d": "boom"})
        m = summarize.tool_call_metrics(recs)
        self.assertEqual((1, 1, 1, 0), (m["tp"], m["fn"], m["fp"], m["tn"]))
        self.assertEqual(1, m["errors"], "an errored row must be counted, not vanish")
        self.assertEqual(["c"], m["missed"])
        self.assertEqual(["b"], m["false_alarms"])
        self.assertEqual(1.0, m["arg_accuracy"])
        self.assertEqual(0.5, m["recall"])
        self.assertEqual(1, m["confusion"]["substance|no_trigger"])

    def test_disagreements_and_new_misses(self):
        base = self._run({"a": [_call("crisis")], "b": [], "c": [_call("substance")], "d": [_call("crisis")]})
        new = self._run({"a": [_call("crisis")], "b": [], "c": [], "d": [_call("boundary")]})
        results = {"scorer": "tool-call", "versions": [
            {"label": "prod", "rows": base}, {"label": "new", "rows": new}]}
        s = summarize.summarize(results)
        self.assertEqual({"c", "d"}, {d["row_id"] for d in s["disagreements"]})
        md = summarize.render_markdown(s, "prod")
        self.assertIn("New missed triggers in new vs prod: 1** — c (0/1 → 1/1)", md)

    def test_label_outside_enum_errors_before_the_model_is_called(self):
        def must_not_run(*a, **k):
            raise AssertionError("model called for an unscorable label")
        run_comparison.run_row = must_not_run
        rec = run_comparison.score_one(None, _bundle(), _row("x", "route_or_safe"), _args(), "flex", None)
        self.assertIn("not a value of", rec["error"])


    def test_either_label_is_counted_not_scored(self):
        run_comparison.run_row = lambda *a, **k: {"text": "", "tool_calls": [_call("crisis")], "usage": {}, "tier": "flex"}
        rec = run_comparison.score_one(None, _bundle(), _row("e", "route_or_safe_in_conversation"), _args(), "flex", None)
        self.assertIsNone(rec["error"])
        self.assertTrue(rec["skip"])
        m = summarize.tool_call_metrics([rec])
        self.assertEqual((0, 0, 0, 0, 1, 1), (m["tp"], m["fn"], m["fp"], m["tn"], m["either"], m["either_triggered"]))


class GroupAndDiffTests(unittest.TestCase):
    """Per-issue readout, --params-json from a file, and the prompt diff."""

    def setUp(self):
        self._orig = (run_comparison.run_row, run_comparison.judge_row)

    def tearDown(self):
        run_comparison.run_row, run_comparison.judge_row = self._orig

    def test_judge_scores_break_down_by_issue(self):
        run_comparison.run_row = lambda *a, **k: {"text": "ok", "tool_calls": [], "usage": {}, "tier": "flex"}
        scores = {"a": 1.0, "b": 0.0, "c": 0.5}
        run_comparison.judge_row = lambda client, model, tier, row, out, rubric: (scores[row["id"]], "r")
        rows = [{"id": i, "input": [], "expected": "PASS if x", "metadata": {"case_id": i, "issue": iss}}
                for i, iss in (("a", "over-analysis"), ("b", "over-analysis"), ("c", "premature-closure"))]
        recs = [run_comparison.score_one(None, _bundle(), r, _args(scorer="llm-judge", label_field=None), "flex", None) for r in rows]
        s = summarize.summarize({"scorer": "llm-judge", "versions": [{"label": "prod", "rows": recs}]})
        self.assertEqual({"over-analysis": {"n": 2, "score": 0.5}, "premature-closure": {"n": 1, "score": 0.5}},
                         s["versions"][0]["groups"])
        self.assertIn("| over-analysis | 50.0% (n=2) |", summarize.render_markdown(s, "prod"))

    def test_rows_without_a_group_add_no_table(self):
        s = summarize.summarize({"scorer": "llm-judge", "versions": [{"label": "prod", "rows": [
            {"row_id": "a", "judge_score": 1.0, "group": None}]}]})
        self.assertNotIn("By group", summarize.render_markdown(s, "prod"))

    def test_params_json_accepts_a_file(self):
        import json, os, tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"top_p": 0.98}, f)
        argv = sys.argv
        try:
            sys.argv = ["x", "--prompt", "a=s", "--prompt", "b=t", "--dataset", "d", "--scorer", "llm-judge",
                        "--out", "o", "--params-json", f.name]
            self.assertEqual({"top_p": 0.98}, run_comparison._parse_args().params_override)
            sys.argv[-1] = '{"top_p": 0.5}'
            self.assertEqual({"top_p": 0.5}, run_comparison._parse_args().params_override)
        finally:
            sys.argv = argv
            os.unlink(f.name)

    def test_prompt_diff_shows_the_edit_and_the_unchanged_case(self):
        prompt_diff = _load("prompt_diff", SCRIPTS / "prompt_diff.py")
        base = _bundle()
        cand = _bundle()
        cand.messages = [{"role": "system", "content": "{{input}}\n## New section\nBe brief."}]
        cand.slug, cand.version = "coach-new", "2"
        md = prompt_diff.render([("prod", base), ("new", cand), ("same", _bundle())])
        self.assertIn("+## New section", md)
        self.assertIn("Lines added / removed: 2 / 0", md)
        self.assertIn("No change: identical to the baseline.", md)


class NewMissRateTests(unittest.TestCase):
    def test_rate_not_set(self):
        base = {"missed": ["a", "c"], "trigger_trials": {"a": 5, "b": 5, "c": 5}}
        cand = {"missed": ["a"] * 5 + ["b"], "trigger_trials": {"a": 5, "b": 5, "c": 5}}
        # a: 1/5 → 5/5 is a regression the set diff hid; b: 0/5 → 1/5 counts; c improved
        self.assertEqual(["a (1/5 → 5/5)", "b (0/5 → 1/5)"], summarize.new_missed_rows(base, cand))


class FakeResponsesClient:
    """Stands in for OpenAI at the single chokepoint `client.post('/responses')`."""

    def __init__(self, outputs):
        self.outputs, self.bodies = list(outputs), []

    def post(self, path, cast_to=None, body=None):
        assert path == "/responses"
        self.bodies.append(body)
        return {"output": self.outputs.pop(0), "usage": {"input_tokens": 10, "output_tokens": 2}}


class ResponsesApiTests(unittest.TestCase):
    def test_params_map_to_the_responses_shape(self):
        got = run_comparison._responses_params(
            {"reasoning_effort": "low", "verbosity": "medium", "max_tokens": 50, "frequency_penalty": 0,
             "tool_choice": "auto", "use_cache": True})
        self.assertEqual({"reasoning": {"effort": "low"}, "text": {"verbosity": "medium"},
                          "max_output_tokens": 50, "tool_choice": "auto"}, got)

    def test_override_merges_nested(self):
        self.assertEqual({"reasoning": {"effort": "medium", "context": "x"}, "top_p": 0.98},
                         run_comparison._merge({"reasoning": {"effort": "low"}},
                                               {"reasoning": {"effort": "medium", "context": "x"}, "top_p": 0.98}))

    def test_stub_round_trip_then_target_call(self):
        fc = lambda name, cid, args: {"type": "function_call", "name": name, "call_id": cid, "arguments": args}
        reasoning = {"type": "reasoning", "id": "rs_1", "encrypted_content": "opaque"}
        client = FakeResponsesClient([
            [reasoning, fc("searchTracks", "c1", "{}")],
            [fc(TOOL, "c2", '{"category": "crisis"}')],
        ])
        out = run_comparison.run_row(client, _bundle(), _row("r", "crisis"), "flex", TOOL, "{}", 2,
                                     api="responses", overrides={"top_p": 0.98})
        self.assertEqual([None, TOOL], [None if c["name"] == "searchTracks" else c["name"] for c in out["tool_calls"]])
        first, second = client.bodies
        self.assertEqual(0.98, first["top_p"])
        self.assertEqual("flex", first["service_tier"])
        self.assertFalse(first["store"])
        self.assertEqual({"type": "function_call_output", "call_id": "c1", "output": "{}"}, second["input"][-1])
        self.assertIn("reasoning.encrypted_content", first["include"])
        self.assertIn(reasoning, second["input"], "reasoning must go back on the next hop when store=false")
        self.assertEqual((20, 4), (out["usage"]["prompt_tokens"], out["usage"]["completion_tokens"]),
                         "usage is summed across both hops")


class TrialsAndToolsTests(unittest.TestCase):
    def test_majority_over_trials_decides_disagreement(self):
        r = lambda trig, arg=None: {"row_id": "x", "case_id": "x", "label": "crisis", "error": None,
                                    "actual_trigger": trig, "actual_arg": arg}
        base = [r(True, "crisis"), r(True, "crisis"), r(False)]
        same = [r(True, "crisis"), r(False), r(True, "crisis")]
        flipped = [r(False), r(False), r(True, "crisis")]
        self.assertEqual([], summarize.disagreements(
            [{"label": "a", "rows": base}, {"label": "b", "rows": same}], "tool-call"))
        d = summarize.disagreements([{"label": "a", "rows": base}, {"label": "b", "rows": flipped}], "tool-call")
        self.assertEqual(1, len(d))
        self.assertEqual("tie", summarize._majority([[True, "x"], [False, None]]))

    def test_tools_override_reaches_both_apis_and_the_enum(self):
        prod_tools = [{"type": "function", "name": TOOL, "strict": True, "description": "prod",
                       "parameters": {"type": "object", "properties": {"category": {"enum": ["crisis"]}}}}]
        self.assertEqual({"crisis"}, run_comparison.arg_enum(_bundle(), TOOL, "category", prod_tools))
        client = FakeResponsesClient([[]])
        run_comparison.run_row(client, _bundle(), _row("r", "crisis"), "flex", TOOL, "{}", 0,
                               api="responses", tools_override=prod_tools)
        self.assertEqual(prod_tools, client.bodies[0]["tools"])
        chat = run_comparison.chat_tools(prod_tools)
        self.assertEqual({"type": "function", "function": {"name": TOOL, "description": "prod", "strict": True,
                          "parameters": prod_tools[0]["parameters"]}}, chat[0])

    def test_trial_events_are_distinct_and_linked_to_one_record(self):
        row = _row("r1", "crisis")
        ids = {run_comparison._event(row, {"trial": t, "scores": {}}, _bundle(), 3)["id"] for t in range(3)}
        self.assertEqual(3, len(ids))
        self.assertEqual("r1", run_comparison._event(row, {"trial": 2, "scores": {}}, _bundle(), 3)["dataset_record_id"])
        self.assertEqual("r1", run_comparison._event(row, {"trial": 0, "scores": {}}, _bundle(), 1)["id"])


class PromptSpecTests(unittest.TestCase):
    def test_parse(self):
        p = run_comparison.parse_prompt_spec
        self.assertEqual(("prod", "coach-production", None), p("prod=coach-production"))
        self.assertEqual((None, "coach-production", "123"), p("coach-production@123"))
        url = "https://www.braintrust.dev/app/Aura%20Health%201/p/Conversation/prompts/3f716f75-4c8d-400c-a39b-8242ec9ca72a"
        self.assertEqual(("new", url, "9"), p(f"new={url}@9"))


class BulkPlanTests(unittest.TestCase):
    def _r(self, case, content="hi"):
        return {"input": [{"role": "user", "content": content}], "metadata": {"case_id": case}}

    def test_existing_rows_are_skipped_and_counted(self):
        plan = save_row.plan_bulk([self._r("a"), self._r("b")],
                                  [{"id": "row-1", "metadata": {"case_id": "a"}}], "metadata.case_id", False)
        self.assertEqual(([], ["a"], 1, 0), (plan["errors"], plan["skip"], len(plan["insert"]), len(plan["replace"])))

    def test_replace_reuses_the_existing_row_id(self):
        plan = save_row.plan_bulk([self._r("a")], [{"id": "row-1", "metadata": {"case_id": "a"}}],
                                  "metadata.case_id", True)
        self.assertEqual("row-1", plan["replace"][0]["id"])

    def test_a_file_row_id_never_reaches_the_insert(self):
        """Braintrust upserts on id; only the dedupe key may pick the row replaced."""
        row = {**self._r("new"), "id": "row-of-another-case"}
        existing = [{"id": "row-of-another-case", "metadata": {"case_id": "other"}}]
        plan = save_row.plan_bulk([row], existing, "metadata.case_id", True)
        self.assertNotIn("id", plan["insert"][0])
        self.assertEqual([], plan["replace"])

    def test_id_as_the_dedupe_key_still_matches(self):
        row = {**self._r("a"), "id": "row-1"}
        plan = save_row.plan_bulk([row], [{"id": "row-1", "metadata": {}}], "id", False)
        self.assertEqual((["row-1"], []), (plan["skip"], plan["insert"]))
        plan = save_row.plan_bulk([row], [{"id": "row-1", "metadata": {}}], "id", True)
        self.assertEqual("row-1", plan["replace"][0]["id"])

    def test_missing_and_repeated_keys_and_bad_input_are_errors(self):
        rows = [self._r("a"), self._r("a"), {"input": [{"role": "user", "content": "x"}], "metadata": {}},
                {"input": "text", "metadata": {"case_id": "z"}}]
        errors = save_row.plan_bulk(rows, [], "metadata.case_id", False)["errors"]
        self.assertEqual(3, len(errors), errors)


class ProductionLayoutTests(unittest.TestCase):
    """The rendered template is sent in Intelligence Service 1.40's layout."""

    def test_the_context_moves_out_of_the_system_message(self):
        md = {"contextInstructions": "## Context\n\nUser name: Alex.",
              "developerInstructions": "### Current Time\n\nIt is Tuesday."}
        rendered = [{"role": "system", "content": "## Role\n\nYou are Aurie.\n\n---\n\n## Context\n\nUser name: Alex.\n"},
                    {"role": "user", "content": "hi"}]
        out = run_comparison.production_layout(rendered, md)
        self.assertEqual([("system", "## Role\n\nYou are Aurie."), ("developer", "---"), ("user", "---"),
                          ("developer", md["contextInstructions"]), ("user", "hi"),
                          ("developer", md["developerInstructions"])],
                         [(m["role"], m["content"]) for m in out])

    def test_nothing_moves_without_a_trailing_context(self):
        rendered = [{"role": "system", "content": "prompt"}, {"role": "user", "content": "hi"}]
        self.assertEqual(rendered, run_comparison.production_layout(rendered, {}))
        self.assertEqual(rendered, run_comparison.production_layout(rendered, {"contextInstructions": "elsewhere"}))


if __name__ == "__main__":
    unittest.main()
