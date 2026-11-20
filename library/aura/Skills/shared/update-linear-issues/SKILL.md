---
name: update-linear-issues
description: >
  Updates one or more EXISTING Linear issues from the conversation context or user input —
  title, description, status, assignee, priority, project, cycle, labels, estimate, or parent.
  Trigger on any change verb (update, edit, change, set, rename, move, reassign, retitle,
  relabel, re-estimate) applied to an existing issue (an identifier like PROD-1234, a pasted
  linear.app URL, or "this card" already in the conversation): "update PROD-1234", "edit this
  Linear card", "rename PROD-2253", "move PROD-9 to the current cycle", "set PROD-7 to In
  Review", "reassign this card to me", "bump PROD-3 to High priority", "add the Bugs label to
  PROD-5", "re-parent PROD-6 under PROD-1907". ALSO trigger on finishing or closing one —
  "mark PROD-1234 as done", "mark this done", "close this card", "complete PROD-7", "wrap up
  PROD-9", "this is done", "ship it" — because completing an issue runs the Definition-of-Done
  guard. To CREATE an issue use add-linear-issues. Also triggers on "finish this card", "fix
  the title on this ticket".

metadata:
  version: "3.3.2"
knowledge:
  reads:
    - Guide/Index        # baseline routing aid (universal)
    - Compliance/Privacy # PII rule for end-user data written into a Linear description/comment (external surface)
    - Procedures/Engineering/Estimation # canonical FAST story-point scale (pre-fetched for the Estimate step)
    - Tools/Linear       # canonical team list (name, key, UUID) — re-verify here if teams change
---

# Update Linear Issues

Edits existing Linear issues. Resolves the target issue, reads its current state, computes the
requested changes, confirms the before/after, and applies them in one update. Handles title,
description, status, assignee, priority, project, cycle, milestone, labels, estimate, and
parent.

**Input:** An issue reference (identifier, URL, or "this card" from context) plus the changes
to make — from conversation context or a direct user request.

> **Creating vs. updating.** This skill only *edits existing* issues. If the user wants to
> create a brand-new issue, hand off to **`add-linear-issues`**.

---

## How this skill talks to Linear

This skill uses the **Linear GraphQL API** directly via a bundled helper script — not the
Linear MCP connector — so it works in every session, including Cowork / local-agent mode
where MCP tools aren't available. The script is a self-contained copy of the
`add-linear-issues` helper, extended with `get-issue` and `update-issue`.

