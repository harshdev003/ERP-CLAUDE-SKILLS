---
name: qa-linear-task
description: >
  QA a Linear task end to end. Plans test cases from the card and its PR, files them as a
  `QA:` sub-card (label QA) under the task, then — once you say go — executes them in this
  session, a subagent or a new session, ticking each case on the card, filing QA Feedback
  cards for failures and splitting cases it cannot run into a follow-up card. Resolves the
  task from the prompt, else the current branch or PR, else asks. Trigger on "QA this",
  "please QA now", "can you QA ENGMOB-2906", "QA this task", "QA this PR", "create a QA
  card", "plan QA for this ticket", "write test cases for this card", "run the QA card",
  "continue the QA pass", or "execute the remaining QA cases". Do NOT use to implement a
  ticket (`eng:linear-task`), to review a diff (`eng:review-pr`), or for a single surface
  that has its own skill (`qa:qa-aurie-usage-limits`, `qa:talk-to-aurie`,
  `qa:chat-with-aurie`) — this skill routes to those.
metadata:
  version: "1.2.0"
knowledge:
  reads:
    - Guide/Index
    - Tools/Linear
    - Tools/GitHub
    - Compliance/Privacy
---

# QA a Linear task

You are the QA engineer for a change someone just built. Your job is to prove it works the way
production users will meet it, not to prove the tests pass — they already do. Plan the cases
first, put them on a card where the team can see them, and only then touch anything.

**The QA card is the run's state.** Every case lives as a checkbox on the card, and every
execution mode reads and ticks that checklist. That is what lets a subagent or a fresh
session pick up a half-finished pass from the card alone, and what makes "QA done" something
the team can check rather than take on trust.

All Linear calls go through two bundled scripts (API key: `LINEAR_API_KEY`; on any script
error with `"fallback":"mcp"`, warn in one line and use the Linear MCP tools instead):

```bash
S=${CLAUDE_PLUGIN_ROOT}/skills/qa-linear-task/scripts
python3 $S/linear_api.py get-issue --id ENGMOB-2906        # task / card state
python3 $S/linear_api.py create-issue --input-file f.json  # file the QA card
python3 $S/linear_api.py update-issue --input-file f.json  # state moves
python3 $S/linear_api.py states --team <teamId>            # team-scoped state ids
python3 $S/linear_api.py comment --issue <uuid> --body-file f.md
python3 $S/qa_card.py children  --issue ENGMOB-2906        # existing QA cards
python3 $S/qa_card.py label     --name QA --team ENGMOB    # label id (never the QA *team* id)
python3 $S/qa_card.py checklist --issue ENGMOB-2908        # cases + done/open/moved counts
python3 $S/qa_card.py tick      --issue ENGMOB-2908 --case 2.3 [--case 2.4]
python3 $S/qa_card.py move      --issue ENGMOB-2908 --case 3.3 --to ENGMOB-2916
```

**Never edit the checklist by rewriting the description yourself.** `tick` and `move` change
only the named lines. A hand-rewritten body silently drops formatting and can flip boxes you
didn't mean to.

## Step 1: Resolve the task

In order, stop at the first hit:

1. A card id or Linear URL in the prompt.
2. The current branch: Linear branch names carry the id (`jerome/engmob-2906-…`).
3. The open PR for the branch: `gh pr view --json title,body,url,isDraft,headRefName`, then
   read the id from the title or body.
4. **Ask the user.** Never guess a card from recency or similarity.

Fetch the task with `get-issue`. If the resolved card is itself `QA`-labelled, the user means
"run this QA card": skip to Step 4.

**Resume, don't duplicate.** Run `qa_card.py children` on the task. If an open `QA`-labelled
sub-card exists, show its `checklist` counts and ask whether to continue it or plan a new
pass. Two live QA cards for one change split the evidence.

## Step 2: Plan the cases

Read, before writing a single case:

- the task body, especially **Why**, **Scope** and **Done when**;
- the linked PR: `gh pr view <n> --repo <owner/repo> --json title,body,files` and
  `gh pr diff <n>`. The diff tells you which paths actually changed. Test those, plus the
  paths the change could plausibly break;
- the PR's own test plan, if it has one. Fold it in, don't duplicate it.

Then draft the cases using [`references/qa-card-template.md`](references/qa-card-template.md).
It holds the body layout, the numbering rule `qa_card.py` parses, how to write an expected
result, and how to tag each case by who can run it (`sim`, `device`, `backend`, `analytics`,
`human`).

**When the change ships behind an experiment flag**, also read
[`references/experiment-cases.md`](references/experiment-cases.md): the setup checks on the
experiment record, how to get a test account into each arm, safe purchases, the analytics
cases every experiment card carries, the simulator device matrix for layout cases (iPhone SE,
standard, Pro Max, large Dynamic Type), and which cases the simulator can run. Prefer `sim` for
every case a simulator can run; `device` and `human` are for hardware and genuine judgement.

**Show the draft in chat and wait for approval before creating anything.** The case list is
the cheapest point to catch a wrong assumption about the change.

## Step 3: File the QA card

Defaults, unless the user says otherwise:

| Field | Value |
|---|---|
| Parent | the task |
| Title | `QA: <short task title>`, plus ` (PR #n)` when there is a PR |
| Label | `QA`, id from `qa_card.py label --name QA --team <team key>` |
| Team / project / cycle / milestone | same as the task |
| Assignee | `"me"` |
| State | the team's Todo: first `unstarted` state from `states --team` |
| Body | the approved draft |

