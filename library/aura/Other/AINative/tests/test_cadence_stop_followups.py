"""An unattended cadence run raises a follow-up for every step that did not finish.

`outcomes.py` turns a run's per-step outcomes into `shared:raise-followup` fields. Forced stops
here: a failed group, a write gate, a step the run never reached (an early end or an expired
wait), and a run that stopped before recording anything. Every copy is tested, and the fields it
prints are fed to raise-followup itself with Slack and Linear stubbed: one post per key, a card
for `blocked`, a changed reason a thread reply on the same card.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import pathlib
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
CANONICAL = REPO / "plugins/data/skills/run-all-harvest-cadence/scripts/outcomes.py"
COPIES = sorted(REPO.glob("plugins/*/skills/*/scripts/outcomes.py"))
DB = "0123456789abcdef0123456789abcdef"

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_raise_followup import CFG, Base, fu  # noqa: E402


def load(path):
    spec = importlib.util.spec_from_file_location(f"outcomes_{path.parent.parent.name}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run(mod, outcomes, due="a,b,c"):
    f = pathlib.Path(tempfile.mkdtemp()) / "outcomes.json"
    f.write_text(json.dumps(outcomes))
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        code = mod.main(["--cadence", "run-x-cadence", "--session", "scheduled", "--in", str(f), "--due", due])
    return code, (json.loads(out.getvalue()) if out.getvalue().strip() else None)


class EveryCopy(unittest.TestCase):
    def test_copies_exist_and_match(self):
        self.assertGreaterEqual(len(COPIES), 2)
        for path in COPIES:
            with self.subTest(copy=str(path.relative_to(REPO))):
                self.assertEqual(path.read_bytes(), CANONICAL.read_bytes())

    def test_a_clean_run_raises_nothing(self):
        for path in COPIES:
            code, out = run(load(path), [{"step": "a", "status": "ok"}, {"step": "b", "status": "ok"},
                                         {"step": "c", "status": "out-of-period"}])
            self.assertEqual((code, out), (0, []), path)

    def test_forced_stops_each_raise_one(self):
        for path in COPIES:
            code, out = run(load(path), [
                {"step": "a", "status": "failed", "reason": f"Notion 404 on page {DB}\ntraceback"},
                {"step": "b", "status": "gate", "reason": "ledger plan waits for a person", "rerun": "data:run-b"},
            ])  # c never reported: the run ended early
            with self.subTest(copy=path.parent.parent.name):
                self.assertEqual(code, 1)
                self.assertEqual([(o["key"], o["kind"]) for o in out],
                                 [("run-x-cadence:a:stop", "blocked"), ("run-x-cadence:b:stop", "decision"),
                                  ("run-x-cadence:c:stop", "blocked")])
                self.assertIn("01234567…", out[0]["what"])
                self.assertNotIn(DB, out[0]["what"])
                self.assertNotIn("traceback", out[0]["what"])
                self.assertIn("data:run-b", out[1]["ask"])
                self.assertIn("did not run", out[2]["title"])

    def test_a_run_that_recorded_nothing_raises_every_due_step(self):
        code, out = run(load(CANONICAL), [])
        self.assertEqual(code, 1)
        self.assertEqual([o["key"] for o in out], [f"run-x-cadence:{s}:stop" for s in "abc"])

    def test_the_append_format_and_a_missing_file_are_read(self):
        for path in COPIES:
            mod = load(path)
            d = pathlib.Path(tempfile.mkdtemp())
            lines = d / "o.jsonl"
            lines.write_text('{"step": "a", "status": "ok"}\n\n{"step": "b", "status": "failed", "reason": "x"}\n')
            for f, want in ((lines, ["b", "c"]), (d / "never-written.jsonl", ["a", "b", "c"])):
                out = io.StringIO()
                with self.subTest(copy=path.parent.parent.name, file=f.name), contextlib.redirect_stdout(out):
                    code = mod.main(["--cadence", "run-x-cadence", "--session", "s", "--in", str(f), "--due", "a,b,c"])
                self.assertEqual(code, 1)
                self.assertEqual([o["key"] for o in json.loads(out.getvalue())], [f"run-x-cadence:{s}:stop" for s in want])

    def test_a_step_out_of_period_is_not_due(self):
        code, out = run(load(CANONICAL), [{"step": "experiments", "status": "ok"}, {"step": "cost", "status": "ok"}],
                        due="experiments,cost")
        self.assertEqual((code, out), (0, []))

    def test_bad_input_is_exit_2(self):
        mod = load(CANONICAL)
        for bad in ([{"status": "ok"}], [{"step": "a", "status": "weird"}], ["not an object"]):
            with self.subTest(bad=bad):
                self.assertEqual(run(mod, bad)[0], 2)


ANALYSIS = REPO / "plugins/data/skills/run-all-analysis-cadence"
GROUPS = "feedback,rating,pmf,cancellation,guardrails,conversation-guardrails"


class AnalysisCadence(unittest.TestCase):
    """The feedback-analysis routine runs run-all-analysis-cadence every 4 h, unattended."""

    def test_it_ships_its_own_copy(self):
        self.assertIn(ANALYSIS / "scripts/outcomes.py", COPIES)

    def test_the_skill_runs_its_copy_with_every_group_due(self):
        body = (ANALYSIS / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("skills/run-all-analysis-cadence/scripts/outcomes.py", body)
        self.assertIn(f"--due {GROUPS}", body)
        for group in GROUPS.split(","):
            self.assertIn(f"`{group}`", body)

    def test_a_tick_cut_short_after_two_groups_raises_the_other_four(self):
        mod = load(ANALYSIS / "scripts/outcomes.py")
        code, out = run(mod, [{"step": "feedback", "status": "ok"},
                              {"step": "rating", "status": "failed", "reason": "OPENAI_API_KEY is not set"}],
                        due=GROUPS)
        self.assertEqual(code, 1)
        self.assertEqual([(o["key"].split(":")[1], o["kind"]) for o in out],
                         [("rating", "blocked"), ("pmf", "blocked"), ("cancellation", "blocked"),
                          ("guardrails", "blocked"), ("conversation-guardrails", "blocked")])

    def test_a_refused_guardrail_reply_is_raised(self):
        code, out = run(load(ANALYSIS / "scripts/outcomes.py"),
                        [{"step": g, "status": "ok"} for g in GROUPS.split(",")[:-1]]
                        + [{"step": "conversation-guardrails", "status": "blocked",
                            "reason": "card guard refused the reply for https://aurahealth.slack.com/archives/C0X/p1700000000000100"}],
                        due=GROUPS)
        self.assertEqual((code, [o["key"] for o in out]), (1, ["run-x-cadence:conversation-guardrails:stop"]))
        self.assertIn("p1700000000000100", out[0]["what"])


class StopsBecomeFollowUps(Base):
    """outcomes.py's fields passed to raise-followup as the SKILL.md says, unchanged."""

    def raise_all(self, outcomes):
        _, specs = run(load(CANONICAL), outcomes, due="a")
        return [fu.raise_followup(spec, CFG) for spec in specs]

    def test_one_post_per_key_and_a_card_for_blocked(self):
        first = self.raise_all([{"step": "a", "status": "failed", "reason": "NOTION_TOKEN is not set"}])
        self.assertEqual([(c, o["action"], o["card_action"]) for c, o in first], [(0, "posted", "filed")])
        again = self.raise_all([{"step": "a", "status": "failed", "reason": "NOTION_TOKEN is not set"}])
        self.assertEqual([(c, o["action"]) for c, o in again], [(0, "unchanged")])
        self.assertEqual((len(self.slack.messages), len(self.lin.cards)), (1, 1))

    def test_a_changed_reason_is_a_reply_on_the_same_post_and_card(self):
        self.raise_all([{"step": "a", "status": "failed", "reason": "NOTION_TOKEN is not set"}])
        out = self.raise_all([{"step": "a", "status": "blocked", "reason": "bot is not in the channel"}])
        self.assertEqual(out[0][1]["action"], "replied")
        self.assertEqual((len(self.slack.messages), len(self.lin.cards), len(self.lin.comments)), (1, 1, 1))

    def test_a_reason_raise_followup_would_refuse_still_posts(self):
        reason = ("uid Za0DIgYuAbCdEfGh1234567 sha 1c9a0aa413654a0317e934c7546c5d80c54f2330 "
                  "page 0123abcd-0123-4567-89ab-0123456789ab from someone@example.com at 10.1.2.3 key sk-abcdefghijklmnopqrstuv")
        out = self.raise_all([{"step": "a", "status": "failed", "reason": reason}])
        self.assertEqual((out[0][0], out[0][1]["action"]), (0, "posted"), out)
        text = self.slack.messages[0]["text"]
        for leaked in ("Za0DIgYuAbCdEfGh1234567", "1c9a0aa413654a0317e934c7546c5d80c54f2330", "someone@example.com",
                       "10.1.2.3", "sk-abcdefghijklmnopqrstuv", "0123abcd-0123-4567"):
            self.assertNotIn(leaked, text)
        self.assertIn("Za0DIgYu…", text)

    def test_a_gate_files_a_decision_card(self):
        out = self.raise_all([{"step": "a", "status": "gate", "reason": "ledger plan waits for a person"}])
        self.assertEqual((out[0][0], out[0][1]["action"], out[0][1]["card_action"]), (0, "posted", "filed"))


