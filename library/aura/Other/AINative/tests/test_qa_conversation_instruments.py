"""Guards on the qa-conversation detector scripts.

Each test corresponds to a defect the 2026-08-21 calibration run found the hard
way — by producing a number that looked fine and was not.
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
SKILL = REPO / "plugins/eng/skills/qa-conversation"
SCRIPTS = SKILL / "scripts"

TRANSCRIPTS = {
    "c1|A": [["User", "hi"], ["Aurie", "Yeah. Rest or resolution?"], ["Aurie", "Okay."]],
    "c2|A": [["User", "hi"], ["Aurie", '**bold** {"x": 1} and Tier 2'],
             ["Aurie", "How are you? And what now?"]],
    "c1|U": [["User", "hi"], ["Aurie", "That sounds heavy."], ["Aurie", "What would help?"]],
}


def run_counters(env_extra=None):
    with tempfile.TemporaryDirectory() as td:
        src = pathlib.Path(td, "t.json"); src.write_text(json.dumps(TRANSCRIPTS))
        out = pathlib.Path(td, "per.json")
        env = {**os.environ, "COUNTERS_PER_CONV": str(out), **(env_extra or {})}
        r = subprocess.run([sys.executable, str(SCRIPTS / "counters.py"), str(src)],
                           capture_output=True, text=True, env=env)
        per = json.loads(out.read_text()) if out.exists() else None
        return r, per


class CountersPerConversation(unittest.TestCase):
    """counters.py emitted only pooled aggregates, so the calibration had to
    re-implement F4, F9 and F10 to get a spread — and the re-implementations then
    disagreed with the validated detectors."""

    @classmethod
    def setUpClass(cls):
        cls.result, cls.per = run_counters()

    def test_it_emits_one_row_per_conversation(self):
        self.assertIsNotNone(self.per)
        self.assertEqual(len(self.per), len(TRANSCRIPTS))

    def test_pooled_artifacts_equal_the_sum_of_per_conversation_artifacts(self):
        """The whole point: one set of detectors, two views. If these diverge, a
        re-implementation has crept back in."""
        pooled = int([l for l in self.result.stdout.splitlines()
                      if l.strip().startswith("prod")][-1].split()[-1])
        summed = sum(r["F10_artifact_hits"] for r in self.per if r["arm"] == "A")
        self.assertEqual(pooled, summed)

    def test_every_row_carries_its_arm_and_conversation(self):
        for r in self.per:
            self.assertTrue(r["arm"])
            self.assertTrue(r["conversation"])

    def test_it_stays_silent_when_not_asked(self):
        """The emit is opt-in; a run without the env var must not write a file."""
        with tempfile.TemporaryDirectory() as td:
            src = pathlib.Path(td, "t.json"); src.write_text(json.dumps(TRANSCRIPTS))
            env = {k: v for k, v in os.environ.items() if k != "COUNTERS_PER_CONV"}
            r = subprocess.run([sys.executable, str(SCRIPTS / "counters.py"), str(src)],
                               capture_output=True, text=True, env=env)
            self.assertEqual(r.returncode, 0)
            self.assertNotIn("per-conversation rows", r.stdout)


class MoveTypeRefusesSilently(unittest.TestCase):
    """label_movetype.py filters on stall-probe id shapes. Production ids are
    UUIDs, so every one was skipped — and because the controls file always
    matches, the run still printed a populated table with the arm absent."""

    def _run(self, convs):
        with tempfile.TemporaryDirectory() as td:
            src = pathlib.Path(td, "c.json"); src.write_text(json.dumps(convs))
            return subprocess.run(
                [sys.executable, str(SCRIPTS / "label_movetype.py"),
                 str(SKILL / "references/controls/probe_controls.json"), f"prod={src}"],
                capture_output=True, text=True)

    def test_a_uuid_keyed_source_is_refused_not_skipped(self):
        r = self._run({"3e375e23-af20-81f7-afc6-ddbdd0605463":
                       [{"role": "user", "content": "x"}, {"role": "assistant", "content": "y"}]})
        self.assertEqual(r.returncode, 1)
        out = r.stdout + r.stderr
        self.assertIn("matched no stall probes in: prod", out)

    def test_the_refusal_names_the_ids_it_saw(self):
        r = self._run({"aae4c9c6-1111-2222-3333-444455556666":
                       [{"role": "user", "content": "x"}, {"role": "assistant", "content": "y"}]})
        self.assertIn("aae4c9c6", r.stdout + r.stderr)


class MoveTypeProbeCoverage(unittest.TestCase):
    """The id filter enumerated H1/H2/H3/P1, so a run over the official stall sets
    scored 4 of 12 probes — A12 measured on a third of its denominator with no
    sign anything was missing. Matching the families fixes it; the digit keeps
    help-seeking probes out, since those carry a different denominator."""

    PROBES = SKILL / "references/probes"
    CONTROLS = SKILL / "references/controls"

    def _ids(self, path):
        return list(json.loads(path.read_text()))

    def _matched(self, path):
        import re
        rx = re.compile(r"^(?:[HP]\d+-|CTRL-(?:stall|topichop|kindchange|amplify))")
        return [i for i in self._ids(path) if rx.match(i)]

    def test_every_stall_probe_is_matched(self):
        for name in ("stall_amplification_v1.json", "stall_amplification_hard_v1.json"):
            p = self.PROBES / name
            self.assertEqual(len(self._matched(p)), len(self._ids(p)), name)

    def test_the_loader_keeps_every_probe_control(self):
        """Asserting the regex against the FILE was the wrong test: it stayed green
        while the controls loader carried its own narrower filter and dropped
        CTRL-amplify-P4/P5 before they ever reached it. This runs the script and
        reads the coverage it reports, so the loader is what is under test."""
        controls = self.CONTROLS / "probe_controls.json"
        expected = len(self._ids(controls))
        with tempfile.TemporaryDirectory() as td:
            src = pathlib.Path(td, "p.json")
            src.write_text(json.dumps({"P1-x": [{"role": "user", "content": "a"},
                                                {"role": "assistant", "content": "b"}]}))
            env = {k: v for k, v in os.environ.items() if k != "OPENAI_EVALUATIONS_API_KEY"}
            r = subprocess.run(
                [sys.executable, str(SCRIPTS / "label_movetype.py"), str(controls), f"prod={src}"],
                capture_output=True, text=True, env=env)
        self.assertIn(f"ctrl: {expected} of {expected} conversations are stall probes",
                      r.stdout + r.stderr)

    def test_help_seeking_probes_are_not_swept_in(self):
        """HS1-* must not match H\\d+-. A different probe set with a different
        denominator — trading an under-count for a wrong count is not a fix."""
        self.assertEqual(self._matched(self.PROBES / "help_seeking_v1.json"), [])

    def test_two_stage_controls_are_not_swept_in(self):
        self.assertEqual(self._matched(self.CONTROLS / "perturbation_controls.json"), [])

    def test_the_script_uses_a_family_pattern_not_an_id_list(self):
        src = (SCRIPTS / "label_movetype.py").read_text()
        self.assertIn("STALL_RE", src)
        self.assertNotIn('STALL=("H1","H2","H3","P1"', src)


class TwoStageCoversV2(unittest.TestCase):
    """SKILL.md called label_two_stage.py 'the per-turn defect labeler for V2/V3
    and R5'. It looped over V3 and R5 only — so V2, a GATE, had no labeler, and
    its CTRL-dismissive positive controls sat in the controls file unloaded."""

    @classmethod
    def setUpClass(cls):
        cls.src = (SCRIPTS / "label_two_stage.py").read_text()
        cls.controls = json.loads(
            (SKILL / "references/controls/perturbation_controls.json").read_text())

    def test_v2_is_in_the_default_row_set(self):
        self.assertIn('"V2,V3,R5"', self.src)

    def test_v2_has_a_stage_one_detector(self):
        self.assertIn("V2_SENSITIVE", self.src)
        self.assertIn('elif row=="V2"', self.src)

    def test_v2_has_a_stage_two_exclusion(self):
        self.assertIn('"V2":"""', self.src)

    def test_the_dismissive_controls_are_loaded(self):
        self.assertIn("dismissive", self.src)
        self.assertTrue([k for k in self.controls if "dismissive" in k])

    def test_each_row_is_validated_against_its_own_positive_control(self):
        """Scoring V2 against the judgmental controls would validate it on the
        wrong defect and call the result a pass."""
        self.assertIn('POSITIVE={"V2":"dismissive"', self.src)

    def test_arms_are_not_hardcoded(self):
        self.assertNotIn('in ("A","U")', self.src)


if __name__ == "__main__":
    unittest.main()
