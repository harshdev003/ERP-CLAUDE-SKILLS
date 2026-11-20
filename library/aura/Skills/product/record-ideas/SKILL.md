---
name: record-ideas
description: >
  Record a product idea (or a batch of them) into the `Learnings/Loop/{Loop}` Notion database.
  Source-agnostic — handles a sim-test session's `findings_ideas.json`, a hand-written JSON
  list, a one-time migration dump, or a single inline idea. Dedupes by deterministic
  `Fingerprint` so the same idea from multiple sources collapses to one row. Use when the user
  says "record this idea", "log this idea", "add to the ideas backlog", "ingest the ideas from
  this sim-test session", "pull ideas into Notion", or pastes an idea description in chat.
  Default target is the **Aurie Features Ideas** database; pass `--data-source-id {id}` for the other
  loops once they land.

metadata:
  version: "3.8.0"
knowledge:
  reads:
    - Guide/Index            # baseline routing aid (universal)
    - Learnings/Loop/Loop Learnings  # the implication shown on an already-answered idea
    - Learnings/Loop/Aurie Features Ideas  # check the target database lives where the skill thinks it does
    - Tools/Notion                   # Notion REST / MCP access pattern (NOTION_TOKEN)
  writes:
    - Learnings/Loop/Aurie Features Ideas
---

# record-ideas

Adds new ideas to the `Learnings/Loop/<Loop>` Notion database. Idempotent — re-running on the same input is a no-op once everything is in sync.

## `expectedImpact` — what it moves, and what it might cost

Optional, and written to the row's **Expected impact** property. Taken as
`{trial, paid, session, risk}` from `product:brainstorm-ideas`, or as a plain string when
a caller already has one; anything else is dropped rather than guessed at, because a
malformed prediction reads like a real one.

Predicting only the metric an idea is *for* is how a win hides a cost: `dailyPractices3`
had its best arm on conversion and its worst on engagement. `risk` is the field that
catches that, so it is the one worth insisting on.

## `proposedBy` and the proposal fields

Two ideation routes feed this backlog — Claude's `product:brainstorm-ideas` and Viktor's
ideation skill — and the loop compares what each gets approved and what wins. That
comparison needs the route on the row, so every batch should carry `proposedBy`
(`Proposed by` select). The rest — `portfolio`, `betSize`, `confidence`, `evidenceGrade`,
`variants`, `testPlan` — land in their own properties so Gate 1 reads the test plan on
the row, not in a chat log. `variants` and `testPlan` render to rich text capped at
Notion's 2,000 characters. A property the target database lacks is dropped with a
warning rather than failing the create — the idea still lands.

## Domain, screenshot, and the prior-art warning

Three things the sim-test ideas analyzer now supplies, and what this does with them.

**`domain` → the Notion `Domain` select.** Validated against the enum before the write, because a variant spelling does not error — it silently creates a new select option and the row lands somewhere nobody filters on. The analyzer resolves the domain from the screen id, so the value and the priors that idea was judged against come from the same table.

**`screenshotPath` → the `Screenshot` files property.** Uploaded through Notion's three-step file API (create upload → POST bytes → attach), and attached in the same `POST /pages` as the row, so a row never briefly exists without the screen it was observed on. A failed upload prints a warning and records the idea anyway — the screenshot is enrichment, and losing it must not cost the idea.

**`plan` warns on an idea we have already answered.** Fingerprint dedup hashes `screen + category + title`, so it only catches the *same wording*. An idea reworded since the team rejected it hashes differently and sails through — which is how a paywall testimonial came back after `aiTestimonials` had already lost. `plan` now compares each new title against every `rejected` / `lost` row and prints the prior reason:

```
ALREADY ANSWERED — read these before applying:
  ! Show a customer quote above the plan cards
      looks like: Add a testimonial or outcome quote above plan cards
      already tested — aiTestimonials, 11 Aug to 1 Sep: … paid cancellation rose +55.5% at 95% …
```

When the prior row carries a `Learning` (written by `learn-idea` when its experiment ended),
the warning shows its **implication** — what the result means for the next idea on that
surface — instead of the status note.

It is a **warning, not a skip**. Near-duplicate is a judgement call, and a human making it with the prior reason in front of them beats a similarity threshold making it silently.

The comparison deliberately ignores the screen. Screens get renamed — the paywall rows the team answered in April sit on `paywall_container_screen`, while a run today reports `packagePaywall_screen` for the same surface. A screen-equality guard silenced the warning on exactly the case it exists for, which is what testing against the real backlog showed.

