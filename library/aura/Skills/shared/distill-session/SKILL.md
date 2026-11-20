---
name: distill-session
description: >
  Distills a Claude Code session into durable artifacts. Classifies the session shape (coding
  + PR review, coding only, or non-coding research/planning), extracts learnings from PR
  review comments + reply threads + in-session user corrections, then routes each learning to
  the right destination: repo-specific patterns go into `{repo}/.claude/rules/*.md` (or
  `CLAUDE.md` fallback) via a new PR with citations to the source comment, and cross-cutting
  patterns go into the fitting AI Knowledge page or database (resolved via
  `shared:search-knowledge`). Always requires user approval per-learning before writing —
  never auto-canonicalizes a rule. Trigger when the user says: "distill this session",
  "distill the session", "what did we learn", "what did we learn this session", "extract
  learnings", "codify the learnings from this PR", "capture the learnings", "turn this into
  rules", "persist these learnings", or "/distill-session". Also proactively offer after
  merging a PR with 3+ reviewer comments.

metadata:
  version: "3.1.0"
knowledge:
  reads:
    - Guide/Index
    - Guide/Placement Rules   # consult before deciding where a learning lands
    - Compliance/Privacy      # redact user PII in evidence quotes before any write
    - Tools/GitHub                   # gh CLI auth + PR conventions
  writes:
    - "*"   # cross-cutting: a distilled learning routes to the Procedures/Tools/Learnings page or database that fits, decided at runtime via search-knowledge
---

# distill-session

Captures durable learnings from a Claude Code session and routes them to two destinations: the active repo's `.claude/rules/*.md` (or `CLAUDE.md`) for codebase-specific patterns, and AI Knowledge for cross-cutting team knowledge. Built so review-heavy PRs and design discussions don't leave their rules-worthy insights behind when the conversation closes.

Distinct from neighboring skills:

- [`shared:ingest-knowledge`](../ingest-knowledge/) writes to AI Knowledge **only** from a single source (URL, PDF, conversation). `distill-session` adds session classification + repo-rules routing, and for the AI Knowledge branch routes each learning to the page or database that fits: a narrative convention lands on the matching `Procedures/*` or `Tools/*` page (edited directly or via `ingest-knowledge`), and only a structured `Learnings/*` / `Signals/*` row goes through `record-knowledge`.
- `claude-md-management:revise-claude-md` writes to the local `CLAUDE.md` **only**. `distill-session` writes to the more granular `.claude/rules/*.md` files (falling back to `CLAUDE.md` only when no `rules/` directory exists) and also opens a PR rather than committing locally.

## Workflow

Five phases. Steps 4 and 5 are gated behind explicit user approval — the skill never auto-writes.

