---
name: backfill-aurie-user-channel
description: >
  Backfill and maintain the acquisition-channel user dimension in BigQuery
  (aura-main.aurie_cost.aurie_user_channel) — the "make sure we have an acquisition channel for
  every user before cost-by-channel analysis" prerequisite. Trigger on: "backfill user channel",
  "backfill acquisition channel", "backfill aurie user channel", "refresh aurie_user_channel",
  "cache acquisition source". Resolves each user's non-PII acquisition attributes
  (aiAppstackCampaign, sourcePlatform, sourceType) from the Aura API and appends only resolved rows
  (append-only, immutable). The coverage guard that eng:chart-aurie-cost-by-channel calls first. Do
  NOT use to render charts (that's the chart skill, which composes this one); this only fills the
  dimension table.
metadata:
  version: "3.3.1"
knowledge:
  reads:
    - Guide/Index                  # baseline routing aid (universal)
    - Tools/BigQuery               # dataset/table conventions, GCP_KEY auth, bq.py
    - Tools/Aura API               # GET /users/:userId -> aiAppstackCampaign / sourcePlatform
    - Compliance/Privacy           # reads /users PII — store only non-PII channel attributes
---

# Backfill Aurie User Channel

## Goal

Keep **`aura-main.aurie_cost.aurie_user_channel`** — a per-user dimension mapping Firebase
`user_id` → **non-PII acquisition attributes** — complete for every user a cost-by-channel query
will touch. `eng:chart-aurie-cost-by-channel` joins the per-user cost table to this dimension, so
this must run (and reach coverage) before that analysis. It's the write-side maintainer; readers
just `JOIN`.

## Why a separate skill

The channel isn't in the cost store or (broadly) in Mixpanel — Mixpanel's `attribution_*` columns
exist only on the `ai_subscription_activated` event (converters) and `appsflyer_user_attributed`
(~0.7% of cost users). The only broad-coverage source is a per-user Aura API call, so we cache it in
BigQuery and only fetch what's missing — the chart skill (and any future by-channel analysis) joins
instead of re-walking the API.

## Table

`aura-main.aurie_cost.aurie_user_channel` — clustered by `user_id`, unpartitioned, **append-only**:

| Column | Type | Notes |
|---|---|---|
| `user_id` | STRING | Firebase uid (= cost table `user_id`, the join key) |
| `ai_appstack_campaign` | STRING | paid Appstack campaign name; **null = organic/other** |
| `source_platform` | STRING | `AIiOS` \| `AurieWeb` \| `UserWeb` … |
| `source_type` | STRING | `Organic` … (often null) |
| `resolved_via` | STRING | which host answered: `prod_users` \| `dev_users` \| `not_found_both`. **Read `NULL` as `prod_users`** — only a prod 200 can produce a stored row without one |
| `fetched_at` | TIMESTAMP | when this row was resolved |

**No PII** — the Aura `/users` response carries email/name; only these marketing/platform attributes
are stored. Acquisition channel is immutable (set at install), so the table is pure append — never
`UPDATE`/`DELETE`/`MERGE`. Read it deduped (`QUALIFY ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY
fetched_at DESC) = 1`) so a rare append-only duplicate can't double-count a user.

## API-first

No BigQuery MCP; API-first via `GCP_KEY` (BigQuery) and `AURA_API_TOKEN` (Aura API). Ships its own
copy of `bq.py` (canonical in `eng:query-bigquery`). Needs `requests` + `google-cloud-bigquery` —
run with the framework python that has `requests`.

## Step 0 — Prerequisites

```bash
test -n "$AURA_API_TOKEN" && echo "aura: OK" || echo "aura: MISSING"
test -n "$GCP_KEY"        && echo "gcp: OK"  || echo "gcp: MISSING"
```

Both required. `AURA_API_TOKEN` → the Aura API; `GCP_KEY` → BigQuery.

## Step 1 — Preview coverage (dry run)

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/backfill-aurie-user-channel/scripts/channel_backfill.py --dry-run
```

Prints requested UIDs (default = all distinct `user_id` in `aurie_per_user_daily_cost`), how many
are already in the table, and how many are missing — no writes.

## Step 2 — Run the backfill

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/backfill-aurie-user-channel/scripts/channel_backfill.py
```

For each missing UID: `GET /users/<uid>` on **prod first, then dev on a 404** (200) → store
`aiAppstackCampaign` / `sourcePlatform` / `sourceType` (even if all null — the user exists, no
campaign just means organic), plus `resolved_via` recording which host answered. Appends the
recorded rows, then prints a coverage report (rows appended, how many carry a paid campaign, the
`resolved_via` split, and the transient count). Idempotent + incremental — a re-run only fetches
what's still missing.

**Outcomes are terminal-vs-transient, and only one of them retries:**

| outcome | when | effect |
|---|---|---|
| `prod_users` | 200 on prod | row stored with its attributes |
| `dev_users` | 404 on prod, 200 on dev | row stored with its attributes |
| `not_found_both` | 404 on **both** hosts | row stored, attributes **null** — terminal, never re-fetched |
| transient | non-200/non-404 on either leg, network/parse failure, malformed uid | left unresolved, **retried next run** |

`resolved_via` is what separates *"organic user with no campaign"* from *"no record to read"* — both
are all-null in the attribute columns, so without it the two are indistinguishable. **Read a `NULL`
`resolved_via` as `prod_users`**: only a prod 200 could produce a stored row without one.

Useful flags: `--limit N` (cap fetches for a test run), `--workers N` (Aura API concurrency, default
16), `--uids a,b,c` / `--uids-file path`, `--project` / `--dataset` overrides.

## Step 3 — Confirm coverage

A cost-by-channel query should treat the run as ready when the missing count is ~0 (or it reports
`unknown`-channel users). Re-run until coverage is adequate before joining.

## Notes & caveats

- **`aiAppstackCampaign` is the paid-campaign signal**, present on a minority of users (paid
  acquisition); null means organic/other, which is the majority. `sourcePlatform` is the app surface
  (iOS/web), near-universal but coarse (platform, not marketing channel).
- **A non-zero unresolved count is not a signal on its own — read the split.** `prod_users` /
  `dev_users` / `not_found_both` are stable properties of the account that a re-run cannot change,
  so they persist at a steady level indefinitely. **Only the transient bucket means something went
  wrong**, and it is the only one worth investigating or re-running for.
- **`not_found_both` users are recorded, not left pending**, and they land in the chart's `unknown`
  bucket — they have no channel to attribute. Recording them is what distinguishes *unattributable*
  from *not yet attempted*; per `/CLAUDE.md` → *Tag, don't drop, wherever the output persists*, since
  this table is read long after the run.
- **Non-PII only.** Never store email, name, referralCode, or any personal field — only the campaign
  name + platform + type.
- **Run one at a time; consumers dedup defensively** (append-only, no unique key).

## Origin

Split from cost-by-channel so the channel dimension is cached once and reused —
[PROD-3508](https://linear.app/aura-health/issue/PROD-3508); sibling of
[`eng:backfill-aurie-user-ai-created`](../backfill-aurie-user-ai-created/SKILL.md).
