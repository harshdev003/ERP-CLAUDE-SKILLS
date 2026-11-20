---
name: evaluate-prompt-candidate
description: >
  Resolve what evidence a drafted candidate needs from its change class, print the commands to
  produce it, and record the scorecard onto the Notion row — moving `drafted` → `evaluated` and
  refusing to without evidence attached. Composes the Safety Suite and rubric harnesses rather
  than reimplementing them. Use when the user says "what do I need to run for CONV-9", "evaluate
  this candidate", "record the gate results", "attach the scorecard", or "what's waiting for
  evals". Do NOT use to run the harnesses directly (`eng:qa-conversation` owns those) or to
  decide whether to ship (that is Gate 2, a human).

metadata:
  version: "0.3.0"
knowledge:
  reads:
    - Guide/Index                          # baseline routing aid (universal)
    - Learnings/Loop/Aurie Conversations Ideas   # the evidence table and the reporting contract
    - Tools/Braintrust                     # the locked suite and the Safety Suite live here
    - Tools/Notion                         # NOTION_TOKEN + the REST access pattern
  writes:
    - Learnings/Loop/Aurie Conversations Ideas
---

# evaluate-prompt-candidate

```bash
S="${CLAUDE_PLUGIN_ROOT}/skills/evaluate-prompt-candidate/scripts/evaluate_prompt_candidate.py"

python3 "$S" ready                                    # drafted and waiting for evidence
python3 "$S" plan CONV-9                              # what its class requires
python3 "$S" record CONV-9 --results scorecard.md --confirm
```

## The class picks the evidence

| Class | Safety Suite | Locked suite | Multi-turn | Source cases |
|---|---|---|---|---|
| `safety` | 3 draws — **blocking** | targeted rows | — | required, informs |
| `correctness` | regression check | — | — | — |
| `behavioural` | regression check | targeted + adjacent | ≥3 drives per arm | required, informs |
| `model/config` | 3 draws — **blocking** | all five datasets | ≥3 drives per arm | — |

Set by the class, not chosen per candidate, so a guardrail fix does not wait on a warmth judge and a cadence change cannot ship on a guardrail pass alone. An unclassified candidate has no resolvable requirements and `plan` says so rather than guessing.

**This table is a rendering, not the rule.** The rule is `references/release_contract.yaml`, which `plan` reads at runtime and which `eng:qa-conversation` gates on — the same file, mirrored byte for byte between the two skills because a skill may not import across plugins. `tests/test_release_contract.py` fails if the copies drift, and if this table stops agreeing with them. Edit the YAML; the table follows.

**Source cases** are the production conversations that prompted the candidate, rebuilt as a dataset and run production-vs-candidate. The suites show the change did no harm; this shows it fixed what it was for. It informs Gate 2 and never blocks on its own. `plan` prints the recipe: a coach `behavioural` change builds from rated replies (`data:investigate-user-rating` → `references/conversation-dataset.md`, judged per issue), a coach `safety` change from guardrail alerts (`data:analyze-conversation-guardrails` → `references/follow-ups/guardrail-dataset.md`, scored on the `handoffToGuardrails` call). Other prompters have no bundled recipe yet, and `plan` says so rather than borrowing the coach's. Both compare with `eng:compare-prompt-versions` and end in a change summary built from its `prompt_diff.md`; record that summary and the experiment links, say "source cases" in it, and Gate 2 reads it under Evidence.

Every class runs the Safety Suite, a pure tone change included. Breakage appears where the pressure was not applied: a behavioural edit has already broken the crisis and de-escalation path once, a path that was reliable and untouched by the change.

## What it composes

`eng:qa-conversation` owns the Safety Suite and the drive harnesses. This skill **reads** the contract — what a class requires, how many draws, and what may not be collapsed into one number — and prints it every time, because it is the part people skip:

- **Report per dataset and per dimension.** Never one unweighted pass rate across all 126 rows, and never repetition pooled with the rest — its 60 rows are 12 replicated cohorts, not 60 independent cases.
- **Never mix dataset replay with multi-turn.** Different replication units, different denominators. Report both and let them converge or disagree visibly.
- **Ship the generated outputs with the scorecard.** Every substantive finding in this rubric's history came from reading the material rather than the numbers.
- **Confidence language may not exceed the number of independent drives.** Five judge passes on one drive licenses "in this drive", never "established".
- **The rubric is a read, not a gate**, until its instruments are repaired. A rubric row that contradicts a passed Safety Suite does not block a release today.

Three draws is not caution: between two draws of an *identical* prompt, 32 to 48 rows flip.

## Recording refuses an empty scorecard

`record` will not move a row to `evaluated` on an empty results file. A scorecard delivered on its own cannot be checked; an empty one cannot be read at all. Results are **appended** to any already on the row, so a second run adds rather than replaces.

## Setup

`NOTION_TOKEN`. `notion_rest.py` is a per-skill copy of the canonical.

## Related

- `draft-prompt-candidate` — creates the version this evaluates
- `eng:qa-conversation` — the harnesses themselves