WEEKLY = {  # skill dir -> (steps the routine is due to run, as its SKILL.md passes them)
    REPO / "plugins/eng/skills/build-aurie-cost-report": "prerequisites,freshness,charts,render,publish",
    REPO / "plugins/eng/skills/summarize-weekly-output": "linear,github,report",
}


class WeeklyReportRoutines(unittest.TestCase):
    """weekly-aurie-cost-report and weekly-output-summary run these skills unattended on Mondays."""

    def test_each_ships_its_own_copy_and_runs_it(self):
        for skill, steps in WEEKLY.items():
            with self.subTest(skill=skill.name):
                self.assertIn(skill / "scripts/outcomes.py", COPIES)
                body = (skill / "SKILL.md").read_text(encoding="utf-8")
                self.assertIn(f"skills/{skill.name}/scripts/outcomes.py", body)
                self.assertIn(f"--cadence {skill.name}", body)
                for step in steps.split(","):
                    self.assertIn(f"`{step}`", body)

    def test_a_cost_report_that_never_published_raises_publish(self):
        skill = REPO / "plugins/eng/skills/build-aurie-cost-report"
        code, out = run(load(skill / "scripts/outcomes.py"),
                        [{"step": s, "status": "ok"} for s in ("prerequisites", "freshness", "charts", "render")],
                        due=WEEKLY[skill])
        self.assertEqual((code, [(o["key"].split(":")[1], o["kind"]) for o in out]), (1, [("publish", "blocked")]))

    def test_an_undercount_stop_is_raised(self):
        skill = REPO / "plugins/eng/skills/build-aurie-cost-report"
        code, out = run(load(skill / "scripts/outcomes.py"),
                        [{"step": "prerequisites", "status": "ok"}, {"step": "freshness", "status": "ok"},
                         {"step": "charts", "status": "blocked", "reason": "untagged_cost still non-zero after the env backfill"}],
                        due=WEEKLY[skill])
        self.assertEqual([o["key"].split(":")[1] for o in out], ["charts", "render", "publish"])
        self.assertIn("untagged_cost", out[0]["what"])

    def test_a_degraded_weekly_summary_raises_the_missing_half(self):
        skill = REPO / "plugins/eng/skills/summarize-weekly-output"
        code, out = run(load(skill / "scripts/outcomes.py"),
                        [{"step": "linear", "status": "blocked", "reason": "LINEAR_API_KEY is not set; GitHub half only"},
                         {"step": "github", "status": "ok"}, {"step": "report", "status": "ok"}],
                        due=WEEKLY[skill])
        self.assertEqual((code, [o["key"].split(":")[1] for o in out]), (1, ["linear"]))


if __name__ == "__main__":
    unittest.main()
