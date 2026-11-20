---
name: add-linear-issues
description: >
  Creates one or more Linear issues from the current conversation context or from user input.
  Trigger on any reasonable phrasing that combines an action verb (add, create, open, file, make,
  new, track, log, ask) with a Linear artifact (Linear card, Linear ticket, Linear issue, card in
  Linear, ticket in Linear). Concrete examples: "add this to Linear", "add to Linear", "create a
  Linear issue for this", "create a Linear card", "create a card in Linear", "make a Linear card
  for this", "make a ticket for this", "file a Linear card", "track this in Linear", "log this
  as a ticket", "ask Linear to create a card". Also trigger on the short forms users type when
  Linear is the obvious tracker — "create a card", "create card", "add a card", "make a card",
  "new card", "open a ticket", "file a ticket". Also trigger proactively when a conversation
  produces a clear list of actionable tasks that should be tracked.
metadata:
  version: "3.8.0"
knowledge:
  reads:
    - Guide/Index        # baseline routing aid (universal)
    - Compliance/Privacy # PII rule for end-user data written to Linear (external surface)
    - Procedures/Engineering/Estimation # canonical FAST story-point scale (pre-fetched for the Estimate step)
    - Tools/Linear       # canonical team list (name, key, UUID) — re-verify here if teams change
    - Tools/GitHub                      # gh CLI auth + PR conventions
---

# Add Linear Issues

Creates Linear issues from context or user input. Handles assignee, priority, project, cycle,
milestone, team, labels, estimates, parent linking, and optional sub-issues — all in one step.

**Input:** Existing conversation context (if present), or user-provided description.

---

## How this skill talks to Linear

This skill uses the **Linear GraphQL API** directly via a bundled helper script — not the
Linear MCP connector — so it works in every session, including Cowork / local-agent mode
where MCP tools aren't available.

