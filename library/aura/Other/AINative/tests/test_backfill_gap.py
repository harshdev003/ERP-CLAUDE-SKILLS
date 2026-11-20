#!/usr/bin/env python3
"""Which rating represents a conversation decides how many there are to backfill.

**The rule.** `Signals/Conversation Patterns` holds one row per rating *event*, so a
conversation rated twice has two rows — and the ratings can disagree: 207 of 7,231 carry
more than one polarity (35 Negative+Positive, 10 Negative+Neutral, 9 all three, measured
2026-09-16). Collapsing to one entry per conversation therefore requires choosing a
representative, and the choice changes which conversations `--polarity Negative` selects.

**Why a test.** Picking by arrival order produces a number that *looks* like a fact and is
not reproducible: the same table answered 383 and 388 on two passes that differed only in
which row was seen first. Nothing errors — a backfill just silently works through a
different set than it reports. The rule is therefore stated (`rank`, worst polarity first)
and pinned here, so `--polarity Negative` provably means *received at least one negative
rating*.

Stdlib `unittest`, no network — `rank` is pure and module-level for exactly that reason.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
import unittest
import uuid

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
GAP = REPO_ROOT / "plugins/data/skills/backfill-learnings-conversations/scripts/gap.py"


def load():
    name = f"_gap_{uuid.uuid4().hex}"
    saved = list(sys.path)
    try:
        sys.path.insert(0, str(GAP.parent))
        spec = importlib.util.spec_from_file_location(name, GAP)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path[:] = saved


def entry(rating, feedback="", date="2026-07-01"):
    return {"rating": rating, "feedback": feedback, "date": date}


class TestRepresentativeRating(unittest.TestCase):
    def setUp(self):
        self.rank = load().rank

    def test_worst_polarity_wins(self):
        """A later thumbs-up does not retract the earlier complaint."""
        neg = entry("Negative", date="2026-07-01")
        pos = entry("Positive", date="2026-07-02")
        self.assertLess(self.rank(neg), self.rank(pos))
        self.assertLess(self.rank(entry("Neutral")), self.rank(entry("Positive")))
        self.assertLess(self.rank(entry("Negative")), self.rank(entry("Neutral")))

    def test_polarity_beats_recency(self):
        """The ordering must not be decided by date when polarities differ.

        A Positive rated first and a Negative rated later is the 35-conversation
        Negative+Positive case; ranking by date first would classify it Positive and
        drop it out of the backfill set entirely.
        """
        early_pos = entry("Positive", date="2026-01-01")
        late_neg = entry("Negative", date="2026-12-31")
        self.assertLess(self.rank(late_neg), self.rank(early_pos))

    def test_polarity_beats_feedback(self):
        """Feedback is a tiebreak within a polarity, never across one."""
        pos_with_text = entry("Positive", feedback="loved it")
        neg_silent = entry("Negative", feedback="")
        self.assertLess(self.rank(neg_silent), self.rank(pos_with_text))

    def test_feedback_breaks_a_tie_within_one_polarity(self):
        with_text = entry("Negative", feedback="it missed the point")
        without = entry("Negative", feedback="")
        self.assertLess(self.rank(with_text), self.rank(without))

    def test_earliest_breaks_the_remaining_tie(self):
        early = entry("Negative", feedback="x", date="2026-06-01")
        late = entry("Negative", feedback="x", date="2026-06-09")
        self.assertLess(self.rank(early), self.rank(late))

    def test_ordering_is_total_and_arrival_order_cannot_change_the_winner(self):
        """The whole point: the same set must reduce to the same entry either way."""
        rows = [
            entry("Positive", feedback="great", date="2026-05-01"),
            entry("Negative", date="2026-08-01"),
            entry("Neutral", feedback="meh", date="2026-04-01"),
        ]
        best_fwd = min(rows, key=self.rank)
        best_rev = min(list(reversed(rows)), key=self.rank)
        self.assertEqual(best_fwd, best_rev)
        self.assertEqual(best_fwd["rating"], "Negative")

    def test_an_unknown_polarity_never_outranks_a_real_one(self):
        """A new option in the Notion select must not silently win the representative."""
        self.assertLess(self.rank(entry("Positive")), self.rank(entry("Surprising")))
        self.assertLess(self.rank(entry("Negative")), self.rank(entry("")))

    def test_a_missing_date_sorts_last_rather_than_first(self):
        """An empty date must not masquerade as the earliest rating."""
        dated = entry("Negative", feedback="x", date="2026-06-01")
        undated = entry("Negative", feedback="x", date="")
        self.assertLess(self.rank(dated), self.rank(undated))


class TestScriptShape(unittest.TestCase):
    def test_severity_covers_every_polarity_the_table_allows(self):
        mod = load()
        self.assertEqual(set(mod.SEVERITY), set(mod.POLARITIES))

    def test_stdout_is_forced_to_utf8(self):
        """Markdown arrows and em-dashes in the summary crash cp1252 consoles."""
        self.assertIn("reconfigure(encoding=\"utf-8\")", GAP.read_text(encoding="utf-8"))


class TestSessionTypeScope(unittest.TestCase):
    """Only four shapes carry an id the conversation lookup can resolve.

    `Conversation ID` is the id of whatever was rated: `Journal` holds a journal entry
    id and `Profile` a `rendererRating` row id, both UUIDs from other systems. They are
    not merely likely to fail — they cannot resolve, and since this skill keeps no state
    the same ids would return in every chunk forever.
    """

    def setUp(self):
        self.mod = load()

    def test_journal_and_profile_are_not_conversation_shapes(self):
        self.assertNotIn("Journal", self.mod.CONVERSATION_SHAPES)
        self.assertNotIn("Profile", self.mod.CONVERSATION_SHAPES)

    def test_every_conversation_bearing_shape_is_included(self):
        for shape in ("Conversation", "Chat", "Quick Processing", "Coaching"):
            self.assertIn(shape, self.mod.CONVERSATION_SHAPES)

    def test_the_default_is_the_conversation_shapes_not_everything(self):
        """A default of 'all' would queue ids that can never resolve."""
        src = GAP.read_text(encoding="utf-8")
        self.assertIn('default=",".join(CONVERSATION_SHAPES)', src)

    def test_excluded_rows_are_counted_in_the_payload(self):
        """Tag, don't drop: an omitted row must still be visible as a number."""
        src = GAP.read_text(encoding="utf-8")
        self.assertIn("excluded_non_conversation", src)
        self.assertIn("excluded_shape[e[\"session_type\"]] += 1", src)

    def test_the_shapes_are_a_subset_of_what_the_table_allows(self):
        """A typo here silently empties the gap instead of erroring."""
        allowed = {"Quick Processing", "Coaching", "Conversation", "Chat", "Journal", "Profile"}
        self.assertTrue(set(self.mod.CONVERSATION_SHAPES) <= allowed)



if __name__ == "__main__":
    unittest.main()
