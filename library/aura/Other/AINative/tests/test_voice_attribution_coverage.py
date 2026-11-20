#!/usr/bin/env python3
"""Voice attribution coverage — the LLM guard's blind twin (ENG-1134).

The defect this guards
----------------------
ENG-1120 closed the per-user **LLM** attribution-collapse hole in
`eng:store-aurie-user-daily-cost`. All three guards it shipped read the LLM walk
and nothing else, and the decisive one counts *users carrying LLM cost* — so a
voice-only collapse moved none of them:

    assert_attribution_usable      -> llm_json_obj
    assert_attribution_coverage    -> llm_json_obj["attribution"]["coverage"]
    assert_user_count_plausible    -> sum(1 for v in llm.values() if v > 0)

The failure shape is identical to the LLM one, which is the point: the day still
reconciles to the cent because every unattributed voice dollar lands on the
store's `Other` reconciling row, nothing raises, and only the per-user
distribution is empty — which is exactly what the `chart-aurie-*` family reads.

Why the voice side has MORE ways to fail silently
-------------------------------------------------
Four of the five voice paths are allocated by `user_min / total_min` from the
LiveKit minute map (`livekit_minutes.day_minutes_by_user`). A GCP auth failure, a
renamed `logName`, or a change to the `TTS provider created:` log line yields an
empty map — while Cartesia's, Deepgram's and ElevenLabs' own billing APIs keep
returning perfectly good daily totals. So the denominator goes to zero while the
numerator stays real, and `allocate_by_minutes` returns `{}` for every one of
them without erroring.

The distinction the guard keys on is therefore NOT "did the log query fail" but
"is there vendor money with nobody to give it to":

    vendor total == 0  ->  genuinely quiet day        -> allowed
    vendor total  > 0, no minutes -> traffic happened, owner unknown -> refused

Failure injection is the point — a live day cannot produce an empty minute map on
demand, so every map, split and BigQuery baseline here is synthesised.
"""
import pathlib
import re
import sys
import unittest
from contextlib import redirect_stderr
import io

REPO = pathlib.Path(__file__).resolve().parents[1]

# Same stubbed-google loader trap as tests/test_attribution_coverage.py: store.py
# imports livekit_minutes, which does `from google.cloud import logging` at module
# scope and sys.exit(2) when the SDK is missing (CI installs only requests).
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_el_caller_mapping import _load_store  # noqa: E402

store = _load_store()


def _paths(**kw):
    """Build the `paths` arg: name -> (eligible_dollars, {uid: $})."""
    return {n: (e, m) for n, (e, m) in kw.items()}


# A healthy day's shape: ConvAI walked per-user, the four LiveKit-path vendors
# allocated over a populated minute map.
HEALTHY = _paths(
    l1labs_agent=(100.0, {"u1": 60.0, "u2": 40.0}),
    cartesia=(30.0, {"u1": 18.0, "u2": 12.0}),
    deepgram=(20.0, {"u1": 12.0, "u2": 8.0}),
    livekit=(50.0, {"u1": 30.0, "u2": 20.0}),
)


class ModuleLoadedFully(unittest.TestCase):
    """Names the cause when the loader half-loads, instead of N AttributeErrors."""

    def test_store_exposes_the_voice_guard(self):
        for name in ("summarize_voice_coverage", "assert_voice_coverage",
                     "assert_voice_user_count_plausible",
                     "MIN_VOICE_COVERAGE_PCT", "MIN_VOICE_USER_COUNT_RATIO"):
            self.assertTrue(hasattr(store, name), f"store.py missing {name}")