### Step 0: Never distill an incident session

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/distill-session/scripts/incident_guard.py check "$CLAUDE_CODE_SESSION_ID"
```

Exit **4** means stop: the session's title starts with `[Privileged`, its id is in the local exclude
file (`~/.claude/legal-hold/exclude-sessions`), or an incident data hold is marked and that file is
missing. A distill writes to AI Knowledge, Linear and PRs, none of them privileged, so say only
"this session is not distilled" and write nothing from it. Exit 0: go on.

### Step 1: Classify the session

Run [`scripts/classify_session.py`](scripts/classify_session.py) to determine the session shape. Pass `--since` to anchor "session start". A **git ref** uses the exclusive `<ref>..HEAD` range, so give a commit that *predates* the session (e.g. `origin/main`) — not the session's first commit, which would undercount by one. A **date** (`2026-06-16`, `"2 hours ago"`) is matched against commit times instead. It defaults to the latest tag, falling back to `HEAD~10`:

```bash
python3 plugins/shared/skills/distill-session/scripts/classify_session.py --since origin/main
```

Returns one of three classifications:

| Shape | Detection signal | Implication |
|---|---|---|
| `coding-with-review` | Commits since the anchor AND a PR exists for the branch AND it has ≥1 **human** reviewer comment | Highest-signal: PR comments + in-session corrections both contribute |
| `coding-only` | Commits exist but no PR, or the PR has only **bot** reviews (Cursor Bugbot, dependabot, …) — nothing human to harvest | In-session user corrections only |
| `non-coding` | No commits since the anchor (research, planning, decisions) | AI Knowledge writes only — no repo touched |

The review signal counts **human** comments only — a Bugbot-only PR classifies as `coding-only`, not `coding-with-review`, because there's no human review to harvest (`pr_comment_count` still reports the raw total; `pr_human_comment_count` drives the classification).

Output JSON: `{classification, is_coding, repo, pr_url, pr_number, commit_count, pr_comment_count, pr_human_comment_count, anchor}`.

### Step 2: Harvest source material

Depending on classification:

- **PR comments** (when `coding-with-review`): use [`scripts/harvest_pr_comments.py`](scripts/harvest_pr_comments.py) — pass `--pr` as a full URL or `owner/repo#N`. It fetches the inline review comments (paginated), orders each top-level comment followed by its replies chronologically, and returns `{id, in_reply_to_id, user, path, line, body, diff_hunk, html_url}` per comment so the extractor keeps each thread together with its diff context.

  ```bash
  python3 plugins/shared/skills/distill-session/scripts/harvest_pr_comments.py --pr AuraHealth/<repo>#<N>
  ```
- **In-session corrections**: Claude reads the transcript and identifies turns where the user corrected the assistant's approach, narrowed scope, named a convention, or explicitly approved a non-obvious choice.
- **Commit messages**: `git log --format=%B <session-start>..HEAD` — corrections often crystallize in commit message wording.

### Step 3: Extract learnings (Claude reasoning step)

For each source, Claude proposes a flat list of `{rule, evidence_quotes, scope, suggested_destination}`:

- `rule` — one-line imperative statement (e.g. "Use `let result;` default, never init to `[]`")
- `evidence_quotes` — verbatim quotes from PR comments / session turns that motivated the rule
- `scope` — `repo` (this codebase only) or `cross-cutting` (any repo / team-wide)
- `suggested_destination` — for `repo` scope: the closest matching `.claude/rules/*.md` file (or a proposed new file name); for `cross-cutting` scope: the AI Knowledge taxonomy path resolved via [`shared:search-knowledge`](../search-knowledge/)

### Step 4: User approval (mandatory gate)

Emit a markdown table to chat showing every proposed learning + destination + evidence quotes + diff preview of the rule-file change. The user picks via multi-select which ones to apply. The skill writes **only** the approved ones. No defaults, no "apply all" shortcut.

### Step 5: Apply approved learnings

Two backends, both gated by Step 4 approval.

**PII guard (both backends).** Evidence quotes are verbatim PR comments and session turns, which can carry user PII. Before persisting any learning — to repo rules or to AI Knowledge — redact per `Compliance/Privacy`: Firebase UID → short prefix, email → domain only, never a full email / given / family name. `ingest-knowledge` and `record-knowledge` apply this guard themselves; the **direct Notion page edit** path bypasses it, so apply the redaction by hand on that path.

- **Repo writer**: opens a PR against the active repo with edits to `.claude/rules/*.md` (or `CLAUDE.md` if no `rules/` directory). Each addition cites the PR comment URL (when present) or the session anchor. Branch name pattern: `chore/distill-session-<short-sha>`; PR title pattern: `Distill rules from session <session-id>`. Follow the target repo's PR-opening workflow — for cursor-bot-enabled repos (AINative and the Aura app repos) that means open with `gh pr create --draft`, post `bugbot run`, address any findings, then `gh pr ready` before handing the PR back. Don't auto-merge — leave that to the user.
- **AI Knowledge writer**: resolve the destination via [`shared:search-knowledge`](../search-knowledge/), consulting `Guide/Placement Rules` for where a new entry belongs. Route by shape:
  - **Narrative convention** (the common case — a coding convention, a tool-usage pattern): append it as a new section, with a dated `# Sources` line, to the **exact `Procedures/*` / `Tools/*` page approved in Step 4**. Write to that specific page with a direct Notion page edit (default), or with [`shared:ingest-knowledge`](../ingest-knowledge/) **given the approved target page** so it doesn't re-derive placement and re-route to a different page. Pinning the write to the approved destination keeps it canonical with the `search-knowledge` resolution. `record-knowledge` **cannot** write narrative pages, so it is not the path here.
  - **Structured row** (a finished experiment, a signal, a curated learning): delegate to [`shared:record-knowledge`](../record-knowledge/) with the resolved `Learnings/*` or `Signals/*` path and `action: create` (or `update`), attaching the rule body + cited evidence + source link.

## Components

- [`scripts/incident_guard.py`](scripts/incident_guard.py) — refuses incident sessions (copy; canonical in `distill-sessions-daily`).
- [`scripts/classify_session.py`](scripts/classify_session.py) — session shape detector (git commit count + PR discovery + human-vs-bot review-comment count).
- [`scripts/harvest_pr_comments.py`](scripts/harvest_pr_comments.py) — PR inline-comment + reply-thread harvester (thread-ordered, with diff context).

## Triggers

- `/distill-session`
- "distill this session" / "distill the session"
- "what did we learn this session" / "what did we learn"
- "extract learnings" / "capture the learnings"
- "codify the learnings from this PR"
- "turn this into rules" / "persist these learnings"
- Proactive offer after merging a PR with ≥3 reviewer comments

## Out of scope (v1)

- Slack / Linear / Notion-comment sources — first version is PR comments + session transcript + commit messages only
- Auto-taxonomy resolution without a `search-knowledge` round-trip — destinations always go through search-knowledge so the path is canonical

## Pairs with

- [`shared:search-knowledge`](../search-knowledge/) — taxonomy resolver used by the destination router (every cross-cutting write resolves its path here first)
- [`shared:ingest-knowledge`](../ingest-knowledge/) — writer for the **narrative page** branch (`Procedures/*` / `Tools/*`) **when handed the Step-4-approved target page** (it must not re-derive placement); also the single-source ingest path generally
- [`shared:record-knowledge`](../record-knowledge/) — destination writer **only** for the structured `Learnings/*` / `Signals/*` row branch
- `claude-md-management:revise-claude-md` — narrow `CLAUDE.md`-only path (use directly when only that file needs an update)

## Sources

- [PROD-2261](https://linear.app/aura-health/issue/PROD-2261) — implementation ticket for distill-session, parented under [PROD-1686](https://linear.app/aura-health/issue/PROD-1686) (Improve Knowledge Tools and Skills)
- [IntelligenceService#240](https://github.com/AuraHealth/IntelligenceService/pull/240) — dogfood target: PR review produced ~10 rule-worthy patterns the team had no mechanism to capture
- `Procedures/AI Native/Skill Authoring` (AI Knowledge) — naming, scope, and routing conventions this skill is built against
