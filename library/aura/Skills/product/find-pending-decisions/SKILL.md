---
name: find-pending-decisions
description: >
  Surface open product decisions buried in comments on active Linear issues. Fetches all
  comments from the last 7 days across the current Product/Design cycle (parent issues and
  sub-issues across all teams), skips resolved threads, and clusters unresolved questions
  about UX, feature scope, flows, or items awaiting product review into at most three
  categories with links back to the source issues. Trigger when the user says things like
  "what's pending product input", "find pending product decisions", "what does product need
  to decide", "open product questions on active work", "what's blocked on product", "pending
  UX decisions", or "review open questions across in-flight issues". Do NOT use for daily
  standup summaries (use `daily-status`) or for creating new Linear issues from a
  conversation (use `add-linear-issues`).
metadata:
  version: "3.0.1"
knowledge:
  reads:
    - Guide/Index   # baseline routing aid (universal)
    - Tools/Linear                   # Linear MCP / GraphQL access
---

# Find Pending Product Decisions

Runs a single Linear GraphQL query to pull all recent comment activity across the current
cycle, skips resolved threads, and surfaces every open product decision still living in a
comment thread. Output is a categorised list of unresolved questions with direct links to
the source issues.

## Goal

Produce a single readable digest answering: **"Across everything in the current cycle,
what is product still on the hook to decide?"**

## Inputs

No required inputs. Optional refinements the user may pass:

- A project filter (e.g. "just Aurie AI - Mobile") — narrow the issue set after fetching.
- An assignee filter — narrow to issues a specific person owns.

## Workflow

### Step 1: Fetch data via Linear GraphQL

Run the bundled script to pull the active cycle and all its issues with comment threads:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/find-pending-decisions/scripts/linear_graphql.py
```

The script:
- Authenticates with `LINEAR_API_KEY` from env (`~/.claude/settings.json`).
- Finds the active cycle for the Product/Design team.
- Fetches all non-Done/non-Cancelled parent issues in the cycle (excluding issues labelled
  **Support**), plus their sub-issues across **all teams** (not just Product/Design), with
  full comment threads.
- Strips bot/integration comments (Linear, GitHub, Slack, Cursor, GitLab).
- Strips comments where `resolvedAt` is set — these are threads the team has already marked resolved via Linear's "Mark as Resolved" button.
- Returns JSON: `{ cycle, issues: [{ identifier, title, url, state, comments, children }] }`

**If the script errors:**
- `no_key` — ask the user to add `LINEAR_API_KEY` to `~/.claude/settings.json` env block
  and restart Claude Code.
- `no_active_cycle` — stop and tell the user there is no current cycle for Product/Design.

Apply any user-supplied project or assignee filters to the returned issue list now.

**Volume guard.** If total issues + sub-issues exceeds ~150, warn the user and suggest
re-running with a project filter.

### Step 2: Scope to the last 7 days

From the full comment data, retain only threads where **at least one comment was posted
in the last 7 days** (relative to today's date). Discard issues and sub-issues whose
entire comment history predates that window — they have had no recent activity and are
not relevant to current momentum.

For threads that pass this filter, keep **all comments** (not just the recent ones) so
that the resolution check in Step 3 has full context.

> **Field conventions (from script output)**
> - `comments` — list of `{ id, body, createdAt, author }` (human comments only, bots stripped)
> - `children` — sub-issues, same shape; no further nesting
> - `createdAt` — ISO-8601 UTC string
> - Comment deep-link: `{issue.url}#comment-{comment.id}`

### Step 3: Skip resolved threads

Before analysing for pending decisions, classify each thread as **resolved** or **open**.
A thread is resolved — skip it entirely — when any of the following hold:

- The final substantive human comment in the thread **closes the question**: contains
  phrases like "decided", "going with", "confirmed", "approved", "will do", "done",
  "shipped", "no longer needed", or an explicit answer to a prior question.
- Every open question in the thread has a direct reply that answers it (read the thread
  sequentially — a later comment that resolves an earlier question marks that question as
  closed).
- There are **no questions or asks** in the thread at all — purely informational updates
  with no unresolved request for product input.

A thread is **not** resolved by:
- A preference vote ("I'd go with X", "personally I think Y") — this signals an opinion,
  not a confirmed decision. The question remains open until someone with authority closes it.
- Acknowledgement without direction ("sounds good", "thanks") from someone other than the
  decision-maker the question was addressed to.
