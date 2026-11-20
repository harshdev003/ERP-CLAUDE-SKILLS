---
name: run-knowledge-harvest-cadence
description: >
  Runs the recurring "Group 5 — Knowledge hygiene" cadence: a manual, plan-first sweep that
  keeps the teamspace current. The standing-task ORCHESTRATOR — each run resolves the `since`
  window from the latest Guide/Harvest run page, DELEGATES the sync to
  `shared:harvest-knowledge`, then the audit to `shared:healthcheck-knowledge`, carrying the
  operational memory so a fresh session needs no pasted procedure. This is the FULL cadence —
  harvest AND healthcheck — not a bare harvest. Trigger on "run the knowledge harvest
  cadence", "run the knowledge cadence", "run the weekly knowledge sweep", "do the knowledge
  hygiene run", "harvest and healthcheck the knowledge", "run the Group 5 knowledge cadence",
  "group 5 knowledge hygiene", "run knowledge harvest and healthcheck", or "start the
  knowledge cadence". "Group 5" is overloaded — the guardrails analysis cadence is also Group
  5 — so qualify it. Do NOT use for a bare "harvest knowledge" (`shared:harvest-knowledge`) or
  a one-off audit (`shared:healthcheck-knowledge`).

metadata:
  version: "3.1.0"
knowledge:
  reads:
    - Guide/Index                                              # baseline routing aid (universal)
    - Procedures/AI Native/Knowledge & Conversation Data — Run Cadence  # the cadence spec this operationalizes (Group 5)
    - Tools/Notion                                             # AI Knowledge teamspace reads + writes (via the delegates)
    - Tools/GitHub                                             # harvest's sync-feedback-config adapter reads IntelligenceService via gh
---

# run-knowledge-harvest-cadence

The **orchestrator** for the recurring "Group 5 — Knowledge hygiene" cadence defined
in `Procedures/AI Native/Knowledge & Conversation Data — Run Cadence`. It keeps the AI
Knowledge teamspace (L4) current by running two skills in order, on a **manual weekly**
cadence:

1. **`shared:harvest-knowledge`** (weekly, first) — sweep upstream canonical sources
   into L4 and regenerate `Guide/Index`.
2. **`shared:healthcheck-knowledge`** (weekly or biweekly, after harvest) — audit L4
   for drift, broken links, missing `# Sources`, placement, and PII.

It owns three things and **nothing else**:

1. **Window resolution** — derive the `since` date for this run from the most recent
   `Guide/Harvest/<YYYY-MM-DD>-runN` page, so the sweep is incremental and idempotent
   across sessions without a local state file.
2. **Ordering + hand-off** — run harvest to completion (including its own plan →
   approve → apply gate), then run healthcheck, then reconcile healthcheck findings
   against what harvest just changed.
3. **A single named entrypoint** — so a fresh session resumes the cadence by invoking
   this skill instead of re-pasting a long procedure by hand.

Everything about *how a harvest works* (taxonomy discovery, upstream scan, classify,
plan, apply, run-page audit, `Guide/Index` regen) and *how a healthcheck works*
(the detector suite, run-page audit, remediation planning) is **not reimplemented
here**. It is delegated to [`shared:harvest-knowledge`](../../../shared/skills/harvest-knowledge/SKILL.md)
and [`shared:healthcheck-knowledge`](../../../shared/skills/healthcheck-knowledge/SKILL.md), the canonical
owners of those mechanisms. This skill is the harness around them, per best-practice
principle 1 (atomic skills, composed workflows).

## Charter — manual, plan-first (read this)

Unlike the Group 1–4 conversation-data cadences, **Group 5 is a manual cadence** — there
is **no cron, no watermark, and no unattended posting**. Per the Run Cadence procedure,
knowledge hygiene runs on demand (typical: weekly). This skill does **not** self-arm a
cron and does **not** authorize any write without approval:

- **Harvest keeps its plan-first gate.** `shared:harvest-knowledge` presents a write plan
  and waits for the user to approve before any L4 write. This orchestrator does not
  pre-approve on the user's behalf or pass a blanket `mode=apply`.
- **Healthcheck keeps its plan-first gate.** Remediation is proposed, not auto-applied.

The orchestrator sequences the two and carries context between them; the approval gates
inside each delegate are preserved exactly.

## Inputs

All optional — the defaults are the cadence's standing configuration:

| Input | Default | Meaning |
|---|---|---|
| `since` | resolved from the latest `Guide/Harvest` run page | Start of the harvest window (`YYYY-MM-DD`). Explicit value overrides the auto-resolved one. |
| `run_healthcheck` | `true` | Whether to run `shared:healthcheck-knowledge` after harvest. Set `false` for a harvest-only run (healthcheck is weekly-or-biweekly, so some weeks skip it). |
| `sources` | `notion` | Passed through to harvest's `sources` input. |

## Workflow

### 1. Resolve the `since` window

Resolve `since` in this strict priority order — each rule falls through to the next only
when it can't produce a date, so window resolution never dead-ends:

1. **Explicit input.** If the user passed a `since`, use it, note the override, and skip
   the rest.
2. **Suggested-since line on the latest run page.** Discover the `Harvest` parent (Guide
   child), list its `YYYY-MM-DD-runN` children, pick the most recent (latest date, then
   highest `N`), and read it. Harvest run pages in practice close with a
   `# Notes for next run` section whose last line is `Suggested since for next sweep:
   <date>` (Step 8 of `shared:harvest-knowledge` emits it). If that line is present, use
   its date.
3. **Fallback — the latest run page's own date.** The harvest run-page *template* does not
   formally require the suggested-since line, and older pages predate the convention — so
   if it's absent, parse the `YYYY-MM-DD` from the most recent run page's **title** and use
   that as `since`. The last sweep ran on that date, so it's a safe incremental floor (at
   worst it re-scans one already-covered day, which harvest is idempotent against).
4. **Bootstrap.** If no run page exists at all (first ever run), fall back to harvest's own
   default (7 days ago) and note it.

Report the resolved window and which rule produced it before proceeding:
`Window: <since> → today (via rule <n>, from <run-page>)`.

### 2. Run the harvest (delegate)

Invoke [`shared:harvest-knowledge`](../../../shared/skills/harvest-knowledge/SKILL.md) with the resolved
`since` (and `sources` if non-default). Let it run its full flow:

- It scans upstream, runs the `eng:sync-feedback-config` adapter, classifies, and
  **presents its write plan and waits for approval** — surface that plan to the user
  and let them approve / redirect exactly as they would in a direct harvest run.
- On approval it applies the writes, creates the `Guide/Harvest/<date>-runN` audit page,
  appends the run-log row, and regenerates `Guide/Index` if the tree changed.

Carry the **operational memory** below into the harvest run so recurring context isn't
lost:

- **Open carry-forward items** (re-propose or re-defer, never drop silently): Aurie
  Monetization Plan v4 (temporary); Tolan / Alinea / Spring Health Growth Readings (no
  primary sources yet); Alinea naming collision (needs a human decision). These are read
  fresh from the prior run page's carry-forward section each run — this list is the
  known state as of the last run, not a substitute for reading the page.
- **Dedup-not-1:1 rule:** the upstream Growth Readings DB sometimes files several
  clip-level readings of the *same* underlying source (e.g. one webinar cut into 3
  videos). Do **not** mirror 1:1 by default — flag near-duplicates and ask before
  creating more than one L4 row per underlying event.
- **Teamspace-scan caveat:** a full workspace REST enumeration gets swamped by this
  teamspace's own automated writes (mostly `Signals/Conversation Patterns` row churn)
  before it reaches a useful cutoff, so the Engineering/Prompts/Product/QA canonical
  teamspaces are often under-swept. If broad upstream coverage beyond Growth Readings is
  wanted this run, say so explicitly and use MCP `notion-search` with `teamspace_id`
  filters per teamspace rather than the raw REST `search()` enumeration.

If `run_healthcheck` is `false`, skip to Step 4 after harvest completes.

### 3. Run the healthcheck (delegate)

Invoke [`shared:healthcheck-knowledge`](../../../shared/skills/healthcheck-knowledge/SKILL.md). Reconcile its
findings against what harvest just changed in Step 2 — a `Guide/Index` entry harvest just
added is not "drift", and a page harvest just created with a fresh `# Sources` is not a
missing-source finding. Let healthcheck present its remediation plan and wait for approval
per its own gate; this orchestrator does not auto-apply.

`Learnings/Conversations.User ID` holds full Firebase UIDs rather than the `uid_prefix`
every `Signals/*` table uses. This is an **accepted exception** to the `Compliance/Privacy`
Agent rule, recorded on that page under "Accepted exceptions to the Agent rule" — read the
rationale and its scope there rather than restating them here. It is not a finding: if
healthcheck surfaces it, close it against that exception.

