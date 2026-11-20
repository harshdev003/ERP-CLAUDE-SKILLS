---
name: run-experiments-harvest-cadence
description: >
  The daily driver for the "Group 4 — Experiments" run cadence — a thin orchestrator that runs
  `data:sync-experiments`, then `data:analyze-experiment` on any experiments newly end-dated
  by that sync, then refreshes the Top 10 Learnings section via
  `data:update-experiments-top10` whenever a row was enriched. If nothing newly ended the
  cadence is done: it does NOT chase the historical backlog of previously-ended-but-unanalyzed
  rows unless asked. Defaults to AI Knowledge only, no Slack. Trigger on "run experiments
  harvest cadence", "harvest experiments", "run group 4 harvest", "run the experiment
  cadence", "daily experiment harvest", "run the experiments daily task", or "experiments
  daily". Do NOT use to import the catalog alone (`data:sync-experiments`), analyze one named
  experiment (`data:analyze-experiment`), or re-synthesise the top-10 alone.

metadata:
  version: "3.1.0"
knowledge:
  reads:
    - Guide/Index
    - Learnings/Experiments
---

# Run Experiments Harvest Cadence

## Goal

Run the recurring **Group 4 — Experiments** cadence in one shot: sync the Aura
experiment catalog into `Learnings/Experiments`, analyze whatever ended since
the last run, and re-synthesise the page's `Top 10 Learnings — Experiments`
section when that analysis changed the corpus. This is the Group 4 owner's
daily/on-demand driver.

The cadence definition this skill automates lives on the AI Knowledge
**"Knowledge & Conversation Data — Run Cadence"** page (page ID
`39d75e23af208187a5fef673ed15cdfe`), under the **"Group 4 — Experiments"**
section: `data:sync-experiments` runs weekly / on-demand, and
`data:analyze-experiment` runs on-demand after experiments end. This skill
chains them — plus the top-10 refresh that keeps the page's synthesis level
with the rows they write — so the owner runs one thing, not three.

## What this skill is (and is not)

