"""Metrics and the offline-bar verdict for data:evaluate-recommender.

The verdict decides whether an idea reaches a human at all, so the cases that
matter are the ones where a plausible-looking candidate must NOT pass: a ranker
that wins only by replaying history, one that wins relevance while collapsing
exposure, one whose lead is inside the noise.
"""

import math
import os
import sys
import unittest

sys.path.insert(
    0,
    os.path.join(
        os.path.dirname(__file__),
        "..",
        "plugins",
        "data",
        "skills",
        "evaluate-recommender",
        "scripts",
    ),
)

import evaluate_recommender as ev  # noqa: E402
import metrics  # noqa: E402


class TestRelevance(unittest.TestCase):
    def test_recall_counts_only_the_top_k(self):
        recs = ["a", "b", "c", "d"]
        self.assertEqual(metrics.recall_at_k(recs, {"a", "d"}, k=2), 0.5)
        self.assertEqual(metrics.recall_at_k(recs, {"a", "d"}, k=4), 1.0)

    def test_recall_refuses_a_user_with_nothing_relevant(self):
        # Scoring such a user 0 would punish a ranker for a user it cannot help,
        # and silently drag the mean down as the eval set grows.
        with self.assertRaises(ValueError):
            metrics.recall_at_k(["a"], [], k=1)

    def test_ndcg_rewards_putting_the_best_item_first(self):
        gains = {"a": 3.0, "b": 1.0}
        first = metrics.ndcg_at_k(["a", "b"], gains, k=2)
        second = metrics.ndcg_at_k(["b", "a"], gains, k=2)
        self.assertEqual(first, 1.0)
        self.assertLess(second, first)

    def test_ndcg_can_reach_one_with_fewer_relevant_items_than_k(self):
        self.assertEqual(metrics.ndcg_at_k(["a", "x", "y"], {"a": 2.0}, k=3), 1.0)

    def test_ndcg_is_zero_when_nothing_relevant_is_returned(self):
        self.assertEqual(metrics.ndcg_at_k(["x", "y"], {"a": 2.0}, k=2), 0.0)


class TestRepeatExclusion(unittest.TestCase):
    """28.7% of real user-track pairs are repeats. A 'replay their history'
    ranker must not be able to win the primary metric."""

    def test_replaying_history_scores_zero_on_the_new_only_metric(self):
        recs = {"u": ["old1", "old2"]}
        truth = {"u": {"old1": 1.0, "old2": 1.0, "fresh": 1.0}}
        repeats = {"u": {"old1", "old2"}}

        plain = metrics.per_user_ndcg(recs, truth, k=2)
        new_only = metrics.per_user_ndcg(recs, truth, k=2, exclude=repeats)

        self.assertEqual(plain["u"], 1.0)        # looks perfect
        self.assertEqual(new_only["u"], 0.0)     # found nothing new

    def test_excluded_tracks_do_not_consume_top_k_slots(self):
        # The repeat sits above the discovery. If exclusion only stripped the
        # truth and not the list, "fresh" would fall outside k=1 and score 0 —
        # penalising a ranker for something the metric is meant to ignore.
        recs = {"u": ["old1", "fresh"]}
        truth = {"u": {"old1": 1.0, "fresh": 1.0}}
        repeats = {"u": {"old1"}}
        self.assertEqual(metrics.per_user_ndcg(recs, truth, k=1, exclude=repeats)["u"], 1.0)

    def test_users_left_with_no_relevant_tracks_are_dropped_not_zeroed(self):
        recs = {"u": ["old1"], "v": ["fresh"]}
        truth = {"u": {"old1": 1.0}, "v": {"fresh": 1.0}}
        repeats = {"u": {"old1"}}
        scored = metrics.per_user_ndcg(recs, truth, k=1, exclude=repeats)
        self.assertNotIn("u", scored)
        self.assertIn("v", scored)


