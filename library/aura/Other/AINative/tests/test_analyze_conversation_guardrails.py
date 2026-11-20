#!/usr/bin/env python3
"""`data:analyze-conversation-guardrails` — the reason-line parser and the dataset rollup.

Two things decide whether the skill's numbers are right, and both fail silently:

* **The producer's line must parse.** `eng:investigate-conversation-guardrail` writes a
  `Trigger reason:` line on every triage reply; this skill reads it back from Slack. If the
  two drift, every triaged alert reads as unclassified and the counts quietly shrink. So the
  parser is fed the producer's own examples, every producer format must carry the line, and
  the two copies of the reason list must be byte-identical.
* **Each user counts once where the table says so.** Severity rows add up to the user total.

Stdlib unittest, no network, no keys.
"""
from __future__ import annotations

import pathlib
import re
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from reader_harness import REPO_ROOT, load_reader  # noqa: E402

SKILL = REPO_ROOT / "plugins/data/skills/analyze-conversation-guardrails"
CANONICAL = REPO_ROOT / "plugins/eng/skills/investigate-conversation-guardrail/references/trigger_reasons.md"
PRODUCER_SKILL = REPO_ROOT / "plugins/eng/skills/investigate-conversation-guardrail/SKILL.md"


def _sweep():
    return load_reader(SKILL / "scripts/sweep_alerts.py", siblings=())


def _rollup():
    return load_reader(SKILL / "scripts/rollup.py", siblings=())


class ReasonList(unittest.TestCase):
    def test_copies_are_byte_identical(self):
        copies = sorted(REPO_ROOT.glob("plugins/*/skills/*/references/trigger_reasons.md"))
        self.assertIn(CANONICAL, copies)
        self.assertGreaterEqual(len(copies), 2)
        for copy in copies:
            with self.subTest(copy=str(copy.relative_to(REPO_ROOT))):
                self.assertEqual(copy.read_bytes(), CANONICAL.read_bytes())

    def test_reasons_are_read_from_the_table(self):
        reasons = _rollup().load_reasons(str(CANONICAL))
        self.assertEqual(reasons[0], "suicidal-or-self-harm")
        self.assertIn("figure-of-speech", reasons)
        self.assertEqual(reasons[-1], "unknown")
        self.assertEqual(len(reasons), len(set(reasons)))


class ProducerLines(unittest.TestCase):
    def test_every_producer_example_parses_into_the_reason_list(self):
        mod, reasons = _sweep(), set(_rollup().load_reasons(str(CANONICAL)))
        examples = [l for l in CANONICAL.read_text(encoding="utf-8").splitlines()
                    if l.startswith("Trigger reason:") and "<" not in l]  # the grammar line is a template
        self.assertGreaterEqual(len(examples), 4)
        for line in examples:
            with self.subTest(line=line):
                _, parsed = mod.parse_reply(f":white_check_mark: no active risk — non-crisis guardrail\n{line}\n_footer_")
                self.assertIsNotNone(parsed)
                self.assertIn(parsed["reason"], reasons)

    def test_attributes_and_flag(self):
        sev, parsed = _sweep().parse_reply(
            ":rotating_light: active risk — review\nTrigger reason: medication · subject: self · asked for: clinical-advice · already in care")
        self.assertEqual(sev, "crisis")
        self.assertEqual(parsed, {"reason": "medication", "subject": "self", "asked_for": "clinical-advice", "in_care": True})

    def test_severity_from_the_verdict_line(self):
        mod = _sweep()
        self.assertEqual(mod.parse_reply(":white_check_mark: no active risk — false positive, no action needed.")[0], "false-positive")
        self.assertEqual(mod.parse_reply(":white_check_mark: no active risk — non-crisis guardrail, no action needed.")[0], "non-crisis")
        self.assertEqual(mod.parse_reply(":ballot_box_with_check: active risk — resources already in place")[0], "crisis")
        self.assertEqual(mod.parse_reply(":warning: *Conversation guardrail alert not analysable")[0], "unknown")

    def test_handoff_category_from_every_producer_format(self):
        mod = _sweep()
        text = PRODUCER_SKILL.read_text(encoding="utf-8")
        formats = text.split("**Formats.**", 1)[1].split("**Product-safety line.**", 1)[0]
        templates = [l for l in formats.splitlines() if "handoffs: #<n> <category>" in l]
        self.assertGreaterEqual(len(templates), 3)
        for t in templates:
            with self.subTest(template=t.strip()[:40]):
                filled = t.replace("#<n> <category>[, …]", "#4 treatment, #9 crisis")
                self.assertEqual(mod.parse_category(filled), "treatment")

    def test_triaged_alert_keeps_its_category(self):
        rows = _rollup().build([{"ts": "1", "user": "u", "conversation": "c", "category": "diagnosis",
                                 "reason_line": {"reason": "diagnosis", "subject": "self"}}], {}, {},
                               ["diagnosis"])
        self.assertEqual(rows[0]["category"], "diagnosis")

    def test_every_producer_format_carries_the_line(self):
        text = PRODUCER_SKILL.read_text(encoding="utf-8")
        formats = text.split("**Formats.**", 1)[1].split("**Product-safety line.**", 1)[0]
        blocks = re.findall(r"```\n(.*?)```", formats, re.S)
        self.assertGreaterEqual(len(blocks), 4)
        for block in blocks:
            with self.subTest(first=block.splitlines()[0]):
                self.assertIn("Trigger reason:", block)


