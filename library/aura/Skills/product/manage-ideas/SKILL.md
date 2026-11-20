---
name: manage-ideas
description: >
  Manage the `Learnings/Loop/{Loop}` Notion backlog — review pending ideas, cluster
  duplicates, approve / reject with reviewer notes, list / show individual rows. The
  clustering pass is Claude-orchestrated; the row-level writes go through the
  [`scripts/manage_ideas.py`](scripts/manage_ideas.py) helper which talks to Notion REST
  directly. Use when the user says "review the ideas", "curate the backlog", "approve this
  idea", "reject the duplicates", "list pending ideas", "show idea {title}", or "what's in the
  Aurie ideas database". Default target is the Aurie Features Ideas database; pass `--data-source-id
  {id}` for the other loops once they exist.

metadata:
  version: "3.10.0"
knowledge:
  reads:
    - Guide/Index            # baseline routing aid (universal)
    - Learnings/Loop/Aurie Features Ideas
    - Tools/Notion                   # Notion REST / MCP access pattern (NOTION_TOKEN)
  writes:
    - Learnings/Loop/Aurie Features Ideas
---

# manage-ideas

Day-to-day operations on the ideas backlog. Splits cleanly into two layers:

1. **Mechanical** (the Python helper) — list / show / approve / reject / set-status. Deterministic single-row reads + writes.
2. **Judgment** (this SKILL.md, executed by Claude) — clustering, duplicate detection, multi-row reasoning about which ideas to approve / reject / keep open. Calls into the helper for execution.

The Notion row is the source of truth for `Status` and `Quick status note`. Once set, automation never overwrites them — `record-ideas` deliberately ignores Status on re-ingest.

## When to invoke

| User says | Run |
|---|---|
| "review the ideas", "curate the backlog" | **Review pass** (below) |
| "what's pending?", "list ideas", "show all approved" | `manage_ideas.py list [--status pending\|approved\|...]` |
| "show idea <ref>", "what's in idea <fingerprint>" | `manage_ideas.py show <ref>` |
| "approve idea <ref>" | `manage_ideas.py approve <ref> --by <who> --note "..."` |
| "reject idea <ref>" | `manage_ideas.py reject <ref> --reason <reason> --by <who> --note "..."` |
| "mark idea <ref> as released" | `manage_ideas.py set-status <ref> released --note "..."` |
| "which rejections have no reason?" | `manage_ideas.py list --missing-reason` |

`<ref>` is permissive: the **Idea ID** (`42`, `#42`, `IDEA-42` — the human-friendly address, preferred), a full page UUID, the fingerprint (12-hex dedup key), or a case-insensitive title substring. Ambiguous title matches fail loudly with the candidate list, keyed by Idea ID.

## Prerequisites

