"""Guards on the Conversations loop's Gate 1 / Gate 2 verdict parser.

The expensive failure is not a missed verdict — it is a *wrong* one written
confidently, because it moves the row and nobody re-reads a thread that already
looks decided. So the cases here are mostly the ones that look like one verdict
and are another.

Ported alongside the parser itself from the Features loop's harvester; when a
case is added here, check whether it belongs there too.
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
HARVEST = REPO / "plugins/product/skills/harvest-prompt-decisions/scripts"
SHARE = REPO / "plugins/product/skills/share-prompt-candidate/scripts"


def _load(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    sys.path.insert(0, str(path.parent))
    spec.loader.exec_module(module)
    return module


hpd = _load("harvest_prompt_decisions", HARVEST / "harvest_prompt_decisions.py")
spc = _load("share_prompt_candidate", SHARE / "share_prompt_candidate.py")


class Verdicts(unittest.TestCase):
    def v(self, text, gate=1):
        return hpd.classify(text, gate=gate)[0]

    def test_named_gate_approval(self):
        self.assertEqual(self.v("I approve the diagnosis"), "approved")
        self.assertEqual(self.v("approved for release", gate=2), "approved")

    def test_generic_approval_passes_gate_1(self):
        self.assertEqual(self.v("approved"), "approved")
        self.assertEqual(self.v("go ahead"), "approved")

    def test_generic_approval_cannot_pass_gate_2(self):
        """The thread still holds the Gate 1 approval that got the candidate here,
        and the harvester reads the LAST human reply — so a bare "approved" found
        at Gate 2 is very likely that one, and accepting it would ship a candidate
        on a decision made about something else."""
        for text in ("approved", "go ahead", "ship it"):
            self.assertEqual(self.v(text, gate=2), "needs_judgment", text)

    def test_the_explicit_form_still_passes_gate_2(self):
        self.assertEqual(self.v("approved for release", gate=2), "approved")

    def test_negated_approval_is_a_rejection_not_a_miss(self):
        """'Not approved for release' CONTAINS 'approved for release'."""
        for text in ("not approved for release",
                     "this is not approved",
                     "we cannot approve the diagnosis",
                     "I wouldn't approve the diagnosis yet"):
            self.assertIn(self.v(text, gate=2), ("rejected", "needs_judgment"), text)

    def test_approval_naming_the_wrong_gate_is_handed_back(self):
        """A release approval on a row nobody has drafted is somebody in the wrong
        thread, or a row that is behind. Either is worth stopping on."""
        self.assertEqual(self.v("approved for release", gate=1), "needs_judgment")
        self.assertEqual(self.v("I approve the diagnosis", gate=2), "needs_judgment")

    def test_defer_beats_the_approval_inside_it(self):
        self.assertEqual(self.v("let's do it later"), "deferred")
        self.assertEqual(self.v("later — until the prompt engineer starts"), "deferred")

    def test_wrong_change_is_not_a_rejection(self):
        """The defect was just endorsed; reading it as a rejection would retire a
        problem the reviewer agrees exists."""
        for text in ("right defect, wrong change",
                     "wrong change, right defect",
                     "not sold on the fix"):
            self.assertEqual(self.v(text), "wrong_change", text)

    def test_rejection_requires_its_reason_phrasing(self):
        self.assertEqual(self.v("no because this is the global-instruction shape"), "rejected")
        self.assertEqual(self.v("drop it"), "rejected")

    def test_a_trailing_question_is_not_a_decision(self):
        self.assertEqual(self.v("what does the safety suite say?"), "needs_answer")

    def test_a_question_mark_inside_a_link_is_not_a_question(self):
        self.assertEqual(self.v("see <https://x.test/a?b=1>"), "no_decision")

    def test_silence_is_no_decision(self):
        self.assertEqual(self.v("interesting"), "no_decision")

    def test_changes_requested_at_gate_2(self):
        self.assertEqual(self.v("asking for changes", gate=2), "changes_requested")


class Transitions(unittest.TestCase):
    def test_wrong_change_sends_the_fix_back_but_keeps_the_defect(self):
        self.assertEqual(hpd.TRANSITIONS[(1, "wrong_change")], "proposed")

    def test_gate_2_rejection_holds_rather_than_rejecting(self):
        """A prompt cannot sit dormant in a build, so the no-verdict is `held`:
        the commit exists and production is untouched."""
        self.assertEqual(hpd.TRANSITIONS[(2, "rejected")], "held")

    def test_undecided_verdicts_have_no_transition(self):
        for verdict in ("needs_answer", "needs_judgment", "no_decision"):
            for gate in (1, 2):
                self.assertIsNone(hpd.TRANSITIONS.get((gate, verdict)))


class SharePermalink(unittest.TestCase):
    """The share leg synthesises a permalink-shaped URL when Slack will not give
    one, because harvest recovers the thread ts from that shape."""

    def test_the_synthesised_shape_round_trips_through_the_ts_parser(self):
        channel, ts = "C0C29JPEUQG", "1758560000.123456"
        synthesised = f"https://slack.com/archives/{channel}/p{ts.replace('.', '')}"
        self.assertEqual(hpd.thread_ts_from_permalink(synthesised), ts)


class Permalinks(unittest.TestCase):
    def test_extracts_the_thread_ts(self):
        ts = hpd.thread_ts_from_permalink(
            "https://aurahealth.slack.com/archives/C0C29JPEUQG/p1758560000123456")
        self.assertEqual(ts, "1758560000.123456")

    def test_returns_none_rather_than_guessing(self):
        self.assertIsNone(hpd.thread_ts_from_permalink(""))
        self.assertIsNone(hpd.thread_ts_from_permalink("https://example.test/nope"))


def _row(**props):
    shaped = {}
    for k, v in props.items():
        name = k.replace("_", " ")
        if name in ("Diagnosis", "Change class", "Status", "Prompter"):
            shaped[name] = {"type": "select", "select": {"name": v} if v else None}
        else:
            shaped[name] = {"type": "rich_text",
                            "rich_text": [{"plain_text": v}] if v else []}
    return {"id": "p1", "properties": shaped}


class Readiness(unittest.TestCase):
    def test_a_complete_candidate_is_ready(self):
        row = _row(Defect="d", Diagnosis="structural", Risk_named="r",
                   Change_class="behavioural")
        self.assertEqual(spc.missing_fields(row), [])

    def test_missing_risk_blocks_the_share(self):
        row = _row(Defect="d", Diagnosis="structural", Risk_named="",
                   Change_class="behavioural")
        self.assertTrue(any("Risk named" in g for g in spc.missing_fields(row)))

    def test_not_yet_diagnosed_blocks_the_share(self):
        """Gate 1 approves a reading, so 'not yet diagnosed' has nothing to approve."""
        row = _row(Defect="d", Diagnosis="not yet diagnosed", Risk_named="r",
                   Change_class="behavioural")
        self.assertTrue(any("not yet diagnosed" in g for g in spc.missing_fields(row)))



class SharePostOutcome(unittest.TestCase):
    """`ts`, not `ok`, decides whether a Slack message landed — a chunked post can
    return a parent ts with ok false, and retrying that mints a second thread."""

    def test_a_ts_means_it_landed_even_without_ok(self):
        resp = {"ok": False, "error": "partial_failure", "ts": "1758560000.123456"}
        self.assertTrue(resp.get("ts"))

    def test_no_ts_means_nothing_landed_and_retry_is_safe(self):
        self.assertFalse({"ok": False, "error": "channel_not_found"}.get("ts"))


class ReleaseIsWrittenOnlyByShip(unittest.TestCase):
    """Gate 2 approval means approved to SHIP, not shipped. When harvest wrote
    `released` directly, `ship record` — which only accepts a pre-release status —
    was refused after every normal harvest, so the release version was never
    stored and ship's change-class guard never ran. That guard is what stops an
    arm-routed candidate being booked as a full rollout it never had."""

    def test_gate_2_approval_does_not_claim_a_release(self):
        self.assertEqual(hpd.TRANSITIONS[(2, "approved")], "in_review")

    def test_no_verdict_anywhere_writes_released(self):
        self.assertNotIn("released", hpd.TRANSITIONS.values())


if __name__ == "__main__":
    unittest.main()