All Linear calls go through:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/update-linear-issues/scripts/linear_api.py <subcommand> [flags]
```

| Need | Subcommand | Notes |
|------|-----------|-------|
| Current user | `whoami` | For "assign to me" |
| Active cycle for a team | `current-cycle --team <id>` | For "move to current cycle" |
| Project milestones | `milestones --project <id>` | For "set milestone" |
| Team workflow states | `states --team <id>` | Resolve "Done" (or any status) on a non-Product/Design team |
| Find an issue by name | `find-issue --query <ident\|text>` | When the user didn't give an identifier |
| **Read an issue's current state** | **`get-issue --id <id\|ident>`** | The before-state for the diff |
| **Apply changes** | **`update-issue --input-file <f.json>`** | The edit (`IssueUpdateInput`) |
| Add a follow-up comment | `comment --issue <id> --body-file <f>` | Optional change note |
| Attach a URL | `attach-url --issue <id> --url <u>` | Optional |

Every subcommand prints **JSON to stdout**. The helper reads `LINEAR_API_KEY` from the
environment and never prints it.

**Authentication — API-first by necessity** (see `/CLAUDE.md` → "Tokens are soft requirements"
→ "API-first by necessity"). This skill deliberately deviates from the repo-wide MCP-first
default because Linear MCP isn't reachable inside Cowork / local-agent sandboxes
([PROD-1305](https://linear.app/aura-health/issue/PROD-1305)) — the bundled `linear_api.py`
script gives a single execution path that works in host Claude Code and Cowork
([PROD-2216](https://linear.app/aura-health/issue/PROD-2216)). The order:

1. **API first.** With `LINEAR_API_KEY` set (in `~/.claude/settings.json` env), the script is
   the preferred path for every subcommand below.
2. **Fall back to MCP on *any* script failure.** On error the script prints a JSON object with
   an `"error"` field and `"fallback":"mcp"`, then exits 1. Error types: `no_key` (key unset),
   `api_error` (non-2xx — e.g. HTTP 401 for an invalid/expired key), `graphql_error`, and
   `network_error`. On **any** of these, **warn the user** (one line), then fall back to the
   Linear **MCP** tools if a connector is available (`get_issue` / `list_issues`,
   `save_issue`/`update_issue`, `get_user`, `list_cycles`, `list_milestones`). Example:
   > ⚠️ Linear API call failed (`api_error`: HTTP 401 — check `LINEAR_API_KEY`). Falling back to the Linear MCP connector.
3. **Ask the user only if neither works** — to add/fix `LINEAR_API_KEY` via the `update-config`
   skill and restart Claude Code.

> **Write safety on `network_error`.** A network/timeout error on the `update-issue` *mutation*
> is ambiguous — the write may have landed before the connection dropped (the script flags this
> with `"verify_before_retry":true`). Before retrying, re-run `get-issue` and check whether the
> change already took effect, to avoid applying it twice (mostly harmless for idempotent fields,
> but it matters for `addedLabelIds` / comments). Read subcommands are always safe to retry.

---

## Hardcoded Workspace Values

Use these directly — no runtime lookup needed. All IDs below were **verified against the
Linear API on 2026-06-19**. To re-verify after a workspace change, query `teams`,
`projects`, `issueLabels`, `workflowStates`, or `users` directly (or look one up with the
helper, e.g. `find-issue`). The canonical team list is maintained in AI Knowledge under
**Tools/Linear** — re-verify there if teams are added or removed.

### Teams

| Team | Key | ID |
|------|-----|----|
| Product/Design | PROD | `7e95fa4a-7510-447d-adc7-c663612e70c6` |
| Engineering | ENG | `ffcc6973-8ebf-4f92-b3b2-5568decbbae8` |
| Backend | ENGBACK | `e25556d5-9f7f-4758-8ac3-4505102aa550` |
| Mobile | ENGMOB | `5497338e-312b-4ddf-ac5f-3339399c956d` |
| Web | ENGWEB | `577a9e8b-f2bc-46e1-b170-30aa13e71ee8` |
| Desktop | ENGDESK | `a2e85af2-36ce-48a8-8a57-31fcaef224c1` |
| Prompt | ENGPROM | `8b9add50-f86d-4719-8567-0afc8a063fbb` |
| QA | QA | `e083e29b-c7c0-4f88-9f85-d04f722cd4c2` |
| Data/DS | DATA | `aa5190e7-50c9-4de8-8dd6-a252fd5b78c7` |
| Support | ENGSUP | `762a1dc5-c384-4d40-a23d-040c225548ed` |
| Customer Success | CUS | `9b5124d8-7484-4a32-b4d7-6ae3e5838212` |
| Webflow | WEBFLOW | `0e07a512-cf23-4ff6-91b0-904aab75d453` |

### Statuses

| Status | ID | Use when |
|--------|-----|----------|
| Todo | `85f68398-a8b5-4df6-8491-0cfc32279769` | No PR exists yet |
| Eng In Progress | `53c1383f-fb0e-485d-b31a-f730a4b9f39f` | PR is open as a draft |
| In Review | `56f194ce-ab02-48a5-b6e6-75f095ddd6f2` | PR is marked ready for review (still a `started` state, not completed) |
| Done | `530cf975-c1e9-4cf8-95fa-1c0a296e2ecc` | PR is merged — the `completed` state (runs the DoD guard, Step 3a) |

Move the card in the same step as the command that moves its PR: `gh pr ready` → In Review,
merge → Done. The Engineering team carries a release gate Product/Design does not — a merged
`ENG-*` card waits in `To Be Released` and reaches Done once a build carries it. The full
per-team mapping lives in `add-linear-issues` → Step 4. Before judging a card's state from its PRs, list every PR that mentions it (`gh search prs <ID> --owner AuraHealth`), not only the attached ones: a mobile card often has one PR per branch plus PRs in other repos (ENGMOB-3029 looked stalled while its merged fixes were unattached). Never move a card to Canceled or Duplicate while an open PR is linked to it, even only through its branch name: the integration closes that PR. Re-link the PR to the surviving card first, or wait until it merges.

> **These state IDs belong to the Product/Design team.** Workflow states are
> team-scoped in Linear, so passing one as `stateId` for an issue on a **different** team
> errors. Before setting a status, confirm the issue's team via `get-issue` (the `team` field).
> If the issue is on the Engineering team (or any non-Product/Design team), these IDs are
> wrong — resolve that team's own state IDs with `states --team <team-id>` (pick the
> `type: "completed"` state for Done), or skip the status change and tell the user.

### Projects

| Project | ID |
|---------|-----|
| AI Native | `c110e9d4-1346-49b8-b7e2-44d79ba5bab8` |
| Aurie AI - Mobile | `50b657cd-f184-4068-aec5-a1dbcac49d22` |
| Aurie AI - Desktop/Web | `fdfd427e-d3aa-44f3-b27d-4736a6226776` |
| Aura - Core App | `a13d07fd-f427-4f35-a5af-fba68b015f95` |
| Aura - Web | `4ee6841a-f5a7-496e-b546-992cc4ad7cd7` |
| Aura - Content | `44608cb6-30f7-4747-9686-8a74ccaf1dd5` |

### Key Users

| User | ID |
|------|-----|
| Alex (Designer) | `16d710a5-8040-4975-91c6-3e15bc702957` |

### Labels

**CRITICAL: Only use labels from this table. Never use archived labels. Never create new labels.**

| Label | ID |
|-------|-----|
| AI First | `81f82e51-0528-4f9e-8f97-0c8dca59d873` |
| Tech Integrations | `19a6e93d-26c7-4b87-91bc-b01c24f271c1` |
| Aura Integration | `d1f2ca52-c112-4ef9-b0cf-1ca733528f45` |
| Aura App Improvements | `71e0ebae-af23-44e6-8c83-849149315086` |
| Core UX & Activation | `bb0f0d28-e681-4664-a0d6-fc8fbcb739c5` |
| Engagement & Retention | `7f9b28b4-8ef3-43bd-923e-fbd8c048cc18` |
| Character & Emotional Bonding | `7509cd32-bd11-4d06-ad14-3643266eab7f` |
| Coaching & Processing Efficacy | `a41abe99-1963-4737-95f1-78c938b932d2` |
| Growth & Monetization | `b68d6001-ce21-4af9-80b2-0b6ebf1fe567` |
| Marketing & Distribution | `392eca9e-8f7a-491d-9286-f2936a9ce387` |
| Platform Expansion | `b30cef0d-de57-4dc1-bba0-f768b8802379` |
| Infrastructure, Reliability & Performance | `6c665b54-87f1-46f6-8016-00575c441d17` |
| Platform Reliability & Performance | `0c7c3393-1151-4488-b0c4-cfdecb41dd7d` |
| Analytics & Data | `db17094a-b8e8-4afb-83d1-dc55c652c2d3` |
| Data Design | `39f17946-9930-4ca1-9fa4-f577b4fa318a` |
| Experimentation | `62e6ccc0-5b1e-4007-8173-5bed370f3a46` |
| Tech Quality | `2511d2ae-b39c-4d2b-bc7c-14ba33d8a1b7` |
| Security & Safety | `42d2d1b3-7a51-4fa4-bb60-1c29f2fce43b` |
| Bugs | `0cdc116b-e9a0-46ee-8133-3481c2b64376` |
| Eng Blocker | `fb7cf70f-a207-428d-a718-7558fcafd3de` |
| User Research | `4a3d82af-dbfc-4531-890f-16034d588df8` |
| Support | `d9aab87b-749a-4991-ac29-1bf0cebf5da3` |

### Priority values

`1` Urgent · `2` High · `3` Medium · `4` Low · `0` No priority

### Estimate scale

**Read the scale from AI Knowledge — this skill carries no copy of it.** The canonical FAST
story-point rubric lives at [`Procedures/Engineering/Estimation`](https://app.notion.com/p/Estimation-38075e23af2081ca86a8ee1110b1021c)
and is pre-fetched via this skill's `knowledge.reads`. Use that page's scale and gates when
proposing or re-estimating a value. If it wasn't pre-fetched (token/MCP unavailable), pull it with
`retrieve-knowledge path=Procedures/Engineering/Estimation`. **If the page can't be read at all,
ask the user for the number** rather than sizing from a remembered scale.

**The release floor binds whether or not the page loads.** Work that ships is **3 or more**; 1 and
2 are only for work that ships nothing. A one-line change that ships is a 3, and so is a test-only
PR — the points buy the review-and-release cycle, not the diff. Every change under `plugins/**` in
this repo ships (the version-bump gate re-ships the plugin into the install cache), as does any
mobile-app, `AuraInfra`, or Intelligence Service change. When re-estimating a card that has a
merged PR, check the floor before anything else.

**The gate above the floor** is *does it need another team?* (the 5↔8 line). When re-estimating,
the most common miss after the floor is sizing a multi-file refactor/move by its net diff instead
of its blast radius.

---

## Step 1: Identify the target issue

Resolve exactly which issue to edit before touching anything.

1. **User gave an identifier** (`PROD-1234`) or a **linear.app URL** → extract the identifier
   (the URL path contains it, e.g. `.../issue/PROD-1234/...`). Pass it straight to `get-issue`
   in Step 2 (it accepts the `PROD-123` form for reads).
2. **User said "this card" / "the ticket"** → use the issue most recently discussed in the
   conversation (one this session created, fetched, or commented on). If there's genuine
   ambiguity, ask which one.
3. **User named the issue by title** → resolve it:
   ```bash
   python3 ${CLAUDE_PLUGIN_ROOT}/skills/update-linear-issues/scripts/linear_api.py find-issue --query "<title text>"
   ```
   - Single clear match → use it.
   - Multiple matches → list them (identifier + title) and ask which one.
   - No match → ask for the identifier or a more distinctive title substring.

**Multiple issues in one request** ("set PROD-1 and PROD-2 to In Review") → process each one
through Steps 2–5 independently.

---

## Step 2: Load the current state

Always read the issue before changing it — this gives you the issue's **UUID** (required for
the update mutation), its **team** (required to validate any status change), and the
before-values for the confirmation diff:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/update-linear-issues/scripts/linear_api.py get-issue --id "<PROD-123 or UUID>"
```

Returns the flattened issue: `id`, `identifier`, `title`, `description`, `url`, `priority`,
`estimate`, `state {id,name,type}`, `assignee {id,name}`, `team {id,name}`,
`project {id,name}`, `cycle {id,name,number}`, `projectMilestone {id,name}`,
`parent {id,identifier,title}`, `labels [{id,name}]`.

If it returns `{"error":"not_found"}`, the identifier is wrong — go back to Step 1 (try
`find-issue` or ask the user). Keep the returned **`id` (UUID)** — `update-issue` needs the
UUID, not the `PROD-123` identifier.

**Parent status change → read the children too.** `get-issue` returns `parent` but **not**
`children`, so read each child with its own `get-issue`. See Edge Cases → *Reopening a parent*.

---

## Step 3: Determine the changes

Translate the user's request into `IssueUpdateInput` fields, using the hardcoded tables to map
human language → IDs. Build the change set as the deltas only — never re-send unchanged fields.

| User says | Field | Value |
|-----------|-------|-------|
| "rename to X" / "fix the title" | `title` | string |
| "change/rewrite the description" | `description` | markdown — **replaces** the whole body (see note) |
| "set status to In Review" / "move to Todo" | `stateId` | status ID — **validate team first** |
| "assign to me" | `assigneeId` | literal `"me"` (script resolves it) |
| "assign to Alex" / a named person | `assigneeId` | user ID (Alex is in the table; others → `find` or ask) |
| "set priority to High" / "bump priority" | `priority` | `1`–`4` (or `0`) |
| "move to project X" | `projectId` | project ID |
| "move to the current cycle" | `cycleId` | run `current-cycle --team <issue's team id>` |
| "set milestone to X" | `projectMilestoneId` | run `milestones --project <issue's project id>` |
| "re-parent under PROD-X" / "make it a sub-issue of X" | `parentId` | resolve X's UUID via `get-issue`/`find-issue` |
| "set the estimate to N" | `estimate` | points |
| "add the X label" | `addedLabelIds` | `[label ID]` — keeps existing labels |
| "remove the X label" | `removedLabelIds` | `[label ID]` |
| "set the labels to X (and Y)" / "replace labels" | `labelIds` | `[label IDs]` — **replaces** the whole set |

**Description is a full replace.** `description` overwrites the entire body. If the user wants
to *append* or *tweak* a section, read the current `description` from Step 2, edit it locally,
and send the complete new body. To merely *add* information without rewriting the body, prefer a
**comment** (Step 6) instead.
**Keep a rewritten description to about 20 lines** (state, what's left, links); move history,
status logs and long tables into a comment. That is the target when asked to "simplify" a card.

**Labels — add/remove vs replace (gets people every time):**
- "add label X" / "remove label X" → **incremental** (`addedLabelIds` / `removedLabelIds`).
  This is the default reading of "add"/"remove".
- "set labels to …" / "replace the labels with …" / "make the only label …" → **replace**
  (`labelIds`, the full final set).
- Do **not** mix `labelIds` with `addedLabelIds`/`removedLabelIds` in the same update.

**Status — confirm the team.** Status IDs are team-scoped. Use the `team` from Step 2: if it's
Product/Design, the hardcoded status IDs apply. If it's any other team, those IDs are wrong —
resolve that team's own workflow state IDs with `states --team <team-id>`, or skip the status
change and tell the user.

---

## Step 3a: Definition-of-Done preflight (only when completing an issue)

**Runs whenever the change moves the issue into a `completed` state** — i.e. the user is
finishing / closing / marking the card done, or `stateId` resolves to a `type: "completed"`
state (Product/Design **Done** = `530cf975-c1e9-4cf8-95fa-1c0a296e2ecc`; for other teams,
resolve via `states --team <id>` and pick the `completed` one). It does **not** run for
ordinary edits (title, label, in-progress status changes, etc.).

A card may not be marked Done unless all four fields are set. Check them against the Step 2
`get-issue` snapshot:

| Constraint | Field | Considered set when |
|------------|-------|---------------------|
| **Estimate** | `estimate` | non-null and > 0 |
| **Assignee** | `assignee` | non-null |
| **Cycle** | `cycle` | non-null |
| **Project** | `project` | non-null |

For any missing field, **fold the fix into the same `update-issue`** so the card is compliant
the moment it's completed. Resolve each as follows, and **surface what you auto-filled** in the
Step 4 confirmation:

- **Estimate missing** → don't guess silently. Propose a value from the estimate scale based on
  the work (default **5** if there's no signal) and confirm it in the diff. If the user is
  present, a one-line "estimate wasn't set — using 5 (Average), ok?" is enough.
- **Assignee missing** → default to the current viewer (`"assigneeId": "me"`) if the user is
  completing their own work; otherwise ask who should own it. Never complete a card unassigned.
- **Cycle missing** → set the issue's team's active cycle: `current-cycle --team <issue's team
  id>` → use its `id` as `cycleId`. If there's no active cycle, tell the user and leave it
  (don't invent one).
- **Project missing** → infer from the work type using the Projects table; if genuinely
  ambiguous, ask once. Don't complete a card with no project.

If a gap can't be resolved without the user (e.g. ambiguous project, no active cycle, an
estimate you can't justify), **don't complete the card** — report the missing field(s) and ask,
rather than forcing it Done in a non-compliant state.

Example completion change set (issue had no estimate and was unassigned):

```json
{
  "id": "<issue-uuid>",
  "stateId": "530cf975-c1e9-4cf8-95fa-1c0a296e2ecc",
  "estimate": 5,
  "assigneeId": "me"
}
```

---

## Step 4: Confirm the diff

**Show the before → after for every changed field and get a go-ahead before applying.** This is
an edit to an existing artifact, so a quick confirmation prevents silent clobbering (especially
for `title`, `description`, and `labelIds` replaces).

```
PROD-1234 — [current title]
Changes:
  • Status:   Todo → In Review
  • Assignee: (unassigned) → Jerome Curlier
  • Labels:   + Bugs   (keeps: AI First)
Apply?
```

- **Description replace** → show that the body will be fully overwritten (don't paste the whole
  new body unless the user asks; summarise the change).
- **Batch of issues** → show one block per issue.
- If the changes came as an explicit, unambiguous instruction the user just typed
  ("set PROD-1234 to In Review"), a single-line echo + apply is fine — don't over-confirm a
  one-field change the user clearly asked for. Reserve the full diff for multi-field edits,
  description rewrites, and label *replaces*.

---

## Step 5: Apply the update

Write the change set to a temp JSON file (keeps long descriptions — and the API key — off the
command line). The file **must** include `id` (the **UUID** from Step 2) plus only the fields
you're changing:

```bash
cat > /tmp/linear_update.json <<'JSON'
{
  "id": "<issue-uuid-from-get-issue>",
  "stateId": "<status-id>",
  "assigneeId": "me",
  "addedLabelIds": ["<label-id>"]
}
JSON
python3 ${CLAUDE_PLUGIN_ROOT}/skills/update-linear-issues/scripts/linear_api.py update-issue --input-file /tmp/linear_update.json
rm -f /tmp/linear_update.json
```

Returns `{"id","identifier","url"}`. `"assigneeId": "me"` is resolved to the current viewer.

**`IssueUpdateInput` field reference:**

| Change | Field | Shape |
|--------|-------|-------|
| Title | `title` | string |
| Description (full replace) | `description` | markdown string |
| Status | `stateId` | string (team-scoped) |
| Assignee | `assigneeId` | string user ID, or literal `"me"` |
| Priority | `priority` | `0`–`4` |
| Project | `projectId` | string |
| Cycle | `cycleId` | string |
| Milestone | `projectMilestoneId` | string |
| Parent | `parentId` | string (UUID) |
| Estimate | `estimate` | number |
| Labels (replace) | `labelIds` | array of strings |
| Labels (add) | `addedLabelIds` | array of strings |
| Labels (remove) | `removedLabelIds` | array of strings |

> The JSON's `id` is sent as a separate GraphQL variable; every other key is the
> `IssueUpdateInput`. Include **only** keys you want to change. To unassign or clear a field,
> Linear accepts `null` for nullable fields (e.g. `"assigneeId": null`, `"parentId": null`).

---

## Step 6: Optional follow-up comment

By default, **don't** add a comment — the field edit is the change. Add one only when:
- The user asks for it ("…and leave a note explaining why"), or
- The edit benefits from an audit trail (e.g. re-scoping, status flip with a reason).

```bash
cat > /tmp/linear_comment.md <<'MD'
Moved to In Review — PR #1234 is ready for review.
MD
python3 ${CLAUDE_PLUGIN_ROOT}/skills/update-linear-issues/scripts/linear_api.py comment --issue "<issue-uuid>" --body-file /tmp/linear_comment.md
rm -f /tmp/linear_comment.md
```

**End-user PII.** A Linear description or comment is an external surface. When the change
includes end-user identity data, follow `Compliance/Privacy` — prefer a UID prefix + email
domain over full identifiers. The exception is when the full value *is* the actionable content
of the ticket (e.g. a coach's contact email the team needs to act on); include only what the
task genuinely requires.

---

## Step 7: Summarise

Report what changed, per issue:

```
Updated PROD-1234 — [title]
  • Status:   Todo → In Review
  • Assignee: → Jerome Curlier
  • Labels:   + Bugs
  https://linear.app/aura-health/issue/PROD-1234/...
```

Use the `url` and `identifier` straight from the `update-issue` (or Step 2 `get-issue`) output —
no URL construction needed.

---

## Edge Cases

**Issue not found:** `get-issue` returns `{"error":"not_found"}` (exit 0). Re-resolve via
`find-issue` or ask the user for the identifier.

**Script returns an `"error"` (`no_key`, `api_error`, `graphql_error`, `network_error`):**
Warn the user with a one-line message, then fall back to the Linear MCP tools if a connector is
available; otherwise ask the user to add/fix `LINEAR_API_KEY` via the `update-config` skill and
restart Claude Code. For `network_error` on the update, re-run `get-issue` to check whether the
change landed before retrying (see Write safety).

**Status on a non-Product/Design team:** The hardcoded status IDs are Product/Design-scoped and
error elsewhere — resolve that team's own IDs with `states --team <id>` (Step 3), or tell the user.

**Completing a card that fails the Definition-of-Done check:** Fold the fixes into the same
completion update per Step 3a; if a gap needs the user, don't complete the card — ask first.

**Reopening a parent may not stick:** read the children's real states first (Step 2) — never act on
a second-hand claim about them. A revert is a *writer*, not an automation: no team has sub-issue
auto-complete enabled, and a personal `LINEAR_API_KEY` writes as **its owner**, so a concurrent
session's edit is indistinguishable from your own in the history (`Tools/Linear` → *Write
attribution and concurrency*).

**Re-parenting across teams:** Linear allows a parent on a different team than the child, but it
can move the issue's effective grouping. Warn the user before re-parenting across teams.

**Description edits:** `description` is a full replace — read the body in Step 2 and send the whole
thing, or add a comment (Step 6) instead. See Step 3.

**Label replace vs add:** "add"/"remove" are incremental; only use `labelIds` when the user says
"set/replace labels" (Step 3). Never use a label outside the hardcoded table.

**Nothing to change:** If the requested value already matches the current state (from Step 2),
say so and skip the write rather than sending a no-op update. Verify the *premise* of a requested
state change against `get-issue`, not against what the conversation asserts — a stale premise
produces a write that either no-ops or fights a concurrent writer.
