#!/usr/bin/env python3
"""Rating volume annotates a UID; it never drops one, and the answer doesn't move.

Two rules, one per class of defect this family has already shipped.

**Volume is not a test signal.** It tracks engagement, so the heaviest raters in any
window are the most engaged real accounts — a volume drop removes real users in
proportion to how much they use the product. ENG-1048 settled this and changed
`fetch_ratings.py` to report (`high_volume=True`) rather than drop.

**The verdict must not depend on the pass's width.** `fetch_sweep_analyses.py` — the
writer for `Signals/Conversation Patterns` — counted its threshold over the *harvest
window*, so widening the sweep raised every count while the bar stayed put. On
2026-06-01..08-10 one 70-day pass dropped 2,673 of 4,972 rated messages where eleven
7-day passes over the same range dropped 1,855: **817 real ratings existed in one shape
of the harvest and not the other**, same data, same filter. ENG-1132.

**Why these need a test, and why the `diff -q` guards could not catch it.** Five files
in `plugins/` encode a rating-volume rule; the CLAUDE.md canonical-source check for
`fetch_ratings.py` inspects three, and reported no drift the entire time — all three
agreed perfectly at 20, report-only. The fourth was a *reimplementation of the same rule
under a different filename*, which a filename-keyed byte-diff is blind to by
construction. So the assertion has to be behavioural and family-wide: give every
implementation the same prolific UID and require the same answer.

The load-bearing assertions here are the **negative** ones — nothing may be removed, and
no count may change when the pass widens. A drop is a silent absence: the run prints the
same shape of success, every row it does write is correct, and the loss is invisible
unless you already know the number you should have seen.

Stdlib `unittest`, no network, no keys. The scripts are imported with their Slack/HTTP
transports untouched but never called — only pure parse/annotate functions run.
"""
from __future__ import annotations

import importlib.util
import pathlib
import re
import sys
import unittest
import uuid

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
PLUGINS = REPO_ROOT / "plugins"

HARVEST = PLUGINS / "data/skills/harvest-conversation-patterns/scripts/fetch_sweep_analyses.py"

# Every file in `plugins/` that decides what a prolific rater means. Discovered by
# behaviour, not by filename — see the module docstring. Keep this list in step with
# `test_family_membership_is_complete` below, which fails if a new one appears.
ANNOTATORS = [
    PLUGINS / "data/skills/investigate-user-rating/scripts/fetch_ratings.py",
    PLUGINS / "data/skills/report-conversation-patterns/scripts/fetch_ratings.py",
    PLUGINS / "data/skills/run-feedback-analysis-cadence/scripts/fetch_ratings.py",
    PLUGINS / "data/skills/chart-monthly-ratings/scripts/chart_ratings.py",
]

# Deliberately OUT of this family: the cancellation parsers' `TEST_BLAST_THRESHOLD`
# (3 cancellations from one UID in-window → dropped as a QA loop). It is the same
# *shape* of rule and it is also window-relative, but it is not the same *decision*:
# a user cannot legitimately cancel three times in a week, so there volume really does
# say something about the account, where for ratings it only says the user is engaged.
# Excluded with a reason rather than silently un-matched — an unexplained new hit must
# still fail `test_family_membership_is_complete`. (Its window-relativity is a real
# but separate issue; ENG-1132 does not touch it.)
NOT_RATING_VOLUME = {
    PLUGINS / "data/skills/investigate-cancellation-feedback/scripts/fetch_cancellations.py",
    PLUGINS / "data/skills/harvest-cancellation-feedback/scripts/fetch_cancellations.py",
}

DAY = 86400
BASE_TS = 1_700_000_000  # fixed epoch — no clock reads, so the suite can't flake


def load(path: pathlib.Path):
    """Import a skill script under a unique name, with its own dir on sys.path.

    Unique name + wholesale sys.path restore, same reasoning as `test_rating_grain`:
    these scripts import siblings from their own directory, and a leaked path entry
    would let a later load resolve against an earlier script's folder.
    """
    name = f"_vol_{uuid.uuid4().hex}"
    saved = list(sys.path)
    try:
        sys.path.insert(0, str(path.parent))
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path[:] = saved


class AnnotateNeverDrops(unittest.TestCase):
    """Every annotator keeps a prolific UID's entries and merely flags them."""

    def test_prolific_uid_is_flagged_and_kept(self):
        for path in ANNOTATORS:
            with self.subTest(script=str(path.relative_to(REPO_ROOT))):
                mod = load(path)
                n = mod.HIGH_VOLUME_UID_THRESHOLD + 5
                entries = [
                    {"uid": "heavyUser000000000000000000AA", "polarity": "negative"}
                    for _ in range(n)
                ]
                mod.apply_test_filter(entries)

                # Negative assertion first: the list is untouched in length, and no
                # entry acquired a test verdict on volume grounds.
                self.assertEqual(len(entries), n, "entries were dropped")
                self.assertTrue(
                    all(not e["is_test"] for e in entries),
                    "volume marked a real account as a test account",
                )
                self.assertTrue(
                    all(not e["test_reason"] for e in entries),
                    f"volume leaked into test_reason: {entries[0]['test_reason']!r}",
                )
                # …and the annotation the drop was replaced by is actually set.
                self.assertTrue(all(e["high_volume"] for e in entries))

    def test_threshold_is_not_tighter_than_the_decision(self):
        """No annotator may re-tighten below ENG-1048's bar of 20.

        The harvest parser sat at 5 — a 4x tighter bar, applied destructively — which
        is how the same question got two answers inside one pipeline.
        """
        for path in ANNOTATORS + [HARVEST]:
            with self.subTest(script=str(path.relative_to(REPO_ROOT))):
                mod = load(path)
                self.assertGreaterEqual(mod.HIGH_VOLUME_UID_THRESHOLD, 20)


