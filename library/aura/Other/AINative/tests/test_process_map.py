#!/usr/bin/env python3
"""The nine-stage process, mapped onto the loop.

The mapping's only job is to leave nothing unassigned. A stage with no leg and no
named gate is a stage that quietly stops happening the week nobody remembers it,
and the four judgments the process document reserves for a human are exactly the
ones a leg would be most tempted to assume.
"""
from __future__ import annotations

import pathlib
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SKILLS = ROOT / "plugins" / "product" / "skills"
OWNER = SKILLS / "prompt-knowledge"
SCRIPT = OWNER / "scripts" / "prompt_knowledge.py"

sys.path.insert(0, str(OWNER / "scripts"))
import prompt_knowledge as pk  # noqa: E402

GATES = {"Gate 1", "Gate 2"}
MODES = {"automated", "assisted", "human"}


class CoverageTests(unittest.TestCase):

    def setUp(self):
        self.proc = pk.load_process()
        self.stages = self.proc["stages"]

    def test_all_nine_stages_are_mapped(self):
        self.assertEqual(list(self.stages), [f"stage_{i}" for i in range(1, 10)])

    def test_every_stage_is_assigned_to_a_leg_or_named_as_a_human_gate(self):
        """The card's done-when, as an assertion."""
        for key, spec in self.stages.items():
            assigned = bool(spec["legs"]) or spec["mode"] == "human"
            self.assertTrue(assigned, f"{key} is assigned to nothing")
            self.assertIn(spec["gate"], GATES, key)

    def test_a_stage_with_no_legs_is_declared_human(self):
        for key, spec in self.stages.items():
            if not spec["legs"]:
                self.assertEqual(spec["mode"], "human", key)

    def test_modes_are_from_the_declared_vocabulary(self):
        for key, spec in self.stages.items():
            self.assertIn(spec["mode"], MODES, key)

    def test_every_stage_says_what_stays_human(self):
        """Including the automated ones. `automated` means a leg runs it, never
        that nobody reads the result."""
        for key, spec in self.stages.items():
            self.assertGreater(len(spec["what_stays_human"]), 40, key)

    def test_every_named_leg_exists_as_a_skill(self):
        for key, spec in self.stages.items():
            for leg in spec["legs"]:
                self.assertTrue((SKILLS / leg / "SKILL.md").exists(),
                                f"{key} names {leg}, which is not a skill")

    def test_the_roadmap_stage_is_honest_about_having_no_leg(self):
        """The loop ships one change against one defect. A phased roadmap is the
        declared exception to the weekly cadence, not the ordinary case."""
        stage2 = self.stages["stage_2"]
        self.assertEqual(stage2["legs"], [])
        self.assertEqual(stage2["mode"], "human")

    def test_the_testing_stages_are_the_automated_ones(self):
        for key in ("stage_6", "stage_7"):
            self.assertEqual(self.stages[key]["mode"], "automated", key)

    def test_diagnosis_is_not_automated(self):
        """The hardest diagnostic skill in the process, and the one the loop most
        conspicuously cannot do."""
        self.assertEqual(self.stages["stage_4"]["mode"], "human")


class HumanJudgmentTests(unittest.TestCase):

    def setUp(self):
        self.proc = pk.load_process()
        self.human = self.proc["human_judgment"]

    def test_the_four_carve_outs_are_all_carried(self):
        self.assertEqual(set(self.human), {
            "architectural_judgment", "final_editorial_authority",
            "clinical_and_relational_quality", "model_fit_calibration"})

    def test_each_names_its_stage_its_reason_and_its_gate(self):
        for name, h in self.human.items():
            self.assertIn(h["stage"], self.proc["stages"], name)
            self.assertGreater(len(h["why_not_automated"]), 40, name)
            self.assertTrue(any(g in h["where_it_sits"] for g in GATES), name)

    def test_no_carve_out_sits_on_a_stage_the_map_calls_automated(self):
        """A stage cannot both be run by a leg and reserve the judgment that
        stage is about — that contradiction is how a leg ends up assuming one."""
        for name, h in self.human.items():
            self.assertNotEqual(self.proc["stages"][h["stage"]]["mode"], "automated",
                                f"{name} sits on an automated stage")

    def test_pace_is_carried_verbatim_enough_to_survive_the_weekly_target(self):
        pace = self.proc["pace"]
        self.assertIn("two weeks or more", pace)
        self.assertIn("not a licence to accelerate past diagnosis", pace)


class RegisterLinkTests(unittest.TestCase):
    """The card's other done-when: the two findings on the hub page are hard
    stops in the register, not opinions to re-litigate."""

    def test_question_rate_is_a_hard_stop(self):
        e = pk.load_register()["entries"]["REG-1"]
        self.assertEqual(e["verdict"], "hard_stop")
        self.assertIn("forward motion", e["title"])

    def test_the_global_instruction_finding_is_a_hard_stop(self):
        e = pk.load_register()["entries"]["REG-2"]
        self.assertEqual(e["verdict"], "hard_stop")
        self.assertIn("100% of turns", e["finding"])

    def test_model_fit_is_reachable_from_the_process_map(self):
        stage = pk.load_process()["human_judgment"]["model_fit_calibration"]["stage"]
        self.assertIn("REG-4", pk.load_process()["stages"][stage]["note"])


class TinyReaderTests(unittest.TestCase):
    """CI has no PyYAML, so CI is the only place the fallback reader runs."""

    def setUp(self):
        self.text = (OWNER / "references" / "process_map.yaml").read_text()

    def test_a_quoted_key_reads_the_same_as_an_unquoted_one(self):
        """`"1"` and `1` naming different entries depending on which reader ran is
        a divergence that only shows up in CI."""
        got = pk._tiny_yaml('a:\n  "1": x\n  2: y\n')
        self.assertEqual(got["a"], {"1": "x", 2: "y"})

    def test_an_empty_inline_list_is_a_list(self):
        self.assertEqual(pk._tiny_yaml(self.text)["stages"]["stage_2"]["legs"], [])

    def test_matches_pyyaml(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML absent — this is the CI path")
        self.assertEqual(pk._tiny_yaml(self.text), yaml.safe_load(self.text))


class CommandTests(unittest.TestCase):

    def _run(self, *args):
        return subprocess.run([sys.executable, str(SCRIPT), *args],
                              capture_output=True, text=True, timeout=60)

    def test_process_prints_every_stage_and_every_carve_out(self):
        r = self._run("process")
        self.assertEqual(r.returncode, 0, r.stderr)
        for name in ("Product alignment", "Roadmap generation", "Multi-turn testing",
                     "Publication and monitoring"):
            self.assertIn(name, r.stdout)
        self.assertIn("JUDGMENT NO LEG MAY ASSUME", r.stdout)
        self.assertIn("architectural judgment", r.stdout)

    def test_a_human_stage_is_marked_as_one(self):
        self.assertIn("HUMAN", self._run("process").stdout)

    def test_one_stage_in_full(self):
        r = self._run("process", "--stage", "4")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Failure diagnosis", r.stdout)
        self.assertIn("Local versus structural", r.stdout)

    def test_an_unknown_stage_is_rejected(self):
        self.assertNotEqual(self._run("process", "--stage", "12").returncode, 0)


def main():
    unittest.main(verbosity=2)


if __name__ == "__main__":
    main()
