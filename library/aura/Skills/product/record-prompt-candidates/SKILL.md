---
name: record-prompt-candidates
description: >
  Record a prompt-change candidate (or a batch) into the `Learnings/Loop/Aurie Conversations Ideas`
  Notion database — the Conversations loop's unit of work, the way an idea is the Features
  loop's. Source-agnostic: takes the signal digest's JSON, a hand-written list, or a single
  inline candidate. Dedupes on a frozen `Fingerprint`, folds renames back onto their row, and
  refuses a candidate that restates something the loop already answered. Use when the user says
  "record this prompt candidate", "log this conversation defect", "add this to the prompt
  backlog", "ingest the digest output", or describes a prompt problem worth tracking. Default
  target is the **Aurie Conversations Ideas** database; pass `--data-source-id {id}` for another loop.
  Do NOT use to change a candidate's status or decision (use `manage-prompt-candidates`), or to
  post one for review (use `share-prompt-candidate`).

metadata:
  version: "0.2.2"
knowledge:
  reads:
    - Guide/Index                          # baseline routing aid (universal)
    - Learnings/Loop/Aurie Conversations Ideas   # the database this writes to, and its schema
    - Tools/Notion                         # NOTION_TOKEN + the REST access pattern
  writes:
    - Learnings/Loop/Aurie Conversations Ideas
---

# record-prompt-candidates

Adds prompt-change candidates to the `Learnings/Loop/Aurie Conversations Ideas` database. Idempotent — re-running on the same input is a no-op once everything is in sync.

A **candidate** is a proposed change to one named prompter, carrying a defect, a diagnosis, and once drafted a diff. It is not an idea: it has a `Prompter` and a `Change class` rather than a `Screen` and a `Domain`, and it must say what it expects to put at risk.

## What it takes

A JSON array, from a file, stdin, or inline. Only `title` and `prompter` are required; everything else is filled in as the candidate moves through the loop.

```bash
S="${CLAUDE_PLUGIN_ROOT}/skills/record-prompt-candidates/scripts/record_prompt_candidates.py"

python3 "$S" plan  --from-json digest.json               # dry run
python3 "$S" apply --from-json digest.json               # STILL a dry run
python3 "$S" apply --from-json digest.json --confirm     # writes
python3 "$S" list  --status proposed
```

`apply` without `--confirm` prints the plan and writes nothing, so the default of every command in this skill is read-only. That is deliberate: an agent following the snippet above cannot mutate the backlog by getting one flag wrong, only by passing a flag that says what it is for.

Accepts camelCase (what the digest emits) or snake_case (what a human types). Field names map to the Notion properties one-to-one:

| JSON | Notion | Notes |
|---|---|---|
| `title` | Title | required |
| `prompter` | Prompter | required; validated against the enum |
| `changeClass` | Change class | `safety`, `correctness`, `behavioural`, `model/config` |
| `defect` | Defect | what is going wrong, in the user's terms where possible |
| `evidence` | Evidence | links to the rows that show it |
| `diagnosis` | Diagnosis | `local`, `structural`, `not yet diagnosed` |
| `cause` | Cause | the generative cause, if one has been identified |
| `riskNamed` | Risk named | what this change expects to destabilise |
| `proposedDiff` | Proposed diff | |
| `gateResults` | Gate results | per suite and dimension, never one blended number |
| `braintrustPrompt` / `braintrustVersion` | Braintrust prompt / version | |
| `status`, `statusNote`, `rejectionReason`, `verdict` | the decision record | **create only** — see below |

Every enum is validated before any write, `verdict` included.

## The three guards

Each exists because of a specific way a backlog rots.

**1 · Fingerprint dedup.** `sha1(prompter|change_class|title)` truncated to 12 chars, written on create and then **frozen**. The same candidate arriving from the digest, a Slack thread and a hand-written note collapses onto one row.

