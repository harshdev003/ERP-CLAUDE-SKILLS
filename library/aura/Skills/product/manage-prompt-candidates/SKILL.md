---
name: manage-prompt-candidates
description: >
  Read and move prompt-change candidates in the `Learnings/Loop/Aurie Conversations Ideas` Notion
  database — the status, queue and decision writer for the Conversations loop. Lists and filters
  the backlog, prints one candidate in full, shows the funnel and what is actionable next, and
  records Gate 1 and Gate 2 decisions with the reason attached. Use when the user says "what's in
  the prompt backlog", "show me CONV-9", "approve that candidate", "reject it because…", "park
  that one", "record the experiment verdict", "what should we work on next", or "where does the
  conversations loop stand". Default target is the **Aurie Conversations Ideas** database; pass
  `--data-source-id {id}` for another loop. Do NOT use to create rows (use
  `record-prompt-candidates`) or to post a candidate for review (use `share-prompt-candidate`).

metadata:
  version: "0.1.1"
knowledge:
  reads:
    - Guide/Index                          # baseline routing aid (universal)
    - Learnings/Loop/Aurie Conversations Ideas   # the database, its lifecycle and its property guide
    - Tools/Notion                         # NOTION_TOKEN + the REST access pattern
  writes:
    - Learnings/Loop/Aurie Conversations Ideas
---

# manage-prompt-candidates

The read and write surface for the Conversations loop backlog. Creates nothing — `record-prompt-candidates` owns that — and moves everything.

```bash
S="${CLAUDE_PLUGIN_ROOT}/skills/manage-prompt-candidates/scripts/manage_prompt_candidates.py"

python3 "$S" funnel                                  # where the backlog stands
python3 "$S" list --status proposed                  # filter by status/prompter/change-class
python3 "$S" show CONV-9                             # one candidate in full
```

## Reading

**`funnel`** prints counts by status in three groups — lifecycle, parked, decided — then a **next action** block that says what is actionable and what to do with it:

```
next action
    3 proposed             share to the channel for Gate 1
    1 evaluated            post Gate 2
```

That second block is the point of the command. A list of statuses tells you what exists; this tells you what is waiting on someone. When nothing is actionable it says so and points at the digest, because an empty actionable queue means the loop needs new input rather than more process.

**`show`** prints one candidate in full — the selects, then every text field that has content, then the links. Fields that are empty are omitted rather than printed blank, so what is missing is visible at a glance: a candidate with no Diagnosis and no Risk named is not ready for Gate 1, and the output makes that obvious without a checklist.

**`list`** filters on `--status`, `--prompter` and `--change-class`.

## Resolving a candidate

Every command that acts on one row takes a `ref`: `CONV-9`, `conv-9`, bare `9`, or a title substring.

**ID resolution is tried first, deliberately.** The `Candidate ID` is immutable; a title can be edited out from under a script or a Slack thread. A title substring that matches more than one row refuses and lists them rather than guessing.

## Writing a decision

```bash
python3 "$S" approve CONV-9 --note "diagnosis agreed, structural read is right" --by jerome
python3 "$S" reject  CONV-4 --reason "registered hard stop" --note "this is the global-instruction shape"
python3 "$S" defer   CONV-5 --note "waiting on the prompt-engineer seat"
python3 "$S" verdict CONV-1 --verdict won --note "primary metric +2.1pts over 3 weeks"
python3 "$S" set-status CONV-9 --status drafted --note "braintrust commit a14f"
```

`--note` is **required on every transition**, and it is the whole point. A status change with no note is what makes a backlog unreadable three months later — the row says `rejected` and nobody remembers why, so the same candidate comes back. The note is appended, never replaced, so `Quick status note` accumulates the history:

```
2026-09-21 — proposed → shared by the loop. posted to the channel
2026-09-23 — shared → approved by jerome. diagnosis agreed, structural read is right
```

`--by` records who; it defaults to `the loop`, which is honest when automation moved it and wrong if a person did, so pass it for human decisions.

`reject` requires a `--reason` from the fixed vocabulary (`already tried`, `registered hard stop`, `low impact`, `off-strategy`, `model not prompt`, `timing`, `duplicate`, `other`) because the digest filters on it — free-text reasons are invisible to the thing that needs to read them.

## Moving out of a decided status

`rejected`, `deferred`, `held`, `won`, `lost`, `inconclusive`, `rolled_back` and `abandoned` all mean *a decision was recorded*. Moving out of one is allowed — a parked candidate whose blocker cleared is exactly the case — but it always prints a warning first, because it is usually a mistake, and when it is not, the note is the only record of what changed:

```
WARNING: CONV-5 is deferred, which is a recorded decision.
         Moving it to approved overwrites that. The note below is the only record of why.
```

`Decided at` is stamped whenever a row lands in a decided status.

## Setup

`NOTION_TOKEN` in the environment. `notion_rest.py` in `scripts/` is a per-skill copy of the canonical at `plugins/shared/skills/retrieve-knowledge/scripts/notion_rest.py` — edit the canonical first and propagate in the same PR.

## Related

- `record-prompt-candidates` — creating rows and the dedup guards
- The lifecycle, the gates and the property guide: `Learnings/Loop/Aurie Conversations Ideas`