Attach the PR with `linear_api.py attach-url` when there is one. Report the card link.

## Step 4: Ask how to execute

Split the open cases by tag, then ask once (AskUserQuestion) which mode to use. Recommend
one based on the mix:

- **This session.** Best for short passes and any pass with `device` or `human` cases, where
  the user has to act while you watch logs.
- **Subagent.** Best when the cases are automatable (`sim` / `backend` / `analytics`) and the
  main context should stay clean.
- **New session.** Best for long passes, or when the user wants to keep working here. The
  card is the entire handoff.

"Plan only" is a valid answer. Stop after Step 3 and say how to resume ("run the QA card
ENGMOB-2908"). Mode mechanics, the subagent prompt and the new-session handoff are in
[`references/execution-modes.md`](references/execution-modes.md).

## Step 5: Execute

Move the QA card to the team's In Progress (first `started` state). Then work through the
open cases in order. For each one:

1. **Run it through the right tool.** Surface-specific cases go to the skill that owns that
   surface. The routing table is in [`references/execution-modes.md`](references/execution-modes.md)
   → *Routing*. Do not re-implement what `qa:qa-aurie-usage-limits` or `qa:talk-to-aurie`
   already do.
2. **Decide against the expected result on the card**, not against what seems reasonable
   now. "Same as production" means compare with production. Don't infer it.
3. **Pass:** `qa_card.py tick`, straight away and one case at a time, so an interrupted run
   leaves an accurate card.
4. **Fail:** file a bug as a sub-card of the QA card (label `QA Feedback`, same team,
   `QA: <symptom>` title) with repro steps, expected vs actual, and evidence (log line, event,
   screenshot). Leave the box unticked. A failure that turns out to be pre-existing and
   unrelated to the change gets its own normal bug card instead, linked from the QA card as
   a heads-up, the way PROD-6536 was in the worked example.
5. **Can't run here** (needs a device, a human, an account state you can't create): don't
   leave it hanging. Collect these and go to Step 6.

Post a short results comment on the QA card at the end of each sitting: passed, failed with
card links, moved, and anything surprising.

## Step 6: Split what is left

If open cases remain that this session can't run, create a follow-up sub-card **under the
same QA card**: `QA: remaining … (PR #n)`, label `QA`, grouped by who runs it (e.g.
*Simulator (Claude)* / *Device (user + Claude)*). Keep the original case ids. Then:

```bash
python3 $S/qa_card.py move --issue <QA card> --case 3.3 --case 3.4 --to <follow-up id>
```

Now the original card reads accurately: every case is either ticked or points at where it
went.

## Step 7: Gate

`checklist` on the QA card (and any follow-up) shows `open: 0`, and every failure has a QA
Feedback card that is fixed or that the user agrees is not a blocker. Only then:

1. Move the QA card(s) to the team's `completed` state named Done.
2. **Offer, don't do**, the outward-facing steps: `gh pr ready <n>` if the PR is still a
   draft, and moving the task to In Review. Each needs the user's yes.

If the gate isn't met, say exactly what blocks it: the open case ids and the unresolved
feedback cards.

## Unattended caller

An orchestrator (e.g. `product:features-loop`) may run this skill with no person at decision
time. The caller owns four decisions this skill otherwise asks for, and says so in its own
SKILL.md:

- **Step 1's resume question is answered by the card.** An open `QA`-labelled sub-card on the
  task is continued, never re-planned; with none, a new pass is planned. Nothing is asked.
- **Step 2 approval is skipped.** The draft is filed as planned; the card itself is the review
  surface, and a wrong case surfaces as a failure or a `cannot run` on the results comment.
- **Step 4 runs in subagent mode** without asking, with only the automatable cases.
- **`device` and `human` cases go to a Step 6 follow-up card** straight away, assigned to the
  person the caller names, so the subagent and the person never tick the same card.

Step 7's outward offers (`gh pr ready`, moving the task) belong to the caller. Everything else
— the checklist contract, ticking one case at a time, QA Feedback cards for failures, the
results comment and the Done gate — is unchanged.

## Privacy

QA runs on **test accounts** only (`test+…@aurahealth.io` and the like). Card bodies,
comments and feedback cards never carry an end user's email, name or full UID, per
`Compliance/Privacy`. A test account's UID prefix is fine as evidence.

## Worked example

ENGMOB-2906 (remove the ElevenLabs voice engine) → ENGMOB-2908 (28 cases, 20 passed on the
simulator) → ENGMOB-2916 (the 8 left over, split into simulator and device). Walkthrough in
[`references/worked-example.md`](references/worked-example.md).

## Related — the standing suite

This skill is **change-scoped**: the cases exist to prove one PR behaves, they live on a
Linear card, and they die when that card closes.

The **standing regression suite** is the other half, and it is deliberately separate:
durable per-feature cases in AI Knowledge under `QA/Test Plan and Test Cases/Aurie`, run on
a cadence rather than per change.

- `qa:write-test-cases` authors it.
- `qa:run-test-cases` runs it and writes the results back.

The two meet in one place worth watching for. When a case on a QA card turns out to be
worth checking on **every** release — not just for this change — that is a suite case
wearing a PR's clothes. Say so, and hand it to `qa:write-test-cases` rather than letting it
disappear when the card closes. The reverse also holds: if a suite case is wrong or
unrunnable, fix it there, not by quietly working around it here.