All Linear calls go through:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/add-linear-issues/scripts/linear_api.py <subcommand> [flags]
```

| Need | Subcommand | Replaces (old MCP tool) |
|------|-----------|-------------------------|
| Current user | `whoami` | `get_user(id:"me")` |
| Active cycle for a team | `current-cycle --team <id>` | `list_cycles(...)` |
| Project milestones | `milestones --project <id>` | `list_milestones(...)` |
| Find a parent issue | `find-issue --query <ident\|text>` | `list_issues(query:...)` |
| Create an issue | `create-issue --input-file <f.json>` | `save_issue(...)` |
| Add a comment | `comment --issue <id> --body-file <f>` | — |
| Attach a URL | `attach-url --issue <id> --url <u>` | `create_attachment(...)` (URL only) |

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
   Linear **MCP** tools if a connector is available (`save_issue`, `list_issues`, `get_user`,
   `list_cycles`, `list_milestones`, `create_attachment` — field mapping in Step 5). Example:
   > ⚠️ Linear API call failed (`api_error`: HTTP 401 — check `LINEAR_API_KEY`). Falling back to the Linear MCP connector.
3. **Ask the user only if neither works** — to add/fix `LINEAR_API_KEY` via the `update-config`
   skill and restart Claude Code.

> **Write safety on `network_error`.** A network/timeout error on a *mutation* (`create-issue`,
> `comment`, `attach-url`) is ambiguous — the write may have landed before the connection
> dropped (the script flags this with `"verify_before_retry":true`). Before retrying that write
> via MCP, check whether the issue/comment already exists, to avoid a duplicate. Read
> subcommands are always safe to retry.

---

## Hardcoded workspace values

Team / project / label / state / user IDs:
[`references/workspace-ids.md`](references/workspace-ids.md). Read its **"How a wrong ID
fails"** section before trusting a row you have not re-verified — a dead ID rejects the whole
create, but a wrong-but-live **status** ID creates the card silently in the wrong column.
Cycle IDs are not pinned; resolve the active one with `current-cycle --team <id>`.

## Step 1: Determine What to Create

**Always read the full conversation history first** — scroll back through every message in this
session before deciding what to create. Do not rely only on the most recent message.

**If actionable tasks exist anywhere in the conversation:**
- Extract all tasks that warrant a Linear issue
- Group related tasks as a single issue; split unrelated tasks into separate issues
- Collect resources: GitHub PR URLs (note merge state), Slack/Notion/Figma links, screenshots
- Determine the team (see Team Selection below)
- Assess whether sub-issues are needed (see Sub-Issue Logic below)
- Proceed directly to Step 2 — no confirmation needed

**If there is genuinely no actionable content:**
> What should I add to Linear? Please describe the task(s) — I'll handle the rest.

### Team Selection

- **Product/Design** (`7e95fa4a-7510-447d-adc7-c663612e70c6`) — default for new features, bugs, data, experiments, improvements, UI/UX, and anything with product relevance
- **Engineering** (`ffcc6973-8ebf-4f92-b3b2-5568decbbae8`) — only when the task has no product relevance at all (releases, devops, pure eng execution)

- **A card about one repo's code** (a bug or fix in a backend service, the mobile app, a web
  front end) → the team that owns that repo, from the Teams table in
  [`references/workspace-ids.md`](references/workspace-ids.md) (e.g. a backend service → Backend).
  AINative tooling stays on Product/Design.

When in doubt, use Product/Design.

### Title Format

If an experiment name is available in context, include it as a prefix:
> `ab:hideQP: Hide quick processing view`
Status and surface prefixes (`[Mobile]`, `[On Hold]`, `[Livekit Support]`) and the voice-agent parent (PROD-6647) are in [`references/workspace-ids.md`](references/workspace-ids.md) → Title prefixes and Standing parents.

### Sub-Issue Logic

Create a parent issue + sub-issues **only when one of these conditions is true**:
1. The user explicitly requests sub-issues
2. The work clearly requires a design or product research phase before engineering can begin

When sub-issues are needed and the user has not specified them, default to:
- One **Design** sub-issue — assigned to Alex, scoped to the design/research work
- One **Engineering** sub-issue — assigned to the current user, scoped to implementation

The parent issue carries the full context. Sub-issues carry work-stream-specific scope.

Do **not** create sub-issues for straightforward tasks where no prior design or research phase
is required.

---

## Step 2: Fetch Runtime Values

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/add-linear-issues/scripts/linear_api.py whoami
python3 ${CLAUDE_PLUGIN_ROOT}/skills/add-linear-issues/scripts/linear_api.py current-cycle --team "<team-id from Step 1>"
```

`whoami` returns `{"id","name","email"}` (for display). `current-cycle` returns
`{"id","name","number"}` — use its `id` as `cycleId`. If it returns
`{"error":"no_active_cycle"}`, omit `cycleId` and note this to the user. Cards filed under
Bug Investigations (Step 3 → Parent Card) never take a `cycleId`.

You don't need the viewer id for self-assignment — pass the literal string `"me"` as
`assigneeId` in Step 5 and the script resolves it to the current viewer.

---

## Step 3: Resolve Per-Issue Metadata

### Assignee

1. **Design task** → Alex (`16d710a5-8040-4975-91c6-3e15bc702957`)
2. **Explicit mention** in conversation → that user
3. **Otherwise** → `"me"` (current user from Step 2)

### Priority

- Blocking production issue → **Urgent** (1)
- Important but not blocking → **High** (2)
- Normal work item → **Medium** (3) ← default
- Nice-to-have → **Low** (4)

Default to **Medium** if unclear. Cards under Bug Investigations take **No priority** (`0`)
instead — see Parent Card.

### Project

| Work type | Project |
|-----------|---------|
| AI tooling, skills, knowledge, agents | AI Native |
| Aurie, iOS, voice assistant, mobile AI | Aurie AI - Mobile |
| Desktop app or Aurie web | Aurie AI - Desktop/Web |
| Aura web portal | Aura - Web |
| Content, meditations, programs | Aura - Content |
| Everything else | Aura - Core App |

