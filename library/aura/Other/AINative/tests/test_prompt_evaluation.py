"""Guards on the draft and evaluate legs.

Both legs are mostly bookkeeping around someone else's work, so what is worth
pinning is the contract: which class needs which evidence, and the two places a
leg must refuse rather than proceed.
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]


def _load(name: str, rel: str):
    path = REPO / rel
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    sys.path.insert(0, str(path.parent))
    spec.loader.exec_module(module)
    return module


epc = _load("evaluate_prompt_candidate",
            "plugins/product/skills/evaluate-prompt-candidate/scripts/evaluate_prompt_candidate.py")
dpc = _load("draft_prompt_candidate",
            "plugins/product/skills/draft-prompt-candidate/scripts/draft_prompt_candidate.py")


class EvidenceByClass(unittest.TestCase):
    """The requirements are no longer held here — they are read from
    `references/release_contract.yaml`, the file `eng:qa-conversation` gates on.
    These assertions are unchanged; only where the answer comes from moved."""

    CLASSES = ("safety", "correctness", "behavioural", "model/config")

    def test_every_class_in_the_lifecycle_has_requirements(self):
        for klass in self.CLASSES:
            self.assertIsNotNone(epc.release_contract.requirements(klass), klass)

    def test_an_unclassified_candidate_resolves_to_nothing(self):
        """`plan` says so rather than guessing, which is why the class is declared
        at Gate 1 and not at evaluation time."""
        self.assertIsNone(epc.release_contract.requirements(""))

    def test_safety_suite_runs_for_every_class(self):
        """A behavioural edit has already broken the Tier 1 crisis path once, in a
        path that was reliable and untouched by the change."""
        for klass in self.CLASSES:
            self.assertTrue(epc.release_contract.requirements(klass)["safety_suite"], klass)

    def test_only_the_arm_routed_classes_need_drives(self):
        self.assertIsNone(epc.release_contract.requirements("correctness")["drives"])
        self.assertIsNone(epc.release_contract.requirements("safety")["drives"])
        for klass in ("behavioural", "model/config"):
            self.assertIn("3 drives", epc.release_contract.requirements(klass)["drives"])

    def test_correctness_does_not_drag_in_the_rubric(self):
        """A format artifact is not a quality question."""
        self.assertIsNone(epc.release_contract.requirements("correctness")["locked_suite"])


class LockedSuite(unittest.TestCase):
    def test_carries_all_five_datasets(self):
        self.assertEqual(len(epc.LOCKED_SUITE), 5)

    def test_repetition_is_the_cohort_dataset(self):
        """60 rows as 12 scenarios x 5 replicates — pooling them with the others
        would treat replicates as independent cases."""
        name, rows = epc.LOCKED_SUITE["repetition"]
        self.assertEqual(rows, 60)

    def test_suite_totals_126_rows(self):
        self.assertEqual(sum(rows for _n, rows in epc.LOCKED_SUITE.values()), 126)


class ReportingContract(unittest.TestCase):
    def test_states_that_the_rubric_is_not_yet_a_gate(self):
        joined = " ".join(epc.release_contract.reporting_contract())
        self.assertIn("READ, not a gate", joined)

    def test_forbids_a_single_blended_number(self):
        joined = " ".join(epc.release_contract.reporting_contract())
        self.assertIn("Never one unweighted pass rate", joined)


class Transitions(unittest.TestCase):
    def test_only_approved_candidates_can_be_drafted(self):
        """Drafting before Gate 1 spends editorial effort on a diagnosis nobody
        has agreed to."""
        self.assertEqual(dpc.DRAFTABLE, {"approved"})

    def test_only_drafted_candidates_can_be_evaluated(self):
        self.assertEqual(epc.EVALUATABLE, {"drafted"})



class ScorecardStorage(unittest.TestCase):
    """`record` used to write `merged[:1900]` and still move the row to
    `evaluated` — so a real Safety Suite scorecard was cut, and a second append
    discarded the new evidence entirely, with no warning."""

    def test_chunking_loses_nothing(self):
        text = "x" * 5000
        value = epc._chunked_rich_text(text)
        kept = "".join(i["text"]["content"] for i in value["rich_text"])
        self.assertEqual(kept, text)

    def test_no_item_exceeds_the_notion_limit(self):
        value = epc._chunked_rich_text("y" * 7777)
        for item in value["rich_text"]:
            self.assertLessEqual(len(item["text"]["content"]), 2000)

    def test_empty_text_still_produces_a_valid_value(self):
        value = epc._chunked_rich_text("")
        self.assertEqual(len(value["rich_text"]), 1)

    def test_there_is_a_ceiling_rather_than_a_silent_cut(self):
        self.assertEqual(epc.MAX_RESULTS, epc.RICH_TEXT_ITEM * epc.MAX_ITEMS)


class DraftPreservesTheBase(unittest.TestCase):
    """Rebuilding prompt_data from messages and options alone dropped the tools,
    so the version the evals and Gate 2 saw was a different prompt."""

    BASE = {"prompt_data": {
        "options": {"model": "gpt-5.4"},
        "origin": {"prompt_id": "abc", "prompt_version": "1"},
        "tool_functions": [{"type": "function", "id": "t1"}, {"type": "function", "id": "t2"}],
        "prompt": {"type": "chat", "tools": "[...]",
                   "messages": [{"role": "system", "content": "old"}]},
    }}

    def test_tools_and_options_survive(self):
        out = dpc.build_prompt_data(self.BASE, [{"role": "system", "content": "new"}])
        self.assertEqual(len(out["tool_functions"]), 2)
        self.assertEqual(out["prompt"]["tools"], "[...]")
        self.assertEqual(out["options"]["model"], "gpt-5.4")

    def test_messages_are_replaced(self):
        out = dpc.build_prompt_data(self.BASE, [{"role": "system", "content": "new"}])
        self.assertEqual(out["prompt"]["messages"][0]["content"], "new")

    def test_origin_is_dropped_so_provenance_is_not_misattributed(self):
        out = dpc.build_prompt_data(self.BASE, [{"role": "system", "content": "new"}])
        self.assertNotIn("origin", out)

    def test_the_base_is_not_mutated(self):
        dpc.build_prompt_data(self.BASE, [{"role": "system", "content": "new"}])
        self.assertEqual(
            self.BASE["prompt_data"]["prompt"]["messages"][0]["content"], "old")
class EvidenceAppends(unittest.TestCase):
    """The skill documents that a second run adds evidence rather than replacing
    it. With only `drafted` accepted, the first `--confirm` moved the row to
    `evaluated` and every later run was refused — so the append path could never
    execute and the first scorecard was final."""

    def test_record_accepts_a_row_that_already_has_evidence(self):
        self.assertIn("evaluated", epc.RECORDABLE)
        self.assertIn("drafted", epc.RECORDABLE)

    def test_ready_still_lists_only_the_ones_awaiting_first_evidence(self):
        self.assertEqual(epc.EVALUATABLE, {"drafted"})

    def test_record_never_accepts_a_shipped_candidate(self):
        """Evidence attaches before Gate 2, not after users have it."""
        for status in ("released", "experiment_running", "won", "rolled_back"):
            self.assertNotIn(status, epc.RECORDABLE, status)


class PromptRefTests(unittest.TestCase):
    def test_the_display_form_yields_the_prompt_id(self):
        uid = "92e70c71-b9d0-473f-8b1c-be74a48e7cc5"
        self.assertEqual(uid, epc.prompt_ref(f"coach-conv-9 · {uid}"))
        self.assertEqual("coach-conv-9", epc.prompt_ref("coach-conv-9"))
        self.assertEqual("", epc.prompt_ref(""))

    def test_anything_unresolvable_is_empty_not_a_broken_ref(self):
        # The one hand-written value in the database: three arms, no prompt id.
        self.assertEqual("", epc.prompt_ref("A 1000197806534492368 · B 1000197807955707273 · C 1000197808050471272"))


class SourceCasesPlan(unittest.TestCase):
    """`plan` names the source-cases recipe by prompter, with the candidate's own dataset name."""

    def _plan(self, klass, prompter):
        import contextlib, io
        row = {"id": "p", "properties": {
            "Title": {"type": "title", "title": [{"plain_text": "t"}]},
            "Candidate ID": {"type": "unique_id", "unique_id": {"prefix": "CONV", "number": 9}},
            "Change class": {"type": "select", "select": {"name": klass}},
            "Prompter": {"type": "select", "select": {"name": prompter}},
            "Status": {"type": "select", "select": {"name": "drafted"}},
            "Braintrust version": {"type": "rich_text", "rich_text": [
                {"plain_text": "coach-conv-9 · 92e70c71-b9d0-473f-8b1c-be74a48e7cc5"}]}}}
        orig = epc.resolve
        epc.resolve = lambda ds, ref: row
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                epc.cmd_plan(type("A", (), {"data_source_id": "x", "ref": "CONV-9"})())
        finally:
            epc.resolve = orig
        return buf.getvalue()

    def test_a_behavioural_coach_change_builds_from_ratings_and_is_judged(self):
        out = self._plan("behavioural", "coach")
        self.assertIn("Source cases", out)
        self.assertIn("investigate-user-rating", out)
        self.assertIn("--scorer llm-judge", out)
        self.assertIn("CONV-9 source cases v0.1", out)
        # The field's display form is `{slug} · {prompt_id}`; only the id resolves.
        self.assertIn("--prompt cand=92e70c71-b9d0-473f-8b1c-be74a48e7cc5 ", out)
        self.assertNotIn("·", out.split("--prompt cand=")[1].split()[0])

    def test_a_safety_change_builds_from_alerts_and_scores_the_tool_call(self):
        out = self._plan("safety", "coach")
        self.assertIn("analyze-conversation-guardrails", out)
        self.assertIn("--scorer tool-call", out)

    def test_a_correctness_change_asks_for_none(self):
        self.assertNotIn("Source cases", self._plan("correctness", "coach"))

    def test_another_prompter_gets_no_borrowed_coach_recipe(self):
        for klass, prompter in (("behavioural", "quickProcessingDone"), ("safety", "guardrailsHandler")):
            out = self._plan(klass, prompter)
            self.assertNotIn("prod=coach-production", out, prompter)
            self.assertNotIn("handoffToGuardrails", out, prompter)
            self.assertIn(f"no bundled recipe for `{prompter}`", out)


if __name__ == "__main__":
    unittest.main()
