---
name: backfill-aurie-user-ai-created
description: >
  Backfill and maintain the aiCreatedAt user dimension in BigQuery
  (aura-main.aurie_cost.aurie_user_ai_created) — the "make sure we have an AI/Aurie
  join date for every user before any cohort SQL" prerequisite. Trigger on: "backfill
  ai created", "backfill aiCreatedAt", "backfill aurie user ai created", "update the
  ai-created dimension", "ensure ai created dates", "refresh aurie_user_ai_created".
  Also the coverage guard that cohort/age-based cost skills call first — it resolves
  each user's aiCreatedAt from the Aura API (Postgres), falling back to their earliest
  AI event in Mixpanel, and appends only resolved rows (append-only, immutable). Do NOT
  use to compute cohort costs or render charts (that's the cohort-cost skill, which
  composes this one); this skill only fills the dimension table.
metadata:
  version: "3.6.1"
knowledge:
  reads:
    - Guide/Index                  # baseline routing aid (universal)
    - Tools/BigQuery               # dataset/table conventions, GCP_KEY auth, bq.py
    - Tools/Aura API               # GET /users/:userId -> aiCreatedAt (+ the sentinel)
    - Tools/Mixpanel               # Nessie event tables for the first-AI-login fallback
    - Compliance/Privacy           # reads /users PII — store UID + timestamps only
---

# Backfill Aurie User aiCreatedAt

## Goal

Keep **`aura-main.aurie_cost.aurie_user_ai_created`** — a per-user dimension mapping
Firebase `user_id` → **`ai_created_at`** (the AI/Aurie join date) — complete for every
user a cohort query will touch. Cohort/age-based cost analysis joins the per-user cost
table to this dimension, so this must run (and reach full coverage) **before** any such
SQL. It's the write-side maintainer of that table; readers just `JOIN` against it.

## Why a separate skill

`aiCreatedAt` isn't cheap to get — it's a per-user Aura API call, with a Mixpanel
fallback — so we cache it in BigQuery and only fetch what's missing. Splitting it out
means the cohort skill (and any future per-user-AI-age analysis) can call this as a
guard and trust the join, instead of re-fetching thousands of users every run.

## Table

`aura-main.aurie_cost.aurie_user_ai_created` — clustered by `user_id`, unpartitioned,
**append-only**:

| Column | Type | Notes |
|---|---|---|
| `user_id` | STRING | Firebase uid (= cost table `user_id`, the join key) |
| `ai_created_at` | TIMESTAMP | AI/Aurie join date |
| `created_at` | TIMESTAMP | Aura account creation (nullable; kept only to show it ≠ aiCreated) |
| `source` | STRING | `postgres` \| `postgres_dev` \| `ai_onboarding` \| `first_ai_login` |
| `fetched_at` | TIMESTAMP | when this row was resolved |

**No `premium_ai`** — premium status is mutable (a user can cancel), so it's read *live*
at query time from the cost table's `subscription_state`, never cached here. **No PII** —
the Aura `/users` response carries email/name; only the two timestamps are ever stored.

`ai_created_at` is immutable and we only ever insert UIDs not already present, so the
table is pure append — never `UPDATE`/`DELETE`/`MERGE`, no dedup, no conflicts.

## API-first

No BigQuery MCP; the skill is API-first via `GCP_KEY` (BigQuery + Mixpanel Nessie) and
`AURA_API_TOKEN` (Aura API). Ships its own copy of `bq.py` (canonical in
`eng:query-bigquery`). Needs `requests` + `google-cloud-bigquery` — run with the
framework python that has `requests` (see `/CLAUDE.md` → "Default python3 lacks requests").

## Step 0 — Prerequisites

```bash
test -n "$AURA_API_TOKEN" && echo "aura: OK" || echo "aura: MISSING"
test -n "$GCP_KEY"        && echo "gcp: OK"  || echo "gcp: MISSING"
```

Both required. `AURA_API_TOKEN` → the Aura API (**both** the prod and dev hosts — the same
Bearer token authenticates each); `GCP_KEY` → BigQuery + the Mixpanel Nessie dataset (the
service account must be a viewer on
`mixpanel-prod-1.mixpanel_nessie_day_partitioned_963821` for the fallback).

