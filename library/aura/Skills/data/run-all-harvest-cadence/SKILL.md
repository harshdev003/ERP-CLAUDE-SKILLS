---
name: run-all-harvest-cadence
description: >
  One-invocation meta-orchestrator that runs the entire "Knowledge & Conversation Data — Run
  Cadence" runbook end-to-end by composing the five `run-*-harvest-cadence` group skills in
  order: conversations, cancellations, experiments, Aurie cost, and knowledge hygiene. A thin
  composer — it invokes each via the Skill tool in sequence and prints one summary, never
  reimplementing their logic. Each group is idempotent, so the sweep is safe to re-run.
  Trigger on "run all harvest cadences", "run every harvest cadence", "run the whole harvest
  cadence", "run the entire knowledge & conversation data cadence", "run the full harvest run
  cadence", "harvest everything", or "run the weekly data cadence end to end". ALSO trigger on
  a period-qualified sweep — "run the daily harvest cadence", "run only the daily ones", "run
  the weekly harvest sweep" — because resolving which groups a period covers is this skill's
  job. Do NOT use for a single group or the analysis cadence.

metadata:
  version: "3.1.0"
knowledge:
  reads:
    - Guide/Index   # baseline routing aid (universal). Every group skill owns its
                    # own knowledge manifest; this meta-orchestrator delegates all
                    # Notion / Slack / BigQuery / OpenAI / GitHub I/O to them.
---

# Run All Harvest Cadence

The whole **Knowledge & Conversation Data — Run Cadence** in one invocation.
This skill is a meta-orchestrator: it runs the five group cadences in order,
then prints a single consolidated readout. All real work — Notion writes,
BigQuery cost stores, teamspace harvest + healthcheck, dedup, idempotency —
lives in the composed group skills, each of which is itself a composer of its
own atomic skills.

It sits one level **above** the `run-*-harvest-cadence` family: those each run
one group; this runs all of them. Mirrors the same composition pattern (e.g.
`data:run-conversations-harvest-cadence` composing four Group 1 skills), applied
one layer up.

## What it runs

| # | Group | Skill | Deliverable (default) |
|---|---|---|---|
| 1 | Conversations (Group 1) | `data:run-conversations-harvest-cadence` | `Signals/*` conversation-pattern / topics / Aurie-conversations snapshots |
| 2 | Cancellations (Group 2) | `data:run-cancellations-harvest-cadence` | `Signals/Cancellations` rows + enrichment + `Learnings` top-10 refresh |
| 3 | Experiments (Group 4) | `data:run-experiments-harvest-cadence` | `Learnings/Experiments` sync + analysis of newly-ended experiments |
| 4 | Aurie cost (Group 5) | `data:run-cost-harvest-cadence` | BigQuery `aura-main.aurie_cost` store caught up + due reports/ledger |
| 5 | Knowledge hygiene (Group 5) | `data:run-knowledge-harvest-cadence` | teamspace harvest + healthcheck (Guide/Index regenerated) |

**Group numbers are per that runbook and are not globally unique** — the sibling
analysis cadence has its own overlapping group numbers. Match on the domain
(conversations / cancellations / experiments / cost / knowledge), not the index.

## Cadence per group — what "run the daily stuff" means

