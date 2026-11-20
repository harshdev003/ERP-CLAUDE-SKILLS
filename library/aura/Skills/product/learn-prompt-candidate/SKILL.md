---
name: learn-prompt-candidate
description: >
  Write a shipped candidate's verdict back onto its row and report the loop's funnel. Records
  won / lost / inconclusive / rolled back with the one-line learning the next candidate on that
  defect needs. Reads a result; never forms one. Use when the user says "record the verdict",
  "the experiment came back", "what did we learn from CONV-9", "show the loop funnel", or
  "what's waiting on a result". Do NOT use to decide an experiment is over (that is the
  experiment owner's call) or to move a candidate before it reached users.

metadata:
  version: "0.1.1"
knowledge:
  reads:
    - Guide/Index                          # baseline routing aid (universal)
    - Learnings/Loop/Aurie Conversations Ideas   # the lifecycle and the register
    - Learnings/Experiments                # where an arm's verdict is reached
    - Tools/Notion                         # NOTION_TOKEN + the REST access pattern
  writes:
    - Learnings/Loop/Aurie Conversations Ideas
---

# learn-prompt-candidate

```bash
S="${CLAUDE_PLUGIN_ROOT}/skills/learn-prompt-candidate/scripts/learn_prompt_candidate.py"

python3 "$S" funnel                     # the whole backlog, by stage and by class
python3 "$S" pending                    # shipped, no verdict yet
python3 "$S" verdict CONV-9 --verdict won --learning "…" --confirm
```

## It reads a result; it never forms one

Whoever owns experiments decides when one is over and what it said. This leg records that decision. `--learning` is required and is the point of the command — it is the one line the digest and the diagnosis leg read next week to avoid re-raising a question the loop has answered.

A verdict can only be written on a candidate that actually reached users (`released` or `experiment_running`). Anything else would record a result nobody observed.

## A loss is an answer

`lost` and `rolled back` are **answered**, which is the opposite of abandoned. They stay in the register so the next candidate on that defect sees that this was tried and what it cost. Recording a loss as abandonment would retire a question the loop actually settled — and the digest would then propose it again.

## The funnel

Three groups — in flight, parked, decided — plus a breakdown by change class, and a closing line that says plainly how many candidates reached users and how many have a verdict. When nothing has, it says the loop has not closed once, which is a more useful thing to read than a table of zeros.

## Setup

`NOTION_TOKEN`. `notion_rest.py` is a per-skill copy of the canonical.

## Related

- `ship-prompt-candidate` — what put the candidate in front of users
- `digest-conversation-signals` — reads these verdicts to avoid re-raising answered questions