- A follow-up question in response to the original — this means the thread is actively
  open, not resolved.

If a thread has **some** resolved questions and **some** open ones, keep it but surface
only the open items.

### Step 4: Identify pending product decisions

For each remaining open thread, scan for unresolved items product still owns. Signals:

- **Direct questions** about UX, feature scope, copy, behaviour, or flow
  ("Should onboarding skip step 3 for returning users?", "What's the empty state copy?")
- **Explicit asks for product input** — `@product`, `@<PM name>`, "needs product",
  "PM call", "product decision", "design decision"
- **Open trade-offs** flagged but not resolved ("we could go A or B — thoughts?",
  "two options — which one?")
- **Awaiting-review markers** — "ready for product review", "please QA",
  "let me know if this looks right"
- **TBDs / TODOs** addressed to product ("TBD: confirm with PM", "TODO: get sign-off")

Pair each surviving signal with:
- A **direct link to the specific comment** — construct as `{issue_url}#comment-{comment.id}`.
  Use the sub-issue URL if the signal came from a child thread; use the parent URL if it
  came from the parent thread.
- The issue identifier and title (for the bullet label).
- A one-line paraphrase of the open question (don't paste the full comment).
- The asker's name and date.
- The handle of the person the question is directed at (e.g. `@steve`, `@alyssa`), if
  identifiable from the comment text.

If two comments or threads are discussing the same question (same topic, same open decision),
surface only the most recent one. Drop the earlier duplicate entirely.

Aim for **high precision over recall** — a noisy digest gets ignored. If a thread is
ambiguous, drop it.

### Step 5: Cluster into at most three categories

Group the surviving items into **2–3 categories** that emerge from the data — do not
force pre-defined buckets. Common natural clusters:

- **UX & flow** (navigation, screen sequencing, state transitions)
- **Copy & messaging** (microcopy, tone, error strings)
- **Scope & cut-lines** (what's in v1, what's deferred)
- **Edge cases & error handling**
- **Awaiting product review** (catch-all for items pending sign-off)

If the data only supports one category, ship one. Never ship more than three — collapse
the smallest bucket into the next-closest one. Order by item count descending.

### Step 6: Render the digest

```markdown
# Pending product decisions — {{N}} open across {{M}} issues

## 1. {{Category name}} ({{count}})

- **[PROD-XXXX]({{issue url}})** — {{issue title}}
  - [{{one-line paraphrase}}]({{issue url}}#comment-{{comment id}}) · Asked by {{name}} on {{date}} → @{{addressee handle}}

## 2. {{Category name}} ({{count}})
...

---
_Scanned {{P}} parent issues and {{Q}} sub-issues in {{cycle name}} · comments from the
last 7 days · {{date}}_
```

Rules:
- Issue URLs are the `url` field from the script output — use as-is, never construct manually.
- One bullet per issue; list each open question as a sub-bullet. An issue may appear in
  multiple categories if its questions fall into different buckets.
- Keep paraphrases ≤ 100 characters — the reader clicks through for context.
- If nothing is found after filtering and resolution checks:
  `No pending product decisions found in the last 7 days across {{P}} parent issues and {{Q}} sub-issues.`

## Output

A markdown digest as shown in Step 6. Do not create Linear issues, post to Slack, or
write to Notion — this skill is read-only. If the user asks for follow-up actions, suggest
the appropriate sibling skill (`add-linear-issues` to log follow-ups, `daily-status` to
fold findings into standup).

## Edge cases

- **`LINEAR_API_KEY` not set.** Stop after Step 1 with the error message from the script.
- **No active cycle.** Stop after Step 1 with a clear message.
- **Parent issue with no sub-issues.** Still scan its own comment thread.
- **Issue with no comments in the last 7 days.** Omit — nothing recent to surface.
- **All threads resolved.** Output the "no pending decisions" message.
- **Question answered later in the same thread.** Step 3 marks it resolved — skip it.
- **Status names differ in workspace.** If statuses beyond "Done"/"Cancelled" signal
  completion, the script's `EXCLUDED_STATES` list can be updated.

## Non-triggers

Do not use this skill for:
- **Daily standup digests** — use `daily-status`.
- **Creating Linear issues from a conversation** — use `add-linear-issues`.
- **Implementing a single Linear ticket** — use `linear-task`.
- **Preparing an engineering handoff** — use `eng-handoff`.
