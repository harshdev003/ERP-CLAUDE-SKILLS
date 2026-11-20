---
name: run-cost-harvest-cadence
description: >
  One-invocation orchestrator for the Aurie cost cadence (Group 5 of the Knowledge &
  Conversation Data runbook). Drives the cost skills in order: catches the daily BigQuery cost
  store up to yesterday, rebuilds the revenue dimension, backfills aiCreatedAt, tags each
  user's environment and refreshes the prod-only view, shows the trend, runs the weekly row
  when a Sun-Sat week completes, and runs the cost-change harvest. A "daily only" scope runs
  just the four foundation writes. Trigger on "run cost cadence", "cost cadence", "run the
  cost group", "run group 5", "do the daily cost task", "catch up the cost store", "the cost
  store is behind", "what's due on cost", "the revenue dimension is stale", "harvest cost
  changes", "harvest costs", "backfill the ai-created dimension", "tag the user environments",
  "run just the daily cost stores, skip reporting", "keep the cost, subscription, aiCreatedAt
  and env stores current, no reports", or "refresh the subscription table". Do NOT use to run
  a single member directly.

metadata:
  version: "3.4.0"
knowledge:
  reads:
    - Guide/Index                 # baseline routing aid (universal)
    - Tools/GCP                   # BigQuery aura-main.aurie_cost is the source of truth for "what's due"
    - Compliance/Privacy          # composed skills handle UIDs; this cadence stays aggregate-only
    - Signals/Aurie Weekly Cost   # Step 1: recorded snapshots are the weekly step's due-check
    - Tools/BigQuery                 # aura-main.aurie_cost store access (GCP_KEY)
---

# Run Cost Harvest Cadence

## Goal

Drive the whole Aurie **cost cadence** in a single session so the cost data
layers stay fresh — without the operator having to remember which skill runs
daily vs. weekly vs. per-cycle. This skill owns **sequencing and "what's due"
detection only**; every actual data operation is delegated to the existing
cost-family skill that owns it.