class TestExposure(unittest.TestCase):
    def test_gini_spans_even_to_concentrated(self):
        self.assertAlmostEqual(metrics.gini([5, 5, 5, 5]), 0.0, places=9)
        self.assertGreater(metrics.gini([0, 0, 0, 40]), 0.7)

    def test_gini_counts_the_unserved_tail(self):
        # Passing only what was served hides the concentration the metric exists
        # to surface: same served slots, very different catalogues.
        served_only = metrics.gini([10, 10])
        with_tail = metrics.gini([10, 10] + [0] * 98)
        self.assertLess(served_only, with_tail)

    def test_coverage_counts_distinct_tracks_not_slots(self):
        recs = {"u": ["a", "b"], "v": ["a", "b"]}
        self.assertEqual(metrics.coverage_at_k(recs, catalogue_size=10, k=2), 0.2)

    def test_novelty_rises_when_rare_tracks_are_served(self):
        pop = {"hit": 0.5, "rare": 0.001}
        head = metrics.novelty({"u": ["hit"]}, pop, k=1)
        tail = metrics.novelty({"u": ["rare"]}, pop, k=1)
        self.assertGreater(tail, head)

    def test_unknown_tracks_take_the_floor_not_a_free_pass(self):
        # A track absent from the popularity map was never played. Skipping it
        # would let a ranker raise novelty by serving only unknowns.
        pop = {"hit": 0.5, "rare": 0.001}
        unknown = metrics.novelty({"u": ["never-seen"]}, pop, k=1)
        self.assertAlmostEqual(unknown, -math.log2(0.001), places=9)

    def test_cohort_exposure_shares_sum_to_one(self):
        recs = {"u": ["t1", "t2"], "v": ["t3", "t4"]}
        cohorts = {"t1": "2017-18", "t2": "2017-18", "t3": "2021-22", "t4": "2023+"}
        shares = metrics.cohort_exposure(recs, cohorts, k=2)
        self.assertAlmostEqual(sum(shares.values()), 1.0, places=9)
        self.assertAlmostEqual(shares["2017-18"], 0.5, places=9)

    def test_cohort_delta_is_in_percentage_points(self):
        d = metrics.cohort_delta({"2017-18": 0.60}, {"2017-18": 0.50})
        self.assertAlmostEqual(d["2017-18"], 10.0, places=9)


class TestBootstrap(unittest.TestCase):
    def test_identical_rankers_give_a_ratio_of_one(self):
        scores = {f"u{i}": 0.5 for i in range(50)}
        point, lo, hi = metrics.bootstrap_ratio_ci(scores, scores, iterations=200)
        self.assertAlmostEqual(point, 1.0, places=9)
        self.assertLessEqual(lo, 1.0)
        self.assertGreaterEqual(hi, 1.0)

    def test_a_clear_win_excludes_one_from_the_interval(self):
        base = {f"u{i}": 0.2 for i in range(200)}
        cand = {f"u{i}": 0.4 for i in range(200)}
        _point, lo, _hi = metrics.bootstrap_ratio_ci(cand, base, iterations=400)
        self.assertGreater(lo, 1.0)

    def test_only_shared_users_are_compared(self):
        # A candidate scored on an easier subset must not be able to win by
        # bringing its own population.
        base = {"a": 0.1, "b": 0.1}
        cand = {"a": 0.1, "c": 9.9}
        point, _lo, _hi = metrics.bootstrap_ratio_ci(cand, base, iterations=100)
        self.assertAlmostEqual(point, 1.0, places=9)

    def test_disjoint_populations_raise_rather_than_return_a_number(self):
        with self.assertRaises(ValueError):
            metrics.bootstrap_ratio_ci({"a": 1.0}, {"b": 1.0}, iterations=10)

    def test_the_bootstrap_is_seeded(self):
        base = {f"u{i}": 0.2 + i * 0.001 for i in range(80)}
        cand = {f"u{i}": 0.3 + i * 0.001 for i in range(80)}
        first = metrics.bootstrap_ratio_ci(cand, base, iterations=300)
        again = metrics.bootstrap_ratio_ci(cand, base, iterations=300)
        self.assertEqual(first, again)


class TestVerdict(unittest.TestCase):
    CLEAN = dict(coverage_ratio=1.0, cohort_deltas={"2017-18": 0.5, "2023+": -0.4})

    def test_a_clean_win_passes(self):
        v, _why = metrics.verdict(
            ndcg_new_ratio=1.31, ndcg_new_ci=(1.12, 1.50), **self.CLEAN)
        self.assertEqual(v, "PASS")

    def test_not_beating_the_baseline_fails(self):
        v, why = metrics.verdict(
            ndcg_new_ratio=0.98, ndcg_new_ci=(0.80, 1.16), **self.CLEAN)
        self.assertEqual(v, "FAIL")
        self.assertIn("does not beat", why[0])

    def test_a_lead_inside_the_noise_fails(self):
        # The failure mode that matters most: a point estimate above 1.0 whose
        # interval still contains it. Without the CI check this ships.
        v, why = metrics.verdict(
            ndcg_new_ratio=1.04, ndcg_new_ci=(0.91, 1.19), **self.CLEAN)
        self.assertEqual(v, "FAIL")
        self.assertIn("CI lower bound", why[0])

    def test_collapsing_coverage_is_conditional_not_a_pass(self):
        v, why = metrics.verdict(
            ndcg_new_ratio=1.40, ndcg_new_ci=(1.20, 1.60),
            coverage_ratio=0.61, cohort_deltas={})
        self.assertEqual(v, "CONDITIONAL")
        self.assertTrue(any("coverage" in r for r in why))

    def test_the_tenure_reshuffle_is_conditional_not_a_pass(self):
        # Shape of the July 2026 regression: relevance up, the newest cohort
        # down hard. This is the case the gate exists for.
        v, why = metrics.verdict(
            ndcg_new_ratio=1.25, ndcg_new_ci=(1.10, 1.41), coverage_ratio=0.97,
            cohort_deltas={"2017-18": +9.2, "2021-22": -6.1, "2023+": -3.1})
        self.assertEqual(v, "CONDITIONAL")
        self.assertTrue(any("2021-22" in r for r in why))

    def test_a_guardrail_breach_never_silently_closes_an_idea(self):
        # CONDITIONAL must reach a human. Only FAIL closes an idea unattended,
        # so a breach must never be reported as FAIL.
        v, _why = metrics.verdict(
            ndcg_new_ratio=1.25, ndcg_new_ci=(1.10, 1.41), coverage_ratio=0.10,
            cohort_deltas={"2021-22": -40.0})
        self.assertNotEqual(v, "FAIL")

    def test_thresholds_are_overridable_and_change_the_verdict(self):
        args = dict(ndcg_new_ratio=1.25, ndcg_new_ci=(1.10, 1.41),
                    coverage_ratio=0.93, cohort_deltas={})
        self.assertEqual(metrics.verdict(**args)[0], "PASS")
        self.assertEqual(metrics.verdict(coverage_floor=0.95, **args)[0], "CONDITIONAL")


