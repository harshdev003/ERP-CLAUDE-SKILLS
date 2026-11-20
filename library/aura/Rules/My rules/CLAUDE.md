# Personal rules (all projects)

## Never spend extra-usage credits without asking

Credits = the plan's **extra usage**: what gets billed once a plan limit (5-hour or
weekly) is used up. Do not spend them unless I say so.

- **Check before heavy work.** Call `mcp__ccd_session_mgmt__get_usage` before a long
  task, a background job, a subagent fan-out, or a workflow, and note
  `extraUsage.spent` as the baseline.
- **Keep checking.** During long work, check again at each natural checkpoint
  (after a build, after a subagent returns, every ~10 tool calls).
- **Stop when credits are about to be used.** If any plan window is at **95% or
  more**, or `extraUsage.spent` has gone up from the baseline:
  1. Stop every process I started: background Bash jobs, Monitors, subagents,
     workflows, sim runs.
  2. Don't start new work or tool calls that cost tokens.
  3. Tell me which limit was hit, its reset time, and where the task stopped, so it
     can be picked up again.
- **Resume only on an explicit go-ahead**, like "use credits" or "continue with
  credits". That approval covers the task it was given for. It does not carry over
  to later tasks or other sessions.

## Save a handoff note at 850k context, then keep working

Never stop or wait for me because the context is filling up. Keep working and let
auto-compact carry the session on. Before it does, save what its summary would lose.

- Check `context.tokensUsed` with `mcp__ccd_session_mgmt__get_usage` at checkpoints.
  On long sessions, check more often once past 700k. If the window is smaller than
  1M, use 85% instead of 850k.
- **At about 850k, write a handoff note** to
  `~/.claude/handoffs/<YYYY-MM-DD>-<short-task-name>.md`: the goal, branch and
  worktree, what's done (commits, PRs, Linear cards), what's left in order, key
  files, decisions made and why, open questions, and any uncommitted or stashed
  work. Say the note's path in one line, then carry on with the task. No pause, no
  question.
- **Keep the note current.** Update it at each later checkpoint until auto-compact
  runs.
- **After a compaction, read the latest note for the task first**, then continue
  from where it says.
- **Start a new session only if I ask for one.** Then use
  `mcp__ccd_session__spawn_task` with the note as the prompt, or give me the prompt
  to paste if that tool isn't available.
- The credit rule above still wins. If credits are about to be used, stop as it
  says, even mid-task.

## Keep my Linear cards current while I work

Do this on every task without being asked, so finished work counts as Done points
the same day, the way it does for Rohan and Jerome. The goal is to record real work
on time, not to add more cards or bigger numbers.

- **Card it before starting.** Every real piece of work has a card assigned to me:
  a fix or feature PR, a review of a teammate's PR, a QA pass, an investigation
  that ends in an answer, a resolved support request. Use the existing card if there
  is one: assign it to me and move it to In Progress. Otherwise, create a sub-card
  under the feature or umbrella card it belongs to. Card fields and shapes are in
  memory `linear-work-cards-conventions`.
- **Split big work when it starts.** Anything estimated 8 or more, or spanning several
  PRs, gets one sub-card per PR (2–5 each), so points land as each PR merges instead
  of all at the end.
- **Estimate from the team Estimation page** (Notion: Procedures › Engineering ›
  Estimation). 1 = no release; 2 = quick change, or had to read code or look up data;
  3 = smallest change that needs a release and QA (every experiment is 3 or more);
  5 = routine; 8 = needs another team; 21 or more = split it first. A PR review is
  1–3 by PR size. A support request is 1–3, and anything over 3 gets its own card.
- **Close each card when its own step ends:**
  - a review is Done once it's submitted;
  - a QA pass is Done once all its cases have run;
  - an investigation is Done once it has an answer;
  - a fix PR goes to In Review when it opens and Done when it merges.

  Don't let finished pieces wait for the parent to be done.
- **When wrapping up a task that touched cards,** check my own cards that are In
  Review or In Progress:
  - add an estimate to any that are missing one;
  - move to Done any whose PR has merged.

  In one line, name any card that has been idle for more than 3 days, and ask before
  closing, canceling or re-scoping it.
- **Don't inflate.**
  - Don't make a card for each commit, BugBot round, rebase, re-run, status check or
    meeting.
  - Don't split one small change into several cards.
  - Don't make a card for work that didn't happen or isn't mine. Search for a
    teammate's card covering the same thing first.
  - Don't raise an estimate past what the Estimation page gives.
  - A parent whose sub-cards split up its work gets no points; the sub-cards carry
    them. The exception is a sub-card that is a task of its own, such as a follow-up
    fix found along the way or another team's card.
  - Never reopen a Done card.
- **Stay quiet.** Change card states and estimates only. Don't post Linear comments
  unless I ask for one.
