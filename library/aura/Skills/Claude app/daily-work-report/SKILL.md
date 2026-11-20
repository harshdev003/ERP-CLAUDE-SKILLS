---
name: daily-work-report
description: Generate Ashish's daily work report from Linear by pulling issues updated in the last day, clubbing related sub-issues into single logical workstreams, and formatting as a short "Done / Working on" summary suitable for Slack standup or personal reference. Use this skill whenever Ashish asks for a "daily report", "today's report", "EOD update", "standup", "today task report", "what did I do today from Linear", or any similar request to summarize his day's Linear activity. Always trigger this skill for these requests — do NOT just dump a raw list of Linear issues.
---

# Daily Work Report

Generate Ashish's daily work summary from Linear in a clubbed, narrative format — not a raw issue dump.

## Step 1: Pull Linear issues

Call `Linear:list_issues` with these parameters:

- `assignee`: `"me"`
- `updatedAt`: `"-P1D"` (last 24 hours)
- `orderBy`: `"updatedAt"`
- `limit`: `50`

If Ashish asks for a different range ("this week", "yesterday"), adjust `updatedAt` accordingly (e.g., `-P7D` for a week).

## Step 2: Categorize issues

Sort each returned issue into one of two buckets based on `statusType`:

- **Done** → `statusType: "completed"` (status names: "Done")
- **Working on / pending** → everything else: `"started"` (In Progress, Blocked, To Be Released), `"unstarted"` (Todo), etc.

Ignore `"canceled"` and `"duplicate"` statuses unless they're notable (e.g., a duplicate that consolidated work).

## Step 3: Club related issues

This is the most important step. Do NOT list every ticket separately. Group issues that share a logical workstream into a single bullet:

**Clubbing signals:**
- Same `parentId` → almost always clubbable (e.g., ENG-552, ENG-553, ENG-555 all under PROD-1829 → "Source App across web surfaces")
- Same project + closely related titles (e.g., multiple coaching refund support tickets → "batch of coaching subscription support tickets")
- A parent issue + its sub-issues completed together → describe as one shipped feature
- Investigation + fix issue pair (e.g., ENG-561 investigate + ENG-562 monitor) → "investigated and fixed X, monitoring in place"

**Naming clubbed groups:**
- Use plain-English descriptions of what was shipped/worked on, not ticket IDs
- Mention the surfaces or systems touched (e.g., "Aura Web, CRM Web, and CoachWeb")
- Add brief outcome context in parentheses if useful ("parent epic closed", "Phase 1 fix shipped")

## Step 4: Format the report

Use exactly this structure:

```
## Daily Summary — <Month Day, Year>

**Done:**
- <Clubbed workstream 1 with brief outcome>
- <Clubbed workstream 2 with brief outcome>
- <Clubbed workstream 3 with brief outcome>
- <etc.>

**Working on / pending:**
- <Open workstream 1 with status if notable>
- <Open workstream 2>
- <etc.>
```

## Formatting rules

- **NO token numbers, story points, estimates, or "X issues" counts** — Ashish explicitly does not want these. Never write things like "shipped 11 issues" or "5 points completed".
- **NO ticket IDs in the summary bullets** — ENG-561, PROD-1829, etc. should not appear. The summary is for humans, not Linear.
- Keep each bullet to a single line where possible — short, scannable, plain English.
- Use backticks for technical identifiers that are part of the work itself (e.g., `Source App` property, `trialDays` flag) — these are fine; they describe what was built, not which ticket tracked it.
- Lead each bullet with the verb/outcome ("Shipped...", "Investigated and fixed...", "Closed...", "Cleared a batch of...").
- If a high-priority item is queued or blocked, mention priority briefly ("high priority") so it stands out.

## Example output

```
## Daily Summary — May 19, 2026

**Done:**
- Shipped `Source App` analytics property across web surfaces — Aura Web, CRM Web, and CoachWeb (parent epic closed)
- Investigated and fixed the Bugsnag error flood on UserWeb — Phase 1 fix shipped and monitoring in place
- Closed the MentorcCares `trialDays` promo bug (6-month free trial now applies correctly)
- Cleared a batch of coaching subscription support tickets — cancellation/refund handling, session app loading issue, and refund for canceled account

**Working on / pending:**
- `Source App` for Aura mobile — in "To Be Released"
- Coach unable to open track types on home page (high priority)
- LiveKit demo update with latest main (high priority)
- Stripe Refund button for Aura Front sidebar
- Aura new-user price increase rollout (mobile + web)
```

## After generating

Offer to draft a Slack message version for standup if Ashish hasn't already asked. Do not draft it unprompted — just offer.