If still unclear, prefer **Aurie AI - Mobile**. If genuinely equally plausible between two
options, ask the user once before creating.

### Milestone

After selecting the project, fetch its milestones:
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/add-linear-issues/scripts/linear_api.py milestones --project "<project-id>"
```
Returns `[{"id","name","targetDate"}]`.
- Use the milestone whose name contains **"Current"** → set as `projectMilestoneId`
- If no "Current" milestone → use the milestone with the latest `targetDate`
- If the project has no milestones → omit `projectMilestoneId`

### Parent Card

**Side-effect bugs default to Bug Investigations.** A bug that surfaced while doing other
work — noticed during an implementation, a QA pass, an investigation, or an agent run — rather
than being the task at hand, goes under the standing **Bug Investigations** parent
([PROD-6645](https://linear.app/aura-health/issue/PROD-6645/bug-investigations), ID in
[`references/workspace-ids.md`](references/workspace-ids.md) → Standing parents). It is filed
in **Backlog** (that team's Backlog state ID, same file), whatever team it lands on — never
Todo or any PR-derived state. It carries **no cycle and no priority** (omit `cycleId`, set
`priority` to `0`, No priority — prioritising happens at investigation, not at filing), and its
only label is **`Bugs`** (`0cdc116b-e9a0-46ee-8133-3481c2b64376`). Two things override the
default:
- **The user names a different parent** → use that parent instead (search as below).
- **The bug already has a PR in this session** → it is past investigation; file it under the
  current `Bugs [Cycle N]` tracker, with status from Step 4. Resolve the tracker from the
  Product/Design cycle's **`name`** (e.g. `Cycle 46`), not its `number` — teams number cycles
  differently: run `current-cycle --team 7e95fa4a-7510-447d-adc7-c663612e70c6`, then
  `find-issue --query "Bugs [<name>]"`. No match → ask the user which parent to use.

Two more kinds of card have a default parent:
- **Security findings** (a credential exposure, an auth gap, PII in logs) → the standing
  **Security and Privacy** parent ([PROD-6926](https://linear.app/aura-health/issue/PROD-6926),
  ID in `references/workspace-ids.md` → Standing parents), label **`Security & Safety`**,
  Backlog, on the team that owns the code.
- **A fix to AINative tooling** (a skill, agent, hook or script in this repo) → the current
  `AI Native [Cycle N]` tracker, resolved like the Bugs tracker above
  (`find-issue --query "AI Native [<name>]"`), project AI Native, Todo.

If the user specifies a parent issue, search for it:
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/add-linear-issues/scripts/linear_api.py find-issue --query "<identifier or name>"
```
Returns `[{"id","identifier","title","url","labels":[{"id","name"}]}]`. It resolves an exact
identifier (e.g. `PROD-2151`) directly; otherwise it does a case-insensitive **substring**
match on the title (not fuzzy — pass the identifier when you have it, or a distinctive
substring of the title).

- **Single clear best match** → use it directly without asking
- **Multiple plausible matches** → present options and ask the user to choose:
  > Which issue should I use as the parent?
  > 1. **AUR-123** — [title] (updated 2 days ago)
  > 2. ...
- **No match found** → ask:
  > I couldn't find a matching issue for "[term]". Continue without a parent, or try a different search term?

When a parent is selected, **inherit the parent's labels** for the new issue. Only carry over
labels that appear in the hardcoded table above. Bug Investigations is the exception: its
cards take `Bugs` alone, not the parent's `Tech Quality` (see above).

### Labels