class HarvestVerdictIsWindowIndependent(unittest.TestCase):
    """The writer's volume verdict must not move with the harvest pass's width."""

    def setUp(self):
        self.mod = load(HARVEST)

    @staticmethod
    def _ratings(uid: str, count: int, step: float, start: float = BASE_TS):
        return [{"uid": uid, "ts": str(start + i * step)} for i in range(count)]

    def test_same_behaviour_same_verdict_regardless_of_pass_width(self):
        """A burst's verdict is identical whether the pass is 7 days or 70."""
        burst = self._ratings("burstUid00000000000000000AAA", 25, 600)  # 25 in ~4h

        narrow = self.mod.peak_span_counts(burst)
        # The same burst inside a 70-day pass that also carries 60 days of sparse
        # ratings from other accounts — the exact shape of a wide re-harvest.
        wide = self.mod.peak_span_counts(
            burst + self._ratings("otherUid00000000000000000BBB", 60, DAY)
        )

        self.assertEqual(narrow["burstUid00000000000000000AAA"], 25)
        self.assertEqual(
            wide["burstUid00000000000000000AAA"],
            narrow["burstUid00000000000000000AAA"],
            "widening the pass changed a UID's volume verdict",
        )

    def test_a_steady_real_user_is_not_flagged_by_a_wide_pass(self):
        """The 817-rating defect, reduced to its mechanism.

        30 ratings spread over 60 days is ~0.4/day — an engaged user, not a test loop.
        A window-relative total flags them the moment the pass is wide enough to
        accumulate 30; a fixed rolling span never does.
        """
        steady = self._ratings("steadyUid0000000000000000CCC", 30, 2 * DAY)
        peak = self.mod.peak_span_counts(steady)["steadyUid0000000000000000CCC"]

        self.assertLess(peak, self.mod.HIGH_VOLUME_UID_THRESHOLD)
        self.assertLessEqual(peak, 4, f"rolling-7d peak should be ~4, got {peak}")

    def test_span_is_seven_days_not_the_window(self):
        """Ratings exactly one span apart never share a span."""
        mod = self.mod
        span = mod.HIGH_VOLUME_SPAN_SECONDS
        self.assertEqual(span, 7 * DAY)

        # 20 ratings, each a full span + 1s after the last: peak is 1, not 20.
        sparse = self._ratings("sparseUid0000000000000000DDD", 20, span + 1)
        self.assertEqual(mod.peak_span_counts(sparse)["sparseUid0000000000000000DDD"], 1)

    def test_entries_without_a_uid_are_simply_absent(self):
        counts = self.mod.peak_span_counts(
            [{"uid": "", "ts": str(BASE_TS)}, {"ts": str(BASE_TS)}]
        )
        self.assertEqual(counts, {})


class NoDestructiveVolumeFilterRemains(unittest.TestCase):
    """Family-wide structural guard for the reimplementation-under-another-name case."""

    def _volume_rule_files(self):
        hits = []
        for path in sorted(PLUGINS.rglob("*.py")):
            if "__pycache__" in path.parts or path in NOT_RATING_VOLUME:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if "HIGH_VOLUME_UID_THRESHOLD" in text or "BLAST_THRESHOLD" in text:
                hits.append(path)
        return hits

    def test_family_membership_is_complete(self):
        """A new rating-volume rule must be added to ANNOTATORS, not left unguarded.

        This is the assertion the CLAUDE.md `diff -q` checks structurally cannot make:
        it looks for the *rule*, wherever it lives and whatever the file is called.
        """
        found = set(self._volume_rule_files())
        declared = set(ANNOTATORS) | {HARVEST}
        undeclared = {p.relative_to(REPO_ROOT) for p in found - declared}
        self.assertEqual(
            undeclared,
            set(),
            "a rating-volume rule exists outside the tested family — add it to "
            "ANNOTATORS (and to CLAUDE.md) rather than leaving it unguarded",
        )
        self.assertEqual({p.relative_to(REPO_ROOT) for p in declared - found}, set())

    def test_no_blast_style_constant_survives(self):
        """`UID_BLAST_THRESHOLD` was the destructive rule's name. It must stay gone."""
        for path in self._volume_rule_files():
            with self.subTest(script=str(path.relative_to(REPO_ROOT))):
                text = path.read_text(encoding="utf-8")
                assignment = re.search(r"^\s*\w*BLAST_THRESHOLD\s*=", text, re.M)
                self.assertIsNone(
                    assignment,
                    "a *BLAST_THRESHOLD constant is assigned again — volume is "
                    "reported, not dropped (ENG-1048 / ENG-1132)",
                )


if __name__ == "__main__":
    unittest.main()
