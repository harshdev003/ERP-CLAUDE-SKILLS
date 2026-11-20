"""Ranking, exposure and robustness metrics for the recommender offline bar.

Pure functions over plain Python structures: no BigQuery, no network, no pandas.
Everything the verdict depends on is computed here so it can be unit-tested
against hand-checked fixtures (tests/test_evaluate_recommender.py).

Vocabulary used throughout:

  recs        {user_id: [track_id, ...]}  ranked best-first, already truncated
              to the depth the caller cares about
  truth       {user_id: {track_id: gain}} graded relevance for the eval window.
              `gain` is the utility score from track_scores_2.1.sql, not a 0/1
              click — a completion with a thumbs-up outranks a bare completion.
  repeats     {user_id: {track_id, ...}}  tracks the user already completed in
              the fit window. 28.7% of real pairs are repeats, so the primary
              metric excludes them (see references/metrics.md).
"""

from __future__ import annotations

import math
import random
from collections import Counter
from typing import Dict, Iterable, List, Mapping, Sequence, Set, Tuple

# --------------------------------------------------------------------------
# relevance
# --------------------------------------------------------------------------


def recall_at_k(recs: Sequence[str], relevant: Iterable[str], k: int) -> float:
    """Share of the user's relevant tracks that appear in the top k.

    Undefined with no relevant tracks — the caller drops those users rather
    than scoring them 0, which would reward a ranker for users it cannot help.
    """
    relevant = set(relevant)
    if not relevant:
        raise ValueError("recall_at_k needs at least one relevant track")
    hits = len(set(recs[:k]) & relevant)
    return hits / len(relevant)


def dcg(gains: Sequence[float]) -> float:
    return sum(g / math.log2(i + 2) for i, g in enumerate(gains))


def ndcg_at_k(recs: Sequence[str], gains: Mapping[str, float], k: int) -> float:
    """Graded NDCG. `gains` maps track -> utility score; missing tracks score 0.

    The ideal ranking is the user's own gains sorted desc, truncated to k, so a
    user with fewer than k relevant tracks can still reach 1.0.
    """
    if not gains:
        raise ValueError("ndcg_at_k needs at least one graded track")
    actual = dcg([gains.get(t, 0.0) for t in recs[:k]])
    ideal = dcg(sorted(gains.values(), reverse=True)[:k])
    return actual / ideal if ideal > 0 else 0.0


def per_user_ndcg(
    recs: Mapping[str, Sequence[str]],
    truth: Mapping[str, Mapping[str, float]],
    k: int,
    *,
    exclude: Mapping[str, Set[str]] | None = None,
) -> Dict[str, float]:
    """NDCG@k for every user the ranker and the truth set share.

    `exclude` removes tracks from BOTH the recommendation list and the truth
    before scoring — this is how `ndcg@k_new` drops repeats. Excluding from the
    recommendations too matters: otherwise a ranker fills the top of the list
    with already-heard tracks, pushes real discoveries below k, and is scored as
    if those slots were free.
    """
    exclude = exclude or {}
    out: Dict[str, float] = {}
    for user, user_truth in truth.items():
        if user not in recs:
            continue
        skip = exclude.get(user, set())
        gains = {t: g for t, g in user_truth.items() if t not in skip}
        if not gains:
            continue
        kept = [t for t in recs[user] if t not in skip]
        out[user] = ndcg_at_k(kept, gains, k)
    return out


# --------------------------------------------------------------------------
# exposure
# --------------------------------------------------------------------------


def coverage_at_k(recs: Mapping[str, Sequence[str]], catalogue_size: int, k: int) -> float:
    """Share of the catalogue that appears in at least one user's top k."""
    if catalogue_size <= 0:
        raise ValueError("catalogue_size must be positive")
    seen = {t for r in recs.values() for t in r[:k]}
    return len(seen) / catalogue_size


def gini(counts: Iterable[float]) -> float:
    """Gini coefficient of an exposure distribution. 0 = perfectly even, →1 = concentrated.

    Zero-exposure items count: they are the long tail this metric exists to
    watch, so the caller passes the full catalogue, not just what was served.
    """
    values = sorted(float(c) for c in counts)
    n = len(values)
    if n == 0:
        raise ValueError("gini needs at least one value")
    total = sum(values)
    if total == 0:
        return 0.0
    weighted = sum((i + 1) * v for i, v in enumerate(values))
    return (2 * weighted) / (n * total) - (n + 1) / n


def exposure_counts(recs: Mapping[str, Sequence[str]], k: int) -> Counter:
    """How many top-k slots each track occupies across all users."""
    c: Counter = Counter()
    for r in recs.values():
        c.update(r[:k])
    return c


def novelty(recs: Mapping[str, Sequence[str]], popularity: Mapping[str, float], k: int) -> float:
    """Mean self-information, -log2(p), of recommended tracks.

    Higher means the ranker is surfacing less-played content. A track absent
    from `popularity` never played in the window, so it gets the floor
    probability rather than being skipped — dropping it would let a ranker
    raise novelty by recommending nothing anyone has heard of.
    """
    if not popularity:
        raise ValueError("novelty needs a popularity distribution")
    floor = min(p for p in popularity.values() if p > 0) if any(
        p > 0 for p in popularity.values()) else 1.0
    bits: List[float] = []
    for r in recs.values():
        for t in r[:k]:
            p = popularity.get(t, 0.0) or floor
            bits.append(-math.log2(p))
    return sum(bits) / len(bits) if bits else 0.0