- **Only use labels from the hardcoded table above** — never use archived labels, never create new ones.
- **If a parent card was selected:** copy the parent's label(s) to this issue, but only if those labels appear in the table above (except under Bug Investigations, whose cards take `Bugs` alone — see Parent Card).
- **If no parent card:** pick the single most relevant label from the table. Do not attach multiple labels unless the content clearly spans two distinct areas.
- **If the label you want has no row, stop and say so** — ask the user whether to create the
  card unlabelled or with a different label. Never reach for a same-named ID from the Teams
  or Projects table: `QA` and `Support` each exist as *both* a team and a label with
  different IDs, and passing the team ID as `labelIds` rejects the whole create with
  `Entity not found in validateAccess: labelIds`. A missing row is a gap in the reference
  file to report, not something to improvise around.

### Estimate

**Read the scale from AI Knowledge — this skill carries no copy of it.** The canonical FAST
story-point rubric lives at [`Procedures/Engineering/Estimation`](https://app.notion.com/p/Estimation-38075e23af2081ca86a8ee1110b1021c)
and is pre-fetched via this skill's `knowledge.reads`. Size the issue from that page's scale and
gates. If it wasn't pre-fetched (token/MCP unavailable), pull it on demand with
`retrieve-knowledge path=Procedures/Engineering/Estimation`. **If the page can't be read at all,
ask the user for the number** rather than sizing from a remembered scale — a guessed estimate is
indistinguishable from a considered one once it's on the card.

**The release floor binds whether or not the page loads.** Work that ships is **3 or more**; 1 and
2 are only for work that ships nothing. A one-line change that ships is a 3 — the points buy the
review-and-release cycle, not the diff. Every change under `plugins/**` in this repo ships (the
version-bump gate re-ships the plugin into the install cache), as does any mobile-app, `AuraInfra`,
or Intelligence Service change.

Default to **5** for most well-scoped tasks. Lean slightly higher when uncertain.

**The gate above the floor** is *does it need another team?* (the 5↔8 line). Raw file count only
forces a higher number at the page's 21/32 split-warnings, and even then only paired with real
complexity — a mechanical N-file rename isn't a 21.

---

## Step 4: Determine Status from PR State

A card's status tracks its PR. This table is the mapping for both moments that matter:
the state a card is **created** in (this step), and the state an existing card **moves
to** as its PR progresses ("Status through the PR lifecycle" below).

Use the state IDs for the **team chosen in Step 1** — never mix teams (see Statuses table above).

| PR state | Product/Design (`PROD-*`) | Engineering (`ENG-*`) | Mobile (`ENGMOB-*`) |
|---|---|---|---|
| No PR yet | Todo `85f68398-a8b5-4df6-8491-0cfc32279769` | Todo `37082212-94ba-468a-b2a3-ef5d9a2f4b59` | Todo `090653f2-f21a-4718-89cd-0438e781f82c` |
| PR open as a **draft** | Eng In Progress `53c1383f-fb0e-485d-b31a-f730a4b9f39f` | In Progress `f0e36626-72b4-4cf6-945c-ba58fe55afe2` | In Progress `2f3af5c5-ed04-459b-84b0-f377d75141e2` |
| PR **ready for review** | In Review `56f194ce-ab02-48a5-b6e6-75f095ddd6f2` | In Review `e2f91b3a-4f04-451e-bf21-dca5013af14b` | In Review `7b782b06-d9c5-41ba-8588-35b193d928ae` |
| PR **merged** | Done `530cf975-c1e9-4cf8-95fa-1c0a296e2ecc` | To Be Released `8d60b67c-eb93-46ee-aa80-cbd8189c2ef5` | To Be Released `38a0275e-05e9-4f57-8cd7-114917c4ca83` |

Engineering **and Mobile** carry a release gate the Product/Design team does not have: a
merged `ENG-*` / `ENGMOB-*` card waits in **To Be Released** and reaches **Done** once a build
actually carries it (Done is `f4a20e93-5f19-492d-9e61-ad9308ff80f7` for `ENG-*`,
`c6860d45-1749-4d95-8f40-3e509d58862e` for `ENGMOB-*`). `PROD-*` has no such state, so a merged
PROD card goes straight to Done. `ENG-*` and `ENGMOB-*` are **separate teams** with their own
state ids. **Never mix teams** — a `PROD`/`ENG` state id on an `ENGMOB` card is a different
team's and Linear rejects it.

Draft and ready-for-review are different states, so resolve `isDraft` alongside `state`:

```bash
gh pr view <pr-number> --repo <owner/repo> --json state,isDraft,mergedAt
```

- `state: "MERGED"` → Done (PROD) / To Be Released (ENG, ENGMOB)
- `state: "OPEN"`, `isDraft: true` → Eng In Progress (PROD) / In Progress (ENG, ENGMOB)
- `state: "OPEN"`, `isDraft: false` → In Review (all teams — their own state id)
- `state: "CLOSED"` with no `mergedAt` → Todo

Default to **Todo** if unsure. A card filed under **Bug Investigations** (Parent Card, Step 3)
skips this table: it is always **Backlog**, using the Backlog ID for its team.

### Status through the PR lifecycle

The table applies for the whole life of the card, not only the moment it is filed. Move
the card in the **same step** as the command that moved its PR:

- `gh pr ready` → set the card to **In Review**
- PR merged → set the card to **Done** (PROD) or **To Be Released** (ENG)

A card left in Todo behind a PR that is already open for human review misreports where the
work is, which is the whole job of the status column. Use the `update-linear-issues` skill
to patch an existing card, or the GraphQL mutation directly:

```bash
curl -sS -X POST https://api.linear.app/graphql \
  -H "Authorization: $LINEAR_API_KEY" -H "Content-Type: application/json" \
  -d '{"query":"mutation { issueUpdate(id: \"<issue-id>\", input: { stateId: \"<state-id>\" }) { success } }"}'
```

---

## Step 5: Create the Issues

For parent + sub-issue structures: create the parent first, then create each sub-issue with
`parentId` set to the parent's returned `id`.

Write the issue input to a temp JSON file, then create it (keeps long descriptions — and the
API key — off the command line):

```bash
cat > /tmp/linear_issue.json <<'JSON'
{
  "teamId": "<team-id from Step 1>",
  "title": "<title>",
  "description": "<markdown description>",
  "assigneeId": "me",
  "priority": 3,
  "projectId": "<project-id>",
  "cycleId": "<current-cycle-id>",
  "parentId": "<parent-issue-id>",
  "labelIds": ["<label-id>"],
  "estimate": 5,
  "stateId": "<state-id from Step 4>"
}
JSON
python3 ${CLAUDE_PLUGIN_ROOT}/skills/add-linear-issues/scripts/linear_api.py create-issue --input-file /tmp/linear_issue.json
rm -f /tmp/linear_issue.json
```

Returns `{"id","identifier","url"}`. **Omit** any field you can't resolve confidently
(delete that line from the JSON). `"assigneeId": "me"` is resolved to the current viewer.

**Field names (`IssueCreateInput`) differ from the old MCP `save_issue` params:**

| MCP `save_issue` param | Linear API (`IssueCreateInput`) |
|------------------------|----------------------------------|
| `assignee` | `assigneeId` (or literal `"me"`) |
| `project` | `projectId` |
| `cycle` | `cycleId` (omit if no active cycle) |
| `milestone` | `projectMilestoneId` (omit if none) |
| `labels` | `labelIds` (array) |
| `state` | `stateId` (omit if unsure — see Statuses team-scoping note) |
| `teamId`, `title`, `description`, `priority`, `estimate`, `parentId` | unchanged |

**PR / resource links:** `IssueCreateInput` has no `links` field. Either include the link in
the description's `## Resources` section (simplest, always works), or attach it after
creation:
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/add-linear-issues/scripts/linear_api.py attach-url --issue "<issue-id>" --url "<pr-url>" --title "<PR title>"
```

### Description Format

Write clear markdown covering:
1. **What** needs to be done
2. **Why** it matters
3. **Acceptance criteria** as a checklist (when scope warrants it)
4. **Relevant context or constraints** from the conversation
5. **## Resources** — markdown links for Slack, Figma, Notion, GitHub, etc.

Note any screenshots when they are relevant (they will be attached in Step 6).

**Keep the description short — about 20 lines.** The description is what a reader sees first,
so it holds the current state: what the card is, what is done, what is left, and the links.
Status logs, investigation notes, long tables and history go in **comments**. This applies most
to umbrella cards, which grow as their children land.

**End-user PII.** Linear is an external surface. When the conversation includes end-user
identity data, follow `Compliance/Privacy` — prefer a UID prefix + email domain over full
identifiers. The exception is when the full value *is* the actionable content of a support
ticket (e.g. a coach's contact email the team needs to action the work); include only what
the task genuinely requires.

---

## Step 6: Attach Images or Links

The helper's `attach-url` attaches a **URL** to an issue (Linear's `attachmentCreate`):
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/add-linear-issues/scripts/linear_api.py attach-url --issue "<issue-id>" --url "<url>" --title "<title>"
```

**Limitation — local / base64 image upload is not supported by this path.** Linear's
`attachmentCreate` links a URL; uploading raw image bytes requires Linear's separate
`fileUpload` signed-URL flow, which this helper does not implement. For images collected in
Step 1, pick one:
- If a Linear **MCP** connector is available, use MCP `create_attachment` (it accepts
  `base64Content`) to upload the image.
- If the image already lives at a URL (Slack / Figma / CDN link), use `attach-url`.
- Otherwise, reference the image URL in the description and note that the raw file wasn't
  uploaded.

Skip this step if no images or links were collected.

---

## Step 7: Confirm and Summarise

After all issues are created, output the title and direct link for each card:

```
Created X Linear issue(s):

• AUR-XXX — [Title] | [Project] | Priority: [name] | Estimate: [N]
  https://linear.app/aura-health/issue/AUR-XXX/...
```

If a parent + sub-issues were created, show the structure explicitly:
```
Parent:
• AUR-XXX — [Parent Title]
  https://...

Sub-issues (parent: AUR-XXX):
• AUR-YYY — [Design sub-issue title]
  https://...
• AUR-ZZZ — [Engineering sub-issue title]
  https://...
```

If any issue was estimated at 21:
> ⚠️ AUR-XXX is Extra-High (21). Consider splitting it into smaller sub-issues.

The `url` and `identifier` fields come straight from the `create-issue` JSON output — use
them as-is, no URL construction needed.

---

## Edge Cases

**Multiple unrelated tasks:** Create separate issues without asking first.

**Script returns an `"error"` (`no_key`, `api_error`, `graphql_error`, or `network_error`):**
Warn the user with a one-line message, then fall back to the Linear MCP tools if a connector is
available (field mapping in Step 5); otherwise ask the user to add/fix `LINEAR_API_KEY` via the
`update-config` skill and restart Claude Code. For `network_error` on a write, verify the write
didn't land before retrying (see the Authentication note).

**No active cycle:** Omit `cycleId` and tell the user.

**Parent search returns nothing for a name:** `find-issue` does a literal case-insensitive
substring match on the title — try the issue identifier (e.g. `PROD-2151`) or a more
distinctive title substring before giving up.

**Engineering-team issue:** The hardcoded status IDs are Product/Design-scoped — omit
`stateId` for Engineering-team issues (see the Statuses note) so the issue lands in that
team's default state.

**Project genuinely ambiguous (two equally plausible options):** Ask once before creating.

**Estimate unclear, scale in hand:** Default to 5; use context clues rather than asking.

**Scale unreadable** (`Procedures/Engineering/Estimation` neither pre-fetched nor fetchable): ask
for the number instead of defaulting. This is the one estimate case where asking beats a default —
context clues are read *against* the scale, so with no scale there is nothing to read them against.
