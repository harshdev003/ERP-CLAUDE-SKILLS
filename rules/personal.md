# Personal working rules (all projects)

Adapted from a senior engineer's personal Claude rules (original in
`library/aura/Rules/My rules/CLAUDE.md`). Their Linear card-bookkeeping section was
team-specific and is not adopted; it stays in the library.

## Never spend extra-usage credits without asking

Credits = the plan's **extra usage**: what gets billed once a plan limit (5-hour or
weekly) is used up. Do not spend them unless I say so.

- **Check before heavy work.** Call `mcp__ccd_session_mgmt__get_usage` (when available)
  before a long task, a background job, a subagent fan-out, or a workflow, and note
  `extraUsage.spent` as the baseline.
- **Keep checking** at natural checkpoints (after a build, after a subagent returns,
  every ~10 tool calls).
- **Stop when credits are about to be used.** If any plan window is at **95% or more**,
  or `extraUsage.spent` has gone up from the baseline:
  1. Stop every process I started: background jobs, monitors, subagents.
  2. Don't start new work that costs tokens.
  3. Tell me which limit was hit, its reset time, and where the task stopped.
- **Resume only on an explicit go-ahead** ("use credits", "continue with credits").
  That approval covers only the task it was given for.

## Save a handoff note before the context fills up, then keep working

Never stop or wait for me because the context is filling up. Keep working and let
auto-compact carry the session on. Before it does, save what its summary would lose.

- Check `context.tokensUsed` with `mcp__ccd_session_mgmt__get_usage` at checkpoints;
  check more often once past ~70% of the window.
- **At about 85% of the window, write a handoff note** to
  `~/.claude/handoffs/<YYYY-MM-DD>-<short-task-name>.md`: the goal, repo/branch/worktree,
  what's done (commits, PRs, tickets), what's left in order, key files, decisions and
  why, open questions, and any uncommitted or stashed work. Say the note's path in one
  line, then carry on. No pause, no question.
- **Keep the note current** at each later checkpoint until auto-compact runs.
- **After a compaction, read the latest note for the task first**, then continue.
- **Start a new session only if I ask for one.**
- The credit rule above still wins.
