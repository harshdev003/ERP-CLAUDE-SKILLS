---
name: conversations-loop
description: >
  The orchestrator for the Aurie Conversations loop — one runbook that sequences the legs and
  carries the cadence. A thin composer over the leg skills; it reimplements none of their logic
  and never crosses a human gate. Subcommands: `digest` (rank defects → candidates), `share`
  (post for Gate 1), `harvest` (read verdicts), `draft` (Braintrust version), `evaluate` (run
  and record the gates), `ship` (route to users), `learn` (write the verdict back), `status`
  (funnel and queue), and `run` (one cadence tick). Use when the user says "run the
  conversations loop", "run one loop tick", "what's the state of the prompt loop", "sweep the
  conversations channel", or "run the weekly prompt digest". Do NOT use to decide at a gate
  (human only), to end an experiment (the experiment owner), or to run a single leg you can
  invoke directly.

metadata:
  version: "0.2.0"
knowledge:
  reads:
    - Guide/Index                          # baseline routing aid (universal)
    - Learnings/Loop/Aurie Conversations Ideas   # the process this skill executes
    - Learnings/Experiments                # verdicts the learn leg reads
    - Tools/Notion                         # NOTION_TOKEN — every leg's Notion I/O
    - Tools/Slack                          # channel ids + tokens for share/harvest
    - Tools/Braintrust                     # BRAINTRUST_API_KEY — the draft leg
---

# conversations-loop

The orchestrator for the **Aurie Conversations** loop. One place that sequences the legs and carries the schedule; every piece of real work lives in the leg skills this composes.

Modelled on `product:features-loop`: a thin composer that invokes each leg via the Skill tool (or its documented script) and **schedules nothing itself**.

## The loop it drives

The full process — the sources, both gates, the change classes, the status lifecycle, the register — is on **`Learnings/Loop/Aurie Conversations Ideas`**. Read that for the *why*; this file is the *how to run it*.

```
observe → diagnose → draft → GATE 1 → evaluate → GATE 2 → release → measure → learn
```

## What this skill will never do

Read this first — it is the contract, and most of it is enforced by the legs themselves.

1. **It never decides at a gate.** Gate 1 and Gate 2 are human replies in the candidate's thread. `harvest` only *reads* them.
2. **It never writes a prompt.** `draft` takes a body a human wrote. A prompt is a layered behavioural system, and final editorial authority over every word is human.
3. **It never merges or releases.** `ship` prints the release path and records the outcome; a human reviews, merges and cuts the release.
4. **It never ends an experiment.** `learn` reads a verdict someone else reached.
5. **It never diagnoses from volume.** `digest` ranks and emits `diagnosis: not yet diagnosed`; `share` then refuses to post until a human supplies one.
6. **It never schedules itself.** A tick is one sweep; cadence lives in the operator's scheduler pointing at `run`.

## Subcommands

Prefer invoking the leg **skill**, which carries its judgment and guards. Resolve script paths from `$CLAUDE_PLUGIN_ROOT`, never as a bare `plugins/…` path.

### `status` — where the backlog stands

```bash
P="${CLAUDE_PLUGIN_ROOT}/skills"
python3 "$P/learn-prompt-candidate/scripts/learn_prompt_candidate.py" funnel
python3 "$P/share-prompt-candidate/scripts/share_prompt_candidate.py" ready
python3 "$P/draft-prompt-candidate/scripts/draft_prompt_candidate.py" ready
python3 "$P/evaluate-prompt-candidate/scripts/evaluate_prompt_candidate.py" ready
python3 "$P/ship-prompt-candidate/scripts/ship_prompt_candidate.py" ready
```

Read them together into one short readout: the funnel, then what is actionable at each stage, then the single next action. Each `ready` prints not just what is waiting but what each blocked candidate is missing, so the readout is a task list rather than a census.

### `digest` — rank this week's defects

> Invoke `product:digest-conversation-signals` — `rank` for the readable digest, then `emit` piped into `product:record-prompt-candidates` `plan`.

Always `plan` before `apply`. The plan surfaces anything the loop has already answered, and `apply` refuses on those without `--force`.