This is the **Aurie cost group** (Group 5 as of the current runbook) of the
[`Knowledge & Conversation Data — Run Cadence`](https://app.notion.com/p/aurahealth/Knowledge-Conversation-Data-Run-Cadence-39d75e23af208187a5fef673ed15cdfe)
runbook — identified by name, not by number, since the groups get renumbered as
the runbook grows. **BigQuery `aura-main.aurie_cost` is the source of truth** for
every cost store; the local CSVs are a rebuildable cache.

**Composition, not duplication.** This skill calls its members through the Skill
tool and never reimplements their logic (Architecture principle 1 + best-practice
1). The members:

| Cadence | Skill | What it does |
|---|---|---|
| Daily (foundation, run first) | [`eng:store-aurie-user-daily-cost`](../../../eng/skills/store-aurie-user-daily-cost/SKILL.md) | Per-user LLM + voice **cost** for one day → BigQuery. Idempotent per day. |
| Daily (foundation) | [`eng:build-aurie-user-subscription`](../../../eng/skills/build-aurie-user-subscription/SKILL.md) | Per-user **revenue** dimension → BigQuery. Full rebuild, self-healing. |
| Daily (foundation) | [`eng:backfill-aurie-user-ai-created`](../../../eng/skills/backfill-aurie-user-ai-created/SKILL.md) | Per-user **aiCreatedAt** dimension → BigQuery (`aurie_user_ai_created`). Append-only, incremental — only fetches UIDs still missing. |
| Daily (foundation, run last of the four) | [`eng:backfill-aurie-user-env`](../../../eng/skills/backfill-aurie-user-env/SKILL.md) | Per-user **env** dimension (`aurie_user_env`) **and** the `aurie_per_user_daily_cost_prod` view every `chart-aurie-*` reads. Append-only, incremental. |
| Daily | [`eng:report-aurie-daily-costs`](../../../eng/skills/report-aurie-daily-costs/SKILL.md) | Combined daily-trend table (OpenAI + multi-vendor voice). Read-only. |
| Weekly (when a Sun–Sat closes) | [`eng:report-aurie-weekly-costs`](../../../eng/skills/report-aurie-weekly-costs/SKILL.md) | One spreadsheet row + per-tier breakdown for the completed week. |
| Per cost cycle | [`eng:harvest-cost-changes`](../../../eng/skills/harvest-cost-changes/SKILL.md) | Sweeps GitHub / Linear / Slack, updates the Aurie Cost Engineering ledger. Plan-before-apply. |
| On demand | [`eng:build-aurie-cost-report`](../../../eng/skills/build-aurie-cost-report/SKILL.md) | Composite HTML economics report (published as an Artifact). |
| Weekly (a week of new data) | [`eng:build-anthropic-usage-report`](../../../eng/skills/build-anthropic-usage-report/SKILL.md) | Claude seat-usage report, republished in place. Not Aurie cost — it rides this cadence because it is the one with an Artifact tool. |

## Inputs

- Usually none — invoked bare ("run the cost cadence").
- Optional scope narrowing from the user: "just the daily store", "skip the
  harvest", "only the weekly row", "include the full report". Honour it — run
  only the requested members.
- **Daily-only / foundation-only scope** ("run just the daily cost stores,
  skip reporting", "keep the cost/subscription/aiCreatedAt/env stores current, no
  reports", "sync today's foundation data only"): run only the
  four foundation writes — Step 2 (cost store), Step 2b (revenue dimension),
  Step 2c (aiCreatedAt dimension), Step 2d (env dimension + prod view) — in that
  order, then go straight to Step 7
  and summarise. Skip Steps 3–6 entirely (daily trend, weekly row, cycle
  harvest, composite report), even if one of them would otherwise be due. This
  is the scope to use for "keep the BigQuery cost/subscription/aiCreatedAt/env
  stores current" with no reporting side effects. Don't confuse this with a
  request for `eng:harvest-cost-changes` specifically ("harvest costs",
  "run the cost harvest") — that's a different member of this cadence (Step 5)
  that this scope explicitly skips.
- Optional `--issue PROD-XXXX` passthrough for the harvest step when cycle
  auto-detection picks the wrong card.

## Workflow

Use the framework `python3` (`/Library/Frameworks/Python.framework/Versions/3.12/bin/python3`)
for any BigQuery/`requests` probe — the default `python3` lacks
`google-cloud-bigquery`.

### Step 1 — Resolve "what's due" (read-only probe, no writes)

Establish today's date and the store's coverage before running anything.

1. **Today (UTC)** and **yesterday (UTC)** — yesterday is the most recent
   *complete* day (OpenAI's Costs / stored-completions lag ~24h).
1a. **Store-vs-warehouse preflight — run this before anything else, and honour
   its refusal.** Invoke [`eng:store-aurie-user-daily-cost`](../../../eng/skills/store-aurie-user-daily-cost/SKILL.md)
   to run its bundled `bq_preflight.py` (composition — never reach into its
   `scripts/` directly) and paste the lines it returns into the plan below. It
   asks BigQuery's own `INFORMATION_SCHEMA` whether the store and the warehouse
   still agree, one line per table:

   - **Schema drift → REFUSED (exit 1).** A column the store declares and
     BigQuery lacks makes every partition load 400. **Stop the cadence here** —
     do not run Step 2. Report the column, the table, and the remedy the probe
     prints.
   - **Max-date divergence → reported, never refused (exit 0).** Local vs
     warehouse max date per table, plus a **MIXED STATE** line when the three
     warehouse tables disagree with each other.
   - **Exit 2** means the probe could not run: say so and carry on.

   **This cadence errs toward refusing.** A tick stopped early costs one re-run;
   a tick that proceeds into a store that cannot sync leaves a day that exists
   only locally, and every `chart-aurie-*` read of `aurie_per_user_daily_cost_prod`
   then renders clean, plausible and **understated** with no error. What each
   outcome means, the remedy's own preconditions, and the incident behind it:
   [`references/preflight.md`](references/preflight.md).

2. **Last stored day** — the max `date` in the authoritative per-user table.
   Probe 1a already prints it for all three stores, warehouse and local side by
   side; take it from there rather than re-querying. If you need it on its own,
   via [`eng:query-bigquery`](../../../eng/skills/query-bigquery/SKILL.md)
   (composition; don't hand-roll a BigQuery client here):

   ```sql
   SELECT MAX(date) AS max_date FROM `aura-main.aurie_cost.aurie_per_user_daily_cost`
   ```

2b. **Revenue-dimension build stamp** — `aurie_user_subscription` is rebuilt
   wholesale, so it has no `date` watermark; its currency is the build stamp:

   ```sql
   SELECT MAX(fetched_at) AS built_at FROM `aura-main.aurie_cost.aurie_user_subscription`
   ```

   Due whenever that is **older than ~24h**. Unlike the cost store this has no
   retention deadline (it reads the permanent Mixpanel Nessie export, so one run
   reconstructs all history) — but a stale copy silently understates revenue in
   every margin/payback/revenue readout, which is exactly how it went 12 days
   stale in July 2026 and knocked ~23% off the revenue those charts reported.
   The readers now fail closed on it (`bq.assert_fresh`), so a stale dimension
   blocks them rather than quietly skewing them.

2c. **aiCreatedAt dimension coverage** — `aurie_user_ai_created` has no build
   stamp to check; instead invoke
   [`eng:backfill-aurie-user-ai-created`](../../../eng/skills/backfill-aurie-user-ai-created/SKILL.md)
   in its dry-run mode (composition via the Skill tool — this is a sibling
   plugin's skill, so never reach into its `scripts/` directly; that skill's
   own SKILL.md resolves its own script path). It prints
   `requested / already in table / missing`.

   Treat this pre-catch-up number as an **early estimate only, not the final
   due-check** — Step 2 (below) can itself add new UIDs to the cost-table
   population this probe reads from. The authoritative check happens at the
   start of Step 2c, after Step 2 has run.

2d. **env dimension coverage** — same shape as 2c and the same caveat (an early
   estimate; the authoritative check is in Step 2d). Invoke
   [`eng:backfill-aurie-user-env`](../../../eng/skills/backfill-aurie-user-env/SKILL.md)
   in its dry-run mode; it prints `requested / already in table / missing`.

   **Report the untagged share in dollars, not just the UID count.** Untagged
   users are, by construction, the *most recent* ones — the dimension is
   incremental, so every user who first appears after the last run stays untagged
   until the next one. That makes the count look small against the all-time
   population while the cost it hides concentrates entirely in the window the
   charts actually render. Left unrun for ~11 days it reached 1,946 users —
   11.9% of all-time per-user cost but **39% of the last 14 days and 59% of the
   last 7**, and 1,945 of those 1,946 were real prod users. Get the live figure
   from [`eng:query-bigquery`](../../../eng/skills/query-bigquery/SKILL.md):

   ```sql
   WITH e AS (SELECT user_id FROM (
     SELECT user_id, ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY fetched_at DESC) rn
     FROM `aura-main.aurie_cost.aurie_user_env`) WHERE rn = 1)
   SELECT ROUND(SUM(IF(e.user_id IS NULL, c.total_cost, 0)), 2) AS untagged_cost,
          ROUND(SUM(c.total_cost), 2)                           AS total_cost
   FROM `aura-main.aurie_cost.aurie_per_user_daily_cost` c
   LEFT JOIN e USING (user_id)
   WHERE c.user_id != 'Other' AND c.date >= DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY)
   ```

3. **Last completed Sun–Sat week** — do **not** compute this boundary yourself in
   UTC. `eng:report-aurie-weekly-costs` resolves the most-recent completed week in
   **America/Los_Angeles** (PDT/PST), so a naive UTC calc disagrees for the
   ~7–8h when it's already Sunday UTC but still Saturday evening Pacific — you'd
   offer a week that PT hasn't closed yet. Instead: a weekly row is due only when
   a new PT week has *fully* closed **and** it is **strictly after** (`>`, not
   `≥` — equality means it's already recorded) the last week already recorded.
   When you're within that UTC/PT boundary window or unsure, defer to the weekly
   skill's own "most recent completed week" resolution rather than pre-deciding —
   offer it, don't assume.

   **Read "the last week already recorded" off `Signals/Aurie Weekly Cost`, not
   off the operator.** Resolve that path with
   [`shared:retrieve-knowledge`](../../../shared/skills/retrieve-knowledge/SKILL.md)
   (its latest-snapshot recipe already enumerates the index page's dated
   children — don't hardcode the page id here) and take the highest
   `YYYY-MM-DD` across the `Aurie Weekly Cost — <date> snapshot` titles. The date
   is the **Sunday that starts** the week, so a snapshot titled `2026-07-19`
   covers 7/19–7/25 and the next one due is `2026-07-26`. This reads the same
   child listing `eng:report-aurie-weekly-costs` checks in its own Step 4a, so
   the two agree by construction and no second source can drift.

   Every **missing** Sunday between the highest recorded one and the last closed
   PT week is due, not just the most recent — report them as a list so a
   multi-week gap surfaces as a multi-week gap.

   **Why this is a probe and not a question.** Every other line of this plan is
   derived (`MAX(date)`, a build stamp, a dry-run count); asking the operator for
   this one made it the only step whose due-ness nobody could see, and it went
   unrecorded for weeks at a stretch without anything reporting a gap. A missing
   snapshot has no shape — the sweep looks identical whether the week was
   recorded or silently skipped. Of the eight snapshots on the page at
   2026-08-06, only two were written in the week they covered; the rest arrived
   in two hand-noticed batch catch-ups (three on 2026-07-14, two on 2026-07-30).
4. **Cost cycle** — whether a cost-change harvest is due this cycle (the
   harvest skill auto-detects the current `AI Cost Review and Changes [Cycle N]`
   Linear card; the operator usually knows if it's already been run this cycle).

Then present a short **"what's due" plan** and confirm before any side-effect:

```
Cost cadence — as of <today UTC>
- Store vs warehouse:
  - per_user (aurie_per_user_daily_cost):  store max <YYYY-MM-DD> | warehouse max <YYYY-MM-DD> | schema OK (19 cols)
  - unit_costs (aurie_daily_unit_costs):   store max <YYYY-MM-DD> | warehouse max <YYYY-MM-DD> | schema OK (16 cols)
  - eng_kpis (aurie_daily_eng_kpis):       store max <YYYY-MM-DD> | warehouse max <YYYY-MM-DD> | schema OK (6 cols)
  (plus a ** BEHIND ** marker per table whose warehouse lags its store, and a
   ** MIXED STATE ** line when the three warehouse tables disagree. On schema
   drift this block is replaced by the REFUSE output and the cadence stops.)
- Daily store: last stored <YYYY-MM-DD>; DUE for <N> day(s): <d1>…<dN>   (or "up to date")
- Revenue dimension: built <YYYY-MM-DD HH:MM>; DUE (>24h) / current
- aiCreatedAt dimension: <N> UID(s) missing / current
- env dimension: <N> UID(s) untagged ($<X> of the last 7 days' per-user cost) / current
- Daily trend report: will run / skip (runs when the store caught up, or on request)
- Weekly row + snapshot: last recorded <YYYY-MM-DD>; DUE for <N> week(s): <w1>…<wN>   (or "not due")
- Cycle harvest: DUE this cycle / already run / skip
- Composite report: run? (default no unless asked)
```

**A refusal outranks "due".** If probe 1a refuses, nothing is due — the
cadence is *blocked*, which is a different report and a different ask. Say which
column drifted, which table, and what unblocks it; do not fall back to running
the steps the refusal does not literally name, because Steps 2b–2d read the
population Step 2 was supposed to extend and would report coverage against a
stale one.

**"Due" means a pending write** — store catch-up, a stale revenue dimension,
missing aiCreatedAt coverage, untagged users in the env dimension, a newly-closed
weekly row, or a cycle harvest.
**If nothing is due, say so and stop** — "store is current
through <date>, revenue dimension current, aiCreatedAt fully covered, env fully
tagged,
`Signals/Aurie Weekly Cost` recorded through <sunday>, harvest already run this
cycle → nothing to do." Name the weekly snapshot's coverage date rather than
saying the weekly row isn't due — the date is the evidence, and every other line
of this plan carries its own. That is a valid, common outcome (the runbook says
so). The read-only daily trend (Step 3) is **not** a reason to keep going:
on a nothing-due invocation, skip it too and just report the current coverage
date — unless the user explicitly asked to see the trend, in which case show it
before stopping.

**Daily-only / foundation-only scope:** if the user asked for just the
foundation writes (see Inputs), skip straight from this plan to Steps 2, 2b,
2c, 2d, then Step 7 — don't evaluate or mention the reporting steps' due-ness.

### Step 2 — Daily store catch-up (foundation — run first)

For each missing day from `last_stored + 1` through `yesterday` (inclusive, UTC),
invoke [`eng:store-aurie-user-daily-cost`](../../../eng/skills/store-aurie-user-daily-cost/SKILL.md)
for that date. It is **idempotent per day** (re-running a stored date is a no-op),
reconciles to the cent, and writes all three stores to BigQuery. Run days in
ascending date order. If a day fails to reconcile (`reconciles: False`), stop and
surface it — don't continue stacking days on a bad boundary.

**Boundary-day refresh:** if the previously-stored latest day was captured within
~24h of its UTC close, its OpenAI figure may have grown from late-arriving
completions. Per that skill's Notes, re-run the prior day's walk and re-store only
if its authoritative total moved.

### Step 2b — Revenue dimension refresh (foundation)

If Step 1's build stamp was over ~24h old, invoke
[`eng:build-aurie-user-subscription`](../../../eng/skills/build-aurie-user-subscription/SKILL.md).
Preview with `--dry-run` first and sanity-check the summary (periods, users, total
revenue) against the previous build — a large drop means a source problem, not a
refresh, so stop rather than replacing a good table with a worse one.

Unlike Step 2 this is a **full `CREATE OR REPLACE` rebuild**, which makes it
self-healing: one successful run repairs any number of missed days, so there's no
per-day catch-up loop. It's a pure BigQuery transform with no API calls, so it's
cheap enough to run whenever it's stale.

Order doesn't matter against Step 2 — the two write different tables and neither
reads the other. Both must be current before any margin/payback/revenue readout,
since those join the two.

### Step 2c — aiCreatedAt dimension backfill (foundation — run after Step 2)

**Run this after Step 2, not just off Step 1's earlier estimate.** Step 2c's
population is every UID in the cost table, and Step 2 can itself add new UIDs
(each newly-stored day can introduce users no prior day billed) — so a UID
freshly stored in this same run needs its own `--dry-run` check to be counted.
Re-invoke [`eng:backfill-aurie-user-ai-created`](../../../eng/skills/backfill-aurie-user-ai-created/SKILL.md)'s
dry-run mode now (fresh, post-Step-2) and proceed only if it still reports
`missing > 0` — Step 1's number is a useful early estimate but not the gate.

If missing, invoke the skill itself (no flags needed — it resolves every UID
missing from `aurie_user_ai_created` against the full cost-table population by
default).

Unlike Step 2b this is **append-only and incremental** — it only fetches UIDs
not already in the table, so there's no dry-run-vs-previous-build sanity check
to make; just run it. Report the resolved/unresolved split it prints
(`postgres` + `postgres_dev` + `first_ai_login` resolved vs. still-unresolved).
**Some UIDs staying unresolved is expected, not a failure** — the run classifies
the residue by reason and only the `transient` bucket is worth flagging; the
others are stable properties of the user (no AI activity at all, no Aura record
on either host) that a re-run cannot clear on its own. That skill's SKILL.md →
*The unresolved residue* owns the reason table; don't restate it here.

Order **does** matter against Step 2 (run after it, per above); order does not
matter against Step 2b — the revenue dimension is a separate independent
table. aiCreatedAt coverage must be current before any
cohort/tenure/curve/free-user chart, since those join against it as a coverage
guard.

### Step 2d — env dimension + prod-view refresh (foundation — run last of the four)

**Run this after Steps 2, 2b and 2c, and before any reporting step.** Like 2c its
population is every UID in the cost table, so re-invoke
[`eng:backfill-aurie-user-env`](../../../eng/skills/backfill-aurie-user-env/SKILL.md)'s
dry-run mode fresh (post-Step-2) rather than reusing Step 1's estimate; proceed if
it reports `missing > 0`.

If missing, invoke the skill itself (no flags). Append-only and incremental, same
as 2c. Report the `prod / dev / unknown` split it prints.

**At 0 missing there is nothing to do here — don't force a refresh.**
`aurie_per_user_daily_cost_prod` is a **logical** view (`table_type = VIEW`), so it
re-runs its own SQL on every read and already reflects whatever is in
`aurie_user_env`. Appending a row publishes that user to every `chart-aurie-*`
consumer immediately; no `CREATE OR REPLACE` is needed to make them visible.

The one case that *does* need a standalone `--view-only` is a changed **view
definition** — the excluded-date list and the env predicate live in the skill's
code, so after `eng:backfill-aurie-user-env` itself is updated the deployed view
still carries the old definition until it is re-run. That is a post-upgrade step,
not a daily one; the skill's own SKILL.md owns it.

**Why this is last of the four.** It is the only foundation step whose output other
steps *read* rather than merely append to. Step 2 adds the day's users, 2c resolves
their join dates, and only then does tagging + a view refresh publish them to the
prod aggregates. Running it before Step 2 tags yesterday's population and leaves
today's untagged until tomorrow — which is exactly the lag this step exists to close.

**A user missing here is silently excluded from every prod chart**, and the
exclusion is invisible in the chart itself — it renders a clean, plausible,
understated number. `eng:build-aurie-cost-report` surfaces it as a *Not-yet-tagged
users* line in its exclusions callout (distinct from the dev/QA line); if that
figure is material, this step is overdue.

### Step 3 — Daily trend report

Skip this step entirely under the daily-only / foundation-only scope (go
straight to Step 7). Otherwise, run this when Step 2 caught the store up (any day
stored) **or** the user asked for the trend. Skip it on a nothing-due
invocation (Step 1 already exited). Run
[`eng:report-aurie-daily-costs`](../../../eng/skills/report-aurie-daily-costs/SKILL.md)
(`--window last-7d` by default) and show its table. Read-only.

**Spike handling belongs to that skill's Step 3 — follow it rather than escalating off the ⚠
marker.** Its first move is to check the `Learnings/Aurie Cost Engineering/Cost Changes` ledger,
so a day whose cause is already diagnosed and fixed is reported as explained instead of re-opened
on every tick. Don't restate its rules here.

### Step 4 — Weekly row + snapshot (only when a Sun–Sat week newly completed)

For each week Step 1 flagged due — oldest first — run
[`eng:report-aurie-weekly-costs`](../../../eng/skills/report-aurie-weekly-costs/SKILL.md) and
surface the markdown row + TSV line for the spreadsheet. Prefer the
`--from-bigquery` mode for older/late weeks (BigQuery is gapless from 2026-06-01;
the live ElevenLabs API only retains ~10 days).

**Take that skill's Step 4 offer and record the `Signals/Aurie Weekly Cost`
snapshot.** The spreadsheet row and the snapshot are two deliverables of this
step, not one — the row is a paste into a sheet, the snapshot is what makes the
week answerable later without re-running anything. `eng:report-aurie-weekly-costs`
asks before writing it (that prompt is its contract, and it owns the schema and
the write); this cadence answers yes by default, because the snapshot is the
durable half of what Step 1 just detected as missing. Its own Step 4a pre-check
keeps a re-run from creating a second snapshot for a week already recorded.

Surface the resulting snapshot URL per week in the Step 7 summary, and if a week
is recorded **without** its snapshot — the caller declined, or the week predates
the store's 2026-06-01 gapless coverage so no accurate source exists — say which
week and why, rather than reporting the week as done.

### Step 5 — Per-cycle cost-change harvest (plan before apply)

If a harvest is due this cycle, run
[`eng:harvest-cost-changes`](../../../eng/skills/harvest-cost-changes/SKILL.md):

- Let it auto-detect the current `AI Cost Review and Changes [Cycle N]` card;
  pass `--issue PROD-XXXX` only if it picks the wrong one.
- Refresh the ledger's **headline metrics from BigQuery** (`aura-main.aurie_cost`)
  via `eng:query-bigquery`, not the local CSVs.
- **Show the proposed plan JSON (new rows / row updates / block updates) and wait
  for approval before applying** — never auto-apply the ledger writes.
- After approval, apply and confirm the watermark date stamped.

### Step 6 — Composite report (on demand)

Only if the user asked for it (or a stakeholder review is imminent), run
[`eng:build-aurie-cost-report`](../../../eng/skills/build-aurie-cost-report/SKILL.md) and publish
the HTML via the Artifact tool. To keep a single shared link, update the existing
report artifact in place (pass its `url`) rather than minting a new one.

### Step 6b — Claude seat usage report (weekly)

Read the live report (`Artifact` action `read`,
`https://claude.ai/artifact/61m6BGsENwSFP4pfQ95h8v`) for its `window.through`,
and take the newest harvested day from the `latest harvested day:` line of
`harvest.py --check --since <through + 1 day>` (ignore its exit code — between weekly harvests it is
non-zero by design). Due when newest ≥ `through` + 7 days: run
[`eng:build-anthropic-usage-report`](../../../eng/skills/build-anthropic-usage-report/SKILL.md)
and republish to that same `url`, unless its collect reports `coverage.complete:
false` (skip, say so). Never harvest here: `eng:harvest-anthropic-usage` owns it.

### Step 7 — Summarise

Report what ran and what was skipped: days stored (+ reconciliation), the revenue
dimension (rebuilt with its period/user/revenue totals, or already current), the
aiCreatedAt dimension (resolved / still-unresolved counts, or already current),
the env dimension (prod/dev/unknown tagged, and that the prod view was refreshed),
the daily trend headline, each weekly row with its `Signals/Aurie Weekly Cost`
snapshot URL (and, for any week recorded without one, which week and why), the
harvest outcome (rows added/updated + watermark), the report link (if
built), and the Claude seat usage report (republished through which day, or skipped and why). When the weekly step wasn't due, give the date it is recorded
through rather than a bare "not due". Under the
daily-only / foundation-only scope, report just the four foundation outcomes
and explicitly note that reporting steps were skipped by request. If nothing
was due, say that plainly.

## Idempotency

Safe to run repeatedly — the design goal is "run it whenever, it does only what's
outstanding":

- The daily store is idempotent per day (stored dates are skipped).
- The revenue dimension is a full rebuild — re-running it is always safe and
  always converges on the same table for a given source state.
- The aiCreatedAt dimension is append-only and incremental — it only fetches
  UIDs not already present, so a re-run is always safe and never duplicates a
  row.
- The env dimension is append-only and incremental on the same terms, and its
  view refresh is a `CREATE OR REPLACE` — re-running it with nothing missing
  (`--view-only`) just republishes the view.
- The daily/weekly reports and the composite report are read-only / regenerated.
- The harvest is plan-then-apply; a re-run on already-applied changes is a no-op.
- A run with nothing due makes **no** writes and says so.

## Privacy

This cadence is **aggregate-only** at the surfacing layer. The composed skills own
their own PII contracts — `eng:store-aurie-user-daily-cost` writes full Firebase
UIDs to the access-restricted `aura-main.aurie_cost` dataset (that's the store, not
a surface), while the daily/weekly reports and the composite report are aggregate
and never emit a UID. **Do not** fold [`eng:report-aurie-margin-users`](../../../eng/skills/report-aurie-margin-users/SKILL.md)
(UID-bearing, internal-only) into this cadence — it's deliberately excluded; run it
separately if ops explicitly asks. Any external surface still follows
`Compliance/Privacy` (UID prefix only, never full email / given / family name).

## Edge cases

- **BigQuery unreachable** (`GCP_KEY` unset / `google-cloud-bigquery` missing) —
  the store and the "what's due" probe hard-fail (BigQuery is the record). Report
  the miss; don't fabricate a coverage date.
- **Partial latest day** — never store "today"; yesterday (UTC) is the newest
  complete day.
- **Store far behind** (e.g. many days) — catching up is one
  `eng:store-aurie-user-daily-cost` run per day (~4 min each); tell the user the
  span and that it will take a while rather than silently looping.
- **User scoped the request** ("just the daily store") — run only that member and
  skip the rest; don't run the whole cadence.
- **Daily-only / foundation-only scope requested but nothing is missing** —
  say so and stop; don't run the reporting steps just because they'd otherwise
  be due.
- **aiCreatedAt looked covered in Step 1 but Step 2 just stored new days** —
  re-check with a fresh dry-run at the start of Step 2c (see above) rather than
  trusting the pre-catch-up number; newly-stored UIDs need their own check.
- **env dimension reports 0 missing** — nothing to do; the prod view is logical
  and already reflects the table, so a forced refresh buys nothing. Re-run
  `--view-only` only after `eng:backfill-aurie-user-env` itself changed (the
  view definition lives in its code), not as part of a daily tick.

## References

The members carry their own docs; this skill only sequences them. Start from the
[Run Cadence runbook](https://app.notion.com/p/aurahealth/Knowledge-Conversation-Data-Run-Cadence-39d75e23af208187a5fef673ed15cdfe)
(the Aurie cost group) for the canonical cadence definition.
