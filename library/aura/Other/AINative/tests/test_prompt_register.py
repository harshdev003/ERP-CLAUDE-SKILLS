#!/usr/bin/env python3
"""The rejected-hypothesis register: one file, three copies, and a refusal.

The register is the loop's answer to a prompt engineer leaving. Its whole value
is that a candidate restating a settled question is refused with the reason
quoted rather than re-argued from memory — so the tests that matter are that the
copies cannot drift, that the refusal actually fires on the phrasings people use,
and that it does not fire on everything.
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
MIRRORS = ["digest-conversation-signals", "record-prompt-candidates"]

sys.path.insert(0, str(OWNER / "scripts"))
import prompt_knowledge as pk  # noqa: E402

HARD_STOPS = {"REG-1", "REG-2", "REG-3"}


def _load_module(name: str, path: pathlib.Path):
    """Load one file as its own module, under its own name.

    Every mirror is called `register.py`; a plain import would serve the first
    one to every later caller.
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class MirrorTests(unittest.TestCase):

    def test_every_mirror_is_byte_identical(self):
        owner = (OWNER / "references" / "register.yaml").read_bytes()
        for skill in MIRRORS:
            copy = SKILLS / skill / "references" / "register.yaml"
            self.assertTrue(copy.exists(), f"{skill} has no register copy")
            self.assertEqual(
                copy.read_bytes(), owner,
                f"{skill}'s register.yaml has drifted from product:prompt-knowledge. "
                "Copy the owner over it; do not reconcile by hand.")

    def test_every_mirror_ships_a_reader(self):
        for skill in MIRRORS:
            self.assertTrue((SKILLS / skill / "scripts" / "register.py").exists(), skill)

    def test_mirrored_readers_return_the_same_entries(self):
        """Each mirror is loaded from its own file.

        Importing the name `register` and reloading it re-executes whichever copy
        landed in sys.modules first, so the second mirror is never read and a
        broken reader there passes — the failure this test exists to catch.
        """
        want = pk.load_register()["entries"]
        for skill in MIRRORS:
            path = SKILLS / skill / "scripts" / "register.py"
            mod = _load_module(f"register_{skill.replace('-', '_')}", path)
            self.assertEqual(mod.entries(), want, skill)
            self.assertEqual(mod.REGISTER.resolve(),
                             (SKILLS / skill / "references" / "register.yaml").resolve(),
                             f"{skill}'s reader is reading somebody else's file")


class RegisterShapeTests(unittest.TestCase):

    def setUp(self):
        self.entries = pk.load_register()["entries"]

    def test_the_settled_findings_are_all_present(self):
        self.assertEqual(set(self.entries),
                         {"REG-1", "REG-2", "REG-3", "REG-4", "REG-5", "REG-6"})

    def test_every_entry_carries_its_evidence(self):
        for eid, e in self.entries.items():
            for field in ("title", "verdict", "finding", "cost", "reopens_if", "source"):
                self.assertTrue(str(e.get(field) or "").strip(), f"{eid}.{field}")

    def test_an_entry_without_a_reopens_condition_is_a_prejudice(self):
        for eid, e in self.entries.items():
            self.assertGreater(len(e["reopens_if"]), 10, eid)

    def test_verdicts_are_from_the_declared_vocabulary(self):
        allowed = set(pk.load_register()["verdicts"])
        for eid, e in self.entries.items():
            self.assertIn(e["verdict"], allowed, eid)

    def test_only_hard_stops_refuse(self):
        for eid, e in self.entries.items():
            self.assertEqual(e["verdict"] in pk.REFUSING, eid in HARD_STOPS, eid)

    def test_every_entry_names_the_prompters_it_applies_to(self):
        for eid, e in self.entries.items():
            self.assertTrue(e.get("applies_to"), eid)

    def test_hard_stops_say_what_to_do_instead(self):
        """A refusal with no alternative gets overridden the first time it is
        inconvenient."""
        for eid in HARD_STOPS:
            self.assertTrue(self.entries[eid].get("instead"), eid)

    def test_the_measurements_survive_the_reader(self):
        self.assertIn("0 of 12 conversations", self.entries["REG-2"]["finding"])
        self.assertIn("thirty coach-prompter patch versions", self.entries["REG-1"]["cost"])


