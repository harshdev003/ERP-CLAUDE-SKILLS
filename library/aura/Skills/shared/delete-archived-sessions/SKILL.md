---
name: delete-archived-sessions
description: >
  Bulk-deletes archived Claude Code desktop sessions. Lists every session including
  archived ones, keeps the archived ones (never pinned or running sessions), optionally
  only those last active before a date, shows the count, date range and sessions with an
  open PR, then deletes them 25 per call after the user says yes and reports what is left.
  Deleting cannot be undone: it removes the transcript and the session's worktree and
  branch. Runs only in the Claude desktop app (Code tab), where the session-management
  tools exist. Trigger on "delete archived sessions", "clean up archived sessions",
  "clear the archive", "purge old Claude sessions", "delete archived sessions older than
  {date}", or "how many archived sessions do I have". Do NOT use to archive live sessions
  (the app's archive_session), to remove git worktrees or branches
  (`eng:clean-up-worktrees`), or to free disk space (`eng:reclaim-disk`).
metadata:
  version: "1.1.1"
knowledge:
  reads:
    - Guide/Index   # baseline routing aid (universal)
---

# delete-archived-sessions

You are cleaning up someone's session history. Deletion is permanent and runs in the
thousands, so the count and the list of what goes come before any delete, and every
session left out is accounted for by reason.

## Requirements

The `mcp__ccd_session_mgmt__list_sessions` and `mcp__ccd_session_mgmt__delete_session`
tools. They exist only in the Claude desktop app; if they are not loadable (load them with
`ToolSearch` `select:` when they are deferred), say this skill needs the desktop app and stop.

## Incident data hold

While an incident data hold is marked (today, PROD-6926; `AURA_LEGAL_HOLD` set, or `~/.claude/legal-hold/active`
exists), `plan.py plan` and `plan.py batches` refuse with exit 3. Then stop: tell the user
an incident data hold covers Claude sessions and that nothing can be deleted until it is lifted.
Never call `delete_session` without a plan from this run, and never remove the marker
yourself.

## Inputs

| Input | Default | Meaning |
|---|---|---|
| `before` | none | Keep only sessions last active before this date (`YYYY-MM-DD`). |
| `skip_open_prs` | false | Leave sessions whose PR is still open. Their PRs stay on GitHub either way; only the local worktree and transcript go. |

## Steps

### 1. List every session

Call `list_sessions` with `include_archived: true` and `limit: 5000`. Check the result
covers everything: if it returns exactly `limit` rows, raise the limit and list again.
A large result is saved to a file and the tool result gives its path; use that path as
`LISTING`. A small result shows inline: write it to a scratchpad file and use that.

### 2. Plan

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/delete-archived-sessions/scripts/plan.py plan \
  --listing "$LISTING" --out "$SCRATCH/batches.json" [--before 2026-10-01] [--skip-open-prs]
```

It prints `to_delete`, `batch_count`, `skipped` by reason (`not_archived`, `pinned`,
`running`, `after_cutoff`, `open_pr`), `date_range`, `by_month` and `open_prs`.
`listed` always equals `to_delete` plus the skipped counts.

### 3. Confirm

Show the user `to_delete`, the date range, the month counts, the number of approval cards
(`batch_count`), and every open-PR session by title and PR number. Remind them it cannot be
undone. Delete nothing until they say yes. "Show me" or "how many" is not a yes.

### 4. Delete

Print ten batches at a time and call `delete_session` once per batch, all ten in one
message. The app shows its own approval card for each call.

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/delete-archived-sessions/scripts/plan.py batches \
  --file "$SCRATCH/batches.json" --start 1 --count 10
```

Each line is `<n> <total> <ids>`; pass the IDs as `session_ids` with
`reason: "Delete archived sessions (batch <n> of <total>)"`. Never pass
`force_worktree_cleanup`: a session with uncommitted changes is skipped, and the result says
so. Keep a running count from each result's "Deleted N sessions" line, and collect every
session a result reports as skipped. Tell the user the running total after each round.

If a call is refused or fails, stop and report how far it got; do not retry the same batch
without the user.

### 5. Check

List again (`include_archived: true`) and run:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/delete-archived-sessions/scripts/plan.py check --listing "$LISTING2"
```

Report deleted, skipped by reason (including the ones the app skipped in step 4), and what
is still archived, by title. Pinned sessions stay until the user unpins them.