## Step 1 — Preview coverage (dry run)

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/backfill-aurie-user-ai-created/scripts/backfill.py --dry-run
```

Prints how many UIDs are requested (default = all distinct `user_id` in
`aurie_per_user_daily_cost`), how many are already in the table, and how many are
missing — no writes.

## Step 2 — Run the backfill

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/backfill-aurie-user-ai-created/scripts/backfill.py
```

For each missing UID:
1. `GET /users/<uid>` on **prod** → `aiCreatedAt`. If real (non-null, **not a batch
   stamp** — see below) → `source=postgres`.
2. Prod 404 → the same call on **dev** → `source=postgres_dev`. Dev-environment users
   404 on prod, carry a real `aiCreatedAt` on dev, and emit nothing into the prod
   Mixpanel project — so without this leg neither path can reach them (see *The
   unresolved residue* below).
3. `aiCreatedAt` null or a batch stamp → **`onboarding.shown.consumerAIOnboarding`** off
   the same response → `source=ai_onboarding`.
4. Still nothing → earliest event across every Aurie event table in the Mixpanel
   Nessie data → `source=first_ai_login`. **Two namespaces**: `ai_*` (the standalone
   Aurie app) and `aiaura_*` (Aurie inside the Aura app). Bare `chat_*` /
   `conversation_*` are the Aura app's own chat feature and are deliberately
   excluded — see *Event namespaces* below.
5. Else → left unresolved (not inserted; retried next run), and **classified by reason**.

Appends only resolved rows, then prints a coverage report (postgres / postgres_dev /
ai_onboarding / first_ai_login / unresolved-by-reason). Idempotent and incremental — a
re-run only fetches what's still missing.

## Batch stamps: detect by shape, never by value

Postgres stamps batches of users with an exact-noon `aiCreatedAt` that is not a real join
date. `2026-02-11T12:00:00Z` is one of at least fourteen such values, so a hardcoded list
is not a guard — it goes stale the next time the upstream batch runs.

**The shape is: exactly `12:00:00` UTC *and* shared by at least `BATCH_STAMP_MIN_USERS`
users.** Both halves are load-bearing. A real join date can legitimately land on noon, so
the time alone discards genuine dates; the cluster alone matches ordinary timestamps. The
signal is stark — on a normal day the table holds ~1 row per distinct `ai_created_at`, on
a stamp day it holds hundreds sharing one.

Three detectors, because each alone has a hole:

| detector | sees | hole it fills |
|---|---|---|
| `known_batch_stamps()` | noon clusters already in the dimension | the steady state |
| `stamp_by_onboarding()` | one user whose noon value disagrees with `consumerAIOnboarding` on the calendar day | a **brand-new** batch, invisible to the dimension |
| `fresh_batch_stamps()` | noon clusters within the current run's own results | a new batch whose users carry no onboarding timestamp |

The cold-start pair matters because `known_batch_stamps()` can only see what is already
stored, while the run fetches only UIDs *missing* from the table — so without them a new
batch's first wave is written as `source=postgres` and, being present, never retried.

The 2026-02-11 sentinel is included unconditionally: the skill refuses it, so it is never
stored, so it can never be rediscovered from the table.

