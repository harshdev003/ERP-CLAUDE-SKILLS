---
name: backfill-aurie-user-env
description: >
  Backfill and maintain the per-user environment dimension in BigQuery
  (aura-main.aurie_cost.aurie_user_env) — tags every user_id in the cost table as prod, dev, or
  unknown. Trigger on: "backfill aurie user env", "backfill user environment", "tag users prod vs
  dev", "user environment dimension", "aurie_user_env", "refresh aurie_user_env", "refresh the
  prod cost view", "aurie_per_user_daily_cost_prod". Resolves each user against the prod Aura API
  first, falls back to dev on a 404, and appends only resolved rows (append-only, immutable). Also
  owns the shared `aurie_per_user_daily_cost_prod` view (prod-only per-user daily cost), refreshed
  after every run or standalone via `--view-only`. Foundation for env-filtering the prod cost
  aggregates (PROD-3589) — the whole `chart-aurie-*` family reads that view instead of the base
  table. Do NOT use to render cost charts (that's the chart-aurie-* family, which composes this
  one); this only fills the dimension table + the view.
metadata:
  version: "3.3.1"
knowledge:
  reads:
    - Guide/Index                  # baseline routing aid (universal)
    - Tools/BigQuery               # dataset/table conventions, GCP_KEY auth, bq.py
    - Tools/Aura API               # GET /users/:userId, prod vs dev bases
    - Compliance/Privacy           # reads /users; store only an env label, no PII
---

# Backfill Aurie User Env

## Goal

Keep **`aura-main.aurie_cost.aurie_user_env`** — a per-user dimension mapping Firebase `user_id`
to which Aura environment it actually belongs to (`prod` / `dev` / `unknown`) — complete for every
user_id a cost query will touch. This is the **PROD-3589** foundation for env-filtering the prod
cost aggregates: some `user_id`s in `aurie_per_user_daily_cost` were created against the dev Aura
backend (test accounts, internal QA) rather than prod, and without this dimension they can't be
excluded from prod-only cost/margin numbers.

## Why a separate skill

There's no single source that already knows which backend a `user_id` belongs to — the cost table
just has a UID, and Mixpanel doesn't carry an environment field for it either. The only way to find
out is to ask each Aura backend directly: does this uid exist there? So this walk hits the prod
`/users/:id` endpoint first (the vast majority resolve there), and only falls back to the dev
backend for the ids that 404 on prod. A uid that 404s on **both** is a stable orphan (e.g. a
QA/test id) and is recorded as `unknown` rather than left unresolved forever.

## Table

`aura-main.aurie_cost.aurie_user_env` — clustered by `user_id`, unpartitioned, **append-only**:

| Column | Type | Notes |
|---|---|---|
| `user_id` | STRING | Firebase uid (= cost table `user_id`, the join key) |
| `env` | STRING | `prod` \| `dev` \| `unknown` |
| `resolved_via` | STRING | `prod_users` \| `dev_users` \| `not_found_both` |
| `fetched_at` | TIMESTAMP | when this row was resolved |

