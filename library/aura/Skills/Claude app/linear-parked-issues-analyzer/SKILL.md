---
name: linear-parked-issues-analyzer
description: Analyze all of Ashish's parked Linear issues (Todo, In Progress, In Review) and produce an overview report that categorizes each issue by product (Aura mobile, Auri, userWeb, coachWeb, backend/Support Service, Aurabot), layer (frontend/backend/database/infra), fix type (code fix, DB debugging, config, investigation), and gives a concrete suggested next action. Output is a summary table grouped by category plus per-issue detail cards. Use this skill whenever Ashish asks for a "parked issues report", "overview of my open tickets", "what's on my plate", "analyze my Linear backlog", "summary of my todo/in-progress issues", or any similar request to get a categorized view of his active Linear work. Always trigger this skill for these requests — do NOT just list raw Linear issues.
---

# Linear Parked Issues Analyzer

Generates an analytical overview of Ashish's active Linear workload — every issue assigned to him sitting in **Todo**, **In Progress**, or **In Review** — categorized by product, layer, fix type, with a suggested next action per issue.

## When to use

Triggers: "parked issues", "analyze my backlog", "overview of my tickets", "what's on my plate", "categorize my open issues", "Linear summary", "open issue report".

This is different from the `daily-work-report` skill — that one summarizes *what got done today*. This one analyzes *what's still open and what to do about it*.

## Workflow

### Step 1: Pull assigned issues

Use the Linear MCP `list_issues` tool. Filter by:
- `assignee`: current user (Ashish)
- `state`: states of type `unstarted` (Todo) and `started` (which covers In Progress, Eng In Progress, In Review, Blocked, and any custom started statuses). Make two calls: one with `state=unstarted`, one with `state=started`. Exclude `completed`, `canceled`, `backlog`.
- Set a reasonable limit (e.g. 50). If results are truncated, paginate.

If the Linear tool isn't loaded yet, call `tool_search` for "linear issues" first.

Fetch enough fields to categorize: `title`, `description`, `status` (exact name), `team`, `labels`, `priority`, `updatedAt`, `url`, `identifier`.

**Important**: surface the **exact `status` name** from Linear in the output (e.g. "Eng In Progress", "In Progress", "In Review", "Blocked", "Todo") — do not collapse them. Ashish's workspace distinguishes "Eng In Progress" from "In Progress" and the distinction matters.

### Step 2: Categorize each issue

For each issue, derive four classifications from **title + description keywords**. Use the heuristics in `references/categorization.md` — read that file for the keyword maps. The four dimensions:

1. **Product** — Aura mobile, Auri, userWeb, coachWeb, backend (Support Service / API), Aurabot, internal tooling, or "unclear"
2. **Layer** — frontend, backend, database, infra/devops, cross-cutting, or "unclear"
3. **Fix type** — code fix, DB debugging, config/env change, third-party integration, investigation/repro needed, design/spec needed
4. **Suggested next action** — one concrete sentence (e.g. "Check Stripe webhook logs for failed checkout sessions on 2026-05-20", "Repro on TestFlight 3.19.108 with promo trialDays payload", "Query subscriptions table for users with `trialDays=null` after Apr 2026")

The action should be specific to the issue's actual content — not generic ("debug the issue"). Pull signals from the description: error messages, ticket IDs referenced, dates, file paths, API endpoints.

### Step 3: Render the output

Output structure, in this exact order:

```
# Parked Issues Overview — N issues

## Summary table

| ID | Title | Status | Product | Layer | Fix type | Priority |
|----|-------|--------|---------|-------|----------|----------|
| ENG-529 | Stripe Checkout deeplink | In Progress | userWeb | frontend | code fix | High |
| PROD-2106 | Coach dashboard shows only 2 of 4 paid sessions | Eng In Progress | coachWeb + backend | database | DB debugging | Medium |
| ENGSUP-57 | JuneB unable to authorize zoom | Blocked | coachWeb + backend | third-party | third-party integration | Medium |
...

## Breakdown
- **By status**: Eng In Progress (5), In Progress (8), In Review (3), Blocked (1), Todo (8)
- **By product**: Aura mobile (4), userWeb (3), backend (2), ...
- **By layer**: frontend (5), backend (3), database (1), ...
- **By fix type**: code fix (6), investigation (2), DB debugging (1), ...

## Detail cards

### ENG-529 — Stripe Checkout deeplink integration
- **Status**: In Progress
- **Product**: userWeb
- **Layer**: frontend + backend (cross-cutting)
- **Fix type**: third-party integration
- **Last update**: 2026-05-21
- **Why this categorization**: Title mentions "Stripe Checkout"; description references userWeb subscribe route and mobile deeplink return.
- **Suggested next action**: Verify the success/cancel URL handlers return the user to the correct mobile deeplink; check ENG-531/532/533 sub-issues for outstanding pieces.

### [next issue...]
```

Keep cards concise — 5–7 lines each. The "Why this categorization" line is important so Ashish can sanity-check the heuristic on a glance.

### Step 4: Flag ambiguous cases

If you can't confidently categorize an issue (too vague a title, no description), put it under a final **"⚠️ Needs more context"** section with a one-line note about what's missing.

## Output preferences

Ashish prefers plain, minimal output. No emoji except the one warning marker above. No filler preamble. Lead with the table.