# The alert exactly as IntelligenceService's handoffToGuardrails.postGuardrailSlackMessage
# builds it (lines joined with "\n"). The Prompt line carries the tool's promptFilename, which
# the prompter sets only when an experiment arm swapped the body: control alerts have no line.
def _is_alert(prompt_file=None):
    lines = ["*Conversation guardrail triggered*", "*User:* abc-123_x.y", "*Transaction:* tx:1",
             "*Conversation:* conv-9"]
    if prompt_file is not None:
        lines.append(f"*Prompt:* {prompt_file}")
    lines.append("_First guardrail trigger in this conversation. Later triggers are "
                 "not posted, so review the whole conversation._")
    return {"ts": "1.0", "text": "\n".join(lines)}


class ProducerPromptLine(unittest.TestCase):
    def setUp(self):
        self.mod = _sweep()

    def test_prompt_file_from_the_producer_format(self):
        a = self.mod.parse_alert(_is_alert("coach.coachPrompterGuardrails.a.txt"))
        self.assertEqual(a["prompt"], "coach.coachPrompterGuardrails.a.txt")
        self.assertEqual((a["user"], a["transaction"], a["conversation"]), ("abc-123_x.y", "tx:1", "conv-9"))

    def test_base_prompt_alert_has_no_line(self):
        self.assertIsNone(self.mod.parse_alert(_is_alert())["prompt"])

    def test_rollup_counts_per_prompt_from_parsed_alerts(self):
        roll = _rollup()
        reasons = roll.load_reasons(str(CANONICAL))
        raw = [_is_alert("coach.coachPrompterGuardrails.a.txt"), _is_alert(), _is_alert()]
        alerts = []
        for i, m in enumerate(raw):
            m["ts"] = str(i)
            a = self.mod.parse_alert(m)
            a["user"] = f"u{i}"
            alerts.append(a)
        codes = {"1": {"reason": "figure-of-speech", "severity": "false-positive"},
                 "2": {"reason": "suicidal-or-self-harm", "severity": "crisis"}}
        r = roll.rollup(roll.build(alerts, {}, codes, reasons), reasons)
        by = {p["prompt"]: p for p in r["prompts"]}
        self.assertEqual(by[roll.NO_PROMPT]["alerts"], 2)
        self.assertEqual(by[roll.NO_PROMPT]["severity"], {"false-positive": 1, "crisis": 1})
        self.assertEqual(by["coach.coachPrompterGuardrails.a.txt"]["alerts"], 1)
        out = roll.render(r)
        self.assertIn("| Prompt that fired |", out)
        self.assertIn("| `base prompt` | 2 | 2 | 1 | 0 | 1 |", out)

    def test_no_prompt_table_when_no_alert_carries_the_line(self):
        roll = _rollup()
        reasons = roll.load_reasons(str(CANONICAL))
        a = self.mod.parse_alert(_is_alert())
        out = roll.render(roll.rollup(roll.build([a], {}, {}, reasons), reasons))
        self.assertNotIn("Prompt that fired", out)

    def test_triage_skill_writes_the_prompt_line_on_every_format(self):
        text = PRODUCER_SKILL.read_text(encoding="utf-8")
        self.assertEqual(text.count("  Trigger reason: "), text.count("  Prompt: <the alert's *Prompt:* value"))


class ExtractFailures(unittest.TestCase):
    def test_a_failed_fetch_does_not_abort_the_batch(self):
        import subprocess
        import tempfile
        mod = load_reader(SKILL / "scripts/extract_turns.py", siblings=())
        def boom(*a, **k):
            raise subprocess.TimeoutExpired(cmd="fetch", timeout=300)
        mod.subprocess.run, real = boom, mod.subprocess.run
        try:
            with tempfile.TemporaryDirectory() as d:
                path = mod.fetch({"ts": "1.0", "conversation": "c", "user": "u"}, d, True)
                rec = mod.extract({"ts": "1.0", "user": "u"}, path, 100, 100)
        finally:
            mod.subprocess.run = real
        self.assertFalse(rec["analysable"])