This is a **thin orchestrator** (best-practices principle 1 — compose atomic
skills, don't re-implement them). It owns **no** Notion, Aura API, Slack, or
Mixpanel logic of its own — all of that lives in the composed skills:

- [`data:sync-experiments`](../sync-experiments/SKILL.md) — bulk-imports the
  `GET /experiments` catalog into `Learnings/Experiments`; creates stubs,
  backfills `End Date`, auto-closes zero-duration stubs, handles the
  re-registration guard. Idempotent.
- [`data:analyze-experiment`](../analyze-experiment/SKILL.md) — enriches a row
  with Mixpanel per-variant metrics, Slack/Linear context, a Won/Lost/
  Inconclusive/Turned-off verdict, and synthesised learnings.
- [`data:update-experiments-top10`](../update-experiments-top10/SKILL.md) —
  re-synthesises the `Top 10 Learnings — Experiments` section from the whole
  corpus. Runs only when a row was enriched this run (Step 4).

If you want to run only the catalog import, call `data:sync-experiments`
directly. If you want to analyze one named experiment or a specific pasted
`Learnings/Experiments` row, call `data:analyze-experiment` directly. This
skill exists for the *cadence* — the "did anything end since last time, and if
so analyze it" loop the Group 4 owner runs regularly.

## Inputs

None required. Trigger it bare as the daily/on-demand cadence check.

Optional, only if the user says so:

- **Route to Slack** — the user explicitly wants the analysis posted to
  `#ai-product` / `#support-bot` / `#data-experiment` this run (see Step 3,
  default is AI-Knowledge-only).
- **Skip deep analysis** — the user explicitly wants a light/boilerplate
  close-out instead of the full Mixpanel pipeline for a legacy batch (see
  Step 3, default is full pipeline).

## Workflow

### Step 1 — Sync the catalog

Invoke [`data:sync-experiments`](../sync-experiments/SKILL.md) with no flags
(full catalog sync). Let it run its own dedup / classify / apply / report
flow end to end.

When it finishes, capture its report — the counts, the **per-row plan** (which
rows were CREATE, CREATE-CLOSED, UPDATE, SKIP, and for each the resulting
`Status` / `End Date`), **and the failure list** (`sync-experiments` prints the
plan *before* it writes and reports `CREATE failures` / `UPDATE failures`
separately at the end). Step 2 keys off both the UPDATE and CREATE buckets —
specifically any row the sync **successfully wrote** a fresh `End Date` for
whose `Status` the sync did **not** set to `Turned off` (i.e. ended normally,
not stub-closed). The plan is the *intended* classification; only rows the sync
confirms it applied are eligible for Step 2.

Report the headline counts back to the user, e.g.
`sync-experiments: 4 created, 1 auto-closed stub, 10 updated, 2,944 in sync`.

**Capture its reconcile block too (the sync's Step 4c), and carry it to Step 5
even when it is zero.** `endedAt` is not the only way an experiment finishes —
a winner shipped to everyone keeps a null `endedAt` and collapses `active` to
one variant, so the rows this cadence's Step 2 selects are only the ones the
server bothered to stamp. On 2026-09-17 that gap was 133 rows reading `Running`
that had stopped months earlier, and **no frequency of running this cadence
would ever have surfaced one**: they are not late, they are invisible to the
signal Step 2 keys on. That is what makes an unreported zero dangerous here —
"nothing newly ended" and "173 rows disagree with the catalogue" printed the
same way for months ([PROD-6116](https://linear.app/aura-health/issue/PROD-6116)).

### Step 2 — Identify what ended *this run*

From the sync's own output, pick out the experiments that were **newly
end-dated this run** — any row the sync gave a fresh `End Date` to this run and
did **not** stub-close, whether it arrived via the **UPDATE** bucket (an
existing launched row that ended since the last sync) **or** the **CREATE**
bucket (an experiment that was already ended the first time the sync imported
it). Both cases are enumerated below — do not read the UPDATE case as the whole
rule.

**Include — any row this run got a fresh `End Date` for that the sync did *not*
stub-close.** Two cases:

1. **End-date-only UPDATE** — an existing launched row where the sync backfilled
   `End Date` from the API's `endedAt`. **The sync deliberately leaves `Status`
   as-is on this path — a launched experiment stays `Status: Running`** (the
   end-date-only update patches only `End Date`, never `Status`). This is the
   normal "experiment ended since last sync" case and it is the **most common**
   row the cadence forwards — so do **not** filter on `Status` being blank here;
   these rows are `Running`.
2. **Ended-on-first-sight CREATE (non-stub)** — a brand-new row the sync created
   this run with `End Date` already set (the API's `endedAt` was non-null at
   first import) and **not** classified as a CREATE-CLOSED stub. Per the sync's
   field mapping such a CREATE leaves `Status` blank. An experiment that had
   already ended before this cadence ever saw it. Without this case it would
   never be analyzed and isn't picked up by the documented backlog sweep either.

The unifying rule: **forward every row the sync gave a fresh `End Date` this run
(via UPDATE or CREATE) whose `Status` the sync did NOT set to `Turned off`.**
The `Turned off` write is the only marker of a stub close (Step-2 Exclude); a
normally-ended row keeps `Running` (UPDATE) or blank (CREATE) and must be
forwarded. Match on "not `Turned off`", never on "`Status` is blank" — a
launched experiment that ends stays `Running`, so a blank-only test would drop
it.

**Applied-writes only.** The sync's per-row plan is printed *before* writes;
its `CREATE failures` / `UPDATE failures` list (captured in Step 1) tells you
which planned writes didn't land. **Drop any row that appears in that failure
list** before building the Step 2 set — a failed CREATE/UPDATE means the row's
`End Date` was never actually written to Notion, so forwarding it would run
analysis on an experiment that isn't end-dated there. Re-running the cadence is
the recovery path for failed sync writes (the sync retries them next run); only
then does the row become eligible.

**Exclude:**
- SKIP rows (already in sync — nothing changed this run).
- Any row in the sync's `CREATE failures` / `UPDATE failures` list (write
  didn't land — see "Applied-writes only" above).
- CREATE rows that are still `Running` (`endedAt == null` — not ended).
- CREATE-CLOSED **and stub-retro-close** rows — the stub cases the sync handles
  itself, identified by the sync setting **`Status: Turned off`** on the row.
  Critically, **stub-retro-close lands in the same UPDATE bucket and also
  backfills `End Date`**, so End Date alone can't distinguish it — but the sync
  additionally writes `Status: Turned off` and the stub `## Learning` template.
  **That `Status: Turned off` write is the exclusion signal:** any row the sync
  marked `Turned off` this run is a stub close — drop it (never launched;
  nothing for `analyze-experiment` to measure). CREATE-CLOSED is the same (the
  sync creates it directly as `Turned off`). A normally-ended experiment is
  never written as `Turned off` by the sync — it stays `Running` (UPDATE) or
  blank (CREATE) — so this exclusion catches only true stubs. (If the user
  explicitly wants a stub row revisited, they can name it — but the cadence
  doesn't sweep them in.)

**If the set is empty → the cadence is done for this run.** Say so plainly
("Harvest complete — sync in sync, nothing newly ended to analyze"), **report
the reconcile counts from Step 1 alongside it** — a no-op run is exactly the
run where the disagreement count is the only thing said — and **stop**. Do **not** go looking through the historical backlog of
previously-ended-but-unanalyzed rows (rows that ended on some earlier run and
never got enriched). Chasing that backlog is a separate, explicit ask — the
daily cadence is only responsible for what changed *this* run. (Validated
2026-07-21: the right daily behaviour is "if no daily, nothing to do.")

### Step 3 — Analyze what ended

If Step 2 produced a non-empty list, invoke
[`data:analyze-experiment`](../analyze-experiment/SKILL.md) on **exactly that
list** of experiment names (create/enrich mode — the rows already exist, so it
enriches them in place via `record-knowledge` `action: update`).

Two cadence-specific defaults override `analyze-experiment`'s own prompts when
this skill is the caller:

**3a. AI-Knowledge-only output (no Slack by default).** `analyze-experiment`'s
Step 8 normally asks whether to post the readout to `#ai-product` or thread it
under the `#support-bot` end-note. When invoked from this cadence skill,
**skip that prompt and default to "keep in AI Knowledge only"** — the
enrichment writes to `Learnings/Experiments` and nothing posts to Slack. Only
route to Slack if the *user's* invocation of this skill asked for it (e.g.
"harvest experiments and post the results to #ai-product"). This matches how
the Group 4 owner runs the cadence day to day. *(Validated 2026-07-21: repeated
"just update AI Knowledge" instruction.)*

**3b. Full Mixpanel pipeline by default, even for legacy/batch-closed
experiments.** `analyze-experiment` can pre-classify a batch as low-value and
fall back to a light/boilerplate close-out (e.g. long-running legacy
experiments that were closed in a single Slack `end experiment` command batch
rather than with a fresh readout, or over-stamping-prone old code). When this
cadence skill is the caller, **default to running the full analysis pipeline
(its Steps 2–6: Slack context, Linear context, Mixpanel per-variant, verdict)**
rather than the shortcut — only skip to a light close-out if the user
explicitly says so. *(Validated 2026-07-21: a 9-experiment batch of years-old
Aura A/B tests, closed together via one engineer's Slack-command sweep, looked
skippable — but the full pass surfaced a genuinely useful finding: the Linear
ticket [ENGMOB-2677](https://linear.app/aura-health/issue/ENGMOB-2677) covering
those experiments was still Blocked/unresolved even though the experiments were
already closed in the catalog. A boilerplate pass would have missed it.)*

**3c. Large batches (>10 newly-ended) — don't let sweep mode downgrade them.**
`analyze-experiment` auto-selects **sweep mode** when more than 10 names are
passed, and sweep mode's Step 1f pre-classifies each row into
`NEVER_LAUNCHED` / `TOO_YOUNG` / `RUNNING_WITH_DATA` — only the last bucket
gets the full pipeline; the first two get boilerplate close-outs. Every row
this cadence forwards is a **newly end-dated** row (`endedAt != null`), which by
`analyze-experiment`'s own classification table lands in `RUNNING_WITH_DATA`
and therefore keeps the full pipeline even under sweep mode. Still, be explicit
when invoking: instruct `analyze-experiment` that **all forwarded rows are
confirmed-ended and must receive the full analysis (`RUNNING_WITH_DATA`
treatment)**, so a cheap-signal misfire can't silently downgrade a genuinely
ended experiment to a boilerplate close-out. This preserves 3b's "full pipeline
by default" guarantee regardless of batch size.

Everything else about the analysis — app detection (Aurie vs Aura), event
selection, over-stamping check, verdict rules, the `## Result` / `## Learning`
/ `## Follow-up experiments` body sections — is `analyze-experiment`'s own
behaviour; this skill doesn't restate or override it.

**3d. Track per-row completion; never drop a failure silently.** Keep the list
of experiments forwarded in Step 3 and, as `analyze-experiment` enriches each,
track which rows completed and which failed or were skipped. This matters
because Step 2 selects only rows **newly end-dated this run** — a row whose
`End Date` was backfilled by Step 1 but whose analysis then failed will **not**
be re-selected by a later bare re-run (its `End Date` is no longer "new"), so a
silent failure strands it in the very backlog this skill declines to sweep. If
any forwarded row does not complete, **list those exact experiment names in the
Step 5 summary and tell the user to recover them by re-invoking
`data:analyze-experiment` on those specific names** (a targeted retry — not a
full backlog sweep). Do not report the run as clean when some forwarded rows
were left unenriched.

### Step 4 — Refresh the Experiments top-10

Run this **only when Step 3 enriched at least one row.** On a nothing-newly-ended
run the corpus is unchanged, so a re-synthesis would rewrite the section with the
same evidence and a fresh date — which reads as "this was checked today" when
nothing was. That is the wrong direction for a page whose whole problem is a
byline you can't trust; skip it and say so in Step 5.

Invoke [`data:update-experiments-top10`](../update-experiments-top10/SKILL.md)
(no arguments — it re-synthesises from the whole `Learnings/Experiments` corpus,
not a window). It rewrites the `Top 10 Learnings — Experiments` section in place
on the `Learnings` page. Capture the row counts it synthesised from and the
bullet count.

That skill asks for approval before writing. **Answer yes on a cadence run** —
keeping the synthesis level with the corpus is this step's purpose, and this
cadence takes the offer as its own decision. Pause only if its diff looks wrong
(a collapse in bullet count, or a corpus count far off the table total).

**Why this step exists.** Every other step here writes rows; nothing kept the
*synthesis over* those rows current, so it drifted from the corpus it claims to
summarise with nothing reporting it — the section sat on a 2026-04-24 synthesis
while experiments kept ending and being enriched underneath it. Same shape as
`data:run-conversations-harvest-cadence`'s own top-10 step, and the same reason.

### Step 5 — Confirm and summarise

Report to the user:

- The sync headline counts (from Step 1).
- **The reconcile counts from Step 1, always** — how many rows still disagree
  with the live catalogue, and the count of rows now reading `Stopped splitting`.
  Never omit this because it is zero; a zero reported is the evidence, a zero
  withheld is indistinguishable from never having looked.
- Any rows the reconcile is **holding for a human**: a repeated-letter `active`
  (ambiguous — neither one arm nor two), and any row marked `Stopped splitting`
  that a person still needs to resolve into a verdict or `Feature flag`.
- The list of experiments analyzed this run, each with the verdict
  `analyze-experiment` assigned (Won / Lost / Inconclusive / Turned off), or
  "nothing newly ended" if Step 2 was empty.
- **Any forwarded rows that did NOT complete (Step 3d)** — call them out by name
  with the recovery instruction (re-run `data:analyze-experiment` on those
  names). Never fold a partial failure into an all-clear.
- Any cross-cutting finding worth surfacing (e.g. an unresolved Linear ticket,
  a data-quality flag) that came out of the analysis.
- **The Experiments top-10 (Step 4)** — rows synthesised from (and the table
  total), bullet count, and the byline date now on the page; or that it was
  skipped because nothing was enriched this run.
- That output went to AI Knowledge only (unless Slack routing was explicitly
  requested).

## Output

A short chat summary: sync counts + the per-experiment verdicts for anything
analyzed this run (or an explicit "nothing newly ended" when the cadence is a
no-op). No separate artifact — the durable output is the enriched
`Learnings/Experiments` rows written by the composed skills.

## Idempotency

Safe to re-run for the **success** path. Both composed skills are idempotent:

- `data:sync-experiments` on an unchanged catalog reports zero writes, so
  Step 2's "newly end-dated this run" set is empty and the cadence stops at
  Step 2 — a clean no-op.
- `data:analyze-experiment` uses `record-knowledge` `action: update`, so
  re-analyzing an already-enriched row replaces its sections in place rather
  than duplicating.

**Failure-path caveat — a failed analyze is NOT auto-recovered by a bare
re-run.** Step 2 keys off rows end-dated *this* run, so once Step 1 has
backfilled a row's `End Date`, a later bare re-run won't re-select it even if
its Step 3 analysis never completed. That's why Step 3d requires tracking
per-row completion and surfacing any unfinished rows for a **targeted** retry
(`data:analyze-experiment` on those exact names) — the recovery is explicit, not
an automatic property of re-running the cadence. Don't rely on "just run it
again" to fix a partial Step 3 failure.

**Test:** run the skill twice in a row with no upstream catalog change — the
second run should report "sync in sync, nothing newly ended" and write nothing.

## Edge cases

- **Nothing ended this run.** The common case for a daily run. Report it and
  stop — do not analyze the backlog (Step 2).
- **User explicitly wants the backlog swept.** Out of scope for the bare
  cadence. If the user says "analyze all previously-ended experiments that
  never got a readout", hand that to `data:analyze-experiment` in its DB-query
  / sweep mode (`End Date set AND Status in {blank, Running}` — a launched
  experiment that ended keeps `Status: Running` until analysis assigns a
  terminal verdict, so a `Status is blank`-only filter would miss most of the
  backlog) rather than this skill.
- **Sync fails partway.** `sync-experiments` leaves successful writes in place
  and surfaces failures; re-running is its recovery path. Don't proceed to
  Step 3 for rows the sync didn't successfully end-date.
- **Analyze fails partway.** If `analyze-experiment` errors or stops after
  Step 1 already backfilled `End Date`, those rows are now "ended" but
  unenriched, and a bare re-run won't re-select them (Step 2 only picks rows
  end-dated *this* run). Per Step 3d, list the unfinished experiment names in
  the summary and recover them with a targeted `data:analyze-experiment` run on
  those names — do not treat the cadence as complete.
- **User wants Slack routing this run.** Honour it — pass the Slack-routing
  intent through to `analyze-experiment`'s Step 8 instead of defaulting to
  AI-Knowledge-only (Step 3a).

## Related skills

- [`data:sync-experiments`](../sync-experiments/SKILL.md) — Step 1's catalog import (composed).
- [`data:analyze-experiment`](../analyze-experiment/SKILL.md) — Step 3's enrichment (composed).
- [`data:update-experiments-top10`](../update-experiments-top10/SKILL.md) — Step 4's top-10 refresh (composed).
- [`shared:record-knowledge`](../../../shared/skills/record-knowledge/SKILL.md) — the actual `Learnings/Experiments` writer, called transitively via `analyze-experiment`.

## References

- Run cadence: AI Knowledge → **"Knowledge & Conversation Data — Run Cadence"**
  (page ID `39d75e23af208187a5fef673ed15cdfe`), **"Group 4 — Experiments"** section.
