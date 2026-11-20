"""Guards on the offline gate and its recorded baselines.

The gate's value is not that it computes a rate — it is that it refuses the
comparisons the rubric forbids. Every test here is one of those refusals.
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
SKILL = REPO / "plugins/eng/skills/qa-conversation"
GATE = SKILL / "scripts/offline_gate.py"


def _load():
    spec = importlib.util.spec_from_file_location("offline_gate", GATE)
    m = importlib.util.module_from_spec(spec)
    sys.modules["offline_gate"] = m
    spec.loader.exec_module(m)
    return m


og = _load()
B = og.load_baselines()


def _run(args, results=None):
    with tempfile.TemporaryDirectory() as td:
        argv = list(args)
        if results is not None:
            p = pathlib.Path(td, "r.json"); p.write_text(json.dumps(results))
            argv.append(str(p))
        return subprocess.run([sys.executable, str(GATE)] + argv,
                              capture_output=True, text=True)


class Baselines(unittest.TestCase):
    def test_the_locked_suite_is_five_datasets_and_126_rows(self):
        self.assertEqual(len(B["datasets"]), 5)
        self.assertEqual(sum(int(d["rows"]) for d in B["datasets"].values()), 126)

    def test_judgement_counts_reflect_three_generations_except_repetition(self):
        """54 = 18x3, 48 = 16x3. Repetition's 60 rows are already 12 scenarios
        with five replicates, so they are scored once each — scoring them x3
        would treat replicates as independent evidence."""
        for key, d in B["datasets"].items():
            rows, j = int(d["rows"]), int(d["judgements"])
            if key == "repetition":
                self.assertEqual(j, rows)
            else:
                self.assertEqual(j, rows * 3, key)

    def test_the_baselines_are_dataset_replay_not_drive_data(self):
        """measured.md is multi-turn drive data. Scoring a replay against it
        compares across two rulers while looking entirely reasonable."""
        self.assertEqual(B["source"]["mode"], "dataset_replay")

    def test_every_baseline_carries_the_instrument_that_produced_it(self):
        for k in ("judge", "measured_on", "generations_per_row"):
            self.assertIn(k, B["source"], k)

    def test_the_baseline_arm_is_production_not_the_best_arm(self):
        self.assertEqual(B["baseline_arm"]["code"], "PROD-CHAT")


class Scorecard(unittest.TestCase):
    FULL = {"transitions": {"pass": 53, "total": 54},
            "frustration": {"pass": 36, "total": 48},
            "warmth": {"pass": 45, "total": 48},
            "repetition": {"pass": 55, "total": 60},
            "hard_conversations": {"pass": 40, "total": 48}}

    def test_it_never_prints_a_suite_total(self):
        out = _run(["score"], self.FULL).stdout
        self.assertIn("No suite total is printed", out)
        self.assertNotIn("258", out)

    def test_repetition_is_reported_separately(self):
        out = _run(["score"], self.FULL).stdout
        self.assertIn("cohort-replicated", out)

    def test_a_small_delta_reads_as_unmoved(self):
        """+1.8pp on a 54-judgement dataset is one row moving."""
        r = og.score_dataset("transitions", B["datasets"]["transitions"],
                             {"pass": 53, "total": 54}, 5.0)
        self.assertEqual(r["verdict"], "unmoved")

    def test_two_regressions_are_a_concern_and_one_is_not(self):
        rows = [{"dataset": "a", "verdict": "REGRESSED"}, {"dataset": "b", "verdict": "unmoved"}]
        self.assertEqual(og.verdict(rows)[0], "WATCH")
        rows[1]["verdict"] = "REGRESSED"
        self.assertEqual(og.verdict(rows)[0], "CONCERN")

    def test_a_missing_dataset_is_not_measured_never_a_pass(self):
        out = _run(["score"], {"transitions": {"pass": 53, "total": 54}}).stdout
        self.assertIn("NOT MEASURED", out)
        self.assertIn("frustration", out)

    def test_a_wrong_denominator_is_flagged(self):
        """18 judgements instead of 54 means one generation per row, not three —
        a rate over the wrong n is not comparable to the baseline."""
        out = _run(["score"], {"transitions": {"pass": 17, "total": 18}}).stdout
        self.assertIn("DENOMINATOR MISMATCH", out)

    def test_uncertain_labels_are_not_passes(self):
        out = _run(["score"], {"transitions": {"pass": 50, "total": 54, "uncertain": 3}}).stdout
        self.assertIn("NOT counted as passes", out)


class ClassRouting(unittest.TestCase):
    def test_every_change_class_has_a_dataset_scope(self):
        self.assertEqual(set(og.class_datasets(og.load_contract())),
                         {"safety", "correctness", "behavioural", "model/config"})

    def test_correctness_runs_no_rubric_datasets(self):
        """A format artifact is not a quality question."""
        self.assertEqual(og.class_datasets(og.load_contract())["correctness"], "none")
        out = _run(["plan", "--change-class", "correctness"]).stdout
        self.assertIn("runs no rubric datasets", out)

    def test_an_unknown_class_is_refused(self):
        r = _run(["plan", "--change-class", "behavioral"])
        self.assertEqual(r.returncode, 2 if r.returncode == 2 else 1)


class GateExit(unittest.TestCase):
    def test_concern_exits_nonzero_and_clear_exits_zero(self):
        bad = {"frustration": {"pass": 30, "total": 48},
               "hard_conversations": {"pass": 38, "total": 48}}
        self.assertEqual(_run(["gate"], bad).returncode, 1)
        good = {"transitions": {"pass": 53, "total": 54}}
        self.assertEqual(_run(["gate"], good).returncode, 0)


if __name__ == "__main__":
    unittest.main()