class CoverageSummary(unittest.TestCase):
    def test_healthy_day_is_complete(self):
        cov = store.summarize_voice_coverage(HEALTHY)
        self.assertEqual(cov["coverage_pct"], 100.0)
        self.assertEqual(cov["users_with_voice_cost"], 2)
        self.assertEqual(cov["collapsed_paths"], [])

    def test_empty_minute_map_collapses_every_livekit_path(self):
        """The ENG-1134 scenario: vendor bills arrive, the minute map does not."""
        cov = store.summarize_voice_coverage(_paths(
            l1labs_agent=(100.0, {"u1": 100.0}),   # ConvAI walk unaffected
            cartesia=(30.0, {}),                    # allocate_by_minutes -> {}
            deepgram=(20.0, {}),
            livekit=(50.0, {}),
        ))
        self.assertEqual(cov["collapsed_paths"], ["cartesia", "deepgram", "livekit"])
        self.assertAlmostEqual(cov["unreached_dollars"], 100.0, places=6)
        self.assertEqual(cov["coverage_pct"], 50.0)

    def test_quiet_day_is_complete_not_collapsed(self):
        """No voice traffic at all must not read as a failure."""
        cov = store.summarize_voice_coverage(_paths(
            l1labs_agent=(0.0, {}), cartesia=(0.0, {}),
            deepgram=(0.0, {}), livekit=(0.0, {}),
        ))
        self.assertEqual(cov["coverage_pct"], 100.0)
        self.assertEqual(cov["eligible_dollars"], 0.0)
        self.assertEqual(cov["collapsed_paths"], [])

    def test_zero_dollar_path_is_not_reported_as_collapsed(self):
        """A vendor with no spend is quiet, not broken — only money can collapse.

        Deepgram is LiveKit-path-only and legitimately bills $0 on a day with no
        LiveKit traffic, so flagging it would fire on ordinary days.
        """
        cov = store.summarize_voice_coverage(_paths(
            l1labs_agent=(100.0, {"u1": 100.0}), deepgram=(0.0, {}),
        ))
        self.assertEqual(cov["collapsed_paths"], [])
        self.assertEqual(cov["coverage_pct"], 100.0)

    def test_unattributable_slice_stays_out_of_the_denominator(self):
        """Intelligence Service TTS has no per-user signal — by design, not failure.

        Counting it as uncovered would make every ordinary day look partly
        collapsed, which is the false-refusal the LLM guard avoids by excluding
        skipped projects.
        """
        cov = store.summarize_voice_coverage(HEALTHY, unattributable_dollars=500.0)
        self.assertEqual(cov["coverage_pct"], 100.0)
        self.assertEqual(cov["unattributable_dollars"], 500.0)
        self.assertNotIn(500.0, [cov["eligible_dollars"], cov["unreached_dollars"]])

    def test_users_counted_once_across_paths(self):
        cov = store.summarize_voice_coverage(HEALTHY)
        self.assertEqual(cov["users_with_voice_cost"], 2)

    def test_zero_dollar_user_does_not_count_as_reached(self):
        cov = store.summarize_voice_coverage(_paths(
            livekit=(10.0, {"u1": 10.0, "u2": 0.0}),
        ))
        self.assertEqual(cov["users_with_voice_cost"], 1)


