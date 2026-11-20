---
name: eng-handoff
description: This skill should be used only when the user explicitly asks to prepare an engineering handoff for a Linear issue. Spawns a product/design lead and engineering lead agent team to analyze the issue and produce a complete, implementation-ready breakdown. Trigger when the user says things like "prepare eng handoff", "engineering handoff for AUR-123", "run eng handoff on this ticket", "handoff this to engineering", "breakdown this linear card for eng", "get this linear issue ready for engineering", or "do an eng handoff". Do NOT use for a bare Linear issue ID or URL without an explicit handoff request.
metadata:
  version: "3.0.1"
---

# Engineering Handoff Skill

## Invocation Guard

This is a manual, agent-spawning workflow. Treat direct slash-command invocation of
`eng-handoff` as explicit. Otherwise, before fetching Linear data or spawning agents,
verify the user's latest message explicitly asks for an engineering handoff. If the
skill was selected from a bare Linear issue, URL, or ambiguous planning request, stop
and ask whether to run the handoff.

You are the **Team Lead** orchestrating an engineering handoff for a Linear issue. You coordinate a Product & Design Lead and an Engineering Lead to produce a complete, implementation-ready breakdown — no implementation, just planning.

**Input:** $ARGUMENTS (Linear issue ID or URL)

---

## Phase 1: Fetch the Linear Issue

Extract the issue identifier from $ARGUMENTS (e.g. `AUR-123` from a URL or direct ID).

Fetch full details via Linear MCP:
```
get_issue(id: "AUR-123", includeRelations: true)
list_issues(parentId: "AUR-123")   ← fetch any existing sub-issues
```

Collect everything:
- Title, description, labels, priority, state, assignee
- All comments (author + content + timestamps)
- Linked/child issues
- All external URLs (Figma links, docs, specs, Notion pages, etc.)
- All image attachments / screenshots on the issue

**Front-end detection:** A task requires front-end changes if it has a Figma link OR image screenshots attached. Note this explicitly — it will determine how agents approach the analysis.

Show the user a brief summary: issue title, state, comment count, any Figma/external links found, and the number of screenshots attached.

---

## Phase 2: Read Agent Spawn Prompts

Read these two files — you will use them verbatim as the spawn prompts for your teammates:
- [Product & Design Lead prompt](./references/product-design-lead.md)
- [Engineering Lead prompt](./references/engineering-lead.md)

---

## Phase 3: Spawn Agent Team

Tell Claude to create an agent team with two teammates. Use the prompts from Phase 2, appending the issue-specific context below.

**Note:** Agent teams require `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`. It is configured in project settings. If spawning fails, ask the user to verify the setting in `.claude/settings.local.json`.

### Teammate 1 — Product & Design Lead

Spawn with the full content of `product-design-lead.md`, then append:

```
---
## Your Assigned Linear Issue

**ID:** {{issue_id}}
**Title:** {{issue_title}}
**State:** {{issue_state}}

**Description:**
{{issue_description}}

**Comments:**
{{issue_comments_formatted}}

**External Links Found:**
{{external_links}}

**Screenshots / Image Attachments:**
{{screenshots}}

**Existing Sub-issues (if any):**
{{existing_sub_issues}}

**Front-end task:** {{is_frontend}} (true if Figma link or screenshots are present)

---

Begin your analysis now. When your requirements summary is complete, send it directly to the Engineering Lead teammate via message.
```

Substitute all `{{...}}` tokens with real values from Phase 1. Do not leave any placeholder tokens.

### Teammate 2 — Engineering Lead

Spawn with the full content of `engineering-lead.md`, then append:

```
---
## Context

**Linear Issue:** {{issue_id}} — {{issue_title}}

Wait for the Product & Design Lead to send you the requirements summary before starting your analysis. Do not begin repository exploration until you have received it.

## Available Repositories

The ERP repos are cloned as sibling folders in the home directory (`C:SERSHARSHITRP-*`), named per the ERP naming rule. Use the signal → repo mapping below:

| Signal | Repo(s) |
|--------|---------|
| mobile app, Android, iOS | `ERP-APP-SERVICE` |
| web, UI, dashboard, screen, form, report page | `ERP-WEB-SERVICE` |
| API, database, migration, auth, inventory, accounting, invoice, payroll, jobs, integrations | `ERP-BACKEND-SERVICE` |
| Claude skills, rules | `ERP-CLAUDE-SKILLS` |

When your complete breakdown is ready, send it to the Team Lead.
```

---

## Phase 4: Monitor and Coordinate

Your role while teammates work:

1. **Let the Product Lead and Engineering Lead communicate directly** — they will message each other for requirements and Q&A. Do not intercede unless asked.
2. **Watch for the Engineering Lead going idle** — if they stop unexpectedly before sending you a breakdown, check in and unblock them.
3. **Answer repo-mapping questions** if the Engineering Lead asks you (the Team Lead) which repo to use.
4. **Wait** until the Engineering Lead sends you the final breakdown.

If either teammate encounters a blocking problem they cannot resolve (e.g. a Figma link requires auth, a repo is missing), surface it to the user immediately.

---

## Phase 5: Synthesize the Final Output

Once the Engineering Lead delivers the breakdown, produce the complete handoff document in this format:

---

```markdown
# Engineering Handoff: {{issue_title}} ({{issue_id}})

## Overview
[2–3 sentence summary of what is being built and why]

---

## Assumptions & Decisions (Product Lead)
> The following decisions were made during analysis where requirements were ambiguous.
> These require human review before engineering begins.

- **[Topic]**: [Decision made and reasoning]
- ...

---

## Sub-task Breakdown

### 1. [Sub-task Title] · `RepoName`

**What**: [Clear description of what changes in this repo and why]

**Checklist:**
- [ ] [Specific actionable task]
- [ ] [Another specific task]
- [ ] [Add/update tests for X]
- [ ] [Update types or interfaces if needed]

**UI States** *(omit if not a UI change)*:
- **Default**: [what the user sees normally]
- **Loading**: [what shows while data loads]
- **Error**: [what shows on failure]
- **Empty**: [what shows with no data]
- [Any other states from the design]

**Figma References** *(omit if none)*:
- [Frame / section name] → [Figma link or description of where to find it]

**Dependencies**: [Other sub-tasks that must complete first — or "none"]

---

### 2. [Sub-task Title] · `RepoName`
...

---

## Suggested Implementation Order
1. [Sub-task N] — no dependencies
2. [Sub-task M] — depends on N
3. [Sub-task P] — can run in parallel with M

---

## Open Questions for Human Review
[List anything that could not be resolved by either agent and needs a human decision]
```

---

## Phase 6: Create Linear Sub-issues (Optional)

Ask the user: "Would you like me to create these as Linear sub-issues under {{issue_id}}?"

If yes:
- Use `save_issue` for each sub-task with `parentId: "{{issue_id}}"`
- Title: the sub-task title
- Description: the full checklist, UI states, and Figma references in markdown
- Copy labels from the parent issue where appropriate