### 4. Report

Summarize the run:

- The resolved window and the run page it came from.
- Harvest outcome: new pages / updates / deferred / rejected, and the new
  `Guide/Harvest/<date>-runN` page URL.
- Healthcheck outcome (if run): findings by severity and the remediation plan status;
  or `skipped (run_healthcheck=false)`.
- The suggested `since` for the next run (today's date) — this becomes next run's window.

### 5. Raise a follow-up for every stop (unattended runs)

A scheduled run has nobody reading its report, so a leg that did not finish becomes a
`shared:raise-followup` post. Append one JSON object per line to a scratchpad file as each
leg ends (`{"step": "harvest", "status": "ok"}`; statuses `ok`, `failed`, `blocked`, `gate`),
then:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/run-knowledge-harvest-cadence/scripts/outcomes.py \
  --cadence run-knowledge-harvest-cadence --session "${AINATIVE_SESSION_NAME:-scheduled}" \
  --in "$SCRATCH/knowledge-outcomes.jsonl" --due "$DUE"
```

`$DUE` is `harvest,healthcheck`, or `harvest` alone when `run_healthcheck=false`. A due leg
with no entry counts as `stopped`, and a missing outcomes file means nothing was recorded. Pass each printed object to
`shared:raise-followup` as is (key `run-knowledge-harvest-cadence:<leg>:stop`). Exit 0 means
nothing to raise. Run it before the turn ends, however it ends: after the report, on a missing
token or skill, and when a wait expires. An interactive run with someone reading the report
may skip it.

## Cadence (read this — it explains the manual model)

- **Triggered from outside.** The skill never schedules itself; a person or a scheduled
  task outside it starts each run (weekly). A scheduled run raises its stops (step 5).
- **The run pages are what persist.** Continuity across sessions comes from the
  `Guide/Harvest` run pages (durable, in Notion), not a local file. A fresh session reads
  the latest run page to resolve `since` and carry forward decisions.
- **Frequency:** harvest weekly (first); healthcheck weekly or biweekly (after harvest).
  Some weeks legitimately run harvest only (`run_healthcheck=false`).

## Starting a fresh session

To resume the cadence in a new session, invoke this skill by name (e.g. "run the knowledge
harvest cadence" or "run the Group 5 knowledge cadence"). Two disambiguations: a bare
"harvest knowledge" goes to `shared:harvest-knowledge`, not here; and a bare "run Group 5"
is ambiguous — the guardrails analysis cadence is also "Group 5" — so qualify it with
"knowledge". It will: resolve `since` from the latest `Guide/Harvest` run page, run harvest
(plan → your approval → apply), then run healthcheck (plan → your approval), reconcile the
two, and report — with the carry-forward items, dedup rule, and teamspace-scan caveat
already baked in. No long hand-pasted procedure is needed; the standing configuration lives
in this skill.

## Edge cases

- **No prior run page** → fall back to harvest's 7-days-ago default and note it; this is
  the first-run bootstrap.
- **Harvest plan rejected / redirected** → follow the user's redirect inside the harvest
  delegate; only proceed to healthcheck once harvest resolves.
- **`run_healthcheck=false`** → run harvest only; still report the suggested next `since`.
- **Healthcheck flags something harvest just wrote** → reconcile before flagging; a
  just-applied harvest change is expected, not drift.
- **Broad upstream coverage requested** → use the teamspace-scoped MCP scan (see the
  teamspace-scan caveat), not the default REST enumeration.
- **A conversation-data cadence** (patterns, topics, cancellations, ratings, PMF) → out
  of scope; route to the relevant `data:run-*` cadence skill.

## References

- [`shared:harvest-knowledge`](../../../shared/skills/harvest-knowledge/SKILL.md) — the upstream→L4 sync
  delegate; owns the plan/apply flow, run-page audit, and `Guide/Index` regen.
- [`shared:healthcheck-knowledge`](../../../shared/skills/healthcheck-knowledge/SKILL.md) — the L4 audit
  delegate; owns the detector suite and remediation planning.
- `Procedures/AI Native/Knowledge & Conversation Data — Run Cadence` — the canonical
  cadence spec (Group 5 = knowledge hygiene) this skill operationalizes.
- `/CLAUDE.md` → "Skill versioning", "Knowledge manifest".