class CoverageRefusal(unittest.TestCase):
    def test_total_collapse_refuses(self):
        cov = store.summarize_voice_coverage(_paths(
            cartesia=(30.0, {}), deepgram=(20.0, {}), livekit=(50.0, {}),
        ))
        with self.assertRaises(SystemExit) as ctx, redirect_stderr(io.StringIO()):
            store.assert_voice_coverage(cov)
        msg = str(ctx.exception)
        self.assertIn("REFUSING to store", msg)
        self.assertIn("cartesia", msg)
        # The message must name the mechanism, not just the number — the whole
        # point is that the operator can act on it.
        self.assertIn("LiveKit minute map", msg)

    def test_healthy_day_passes(self):
        with redirect_stderr(io.StringIO()):
            store.assert_voice_coverage(store.summarize_voice_coverage(HEALTHY))

    def test_quiet_day_passes(self):
        with redirect_stderr(io.StringIO()):
            store.assert_voice_coverage(store.summarize_voice_coverage(
                _paths(livekit=(0.0, {}))))

    def test_allow_low_coverage_persists_with_a_note(self):
        cov = store.summarize_voice_coverage(_paths(livekit=(50.0, {})))
        err = io.StringIO()
        with redirect_stderr(err):
            store.assert_voice_coverage(cov, allow_low_coverage=True)
        self.assertIn("--allow-low-coverage", err.getvalue())

    def test_partial_path_loss_is_reported_even_when_the_blend_passes(self):
        """One dead vendor inside a healthy day is what a single ratio hides."""
        cov = store.summarize_voice_coverage(_paths(
            l1labs_agent=(1000.0, {"u1": 1000.0}),   # dominates the blend
            cartesia=(30.0, {}),                      # silently dead
        ))
        self.assertGreater(cov["coverage_pct"], store.MIN_VOICE_COVERAGE_PCT)
        err = io.StringIO()
        with redirect_stderr(err):
            store.assert_voice_coverage(cov)          # must NOT raise
        self.assertIn("cartesia", err.getvalue())

    def test_collapsed_path_is_stored_with_a_warning_naming_its_dollars(self):
        """2026-09-24: ElevenLabs agent billed $33.10 to nobody while LiveKit
        carried the blend. Store and flag (PROD-6992) — a WARNING with the
        amount, not a refusal and not a bare note."""
        # The stored 2026-09-24 column totals: agent reached nobody, every LiveKit-path
        # vendor was attributed in full.
        cov = store.summarize_voice_coverage(_paths(
            l1labs_agent=(33.10, {}),
            l1labs_livekit=(200.68, {"u1": 120.0, "u2": 80.68}),
            deepgram=(18.39, {"u1": 11.0, "u2": 7.39}),
            livekit=(60.74, {"u1": 40.0, "u2": 20.74}),
        ))
        self.assertGreater(cov["coverage_pct"], store.MIN_VOICE_COVERAGE_PCT)
        err = io.StringIO()
        with redirect_stderr(err):
            store.assert_voice_coverage(cov)          # must NOT raise
        self.assertIn("WARNING", err.getvalue())
        self.assertIn("$33.10", err.getvalue())

    def test_threshold_separates_the_observed_healthy_floor_from_a_collapse(self):
        """97.9% was the worst healthy day across the 70 held on 2026-08-10."""
        self.assertLess(store.MIN_VOICE_COVERAGE_PCT, 97.9)
        self.assertGreater(store.MIN_VOICE_COVERAGE_PCT, 0.0)


class _FakeBQ:
    """Stands in for `bq.query`, so the baseline is under the test's control."""

    def __init__(self, counts=None, raise_with=None):
        self.counts, self.raise_with = counts or [], raise_with

    def __call__(self, sql, project, params=None):
        if self.raise_with:
            raise self.raise_with
        self.sql = sql
        return [{"users": c} for c in self.counts]