Carry the hard stops into the readout verbatim. A theme can be the top-ranked defect *and* have a settled reading that says the obvious fix is wrong — `question_saturation` is exactly that, and reporting the rank without the reading is how the loop proposes the thing that was already falsified.

### `share` — put one candidate at Gate 1

> Invoke `product:share-prompt-candidate` — `ready`, then `share <ref>` to render the
> post, **read it**, then `share <ref> --confirm`.

The dry render is not optional here. `--confirm` posts a top-level Gate 1 thread, and a thread nobody read before it went out is the one thing this loop cannot take back: the candidate is now in front of reviewers with whatever the row happened to contain.

**One candidate per tick, and only while Gate 1's queue is short.** A channel with six new threads in a morning gets read by nobody, and Gate 1 is the gate whose quality matters most. When the queue is already backed up, share nothing and answer the open threads instead.

### `harvest` — read the verdicts

> Invoke `product:harvest-prompt-decisions` — `sweep`, then `sweep --confirm`.

Carry `needs_answer` and `needs_judgment` rows into the tick summary. They are not failures: a thread waiting on an answer is waiting on **this loop**, and answering it is the highest-value thing in the tick.

**Post those answers through `shared:post-to-slack` with `footer_skill: product:conversations-loop`**, as a threaded reply. Not through Slack MCP, and never unstamped. The harvester skips bot replies and reads the last *human* one — so an answer that goes out unattributed is indistinguishable from a reviewer's verdict, and the loop can end up harvesting its own words as a decision.

**This reply is a pre-authorized unattended post.** `shared:post-to-slack` has its own confirm gate and a scheduled `run` has nobody to confirm it, so tell the composed skill it is pre-authorized — otherwise a tick that finds a question either stalls at step 1 or skips the work this runbook calls the highest-value thing in the tick.

The line that authorization follows: **answering is not deciding.** A reply that supplies context a reviewer asked for does not cross a gate, and withholding it only makes the thread quieter. Posting a *new* top-level candidate does not get the same licence — `share` keeps its render-then-confirm rule, because that puts a fresh proposal in front of the team rather than answering one they already opened.

### What an answer may contain

Pre-authorized and unattended is exactly the combination that makes a wrong answer expensive: it lands in the thread reviewers use to decide, in the loop's own voice, with nobody checking it first. So the answer is **retrieved, never composed**.

Every claim comes from one of four places, and is cited so a reviewer can check it:

| Source | What it answers |
|---|---|
| The register on `Learnings/Loop/Aurie Conversations Ideas` | "has this been tried?", "why was that rejected?" |
| The candidate's own row | the defect, the evidence links, the diagnosis, the named risk |
| The gate results already attached | "what did the Safety Suite say?" |
| A named Braintrust experiment or signal row | anything about a measurement |

**When the answer is not in one of those, say so once and stop.** Post that the loop does not have it and what would answer it — or leave the thread and carry the question into the tick summary as `needs_judgment`. Both are better outcomes than a plausible sentence, because a reviewer reading the loop's reply reasonably treats it as retrieved fact, and a Gate 1 decision made on an invented number is worse than one delayed a day.

### Answer each question at most once

`harvest` skips bot replies and keeps returning `needs_answer` on the same human question until a *person* replies, and `shared:post-to-slack` does not dedup. So a tick that posts without checking will post the same reply again on every run — and the "we do not have this" case is the worst of them, because it never resolves itself.

**Before posting into a thread, fetch it with `shared:read-slack` in replies mode and check whether the loop has already replied after the last human message.** If it has, the question is already answered or already acknowledged: post nothing and carry it in the tick summary instead. This is the same idempotency rule every other leg follows by deduping on the row; the thread is this leg's row.

Fetch it fresh — **do not reuse the view `harvest` was working from.** That one has the bot replies already filtered out, because harvest reads the last *human* reply to decide a verdict. Checking for the loop's own prior reply in a list the loop's replies were removed from finds nothing, every time, which is precisely the bug this guard exists to prevent. The two legs need opposite views of the same thread: harvest wants humans only, this check wants everything.

Never restate a number from memory. If it is worth putting in the thread it is worth quoting from the row, the scorecard or the experiment it came from.