The five groups do **not** share a period. A request that names one ("run only
the daily ones", "just the daily stuff", "run the weekly sweep") selects a
**subset of groups**, and for the cost group it also narrows what runs inside
it. Resolve it from this table rather than re-deriving it from five SKILL.md
files:

| Group | Natural period | In a **daily-only** request? |
|---|---|---|
| Conversations | weekly (7d window; monthly on request) | no |
| Cancellations | weekly, or ~every 20 new rows | no — it has **no** daily item at all |
| Experiments | **daily / on-demand** | **yes** — run it bare |
| Aurie cost | mixed: daily foundation + daily trend, weekly row, per-cycle harvest | **yes, partially** — see below |
| Knowledge hygiene | weekly harvest; weekly-or-biweekly healthcheck | no |

So **daily-only = Experiments, then Aurie cost.** Same relative order as the
full table; knowledge-last is moot because knowledge isn't in the set.

**Within the cost group, "daily" is its daily-cadence steps, not its
daily-only scope flag.** Those are two different things and the names collide:

- Run **all four of its daily foundation writes** — cost store catch-up, revenue
  dimension, aiCreatedAt dimension, then the **env dimension + prod-view
  refresh** last — **and** its **daily trend report**; the trend is a
  daily-cadence step, and read-only. The env step is the easiest of the four to
  drop and the most expensive to: it is the only foundation step whose output
  the others *read*, and a user left untagged is silently excluded from every
  `chart-aurie-*` prod aggregate, which renders as a clean, plausible,
  understated number rather than an error.
- Skip the weekly row + snapshot, the per-cycle ledger harvest, and the
  composite report. They aren't daily.
- Do **not** pass that group's `daily-only / foundation-only` scope unless the
  user asked for it in those terms — that scope also drops the daily trend
  report, which a plain "run the daily stuff" did not ask to drop.

**Say what you skipped and why.** A daily-only sweep that reports only what it
ran is indistinguishable from a full sweep in which the weekly groups happened
to have nothing due. Name the skipped groups and their period in the Step 6
summary, and if a *non-daily* step looks overdue while you're there — a weekly
cost snapshot the cost group's Step 1 probe reports missing, say — surface it as
an offer rather than silently running it or silently dropping it.

**Other period words** map the same way: "weekly" is conversations +
cancellations + knowledge hygiene (plus the cost group's weekly row), "monthly"
is the conversations group's monthly window. When a request names a period no
group owns, ask rather than guessing which groups it covers.

## Ordering

The five groups are **largely independent** and each reads its own sources, so
strict ordering is not required for correctness. Run them in the table order
anyway, with **knowledge hygiene last** — it regenerates `Guide/Index` and audits
the teamspace, so running it after the data groups have written their snapshots
leaves the index reflecting this run. If a user asks for a subset, run only those
groups (still knowledge-last).

## Standing defaults

- **Composition only.** Always invoke each group skill via the Skill tool; never
  inline its scripts or duplicate its logic. Each group inherits its own defaults,
  windows, PII rules, and idempotency — this meta-skill adds none.
- **Notion / stores are the deliverable; no Slack unless asked.** Each group's own
  default already keeps Slack opt-in (the cost group posts nothing by default; the
  conversation group leaves Slack opt-in). Do not pass a "post to Slack" option to
  any group unless the user explicitly asks.
- **Run autonomously *between* groups — but do not override a group's own write
  gates.** Invoking this meta-skill means you don't re-confirm to move from one
  group to the next. It is **not** a blanket approval of every downstream write:
  each group keeps its own approval posture, and you honour it as the group
  defines it. Some groups pre-approve their own writes when run as a cadence (e.g.
  `data:run-conversations-harvest-cadence` pre-approves only its own write-once row
  plan); others deliberately gate a side-effectful write and expect a human
  (e.g. `data:run-cost-harvest-cadence`'s `harvest-cost-changes` step is
  plan-before-apply on the Aurie Cost ledger). Let those gates fire — surface the
  plan and pause where the group would pause; do not auto-confirm it just because
  this meta-skill was invoked. Only skip a confirmation where the group itself says
  it's pre-approved. Always pause on a hard blocker (a missing token it can't route
  around) or anything a group's plan flags as unexpected (a wildly off row/cost
  count).
- **Idempotent.** Every group is safe to re-run — dedup on write-once keys, day/
  week idempotency on the cost store, title-suffixed snapshots. Re-running the
  whole sweep is safe.

## Step 0 — Resolve the period

Two distinct questions, in this order:

1. **Which groups?** If the request names a period ("only the daily ones", "the
   weekly sweep"), resolve the subset from the *Cadence per group* table above
   and state it before running anything, so the caller can correct the reading
   cheaply. An unqualified invocation runs all five.
2. **Which window inside each group?** Otherwise let each group use its own
   default. If the user names a window ("run the monthly sweep"), pass each
   group the flag that expresses it **in its own SKILL.md's terms** — the groups
   do not share a single window flag, so confirm each against its own skill
   rather than reusing one token.

## Steps 1–5 — Run each group

For each row in the table, in order:

1. Invoke the group skill via the Skill tool (`Skill: <plugin>:<skill-name>`),
   passing only the window/options the user asked for (else its defaults).
2. Let it run its full internal sequence — it composes its own atomic skills.
3. Capture its headline counts / snapshot links / "nothing due" result for the
   final summary, and **append its outcome to the run's outcomes file** right away (Step 7):
   `ok`, `out-of-period`, `failed`, `blocked`, or `gate` (a write that waits for a person),
   with the reason and the single-group skill to re-run it.
4. On a hard failure in one group, **note it and continue** to the next group —
   do not abort the whole sweep (see Degradation below).

If a group skill isn't in the registry yet (freshly reinstalled), `/reload-plugins`
(or restart) to surface it, then invoke it normally. Do **not** substitute a
bundled script as a fallback — running a bare script skips the group's deliverable
and violates the composition rule.

## Step 6 — Consolidated summary

Print one readout with a line per group:

- **Conversations:** harvest counts + snapshot URLs (or "nothing new").
- **Cancellations:** new rows harvested / enriched + whether the top-10 refreshed.
- **Experiments:** synced catalog count + any newly-ended experiments analysed
  (or "nothing newly ended").
- **Aurie cost:** what was due and run (store caught up to which day, weekly row
  + snapshot, ledger) or "nothing due."
- **Knowledge:** harvest summary + healthcheck findings (carry-forward items).
- **Failures/skips:** any group that couldn't run, with the reason and the
  single-group skill to re-run it.
- **Out of period:** on a period-qualified run, the groups deliberately not run
  and their own period ("cancellations, conversations, knowledge — weekly"), so
  a partial sweep never reads as a full one. Add any non-daily step a group's
  own due-probe flagged while running, as an offer.

## Step 7 — Raise a follow-up for every stop (unattended runs)

A scheduled or looped run has nobody reading its summary, so every step that did not finish
becomes a `shared:raise-followup` post. Keep the outcomes file in the session scratchpad and
append one JSON object per line as each group finishes (Steps 1–5), e.g.
`{"step": "cost", "status": "gate", "reason": "ledger plan waits for a person", "rerun": "data:run-cost-harvest-cadence"}`.
Steps are `conversations`, `cancellations`, `experiments`, `cost`, `knowledge`. Then:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/run-all-harvest-cadence/scripts/outcomes.py \
  --cadence run-all-harvest-cadence --session "${AINATIVE_SESSION_NAME:-scheduled}" \
  --in "$SCRATCH/harvest-outcomes.jsonl" --due "$DUE"
```

`$DUE` is the comma-separated groups the period resolved in Step 0 runs, and only those: a
daily run passes `experiments,cost`, an unqualified run all five. A group out of period is
never due, so it raises nothing. A due group with no entry counts as `stopped`, and a missing
outcomes file means nothing was recorded, so a run that ends early still reports what it
never reached. The script prints
one object per follow-up (`kind`, `title`, `what`, `why`, `ask`, `key`, `session`): pass each
to `shared:raise-followup` as is. The key is `run-all-harvest-cadence:<group>:stop`, one post
per group, with a changed reason going in as a reply. Exit 0 means nothing to raise.

Run this step **before the turn ends, however it ends**: after Step 6, on a hard blocker, and
when a wait expires or the run is cut short. An interactive run with someone reading the
summary may skip it.

## Notes & edge cases

- **Composition, not reimplementation.** If a group cadence's behaviour changes,
  this meta-orchestrator inherits it for free. Same pattern as `data:profile-user`
  and the individual `run-*-harvest-cadence` skills.
- **All five members live in `data`, but two reach cross-plugin.** All five group
  cadences are `data:` skills, so this meta-skill's direct members are all in one
  plugin. Two of them still reach into other plugins internally —
  `data:run-cost-harvest-cadence` composes the `eng:` Aurie-cost skills, and
  `data:run-knowledge-harvest-cadence` composes `shared:harvest-knowledge` +
  `shared:healthcheck-knowledge`. Invoking skills across plugins via the Skill tool
  is fine; this meta-skill is self-contained per Architecture principle 1: it ships one
  script (`outcomes.py`, which only formats follow-ups) and delegates every side effect.
- **Partial degradation.** If one group can't run (missing token, upstream
  outage), complete the other four and record the gap in the summary rather than
  aborting. The whole point of the sweep is to leave every layer as current as this
  session can make it.
- **Framework Python.** If a composed group's bundled script errors on
  `import requests` or needs BigQuery, follow that group's own guidance — several
  need the framework interpreter
  (`/Library/Frameworks/Python.framework/Versions/Current/bin/python3`). This
  meta-skill runs no Python itself.
- **Scope.** Harvest run cadence only. The **analysis** run cadence (per-entry
  feedback triage + auto-posted Slack replies) is the sibling
  `data:run-all-analysis-cadence` — do not run it from here.
- **The period table is a routing aid, not a second source of truth.** Each
  group's own SKILL.md owns its cadence; this table only records which groups a
  period word selects, so a caller doesn't have to open five files to find out.
  When a group's period changes, update the row here in the same PR — and if the
  two ever disagree, the group's own SKILL.md wins.

## References

- Group 1: [`data:run-conversations-harvest-cadence`](../run-conversations-harvest-cadence/SKILL.md)
- Group 2: [`data:run-cancellations-harvest-cadence`](../run-cancellations-harvest-cadence/SKILL.md)
- Group 4: [`data:run-experiments-harvest-cadence`](../run-experiments-harvest-cadence/SKILL.md)
- Group 5 (cost): [`data:run-cost-harvest-cadence`](../run-cost-harvest-cadence/SKILL.md)
- Group 5 (knowledge): [`data:run-knowledge-harvest-cadence`](../run-knowledge-harvest-cadence/SKILL.md)
- Sibling meta-skill (analysis): `data:run-all-analysis-cadence`
- Runbook: AI Knowledge → `Procedures/AI Native/Knowledge & Conversation Data — Run Cadence`.