- `NOTION_TOKEN` set (see AINative root [CLAUDE.md → "Notion REST integration"](../../../../CLAUDE.md#notion-rest-integration)). REST is required — the MCP `notion-search` can't enumerate a database and won't filter on Status / Category cleanly enough for review work.
- [`scripts/manage_ideas.py`](scripts/manage_ideas.py) and its [`scripts/notion_rest.py`](scripts/notion_rest.py) copy alongside this SKILL.md.

## Thread marks

`approve`, `reject` and `set-status abandoned` also mark the idea's Slack thread, so the
parent post says where the idea stands without anyone opening it:

| Status | Mark on the parent post |
|---|---|
| `approved` | 🚀 `rocket` — Gate 1 said build it |
| `rejected`, `abandoned` | ❌ `x` — closed |

One mark at a time: landing one takes the other off, so a row that moves
approved → rejected shows a cross and nothing else. The mark is written **after** the
Notion row and is best-effort — no `SLACK_BOT_TOKEN`, no thread on the row, or a
missing `reactions:write` scope is reported on the `thread:` line, never raised over a
decision already recorded. Re-running is idempotent (`already_reacted` is "on").

The harvester reads the loop's own marks back as marks: a reaction from the bot user
is never a person's signal. ✅ stays a human's mark for *done* (released and analysed).

## CLI quick reference

```bash
SCRIPT=plugins/product/skills/manage-ideas/scripts/manage_ideas.py

# List
python3 $SCRIPT list                       # all rows, sorted by status then screen
python3 $SCRIPT list --status pending
python3 $SCRIPT list --category paywall
python3 $SCRIPT list --screen welcome

# Audit: rejected / deferred rows carrying no Rejection reason. Exits 1 if any.
python3 $SCRIPT list --missing-reason

# Show one row in full
python3 $SCRIPT show <ref>

# Approve — --by and --at record who settled it and when
python3 $SCRIPT approve <ref> --by steve --note "Bundle with home dashboard refresh"

# Reject — --reason is REQUIRED and comes from a fixed vocabulary
python3 $SCRIPT reject <ref> --reason duplicate --by steve --at 2026-04-17 \
  --note "Duplicate of <other-fp>"

# Defer — also requires a reason
python3 $SCRIPT set-status <ref> deferred --reason timing --by steve \
  --note "Blocked on manual journal entries"

# Manual status flip (rare — prefer approve/reject)
python3 $SCRIPT set-status <ref> released --note "Landed in 1.31.0"
```

All writes are single-row PATCH calls. Notion is the canonical store — there's no local state file to keep in sync.

### Recording the decision, not just the outcome

A verdict writes four properties. `Status` is the outcome and `Quick status note` the prose, but the two that make the loop compound are:

- **`Rejection reason`** — a fixed vocabulary (`already tested`, `no longer valid`, `low impact`, `off-strategy`, `tech cost`, `duplicate`, `timing`, `other`), so the ideation step can *filter* on it. `already tested` and `no longer valid` retire an idea; `timing` and `tech cost` mean re-propose later. Free-text prose cannot carry that distinction to a machine. **The CLI refuses to write `rejected` or `deferred` without it** — this is the one enforced rule, because a reasonless rejection is exactly the failure the database exists to prevent.
- **`Decided by` / `Decided at`** — the record of a **person's reply in the Gate 1 thread**, and of nothing else. `--by` takes a Notion member name or email (`steve`, `<redacted-email>`) and resolves against the workspace; an ambiguous match fails with the candidates rather than guessing. `--at` backdates, which is the normal case when transcribing a Slack thread months later, so it needs a decider too.

  **Naming someone requires `--decided-in <permalink>`** — the reply's own Slack “Copy link”. The CLI refuses `--by` / `--by-id` without it, checks it is a link rather than a sentence, and writes it into the row's note, so the name has its evidence beside it instead of in someone's shell history. A requirement nobody can check is ceremony.

`Decided at` is **write-once**: today's date fills it only when it is empty, so re-running `reject` to fix a note, or moving `approved` → `deferred` later, never rewrites when the call was actually made. An explicit `--at` always wins, because that is someone asserting the date on purpose.

Only `approved`, `rejected` and `deferred` count as verdicts. `merged` is a *build* milestone in this lifecycle (`in_progress` → `in_review` → `merged` → `released`) meaning the PR landed — **not** "folded into another idea". An idea that duplicates another is `rejected --reason duplicate`, never `merged`.

**Agent triage stamps nobody.** Re-validating the backlog, deferring, rejecting before anything is posted — none of it is a decision a person made, so it writes `Status`, `--reason` and a `--note` that says it was triage, and leaves `--by` off. An unattributed verdict is honest. A row carrying a name the loop never asked is a false record, and Notion pages that person for every one of them.

## Review pass (Claude-orchestrated)

When the user says "review the ideas" or "curate the backlog", do the following in order. Don't skip steps and don't act from a stale list — re-fetch between batches if you've made changes.

### Step 1 — Snapshot the pending pool

```bash
python3 plugins/product/skills/manage-ideas/scripts/manage_ideas.py list --status pending
```

Capture the full list. For each row use `show <fingerprint>` to load the Description / Rationale / Hypothesis when the title alone isn't enough to judge.

### Step 2 — Cluster, don't sort by screen

Group ideas by **theme**, not by screen. Same screen often has unrelated ideas (a copy tweak, a layout tweak, a paywall test); different screens often share a theme (every "add testimonial" idea across home / paywall / settings). Themes the team commonly cares about:

- Social proof / testimonials placement
- Streak / progress / habit reinforcement
- Paywall friction reduction
- Onboarding shortening
- Notification timing / copy
- Sign-in friction (social auth placement, password rules)

### Step 3 — Classify each cluster

| Cluster shape | Action |
|---|---|
| **Near-duplicates** — same pattern across surfaces, indistinguishable rationale | Pick one canonical keeper. `reject` the rest with note `"Duplicate of <keeper-fingerprint>"`. The keeper stays pending. |
| **Mutually exclusive alternatives** — two competing approaches to the same problem | Keep both pending. Annotate with cross-reference notes (`set-status <ref> pending --note "Alternative to <other-fp>"`). Team picks one to A/B. |
| **Coherent epic** — multiple surfaces of one feature (e.g. streaks shown on home + paywall + push) | Keep all pending. Annotate each with a shared note tagging the epic. |
| **Standalone** | Leave pending, no action needed. |

### Step 4 — Apply, then verify

Execute the approve / reject calls. After the batch, re-list pending to confirm the counts moved.

### Step 5 — Report

Summarise to the user:

- Clusters found (theme + ids)
- What was rejected (and why)
- What was annotated (cross-references / epic tags)
- What remains untouched

Keep the recap tight — the user is going to scan it to decide whether to push further or stop.

## Invariants

1. **Never clobber human curation.** `Status` and `Quick status note` on a row may have been set by a human reviewer in Notion UI — automation (this script and `record-ideas`) must not overwrite them silently. `approve` / `reject` *do* overwrite, but they're explicit human-driven verbs invoked by name.
2. **Re-fetch after writes.** When the review pass writes a batch, list-based state from before the writes is stale. Re-run `list` before the next round of decisions.
3. **Reasons are required for rejections; notes are expected.** The CLI enforces `--reason` on `rejected` and `deferred` — that one is not negotiable. `--note` is still unenforced, but a rejected row whose only explanation is a one-word enum is hostile to the next reviewer: always write both. `list --missing-reason` audits the backlog and exits non-zero, so it works as a gate.
4. **Don't auto-approve.** Approvals always come from a human (or the user explicitly directing Claude). Even high-confidence ideas wait for an explicit `approve` call.

## Domain

`Domain` is what `guard-collision` filters on and what the sim-test priors block groups a screen's history by. An empty one is not cosmetic: `build-idea plan` renders `domain: ?` and the collision gate cannot be driven from the row at all.

```bash
python3 $SCRIPT backfill-domains            # dry run
python3 $SCRIPT backfill-domains --apply
python3 $SCRIPT set-domain <ref> --domain profile
```

`backfill-domains` derives the value from `Screen` using `SCREEN_DOMAIN_PATTERNS` — a deliberate copy of `DOMAIN_PATTERNS` in AuraConsumerApp's `sim_priors.py`, which is canonical because the ideation analyzer tags *new* ideas with it. Backfilling with any other mapping would file old rows under names the new ones never use, and a screen's history would split across two domains in the block that exists to show it whole. Copied rather than imported because the two live in different repositories; if one changes, change both.

**There is no `welcome` domain, and adding one would be a mistake.** `welcome_screen` is the first screen of onboarding and the onboarding pattern claims it. A separate value would split one funnel in two.

Where `sim_priors` returns `"other"` for an unclaimed screen, this returns **None** and the row is skipped with a note. The two are answering different questions: the priors block needs a bucket for every screen it renders, while writing `"other"` here would record a *decision* that the screen has no domain — and `other` is a real value the collision gate treats as a domain. An unmatched screen should be looked at, then set with `set-domain`.

## Related skills

- [`product:record-ideas`](../record-ideas/SKILL.md) — adds new rows. Does not touch Status / Quick status note on existing rows.
- [`product:share-ideas`](../share-ideas/SKILL.md) — posts pending rows to `#ai-ideas` for async team input. Run before a review pass to gather extra signal.

## References

- Notion database: [Aurie Features Ideas](https://www.notion.so/7e721e75374645a7acfa085e3aeca6df) (data source `8d14f3eb-0c55-4f25-9322-ab2b2a69f092`)
- Parent path: [`Learnings/Loop/Aurie Features Ideas`](https://www.notion.so/35d75e23af2081408320f29b9d750f1c) — process documented on the [Loop page](https://www.notion.so/35d75e23af208195b333d4158c46aed2)
- Linear: [PROD-1700](https://linear.app/aura-health/issue/PROD-1700) (parent [PROD-1686](https://linear.app/aura-health/issue/PROD-1686)).