class TestRankingDepth(unittest.TestCase):
    """recall@20 is reported, so every ranker must emit at least 20 candidates.

    The original cut sliced every list to --k (default 10), which made
    recall@20 silently identical to recall@10 — a metric that looks present in
    the report and measures nothing.
    """

    CATALOGUE = {f"t{i}": {"coach": "c", "cohort": "2017-18"} for i in range(200)}
    POPULAR = [f"t{i}" for i in range(200)]
    USERS = ["u1", "u2"]

    def test_every_baseline_emits_at_least_the_deepest_recall_depth(self):
        fit = {"u1": [f"t{i}" for i in range(60)], "u2": [f"t{i}" for i in range(60)]}
        for name, fn in ev.BASELINES.items():
            recs = fn(users=self.USERS, popular_order=self.POPULAR,
                      catalogue=self.CATALOGUE, fit_by_user=fit, k=10)
            for user, ranking in recs.items():
                self.assertGreaterEqual(
                    len(ranking), ev.MAX_DEPTH,
                    f"{name} emitted {len(ranking)} for {user}, below MAX_DEPTH")

    def test_recall_at_20_can_differ_from_recall_at_10(self):
        # A hit at rank 15 must be invisible to recall@10 and visible to @20.
        recs = [f"t{i}" for i in range(20)]
        self.assertEqual(metrics.recall_at_k(recs, {"t14"}, 10), 0.0)
        self.assertEqual(metrics.recall_at_k(recs, {"t14"}, 20), 1.0)

    def test_depth_never_falls_below_k(self):
        # A candidate capped at MAX_DEPTH while baselines emit k would be scored
        # on fewer slots than the bar it has to beat, for any k above 20.
        self.assertEqual(ev.depth_for(5), ev.MAX_DEPTH)
        self.assertEqual(ev.depth_for(20), ev.MAX_DEPTH)
        self.assertEqual(ev.depth_for(50), 50)

    def test_baselines_match_depth_for_at_a_large_k(self):
        fit = {"u1": [f"t{i}" for i in range(80)], "u2": [f"t{i}" for i in range(80)]}
        for name, fn in ev.BASELINES.items():
            recs = fn(users=self.USERS, popular_order=self.POPULAR,
                      catalogue=self.CATALOGUE, fit_by_user=fit, k=50)
            for user, ranking in recs.items():
                self.assertEqual(len(ranking), ev.depth_for(50),
                                 f"{name} emitted {len(ranking)} for {user} at k=50")

    def test_a_short_candidate_is_flagged_rather_than_scored_as_complete(self):
        recs = {"u1": [f"t{i}" for i in range(5)]}
        truth = {"u1": {"t1": 1.0}}
        scored = ev.score_recs(recs, truth, {"u1": set()}, self.CATALOGUE,
                               self.POPULAR, k=10)
        self.assertTrue(scored["recall_at_20_truncated"])


class TestOfflineScoreLine(unittest.TestCase):
    def test_it_names_the_worst_cohort_not_the_first(self):
        line = metrics.offline_score_line(
            ndcg_new_ratio=1.31, coverage_ratio=0.94,
            cohort_deltas={"2017-18": +4.0, "2021-22": -6.1, "2023+": -0.2})
        self.assertIn("1.31x", line)
        self.assertIn("2021-22", line)
        self.assertIn("-6.1pp", line)


if __name__ == "__main__":
    unittest.main()
