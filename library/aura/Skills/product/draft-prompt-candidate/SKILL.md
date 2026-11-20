---
name: draft-prompt-candidate
description: >
  Turn a Gate-1-approved candidate into a labelled Braintrust prompt version and record it on
  the Notion row, moving `approved` → `drafted`. Branches from a named base prompt, replaces the
  system message with a body a human wrote, and keeps the base's model options. Use when the
  user says "draft the change", "create the Braintrust version", "make a dev prompt for CONV-9",
  or "what's approved and waiting to be drafted". Do NOT use to deploy to production (that is
  the ship leg, after Gate 2), to compose the prompt text itself, or to run the evals
  (`evaluate-prompt-candidate`).

metadata:
  version: "0.1.2"
knowledge:
  reads:
    - Guide/Index                          # baseline routing aid (universal)
    - Learnings/Loop/Aurie Conversations Ideas   # the lifecycle and the gates
    - Tools/Braintrust                     # BRAINTRUST_API_KEY, prompt versioning
    - Tools/Notion                         # NOTION_TOKEN + the REST access pattern
  writes:
    - Learnings/Loop/Aurie Conversations Ideas
---

# draft-prompt-candidate

```bash
S="${CLAUDE_PLUGIN_ROOT}/skills/draft-prompt-candidate/scripts/draft_prompt_candidate.py"

python3 "$S" ready                                   # approved and waiting
python3 "$S" draft CONV-9 --base-prompt <bt-id> --body new-coach.txt
python3 "$S" draft CONV-9 --base-prompt <bt-id> --body new-coach.txt --confirm
```

## It does not write the prompt

The body comes in as a file a human wrote. A prompt is a layered behavioural system where changes anywhere produce effects everywhere, and the one thing the process document is unambiguous about is that a human holds **final editorial authority over every word**. This skill handles versioning and bookkeeping; it does not compose.

It also does not touch production. That is the ship leg, after Gate 2.

## POST, never PATCH

`POST /v1/prompt` with a `project_id` and `slug` creates a new **version**. `PATCH` mutates state without one — so a change made with PATCH cannot be bisected to, reverted to, or compared against later, which is the entire reason the loop keeps a granular history.

Every version carries a description naming the candidate (`CONV-9: <title>`), so the Braintrust activity log reads as the loop's change log rather than a list of anonymous edits.

## What it preserves

The base prompt's **model options** — model, reasoning effort, verbosity, tool choice — are carried across unchanged, so a body edit is a body edit. Changing the model is a separate candidate with its own class (`model/config`) and its own evidence requirements.

Only the **first** system message is replaced, and the scaffolding is kept in whichever shape the base has. `Coach [Production]` is *delimited* since 2026-10-20 — the body, then system `---`, user `---`, system `{{metadata.contextInstructions}}` and the conversation message — so the later messages are left exactly as they are. An *appended* base (the context tag ending the body) gets its suffix carried onto the new body.

## A re-draft clears the previous evidence

Gate 2 can send a candidate back with "ask for changes", which returns it to `approved` for another draft. The scorecard already on the row belongs to the **previous body**, and `evaluate record` appends — so leaving it would hand Gate 2 one write-up covering two different prompts, and a large prior scorecard can exhaust the field's budget and block the new evidence outright.

`draft` therefore clears `Gate results` when it creates a new version, saying how much it cleared and which version it belonged to. Nothing is lost: the Braintrust experiments that produced that evidence are where it actually lives.

## It refuses unapproved candidates

Only `approved` rows can be drafted. Drafting before Gate 1 spends editorial effort on a diagnosis nobody has agreed to, which is the expensive half of this loop.

`ready` also flags an approved candidate with no `Proposed diff` recorded — approved *in principle* with nothing written down is a common and confusing state.

## Setup

`BRAINTRUST_API_KEY` and `NOTION_TOKEN`. `notion_rest.py` is a per-skill copy of the canonical.

## Related

- `evaluate-prompt-candidate` — what to run against the version this creates
- `harvest-prompt-decisions` — the Gate 1 verdict that unblocks drafting
