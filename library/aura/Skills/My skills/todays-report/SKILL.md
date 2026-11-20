---
name: todays-report
description: Build Ashish's "Today's Report" — a short, sectioned list of what he did today — from his GitHub PRs, reviews, local commits and Linear cards and comments, and show it in chat. Use when the user says "create for today", "make for today", "can you make for today", "create today's report", "today's report", "daily report", "EOD update", or, right after a report, "update it", "make all points short", "show me the full report again".
---

# Today's Report

Builds the user's end-of-day report from what actually happened today, in the exact
shape he settled on over many rounds of edits (Oct 2026). **Show it in chat only.**
Never post it to Slack, Linear, Notion or anywhere else.

## Window

- "Today" is the user's day in **IST (UTC+5:30)**: from 18:30Z the previous day to now.
- **"update it"** means: gather again from the time of the last gather in this
  session (the `WINDOW now=` line), merge anything new into the last report, and
  show the full report again.
- Work done **after the previous report but before today** (e.g. last evening): don't
  add it silently. Leave it out, say what it is in one line under the report, and ask
  whether to add it.

## 1. Gather

Run both in parallel.

**GitHub and local git** (≈30 s):

```bash
bash ~/.claude/skills/todays-report/scripts/gather.sh                # today
bash ~/.claude/skills/todays-report/scripts/gather.sh --since <ISO>  # "update it"
```

It prints: my PRs touched in the window (state, draft, created, closed), per PR my
non-merge commits, my comments (QA notes show up here), BugBot runs, reviews, base
branch and who merged it; others' PRs I reviewed or commented on; and my local
commits across `~/Work` repos and worktrees, in IST.

**Linear** (MCP tools), with `since` = the window start:

- `list_issues` with `assignee: "me"`, `updatedAt: <since>`, `limit: 100`, fields
  `id, title, status, statusType, parentId, createdAt, updatedAt, completedAt, labels`
- `list_issues` with `creator: "me"`, `createdAt: <since>`, fields `id, title, status,
  assignee, parentId, createdAt, labels`
- `list_comments` (orderBy `createdAt`, limit 3) only on cards that need it to say
  what was done: support and research cards, and cards with no PR behind them. Keep
  only **my own** comments from the window.

## 2. Read the signals

| Signal | Means |
|---|---|
| My PR created in the window | PR raised (draft or not) |
| `merged_by` me, base `master`/`main` | Merged |
| Merged into another branch (stacked base) | Combined into a bigger PR, **not** merged |
| Many small PRs closed together + new grouped PRs | Combined into N PRs |
| Draft → not draft in the window | Moved to review |
| My comment starting "QA" / "Sim QA" / "Real delete…" | QA done on that PR |
| `bugbot run` + my commits after a cursor[bot] review | Fixed the BugBot findings |
| `chore(ios): release x.y.z` PRs or version-bump commits | Cut iOS builds |
| "Review X's PR" Linear cards created Done | Code reviews of X's PRs |
| "QA: …" Linear cards I created, Done | QA done |
| Cards I created in Todo/Backlog | Raised cards (or planned the work, if it is a parent split into parts) |
| My comment on a support card replying to support, or closing it with a summary | Support reply / closed with summary |

**Not work — skip:** many cards updated in the same second (cycle rollover, release
sweeps), linear-code[bot] linkbacks, cursor[bot] summaries, merge commits, "Merge
main into …", other people's comments on my cards, and cards assigned to others.

If the user says he did something you can't see (QA, research), add only what you can
confirm and ask which other items to include. Never invent.

## 3. Write it

```
**Today's Report**

**<Section>**

* <one short line>
* <one short line>

**<Section>**

* <one short line>
```

Pick sections from the day's content, in roughly this order: the main project of the
day (e.g. "4.0 for Every iOS User", "Light Theme (4.0)"), Merged, PRs Raised, PRs
Moved to Review, Bug Fixes / Live Fixes (PRs Raised), Security, Releases and QA, QA,
Code Reviews, Aurie Voice, Research and Support, Working On. Drop empty sections.

## Rules — each one came from the user correcting a draft

- **Title is "Today's Report".** Never "Daily Update" or "Daily Summary".
- **Short by default.** Every bullet is one short line, about 6–12 words. The user
  asked "make all points short" on almost every report, so start there.
- **One point per bullet**, no multi-sentence bullets, no sub-bullets.
- **No ticket IDs, PR numbers, branch names or counts** ("13 screens" is fine as a
  description; "shipped 11 issues" is not).
- **Plain words**, what changed for the user: "A tap now brings back the hidden live
  controls", not the commit subject.
- **Only today.** Don't repeat yesterday's work; check timestamps before calling
  something QA or a merge. Yesterday's QA findings fixed today = "Fixed issues from
  yesterday's QA", not "QA".
- **Group related work** (all of one reviewer's PRs on one line; a set of gap PRs as
  one line plus one "covered: …" line).
- **"Working On"**: include it when the user asks, or when there is clear in-progress
  work today with no PR yet (a card started, local commits only). Take it from
  in-progress cards and today's local branches, and say where it came from.
- **When asked "when did we do X"**, answer with IST times from the timestamps.
- **When asked which repo**, give `owner/repo` and a link.
- **Show only.** No Slack draft unless the user asks.

## Example (2026-10-28, after the user's edits)

```
**Today's Report**

**4.0 for Every iOS User**

* Raised the PR that gives every iOS user the 4.0 design, and QA'd it
* Planned removing the old design in 10 parts, with a QA card for each
* Listed what the old design has that 4.0 doesn't, and built the missing pieces
* Combined them into 4 PRs: live and coaching, Home and content, library and Me, and Search, Timer and account
* Fixed the BugBot, VoiceOver and translation issues on those PRs

**Live Fixes (PRs Raised)**

* A tap now brings back the hidden live and replay controls on iOS
* iPhone live donations are no longer recorded twice

**Code Reviews**

* Reviewed Farhan's Aurie upsell, cancel flow and paywall return PRs
```

## Gotchas

- Loops that use `set -- $r` must run under `bash -c`; zsh doesn't word-split.
- `gh search prs` has no `--org`; use `--owner AuraHealth`.
- In `sed`, a `|` in the replacement breaks a `s|…|…|` expression; use `awk`.
- A merge into a stacked base branch shows `merged=true`, so check `base=` before
  writing "Merged".

## After writing

Show the report and stop. If the user corrects the style, apply the fix and add it
to the Rules above so the next run starts from it.