class MatchingTests(unittest.TestCase):
    """What the check catches, in the words people actually use."""

    def assert_hits(self, text, eid, prompter=None):
        hits = [h[0] for h in pk.matches(text, prompter)]
        self.assertIn(eid, hits, f"{text!r} did not match {eid} (matched {hits})")

    def assert_clean(self, text):
        self.assertEqual(pk.matches(text), [], text)

    def test_lowering_the_question_rate(self):
        for text in ("ask fewer questions per turn",
                     "we should reduce the number of questions",
                     "cap it at one question per response",
                     "bring the question rate down",
                     "stop asking so many questions"):
            self.assert_hits(text, "REG-1")

    def test_a_global_instruction(self):
        for text in ("add a global instruction to give more advice",
                     "always offer a concrete step in every conversation",
                     "add an exception list for grief"):
            self.assert_hits(text, "REG-2")

    def test_one_more_rule(self):
        for text in ("just add a rule telling it to be concrete",
                     "add a line that says to offer help",
                     "write more exceptions for the cases it misses"):
            self.assert_hits(text, "REG-3")

    def test_an_unexplained_behaviour_change_checks_the_model(self):
        self.assert_hits("aurie has started rambling since last week", "REG-4")

    def test_an_attitude_level_rule(self):
        for text in ("tell aurie to be less clinical",
                     "it should not sound preachy"):
            self.assert_hits(text, "REG-5")

    def test_an_ordinary_candidate_is_not_refused(self):
        """A check that fires on everything gets ignored, which costs more than
        one that fires on nothing."""
        for text in ("coach: hold scope when the user removes the referral",
                     "name the specific outputs to withhold in the money section",
                     "detect the switch from processing to asking and offer a handhold",
                     "fix the stray brace in the sentinel output"):
            self.assert_clean(text)

    def test_the_prompter_filter_narrows(self):
        """REG-1 does not apply to the guardrail classifiers, so the same words
        against that prompter are not a restatement of it."""
        self.assert_hits("ask fewer questions", "REG-1", "coach")
        self.assertEqual(pk.matches("ask fewer questions", "userGuardrail"), [])


class PrompterKnowledgeTests(unittest.TestCase):

    def setUp(self):
        self.know = pk.load_prompters()

    def test_every_prompter_in_scope_is_recorded(self):
        self.assertEqual(set(self.know["prompters"]), {
            "coach", "companion", "chat", "onboarding", "quickProcessing",
            "quickProcessingSentinel", "quickProcessingDone", "guardrailsHandler",
            "userGuardrail", "assistantGuardrail"})

    def test_coach_serves_both_surfaces(self):
        """Chat runs on the coach prompter in production; a coach change is never
        chat-only."""
        self.assertEqual(set(self.know["prompters"]["coach"]["serves"]), {"chat", "voice"})

    def test_chat_is_recorded_as_near_dormant_rather_than_dropped(self):
        self.assertEqual(self.know["prompters"]["chat"]["status"], "near_dormant")

    def test_every_prompter_records_a_config_and_a_prompt_file(self):
        for name, spec in self.know["prompters"].items():
            self.assertIn("model", spec["config"], name)
            self.assertTrue(spec["prompt_file"].endswith(".txt"), name)

    def test_the_dependency_order_is_the_process_documents(self):
        self.assertEqual(self.know["dependency_order"],
                         ["identity", "pacing", "tier_logic", "guardrails", "tone"])

    def test_section_layers_resolve_longest_match_first(self):
        """`Tier Progression & Governance` is tier logic, not pacing — `progression`
        is the longer accidental match and `tier progression` must win."""
        secs = pk.sections("## Tier Progression & Governance\n",
                           self.know["section_layers"])
        self.assertEqual(secs[0][2], "tier_logic")

    def test_headings_map_to_layers(self):
        text = "## Role\n### Voice & Naturalness\n## Guardrails\n## Tool Usage\n"
        got = {t: layer for _, t, layer in pk.sections(text, self.know["section_layers"])}
        self.assertEqual(got["Role"], "identity")
        self.assertEqual(got["Voice & Naturalness"], "tone")
        self.assertEqual(got["Guardrails"], "guardrails")
        self.assertEqual(got["Tool Usage"], "tools")


