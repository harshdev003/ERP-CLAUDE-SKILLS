#!/usr/bin/env python3
"""The release contract is one file, read by two plugins, and it must stay one.

The table it holds — which class ships to everyone, which gates on the Safety
Suite, which takes drives — used to exist three times: in the offline gate, in
the product planner, and in prose on the loop page. Three copies of a release
rule disagree eventually, and the disagreement surfaces at Gate 2 as an argument
rather than as an error.

So the tests here are mostly about the copies agreeing. The interesting failure
is not "the parser broke" — it is "someone changed one file and shipped".
"""
from __future__ import annotations

import pathlib
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
ENG = ROOT / "plugins" / "eng" / "skills" / "qa-conversation"
PRODUCT = ROOT / "plugins" / "product" / "skills" / "evaluate-prompt-candidate"
ENG_YAML = ENG / "references" / "release_contract.yaml"
PRODUCT_YAML = PRODUCT / "references" / "release_contract.yaml"

sys.path.insert(0, str(ENG / "scripts"))
sys.path.insert(0, str(PRODUCT / "scripts"))
import offline_gate  # noqa: E402
import release_contract  # noqa: E402

CLASSES = {"safety", "correctness", "behavioural", "model/config"}


class MirrorTests(unittest.TestCase):
    """The product copy is a copy, not a fork."""

    def test_both_copies_exist(self):
        self.assertTrue(ENG_YAML.exists())
        self.assertTrue(PRODUCT_YAML.exists())

    def test_copies_are_byte_identical(self):
        self.assertEqual(
            ENG_YAML.read_bytes(), PRODUCT_YAML.read_bytes(),
            "release_contract.yaml has drifted between eng:qa-conversation and "
            "product:evaluate-prompt-candidate. Copy one over the other; do not "
            "reconcile them by hand.")

    def test_every_copy_is_byte_identical(self):
        """All copies, found rather than listed: the share skill's copy was a third
        one this class never checked."""
        copies = sorted(ROOT.glob("plugins/*/skills/*/references/release_contract.yaml"))
        self.assertGreaterEqual(len(copies), 3)
        for c in copies:
            self.assertEqual(ENG_YAML.read_bytes(), c.read_bytes(), str(c))
        readers = sorted(ROOT.glob("plugins/*/skills/*/scripts/release_contract.py"))
        self.assertGreaterEqual(len(readers), 2)
        for r in readers[1:]:
            self.assertEqual(readers[0].read_bytes(), r.read_bytes(), str(r))

    def test_both_readers_agree(self):
        self.assertEqual(offline_gate.load_contract(), release_contract.load())


class ContractShapeTests(unittest.TestCase):

    def setUp(self):
        self.c = offline_gate.load_contract()

    def test_every_class_present(self):
        self.assertEqual(set(self.c["classes"]), CLASSES)

    def test_every_class_declares_a_ship_route(self):
        for name, spec in self.c["classes"].items():
            self.assertIn(spec["ships"], {"everyone", "experiment_arm"}, name)

    def test_safety_suite_runs_for_every_class(self):
        """Including a pure tone change: breakage appears where the pressure was
        not applied, and a behavioural edit broke the crisis path once."""
        for name, spec in self.c["classes"].items():
            self.assertTrue(spec["safety_suite"]["required"], name)

    def test_blocking_suites_take_three_draws(self):
        """A blocking comparison built on one draw per arm has measured the draw:
        32 to 48 rows flip between draws of an identical prompt."""
        for name, spec in self.c["classes"].items():
            if spec["safety_suite"].get("blocking"):
                self.assertEqual(spec["safety_suite"]["draws"], 3, name)

    def test_experiment_arm_classes_take_drives(self):
        for name, spec in self.c["classes"].items():
            if spec["ships"] == "experiment_arm":
                self.assertTrue(spec["multi_turn"]["required"], name)
                self.assertGreaterEqual(spec["multi_turn"]["drives_per_arm"], 3, name)

    def test_safety_ships_to_everyone_and_blocks(self):
        safety = self.c["classes"]["safety"]
        self.assertEqual(safety["ships"], "everyone")
        self.assertTrue(safety["safety_suite"]["blocking"])

    def test_targeted_classes_require_source_cases_and_never_block_on_them(self):
        for name, spec in self.c["classes"].items():
            self.assertIn("source_cases", spec, name)
            self.assertNotIn("blocking", spec["source_cases"], name)
        self.assertTrue(self.c["classes"]["safety"]["source_cases"]["required"])
        self.assertTrue(self.c["classes"]["behavioural"]["source_cases"]["required"])
        self.assertFalse(self.c["classes"]["correctness"]["source_cases"]["required"])
        self.assertFalse(self.c["classes"]["model/config"]["source_cases"]["required"])
        self.assertGreater(len(self.c["source_cases_why"]), 40)

    def test_correctness_does_not_spend_the_rubric(self):
        self.assertEqual(self.c["classes"]["correctness"]["locked_suite"], "none")

    def test_every_locked_scope_has_a_label(self):
        labels = self.c["locked_suite_labels"]
        for name, spec in self.c["classes"].items():
            self.assertIn(spec["locked_suite"], labels, name)

    def test_reasons_survive_the_reader(self):
        """Folded scalars carry every WHY in the file. A reader that drops them
        turns the contract into a table of unexplained numbers."""
        for key in ("safety_suite_universal_why", "three_draws_why",
                    "unreviewed_rows_may_not_gate", "product_priorities_note"):
            self.assertIsInstance(self.c[key], str, key)
            self.assertGreater(len(self.c[key]), 40, key)

    def test_priorities_are_report_order_not_weights(self):
        self.assertEqual(self.c["product_priorities"][0], "aurie_voice")
        self.assertIn("not coefficients", self.c["product_priorities_note"])

    def test_advisory_rules_parse_as_a_list(self):
        rules = self.c["resolution"]["advisory_rules"]
        self.assertIsInstance(rules, list)
        self.assertGreaterEqual(len(rules), 4)
        self.assertTrue(all(isinstance(r, str) and r for r in rules))

    def test_reporting_contract_parses_as_a_list(self):
        lines = self.c["reporting_contract"]
        self.assertIsInstance(lines, list)
        self.assertEqual(len(lines), 5)
        self.assertTrue(any("READ, not a gate" in line for line in lines))