**No PII** — the Aura `/users` response carries email/name/etc.; only the environment label +
how it was resolved are stored, never any field from the response body itself. Environment is
treated as immutable once resolved (a uid doesn't move between Aura backends), so the table is
pure append — never `UPDATE`/`DELETE`/`MERGE`. Read it deduped
(`QUALIFY ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY fetched_at DESC) = 1`) so a rare
append-only duplicate can't double-count a user.

## The prod-only view

`aura-main.aurie_cost.aurie_per_user_daily_cost_prod` — same columns as
`aurie_per_user_daily_cost`, restricted to rows whose `user_id` resolves to `env='prod'` in this
table:

```sql
CREATE OR REPLACE VIEW `aura-main.aurie_cost.aurie_per_user_daily_cost_prod` AS
SELECT c.* FROM `aura-main.aurie_cost.aurie_per_user_daily_cost` c
JOIN (
  SELECT user_id FROM (
    SELECT user_id, env,
           ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY fetched_at DESC) AS rn
    FROM `aura-main.aurie_cost.aurie_user_env`
  ) WHERE rn = 1 AND env = 'prod'
) e USING (user_id)
WHERE c.date NOT IN (DATE '2026-06-23', DATE '2026-06-24', DATE '2026-06-25')
```

**The join dedups first.** `aurie_user_env` is append-only with no unique key, so a `user_id`
can carry more than one row; the subquery takes the latest row per user
(`ROW_NUMBER() … ORDER BY fetched_at DESC`) before filtering to `env='prod'`. Without the dedup a
duplicated dimension row would fan out the joined cost rows and inflate every downstream
aggregate. Any other skill that selects the prod-user set (e.g. `chart-aurie-daily-margin`'s
revenue filter and excluded-$ query) uses the identical deduped pattern.

The `INNER JOIN` means this also drops the `Other` reconciling row (it has no matching
`user_id` in `aurie_user_env`) along with every `dev`/`unknown` user — the view is prod real-user
rows only. Every `chart-aurie-*` skill in the per-user daily cost family reads this view instead
of the base table, so a filter change here (or a coverage refresh) takes effect everywhere at
once. The view is (re)created idempotently — `CREATE OR REPLACE` — after every backfill run, and
can also be refreshed standalone without touching the Aura API:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/backfill-aurie-user-env/scripts/env_backfill.py --view-only
```

Use `--view-only` when you just fixed/extended `aurie_user_env` coverage out-of-band and want the
view to pick it up immediately, without waiting for (or triggering) a full API walk.

### Excluded days

**The view also drops three days of unusable per-user attribution ([ENG-1120](https://linear.app/aura-health/issue/ENG-1120)).** `UNUSABLE_ATTRIBUTION_DATES` in `scripts/env_backfill.py` is the list, with the evidence. On **2026-06-24** and **2026-06-25** the LLM per-user join resolved **0** users and \$144.88 / \$151.00 of LLM spend landed wholly on the `Other` row; on **2026-06-23** it resolved 317 instead of ~1,630, so the survivors absorbed the whole day's bill at roughly 5x their true cost. Voice attribution was intact on all three — this is the LLM join specifically. They are unrepairable: June 2026 stored completions are long past OpenAI's ~30-day retention and the ledger has no pre-epoch rows.

They are excluded **here**, rather than caveated in each reader, for the reason the guard exists at all — the failure is invisible in the data. The dollars still reconcile to the authoritative bill, so a reader that forgets to special-case these dates silently averages over an empty (or wildly over-concentrated) distribution rather than erroring. One exclusion covers the whole `chart-aurie-*` family.

**Scope: the per-user view only.** The base table keeps every row, so `eng:chart-aurie-daily-cost` and `eng:report-livekit-daily-cost` — which deliberately read the base table so their headline totals reconcile to the vendor bill — still see these days in full. Recurrence is prevented upstream by `eng:store-aurie-user-daily-cost`'s coverage and user-count guards, so this list should never need another entry; if it does, that guard was bypassed.

## API-first

No BigQuery MCP; API-first via `GCP_KEY` (BigQuery) and `AURA_API_TOKEN` (Aura API — the same
Bearer token authenticates against both the prod and dev backends). Ships its own copy of `bq.py`
(canonical in `eng:query-bigquery`). Needs `requests` + `google-cloud-bigquery` — run with the
framework python that has `requests`.

## Step 0 — Prerequisites

```bash
test -n "$AURA_API_TOKEN" && echo "aura: OK" || echo "aura: MISSING"
test -n "$GCP_KEY"        && echo "gcp: OK"  || echo "gcp: MISSING"
```

Both required. `AURA_API_TOKEN` → the Aura API (prod + dev); `GCP_KEY` → BigQuery.

## Step 1 — Preview coverage (dry run)

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/backfill-aurie-user-env/scripts/env_backfill.py --dry-run
```

Prints requested UIDs (default = all distinct `user_id` in `aurie_per_user_daily_cost`), how many
are already in the table, and how many are missing — no writes.

## Step 2 — Run the backfill

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/backfill-aurie-user-env/scripts/env_backfill.py
```

For each missing UID:

1. `GET https://services.aurahealth.io/users/<uid>` — `200` → `env=prod`. `404` → step 2. Any
   other status, network, or parse error → **transient**, left unresolved, retried next run.
2. `GET https://services.dev.aurahealth.io/users/<uid>` (same Bearer token) — `200` → `env=dev`.
   `404` → `env=unknown` (**terminal** — a uid that 404s on both is a stable orphan). Any other
   status/network/parse error → transient, retried next run.

Appends only resolved rows, then prints a coverage report split into prod / dev / unknown counts.
Idempotent + incremental — a re-run only fetches what's still missing.

Useful flags: `--limit N` (cap fetches for a test run), `--workers N` (Aura API concurrency,
default 16 — lower it if the dev backend starts rate-limiting and transient-unresolved climbs),
`--uids a,b,c` / `--uids-file path`, `--project` / `--dataset` overrides.

## Step 3 — Confirm coverage

A cost query that wants to filter to `env = 'prod'` should treat the run as ready when the missing
count is ~0. Expect the overwhelming majority of users to resolve `prod` on the first Aura API
call — `dev` and `unknown` are both small minorities. Re-run until coverage is adequate before
relying on the dimension for a prod-only filter.

## Notes & caveats

- **Two-hop resolution, prod first.** Most cost-table users are prod, so the prod call resolves
  the vast majority in one round trip; only a small minority need the dev fallback.
- **`unknown` is terminal, not an error.** A uid that 404s on both backends genuinely doesn't
  exist in either — it's recorded once and never retried.
- **Anything else is transient.** Non-200/non-404 statuses, timeouts, and connection errors leave
  the uid unresolved so a flaky call (or a rate-limited dev backend) doesn't wrongly land it as
  `unknown`. If a run reports a large transient-unresolved count, lower `--workers` and re-run.
- **The view's `INNER JOIN` also drops not-yet-resolved users, not just dev/unknown.** Any
  `user_id` in the cost table that hasn't been backfilled into `aurie_user_env` yet (e.g. a user
  who joined since the last run) has no matching row, so the `INNER JOIN` excludes it from
  `aurie_per_user_daily_cost_prod` too — same effect as `dev`/`unknown`, but it's a coverage gap,
  not an environment classification. Re-run the backfill regularly (no args, so it only fetches
  what's missing) to keep this gap near zero; a stale backfill silently drags real prod users out
  of the view's totals.
- **Non-PII only.** Never store any field from the `/users` response body — only the resolved
  environment label and how it was resolved.
- **Run one at a time; consumers dedup defensively** (append-only, no unique key).

## Origin

[PROD-3589](https://linear.app/aura-health/issue/PROD-3589) Part A — the per-user environment
dimension that lets prod cost/margin aggregates exclude dev-environment users; sibling of
[`eng:backfill-aurie-user-channel`](../backfill-aurie-user-channel/SKILL.md) and
[`eng:backfill-aurie-user-ai-created`](../backfill-aurie-user-ai-created/SKILL.md).
