# Cards and sessions — detail for step 7

Step 7 of `SKILL.md` turns the run's actionable items into Linear cards and offers a
Claude session for the ones a session can finish alone. This file holds
what this run passes to `shared:add-linear-issues`, the dedupe search, the card body and the
session prompt template.

## Which items become cards

| Item in the run | Card? | Session? |
|---|---|---|
| **Bug noticed** in a session (wrong output, broken script, drifted schema) | yes | only when the fix is a repo change a session can make and test alone |
| **Security finding** (credential exposure, auth gap, PII leak) | yes | no by default — security fixes need an owner's call on scope; offer one only when the source session already settled the fix |
| **Deferred learning that needs a change in another repo** (e.g. an AINative skill behind the version gate, a script fix rather than a rule) | yes | yes, when it ends in a draft PR |
| A learning that landed in this run's PR or in AI Knowledge | no | no |
| `not written` and ignored-rule findings | no — they stay in the run log | no |
| An item that needs a human decision (accept a known limit, choose between approaches, a pricing call) | yes, card only | **no** |

A rule the run can write goes in a PR, not a card. A bug never becomes a rule.

## Placement belongs to `shared:add-linear-issues`

This skill does not choose team, parent, label or state. It hands each item to
`shared:add-linear-issues`, which owns those rules (its Team Selection and Parent Card
sections); they are not restated here. When a placement rule needs to change, change it
there, so a card filed by hand and one filed by this run land in the same place.

Pass it, per item: the kind (bug, security finding, cross-repo fix), the repo and file
paths, the title and body below, and `assignee`. Where `add-linear-issues` would stop to ask
(no parent match, a label with no row, two equally plausible projects), there is nobody to
answer: log `not filed: <its question>` and move on.

## Dedupe first

Before creating anything, search Linear for an existing card about the same thing:

1. the title's distinctive keywords (`find-issue --query "<keywords>"`, or `list_issues(query: …)`);
2. the file path the item is about (`create.sql`, `redact.ts`);
3. any PR, Bugsnag or GCP link in the evidence.

Search open cards and cards closed in the last 30 days. A match on the same defect means
**comment** on that card with the new evidence and session ids, and log
`existing card <id>`. Skip the comment when the card already cites the same session ids:
re-running the same window must create nothing and add nothing.

## Card body

Title: `<area>: <defect>` or an imperative, no ticket ids. Assignee: the `assignee` input
(`@me` → the viewer). Body:

```markdown
## Problem
<what is wrong, in one paragraph>

## Evidence
> <redacted quote> — session <8-char id>, <date>
- Files: `<repo>/<path>`

## Acceptance
- <observable outcome a reviewer can check>

_Filed by `shared:distill-sessions-daily` · run <YYYY-MM-DD> · sessions <id prefixes>_
```

Evidence quotes get the same `Compliance/Privacy` redaction as PR bodies (UID → short
prefix, email → domain, no end-user names, no keys, tokens or account IDs). Linear refs
belong in the card, never in a repo rule the run writes.

## Session offer

For each card a session can finish alone, call `spawn_task` (load it with ToolSearch when
it is deferred). The spawned session has none of this run's context, so the prompt must
stand on its own:

```text
Implement Linear card <ID> (<url>)<, parent <PARENT-ID>>. Read the card first — it is the
spec. Move it to In Progress when you start. Linear: the Linear MCP, or the GraphQL API
with LINEAR_API_KEY from ~/.claude/settings.json env (raw Authorization header, no Bearer).

Repo: <absolute repo path> (default branch <base>). Work in a git worktree off
origin/<base> at <repo>/.claude/worktrees/<id-lower>, branch
<firstname>/<id-lower>-<slug>. Never edit the main checkout. Follow the repo's CLAUDE.md
and PR rules<; for AINative: bump the skill and plugin versions per its version gate and
run python3 -m unittest discover -s tests -q>.

Commit referencing [<ID>], push, open a DRAFT PR assigned to <assignee> linking <ID>.
Comment `bugbot run` on its own, fix and re-run until BugBot is clean on the current head
commit (check-run completed, summary cites the head SHA, no inline finding on it) and CI
is green, then `gh pr ready` and move <ID> to In Review. Never merge.
```

`title` is the card's title as an imperative (under 60 characters); `tldr` names the
session that found the item and what the new session will do. Log each returned
`task_id` in the run log.

## Worked example

Three items from one day's sessions, as step 7 handles them (placement by
`shared:add-linear-issues`):

| Item | Kind → placement | Session |
|---|---|---|
| FeedService `create.sql` has syntax errors and drifts from the prod schema | bug → Backend, Bug Investigations | yes — a backend repo change ending in a draft PR |
| Log redaction hides a free-text credential only up to its first line break | security finding → Backend, Security and Privacy, `Security & Safety` | no — the fix spans 13 services and needs an owner's scope call |
| `sync-design-system` writes the Aurie bundle in batches too small for 305 files | cross-repo fix → `AI Native [Cycle N]` | yes — an AINative skill fix behind the version gate |