Background and the measured impact: [PROD-6109](https://linear.app/aura-health/issue/PROD-6109).

## Event namespaces: two count as Aurie, one looks like it and is not

The proxy union covers **`ai_*`** (the standalone Aurie app) **and `aiaura_*`** (Aurie used
inside the Aura app). `aiaura_` does not match `ai_` — the underscore is the discriminator —
so a union built on `ai_` alone is *disjoint* from the entire in-Aura surface and cannot see
those users at all. They were reported `no_ai_activity` while having signed up and
conversed. Discovering the table list from `INFORMATION_SCHEMA` never protected against
this: the predicate decides what the discovery can see, and the predicate is the
hand-curated part.

**Bare `chat_*` / `conversation_*` are NOT Aurie** — they are the Aura app's own chat
feature (`chat_beta_joined`, `chat_ai_onboarding_started`, `conversation_mode_selected`).
Folding them in is worse than the under-coverage it appears to fix: measured against the
stored rows, **56 of 62** resolved users with `chat_*` events would be back-dated, by 651
days on average and up to 1,921, silently placing real Aurie users in cohorts they were
never in. `NON_AURIE_TABLE_PREFIXES` names them so the exclusion carries its reason.

Background: [PROD-6096](https://linear.app/aura-health/issue/PROD-6096).

## The AI-onboarding rung

`GET /users/:id` — the call step 1 already makes — returns
`onboarding.shown.consumerAIOnboarding`. Measured against 592 users holding both a real
(non-stamped) `aiCreatedAt` and an onboarding timestamp: **97.3% land on the exact same
calendar day, median difference 0 seconds, p90 0 seconds**, and it is present on 500/500
users in a control sample. That is a tighter match than the Mixpanel proxy below, at zero
extra API calls, and it is a real product event rather than a proxy for one.

**It rescues, never overrides.** Only users Postgres could not answer for reach it. Of
the 13 users (2.2%) who disagree by more than a day, **10 have onboarding LATER** — so a
ladder that let onboarding win would push those cohorts later, which is the same silent
cohort corruption the batch-stamp fix removes. Re-measure and update these figures if the
rung is reordered.

Useful flags: `--limit N` (cap fetches for a test run), `--workers N` (Aura API
concurrency, default 16), `--uids a,b,c` / `--uids-file path` (specific UIDs instead of
the whole cost table), `--project` / `--dataset` overrides.

## Step 3 — Confirm coverage

A cohort query should treat the run as ready when the only remaining unresolved UIDs are
**stable** ones (see below) — or when it explicitly excludes and reports them. Re-run
while `transient` is non-zero; re-running does not clear a stable reason.

## Step 2b — Restate stored batch stamps (one-off, guarded)

`backfill.py` only fetches UIDs **missing** from the dimension, so it never revisits a
user whose stored value is wrong. Rows written before the shape detector landed still
carry a batch stamp. `restate_stamps.py` corrects them:

```bash
# dry run — prints what would change, writes nothing
python3 ${CLAUDE_PLUGIN_ROOT}/skills/backfill-aurie-user-ai-created/scripts/restate_stamps.py
# apply
python3 ${CLAUDE_PLUGIN_ROOT}/skills/backfill-aurie-user-ai-created/scripts/restate_stamps.py --confirm
```

It selects rows whose **winning** value is a batch stamp (same shape rule, read through
the dedup), re-resolves each through `consumerAIOnboarding` then the Mixpanel proxy, and
**appends** a corrected row with `source=ai_onboarding_restated`. A user it cannot resolve
is left alone and counted — never guessed at.

**Append, never `UPDATE`.** The table is append-only and `ai_created_at` is immutable, so
a correction is a new row with a later `fetched_at`. Every reader resolves the dimension
with `ROW_NUMBER() … PARTITION BY user_id ORDER BY fetched_at DESC = 1`, so the appended
row wins. `tests/test_ai_created_dedup.py` enforces that: it discovers readers by shape
and fails when a new one omits the dedup.

**Re-running is a no-op.** The selection reads the current winning value, and a corrected
row is by construction not a batch stamp, so the pass cannot walk a value further or
restate a user twice. A transient API failure leaves the user untouched rather than
falling through to a weaker rung — the correction is written once, so a blip must not
cache a proxy date over an available `consumerAIOnboarding`.

## The unresolved residue

A permanently non-zero unresolved count is normal, and the run says which kind:

| Reason | Meaning | Does a re-run help? |
|---|---|---|
| `no_ai_activity` | prod user, no `aiCreatedAt`, no AI event in Mixpanel | Only once they act |
| `dev_no_ai_activity` | dev user with no `aiCreatedAt` on dev; the prod Mixpanel project holds no dev traffic, so the proxy can't answer | Only if dev stamps one |
| `no_user_record` | 404 on prod *and* dev — a stable orphan (QA / test / deleted id) | No |
| `transient` | Aura API error, malformed uid, or the Mixpanel proxy never ran for this UID (table discovery or its chunk failed) | **Yes** — this is the one to watch |

**A stable reason requires a verdict, not an absence.** `no_ai_activity` means the proxy
was asked about this UID and found nothing; a UID whose fallback query never completed is
`transient`, not stable. Keeping those apart is what stops a BigQuery outage from being
reported as a permanently unresolvable cohort.

**Nothing is suppressed from the retry set.** A stable reason describes the user *today*,
not forever: the `no_ai_activity` cohort is in the cost table because it is spending, so
one AI event flips it to resolvable. Marking it terminal trades a self-explaining count
for a permanently missed join date.

**Impact when it doesn't resolve.** Every `chart-aurie-*` cohort chart joins this
dimension off the prod-only view, so a dev user missing here changes nothing (the view
already excludes them) and a prod user missing here drops out of the cohort charts. For
scale: at the last measurement the whole prod residue was 58 of 11,507 users and **\$6.10
of \$23,924.90** all-time prod cost — 0.025%. Re-measure before treating it as material:

```sql
SELECT COUNT(DISTINCT IF(a.user_id IS NULL, v.user_id, NULL)) AS unresolved_users,
       ROUND(SUM(IF(a.user_id IS NULL, v.total_cost, 0)), 2)  AS unresolved_cost,
       ROUND(SUM(v.total_cost), 2)                            AS total_cost
FROM `aura-main.aurie_cost.aurie_per_user_daily_cost_prod` v
LEFT JOIN (SELECT DISTINCT user_id FROM `aura-main.aurie_cost.aurie_user_ai_created`) a
  USING (user_id)
WHERE v.user_id != 'Other'
```

## Notes & caveats

- **`created` ≠ `aiCreatedAt`.** Mixpanel/Aura project 963821 is shared by Aura
  (meditation) and Aurie (AI); a user's Aura `createdAt` can be years old (legacy) — only
  `aiCreatedAt` marks the AI join. `createdAt` also has its own sentinels (2016-06-19,
  2023-05-10) — don't use it as the join anchor.
- **`aiCreatedAt` sentinel.** `2026-02-11T12:00:00.000Z` (exact noon, batch) was stamped
  on users who joined AI before the field existed; it's treated as unknown → fallback.
  It is **not the only one** — see *Batch stamps* above; detect by shape, never by value.
- **Fallback is a proxy, over every AI event table.** "Earliest AI event of any kind"
  approximates the join date for users Postgres can't answer. The table list is discovered
  from `INFORMATION_SCHEMA` per run (`ai_*` carrying both `userid` and `time`), never
  hand-curated — a fixed list silently misses users whose first event isn't on it, and
  the `userid`/`time` filter is load-bearing because one table missing either column
  makes the whole `UNION` fail to compile. Bytes scanned are ~90 MB per chunk regardless
  of how many UIDs are in flight, which is why the chunk is sized for query-text length
  rather than scan cost. Widening the union is safe at day/week grain: measured over the
  119 UIDs resolved this way, the full union moves 44 timestamps earlier and none by a
  whole day. Re-check that before narrowing or widening it again.
- **Subscription events won't help here** — they only cover paying users; many Aurie users
  are free. This dimension covers all users via `aiCreatedAt`.
- **Run one at a time; consumers dedup defensively.** The table is append-only with no
  unique key, so two overlapping runs could in theory append a duplicate user row (the
  skill re-checks existing UIDs right before writing to narrow this window). Read it with
  a dedup so a rare duplicate can't double-count a user in a cohort join:
  `… QUALIFY ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY fetched_at DESC) = 1`.

## Origin

Split from the cohort-based cost skill so backfill-to-complete always precedes cohort SQL
— [PROD-3502](https://linear.app/aura-health/issue/PROD-3502). Dev-API leg, full-union
fallback and residue classification — [ENG-1119](https://linear.app/aura-health/issue/ENG-1119).
