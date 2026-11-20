---
name: analyze-linear-done
description: >
  Analyse what a team member has been working on in Linear by rolling their completed (Done)
  issues up to top-level parent initiatives, then summarising the largest ones. Produces a
  ranked list of every top-level issue with the member's completed story points summed across
  all nested sub-issues, plus a qualitative summary of the top initiatives — what kind of work
  made them up. Defaults to the previous full calendar week. Trigger on "analyze what {name}
  has been working on", "what kind of issues has {name} been doing", "categorise {name}'s
  completed Linear work", "what did {name} ship last week", "breakdown of {name}'s done issues
  by initiative", "summarise {name}'s completed work", "what has {name} worked on in the last
  2 weeks", "analyze linear done for {name}", or "roll up {name}'s closed issues by parent".
  Do NOT use for story-point velocity or missing-estimate fixups
  (`eng:summarize-weekly-output`), creating cards (`shared:add-linear-issues`), or a daily
  standup (`shared:daily-status`).

metadata:
  version: "3.0.3"
knowledge:
  reads:
    - Guide/Index   # baseline routing aid (universal)
    - Tools/Linear                   # Linear MCP / GraphQL access
---

# Analyze Linear Done

## Goal

Tell the user **what a given team member has been working on** — not just how many
points they closed. Collapse the member's completed Linear issues into the handful
of **top-level parent initiatives** they belong to, rank those by rolled-up story
points, and write a short narrative summary of the biggest ones (what kinds of
tasks made them up).

This is a **read-only** skill: it queries Linear and prints a report in chat. It
never writes to Linear, Slack, Notion, or anywhere else.

## Inputs

| Input | Required | Default | Meaning |
|---|---|---|---|
| Team member | **Yes** | — | Name or email. If the user did not name a member, **ask first** — never guess or default to `me`. |
| `--window` | No | `previous-week` | `previous-week`, `last N days`, or `last N weeks`. |
| `--week-start` | No | `mon` | Week boundary for `previous-week` (`mon` = ISO Mon–Sun, `sun` = Sun–Sat). |
| `--top` | No | `6` | How many top initiatives to write detailed summaries for. |

**Window semantics.** `previous-week` means the **previous full calendar week by
date** (e.g. last Monday 00:00 → this Monday 00:00 UTC), *not* a rolling last-7-days.
"last 14 days" / "last 4 weeks" are rolling windows ending now. The script parses
all of these — pass the user's phrasing through to `--window` (e.g. `--window "last 4 weeks"`).

## Workflow

1. **Resolve the member first.** If the user did not specify whose work to analyse,
   ask: *"Whose completed Linear work should I analyse?"* Do not proceed without it.
2. **Resolve the window.** Map the user's phrasing to `--window`. If they said
   nothing about time, use the default (`previous-week`).
3. **Run the deterministic engine** (it does member resolution, paginated fetch,
   parent-ancestor rollup, and duration math — no judgement, so it's a script):

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/skills/analyze-linear-done/scripts/analyze_linear_done.py" \
     --member "<name-or-email>" \
     --window "<previous-week | last N days | last N weeks>" \
     --top 6
   ```

   The script prints a Markdown report: a ranked **top-level issue table** (points +
   issue count per initiative) and a **child breakdown** for the top N initiatives.
4. **Write the summaries.** This is the only reasoning step. Read the child
   breakdown and, for each top initiative, write a 2–4 sentence summary of *what kind
   of work it was* — count the concrete things (screens/components built, experiments
   run, bugs fixed, designs produced, integrations) rather than restating titles.
5. **Present** the full top-level list (verbatim from the script) followed by your
   top-issue summaries, then a one-line bottom-line on where the member's effort went.

## Output

1. **Final list** — every top-level (no-parent) issue, ranked by the member's
   rolled-up completed points, with issue counts.
2. **Top-issue summaries** — `--top` short narratives describing the composition of
   the largest initiatives.
3. **Bottom line** — one or two sentences on the overall shape of the member's work.

## Data access — API-first by necessity

Linear bulk queries go through the **GraphQL API (`LINEAR_API_KEY`)**, not the MCP,
for the same reason as `eng:summarize-weekly-output`: `mcp__linear__list_issues`
truncates on heavy result sets (output spills to a file and buckets get dropped) and
doesn't paginate the full subtree cleanly. The script pages via `pageInfo.endCursor`
and inlines the parent chain to resolve top-level ancestors in one pass. See
`/CLAUDE.md` → "Tokens are soft requirements" → "API-first by necessity".

**MCP fallback (only when `LINEAR_API_KEY` is unset):** resolve the member with
`mcp__linear__list_users(query=…)`, then `mcp__linear__list_issues(assignee=…,
updatedAt=<window_start_ISO>, limit=250)`, filter to `completedAt` in the window,
and do the parent rollup by walking `parentId` (fetch missing ancestors with
`mcp__linear__get_issue`). Prefer the script whenever the token is available.

## Edge cases

- **No member given** → ask; never default to `me` or guess.
- **Member not found / ambiguous** → the script exits non-zero with `MEMBER_NOT_FOUND`
  or `MEMBER_AMBIGUOUS` and a candidate list. Surface the candidates and ask the user
  to pick a more specific name or email.
- **No completed issues in the window** → the script prints "_No completed issues
  found_". Report that plainly; don't invent work.
- **Unestimated issues** count as 0 points (this skill does not fix estimates — that's
  `eng:summarize-weekly-output`'s job). Note it if a top initiative has many
  0-point children.
- **`LINEAR_API_KEY` unset** → script exits with `NO_LINEAR_API_KEY`; use the MCP
  fallback above.

## Idempotency

Read-only and fully re-runnable: running twice on the same member + window produces
the same report and writes nothing.

## References

- `scripts/analyze_linear_done.py` — the deterministic fetch + rollup engine.
- `eng:summarize-weekly-output` — sibling skill for the *velocity number* + estimate
  hygiene (different job: this skill is about the *composition* of completed work).
