"""Guards on the ship and learn legs — the loop's two write-back points."""
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


ship = _load("ship_prompt_candidate",
             "plugins/product/skills/ship-prompt-candidate/scripts/ship_prompt_candidate.py")
learn = _load("learn_prompt_candidate",
              "plugins/product/skills/learn-prompt-candidate/scripts/learn_prompt_candidate.py")


class Routing(unittest.TestCase):
    def test_every_class_has_exactly_one_route(self):
        classes = ship.FULL_ROLLOUT | ship.ARM_ROUTED
        self.assertEqual(classes, {"safety", "correctness", "behavioural", "model/config"})
        self.assertEqual(ship.FULL_ROLLOUT & ship.ARM_ROUTED, set())

    def test_safety_never_goes_out_as_an_arm(self):
        """A crisis path cannot be A/B tested, and there is nothing to learn from
        withholding a guardrail fix from half the users."""
        self.assertIn("safety", ship.FULL_ROLLOUT)
        self.assertNotIn("safety", ship.ARM_ROUTED)

    def test_behavioural_never_goes_out_to_everyone(self):
        self.assertIn("behavioural", ship.ARM_ROUTED)
        self.assertNotIn("behavioural", ship.FULL_ROLLOUT)

    def test_only_pre_release_candidates_can_ship(self):
        """`evaluated` is evidence-in, verdict-not-yet-harvested; `in_review` is
        Gate-2-approved. Both are pre-release — and `released` is written by ship
        alone, so it must never appear here."""
        self.assertEqual(ship.SHIPPABLE, {"evaluated", "in_review"})
        self.assertNotIn("released", ship.SHIPPABLE)

    def test_the_arm_blocker_names_its_ticket(self):
        """A refusal that does not say what would unblock it is just a wall."""
        self.assertIn("ENGBACK-1376", ship.ARM_BLOCKER)


class Verdicts(unittest.TestCase):
    def test_a_verdict_needs_a_candidate_that_reached_users(self):
        self.assertEqual(learn.VERDICTABLE, {"released", "experiment_running"})

    def test_rolled_back_is_a_verdict_not_an_abandonment(self):
        """A withdrawn candidate is answered; the next one on that defect has to
        see that this was tried."""
        self.assertIn("rolled back", learn.VALID_VERDICTS)
        self.assertEqual(learn.VERDICT_TO_STATUS["rolled back"], "rolled_back")
        self.assertNotIn("abandoned", learn.VALID_VERDICTS)

    def test_every_verdict_maps_to_a_terminal_status(self):
        for verdict, status in learn.VERDICT_TO_STATUS.items():
            self.assertIn(status, learn.TERMINAL, verdict)

    def test_lost_is_terminal_and_distinct_from_abandoned(self):
        self.assertIn("lost", learn.TERMINAL)
        self.assertIn("abandoned", learn.TERMINAL)
        self.assertNotEqual(learn.VERDICT_TO_STATUS.get("lost"), "abandoned")



class WritePathGuards(unittest.TestCase):
    """`route` refused the dangerous cases; the write paths did not. A guard that
    only exists on the advisory command is not a guard."""

    def test_rollback_requires_a_candidate_that_reached_users(self):
        """The digest treats `rolled_back` as answered, so rolling back an untried
        candidate retires a defect nobody ever tested."""
        self.assertEqual(ship.ROLLBACKABLE, {"released", "experiment_running"})
        self.assertNotIn("evaluated", ship.ROLLBACKABLE)
        self.assertNotIn("proposed", ship.ROLLBACKABLE)

    def test_rollbackable_and_shippable_are_disjoint(self):
        """Nothing can be shipped and rolled back from the same status."""
        self.assertEqual(ship.SHIPPABLE & ship.ROLLBACKABLE, set())


class FunnelArithmetic(unittest.TestCase):
    def test_abandoned_is_terminal_but_not_a_verdict(self):
        """It ends a candidate without answering it, and those candidates
        typically never shipped — so counting it as reached-and-decided makes one
        abandonment look like a closed loop."""
        self.assertIn("abandoned", learn.TERMINAL)
        self.assertNotIn("abandoned", learn.VERDICT_TO_STATUS.values())

    def test_every_real_verdict_is_terminal_and_not_abandoned(self):
        for status in learn.VERDICT_TO_STATUS.values():
            self.assertIn(status, learn.TERMINAL)
            self.assertNotEqual(status, "abandoned")

if __name__ == "__main__":
    unittest.main()
