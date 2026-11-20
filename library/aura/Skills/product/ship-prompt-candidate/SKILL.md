---
name: ship-prompt-candidate
description: >
  Route a Gate-2-approved candidate to users and record what happened — to everyone for `safety`
  and `correctness`, as a production experiment arm for `behavioural` and `model/config`. Also
  records a rollback. Composes `eng:update-intelligence-prompt` and `eng:release-version` rather
  than owning the release. Use when the user says "ship CONV-9", "how does this one go out",
  "what's waiting to ship", "record the release", or "roll that back". Do NOT use to decide
  whether to ship (that is Gate 2, a human) or to write the verdict afterwards
  (`learn-prompt-candidate`).

metadata:
  version: "0.1.1"
knowledge:
  reads:
    - Guide/Index                          # baseline routing aid (universal)
    - Learnings/Loop/Aurie Conversations Ideas   # change classes, ship routes, the rollback rule
    - Tools/Notion                         # NOTION_TOKEN + the REST access pattern
  writes:
    - Learnings/Loop/Aurie Conversations Ideas
---

# ship-prompt-candidate

```bash
S="${CLAUDE_PLUGIN_ROOT}/skills/ship-prompt-candidate/scripts/ship_prompt_candidate.py"

python3 "$S" ready                      # past Gate 2, with each one's route
python3 "$S" route CONV-9               # what this class means it should do next
python3 "$S" record CONV-9 --version 1.22.0 --confirm
python3 "$S" rollback CONV-9 --reason "guardrail trips stepped up" --confirm
```

## What it accepts

`evaluated` (evidence in, Gate 2 verdict not yet harvested) or `in_review` (harvested as approved). Both are pre-release.

**`released` is written here and nowhere else**, because this is the only leg that knows a release actually happened. `harvest` records the Gate 2 *decision*; it does not claim a deploy.

## The route was decided at Gate 1

The change class picks it, and by the time a candidate reaches here that is settled. This leg does not re-open it.

**`safety` and `correctness` ship to everyone.** There is nothing to learn from withholding a guardrail fix, and a crisis path cannot be A/B tested.

**`behavioural` and `model/config` ship as an arm** — and today they cannot ship at all. No prompter declares `experiments` and the per-arm prompt-body wiring is unbuilt ([ENGBACK-1376](https://linear.app/aura-health/issue/ENGBACK-1376)). `route` refuses rather than falling back to a full rollout, because that would silently convert a measured test into an unmeasured release of precisely the class that most needs measuring.

When the wiring lands: register the experiment with **one** named primary metric and written stopping rules **before** the arms are enabled, and reconcile the lettering — the current Aurie convention is that **arm B is the control**.

## The full-rollout path

`route` prints it, and each step belongs to something else:

1. `eng:update-intelligence-prompt` (Mode A) opens the PR from the Braintrust version and updates `config.js`
2. a human reviews and merges
3. `eng:release-version --level patch` — a prompt-only IS release is a **patch**; the tool otherwise infers minor from a `feat()` commit
4. the Braintrust `[Production]` prompt is synced after deploy — leaving it unsynced makes the registry disagree with production
5. `ship record` writes the release version back

## Rollback reverts both halves

The IntelligenceService commit **and** the Braintrust `[Production]` prompt. Reverting only the code leaves the registry claiming production runs something it does not, which is worse than the regression you were fixing.

A rollback is a **result**, not a failure to record. It stays on the row so the next candidate on that defect sees that this was tried and withdrawn.

## Setup

`NOTION_TOKEN`. `notion_rest.py` is a per-skill copy of the canonical.

## Related

- `learn-prompt-candidate` — the verdict after it reaches users
- `evaluate-prompt-candidate` — the evidence Gate 2 read