class GuardrailRunWalkBack(unittest.TestCase):
    """Bugbot on #1307: trigger_reasons.md judges the message that started a run of
    guardrail replies, so the extractor must hand the model that message too."""

    def test_reply_to_a_guardrail_walks_back_to_the_message_that_started_the_run(self):
        import json
        import tempfile
        mod = load_reader(SKILL / "scripts/extract_turns.py", siblings=())
        msgs = [
            {"index": 1, "role": "user", "text": "It's all I can do to keep from hating him."},
            {"index": 2, "role": "assistant", "text": "coach reply"},
            {"index": 3, "role": "user", "text": "Sometimes I imagine hurting him."},
            {"index": 4, "role": "assistant", "text": "guardrail", "guardrail_handled": True},
            {"index": 5, "role": "user", "text": "But I'm not gonna do that."},
            {"index": 6, "role": "assistant", "text": "guardrail", "guardrail_handled": True,
             "is_alert_turn": True},
        ]
        with tempfile.TemporaryDirectory() as d:
            path = f"{d}/a.json"
            with open(path, "w") as fh:
                json.dump({"messages": msgs}, fh)
            rec = mod.extract({"ts": "1.0", "user": "u"}, path, 200, 200)
        self.assertEqual(rec["trigger"], "Sometimes I imagine hurting him.")
        self.assertEqual(rec["previous"], "It's all I can do to keep from hating him.")


class GuardrailRunWalkBackEdge(unittest.TestCase):
    def test_transcript_opening_on_a_handler_reply_keeps_the_follow_up(self):
        # Bugbot on #1307: walking back onto a leading handler reply emptied the trigger.
        import json
        import tempfile
        mod = load_reader(SKILL / "scripts/extract_turns.py", siblings=())
        msgs = [
            {"index": 1, "role": "assistant", "text": "guardrail", "guardrail_handled": True},
            {"index": 2, "role": "user", "text": "But I'm not gonna do that."},
            {"index": 3, "role": "assistant", "text": "guardrail", "guardrail_handled": True,
             "is_alert_turn": True},
        ]
        with tempfile.TemporaryDirectory() as d:
            path = f"{d}/a.json"
            with open(path, "w") as fh:
                json.dump({"messages": msgs}, fh)
            rec = mod.extract({"ts": "1.0", "user": "u"}, path, 200, 200)
        self.assertTrue(rec["analysable"])
        self.assertEqual(rec["trigger"], "But I'm not gonna do that.")


class DuplicateSeverity(unittest.TestCase):
    def test_pointer_inherits_the_first_threads_verdict(self):
        mod = _sweep()
        self.assertEqual(mod.parse_reply(":leftwards_arrow_with_hook: duplicate alert — same conversation as …")[0],
                         "duplicate")
        alerts = [{"ts": "1.0", "conversation": "c", "severity": "crisis", "category": "crisis"},
                  {"ts": "2.0", "conversation": "c", "severity": "duplicate", "category": None},
                  {"ts": "3.0", "conversation": "d", "severity": "duplicate", "category": None}]
        mod.inherit_duplicate_severity(alerts)
        self.assertEqual(alerts[1]["severity"], "crisis")
        self.assertEqual(alerts[1]["category"], "crisis")
        self.assertEqual(alerts[2]["severity"], "duplicate")  # first thread not triaged yet


class Rollup(unittest.TestCase):
    def setUp(self):
        self.mod = _rollup()
        self.reasons = self.mod.load_reasons(str(CANONICAL))

    def _alert(self, ts, user, conv=None, line=None, severity=None):
        return {"ts": ts, "user": user, "conversation": conv or f"c{ts}", "severity": severity, "reason_line": line}

    def test_worst_severity_counts_each_user_once(self):
        alerts = [self._alert("1", "u1"), self._alert("2", "u1"), self._alert("3", "u2"), self._alert("4", "u3")]
        codes = {"1": {"reason": "medication", "subject": "self", "severity": "non-crisis"},
                 "2": {"reason": "suicidal-or-self-harm", "subject": "self", "severity": "crisis"},
                 "3": {"reason": "figure-of-speech", "subject": "self", "severity": "false-positive"}}
        r = self.mod.rollup(self.mod.build(alerts, {}, codes, self.reasons), self.reasons)
        self.assertEqual(r["users"], 3)
        self.assertEqual(sum(r["users_by_severity"].values()), 3)
        self.assertEqual(r["users_by_severity"], {"crisis": 1, "false-positive": 1, "unclassified": 1})

    def test_triage_line_fills_gaps_and_model_overrides(self):
        line = {"reason": "diagnosis", "subject": "other", "asked_for": "clinical-advice", "in_care": False}
        alerts = [self._alert("1", "u1", line=line, severity="non-crisis"), self._alert("2", "u2", line=line)]
        rows = self.mod.build(alerts, {}, {"2": {"reason": "medication", "subject": "self"}}, self.reasons)
        self.assertEqual([(x["reason"], x["source"]) for x in rows], [("diagnosis", "triage_line"), ("medication", "model")])

    def test_unknown_reasons_and_duplicates_are_flagged(self):
        alerts = [self._alert("1", "u1", conv="same"), self._alert("2", "u1", conv="same")]
        rows = self.mod.build(alerts, {}, {"1": {"reason": "not-a-reason"}}, self.reasons)
        out = self.mod.render(self.mod.rollup(rows, self.reasons))
        self.assertIn("share a conversation", out)
        self.assertIn("not-a-reason", out)


if __name__ == "__main__":
    unittest.main()