class SingleOwnerTests(unittest.TestCase):
    """One number, one file. The materiality threshold moved here from the
    baselines store; both files holding it is how they disagree."""

    def test_materiality_lives_in_the_contract(self):
        self.assertEqual(
            float(offline_gate.load_contract()["resolution"]["material_change_pp"]),
            5.0)

    def test_baselines_no_longer_define_materiality(self):
        b = offline_gate.load_baselines()
        self.assertNotIn("material_change_pp", b["interpretation"])
        self.assertIn("material_change_pp_owned_by", b["interpretation"])

    def test_gate_reads_the_contract_for_the_class_map(self):
        mapping = offline_gate.class_datasets(offline_gate.load_contract())
        self.assertEqual(mapping["model/config"], "all")
        self.assertEqual(mapping["correctness"], "none")

    def test_no_hardcoded_class_map_left_in_the_gate(self):
        src = (ENG / "scripts" / "offline_gate.py").read_text()
        self.assertNotIn("CLASS_DATASETS = {", src)


class ProductRenderingTests(unittest.TestCase):
    """The product planner renders the same rules it used to hold."""

    def test_unknown_class_returns_none(self):
        self.assertIsNone(release_contract.requirements("not-a-class"))
        self.assertIsNone(release_contract.requirements(""))

    def test_safety_renders_blocking_three_draws(self):
        req = release_contract.requirements("safety")
        self.assertIn("3 draws", req["safety_suite"])
        self.assertIn("BLOCKING", req["safety_suite"])
        self.assertEqual(req["locked_suite"], "targeted rows only")
        self.assertIsNone(req["drives"])

    def test_correctness_asks_for_no_locked_suite(self):
        req = release_contract.requirements("correctness")
        self.assertIsNone(req["locked_suite"])
        self.assertIsNone(req["drives"])
        self.assertNotIn("BLOCKING", req["safety_suite"])

    def test_behavioural_asks_for_drives(self):
        req = release_contract.requirements("behavioural")
        self.assertIn("3 drives", req["drives"])
        self.assertEqual(req["ships"], "experiment_arm")

    def test_source_cases_render_only_where_required(self):
        self.assertIn("prompted it", release_contract.requirements("behavioural")["source_cases"])
        self.assertIsNone(release_contract.requirements("correctness")["source_cases"])

    def test_model_config_runs_the_whole_suite(self):
        req = release_contract.requirements("model/config")
        self.assertEqual(req["locked_suite_scope"], "all")
        self.assertEqual(req["locked_suite"], "all five datasets")