## Two ids, two jobs: `Idea ID` addresses, `Fingerprint` dedups

An idea has two identifiers, and they do different things:

- **`Idea ID`** (`IDEA-42`) — the **address**. A Notion auto-incrementing ID, minted once on creation, immutable, and human-friendly ("approve idea 42"). It is what every skill, Slack thread and Linear card should reference. Skills resolve a row by it: `42`, `#42`, or `IDEA-42` all work, alongside the fingerprint and a title substring for older references.
- **`Fingerprint`** (`sha1(screen+category+title)[:12]`) — the **dedup key**. Content-derived, so the same idea from a sim-test run and a brainstorm collapses to one row. Computed on CREATE and then frozen — an update never rewrites it.

The two are separate on purpose. The fingerprint's inputs are editable, so deriving an address from them is fragile — a rename re-hashes the idea and a reference computed from the new title resolves to nothing. Addressing rides on the immutable `Idea ID` instead, leaving the fingerprint a pure dedup key. **Never address a row by recomputing its fingerprint from the current title — use the `Idea ID`.**

**`plan` detects a rename rather than duplicating it.** A renamed idea coming back through record-ideas hashes to a fingerprint that matches no row, so it looks like a would-be CREATE. When such a row's **screen matches an existing row and its title overlaps it strongly** (a higher bar than the prior-art warning), `plan` marks it a rename-suspect and **skips it by default**, so a rename folds back onto the row it belongs to (keeping its `Idea ID`) instead of minting a second:

```
LIKELY RENAME — skipped so a renamed idea does not land as a duplicate.
  ~ Reframe the greeting toward aspiration instead of the problem
      looks like a rename of `0a05fffc41b9`: Reframe greeting from problem to aspiration
```

If it is genuinely a new idea, re-run with `--allow-similar` to record it. If it is the same idea, edit the existing row rather than recording this one. The detector requires the **same screen** — a rename that also changed the screen falls through to CREATE (the surface is the stable anchor; the cross-screen prior-art warning still backstops answered rows).

## Where ideas come from

The skill is **source-agnostic**. Today's inputs:

