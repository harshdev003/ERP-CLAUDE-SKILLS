---
name: share-prompt-candidate
description: >
  Post a prompt-change candidate to `#ai-loop-aurie-conversations` at either gate. Gate 1 opens
  one top-level thread per candidate and asks whether this is the right change; Gate 2 replies
  into that thread with the scorecard, what the change put at risk, and the surfaces its class
  required that the evidence does not mention. Refuses to post anything a reviewer could not
  answer. Use when the user says "share this candidate", "post CONV-9 for review", "ask for a
  Gate 1 decision", "post the evidence for Gate 2", "ask whether to ship CONV-9", or "what's
  ready to share". Do NOT use to read the verdict back (use `harvest-prompt-decisions`), to
  create the row (`record-prompt-candidates`), to attach evidence
  (`evaluate-prompt-candidate`), or to change status by hand (`manage-prompt-candidates`).

metadata:
  version: "0.3.1"
knowledge:
  reads:
    - Guide/Index                          # baseline routing aid (universal)
    - Learnings/Loop/Aurie Conversations Ideas   # the gates, the change classes, the lifecycle
    - Tools/Notion                         # NOTION_TOKEN + the REST access pattern
    - Tools/Slack                          # SLACK_BOT_TOKEN + channel ids
  writes:
    - Learnings/Loop/Aurie Conversations Ideas
---

# share-prompt-candidate

Puts a candidate in front of a human, at **Gate 1 — is this the right change?** and at **Gate 2 — ship it, and how?**

```bash
S="${CLAUDE_PLUGIN_ROOT}/skills/share-prompt-candidate/scripts/share_prompt_candidate.py"

python3 "$S" ready                      # what can be shared, and what the rest still need
python3 "$S" share CONV-9               # renders the post, writes nothing
python3 "$S" share CONV-9 --confirm     # posts, records the permalink, moves to shared

python3 "$S" gate2-ready                # what is evaluated, and what it still owes
python3 "$S" gate2 CONV-9               # renders the evidence post, writes nothing
python3 "$S" gate2 CONV-9 --confirm     # replies in the candidate's own thread
python3 "$S" gate2 CONV-9 --confirm --again   # a second ask, after further evidence
```

One thread per candidate, both gates. Gate 2 replies into the thread Gate 1 opened, so the decision and the discussion that produced it stay in one place — a second top-level message would split them, and the row only records one permalink.

## It refuses candidates a reviewer could not answer

Gate 1 approves a **diagnosis**, not a wording. A post without one cannot be answered, so the thread goes quiet and the row then sits at `shared` looking like it is waiting on a person when it is really waiting on its author. `ready` and `share` both check for:

| Field | Why a reviewer needs it |
|---|---|
| `Defect` | what is going wrong |
| `Diagnosis` | local or structural — and `not yet diagnosed` is **not** a diagnosis |
| `Risk named` | what this change expects to destabilise |
| `Change class` | which gate it will face and how it would ship |

`ready` prints the blocked ones with exactly what each is missing, so filling the gap is a task rather than a puzzle.

## The post

Ordered by what the reviewer decides on, not by the schema: the candidate id and title, the prompter and class with the route that class implies, the defect, the diagnosis and its cause, **what this puts at risk**, the proposed change, the evidence, and then the four replies Gate 1 accepts.

Risk comes before the proposed change deliberately. A prompt has a finite budget for competing instructions, and the reviewer's job is partly to judge whether this change is worth what it might destabilise — which is hard to weigh after you have already read the fix and started agreeing with it.

## Only `proposed` rows

A row at `shared` already has a thread. Re-posting mints a second one and splits the discussion across both, and the harvester then reads whichever it happens to find. `share` refuses and prints the existing permalink instead.

## Setup

`NOTION_TOKEN` and `SLACK_BOT_TOKEN` in the environment. `notion_rest.py` and `slack_post.py` in `scripts/` are per-skill copies of their canonicals — edit the canonical first and propagate in the same PR.

## Related

- `harvest-prompt-decisions` — reads the verdict back out of the thread
- `record-prompt-candidates` · `manage-prompt-candidates`
- The gates and the decision protocol: `Learnings/Loop/Aurie Conversations Ideas`

## Gate 2 — ship it, and how?

A different question from Gate 1, answered from different material: the scorecard against recorded baselines, the diff in plain language, what the candidate said it put at risk, and the class — which is what picks the ship route.

Requires `evaluated`. A `drafted` candidate would post a question nobody can answer, which is the Gate 1 failure mode repeated one stage later. `gate2` and `gate2-ready` both check for `Gate results`, `Proposed diff`, `Risk named` and `Change class`.

**The status does not move.** `evaluated` is what `harvest-prompt-decisions` scans at Gate 2; moving the row would take it out of that scan while the question is still open. The verdict is written by the harvester, with the quote that produced it.

Which means the status cannot also serve as the record that the ask happened — the way `proposed` → `shared` does at Gate 1. So the post writes a marker into the status note and reads it back: a second `gate2` on the same row is **refused**, and `gate2-ready` lists it as `asked` rather than `ready`. Asking twice in one thread splits the answer. When further evidence lands after the first ask, `--again` posts an updated one deliberately.

### What did not run is stated outright

Otherwise a five-row smoke run and a full run produce documents that look alike.

The class says which surfaces it owes — read from `references/release_contract.yaml`, a byte-identical mirror of the copy `eng:qa-conversation` gates on — and the post names any the recorded evidence does not mention:

```
*Not measured*
The recorded evidence does not mention multi-turn drives. Say it ran, or mark it
`N/A — precondition absent`, before answering.
```

This is a keyword read over prose, so it cannot prove a surface did not run, and the post says exactly that. It proves the evidence on the row does not say — which is the thing a reviewer cannot otherwise see.

### How the post tells a reviewer to read it

- A **gate failure blocks the release** and does not rank one candidate below another. Gates are reported apart from the quality read.
- **Dataset replay and multi-turn are never converged into one number.** Different replication units, different denominators.
- **The rubric is a read, not a gate** until its instruments are repaired; a rubric row contradicting a passed Safety Suite does not block.

### The three verdicts

| Reply | What happens |
|---|---|
| `I approve for release` | → `in_review`. Routes by class: safety and correctness to everyone, behavioural and model/config as an experiment arm. Approval is not a release — `ship record` writes `released`. |
| `ask for changes` | → back to drafting, with the failing dimension named. As many rounds as it takes. |
| `no because …` | → `held`. The commit stays in Braintrust, production is untouched, and the harvester prints a **register entry** for `product:prompt-knowledge` so the digest does not re-raise it. |

A candidate dropped at Gate 2 was drafted, evaluated and read — the most expensive answer this loop produces. The stub is printed rather than written: the register lands through a PR, and `reopens_if` is left blank because only a human can say what would reopen the question.
