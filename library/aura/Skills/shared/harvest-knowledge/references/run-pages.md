# Run pages under `Guide › Harvest`

Every `apply` run produces one immutable audit page as a child of `Guide › Harvest`. The parent page is an index; per-run detail lives on the child pages.

## Parent index template

The `Harvest` page under Guide renders as an index only. No findings content — that belongs on each run page.

```markdown
**At a glance:** Audit log for `shared:harvest-knowledge`. One child page per run, named `YYYY-MM-DD-runN`. Rows are appended at the bottom of the run-log table — the newest entry is the last row, not the first.

# Cadence and scope
- Cadence: on demand (typical: weekly or after a big project).
- Scope: upstream canonical sources (Notion teamspaces Engineering, Prompts, Product, QA; Slack/Linear/Drive as adapters land) → AI Knowledge (L4).
- Writes: only inside AI Knowledge. Canonical sources are read-only.

# Run log
Append-only. New runs land at the bottom. (The Notion table API only supports appending `table_row` children to the end; maintaining "newest on top" would require rebuilding the table every run, which adds risk and write cost. The append-at-bottom convention matches actual implementation behaviour across all prior runs.)

| Date | Window | New | Updates | Deferred | Rejected | Run page |
|---|---|---|---|---|---|---|
| 2026-04-15 | 2026-04-01 → 2026-04-15 | 7 | 4 | 0 | 1 | [2026-04-15-run1](<url>) |
| 2026-04-22 | 2026-04-15 → 2026-04-22 | 3 | 5 | 1 | 2 | [2026-04-22-run1](<url>) |

# Sources
- Native to this teamspace (automated audit index; not distilled from a canonical source).
```

## Run page template

```markdown
**At a glance:** <run date> — window <since> → <today>. <new> new pages, <updates> updates, <deferred> deferred, <rejected> rejected.

# Sources scanned
- Notion (REST enumeration): <teamspaces + window> — reached `since`? <yes | NO, stopped at <n> results> · own-writes ratio <n>/<total> = <ratio>
- Notion (MCP teamspace probe): <per teamspace: query terms asked, hits returned; note any teamspace that returned the 25-result cap>
- Coverage verdict: <complete | partial — MCP hits real, window unknown | unknown>
- Slack: <channels + window, or "not run — MCP missing">
- Other: ...

# Findings

## New durable knowledge → create page
- Source: [<title>](<url>)
- Proposed destination: Identity / Domain / Procedures / Learnings › <page>
- Classification rationale: ...
- Status: Open | Approved — <page created> | Rejected — <reason> | Deferred — <until>

## Updates to existing page
- Page: [<title>](<url>)
- Source: [<title>](<url>)
- Proposed edit: ...
- Status: ...

## Skipped / stays at source
- Source: ...
- Why: canonical / ephemeral / stale

# Edits applied this run
## Pre-review (auto-apply)
- ...
## Human-approved
- ...

# Decision log
| Finding | Type | Decision | Action |
|---|---|---|---|
| <short description> | New / Update / Skip | Approved / Rejected / Deferred | <page link or reason> |

# Sources
- Native to this teamspace.
- Upstream sources read: <list of Notion/Slack canonical-doc URLs scanned — never Linear tickets; see Rule 11>
```

Carry-forward items from a prior run page are shown inline under the relevant Findings subsection with a trailing `Recurring from [<prior run>](<url>)` note.
