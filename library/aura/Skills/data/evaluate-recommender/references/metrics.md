# Metric definitions

Every metric is implemented in [`../scripts/metrics.py`](../scripts/metrics.py) as a pure
function over plain Python structures, and covered by `tests/test_evaluate_recommender.py`.
This file says what each one means and why it is in the set; the code is the specification of
how it is computed.

## Vocabulary

| Term | Shape | Meaning |
|---|---|---|
| `recs` | `{user: [track, …]}` | ranked best-first, already truncated |
| `truth` | `{user: {track: gain}}` | graded relevance over the eval window |
| `repeats` | `{user: {track, …}}` | tracks the user already completed in the fit window |

`gain` is a graded relevance, not a click: a completion counted more than once outranks a
single completion. The intended upgrade is the full utility score from
`4U-engine/models/neural-model-trainer/sql/track_scores_2.1.sql`, which already weighs
completions, thumbs, ratings, saves, downloads, listen-later and playlist adds against short
and long skips. Reuse that rather than inventing a second definition of "good".

## Relevance

### `ndcg@k_new` — the primary

Graded NDCG at depth *k*, with repeat listens removed **from the truth and from the
recommendation list**.

Removing them from the list matters as much as from the truth. If only the truth were
filtered, a ranker could fill the top of the list with tracks the user has already heard,
push its genuine discoveries below *k*, and be scored as though those slots were free.

**Why the primary excludes repeats.** Measured 2026-09-22: 72,351 of 251,665 user-track pairs
over 30 days (**28.7%**) were tracks that user had already completed in the prior 120 days. A
ranker that simply replays listening history therefore scores well on plain NDCG while
recommending nothing at all. `user-repeat` exists as a baseline to make that concrete.

Plain `ndcg@k` is still reported — it is the right number for "did we surface something they
wanted", and the gap between it and `ndcg@k_new` is itself informative.

### `recall@10`, `recall@20`

Share of the user's relevant tracks appearing in the top *k*. Users with no relevant tracks
are dropped, not scored zero: zeroing would penalise a ranker for users it cannot help and
would drag the mean down as the eval set grows.

## Exposure

Exposure is first-class here, not a counter-metric, because it is the axis both live regimes
failed on. Collaborative filtering favoured the long-tenured; raw popularity favours the
prolific. Neither is neutral, and coach listens drive coach payouts.

### `coverage@k`

Distinct tracks reaching any user's top *k*, over catalogue size. Baseline reality: 20,446 of
72,486 tracks (**28.2%**) drew a completed listen over 30 days.

### `gini_exposure`

Concentration of top-*k* slots across the catalogue. 0 is perfectly even, →1 is one track
taking everything. **Computed over the full catalogue, including zero-exposure tracks** — the
unserved tail is precisely what the metric exists to watch, and passing only what was served
would hide it.

### `novelty`

Mean self-information, −log2(p), of recommended tracks; higher means less-played content. A
track absent from the popularity distribution takes the floor probability rather than being
skipped, so a ranker cannot inflate novelty by recommending only unknowns.

### `cohort_exposure` and `cohort_delta`

Share of top-*k* slots by the coach's first-upload year:

```
2017-18 · 2019-20 · 2021-22 · 2023+ · unknown
```

`cohort_delta` reports the candidate minus baseline shift in **percentage points**.

This is the regression test for the tenure bias. When the neural model was repaired in July
2026 it began ranking and immediately moved exposure toward the founding cohort — Lauren
Ziegler (2017-05) went 151 → 1,451 attributed listens while Teri Holland (2021-10) went
152 → 41, over matched 14-day windows. The mechanism was uniform negative sampling: ~358
synthetic negatives per track regardless of popularity, against a real positives distribution
whose 90-day median is 4. Any candidate trained on implicit feedback can reproduce it, so
every run reports this whether the candidate is a CF model or not.

## Significance

### `bootstrap_ratio_ci`

Paired bootstrap on the mean ratio candidate / baseline, seeded, default 2,000 iterations.

**Paired**, because both rankers are scored on the same users — resampling independently would
inflate the interval with between-user variance that cancels. **Intersected**, because a
candidate scored on an easier subset must not be able to win by bringing its own population.

## The verdict

```
FAIL         ratio ≤ 1.0, or the 95% CI lower bound ≤ 1.0
CONDITIONAL  beats the primary, breaches a guardrail
PASS         beats the primary, clears every guardrail
```

Guardrail defaults, both overridable and both recorded on the run:

| Guardrail | Default |
|---|---|
| `coverage_ratio` floor | 0.90 |
| max cohort exposure loss | 5.0 pp |

The CI condition is the one that earns its place. A point estimate of 1.04 with an interval of
0.91–1.19 is a candidate indistinguishable from the baseline, and without the lower-bound
check it ships.

**Only FAIL closes an idea unattended.** CONDITIONAL always reaches a human, because trading
exposure for completions is a product decision.
