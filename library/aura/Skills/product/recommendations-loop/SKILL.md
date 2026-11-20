---
name: recommendations-loop
description: >
  The orchestrator for the Aura Recommendations loop — one runbook that sequences the legs and
  carries the schedule. A thin composer over the loop leg skills; it reimplements none of their
  logic and never crosses a human gate. Subcommands: sweep (telemetry to scored, shared ideas),
  harvest (Slack decisions to Notion), build (next approved idea, one at a time), ship (merged
  PR to a dormant release), watch (guardrails while an experiment ramps), status (funnel and
  queue), digest (review nudge and weekly roll-up), and run (one cadence tick). Use when the
  user says "run the recommendations loop", "run one recommendations loop tick", "sweep the
  recommender loop", "harvest the recommendation decisions", "show the recommendations loop
  status", or "post the recommendations digest". Do NOT use to decide at a gate (human only),
  to create or end an experiment (Yawar and Viktor), or to run a single leg you can invoke
  directly.

metadata:
  version: "1.1.0"
knowledge:
  reads:
    - Guide/Index      # baseline routing aid (universal)
    - Learnings/Loop/Aura Recommendations Ideas   # the database and process the loop turns on
    - Learnings/Experiments                 # verdicts the learn leg reads
    - Tools/Notion     # NOTION_TOKEN — every leg's Notion I/O
    - Tools/Slack      # channel IDs + tokens for share / harvest / digest
    - Tools/Linear     # LINEAR_API_KEY — the build leg's card
    - Tools/BigQuery   # GCP_KEY — the sweep and evaluate legs
---

# recommendations-loop

The orchestrator for the **Aura Recommendations** loop. Every piece of real work lives in the
leg skills this composes; this file is the order they run in and the rules that hold across
them.

`product:features-loop` explicitly declines to cover this loop: *"they do not ideate from a
sim-test walk or build a UI PR, so they carry different legs and get their own orchestrators,
not another `--app` value here."* The Notion schema, the channel-per-loop shape and the
decision protocol clone across all four loops; the legs do not.

