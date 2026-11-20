"""Signal breach, materiality and priors for data:sweep-recommender.

The sweep's failure mode is not missing a signal — it is filling the loop
channel with marginal ones until nobody reads it. These tests are weighted to
the cases where a plausible candidate must be held back, and to the one case
where suppression would be wrong.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(__file__), "..", "plugins", "data", "skills",
    "sweep-recommender", "scripts"))

import signals  # noqa: E402


def cand(**kw):
    base = dict(
        title="Rank the Today row by similarity to the last completed track",
        description="d",
        layer="ranking",
        mechanism="order the row by embedding similarity to what the user just finished",
        source="harness",
    )
    base.update(kw)
    return signals.Candidate(**base)


class TestSignal(unittest.TestCase):
    def test_direction_decides_what_counts_as_a_breach(self):
        hi = signals.Signal(key="empty_rate", layer="ranking", value=12.0,
                            threshold=10.0, direction="above")
        lo = signals.Signal(key="coverage", layer="candidate supply", value=0.21,
                            threshold=0.30, direction="below")
        self.assertTrue(hi.breached)
        self.assertTrue(lo.breached)

    def test_a_wrong_direction_is_rejected_at_construction(self):
        with self.assertRaises(ValueError):
            signals.Signal(key="x", layer="ranking", value=1, threshold=0, direction="up")

    def test_an_unknown_layer_is_rejected(self):
        # The layer is the rotation key and the Notion Domain value; a typo
        # would silently create a row nothing rotates over.
        with self.assertRaises(ValueError):
            signals.Signal(key="x", layer="home/feed", value=1, threshold=0, direction="above")

    def test_a_steady_breach_is_not_news(self):
        held = signals.Signal(key="empty", layer="ranking", value=100.0,
                              threshold=10.0, direction="above", prior=100.0)
        self.assertTrue(held.breached)
        self.assertFalse(held.moved)

    def test_a_first_run_with_no_prior_treats_every_breach_as_news(self):
        fresh = signals.Signal(key="empty", layer="ranking", value=100.0,
                               threshold=10.0, direction="above", prior=None)
        self.assertTrue(fresh.moved)

    def test_a_prior_of_zero_does_not_divide_by_zero(self):
        s = signals.Signal(key="empty", layer="ranking", value=5.0, threshold=1.0,
                           direction="above", prior=0.0)
        self.assertTrue(s.moved)


class TestMateriality(unittest.TestCase):
    def test_a_hair_above_the_bar_is_not_material(self):
        ok, why = signals.is_material(cand(offline_ratio=1.004))
        self.assertFalse(ok)
        self.assertIn("materiality margin", why)

    def test_a_clear_win_with_a_mechanism_is_material(self):
        ok, _why = signals.is_material(cand(offline_ratio=1.31))
        self.assertTrue(ok)

    def test_a_win_with_no_mechanism_is_held_back(self):
        ok, why = signals.is_material(cand(offline_ratio=1.31, mechanism=""))
        self.assertFalse(ok)
        self.assertIn("no mechanism", why)

    def test_grid_search_phrasing_is_held_back(self):
        # This is the noise the filter exists for: a real offline win whose
        # only description is that a number changed.
        ok, why = signals.is_material(
            cand(offline_ratio=1.31, mechanism="bump the weight from 10000 to 12000"))
        self.assertFalse(ok)
        self.assertIn("tuning", why)

    def test_the_same_phrasing_from_telemetry_is_not_treated_as_tuning(self):
        # A telemetry finding legitimately reports that a constant is wrong —
        # the tuning heuristic is aimed at harness grid output, not at a
        # measured production defect.
        ok, _why = signals.is_material(
            cand(source="telemetry", offline_ratio=None,
                 mechanism="bump the weight so the model stops being ignored"))
        self.assertTrue(ok)

    def test_a_telemetry_candidate_with_no_offline_score_can_still_pass(self):
        # Instrumentation and filter changes cannot be replayed, so a missing
        # ratio must not be read as failing the bar.
        ok, _why = signals.is_material(
            cand(source="telemetry", offline_ratio=None, layer="instrumentation",
                 mechanism="log the ordered ids so position bias can be corrected"))
        self.assertTrue(ok)

    def test_a_two_word_mechanism_is_too_thin(self):
        ok, why = signals.is_material(cand(offline_ratio=1.31, mechanism="better ranking"))
        self.assertFalse(ok)
        self.assertIn("too thin", why)


class TestPriors(unittest.TestCase):
    ROWS = [
        {"title": "Rank the Today row by similarity to the last completed track",
         "domain": "ranking", "status": "rejected", "rejection_reason": "already tested"},
        {"title": "Cap exposure per coach", "domain": "ranking",
         "status": "rejected", "rejection_reason": "timing"},
        {"title": "Log impressions", "domain": "instrumentation",
         "status": "approved", "rejection_reason": ""},
    ]

    def test_a_permanently_retired_idea_is_suppressed(self):
        kept, supp = signals.check_priors([cand()], self.ROWS)
        self.assertEqual(kept, [])
        self.assertIn("already answered", supp[0][1])

    def test_a_timing_rejection_is_re_proposable(self):
        # The fixed rejection vocabulary exists precisely so 'timing' and
        # 'tech cost' come back. Suppressing them would silently retire them.
        c = cand(title="Cap exposure per coach")
        kept, supp = signals.check_priors([c], self.ROWS)
        self.assertEqual(kept, [c])
        self.assertEqual(supp, [])

    def test_an_idea_already_in_the_backlog_is_suppressed(self):
        c = cand(title="Log impressions", layer="instrumentation")
        kept, supp = signals.check_priors([c], self.ROWS)
        self.assertEqual(kept, [])
        self.assertIn("already in the backlog", supp[0][1])

    def test_the_same_title_in_a_different_layer_is_a_different_idea(self):
        c = cand(title="Log impressions", layer="ranking")
        kept, _supp = signals.check_priors([c], self.ROWS)
        self.assertEqual(kept, [c])

    def test_matching_ignores_case_and_spacing(self):
        c = cand(title="  RANK the Today   row by similarity to the last completed track ")
        kept, _supp = signals.check_priors([c], self.ROWS)
        self.assertEqual(kept, [])

    def test_an_empty_backlog_keeps_everything(self):
        c = cand()
        kept, supp = signals.check_priors([c], [])
        self.assertEqual(kept, [c])
        self.assertEqual(supp, [])


class TestRotation(unittest.TestCase):
    def test_a_never_swept_layer_comes_first(self):
        seen = {layer: "2026-09-01" for layer in signals.LAYERS if layer != "filters"}
        self.assertEqual(signals.next_layer(seen), "filters")

    def test_otherwise_the_oldest_layer_wins(self):
        seen = {layer: "2026-09-20" for layer in signals.LAYERS}
        seen["blend weights"] = "2026-06-01"
        self.assertEqual(signals.next_layer(seen), "blend weights")

    def test_it_always_returns_a_known_layer(self):
        self.assertIn(signals.next_layer({}), signals.LAYERS)


class TestIdeaShape(unittest.TestCase):
    def test_surface_maps_to_screen_and_layer_to_domain(self):
        # The shared loop schema reused with this loop's vocabulary: Screen is
        # the surface from sent_from, Domain is the pipeline layer.
        idea = cand(surface="todayRow", layer="ranking").to_idea()
        self.assertEqual(idea["screen"], "todayRow")
        self.assertEqual(idea["domain"], "ranking")

    def test_evidence_becomes_the_rationale_when_none_is_given(self):
        idea = cand(evidence="activation-model returned 0 results in 5,290 calls/h").to_idea()
        self.assertIn("activation-model", idea["rationale"])

    def test_every_key_record_ideas_reads_is_present(self):
        idea = cand().to_idea()
        for key in ("title", "description", "screen", "domain", "category",
                    "lever", "impact", "effort", "rationale", "hypothesis"):
            self.assertIn(key, idea)



class CollectorRobustness(unittest.TestCase):
    """Two failure modes that would have made the sweep lie rather than stop."""

    def setUp(self):
        import importlib
        self.sr = importlib.import_module("sweep_recommender")

    def test_a_saturated_log_window_refuses_rather_than_returning_a_bound(self):
        # activation-model runs ~5,290/h, so 24h is ~127k entries against a 20k
        # cap. Dividing the capped total gave a stuck ~833/h: wrong magnitude in
        # the evidence, and a `moved` check comparing two truncated numbers
        # would conclude nothing had changed while it deteriorated.
        calls = []

        class FakeGCP:
            @staticmethod
            def get_client():
                return object()

            @staticmethod
            def list_entries(auth, flt, max_entries):
                calls.append(max_entries)
                return range(max_entries)  # always saturates

        sys.modules["gcp"] = FakeGCP
        try:
            with self.assertRaises(RuntimeError) as ctx:
                self.sr._log_count_per_hour("No results from this model")
            self.assertIn("lower bound", str(ctx.exception))
            self.assertEqual(len(calls), len(self.sr.LOG_WINDOWS_HOURS))
        finally:
            del sys.modules["gcp"]

    def test_it_narrows_the_window_until_the_count_is_real(self):
        state = {"n": 0}

        class FakeGCP:
            @staticmethod
            def get_client():
                return object()

            @staticmethod
            def list_entries(auth, flt, max_entries):
                state["n"] += 1
                # saturate the 24h read, return a real count at 6h
                return range(max_entries if state["n"] == 1 else 600)

        sys.modules["gcp"] = FakeGCP
        try:
            rate = self.sr._log_count_per_hour("No recommendation")
            self.assertEqual(rate, 100.0)  # 600 over the 6h window
        finally:
            del sys.modules["gcp"]

    def test_a_missing_logging_library_skips_the_signal_instead_of_killing_the_run(self):
        # The gcp helper calls sys.exit() when its library or key is missing.
        # SystemExit is not an Exception, so an `except Exception` would let it
        # kill the process before any BigQuery signal ran.
        class ExitingGCP:
            @staticmethod
            def get_client():
                raise SystemExit(2)

        sys.modules["gcp"] = ExitingGCP
        try:
            out = self.sr.collect("ranking", {})
        finally:
            del sys.modules["gcp"]
        # the log-based ranking signal is skipped; collect still returns
        self.assertTrue(all(s.key != "empty_model_rate" for s in out))

if __name__ == "__main__":
    unittest.main()
