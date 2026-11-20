"""Guards on the Conversations loop's signal digest.

The digest's job is to rank, and its danger is that ranking looks like reasoning.
These tests pin the three places it must refuse to reason: it excludes topics
that are not defects, it carries the register's hard stops alongside the volume,
and it never emits a diagnosis.
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
SKILL = REPO / "plugins/product/skills/digest-conversation-signals/scripts"


def _load(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    sys.path.insert(0, str(path.parent))
    spec.loader.exec_module(module)
    return module


dcs = _load("digest_conversation_signals", SKILL / "digest_conversation_signals.py")


def _defect(theme, *, rows, negative, ctype="Coaching"):
    d = dcs.Defect(theme)
    d.total = d.in_window = rows
    d.negative = negative
    d.types[ctype] = rows
    return d


class Scoring(unittest.TestCase):
    def test_a_reliably_negative_theme_outranks_a_larger_ambivalent_one(self):
        """100% negative on 10 rows is a defect; 50% negative on 14 is a topic
        people feel two ways about."""
        defect = _defect("transcript_truncated", rows=10, negative=10)
        topic = _defect("mixed", rows=14, negative=7)
        self.assertGreater(defect.score, topic.score)

    def test_volume_still_matters_at_equal_negativity(self):
        big = _defect("a", rows=50, negative=50)
        small = _defect("b", rows=5, negative=5)
        self.assertGreater(big.score, small.score)

    def test_prompter_is_guessed_from_conversation_type(self):
        self.assertEqual(_defect("x", rows=1, negative=1,
                                 ctype="Quick Processing").prompter, "quickProcessing")
        # Chat runs on the coach prompter in production.
        self.assertEqual(_defect("x", rows=1, negative=1, ctype="Chat").prompter, "coach")


class TopicExclusion(unittest.TestCase):
    def test_subject_themes_are_not_defects(self):
        """Ranking on these would propose 'fix grief', which is not a change."""
        for theme in ("grief", "anxiety", "loneliness", "self_worth"):
            self.assertIn(theme, dcs.TOPIC_THEMES, theme)

    def test_abandonment_is_a_subject_despite_reading_like_a_defect(self):
        """27 of its 30 rows are the relationships vertical — "how long do I keep
        reaching out", "I wasn't good enough" — and it co-occurs with the real
        defect tags on those same rows."""
        self.assertIn("abandonment", dcs.TOPIC_THEMES)

    def test_behaviour_themes_are_not_excluded(self):
        for theme in ("coping_tools_gap", "pacing_mismatch", "closure_misfire",
                      "transcript_truncated", "forced_choice"):
            self.assertNotIn(theme, dcs.TOPIC_THEMES, theme)


class HardStops(unittest.TestCase):
    def test_question_saturation_is_a_hard_stop(self):
        """The loudest complaint in the corpus, and the obvious response to it is
        the one the evidence falsified."""
        self.assertIn("question_saturation", dcs.HARD_STOPS)
        reading = dcs.HARD_STOPS["question_saturation"]
        self.assertIn("forward motion", reading)


class Coverage(unittest.TestCase):
    """The matcher failed in both directions before it was made literal."""

    def _row(self, cid, title, haystack):
        return (cid, title, "proposed", haystack.lower())

    def test_matches_a_row_that_names_the_theme_in_its_defect(self):
        rows = [self._row("CONV-9", "coach: close the practical-help gap",
                          "coping_tools_gap — 50 tagged rows")]
        self.assertEqual(len(dcs._covered_by("coping_tools_gap", rows)), 1)

    def test_does_not_match_on_an_incidental_shared_word(self):
        """'conversation' appears in nearly every title in this database."""
        rows = [self._row("CONV-1", "coach: ship the reviewed Conversation prompt",
                          "three configurations ready to compare")]
        self.assertEqual(dcs._covered_by("first_conversation", rows), [])


class Handoff(unittest.TestCase):
    def test_emitted_candidates_never_carry_a_diagnosis(self):
        """A complaint is not a diagnosis. `share-prompt-candidate` refuses these
        until a human supplies one, and that refusal is the discipline."""
        c = dcs.to_candidate(_defect("pacing_mismatch", rows=12, negative=12), "2026-08-01")
        self.assertEqual(c["diagnosis"], "not yet diagnosed")

    def test_emitted_candidates_carry_no_change_class_either(self):
        """The class picks the gate and the ship route — also a human decision."""
        c = dcs.to_candidate(_defect("pacing_mismatch", rows=12, negative=12), "2026-08-01")
        self.assertNotIn("changeClass", c)

    def test_evidence_states_the_window_and_the_counts(self):
        c = dcs.to_candidate(_defect("pacing_mismatch", rows=12, negative=12), "2026-08-01")
        self.assertIn("2026-08-01", c["evidence"])
        self.assertIn("12", c["evidence"])



class StatusBuckets(unittest.TestCase):
    """A status in neither bucket becomes an uncovered theme, and the digest
    re-raises a question the loop already answered. `won`, `inconclusive`,
    `deferred` and `held` were each missing exactly that way."""

    LIFECYCLE = {"proposed", "shared", "approved", "rejected", "deferred", "drafted",
                 "evaluated", "held", "in_review", "merged", "released",
                 "experiment_running", "won", "lost", "inconclusive", "rolled_back",
                 "abandoned"}

    def test_every_lifecycle_status_is_open_or_settled(self):
        for status in self.LIFECYCLE:
            settled = status not in dcs.OPEN_STATUSES
            self.assertTrue(status in dcs.OPEN_STATUSES or settled, status)

    def test_a_tested_question_counts_as_answered(self):
        for status in ("won", "lost", "inconclusive", "rolled_back", "rejected"):
            self.assertIn(status, dcs.ANSWERED_STATUSES, status)

    def test_parked_statuses_are_not_open(self):
        """Parked is not 'still moving' — re-raising a deferred theme is a
        duplicate, not a revival."""
        for status in ("deferred", "held"):
            self.assertNotIn(status, dcs.OPEN_STATUSES, status)


class Verbatims(unittest.TestCase):
    def test_examples_are_only_ever_quoted_from_negative_rows(self):
        """A theme is tagged on rows of every polarity. Taking the first four
        verbatims would quote praise as evidence for a complaint — and the emitted
        candidate carries them into the Gate 1 post as the case."""
        source = (REPO / "plugins/product/skills/digest-conversation-signals/scripts"
                  / "digest_conversation_signals.py").read_text()
        collect = source.split("def collect(")[1].split("\ndef ")[0]
        appended = collect.split("d.examples.append")[0]
        guard = appended.rsplit("if rating ==", 1)
        self.assertEqual(len(guard), 2, "the append must sit under a rating guard")
        self.assertTrue(guard[1].lstrip().startswith('"Negative"'))

if __name__ == "__main__":
    unittest.main()