Process, gates, statuses and the property guide are on the
[Aura Recommendations Ideas](https://app.notion.com/p/Aura-Recommendations-3e375e23af2081a1ae5cd5708a8a9684)
page — read that for the *why*. This is the *how to run it*.

```
observe → record → OFFLINE BAR → share → GATE 1 → build → release → GATE 2 → ramp → learn
```

## The structural parallel worth knowing

Features renders a **picture** between record and share so a Gate 1 thread opens with evidence.
This loop renders a **scorecard** in the same slot. `data:evaluate-recommender` is this loop's
`product:design-idea` — and unlike a picture, it can refuse.

## Targets

| | |
|---|---|
| Database | `3e375e23-af20-815b-80d5-000ba798e625` (Aura Recommendations Ideas) |
| Channel | `#ai-loop-aura-recommendations` `C0C0ZSMU7C7` |
| Build cards | **ENGBACK**, parented to `PROD-5931` |
| Repos | ContentService, 4U-engine |

Every leg that takes `--data-source-id` gets the value above; `product:share-ideas` resolves
the channel from it automatically.

## What this will never do

1. **Never decides at a gate.** Both gates are human replies; `harvest` only reads them.
2. **Never merges or releases without a human.** `build` stops at a BugBot-clean PR.
3. **Never creates, ramps, halts or ends an experiment.** `ship` asks; `watch` reports.
4. **Never runs two content experiments at once.** ~6,800 daily listening users will not carry
   two competing for the same primary metric.
5. **Never schedules itself.** A tick is one sweep; cadence is the operator's.

The one thing it *may* close without a human: an idea that **fails the offline bar**, recorded
`rejected` / `offline bar` with its numbers posted. That is the loop's whole speed advantage.

## Subcommands

### `status` — funnel and queue, from Notion alone

```bash
DS=3e375e23-af20-815b-80d5-000ba798e625
LEARN="${CLAUDE_PLUGIN_ROOT}/skills/learn-idea/scripts/learn_idea.py"
MANAGE="${CLAUDE_PLUGIN_ROOT}/skills/manage-ideas/scripts/manage_ideas.py"

python3 "$LEARN"  funnel --data-source-id "$DS"
python3 "$MANAGE" list --status shared         --data-source-id "$DS"   # waiting on Gate 1
python3 "$MANAGE" list --status approved       --data-source-id "$DS"   # ready to build
python3 "$MANAGE" list --status implementing   --data-source-id "$DS"   # building, OR released and waiting on Gate 2
python3 "$MANAGE" list --status shipped_dormant --data-source-id "$DS"  # Gate 2 declined
```

**`implementing` is the Gate 2 queue, not `shipped_dormant`.** `harvest-idea-decisions`
harvests only `pending`, `shared` and `implementing`, and maps the release gate to
`implementing` alone (`HARVESTABLE` / `STATUS_GATE`). A row moved to `shipped_dormant` leaves
the harvester's view, so its Gate 2 reply is never read — silently. `shipped_dormant` is where
a **declined** Gate 2 lands.

To tell the two `implementing` cohorts apart, read the release anchor: a row whose thread
carries one is awaiting Gate 2, one without it is still building.

### `sweep` — replaces `ideate`

One pipeline layer per run, least-recently-swept first.

**`sweep` runs on a schedule with nobody watching, so every write in it needs its confirm gate
lifted.** `record-ideas` and `share-ideas` both no-op without `--confirm` — they print a
preview and exit 0, so a scheduled sweep would look like it ran and record nothing. The same
applies to the `FAIL` close and its FYI below. Invoking `sweep` is standing approval for all of
them.

1. **Sweep** — invoke `data:sweep-recommender sweep --layer {layer}`, passing the backlog so
   priors are checked before anything is written.
2. **Record** — `product:record-ideas --from-json … --data-source-id "$DS" --confirm`.
3. **Score the rows that can be scored.** `data:evaluate-recommender score` needs a file of
   ranked lists, so it runs only on a row that carries a **candidate ranking**. A telemetry
   finding — a model returning nothing, coverage drifting, an instrumentation gap — has no
   ranking to replay and **skips the bar**, recording `Offline score = n/a`. Scoring is not a
   universal step; treating it as one would stall every telemetry idea before share.

   | Row carries | What happens |
   |---|---|
   | a candidate ranking | scored. `PASS` / `CONDITIONAL` go on; `FAIL` is closed — see below |
   | no ranking (telemetry, filters, instrumentation) | `Offline score = n/a`, straight to share, judged at Gate 1 on its argument |

4. **Close the failures BEFORE sharing.** `data:evaluate-recommender` returns a verdict and
   writes no Notion; nothing closes a `FAIL` on its own. Set it explicitly with
   **`product:manage-ideas`** — `rejected`, `Rejection reason = offline bar`, the numbers in
   the status note — and only then post the FYI. Ordering matters: `share-ideas` posts *every*
   pending unshared row, so a `FAIL` left `pending` opens a Gate 1 thread for an idea the bar
   already answered.

5. **Share** — `product:share-ideas … --confirm`, carrying the scorecard where there is one. A
   `CONDITIONAL` **must state the trade in the post** — that is the whole reason it is not a
   `PASS`. Post the `FAIL` FYI by composing **`shared:post-to-slack`** with
   `footer=product:recommendations-loop`, not through the MCP connector, so the loop owns its
   own record — and **tell it to post non-interactively, skipping its plan-and-confirm step**.
   `sweep` runs on a schedule with nobody watching; a `FAIL` that should close automatically
   would otherwise wait for a confirmation that never comes.

### `harvest` — Slack decisions → Notion

```bash
product:harvest-idea-decisions --data-source-id "$DS"    # one sweep, non-interactive
```

**`--data-source-id` is not optional here.** Every leg defaults to the Aurie Features Ideas database,
so an hourly `run` that omits it harvests the wrong loop: this loop's Gate 1 and Gate 2 replies
are never written, and Features threads get double-harvested by two orchestrators. Nothing
errors in either direction. Pass `$DS` to **every** leg that accepts it — `record-ideas`,
`share-ideas`, `manage-ideas`, `harvest-idea-decisions`, `learn-idea`.

Zero-write on a quiet channel is success.

Two outcomes are not verdicts and must not be reported as one:

- **`needs_judgment`** — the harvester could not read the thread confidently. This is what gets
  **handed back** for a human to make legible.
- **`needs_answer`** — the thread's last word is an unanswered question. That is the **loop's**
  job, not a person's: answer it in-thread with the evidence, and the next harvest classifies
  the thread normally.

### `build` — one approved idea at a time

Invoke `eng:linear-task` on the head of the `approved` queue **for its implement-to-PR path
only**, then `eng:bugbot-review`. Tell it to proceed **without its plan-and-confirm step** — it
otherwise waits on plan approval, and an unattended hourly tick stalls there whenever the
approved queue is non-empty.

`eng:linear-task` will otherwise drive a card through review to merge, which contradicts this
loop's stop-at-a-clean-PR contract and, under a scheduled `run`, would either stall on its
explicit-implement guard or carry on past the point a human is supposed to decide. Tell it
plainly: open the PR, get it BugBot-clean, stop. **Do not merge, do not release.**

Skip the tick if a build is in flight, or if the idea's surface already carries a running
experiment — report the skip rather than overriding it.

### `ship` — merged PR → dormant release

Invoke `product:ship-recommendation`. It checks the flag name first, cuts the release, posts
the Gate 2 anchor, and moves the row to `shipped_dormant`.

### `watch` — the ramp guardrails *(no Features equivalent)*

While an experiment is ramping, read the guardrails and **reply in the idea's thread** when one
regresses — by composing **`shared:post-to-slack`** with `footer=product:recommendations-loop`
and the skip-confirm instruction, like every other unattended write in this loop. A regression
reply that goes out through the MCP connector loses loop attribution, and `watch` runs hourly
with no human present.

Guardrails:

- **sub-3s skip rate** — the release guardrail, ~17% today
- **coach exposure by cohort** — listens drive payouts, so a concentrating ranker is a payroll
  change (ENGBACK-1396)

It reports. It does not halt, narrow or end anything. Features needs no such leg because a UI
experiment does not redistribute money between coaches.

### `digest` — the roll-up

`--daily` is the review nudge: rows `implementing` whose thread carries a release anchor
(awaiting Gate 2), `implementing` with an open clean PR (awaiting review), and `approved` not
yet started. `--weekly` is the retrospective.

Post to **`#ai-loop` (`C0C5TSVA8U9`)**, the status channel every loop shares — never to
`#ai-loop-aura-recommendations`, which carries one thread per candidate and nothing else.
Anything about one candidate is a reply in its own thread. Open the digest with the loop's
name (`Aura Recommendations · ready for review`). Compose **`shared:post-to-slack`** with
`footer=product:recommendations-loop`, no unfurl, no thread. **Tell the composed skill to post non-interactively — skip its
plan-and-confirm step.** Under a scheduled `run` there is no human to confirm, and a digest
that quietly waits for one is a tick that never finishes. Invoking `digest` is itself the
standing approval.

### `run` — one tick

`harvest` → `build` → `watch` → **digest only on a digest slot**.

`run` is scheduled hourly for `harvest` and `build`. **A plain hourly tick posts no digest** —
the daily slot posts `--daily`, the weekly slot posts `--weekly`, and every other tick skips it
and says so in the summary. The tick knows which slot it is **from the schedule that invoked
it**, not from a clock: this skill reads no wall clock. Posting a digest every hour into the
status channel is how a digest stops being read.

**`sweep` is not in `run`** — the harness leg is expensive, so it runs on its own slower
cadence.

## Cadence

| Leg | Cadence | Human gate? |
|---|---|---|
| `harvest` | hourly | no |
| `build` | when the approved queue is non-empty and nothing is in flight | **PR review is human** |
| `watch` | hourly while an experiment ramps | no — it reports |
| `sweep` | weekly, one layer | no |
| `digest --daily` / `--weekly` | daily / weekly | no |
| Gate 1 / Gate 2 | — | **human, in Slack, never scheduled** |

Rotation, one layer per sweep: `retrieval · ranking · blend weights · filters · candidate
supply · instrumentation`. These are the `Domain` values in the database, so the rotation reads
out of rows `status` already loads.

## Tick summary

End every `run` with: threads swept → decisions written by verdict → `needs_judgment` handed
back and `needs_answer` answered in-thread; the idea built or why none; whether a digest was
posted or the slot was skipped; guardrail state for any ramping experiment; queue depths, splitting
`implementing` into awaiting-review and awaiting-Gate-2; the single next action; and every skip with the one-leg command to
retry it. A leg that skipped for a transient reason leaves no marker and retries next tick — say
so rather than reading a quiet leg as a clean one.

## Readiness

The loop **cannot complete a cycle yet**. `buildUser` hardcodes `user.experiments = {}` and
`config.experiment.includeList` is empty, so no content experiment can be assigned to anybody
(`ENGBACK-1377`). Impression logging (`ENGBACK-1378`) and attribution coverage
(`ENGBACK-1379`) are the other two prerequisites. Until they land, `sweep`, `status` and
`digest` are useful and the rest has nothing to act on — say that rather than reporting a quiet
tick as a healthy one.

## References

- Process (source of truth): [Aura Recommendations Ideas](https://app.notion.com/p/Aura-Recommendations-3e375e23af2081a1ae5cd5708a8a9684)
- Baseline: [Aura Recommendations Teardown](https://claude.ai/artifact/41kkF6yVVorfYPXsjGc9KE)
- Legs: `data:sweep-recommender` · `data:evaluate-recommender` · `product:record-ideas` ·
  `product:share-ideas` · `product:harvest-idea-decisions` · `product:manage-ideas` ·
  `eng:linear-task` · `eng:bugbot-review` · `product:ship-recommendation` ·
  `product:learn-idea` · `eng:sweep-ended-experiments`
- Model composer: `product:features-loop`