### `draft` → `evaluate` → `ship` → `learn`

> `product:draft-prompt-candidate` → `product:evaluate-prompt-candidate` → `product:ship-prompt-candidate` → `product:learn-prompt-candidate`.

`draft` needs a body a human wrote. `evaluate plan` prints what the class requires and the reporting contract; the runs themselves belong to `eng:qa-conversation`. `ship route` refuses an arm-routed candidate while the per-arm wiring is unbuilt.

### `run` — one cadence tick

A tick **never stops early**. Almost every step hands off to a human at some point — a body to write, a harness to run, a PR to merge — so stopping at the first one would mean the later steps never run at all, and Gate 1 would starve whenever the middle of the pipeline had work. Note what needs a human, and carry on.

1. `harvest` — verdicts first, because they unblock everything downstream
2. `status` — what the harvest changed
3. `share` — one candidate, **only if Gate 1's queue is short**. Before the mid-pipeline, so a busy middle never starves the gate that feeds it.
4. `draft` / `evaluate` / `ship` — advance **one** candidate as far as it goes without a human; where it needs one, record that and move on
5. `digest` — weekly only, not every tick
6. summarise: what moved, what is waiting on a human and on whom, and what is blocked and on what

## Standing defaults

- **The loop channel carries candidates only.** `#ai-loop-aurie-conversations` holds one
  thread per prompt candidate; every update about a candidate is a reply in its thread. A
  status roll-up of the loop, if one is posted, goes to `#ai-loop` (`C0C5TSVA8U9`) — the
  status channel every loop shares.

- **Composition only.** Never inline or duplicate a leg's logic. If a leg changes, this inherits it.
- **One tick, one thing.** One candidate drafted, one shared, one digest run.
- **Idempotent.** Every leg dedups on the row, so a re-run never double-posts, double-records or double-ships.
- **Dry run first, always.** Every writing leg defaults to read-only and needs `--confirm`.
- **A quiet channel is a no-op** and that is success, not a failure.

## Cadence

The target is **at least one shipped prompt change per week**, which sets the shape:

| | |
|---|---|
| `harvest` | often — a gate verdict should never sit unread |
| `status` | each tick |
| `digest` | weekly |
| `share` | as candidates become ready, one at a time |
| `draft` / `evaluate` / `ship` | as they become actionable |
| `learn` | when a result lands |

Cadence belongs in the operator's scheduler pointing at `run`. This skill schedules nothing.

## What is not built yet

Being honest about this is load-bearing — a runbook that describes a loop which cannot close reads as working software until someone tries it.

- **Arm-routed candidates cannot ship.** Per-arm prompt bodies are not wired in production ([ENGBACK-1376](https://linear.app/aura-health/issue/ENGBACK-1376)), so `behavioural` and `model/config` — the two classes that most need measuring — stop at Gate 2.
- **The rubric is a read, not a gate.** Its instruments are under repair; a rubric row that contradicts a passed Safety Suite does not block a release today.
- **There is no production scorecard.** Nothing scores logged conversations, so a shipped candidate's `learn` step currently reads the rating series and nothing else.

Steps 1 through 5 of the process run today. Steps 6 and 7 close only for `safety` and `correctness`.

## The legs

| Leg | Skill |
|---|---|
| Observe | `product:digest-conversation-signals` |
| Record | `product:record-prompt-candidates` |
| Curate | `product:manage-prompt-candidates` |
| Share | `product:share-prompt-candidate` |
| Harvest | `product:harvest-prompt-decisions` |
| Draft | `product:draft-prompt-candidate` |
| Evaluate | `product:evaluate-prompt-candidate` + `eng:qa-conversation` |
| Ship | `product:ship-prompt-candidate` + `eng:update-intelligence-prompt` + `eng:release-version` |
| Learn | `product:learn-prompt-candidate` |

## Sources

- The process, the gates and the register: `Learnings/Loop/Aurie Conversations Ideas`
- Channel: `#ai-loop-aurie-conversations` (`C0C29JPEUQG`)
- Sibling orchestrator: `product:features-loop`, which this is modelled on