class ConfigReadingTests(unittest.TestCase):
    """Reading src/config.js with a regex, against a fixture shaped like it."""

    CONFIG = """
module.exports = {
  prompt: {
    coach: {
      key: 'coach',
      model: 'chat-latest',
      openaiAPI: 'responses',
      temperature: 1,
      tools: [
        'findSimilarInMemory',
        'storeInMemory',
      ],
    },
    guardrailsHandler: {
      key: 'guardrails',
      model: 'gpt-5.4',
      openaiAPI: 'responses',
      reasoningEffort: 'low',
      // tools: ['showCrisisModal'],
      verbosity: 'medium',
    },
  },
};
"""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = pathlib.Path(self.tmp.name)
        (self.repo / "src").mkdir()
        (self.repo / "src" / "config.js").write_text(self.CONFIG)

    def tearDown(self):
        self.tmp.cleanup()

    def test_reads_the_block(self):
        got = pk.live_config(self.repo, "coach")
        self.assertEqual(got["model"], "chat-latest")
        self.assertEqual(got["openaiAPI"], "responses")
        self.assertIsNone(got["reasoningEffort"])
        self.assertEqual(got["tools"], ["findSimilarInMemory", "storeInMemory"])

    def test_notices_a_commented_out_tool_line(self):
        """A candidate assuming that tool fires is proposing a code change too."""
        got = pk.live_config(self.repo, "guardrailsHandler")
        self.assertTrue(got["tools_commented_out"])
        self.assertEqual(got["tools"], [])

    def test_a_missing_prompter_reads_as_missing_not_as_empty(self):
        self.assertIsNone(pk.live_config(self.repo, "companion"))

    def test_a_missing_repo_reads_as_missing(self):
        self.assertIsNone(pk.live_config(pathlib.Path("/nonexistent"), "coach"))

    def test_drift_is_detected(self):
        drift = pk._diff_config({"model": "gpt-5.2", "openaiAPI": "responses"},
                                pk.live_config(self.repo, "coach"))
        self.assertEqual([d[0] for d in drift], ["model"])

    def test_a_tool_appearing_or_disappearing_is_drift(self):
        """Tools are part of the deployed configuration REG-4 points at: a tool
        moving changes behaviour with the prompt body untouched."""
        live = pk.live_config(self.repo, "coach")
        drift = pk._diff_config(live, live, ["findSimilarInMemory"])
        self.assertEqual([d[0] for d in drift], ["tools"])

    def test_matching_tools_are_not_drift(self):
        live = pk.live_config(self.repo, "coach")
        self.assertEqual(pk._diff_config(live, live, live["tools"]), [])

    def test_tools_are_only_compared_when_asked(self):
        """`brief` and `config-drift` both pass them; a caller that does not is
        comparing the scalar fields on purpose."""
        live = pk.live_config(self.repo, "coach")
        self.assertEqual(pk._diff_config(live, live), [])

    def test_no_drift_when_they_agree(self):
        live = pk.live_config(self.repo, "coach")
        self.assertEqual(pk._diff_config(live, live), [])


class TinyReaderTests(unittest.TestCase):
    """CI has no PyYAML, so CI is the only place the fallback reader runs."""

    def _tiny(self, name):
        return pk._tiny_yaml((OWNER / "references" / name).read_text())

    def test_regex_patterns_survive_intact(self):
        """A backslash mangled here silently stops the refusal from firing."""
        pats = self._tiny("register.yaml")["entries"]["REG-1"]["patterns"]
        self.assertTrue(any(p.startswith("ask") for p in pats))
        self.assertTrue(all("\\\\" not in p for p in pats), pats)

    def test_inline_maps_and_nulls(self):
        cfg = self._tiny("prompters.yaml")["prompters"]["coach"]["config"]
        self.assertEqual(cfg["model"], "chat-latest")
        self.assertIsNone(cfg["reasoningEffort"])

    def test_folded_scalars_survive(self):
        self.assertIn("0 of 12", self._tiny("register.yaml")["entries"]["REG-2"]["finding"])

    def test_matches_pyyaml(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML absent — this is the CI path")
        for name in ("register.yaml", "prompters.yaml"):
            text = (OWNER / "references" / name).read_text()
            self.assertEqual(pk._tiny_yaml(text), yaml.safe_load(text), name)


class CommandTests(unittest.TestCase):

    def _run(self, *args):
        return subprocess.run([sys.executable, str(SCRIPT), *args],
                              capture_output=True, text=True, timeout=60)

    def test_check_exits_nonzero_on_a_hard_stop_and_quotes_the_reason(self):
        r = self._run("check", "lower the question rate")
        self.assertEqual(r.returncode, 1)
        self.assertIn("REFUSED", r.stdout)
        self.assertIn("forward motion", r.stdout)
        self.assertIn("reopens", r.stdout)

    def test_check_exits_zero_on_a_rewrite(self):
        """A rewrite is not a refusal; conflating them makes the refusal cheap."""
        r = self._run("check", "tell aurie to be less clinical")
        self.assertEqual(r.returncode, 0)
        self.assertIn("REWRITE", r.stdout)

    def test_check_says_a_clean_result_is_not_a_clearance(self):
        r = self._run("check", "hold scope when the user removes the referral")
        self.assertEqual(r.returncode, 0)
        self.assertIn("not a clearance", r.stdout)

    def test_register_lists_every_entry(self):
        r = self._run("register")
        self.assertEqual(r.returncode, 0, r.stderr)
        for eid in ("REG-1", "REG-6"):
            self.assertIn(eid, r.stdout)

    def test_register_rejects_an_unknown_entry(self):
        self.assertNotEqual(self._run("register", "--entry", "REG-99").returncode, 0)

    def test_brief_works_without_the_repo(self):
        """It degrades to the snapshot and says so, rather than failing."""
        r = self._run("brief", "coach", "--is-repo", "/nonexistent")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Could not read", r.stdout)
        self.assertIn("REG-1", r.stdout)

    def test_brief_rejects_an_unknown_prompter(self):
        self.assertNotEqual(self._run("brief", "nope").returncode, 0)


def main():
    unittest.main(verbosity=2)


if __name__ == "__main__":
    main()
