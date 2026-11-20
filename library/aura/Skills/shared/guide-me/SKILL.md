---
name: guide-me
description: >
  Walks the user through everything waiting on them, one step at a time: builds one
  list of their pending actions (PR reviews and merges, gos to give in another
  session, decisions, manual steps in a UI such as a spreadsheet or console), drops
  what is already done, then shows only the current step with the exact action and
  waits for "done" before verifying it and moving on. Trigger on "guide me", "guide
  me one step at a time", "guide me through my tasks one by one", "walk me through
  it", "what do I need to do next", "one item at a time", "go through the list with
  me", "next step", or "status" in a session that keeps a list of asks for the user.
  Do NOT use for the daily standup (`shared:daily-status`), for filing cards
  (`shared:add-linear-issues`), or when the user wants the whole list at once
  without being walked through it.
metadata:
  version: "1.0.0"
knowledge:
  reads:
    - Guide/Index                  # baseline routing aid (universal)
    - Tools/GitHub                 # live PR state before presenting or after "done"
    - Tools/Linear                 # live card state before presenting or after "done"
    - Tools/Slack                  # a ✅ on an ask's thread, read through shared:read-slack
---

# Guide me

You are the user's guide through work only they can do. They have asked for **one
step at a time** because a wall of items is too much to act on: every message you
send holds exactly one thing for them to do. Slow down before each message and check
it holds one step, not two.

## Inputs

- **The session's context**: asks other sessions sent, steps your own work left for
  the user, decisions you are waiting on.
- **An explicit list** the user pastes or points at, if any.
- **The list file** from a previous run (Step 1), which survives a restart.

## Workflow

### 1. Load or build the list

The list lives at `~/.claude/guide-me/<session>.md`, where `<session>` is
`$AINATIVE_SESSION_NAME`, or the first 8 characters of `$CLAUDE_CODE_SESSION_ID`.
It is outside the scratchpad on purpose: the scratchpad is wiped on restart.

Every run gathers every pending action from the inputs above, then:

- **No file, or every item in it is `[x]`:** start a new list (rename a finished
  file to `<session>-<date>.md` first) with what you gathered.
- **File with open items:** keep it, and append each gathered action it does not
  already hold. Resume at the first `[ ]` item (`[~]` items wait on another
  session, not the user).

One line per item: `N. [ ] <what> — <why> — <action> — <link>`.

Each item is one thing the user does: a merge, a go, a decision, or a short run of
UI steps toward one result. Split an item that needs two separate decisions.

**Order:** blocking items first (something else waits on them), then time-bound
ones, then the rest in arrival order.

### 2. Check live state before showing anything

Never show an item the user has already handled. Before presenting the list, and
again before each item:

- PR: `gh pr view <url> --json state,isDraft,mergeStateStatus,reviewDecision,statusCheckRollup`,
  plus its unresolved review threads (GraphQL `reviewThreads`, `isResolved == false`).
  - `MERGED` or `CLOSED`: drop it.
  - **Ready:** not a draft, every check in `statusCheckRollup` passed, no unresolved
    thread, `reviewDecision` is anything but `CHANGES_REQUESTED` (it is often empty
    under rulesets), and
    `mergeStateStatus` is `CLEAN` or `BLOCKED`. Present it; the review or merge is
    the user's step.
  - **Anything else** (`DIRTY`, `UNSTABLE`, a failing or pending check, an open
    thread, `CHANGES_REQUESTED`, a draft): the fix belongs to the session that owns
    the PR. If the item is not already `[~]`, ask that session once to fix it and
    mark the item `[~] waiting on <session>`. Go on to the next item.
  - **A `[~]` item** is re-checked, never re-asked: when the check says ready, set
    it back to `[ ]` in its place.
- Linear card: its current state; drop it if Done or Canceled.
- A go in another session: ask that session, or read its transcript, whether the
  user already gave it there.
- A Slack thread: read it with `shared:read-slack` (`replies`, or `message` for
  the parent's reactions); the user's ✅ means done. Never read Slack directly.

Mark dropped items `[x] (already done)` in the file, so the count stays honest.
`[~]` items don't count as waiting on the user.

### 3. Present the current item only

First message of a run: one line with the count (`7 things need you; here's the
first.`), then item 1. After that, never re-list. Each message has:

1. **What and why**, in one or two sentences.
2. **The exact action**, in one of these shapes:
   - **UI steps:** numbered, at most 3 per message, naming the exact tab, button
     and value. For a longer procedure, give the next 3 and say more follow.
   - **A go in another session:** the session name, the paste-ready text in its
     own code block, and one line on what it unblocks (read-only or a prod write).
   - **A PR:** the full link, what it changes, its risk, and the merge order if
     other PRs depend on it.
   - **A decision:** the options in one line each, and your recommendation first.
3. **What to say back:** usually `Tell me "done"`.

Warn about traps on the way, such as an offer to auto-fix that must be declined.

### 4. Act on the reply

| User says | Do |
|---|---|
| "done" / "merged" | Verify what can be checked (Step 2 checks, or read the result they produced), mark it `[x]`, show the next item. If the check fails, say what you see and keep the item current. |
| "ok" / "go" | A go for the step shown, not "done". Act on that step; the item stays current until its result is verified. |
| An answer to a decision | Act on it, relay it to the owning session with their exact words, mark it, move on. |
| "skip" / "later" | Move the item to the end of the list, show the next. |
| A question | Answer it; the current item stays current. |
| "stop" | Stop; the file keeps the place for the next run. |

**A short go approves only the step shown.** "ok" on an item that bundles several
recommendations is a go to start it, not a decision on each part; ask about the rest.

### 5. Take new items without interrupting

When a new ask arrives mid-walk, append it to the end of the file and mention it in
one line: `Added as #9.` Don't switch to it, unless it blocks something urgent; then
say why you're moving it up.

## Rules

- **One step per message.** Never show the next item before the current one is done,
  skipped or answered.
- **Go text only when it is time.** A go scheduled for later is named, never given
  as paste-ready text, so it cannot be pasted early.
- **No blocking prompts.** Ask in the message; never use AskUserQuestion.
- **Verify, then claim.** "Done" from the user is a cue to check, not proof; say
  "verified" only after the check ran and returned a non-empty result.
- **Finish cleanly.** When nothing is left for the user, say so in one line, list
  anything you could not verify and any `[~]` items still with another session, and
  leave the file; the next run starts a new list or appends to this one (Step 1).

## Output

One short message per step, in the shape of Step 3. No tables of items, no recap of
past items, and no footer.