def cohort_exposure(
    recs: Mapping[str, Sequence[str]],
    track_cohort: Mapping[str, str],
    k: int,
) -> Dict[str, float]:
    """Share of top-k slots held by each coach cohort.

    The cohort is the coach's first-upload year bucket. This is the direct
    regression test for the tenure bias (ENGBACK-1395): a collaborative-
    filtering candidate that reproduces the July 2026 reshuffle shows up here
    as the oldest bucket gaining share.
    """
    counts: Counter = Counter()
    for r in recs.values():
        for t in r[:k]:
            counts[track_cohort.get(t, "unknown")] += 1
    total = sum(counts.values())
    if total == 0:
        return {}
    return {c: n / total for c, n in counts.items()}


def cohort_delta(
    candidate: Mapping[str, float], baseline: Mapping[str, float]
) -> Dict[str, float]:
    """Percentage-point shift per cohort, candidate minus baseline."""
    keys = set(candidate) | set(baseline)
    return {k: 100.0 * (candidate.get(k, 0.0) - baseline.get(k, 0.0)) for k in keys}


# --------------------------------------------------------------------------
# significance
# --------------------------------------------------------------------------


def bootstrap_ratio_ci(
    candidate: Mapping[str, float],
    baseline: Mapping[str, float],
    *,
    iterations: int = 2000,
    alpha: float = 0.05,
    seed: int = 20260922,
) -> Tuple[float, float, float]:
    """Paired bootstrap on the mean ratio candidate/baseline. Returns (ratio, lo, hi).

    Paired because both rankers are scored on the same users: resampling users
    independently would inflate the interval with between-user variance that
    cancels. Only users present in both are used, so a candidate cannot win by
    scoring a different population.
    """
    users = sorted(set(candidate) & set(baseline))
    if not users:
        raise ValueError("no users in common between candidate and baseline")
    cand = [candidate[u] for u in users]
    base = [baseline[u] for u in users]

    def ratio(idx: Sequence[int]) -> float:
        b = sum(base[i] for i in idx)
        if b == 0:
            return float("inf") if sum(cand[i] for i in idx) > 0 else 1.0
        return sum(cand[i] for i in idx) / b

    point = ratio(range(len(users)))
    rng = random.Random(seed)
    n = len(users)
    draws = sorted(ratio([rng.randrange(n) for _ in range(n)]) for _ in range(iterations))
    lo = draws[int((alpha / 2) * iterations)]
    hi = draws[min(int((1 - alpha / 2) * iterations), iterations - 1)]
    return point, lo, hi


# --------------------------------------------------------------------------
# the gate
# --------------------------------------------------------------------------

# Defaults are the loop's published thresholds; the CLI can override them, and
# whatever was used is stored on the run so a verdict is reproducible.
COVERAGE_FLOOR = 0.90          # candidate coverage@k as a ratio of baseline
COHORT_DROP_LIMIT_PP = 5.0     # max percentage points any cohort may lose


def verdict(
    *,
    ndcg_new_ratio: float,
    ndcg_new_ci: Tuple[float, float],
    coverage_ratio: float,
    cohort_deltas: Mapping[str, float],
    coverage_floor: float = COVERAGE_FLOOR,
    cohort_drop_limit_pp: float = COHORT_DROP_LIMIT_PP,
) -> Tuple[str, List[str]]:
    """PASS / CONDITIONAL / FAIL, with the reasons that produced it.

    Three verdicts rather than two because exposure cannot be collapsed into the
    relevance score. A candidate that wins on relevance while concentrating
    exposure is a real product trade — worth a human deciding, not worth a
    script deciding. Only failing the primary closes an idea automatically.
    """
    lo, _hi = ndcg_new_ci
    reasons: List[str] = []

    if ndcg_new_ratio <= 1.0:
        return "FAIL", [f"ndcg@k_new ratio {ndcg_new_ratio:.3f} does not beat the baseline"]
    if lo <= 1.0:
        return "FAIL", [
            f"ndcg@k_new ratio {ndcg_new_ratio:.3f} but the 95% CI lower bound is {lo:.3f} "
            "— not distinguishable from the baseline"
        ]

    if coverage_ratio < coverage_floor:
        reasons.append(
            f"coverage ratio {coverage_ratio:.3f} is below the {coverage_floor:.2f} floor"
        )
    for cohort, delta in sorted(cohort_deltas.items()):
        if delta < -cohort_drop_limit_pp:
            reasons.append(
                f"cohort '{cohort}' loses {abs(delta):.1f}pp of exposure "
                f"(limit {cohort_drop_limit_pp:.0f}pp)"
            )

    if reasons:
        return "CONDITIONAL", reasons
    return "PASS", [
        f"ndcg@k_new {ndcg_new_ratio:.3f}× (CI lower {lo:.3f}), coverage "
        f"{coverage_ratio:.3f}×, no cohort beyond {cohort_drop_limit_pp:.0f}pp"
    ]


def offline_score_line(
    *, ndcg_new_ratio: float, coverage_ratio: float, cohort_deltas: Mapping[str, float]
) -> str:
    """The one-line `Offline score` string written to the loop's Notion row."""
    worst = min(cohort_deltas.items(), key=lambda kv: kv[1], default=("—", 0.0))
    return (
        f"NDCG@10-new {ndcg_new_ratio:.2f}x · coverage {coverage_ratio:.2f}x · "
        f"cohort {worst[0]} {worst[1]:+.1f}pp"
    )
