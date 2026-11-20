"""Tests for `eng:report-aurie-daily-costs`' spike diagnosis.

The point of this suite is the branches live data does not reach. A real window
usually holds a batch running normally, so the two ways the classifier can be
*wrong* — contradicting itself on an off-level batch, and waving a day through
because the batch's own delta already accounts for it — never execute against
production. Both shipped and were caught in review; they are pinned here.

The load-bearing assertions are the negative ones: a day that carries a real
concurrent jump must NOT be reported as needing no root-cause pass. A test that
only checks the happy batch line passes against both defects.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import pathlib
import sys
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = REPO / "plugins/eng/skills/report-aurie-daily-costs/scripts/report.py"

BATCH = "proj_batch"
CHAT = "proj_chat"
NAMES = {BATCH: "Insights [Production]", CHAT: "Conversation [Production]"}


def load():
    spec = importlib.util.spec_from_file_location("aurie_daily_costs", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["aurie_daily_costs"] = mod
    spec.loader.exec_module(mod)
    return mod


mod = load()


def build_history(
    *,
    weeks: int = 5,
    batch_on_last_sunday: float = 100.0,
    chat_on_last_sunday: float = 150.0,
    chat_baseline: float = 150.0,
    batch_quiet: float = 12.0,
    batch_peak: float = 100.0,
):
    """A Sunday-batch history ending on a Sunday.

    Every day carries `chat_baseline` of chat cost; the batch project sits at
    `batch_quiet` except on Sundays, where it reaches `batch_peak`. The final
    Sunday is the day under test and takes its own two overrides.
    """
    end = dt.date(2026, 8, 16)                      # a Sunday
    assert end.weekday() == 6
    start = end - dt.timedelta(days=weeks * 7 - 1)
    totals, by_project = {}, {}
    d = start
    while d <= end:
        ds = d.isoformat()
        is_sun = d.weekday() == 6
        last = d == end
        batch = (
            batch_on_last_sunday if last
            else (batch_peak if is_sun else batch_quiet)
        )
        chat = chat_on_last_sunday if last else chat_baseline
        by_project[ds] = {BATCH: batch, CHAT: chat}
        totals[ds] = batch + chat
        d += dt.timedelta(days=1)
    return [end.isoformat()], totals, by_project, end.isoformat()


def diagnose(**kw):
    days_all, totals, by_project, target = build_history(**kw)
    # Diagnose over the whole span so the window median reflects normal days.
    window = sorted(totals)
    notes = mod.diagnose_spikes(window, totals, by_project, NAMES, None)
    return [n for n in notes if target in n]


class RecurringBatchRunningNormally(unittest.TestCase):
    def test_reported_as_routine_without_escalation(self):
        (note,) = diagnose()
        self.assertNotIn("⚠", note)
        self.assertIn("Recurring Sunday batch", note)
        self.assertIn("in range", note)
        self.assertIn("no root-cause pass needed", note)
        self.assertIn("Insights [Production]", note)


class BatchOffItsUsualLevel(unittest.TestCase):
    """The contradiction case: an off-level verdict must not close with 'expected'."""

    def test_escalates_and_does_not_contradict_itself(self):
        (note,) = diagnose(batch_on_last_sunday=600.0)
        self.assertIn("⚠", note)
        self.assertIn("off its usual level", note)
        # The defect: the line still ended "no root-cause pass needed unless ...".
        self.assertNotIn("no root-cause pass needed", note)
        self.assertIn("Worth a root-cause pass", note)


class ConcurrentJumpOnABatchDay(unittest.TestCase):
    """The masking case: a batch must only excuse the cost it accounts for."""

    def test_second_mover_is_named_and_the_day_escalates(self):
        # The batch is entirely normal, and chat rises by LESS than the batch's own
        # quiet-to-batch delta (+$60 vs +$88) — so the batch wins attribution and
        # would have absorbed the day. This is the shape that masks a real jump.
        (note,) = diagnose(chat_on_last_sunday=210.0)
        self.assertIn("⚠", note)
        self.assertIn("Conversation [Production]", note)
        self.assertIn("Setting the batch aside", note)
        self.assertNotIn("no root-cause pass needed", note)

    def test_batch_still_reported_as_in_range(self):
        # The batch itself is fine — the day escalates for the other project,
        # so the batch verdict must not be smeared into "off its usual level".
        (note,) = diagnose(chat_on_last_sunday=210.0)
        self.assertIn("in range", note)
        self.assertNotIn("off its usual level", note)


class ResidualElevatedWithNoSecondMover(unittest.TestCase):
    """An elevated residual must escalate even when no project is above its usual.

    Per-project deltas are measured against the lookback median while the bar is the
    window median, so after a traffic step-change every non-batch project can sit at or
    below its own usual while the day is still materially high without the batch.
    Gating escalation on "a second mover was found" suppresses exactly that case.
    """

    def _history(self):
        # Batch peaks at 200 on Sundays, 12 otherwise. Chat ran at 300 historically
        # and has since stepped down; the window only sees the low period, so the
        # window median is low while chat's own lookback median stays high.
        end = dt.date(2026, 8, 16)
        assert end.weekday() == 6
        totals, by_project = {}, {}
        for i in range(35):
            d = end - dt.timedelta(days=34 - i)
            ds = d.isoformat()
            batch = 200.0 if d.weekday() == 6 else 12.0
            in_window = (end - d).days <= 6
            chat = (250.0 if d == end else 150.0) if in_window else 300.0
            by_project[ds] = {BATCH: batch, CHAT: chat}
            totals[ds] = batch + chat
        window = [
            (end - dt.timedelta(days=n)).isoformat() for n in range(6, -1, -1)
        ]
        return window, totals, by_project, end.isoformat()

    def test_escalates_and_says_the_remainder_is_spread(self):
        window, totals, by_project, target = self._history()
        notes = mod.diagnose_spikes(window, totals, by_project, NAMES, None)
        (note,) = [n for n in notes if target in n]
        # Chat is BELOW its own lookback median, so there is no second mover to name.
        self.assertNotIn("also moved", note)
        # The defect: this closed as "Expected — no root-cause pass needed".
        self.assertIn("⚠", note)
        self.assertNotIn("no root-cause pass needed", note)
        self.assertIn("Setting the batch aside", note)
        self.assertIn("spread across projects", note)
        self.assertIn("still elevated with the batch set aside", note)

    def test_batch_verdict_is_untouched(self):
        window, totals, by_project, target = self._history()
        notes = mod.diagnose_spikes(window, totals, by_project, NAMES, None)
        (note,) = [n for n in notes if target in n]
        self.assertIn("in range", note)
        self.assertNotIn("off its usual level", note)


class NoRecurrence(unittest.TestCase):
    def test_single_occurrence_is_an_unexplained_jump(self):
        # Flat batch every day, then one spike on the final day: no same-weekday
        # history, so it must not be excused as a batch.
        days_all, totals, by_project, target = build_history(
            batch_peak=12.0, batch_on_last_sunday=400.0
        )
        notes = mod.diagnose_spikes(sorted(totals), totals, by_project, NAMES, None)
        (note,) = [n for n in notes if target in n]
        self.assertIn("⚠", note)
        self.assertIn("no recurring Sunday pattern", note)


class DiffuseDay(unittest.TestCase):
    def test_no_single_project_accounts_for_it(self):
        # Three projects rise together by similar amounts, so none clears the
        # concentration bar and the day cannot be pinned on one of them.
        end = dt.date(2026, 8, 16)
        totals, by_project = {}, {}
        for i in range(28):
            d = end - dt.timedelta(days=27 - i)
            ds = d.isoformat()
            last = d == end
            vals = {f"p{n}": (200.0 if last else 100.0) for n in range(3)}
            by_project[ds] = vals
            totals[ds] = sum(vals.values())
        names = {f"p{n}": f"P{n} [Production]" for n in range(3)}
        notes = mod.diagnose_spikes(sorted(totals), totals, by_project, names, None)
        (note,) = [n for n in notes if end.isoformat() in n]
        self.assertIn("⚠", note)
        self.assertIn("no single", note)


class QuietWindow(unittest.TestCase):
    def test_nothing_flagged_when_every_day_is_normal(self):
        days_all, totals, by_project, target = build_history(
            batch_peak=12.0, batch_on_last_sunday=12.0
        )
        notes = mod.diagnose_spikes(sorted(totals), totals, by_project, NAMES, None)
        self.assertEqual(notes, [])

    def test_partial_day_is_never_diagnosed(self):
        days_all, totals, by_project, target = build_history()
        notes = mod.diagnose_spikes(sorted(totals), totals, by_project, NAMES, target)
        self.assertEqual([n for n in notes if target in n], [])


class TooLittleHistory(unittest.TestCase):
    def test_declines_to_judge_under_three_graded_days(self):
        totals = {"2026-08-15": 100.0, "2026-08-16": 900.0}
        by_project = {d: {BATCH: v / 2, CHAT: v / 2} for d, v in totals.items()}
        self.assertEqual(
            mod.diagnose_spikes(sorted(totals), totals, by_project, NAMES, None), []
        )


if __name__ == "__main__":
    unittest.main()
