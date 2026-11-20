---
name: evaluate-recommender
description: >
  The offline bar for Aura content recommendations — replays a candidate ranker against
  held-out listening history and scores it against the incumbent popularity ranker, so a
  candidate can be settled in hours instead of a two-week experiment. Reports relevance
  (NDCG excluding repeat listens, recall@10/@20), exposure (catalogue coverage, Gini,
  novelty, per-coach-cohort share) and robustness, each as a ratio to four baselines on the
  identical split, then returns PASS / CONDITIONAL / FAIL. Writes every run to
  aura-main.recs_eval so a verdict is reproducible. Trigger on "evaluate a recommender",
  "score a ranking candidate", "run the offline bar", "does this beat popularity",
  "recommender offline eval", "score the recommendation baselines", "refresh the recs_eval
  baselines", or a candidate scores file with a request to judge it. Do NOT use to check
  whether the recommender is healthy (eng:monitor-recommendation-health), to read an A/B
  result (data:analyze-experiment), or to run ad-hoc BigQuery (eng:query-bigquery).

metadata:
  version: "1.2.1"
knowledge:
  reads:
    - Guide/Index      # baseline routing aid (universal)
    - Tools/BigQuery   # GCP_KEY service account, aura-main access
    - Learnings/Loop/Aura Recommendations Ideas   # the loop this gate belongs to
---

# evaluate-recommender

The machine gate in the [Aura Recommendations loop](https://app.notion.com/p/Aura-Recommendations-3e375e23af2081a1ae5cd5708a8a9684). A ranking candidate must beat the incumbent here before anyone is asked to look at it; one that does not is closed as `rejected` / `offline bar` with its numbers posted, no human gate.

It occupies the same slot as `product:design-idea` does in the Features loop — the leg between *record* and *share* that makes a proposal judgeable. Features renders a picture; this renders a scorecard, and unlike a picture it can also refuse.

## Prerequisites

```bash
test -n "$GCP_KEY" && echo "gcp: OK" || echo "gcp: MISSING"
python3 -c "import google.cloud.bigquery" 2>/dev/null && echo "lib: OK" \
  || echo "lib: MISSING — python3 -m pip install --user google-cloud-bigquery"
```

Both required. `GCP_KEY` is the inline service-account JSON, needing `bigquery.jobUser` + `bigquery.dataEditor` on `aura-main`.

## Usage

```bash
E="${CLAUDE_PLUGIN_ROOT}/skills/evaluate-recommender/scripts/evaluate_recommender.py"

# refresh the four baselines on a split (do this first, and after any data change)
python3 "$E" baselines --split-date 2026-09-15

# score a candidate: JSON {user_id: [track_id, ...]} ranked best-first
python3 "$E" score --name cf-item-item-v1 --scores-file /tmp/cf_v1.json

python3 "$E" runs --limit 20          # recent runs
python3 "$E" show --run-id 9f2c1a…    # one run in full
```

Exit code is `0` for PASS or CONDITIONAL, `1` for FAIL, `2` when the eval set could not be built — so an orchestrator can branch on it.

## What a candidate is

**A recipe, not a model.** The blend is where models go to die: `activation-model` holds 33% of the new-user weight and contributes nothing, while `popular-model` holds 0.03% and carries 88% of listens. Scoring a model alone tells you nothing about what ships.

So the interface is deliberately dumb: a JSON file of `{user_id: [track_id, …]}` produced by whatever you are testing — a replayed model service, a notebook, a modified recipe run against a fixture. The harness does not import your candidate, which means it can score anything that can emit a ranking, including a parked model artefact.

## The split

| | |
|---|---|
| Split date `T` | default 7 days ago; always explicit in the output and the stored row |
| Fit window | 365 days ending `T` |
| Eval window | `(T, T+7d]` |
| Eval users | history in **both** windows. Users without fit-window history are a separate cold-start cohort, never folded into the headline |
| Truth | completed listens in the eval window, graded by completion count |

## The metrics, and why these

Full definitions in [references/metrics.md](references/metrics.md). The three that decide anything:

- **`ndcg@k_new` — the primary, with repeat listens excluded.** 28.7% of user-track pairs over 30 days are tracks that user already completed in the prior 120 days. Plain NDCG lets a "replay their history" ranker score beautifully while recommending nothing, so the primary metric is blind to repeats — removed from the truth *and* from the list, so repeats cannot consume top-k slots.
- **`coverage@k`** — share of catalogue reaching any user's top k. 28.2% of the catalogue was heard at all last month; a candidate that wins by serving the head harder is caught here.
- **`cohort_exposure`** — top-k share by the coach's first-upload year. This is the direct regression test for the tenure bias: when the neural model was repaired in July 2026 it shifted exposure to 2017–18 coaches and away from 2021–23 ones, and coach listens drive coach payouts.

## The four baselines

Every number is reported as a ratio to a baseline on the identical split.

| Baseline | Why it is there |
|---|---|
| `popular-30d` | the incumbent — literally what ships today |
| `random` | the floor; failing it means the harness is wrong, not the candidate |
| `user-repeat` | the **trap**. Given 28.7% repeats it scores well on plain NDCG and near zero on the primary. A candidate that cannot beat it is not recommending anything |
| `popular-by-tag` | *not yet implemented* — needs the include-filter mirror; tracked on ENGBACK-1380 |

## The verdict is a vector

| Verdict | Condition | Effect |
|---|---|---|
| **PASS** | primary ratio > 1.0, bootstrap 95% CI excluding 1.0, coverage ≥ 0.9×, no cohort losing > 5pp | → `shared`, Gate 1 |
| **CONDITIONAL** | beats the primary, breaches a guardrail | → `shared`, Gate 1, **with the trade stated**. A human decides |
| **FAIL** | does not beat the primary, or the lead is inside the interval | → `rejected` / `offline bar`, posted as an FYI |

Exposure cannot be collapsed into the relevance score, so the gate has two dimensions and a middle verdict. **Only FAIL closes an idea without a human** — concentrating exposure to win completions is a real product choice, it is just not one a script should make.

## Validity limits

Printed with every report, and not optional reading:

- **No impression log.** Retrieval evaluation over *observed* outcomes — a track never shown cannot be credited, so anything surfacing the tail is structurally penalised. Coverage partly compensates; it does not fix it. `ENGBACK-1378` lands the log; re-baseline everything after it.
- **Position bias uncorrected** until `positiononlist` is populated (`ENGBACK-1379`).
- **Feedback loop.** The truth was generated by a popularity-dominated system, so `popular-30d` is advantaged by construction. A candidate that merely *matches* it offline may still be better live — an argument for running the experiment, not for weakening the gate.

## Notes

- **Deterministic.** Seeded bootstrap, explicit split dates, candidate file hashed into the run id — the same inputs produce the same verdict and the same row.
- **The store is the record.** Every run lands in `aura-main.recs_eval.runs`; see [references/store.md](references/store.md). The printed report is a view of it, not the artefact.
- **This skill never writes to Notion or Slack.** It returns a verdict and an `offline_score` line; `product:recommendations-loop` is what acts on them.
- **Its own validation test**: replay the 2.1 neural artefact and confirm `cohort_exposure` reproduces the July 2026 reshuffle. A harness that cannot detect a bias we already measured is not ready to gate anything.