class UserCountPlausibility(unittest.TestCase):
    """The distribution half — a PARTIAL minute map keeps coverage healthy."""

    def setUp(self):
        self._real = sys.modules.get("bq")
        import types
        self.fake = types.ModuleType("bq")
        sys.modules["bq"] = self.fake

    def tearDown(self):
        if self._real is not None:
            sys.modules["bq"] = self._real
        else:
            sys.modules.pop("bq", None)

    def test_partial_collapse_refuses(self):
        """The voice analogue of 2026-06-23: survivors absorb every vendor dollar."""
        self.fake.query = _FakeBQ([1000] * 10)
        with self.assertRaises(SystemExit) as ctx, redirect_stderr(io.StringIO()):
            store.assert_voice_user_count_plausible(
                "2026-06-24", 100, "t", "p", eligible_dollars=100.0)
        self.assertIn("REFUSING to store", str(ctx.exception))
        self.assertIn("10.0x", str(ctx.exception))

    def test_total_collapse_uses_the_zero_safe_message(self):
        """`1/ratio` is undefined at exactly the case the guard exists for."""
        self.fake.query = _FakeBQ([1000] * 10)
        with self.assertRaises(SystemExit) as ctx, redirect_stderr(io.StringIO()):
            store.assert_voice_user_count_plausible(
                "2026-06-24", 0, "t", "p", eligible_dollars=100.0)
        self.assertIn("No user carries any voice cost", str(ctx.exception))

    def test_healthy_count_passes(self):
        self.fake.query = _FakeBQ([1000] * 10)
        store.assert_voice_user_count_plausible(
            "2026-06-24", 950, "t", "p", eligible_dollars=100.0)

    def test_quiet_day_skips_the_check(self):
        """Zero attributable voice spend means zero users is the right answer."""
        self.fake.query = _FakeBQ(raise_with=AssertionError("must not query"))
        store.assert_voice_user_count_plausible(
            "2026-06-24", 0, "t", "p", eligible_dollars=0.0)

    def test_thin_history_skips_rather_than_refusing(self):
        """A first backfill has nothing to compare against; refusing would block seeding."""
        self.fake.query = _FakeBQ([1000] * 3)
        err = io.StringIO()
        with redirect_stderr(err):
            store.assert_voice_user_count_plausible(
                "2026-06-24", 0, "t", "p", eligible_dollars=100.0)
        self.assertIn("skipping", err.getvalue())

    def test_allow_low_persists(self):
        self.fake.query = _FakeBQ([1000] * 10)
        store.assert_voice_user_count_plausible(
            "2026-06-24", 0, "t", "p", eligible_dollars=100.0, allow_low=True)

    def test_baseline_counts_voice_users_not_llm_users(self):
        """The bug being fixed: the LLM guard's count cannot see a voice collapse."""
        self.fake.query = _FakeBQ([1000] * 10)
        store.assert_voice_user_count_plausible(
            "2026-06-24", 950, "t", "p", eligible_dollars=100.0)
        self.assertIn("voice_cost > 0", self.fake.query.sql)
        self.assertNotIn("llm_cost", self.fake.query.sql)


class LiveKitPoolOverage(unittest.TestCase):
    """ENG-1705: the LiveKit path allocates `day_livekit + day_livekit_pools`, so its
    eligible dollars must include the pool overage too. With only `day_livekit` as the
    denominator, 2026-10-27/28 read `livekit:162%` ((118.33 + 73.37) / 118.33), and a
    path that lost half its minutes would read 81%: above the 75% floor, so hidden."""

    DAY, POOLS = 118.33, 73.37

    def coverage(self, minutes_by_user, total_min):
        livekit = store.allocate_by_minutes(self.DAY + self.POOLS, minutes_by_user, total_min)
        return store.summarize_voice_coverage(
            _paths(livekit=(self.DAY + self.POOLS, livekit)))

    def test_coverage_never_exceeds_100_with_pool_overage(self):
        cov = self.coverage({"a": 60.0, "b": 40.0}, 100.0)
        self.assertEqual(cov["paths"]["livekit"]["coverage_pct"], 100.0)
        self.assertLessEqual(cov["coverage_pct"], 100.0)

    def test_a_partial_attribution_drop_still_shows(self):
        # Half the day's minutes carry no user: coverage must read 50% and refuse.
        cov = self.coverage({"a": 30.0, "b": 20.0}, 100.0)
        self.assertEqual(cov["paths"]["livekit"]["coverage_pct"], 50.0)
        with self.assertRaises(SystemExit):
            store.assert_voice_coverage(cov)

    def test_the_store_passes_what_it_allocates(self):
        src = (REPO / "plugins/eng/skills/store-aurie-user-daily-cost/scripts/store.py").read_text(encoding="utf-8")
        allocated = re.search(r"livekit = allocate_by_minutes\((.+?), lk_min_by_user", src).group(1)
        eligible = re.search(r'"livekit": \((.+?), livekit\)', src).group(1)
        self.assertEqual(eligible, allocated)


if __name__ == "__main__":
    unittest.main()
