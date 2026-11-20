"""The weekday distill-sessions-daily routine raises a follow-up for every stop and every decision.

Its stops and its "Needs you" decisions used to live only in the run log. The SKILL.md's step 10
records each step's outcome and runs its own copy of `outcomes.py` (the shared behaviour, and the
raise-followup round trip, are pinned in `test_cadence_stop_followups.py` over every copy).
"""
from __future__ import annotations

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_cadence_stop_followups import COPIES, REPO, load, run  # noqa: E402

SKILL = REPO / "plugins/shared/skills/distill-sessions-daily"
STEPS = "find,triage,extract,prs,knowledge,cards,log,ledger"


class DistillSessionsDaily(unittest.TestCase):
    def setUp(self):
        self.mod = load(SKILL / "scripts/outcomes.py")

    def test_it_ships_its_own_copy_and_runs_it_with_every_step_due(self):
        self.assertIn(SKILL / "scripts/outcomes.py", COPIES)
        body = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("skills/distill-sessions-daily/scripts/outcomes.py", body)
        self.assertIn(f"--due {STEPS}", body)
        for step in STEPS.split(","):
            self.assertIn(f"`{step}`", body)
        self.assertIn('{"step": "decisions", "status": "gate"', body)
        self.assertIn("Skip it on a `dry_run`", body)

    def test_a_run_that_stopped_after_triage_raises_every_step_it_never_reached(self):
        code, out = run(self.mod, [{"step": "find", "status": "ok"}, {"step": "triage", "status": "ok"}], due=STEPS)
        self.assertEqual(code, 1)
        self.assertEqual([o["key"].split(":")[1] for o in out],
                         ["extract", "prs", "knowledge", "cards", "log", "ledger"])
        self.assertTrue(all(o["kind"] == "blocked" for o in out))

    def test_a_failed_write_and_the_decisions_list_are_raised(self):
        done = [{"step": s, "status": "ok"} for s in STEPS.split(",") if s != "knowledge"]
        code, out = run(self.mod, done + [
            {"step": "knowledge", "status": "failed", "reason": "2 Notion writes failed (HTTP 502)"},
            {"step": "decisions", "status": "gate", "reason": "3 decisions need you: see run log 2026-11-18"},
        ], due=STEPS)
        self.assertEqual(code, 1)
        self.assertEqual([(o["key"].split(":")[1], o["kind"]) for o in out],
                         [("knowledge", "blocked"), ("decisions", "decision")])
        self.assertIn("3 decisions", out[1]["what"])

    def test_a_clean_day_with_no_decisions_raises_nothing(self):
        code, out = run(self.mod, [{"step": s, "status": "ok"} for s in STEPS.split(",")], due=STEPS)
        self.assertEqual((code, out), (0, []))


if __name__ == "__main__":
    unittest.main()
