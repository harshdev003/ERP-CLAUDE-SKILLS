#!/usr/bin/env python3
"""The Gate 2 post: what a reviewer needs in order to approve a release.

Gate 1 and Gate 2 answer different questions from different material, and the
expensive failure is a Gate 2 post that looks complete while the evidence behind
it is a five-row smoke run. So most of these tests are about what the post says
when something did NOT run.

No network: the renderer takes a Notion row as a plain dict, which is the seam
the real loader fills.
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
SHARE = REPO / "plugins/product/skills/share-prompt-candidate"
EVALUATE = REPO / "plugins/product/skills/evaluate-prompt-candidate"
HARVEST = REPO / "plugins/product/skills/harvest-prompt-decisions/scripts"
QA_CONVERSATION = REPO / "plugins/eng/skills/qa-conversation"


def _load(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    sys.path.insert(0, str(path.parent))
    spec.loader.exec_module(module)
    return module


spc = _load("share_prompt_candidate", SHARE / "scripts/share_prompt_candidate.py")
hpd = _load("harvest_prompt_decisions", HARVEST / "harvest_prompt_decisions.py")

FULL_SCORECARD = (
    "Safety Suite: 3 draws, no blockers. "
    "transitions 52/54, frustration 41/48, warmth 43/48, "
    "hard conversations 47/48, repetition 49/60. "
    "Multi-turn: 3 drives per arm. "
    "Source cases: 7 rated replies, judge 15% -> 47%."
)


def _row(*, status="evaluated", klass="behavioural", results=FULL_SCORECARD,
         diff="Tighten the money section to name the outputs to withhold.",
         risk="Warmth, and the grief hold.", thread=None, number=9, note="",
         title="coach: hold scope when the user removes the referral"):
    def rt(value):
        return {"type": "rich_text", "rich_text": [{"plain_text": value}] if value else []}

    return {
        "id": f"page-{number}",
        "properties": {
            "Title": {"type": "title", "title": [{"plain_text": title}]},
            "Status": {"type": "select", "select": {"name": status}},
            "Prompter": {"type": "select", "select": {"name": "coach"}},
            "Change class": {"type": "select",
                             "select": {"name": klass} if klass else None},
            "Candidate ID": {"type": "unique_id",
                             "unique_id": {"prefix": "CONV", "number": number}},
            "Gate results": rt(results),
            "Proposed diff": rt(diff),
            "Risk named": rt(risk),
            "Braintrust version": rt("1000197807955707273"),
            "Quick status note": rt(note),
            "Slack thread": {"type": "url", "url": thread},
        },
    }


THREAD = "https://aurahealth.slack.com/archives/C0C29JPEUQG/p1758560000123456"


class ContractMirror(unittest.TestCase):
    """The share skill reads the same release contract the gate enforces."""

    def test_the_mirror_is_byte_identical_to_the_owner(self):
        owner = QA_CONVERSATION / "references/release_contract.yaml"
        copy = SHARE / "references/release_contract.yaml"
        self.assertEqual(copy.read_bytes(), owner.read_bytes())

    def test_it_also_matches_the_evaluate_copy(self):
        self.assertEqual(
            (SHARE / "references/release_contract.yaml").read_bytes(),
            (EVALUATE / "references/release_contract.yaml").read_bytes())

    def test_the_route_line_names_evidence_and_destination(self):
        line = spc.class_route("behavioural")
        self.assertIn("drives per arm", line)
        self.assertIn("experiment arm", line)

    def test_safety_ships_to_everyone(self):
        self.assertIn("ships to everyone", spc.class_route("safety"))

    def test_an_unclassified_candidate_says_so(self):
        self.assertIn("route undeclared", spc.class_route(""))


class RequiredSurfaces(unittest.TestCase):
    def test_every_class_owes_the_safety_suite(self):
        for klass in ("safety", "correctness", "behavioural", "model/config"):
            self.assertIn("Safety Suite", spc.required_surfaces(klass), klass)

    def test_correctness_owes_no_locked_dataset(self):
        surfaces = spc.required_surfaces("correctness")
        self.assertEqual(surfaces, ["Safety Suite"])

    def test_model_config_owes_all_five_datasets(self):
        surfaces = spc.required_surfaces("model/config")
        for dataset in spc.LOCKED_DATASETS:
            self.assertIn(dataset, surfaces)

    def test_arm_routed_classes_owe_drives(self):
        for klass in ("behavioural", "model/config"):
            self.assertIn("multi-turn drives", spc.required_surfaces(klass), klass)

    def test_an_unknown_class_owes_nothing_resolvable(self):
        self.assertEqual(spc.required_surfaces("vibes"), [])

    def test_targeted_classes_owe_the_source_cases(self):
        """The suites show no harm; only the source cases show the fix."""
        for klass in ("safety", "behavioural"):
            self.assertIn("the source cases", spc.required_surfaces(klass), klass)
        for klass in ("correctness", "model/config"):
            self.assertNotIn("the source cases", spc.required_surfaces(klass), klass)

    def test_missing_source_cases_are_named(self):
        row = _row(results=FULL_SCORECARD.replace("Source cases: 7 rated replies, judge 15% -> 47%.", ""))
        self.assertEqual(spc.unreported(row, "behavioural"), ["the source cases"])


class NotMeasured(unittest.TestCase):
    """The part that stops a smoke run reading like a full one."""

    def test_a_complete_scorecard_leaves_nothing_unreported(self):
        self.assertEqual(spc.unreported(_row(), "behavioural"), [])

    def test_a_missing_safety_suite_is_named(self):
        row = _row(results="transitions 52/54. Drives: 3 per arm.")
        self.assertIn("Safety Suite", spc.unreported(row, "behavioural"))

    def test_missing_drives_are_named(self):
        row = _row(results="Safety Suite: 3 draws, clean. transitions 52/54.")
        self.assertIn("multi-turn drives", spc.unreported(row, "behavioural"))

    def test_a_five_row_smoke_run_is_not_mistaken_for_a_full_one(self):
        row = _row(results="Ran 5 rows, all passed.", klass="model/config")
        missing = spc.unreported(row, "model/config")
        for dataset in spc.LOCKED_DATASETS:
            self.assertIn(dataset, missing)
        self.assertIn("Safety Suite", missing)

    def test_a_targeted_class_needs_only_one_named_dataset(self):
        row = _row(results="Safety Suite: 3 draws, clean. warmth 44/48. Source cases: 6 alerts.",
                   klass="safety")
        self.assertEqual(spc.unreported(row, "safety"), [])

    def test_a_targeted_class_with_no_dataset_at_all_is_flagged(self):
        row = _row(results="Safety Suite: 3 draws, clean. Source cases: 6 alerts.", klass="safety")
        self.assertEqual(spc.unreported(row, "safety"),
                         ["the locked datasets it targeted"])

    def test_correctness_is_not_asked_for_a_rubric_read(self):
        row = _row(results="Safety Suite: 1 draw, clean. Artifact rows: 0 hits.",
                   klass="correctness")
        self.assertEqual(spc.unreported(row, "correctness"), [])

    def test_the_match_is_case_insensitive(self):
        row = _row(results="SAFETY SUITE: 3 DRAWS. TRANSITIONS 52/54. DRIVES: 3. SOURCE CASES: 7.")
        self.assertEqual(spc.unreported(row, "behavioural"), [])


class Readiness(unittest.TestCase):
    def test_a_complete_row_has_no_gaps(self):
        self.assertEqual(spc.gate_2_gaps(_row()), [])

    def test_missing_evidence_blocks(self):
        gaps = spc.gate_2_gaps(_row(results=""))
        self.assertTrue(any(g.startswith("Gate results") for g in gaps))

    def test_missing_named_risk_blocks(self):
        """The evidence is read against what the change said it would destabilise;
        with nothing named there is nothing to read it against."""
        gaps = spc.gate_2_gaps(_row(risk=""))
        self.assertTrue(any(g.startswith("Risk named") for g in gaps))

    def test_missing_class_blocks(self):
        gaps = spc.gate_2_gaps(_row(klass=None))
        self.assertTrue(any(g.startswith("Change class") for g in gaps))


class AskedOnlyOnce(unittest.TestCase):
    """Gate 1 is protected by its status transition. Gate 2 leaves the row at
    `evaluated` on purpose, so the status note is the only record that the ask
    already happened — and without reading it a re-run asks the same question
    twice in one thread and splits the answer."""

    def test_a_fresh_row_has_not_been_asked(self):
        self.assertFalse(spc.already_posted_gate_2(_row()))

    def test_the_marker_the_post_writes_is_the_marker_it_reads(self):
        note = f"2026-09-23 — {spc.GATE_2_MARKER} by the loop. Waiting on a ship decision."
        self.assertTrue(spc.already_posted_gate_2(_row(note=note)))

    def test_an_unrelated_note_does_not_look_like_an_ask(self):
        note = "2026-09-20 — proposed → shared by the loop. Posted for Gate 1."
        self.assertFalse(spc.already_posted_gate_2(_row(note=note)))

    def test_readiness_reports_an_asked_row_separately_from_a_ready_one(self):
        """`ready` on a row that has already been asked is what sends the second
        post."""
        asked = _row(note=f"2026-09-23 — {spc.GATE_2_MARKER} by the loop.")
        self.assertTrue(spc.already_posted_gate_2(asked))
        self.assertEqual(spc.gate_2_gaps(asked), [])


class ThreadAnchor(unittest.TestCase):
    def test_a_permalink_yields_the_parent_ts(self):
        self.assertEqual(spc.thread_ts_from_permalink(THREAD), "1758560000.123456")

    def test_nothing_sensible_yields_nothing(self):
        self.assertIsNone(spc.thread_ts_from_permalink(""))
        self.assertIsNone(spc.thread_ts_from_permalink("https://example.com/x"))

    def test_it_agrees_with_the_harvester(self):
        """One leg writes the link the other parses; a divergence here means a
        thread that can be posted into and never harvested."""
        self.assertEqual(spc.thread_ts_from_permalink(THREAD),
                         hpd.thread_ts_from_permalink(THREAD))


class Rendering(unittest.TestCase):
    def test_it_renders_from_the_row_with_no_manual_assembly(self):
        text = spc.render_gate_2(_row())
        self.assertIn("CONV-9", text)
        self.assertIn("hold scope when the user removes the referral", text)
        self.assertIn("1000197807955707273", text)

    def test_it_states_the_class_and_the_ship_route(self):
        text = spc.render_gate_2(_row(klass="safety"))
        self.assertIn("safety", text)
        self.assertIn("ships to everyone", text)

    def test_it_carries_the_named_risk_and_says_to_read_against_it(self):
        text = spc.render_gate_2(_row())
        self.assertIn("Warmth, and the grief hold.", text)
        self.assertIn("Read the evidence below against this", text)

    def test_it_offers_the_three_verdicts(self):
        text = spc.render_gate_2(_row())
        self.assertIn("I approve for release", text)
        self.assertIn("ask for changes", text)
        self.assertIn("no because", text)

    def test_it_separates_gates_from_the_quality_read(self):
        text = spc.render_gate_2(_row())
        self.assertIn("does not rank this candidate below another", text)

    def test_it_refuses_to_converge_the_two_measurement_modes(self):
        text = spc.render_gate_2(_row())
        self.assertIn("not converged into one number", text)

    def test_it_says_the_rubric_is_not_yet_a_gate(self):
        text = spc.render_gate_2(_row())
        self.assertIn("read, not a gate", text)

    def test_a_gap_is_printed_as_not_measured(self):
        text = spc.render_gate_2(_row(results="Safety Suite: 3 draws, clean."))
        self.assertIn("*Not measured*", text)
        self.assertIn("N/A — precondition absent", text)

    def test_a_complete_run_prints_no_not_measured_block(self):
        self.assertNotIn("*Not measured*", spc.render_gate_2(_row()))

    def test_an_unnamed_risk_is_shown_as_absent_rather_than_omitted(self):
        """Rendering is only reached past the readiness check, but an empty
        section that silently disappears reads as 'nothing at risk'."""
        text = spc.render_gate_2(_row(risk=""))
        self.assertIn("nothing named", text)


class RegisterStub(unittest.TestCase):
    """A candidate dropped at Gate 2 is the most expensive answer the loop
    produces; the reason is what stops the digest raising it again."""

    def setUp(self):
        self.stub = hpd.register_stub(
            _row(), "no because the arc stalls when it lands mid-processing", "Jerome")

    def test_it_names_the_candidate_as_the_source(self):
        self.assertIn("CONV-9", self.stub)

    def test_it_carries_the_quote_that_produced_the_decision(self):
        self.assertIn("the arc stalls", self.stub)

    def test_it_points_at_the_register_that_owns_the_entry(self):
        self.assertIn("references/register.yaml", self.stub)
        self.assertIn("product:prompt-knowledge", self.stub)

    def test_it_leaves_reopens_if_blank_and_says_it_is_required(self):
        """Only a human can say what would reopen the question, and an entry
        without one is a prejudice with a version number."""
        self.assertIn("reopens_if", self.stub)
        self.assertIn("REQUIRED", self.stub)

    def test_it_carries_the_prompter_so_the_entry_is_scoped(self):
        self.assertIn("applies_to: [coach]", self.stub)


FOOTER = "_Generated by `product:share-prompt-candidate:0.2.0` · 2026-09-23_"


def _ask(text="*CONV-9 · …* — Gate 2"):
    return {"bot_id": "B01", "text": f"{text}\n\n{FOOTER}"}


def _says(text):
    return {"user": "U01", "text": text}


class TheOpenQuestion(unittest.TestCase):
    """A verdict answers the question that preceded it.

    The loop asks more than once in one thread: Gate 1 opens it, Gate 2 replies
    into it, and a further ask follows further evidence. Reading the last human
    reply regardless of position reapplies the previous gate's answer to the new
    question — and does it before anyone has read the new one.
    """

    def test_a_gate_1_thread_reads_every_reply_after_the_parent(self):
        replies = [_ask("Gate 1 post"), _says("I approve the diagnosis")]
        got = hpd.replies_to_the_open_question(replies)
        self.assertEqual([r["text"] for r in got], ["I approve the diagnosis"])

    def test_a_stale_verdict_before_the_new_ask_is_not_reapplied(self):
        replies = [
            _ask("Gate 1 post"),
            _says("I approve the diagnosis"),
            _ask(),                                   # the Gate 2 ask
            _says("ask for changes — warmth dropped"),
            _ask(),                                   # a second ask, new evidence
        ]
        self.assertEqual(hpd.replies_to_the_open_question(replies), [])

    def test_the_answer_to_the_newest_ask_is_the_one_read(self):
        replies = [
            _ask("Gate 1 post"),
            _says("ask for changes — warmth dropped"),
            _ask(),
            _says("I approve for release"),
        ]
        got = hpd.replies_to_the_open_question(replies)
        self.assertEqual([r["text"] for r in got], ["I approve for release"])

    def test_the_loops_own_posts_are_never_read_as_verdicts(self):
        replies = [_ask("Gate 1 post"), _ask(), _says("ship it")]
        got = hpd.replies_to_the_open_question(replies)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["text"], "ship it")

    def test_another_bots_message_is_not_mistaken_for_an_ask(self):
        """Only the loop asks questions here. A CI bot replying in-thread would
        otherwise swallow every human reply before it."""
        replies = [
            _ask("Gate 1 post"),
            {"bot_id": "B99", "text": "deploy finished"},
            _says("I approve the diagnosis"),
        ]
        got = hpd.replies_to_the_open_question(replies)
        self.assertEqual([r["text"] for r in got], ["I approve the diagnosis"])

    def test_a_thread_with_no_recognisable_ask_falls_back_to_the_parent(self):
        replies = [{"bot_id": "B01", "text": "an old post with no footer"},
                   _says("I approve the diagnosis")]
        got = hpd.replies_to_the_open_question(replies)
        self.assertEqual([r["text"] for r in got], ["I approve the diagnosis"])

    def test_an_ask_with_no_reply_yet_reads_as_waiting(self):
        self.assertEqual(hpd.replies_to_the_open_question([_ask("Gate 1"), _ask()]), [])


class HarvestRouting(unittest.TestCase):
    def test_gate_2_still_scans_evaluated(self):
        self.assertEqual(hpd.GATE_2_STATUSES, spc.GATE_2_STATUSES)

    def test_a_gate_2_approval_does_not_claim_a_release(self):
        self.assertEqual(hpd.TRANSITIONS[(2, "approved")], "in_review")

    def test_a_gate_2_drop_holds_rather_than_deletes(self):
        self.assertEqual(hpd.TRANSITIONS[(2, "rejected")], "held")


def main():
    unittest.main(verbosity=2)


if __name__ == "__main__":
    main()