class TinyReaderTests(unittest.TestCase):
    """CI has no PyYAML, so CI is the only place the fallback reader runs.

    Every developer machine here has PyYAML, which means the reader that actually
    parses the contract in CI is the one nobody ever exercises by hand. It shipped
    once returning the string "false" for a boolean — truthy, so every class read
    as blocking and every class demanded drives.
    """

    def setUp(self):
        self.text = ENG_YAML.read_text()
        self.tiny = offline_gate._tiny_yaml(self.text)

    def test_booleans_are_booleans(self):
        for name, spec in self.tiny["classes"].items():
            for flag in (spec["safety_suite"].get("blocking"),
                         spec["multi_turn"]["required"]):
                self.assertIsInstance(flag, (bool, type(None)), name)

    def test_a_false_flag_is_falsey(self):
        self.assertFalse(self.tiny["classes"]["correctness"]["safety_suite"]["blocking"])
        self.assertFalse(self.tiny["classes"]["safety"]["multi_turn"]["required"])

    def test_block_lists_survive(self):
        self.assertIsInstance(self.tiny["resolution"]["advisory_rules"], list)
        self.assertEqual(len(self.tiny["reporting_contract"]), 5)

    def test_folded_scalars_survive(self):
        why = self.tiny["three_draws_why"]
        self.assertIsInstance(why, str)
        self.assertIn("32 to 48 rows flip", why)

    def test_numbers_are_numbers(self):
        self.assertEqual(self.tiny["resolution"]["material_change_pp"], 5.0)
        self.assertEqual(self.tiny["classes"]["safety"]["safety_suite"]["draws"], 3)

    def test_matches_pyyaml_where_pyyaml_exists(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML absent — this is the CI path, already covered above")
        self.assertEqual(self.tiny, yaml.safe_load(self.text))

    def test_matches_pyyaml_on_the_baselines_too(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML absent")
        text = (ENG / "references" / "baselines.dataset_replay.yaml").read_text()
        self.assertEqual(offline_gate._tiny_yaml(text), yaml.safe_load(text))

    def test_both_plugins_ship_the_same_reader(self):
        """The product copy is a copy of the reader as well as of the file."""
        self.assertEqual(release_contract._tiny_yaml(self.text), self.tiny)


class DocTableTests(unittest.TestCase):
    """The table in the product SKILL.md is a rendering of the contract.

    It is what a reading agent sees first and what it will act on, so a table
    that has drifted from the file is worse than no table: the skill would run
    one rule and describe another.
    """

    def setUp(self):
        text = (PRODUCT / "SKILL.md").read_text()
        self.rows = {}
        for line in text.splitlines():
            if not line.startswith("| `"):
                continue
            cells = [c.strip() for c in line.strip("|").split("|")]
            if len(cells) == 5 and cells[0].strip("`") in CLASSES:
                self.rows[cells[0].strip("`")] = cells[1:]
        self.c = offline_gate.load_contract()

    def test_table_lists_every_class(self):
        self.assertEqual(set(self.rows), CLASSES)

    def test_blocking_column_matches_the_contract(self):
        for name, (suite, _, _, _) in self.rows.items():
            blocking = bool(self.c["classes"][name]["safety_suite"].get("blocking"))
            self.assertEqual("blocking" in suite.lower(), blocking, name)

    def test_draw_counts_match_the_contract(self):
        for name, (suite, _, _, _) in self.rows.items():
            draws = self.c["classes"][name]["safety_suite"]["draws"]
            if draws > 1:
                self.assertIn(f"{draws} draws", suite, name)

    def test_locked_column_is_empty_exactly_when_the_scope_is_none(self):
        for name, (_, locked, _, _) in self.rows.items():
            scope = self.c["classes"][name]["locked_suite"]
            self.assertEqual(locked in ("—", "-", ""), scope == "none", name)

    def test_drives_column_matches_the_contract(self):
        for name, (_, _, drives, _) in self.rows.items():
            mt = self.c["classes"][name]["multi_turn"]
            self.assertEqual(drives not in ("—", "-", ""), bool(mt.get("required")), name)
            if mt.get("required"):
                self.assertIn(str(mt["drives_per_arm"]), drives, name)

    def test_source_cases_column_matches_the_contract(self):
        for name, (_, _, _, cases) in self.rows.items():
            required = bool(self.c["classes"][name]["source_cases"].get("required"))
            self.assertEqual(cases not in ("—", "-", ""), required, name)
            self.assertNotIn("block", cases.lower(), name)


class CommandTests(unittest.TestCase):

    def _run(self, *args):
        return subprocess.run(
            [sys.executable, str(ENG / "scripts" / "offline_gate.py"), *args],
            capture_output=True, text=True, timeout=60)

    def test_contract_command_lists_every_class(self):
        r = self._run("contract")
        self.assertEqual(r.returncode, 0, r.stderr)
        for name in CLASSES:
            self.assertIn(name, r.stdout)

    def test_contract_command_states_what_blocks(self):
        r = self._run("contract", "--change-class", "safety")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("BLOCKS the release", r.stdout)
        self.assertIn("everyone", r.stdout)

    def test_contract_command_rejects_an_unknown_class(self):
        r = self._run("contract", "--change-class", "vibes")
        self.assertNotEqual(r.returncode, 0)

    def test_plan_reports_the_ship_route_and_the_suite(self):
        r = self._run("plan", "--change-class", "behavioural")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("experiment_arm", r.stdout)
        self.assertIn("3 drives per arm", r.stdout)
        self.assertIn("targeted_plus_adjacent", r.stdout)

    def test_plan_rejects_an_unknown_class(self):
        r = self._run("plan", "--change-class", "vibes")
        self.assertNotEqual(r.returncode, 0)


def main():
    unittest.main(verbosity=2)


if __name__ == "__main__":
    main()