The fingerprint is *not* the row's address — the immutable `Candidate ID` (`CONV-42`) is. Never recompute the fingerprint to look a row up: its inputs are editable, so a fingerprint derived from a renamed title resolves to nothing.

**2 · Rename folding.** A retitled candidate hashes differently and would otherwise mint a duplicate. When the prompter and class match and the titles overlap by ≥0.6 (Jaccard over stemmed tokens), the update folds onto the existing row instead.

**3 · The similarity report.** Before anything is written, `plan` prints every existing row whose title overlaps by ≥0.4, with its status and its reason:

```
ALREADY ANSWERED — read these before applying:
  ! CONV-14 [rejected] coach: ask fewer questions per turn
      registered hard stop

SIMILAR — an open or parked candidate already covers this:
  ~ CONV-3 [proposed] quickProcessing: revert the QP Sentinel prompt
```

## The register refuses, and `--force` does not clear it

Before dedup, every candidate is read against the rejected-hypothesis register — title, defect, diagnosis, cause, diff and status note together, because the title is a label and the hypothesis usually lives in the diagnosis.

```
REFUSED — the register has already answered this:
  x coach: ask fewer questions per turn
      REG-1 [hard_stop] Question rate is not the variable - forward motion is
      matched "fewer question"
      Four configurations were measured on non-final-turn question rate…
      instead: Measure forward motion: does each non-final turn add an angle…
      reopens: A measurement shows a configuration that advances the arc…
```

`--force` clears the *similarity* warning above. It does not clear this. The register is measurement rather than opinion, and the documented way past an entry is its own `reopens` condition — meet it and retire the entry in `product:prompt-knowledge`. An override flag on a measured result would make the register advisory, which is the state it exists to end.

`references/register.yaml` is a byte-identical mirror of the owner's copy and `tests/test_prompt_register.py` fails if it drifts.

A candidate never matches its own row — re-recording an answered candidate would otherwise flag itself and refuse the batch, taking every unrelated new row down with it. Repeat fingerprints *within one input* collapse too, so a digest that emits the same candidate twice produces one row rather than two pages sharing a fingerprint.

`apply` **refuses** when anything lands in the answered group — the loop has settled that question and re-proposing it silently is how a rejected idea comes back a month later reworded. Override with `--force`, and say what is different in `statusNote`.

A match in the *similar* group only warns. The duplicate that actually costs you is usually a row somebody opened last week, not one the team settled months ago, so both are reported — but only the settled one blocks.

## What it will never overwrite

**The decision record.** `Status`, `Quick status note`, `Rejection reason` and `Verdict` are written on **create only**. On an update they are left alone, and a row whose status is one a human set — `approved`, `rejected`, `deferred`, `held`, `won`, `lost`, `inconclusive`, `abandoned`, `rolled_back` — is skipped entirely, with the reason printed.

This is the loop's load-bearing rule: automation never overwrites a human decision. Re-observing a defect updates its evidence and its last-seen date, never its verdict.

## Validation aborts the batch

A bad enum value does not error against the Notion API — it silently creates a new select option, and the row lands in a bucket nobody filters on. So every enum is checked before any write, and one bad row fails the whole batch rather than half-applying it.

One rule beyond the enums: **a `safety`-class candidate must carry a diagnosis.** It routes to the strictest gate and the most senior reviewer, so arriving without a stated cause wastes both.

## Setup

`NOTION_TOKEN` in the environment (see the repo's *Notion REST integration* notes). `notion_rest.py` in `scripts/` is a per-skill copy of the canonical at `plugins/shared/skills/retrieve-knowledge/scripts/notion_rest.py` — edit the canonical first and propagate in the same PR.

## Related

- `manage-prompt-candidates` — status, queue and decision writes
- `share-prompt-candidate` — post a candidate to the loop channel for Gate 1
- The loop's process, gates and property guide: `Learnings/Loop/Aurie Conversations Ideas`
