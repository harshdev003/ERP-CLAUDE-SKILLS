---
name: summarize-weekly-output
description: >
  Reports a Sun→Sat week of engineering output: Linear story points closed, GitHub PRs merged,
  and lines added/deleted per repo, fixing un-estimated cards before reporting. Trigger when the
  user says "summarize weekly output", "weekly summary", "how many points did I do this week",
  "how many PRs did I ship this week", "how much code did I write this week", "lines added this
  week", "summarize linear velocity", "Linear velocity this week", "any card missing points",
  "team weekly summary", or asks for a recap of the current/previous Sun→Sat window. Supports
  individual mode (default `me`), `--user`, `--team`, `--week {offset}` (0 = this week, -1 =
  last week, …), `--no-fix` (skip the estimate fixup), `--no-prs` (skip the GitHub sweep), and
  `--fix-others` (in team mode, prompt for teammates' missing estimates). Do NOT use for
  creating Linear cards (use `shared:add-linear-issues`), a daily standup status (use
  `shared:daily-status`), or rolling completed work up to parent initiatives (use
  `eng:analyze-linear-done`).

metadata:
  version: "3.4.0"
knowledge:
  reads:
    - Guide/Index   # baseline routing aid (universal)
    - Procedures/Engineering/Estimation # canonical FAST story-point scale (pre-fetched for the estimate-fixup step)
    - Tools/Linear                      # Linear MCP / GraphQL access
    - Tools/GitHub                      # gh CLI auth + API rate-limit and failure-mode rules
---

# Summarize Weekly Output

## Goal

Tell the user what they (or the eng team) produced in a Sun→Sat week, across both systems
that record it: **Linear story points closed** and **GitHub PRs merged with their code
volume**. Make the points number honest first, by interactively setting estimates on
un-pointed completed cards before reporting.

The two halves answer different questions and neither substitutes for the other — points
measure agreed effort, PR volume measures what actually landed. Reporting them together is
the point of the skill; reporting either alone is what the sibling skills already do.

## Inputs

Args, all optional:

| Arg | Default | Meaning |
|---|---|---|
| `--user <name\|email\|me>` | `me` | Who to summarise. Mutually exclusive with `--team`. |
| `--team` | off | Full eng team summary (per-person breakdown + grand total). |
| `--week <offset>` | `0` | `0` = current Sun→Sat, `-1` = previous, etc. |
| `--no-fix` | off | Skip Phase 2; report-only (use for cron/scheduled runs). |
| `--no-prs` | off | Skip Phase 1b; Linear points only, no GitHub sweep. |
| `--fix-others` | off | In team mode, also prompt for teammates' un-estimated cards. Default off — courtesy guard. |

The week is **Sunday 00:00 → Saturday 23:59** in the user's local timezone. Compute
boundaries from the system clock; do not infer a different week convention from
Linear's cycle dates.

## Workflow

### Phase 1 — Sweep

**API-first by necessity for Linear bulk queries** (see `/CLAUDE.md` → "Tokens are soft
requirements"). This skill deviates from the repo-wide MCP-first default because
`mcp__linear__list_issues` silently truncates on heavy completion days — output spills
to a file and entire buckets get dropped. The Linear GraphQL API (`LINEAR_API_KEY`)
paginates cleanly via `pageInfo.endCursor`, doesn't truncate on bulk/team queries, and
exposes the `updatedAt` / `completedAt` filter fields the MCP doesn't. Fall back to the
Linear MCP only when `LINEAR_API_KEY` isn't set. Consistent with sibling
`shared:daily-status`.

Fetch issues for the chosen scope updated within the Sun→Sat window:

_GraphQL (primary):_ query `issues` filtered on `updatedAt >= <window_start_ISO>`,
scoped by `assignee` (individual mode) or `team` = `Product/Design` (team mode),
with `first: 250`, `orderBy: updatedAt`. Page through `pageInfo.endCursor` until
`hasNextPage` is false — never proceed on a partial result set. Request the fields
the buckets below need:
`identifier title estimate completedAt updatedAt state { name type } assignee { name } parent { id } children(first: 250) { nodes { id identifier title estimate completedAt team { key } state { type } } pageInfo { hasNextPage endCursor } } attachments { nodes { url } }`.

`attachments` carries the linked PR URLs the release-floor check in Phase 2 reads.

`children` is a connection too: when a card's `children.pageInfo.hasNextPage` is true, page it with `issue(id) { children(first: 250, after: <endCursor>) { … } }` until it is false. A breakdown judged on part of the children can skip a parent that has an own-task child on a later page.

_MCP fallback (only when `LINEAR_API_KEY` is unset):_

```
mcp__linear__list_issues(
  assignee=<resolved user> | unset (team mode),
  team="Product/Design" (team mode),
  updatedAt=<window_start_ISO>,
  limit=250,
)
```

Under the MCP fallback a `--team` result can exceed the tool-result token budget;
spawn a sub-agent to slice the cached output file in ~80,000-char spans (the
pattern from the manual run that motivated this skill). The sub-agent returns a
structured digest; the main thread does the bucketing and rendering. The GraphQL
path avoids this entirely — its cursor pagination is the preferred way to handle
large team sweeps.

Bucket each issue into one of:

- **`completed_with_estimate`** — `state == Done` AND `completedAt` in window AND `estimate` set
- **`completed_no_estimate`** — `state == Done` AND `completedAt` in window AND `estimate` is null. **This is the critical bucket — it deflates the velocity number.**
- **`active_no_estimate`** — touched in window AND not Done AND `estimate` is null AND state ∈ {`In Progress`, `Eng In Progress`, `In Review`, `Blocked`}. **Note:** `Todo` is *not* in this set — Todo cards haven't started, so estimating them is planning work, not weekly-velocity hygiene. Backlog / Canceled / Duplicate are also excluded.
- **`product_rollup_skipped`** — long-running parents whose children carry the actual points. Bucket here regardless of estimate or state; estimating these double-counts work the children already cover. Detect by **all** of:
  1. `identifier` starts with `PROD-`
  2. **And at least one of:**
     - Has children whose own estimates already covered work in the window (strongest signal — if the children racked up points this week, the parent is by definition a rollup), OR
     - Title matches a rollup pattern: starts with `Add / update`, `Improve <X> / <Y>`, `Porting <X>`, `<X> Loop - Ideas to Release`, or other obviously meta phrasing.
  3. **Do NOT** require `parentId` to be null — many Product rollups are themselves parented to a higher catch-all (e.g. PROD-1522, PROD-942, PROD-1220 all sit under PROD-1287). The null-parent test was in v1.1 and produced false negatives; v1.2 drops it.

  **Positive examples:** `PROD-1522` "Add / update skills" (Done, parent of 6+ ticket-per-skill children), `PROD-942` "Improve agent knowledge / tools" (Eng In Progress, parent of feature work), `PROD-1220` "Porting QA agent process to Aura" (Eng In Progress).

  **Negative example (must NOT bucket here):** `PROD-1664` "Rename eng:summarize-linear-progress → summarize-linear-velocity" — parented to PROD-1522 but is itself a leaf with its own estimate. It belongs in `completed_with_estimate`. The PROD- prefix alone never triggers; the rollup-pattern OR child-signal evidence does.

  Surface in the report's "Intentionally skipped" section with reason `top-level product rollup`. Never prompt on these.
- **`breakdown_parent_skipped`** — a card of **any** team (ENGBACK, ENGMOB, ENGWEB, ENG…) that was broken down into sub-cards which split the same work. The sub-cards carry the points; a parent pointed as well counts the work twice. Requires the card's **complete** children list (the sweep fetches them and pages past the first 250; see Phase 1). Judge each child:
  - **Breakdown** — the child is a piece of the parent's own work, usually the same team and repo (e.g. `ENGBACK-2200` "verify the Apple V2 signed payload" split into `ENGBACK-2201` count by version + `ENGBACK-2202` drop V1).
  - **Its own task** — the child is separate work the parent needed or uncovered: typically another team's card (a mobile card that needs a new backend endpoint gets a backend card), a follow-up fix found along the way, or an audit across other repos. Both parent and child are tasks, **both get points** — the parent stays in `completed_no_estimate`.

  Bucket here only when the children are a breakdown. A mixed parent (some breakdown, some own-task) or an unclear one stays in `completed_no_estimate` and is prompted, with the children listed so the user decides; prefer prompting over silently skipping. Children that are all Backlog or Canceled are no breakdown — the parent did the work. Report reason: `broken into sub-cards`. Never prompt on these. Rule from Jerome, 2026-11-06 (ENG-1842).
- **`other`** — everything else (in-window touches that are already estimated and still active, or Done-but-completed-before-window). Used only in the Highlights section.

State `Done` with `completedAt` *before* the window goes to `other`, not to
`completed_*`. The bucket condition is the timestamp, not the label.

### Phase 1b — GitHub PR sweep

**Skip this phase entirely if `--no-prs` is set.** Otherwise it runs for the same window and
the same people as Phase 1.

**Resolve the GitHub login for each person in scope.** `me` resolves deterministically:

```bash
gh api user --jq .login
```

`--user` and `--team` need a Linear-name → GitHub-login mapping, which lives in the `GitHub`
column of the **Team** page in AI Knowledge. Read it for every person in scope. A person with
no mapping still gets their full Linear row; their PR and code cells render `—` and the report
names them under the table as unmapped. **Never render `0` for an unresolved login** — zero is
a measurement ("they merged nothing"), and a missing mapping is not one.

**Fetch the PRs with two searches**, using the same window boundaries Phase 1 computed. Two,
because the buckets key off different timestamps and `gh search prs` exposes only one of them:

```bash
# 1. The merged bucket — the headline count. The window is a SERVER-SIDE qualifier.
gh search prs --author <login> --merged \
  --merged-at "<start_iso_local>..<end_iso_local>" \
  --limit 200 --json number,repository,title,createdAt,url

# 2. The in-flight bucket — opened in the window, not merged in it.
gh search prs --author <login> \
  --created "<start_iso_local>..<end_iso_local>" \
  --limit 200 --json number,repository,title,state,url
```

**Pass both bounds as full ISO 8601 timestamps carrying the local UTC offset** — e.g.
`2026-09-13T00:00:00-07:00..2026-09-19T23:59:59-07:00` — never the date-only `2026-09-13..2026-09-19`
form. GitHub reads a bare date as a **UTC** calendar day, while Phase 1's week is Sunday 00:00 to
Saturday 23:59 *local*. Under a negative offset a Saturday-evening local merge is already Sunday in
UTC and drops out of the window, so the PR total silently stops covering the same week as the points
total. The two forms agree on most weeks, which is what makes the mismatch easy to ship: it only
diverges when someone merges near a boundary, and then only by a PR or two.

**`mergedAt` is not one of the `--json` fields `gh search prs` can return**, so the merge window
has to be expressed as the `--merged-at` qualifier and can never be applied by filtering results
afterwards. Searching on `--created` alone gets the bucket wrong twice over: a PR opened before
the window and merged inside it never appears, and `state` alone cannot separate a merged PR
from one closed unmerged.

The buckets:

- **`prs_merged`** — every result of search 1. **This is the headline PR count.** It pairs with
  `completed_with_estimate`: both count work that *landed* in the window, whenever it started.
- **`prs_open`** — search 2 where `state == "open"`. Surfaced separately, never folded into the
  headline.
- **`prs_closed_unmerged`** — search 2 where `state == "closed"`. Counted separately; its code
  volume is excluded. (`gh search prs` reports `state` as `"merged"` for a merged PR, so these
  two cases are distinguishable within search 2's results.)

`gh search prs` caps at 1000 results and `--limit` at 200; a single person-week never
approaches either, but a `--team` sweep queries **one login at a time** rather than OR-ing
authors into one query, so no individual result set can crowd out another's. A result set that
comes back at exactly the `--limit` is a truncation signal — raise it or split the window
rather than reporting the capped number.

**Fetch code volume per merged PR.** The search endpoint does not return diff stats, so each
merged PR needs its own call:

```bash
gh api "repos/<owner>/<repo>/pulls/<number>" \
  --jq '[.additions, .deletions, .changed_files] | @tsv'
```

Three rules govern this loop, all three from `Tools/GitHub`:

1. **Cap concurrency at 4.** GitHub enforces a burst/concurrency limiter separate from the
   hourly quota: an 8-thread sweep tripped it while `/rate_limit` still reported ~4995/5000
   core remaining. The hourly quota is not the binding constraint and must not be read as
   headroom.
2. **Fail closed on any non-200.** Record the failure and carry it into the report; never let a
   failed fetch contribute `0` additions. A sweep that silently books errors as zeros reports a
   *smaller* number with no indication it is wrong, which is the failure mode worth designing
   against.
3. **A `--jq` parse error empties stdout.** `gh api` exits non-zero, but a wrapper shaped
   `stdout if returncode == 0 else ""` collapses that onto a legitimate empty result. Check the
   status explicitly, and do not measure it through a pipe — `cmd | head; echo $?` reports
   *head's* status; use `${PIPESTATUS[0]}`.

Sum `additions`, `deletions` and `changed_files` over `prs_merged` only, and keep the per-repo
subtotals for the report table.

### Phase 2 — Interactive estimate fix (gates the report on completed-no-estimate only)

**Skip this phase entirely if `--no-fix` is set.** Otherwise:

**Phase 2 only gates on `completed_no_estimate`.** Cards completed in the window
without an estimate are critical — they silently deflate the velocity number, so
we must fix or explicitly skip each one before the report is rendered.

Cards in `active_no_estimate` are **surfaced in the report's "Active without
estimate" section but the skill does not prompt for them** ("share but continue").
They aren't impacting this week's velocity claim until they're actually completed,
so blocking the report on them is friction without a payoff. The user can run a
follow-up estimation session anytime they like.

Cards in `product_rollup_skipped` and `breakdown_parent_skipped` are listed in the
report under "Intentionally skipped" with the reason `top-level product rollup` or
`broken into sub-cards`. **Never prompt on these** — their children already carry
the points. A parent whose child is its own task (another team's card, a follow-up
fix) is prompted like any other card: both get points.

If `completed_no_estimate` is empty, print
"✅ All cards completed in window have estimates." and continue to Phase 3.

If non-empty, **show the full list before any prompts**
(plan-before-side-effects per principle 4). For each card show identifier,
title, state, `completedAt`, and a heuristic-suggested estimate.

**Suggested estimates — source the scale from AI Knowledge.** Derive each suggestion from the
canonical FAST rubric at `Procedures/Engineering/Estimation`, pre-fetched via this skill's
`knowledge.reads`. If the page wasn't pre-fetched (token/MCP unavailable), pull it with
`retrieve-knowledge path=Procedures/Engineering/Estimation`. The user always confirms the final
number.

**Every card this step sizes is already completed, so the release floor applies to almost all of
them.** Work that ships is **3 or more**, so a suggestion never starts below 3 — and a completed
card is **treated as having shipped by default**. The exception needs positive evidence to the
contrary: a card whose `attachments` carry no PR URL, and whose title reads as an investigation, a
data/ops run, or a decision or spike, shipped nothing — size that one from the page's
support-estimation guidance, where 1 and 2 are available.

The default runs that way round on purpose. Under the MCP fallback `attachments` may be absent
entirely, and an *absent* attachment list is not evidence a card shipped nothing — so the floor has
to be what applies when the data is missing. Keying the floor on *seeing* a merged PR would silently
suggest 1s and 2s for every card whose attachments failed to load, which is the exact defect this
rule exists to stop.

The title-pattern table below is this skill's own heuristic for the shape of the work, applied on
top of the floor — not a mirror of the page's scale:

| Title pattern | Suggested points |
|---|---|
| Single-line fix, version bump, doc tweak, copy change, small endpoint patch, prompt tweak, single-day feature, monthly-ops task, small refactor | 3 |
| New skill, multi-file feature, integration glue | 5 |
| Multi-day feature, migration, infra change | 8 |
| Cross-system project, large refactor, new service | 13 |

Above the floor the deciding gate is *does it need another team?* (the 5↔8 line); raw file/line
count only forces a higher number at the page's 21/32 split-warnings, and even then only paired
with real complexity.

Then loop card-by-card **over `completed_no_estimate` only**. Prompt format:

```
[3 of 7] PROD-1234 — "Fix coach analytics endpoint timeout"  (Eng In Progress)
Suggested: 5
> _
```

Accepted inputs:

- A number (`1`, `2`, `3`, `5`, `8`, `13`) → write the estimate. _GraphQL (primary):_ `issueUpdate(id: <id>, input: { estimate: N })`. _MCP fallback:_ `mcp__linear__save_issue(id, estimate=N)`. On success, move the card to `completed_with_estimate` or `active_no_estimate→active_with_estimate` in memory.
- Empty input → accept the suggestion (same as typing the suggested number).
- `s` / `skip` → leave un-estimated; mark `intentionally_skipped` for the report.
- `q` / `quit` → exit the loop. Remaining un-prompted cards stay in their `*_no_estimate` bucket for the report.

**Team mode rules:**

- Default: only loop over the **invoking user's own** completed-no-estimate cards. Teammates' un-estimated cards are listed in the report but not prompted on.
- With `--fix-others`: prompt for everyone's `completed_no_estimate`, but prefix each prompt with `[editing <Name>'s card]` so the user knows they're touching someone else's ticket. Active-no-estimate cards are still surfaced-only regardless of `--fix-others`.

After the loop, recompute the buckets in memory (no need to re-fetch — the
estimate write returned the new value). Track every applied change in an
`audit_trail` list: `(identifier, title, applied_estimate)`.

### Phase 3 — Report

Render markdown:

1. **Header:** `Week of {Sun-date} → {Sat-date}` and the chosen scope (user name, or
   `Team — Engineering`). When the window's Saturday is still in the future, say so — the run
   is a partial week and every number below is a running total, not a final one.

2. **Headline** — one line, the three numbers the skill exists to produce:

   `{P} points · {N} PRs · {A} lines added (−{D} deleted, net {A−D} across {F} files)`

   `{N}` is `prs_merged`. Follow it with a short table carrying points, cards, PRs merged,
   additions, deletions, files and repos touched; in team mode, one row per person plus a
   totals row. If `--no-prs` was set, render the points half alone and say the GitHub sweep
   was skipped rather than printing zeros.

3. **Points completed this week** — sum of `estimate` over `completed_with_estimate`. In team mode, also a per-person breakdown. A day-by-day points table is worth rendering when the window covers three or more active days.

4. **PRs and code volume** — render unless `--no-prs` was set. A per-repo table sorted by
   additions desc: repo, PRs merged, additions, deletions, files. Below it, state the
   `prs_open` and `prs_closed_unmerged` counts in a sentence — they are context for the
   headline, not part of it. Read the concentration out loud when one repo dominates
   ("AINative is half the PR count and 72% of the added lines"): which repos absorbed the
   week is the part a reader acts on, and it is not legible from a total.

   Any PR whose diff fetch failed is listed here by number with the error, and the totals
   above carry a `≥` marker. A partial sweep is reported as partial.

5. **Completed issues** — table with identifier (linkified to Linear), title, points, completion date. In team mode, group by assignee. Sort by completion date desc within each group.

6. **Active without estimate** — render if `active_no_estimate` is non-empty. Table with identifier, title, state. Header line: `Surfaced but not prompted — these cards are still in flight, so they don't deflate this week's velocity. Estimate them when they're closer to landing.`

7. **Intentionally skipped** — render if `product_rollup_skipped` ∪ `breakdown_parent_skipped` ∪ (Phase 2 `s`-skipped cards) ∪ (`completed_no_estimate` cards left after `q`/quit or `--no-fix`) is non-empty. Single table with a `Reason` column: `top-level product rollup`, `broken into sub-cards`, `user skipped`, `--no-fix`, etc.

8. **Highlights** — top 3 by points; mention any obvious cluster (skill family, monthly-ops,
   multi-card refactor) by recurring keywords in titles, sized in points so the reader can tell
   a theme that absorbed the week from one that took an afternoon.

9. **Estimates added this run** — only if `audit_trail` is non-empty. Lists each
   `(identifier, applied_estimate)` so the user has receipts.

### Phase 4 — Raise a follow-up for every stop (unattended runs)

A scheduled run (the Monday `weekly-output-summary` routine, always `--no-fix`) has nobody
reading the report when it lands, so every half that did not finish becomes a
`shared:raise-followup` post. Keep an outcomes file in the session scratchpad and append one
JSON object per line as each phase ends, e.g.
`{"step": "linear", "status": "blocked", "reason": "LINEAR_API_KEY is not set; GitHub half only"}`.
Steps are `linear` (Phase 1), `github` (Phase 1b) and `report` (Phase 3). Status is `ok`,
`failed` or `blocked`. A degraded half is `blocked` even though the report still renders: a
missing token, `gh` unauthenticated, or per-PR diff fetches that failed (the reason gives their
count; the totals carry `≥`). Then:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/summarize-weekly-output/scripts/outcomes.py \
  --cadence summarize-weekly-output --session "${AINATIVE_SESSION_NAME:-scheduled}" \
  --in "$SCRATCH/weekly-output-outcomes.jsonl" --due "$DUE"
```

`$DUE` is `linear,github,report`, or `linear,report` under `--no-prs`. When this SKILL.md is
read from a checkout rather than loaded as a plugin (the routine reads it by path), `CLAUDE_PLUGIN_ROOT` is unset: run the script from this file's own folder,
`<this folder>/scripts/outcomes.py`. A step with no entry
counts as `stopped`, and a missing outcomes file means nothing was recorded. The script prints
one object per follow-up: pass each to `shared:raise-followup` as is. The key is
`summarize-weekly-output:<step>:stop`. Exit 0 means nothing to raise. Run this phase **before
the turn ends, however it ends**. An interactive run may skip it.

## Output

A single markdown report posted to the conversation. No file writes, no Slack/Notion
side-effects, apart from the follow-ups an unattended run raises in Phase 4. The only writes the skill performs are Linear `estimate` updates the
user explicitly approved in Phase 2.

## Edge cases

- **No issues in window.** Say `No Linear activity for {scope} in {window}.` instead of rendering an empty table — then continue to Phase 1b and report the GitHub half normally. The two halves fail independently: a week of merged PRs against cards closed in a different window is a real week, and exiting on the Linear count alone would report it as no activity at all. Only when *both* halves come back empty does the run print `No activity for {scope} in {window}.` and stop.
- **`updatedAt` filter brings in old-completion noise.** A card touched this week (e.g. comment added) but completed weeks ago belongs in `other`, not `completed_*`. The bucketing is timestamp-driven; trust it.
- **`me` resolves to an unknown user.** The viewer/user lookup errors (GraphQL `viewer` or the MCP fallback) → ask the user to authenticate (`update-config` → `LINEAR_API_KEY`) or pass `--user <email>` explicitly.
- **`--team` returns >250 issues.** Paginate via `pageInfo.endCursor` (GraphQL) or `cursor` (MCP) until exhausted. Under the MCP fallback, if aggregate output exceeds the tool-result token budget, sub-agent the slicing — the GraphQL path doesn't hit this.
- **User estimates a card and immediately quits.** The applied estimate is *not* rolled back — Linear is the source of truth and the write already landed. The audit trail still lists it.
- **A card moves between buckets mid-run** (e.g. someone else marks it Done while we're prompting). Trust the in-memory state from the initial sweep; correctness is "best effort as of sweep time" and acceptable. Document this in the report footer if relevant.
- **Re-running on the same week.** Idempotent: cards estimated in the previous run now show up in `completed_with_estimate`/`active_with_estimate` and are skipped in Phase 2. A second back-to-back run with no new completed-no-estimate cards prints `✅ All cards completed in window have estimates.` and proceeds straight to the report.
- **Product rollup detection edge cases.** If a `PROD-` parent has no children yet (recently created, hasn't been broken down), don't classify it as a rollup — it's a real piece of work. The rule needs *evidence* of children. Prefer false negatives (treat as real work, prompt) over false positives (silently skip something that needed a point).
- **`--week <offset>` is invalid.** Anything outside `[-52, 0]` → reject with a clear error rather than wrapping silently.
- **`gh` is unauthenticated or missing.** Run the Linear half, render the report without the PR sections, and say the GitHub sweep could not run and why. A missing `gh` degrades one section; it never fails the run. Check with `gh auth status`.
- **A `GITHUB_TOKEN` env var is set.** `gh` gives it precedence over its keyring credential, and a fine-grained PAT in that slot reads PRs fine while 403-ing on other endpoints — a selective failure that is easy to misread as a permissions verdict. A 403 here is a credential fault, not an answer: check `gh auth status`, and use `env -u GITHUB_TOKEN -u GH_TOKEN gh api …` as the one-call escape hatch.
- **A person in scope has no GitHub mapping.** Render their Linear row in full, `—` in the PR and code cells, and name them under the table as unmapped. Never `0` — see Phase 1b.
- **A PR touches a vendored directory, lockfile or generated bundle.** The additions figure counts every changed line, so one `package-lock.json` can outweigh a week of hand-written code. Don't silently filter it — report the total, and when a single PR carries an outsized share of the week's additions, name it so the reader can discount it themselves.
- **A PR merged in the window but was opened before it.** It counts: the bucket key is `merged_at`, matching how Phase 1 buckets on `completedAt`. The converse also holds — a PR opened in the window and merged after it lands in next week's report, not this one.
- **Squash-merged PRs.** `additions`/`deletions` come from the PR's own diff against its base, so a squash merge is counted once. Repeated merges of the same branch into successive bases would double-count; this is rare enough to note rather than engineer around.

## Token strategy

**API-first by necessity** (see `/CLAUDE.md` → "Tokens are soft requirements" →
"API-first by necessity"). The repo-wide default is MCP-first; this skill is on the
exempt list because `mcp__linear__list_issues` silently truncates on heavy weeks.
Consistent with `shared:daily-status`, the sibling Linear skill. Prefer the Linear
GraphQL API (`LINEAR_API_KEY`) for both the sweep
(`issues` query) and the estimate writes (`issueUpdate`): it paginates cleanly via
`pageInfo.endCursor`, never silently truncates, and exposes the `updatedAt` /
`completedAt` filters the MCP lacks — exactly what a `--team` sweep needs.

Fall back to the Linear MCP (`list_issues` + `save_issue`) only when
`LINEAR_API_KEY` isn't set (a contributor who hasn't added the key, or a Cowork
sandbox where host env is stripped per PROD-1305). The soft-token rule holds — the
skill still functions on MCP alone — but under MCP a large `--team` result can blow
the tool-result budget, so slice the cached output via a sub-agent as documented in
Phase 1.

GitHub goes through the **`gh` CLI**, the team standard — `gh search prs` and `gh api` for
raw REST. Don't write a custom HTTP client, and don't set `GITHUB_TOKEN`: `gh` keeps its
credential in the OS keyring and that env var shadows it. The per-PR diff fetch is the one
expensive loop in the skill; Phase 1b's three rules (cap at 4, fail closed, check the status
not the empty string) are what keep it honest under a rate limiter that reports plenty of
quota while refusing the request.

## Why this skill exists

Manual proof-of-concept run on 2026-05-08 (Jerome, Sun→Sat ending that day):
**273 points across 52 completed cards, 17 active cards updated in-window with no
estimate.** Team-wide velocity tracking is undermined whenever a card lands without
points — and we land cards without points often enough that any "this week we did N
points" claim is silently understating. This skill closes the gap by fixing the data
*before* reporting on it.

The GitHub half exists because a points total on its own invites one question it cannot
answer — *and how much actually shipped?* Answering it by hand means a `gh search prs` plus a
per-PR diff loop, reconstructed differently every time. A week that reads as 362 points is
also ~118 merged PRs and ~37k added lines concentrated in a single repo; the two numbers
constrain each other, and a reader given only one draws conclusions the other would have
corrected.

## References

- Naming conventions: `/CLAUDE.md` → "Naming conventions"
- Knowledge manifest baseline (`Guide/Index`): `/CLAUDE.md` → "Knowledge manifest"
- `gh` auth, rate-limit and silent-failure rules: `Tools/GitHub` in AI Knowledge
- Linear team / status / project hardcoded values: `plugins/shared/skills/add-linear-issues/SKILL.md`
- Sibling skill: [`eng:analyze-linear-done`](../analyze-linear-done/SKILL.md) — composition of completed work rolled up to parent initiatives, rather than the weekly totals
- Spec for Phase 2 fix-then-report: [PROD-1661](https://linear.app/aura-health/issue/PROD-1661)
- Spec for the GitHub half and the rename: [PROD-6173](https://linear.app/aura-health/issue/PROD-6173)
