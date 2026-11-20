# The `aura-main.recs_eval` store

BigQuery is the record of what was scored and what verdict it earned; the printed report is a
view of it. A verdict that is not in the store did not happen.

## `recs_eval.runs`

One row per scored candidate or baseline.

| Column | Type | Notes |
|---|---|---|
| `run_id` | STRING | `sha1(candidate\|split\|k\|config_hash)[:16]` — deterministic; the write is a MERGE, so an identical rerun replaces the row |
| `created_at` | TIMESTAMP | UTC |
| `candidate` | STRING | candidate name, or the baseline name |
| `config_hash` | STRING | sha1 of the scores file, or `baseline` |
| `split_date` | DATE | the split, always explicit |
| `k` | INT64 | evaluation depth |
| `scored_users` | INT64 | users in both windows that were actually scored |
| `metrics_json` | STRING | every metric, including `cohort_exposure` |
| `comparison_json` | STRING | ratios, CI, cohort deltas, reasons; empty for a baseline |
| `verdict` | STRING | `PASS` / `CONDITIONAL` / `FAIL`, or `BASELINE` |
| `offline_score` | STRING | the one-line string written to the loop's Notion row |

Per-user scores are **not** stored. They are large, they are reproducible from the same
inputs, and they are the one part of the payload that is close to personal data.

## Creating the dataset

Nothing to do by hand. `ensure_store()` runs idempotent DDL before any read or write:

```sql
CREATE SCHEMA IF NOT EXISTS `aura-main.recs_eval` OPTIONS (location = 'US');
CREATE TABLE IF NOT EXISTS `aura-main.recs_eval.runs` (…)
  PARTITION BY split_date CLUSTER BY candidate;
```

The service account needs `bigquery.dataEditor` on `aura-main` for the first run to create
them; afterwards `jobUser` is enough to write rows.

The column list comes from `RUNS_SCHEMA` in
[`../scripts/evaluate_recommender.py`](../scripts/evaluate_recommender.py) — the DDL and the
MERGE are both generated from it, so adding a column there is the only edit needed.

## Re-running is a replace, not an append

`run_id` is a deterministic hash of `(candidate, split, k, config_hash)`, and the write is a
`MERGE`. Re-scoring identical inputs overwrites the row rather than leaving two rows for
`show --run-id` to pick between. `created_at` and `split_date` are `CAST` inside the MERGE
because the shared BigQuery helper types every non-numeric parameter as `STRING`.

## Reading it without the skill

```sql
SELECT candidate, split_date, verdict, offline_score
FROM `aura-main.recs_eval.runs`
WHERE split_date = '2026-09-15'
ORDER BY created_at DESC
```
