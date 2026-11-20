"""Guards on the Aurie Conversations loop's candidate recorder.

Every test here corresponds to a way the backlog silently corrupts itself. They
are written against the behaviour, not the implementation, so a rewrite of the
dedup internals still has to keep these true.

No network: `plan_writes` and `find_similar` take the existing rows as a plain
dict, which is the seam the real loader fills from Notion.
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
SKILL = REPO / "plugins/product/skills/record-prompt-candidates/scripts"


def _load(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module          # dataclasses needs the module registered
    sys.path.insert(0, str(path.parent))
    spec.loader.exec_module(module)
    return module


rpc = _load("record_prompt_candidates", SKILL / "record_prompt_candidates.py")


def _row(*, title, status, fingerprint, prompter="coach", klass="behavioural",
         number=1, reason=None, note=None):
    """A Notion row shaped the way `query_data_source` yields them."""
    props = {
        "Title": {"type": "title", "title": [{"plain_text": title}]},
        "Status": {"type": "select", "select": {"name": status}},
        "Prompter": {"type": "select", "select": {"name": prompter}},
        "Change class": {"type": "select", "select": {"name": klass}},
        "Fingerprint": {"type": "rich_text", "rich_text": [{"plain_text": fingerprint}]},
        "Candidate ID": {"type": "unique_id", "unique_id": {"prefix": "CONV", "number": number}},
        "Rejection reason": {"type": "select",
                             "select": {"name": reason} if reason else None},
        "Quick status note": {"type": "rich_text",
                              "rich_text": [{"plain_text": note}] if note else []},
    }
    return {"id": f"page-{number}", "properties": props}


def _candidate(**kw):
    kw.setdefault("prompter", "coach")
    kw.setdefault("change_class", "behavioural")
    return rpc.Candidate(**kw)


class Fingerprint(unittest.TestCase):
    def test_is_stable_and_case_insensitive_on_title(self):
        a = _candidate(title="Reduce repeated Yeah openers")
        b = _candidate(title="reduce repeated yeah openers")
        self.assertEqual(a.fingerprint(), b.fingerprint())

    def test_separates_fields_so_concatenation_cannot_collide(self):
        # Without separators, ("coach","behaviouralX") and ("coachb","ehaviouralX")
        # would hash identically.
        a = _candidate(prompter="coach", change_class="behavioural", title="t")
        b = rpc.Candidate(prompter="coachb", change_class="ehavioural", title="t")
        self.assertNotEqual(a.fingerprint(), b.fingerprint())

    def test_changes_with_prompter(self):
        a = _candidate(title="same title")
        b = _candidate(title="same title", prompter="companion")
        self.assertNotEqual(a.fingerprint(), b.fingerprint())


class Similarity(unittest.TestCase):
    def test_stems_so_inflections_match(self):
        score = rpc._similar("Growth Renderer V0 requires an update",
                             "Growth Renderer V0 needs updating")
        self.assertGreaterEqual(score, 0.4)

    def test_unrelated_titles_score_low(self):
        score = rpc._similar("coach: vary the opening interjection",
                             "coach: hold scope when the user removes the referral")
        self.assertLess(score, 0.4)

    def test_never_flags_the_candidates_own_row(self):
        """The bug: a re-recorded answered candidate matched itself, so `apply`
        refused the whole batch including unrelated new rows."""
        c = _candidate(title="coach: ask fewer questions per turn")
        existing = {c.fingerprint(): _row(title=c.title, status="rejected",
                                          fingerprint=c.fingerprint(),
                                          reason="registered hard stop")}
        self.assertEqual(rpc.find_similar(c, existing), [])

    def test_still_flags_a_different_row_that_resembles_it(self):
        c = _candidate(title="coach: ask fewer questions per turn")
        other = _candidate(title="coach: ask fewer questions each turn")
        existing = {other.fingerprint(): _row(title=other.title, status="rejected",
                                              fingerprint=other.fingerprint(),
                                              reason="registered hard stop")}
        hits = rpc.find_similar(c, existing)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0][0], "rejected")


class PlanWrites(unittest.TestCase):
    def test_collapses_duplicate_fingerprints_within_one_batch(self):
        """Two identical rows in one input previously produced two Notion pages
        sharing a Fingerprint; later loads keep one and updates miss the other."""
        c1 = _candidate(title="coach: same candidate twice")
        c2 = _candidate(title="coach: same candidate twice")
        plan = rpc.plan_writes([c1, c2], {})
        self.assertEqual(len(plan.creates), 1)
        self.assertEqual(len(plan.skips), 1)
        self.assertIn("duplicate", plan.skips[0][1])

    def test_skips_a_row_a_human_decided(self):
        c = _candidate(title="coach: something settled")
        existing = {c.fingerprint(): _row(title=c.title, status="rejected",
                                          fingerprint=c.fingerprint())}
        plan = rpc.plan_writes([c], existing)
        self.assertEqual(plan.creates, [])
        self.assertEqual(len(plan.skips), 1)

    def test_updates_an_open_row_rather_than_duplicating_it(self):
        c = _candidate(title="coach: still open")
        existing = {c.fingerprint(): _row(title=c.title, status="proposed",
                                          fingerprint=c.fingerprint())}
        plan = rpc.plan_writes([c], existing)
        self.assertEqual(plan.creates, [])
        self.assertEqual(len(plan.updates), 1)

    def test_answered_match_warns_and_open_match_only_notes(self):
        answered = _candidate(title="coach: ask fewer questions per turn")
        c = _candidate(title="coach: ask fewer questions each turn")
        existing = {answered.fingerprint(): _row(title=answered.title, status="lost",
                                                 fingerprint=answered.fingerprint())}
        plan = rpc.plan_writes([c], existing)
        self.assertTrue(plan.warnings)
        self.assertFalse(plan.parked)


class Validation(unittest.TestCase):
    def test_rejects_an_unknown_verdict(self):
        errs = _candidate(title="t", verdict="definitely won").validate()
        self.assertTrue(any("verdict" in e for e in errs))

    def test_rejects_an_unknown_change_class(self):
        c = rpc.Candidate(title="t", prompter="coach", change_class="behavioral")
        self.assertTrue(any("changeClass" in e for e in c.validate()))

    def test_safety_class_requires_a_diagnosis(self):
        c = rpc.Candidate(title="t", prompter="coach", change_class="safety")
        self.assertTrue(any("diagnosis" in e for e in c.validate()))
        c.diagnosis = "structural"
        self.assertEqual(c.validate(), [])

    def test_title_and_prompter_are_required(self):
        errs = rpc.Candidate().validate()
        self.assertTrue(any("title" in e for e in errs))
        self.assertTrue(any("prompter" in e for e in errs))


class Properties(unittest.TestCase):
    def test_create_keys_the_title_lowercase(self):
        """Under the 2025-09-03 API a data_source_id parent resolves the title by
        id; keying it "Title" on a create lands the row with an empty title."""
        props = rpc._props(_candidate(title="t"), creating=True)
        self.assertIn("title", props)
        self.assertNotIn("Title", props)

    def test_update_keys_the_title_by_display_name(self):
        props = rpc._props(_candidate(title="t"), creating=False)
        self.assertIn("Title", props)
        self.assertNotIn("title", props)

    def test_update_never_touches_the_decision_record(self):
        c = _candidate(title="t", status="rejected", status_note="n",
                       rejection_reason="timing", verdict="lost")
        props = rpc._props(c, creating=False)
        for decision_field in ("Status", "Quick status note", "Rejection reason",
                               "Verdict"):
            self.assertNotIn(decision_field, props)

    def test_create_writes_the_decision_record_and_freezes_the_fingerprint(self):
        c = _candidate(title="t", status="proposed", status_note="n")
        props = rpc._props(c, creating=True)
        self.assertIn("Status", props)
        self.assertIn("Fingerprint", props)
        self.assertIn("First seen", props)


if __name__ == "__main__":
    unittest.main()