- **Sim-test analyzer** — `AuraConsumerAppSimTests/Sessions/<id>/findings_ideas.json` after a sim-test run.
- **One-time JSON migrations** — e.g. the original [`AuraConsumerAppSimTests/ideas/ideas.json`](https://github.com/AuraHealth/AuraConsumerApp/blob/main/AuraConsumerAppSimTests/ideas/ideas.json) on the first move into Notion ([PROD-1700](https://linear.app/aura-health/issue/PROD-1700)).
- **Manual / interactive** — the user describes an idea in chat and asks Claude to record it.
- **Future** — Slack thread, customer feedback, internal brainstorm doc. Same skill, same fingerprint.

The fingerprint (`sha1(screen + category + title)[:12]`) collapses cross-source duplicates so the database doesn't grow noisy.

## When to invoke

Trigger on:

- `record this idea ...`
- `log this idea ...`
- `add to the ideas backlog`
- `pull the ideas from this sim-test session into Notion`
- `migrate AuraConsumerApp/.../ideas.json to the Aurie Features Ideas database`
- the user pasting an idea description and asking to track it

If the user just describes an idea conversationally without an explicit "record" verb, ask once: "Want me to record this as a row in the Aurie Features Ideas database?" before running.

## Prerequisites

- `NOTION_TOKEN` is set (lives in `~/.claude/settings.json` env block). The skill **requires** REST access for dedup — there is no MCP fallback for `query_data_source`, and the `notion-search` MCP can't enumerate database rows. If the token is missing, prompt the user to add it via `update-config` and stop. See the AINative root [CLAUDE.md → "Notion REST integration"](../../../../CLAUDE.md#notion-rest-integration) for setup.
- The skill's [`scripts/record_ideas.py`](scripts/record_ideas.py) and its [`scripts/notion_rest.py`](scripts/notion_rest.py) copy are present alongside this SKILL.md.

## Inputs

Three input modes — all converge to the same canonical shape internally.

### `--from-json <path>`

Path to a JSON array of idea objects. Each object accepts:

```json
{
  "title": "...",                  // required
  "description": "...",            // recommended
  "screen": "welcome_screen",      // optional — screen identifier when scoped
  "category": "feature",           // optional — see enum below
  "lever": "conversion",           // optional — see enum below
  "impact": "high",                // optional — high | medium | low
  "effort": "M",                   // optional — S | M | L
  "rationale": "...",              // optional
  "hypothesis": "...",             // optional
  "status": "pending",             // optional — defaults to pending
  "statusNote": "...",             // optional
  "firstSeenAt": "2026-04-17",     // optional ISO-8601 (date or datetime)
  "lastSeenAt": "2026-04-17",
  "slack":  { "permalink": "..." },// optional — preserved if Notion's Slack thread is blank
  "linear": { "issueUrl": "..." }  // optional — preserved if Notion's Linear issue is blank
}
```

`product:brainstorm-ideas` also emits the proposal fields below. All are optional and
written on CREATE only; the full shape and what each means is in that skill.

```json
{
  "scope": "new",                  // screen | feature | new
  "expectedImpact": { "trial": "...", "paid": "...", "session": "...", "retention": "...", "risk": "..." },
  "priorArt": "...",
  "proposedBy": "claude",          // the ideation route — claude | viktor | sim-test | a person
  "portfolio": "evidence",         // evidence | adjacent | moonshot
  "betSize": "medium",             // small | medium | large
  "confidence": "medium",          // high | medium | low
  "evidenceGrade": "B",            // A | B | C | D
  "variants": ["b: control", "a: ..."],
  "testPlan": { "primaryMetric": "...", "direction": "up", "guardrails": ["...", "..."],
                "instrumentation": "...", "decisionWindow": "...", "falsification": "..." }
}
```

Recognises both:

- The **legacy** AuraConsumerApp `ideas.json` shape (with `status`, `slack.permalink`).
- The **sim-test session** `findings_ideas.json` shape (no status — defaults to `pending`).

### `--idea '<json>'`

Inline single-idea JSON. Useful when Claude is invoked interactively.

### `--from-stdin`

Same JSON array as `--from-json`, piped to stdin. Useful for chaining.

## Enums

| Field | Allowed values | Strict? |
|---|---|---|
| `category` | Free text — common values: `feature`, `friction`, `ui`, `copy`, `navigation`, `onboarding`, `paywall`, `retention`, `notification`, `gamification`, `shareability`, `referral`, `engagement`, `social_proof`, `discovery`, `personalization`, … | No (rich_text in Notion) |
| `lever` | Free text — common values: `conversion`, `activation`, `retention`, `engagement`, `growth` | No (rich_text in Notion) |
| `impact` | `high`, `medium`, `low` | Yes (select) |
| `effort` | `S`, `M`, `L` | Yes (select) |
| `portfolio` | `evidence`, `adjacent`, `moonshot` | Yes (select) |
| `betSize` | `small`, `medium`, `large` | Yes (select) |
| `confidence` | `high`, `medium`, `low` | Yes (select) |
| `evidenceGrade` | `A`, `B`, `C`, `D` | Yes (select) |
| `proposedBy` | Free text, lowercased — `claude`, `viktor`, `sim-test`, a person | No (select, options created on write) |
| `status` | The Loop lifecycle: `pending`, `shared`, `approved`, `rejected`, `deferred`, `in_progress`, `in_review`, `merged`, `released`, `experiment_running`, `won`, `lost`, `inconclusive` (default: `pending`; everything past `shared` is written by the loop's own legs, never by an ingest) | Yes (select) |

`category` and `lever` are intentionally free-text: the analyzers and humans both generate a wide vocabulary, and forcing a fixed enum kills descriptive power. The script normalises whitespace but never rejects on these fields. The strict-select fields (`impact`, `effort`, `status`) drive Notion's UI colouring and downstream sorting, so they're gated — a bad value causes a clear per-row validation error so the source can be fixed.

## Workflow

### Step 0 — Confirm the target

By default the skill writes to the **Aurie Features Ideas** database (`data_source_id = 8d14f3eb-0c55-4f25-9322-ab2b2a69f092`). Each loop gets its own database under `Learnings/Loop` — pass `--data-source-id <id>` for the others once they exist. The script's `AURIE_FEATURES_DS` constant is the source of truth.

### Step 1 — Plan (dry-run)

Always start with `plan`. It reads the source, computes fingerprints, queries Notion for existing rows, and prints a CREATE / UPDATE / SKIP plan without writing.

```bash
python3 plugins/product/skills/record-ideas/scripts/record_ideas.py \
  plan --from-json <path-to-source-json>
```

The plan tells you exactly what would happen. Read it back to the user before applying.

### Step 2 — Apply

Once the plan looks right, run `apply --confirm`:

```bash
python3 plugins/product/skills/record-ideas/scripts/record_ideas.py \
  apply --from-json <path-to-source-json> --confirm
```

Without `--confirm`, `apply` is the same as `plan`. The two-step gate is intentional — Notion writes are user-visible and not cheap to undo at scale.

### Step 3 — Report

The script prints a summary line per row and a totals line. Surface it to the user verbatim.

If `failed > 0`, the run is partial — re-run with the same input and the script will retry only the still-needed rows (idempotent by fingerprint).

## Classification rules

For each input idea:

| Outcome | Condition | Action |
|---|---|---|
| **CREATE** | Fingerprint not present in Notion | Insert a new row with `Status = pending` (or the explicit status on the input). `First seen` and `Last seen` default to today if absent. |
| **UPDATE** | Fingerprint present AND the input carries `Slack thread` / `Linear issue` that Notion doesn't AND/OR `Last seen` is older than today | PATCH only the missing/stale fields. **Never** overwrite `Status` or `Quick status note` — those are owned by Notion via `manage-ideas`. |
| **SKIP** | Fingerprint present, all enrichment in sync, `Last seen == today` | No-op. |
| **RENAME-suspect** | Fingerprint absent BUT same screen + strong title overlap with an existing row | Skipped (not recorded), so a renamed idea folds back onto its row. `--allow-similar` records it as a CREATE instead. |
| **DUPLICATE-IN-BATCH** | Two input rows share a fingerprint | First is CREATE/UPDATE, rest are SKIP with `duplicate of earlier input row` reason. |

The rule "never overwrite human curation" is the load-bearing invariant. If a future need arises to bulk-correct a typo in `Description` post-creation, add a dedicated `--force-fields` flag rather than widening this skill's default update surface.

## Listing the current database

```bash
python3 plugins/product/skills/record-ideas/scripts/record_ideas.py list
```

Prints every row's status and title. Cheap way to confirm the apply landed correctly without bouncing to Notion UI.

`--proposed-by <route>` and `--since <YYYY-MM-DD>` narrow it to one ideation route's rows first seen on or after a date — how `brainstorm-ideas` counts its week against the 10-idea cap.

For richer review (clustering, approve / reject, status flips) use [`manage-ideas`](../manage-ideas/SKILL.md). For posting to Slack use [`share-ideas`](../share-ideas/SKILL.md).

## Idempotency contract

Re-running with no upstream change must produce `0 created, 0 updated, all-rows skipped`. Test this on any change to the dedup logic:

1. Run `apply --confirm` once → expect non-zero creates.
2. Run `apply --confirm` again immediately → expect `0, 0, all`.

Any deviation is a bug in `plan_writes` or `load_existing`.

## Cowork caveat

`mcp__workspace__bash` strips host env (PROD-1305), so `NOTION_TOKEN` won't reach Cowork sandboxes. This skill **cannot** degrade to MCP — REST is required for dedup. If you hit this, paste-export the token in the workspace bash session, or run the skill from a normal Claude Code session.

## Related skills

- [`product:manage-ideas`](../manage-ideas/SKILL.md) — review / cluster / approve / reject on the same database.
- [`product:share-ideas`](../share-ideas/SKILL.md) — post pending rows to `#ai-ideas` for async team input.
- [`shared:retrieve-knowledge`](../../../shared/skills/retrieve-knowledge/SKILL.md) — the `Learnings/Loop/{Loop}` schema is registered in its [`learning-schemas.md`](../../../shared/skills/retrieve-knowledge/references/learning-schemas.md), so any skill can declare `Learnings/Loop/Aurie Features Ideas` in its `knowledge.reads` to pre-fetch the backlog.

## References

- Notion database: [Aurie Features Ideas](https://www.notion.so/7e721e75374645a7acfa085e3aeca6df) (data source `8d14f3eb-0c55-4f25-9322-ab2b2a69f092`)
- Parent path: [`Learnings/Loop/Aurie Features Ideas`](https://www.notion.so/35d75e23af2081408320f29b9d750f1c) — process documented on the [Loop page](https://www.notion.so/35d75e23af208195b333d4158c46aed2)
- Linear: [PROD-1700](https://linear.app/aura-health/issue/PROD-1700) (parent [PROD-1686](https://linear.app/aura-health/issue/PROD-1686)).
