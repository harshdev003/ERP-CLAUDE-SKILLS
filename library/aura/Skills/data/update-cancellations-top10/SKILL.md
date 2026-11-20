---
name: update-cancellations-top10
description: >
  Update the "Top 10 Learnings — Cancellations" section on the AI Knowledge
  Learnings page from the current state of the Signals/Cancellations
  database. Reads every row, clusters by Reason / Signal / Archetype /
  Support Style / Acquisition to surface cross-row patterns (e.g.
  "price exits concentrate in deepProcessor archetype with high engagement")
  and rewrites the inline section above the Cancellations database in
  place. Ships fewer than 10 bullets when the corpus is small (under ~15
  rows); grows to 10 once the DB matures. Never pads with weak bullets.
  Trigger when the user says "update cancellations top 10", "refresh
  cancellation learnings", "rerun cancellations top 10", "resynthesize
  cancellation learnings", or asks to update the Top 10 Cancellations
  section on the Learnings page.
metadata:
  version: "3.0.2"
knowledge:
  reads:
    - Guide/Index                # baseline routing aid (universal)
    - Signals/Cancellations    # source rows for the synthesis
    - Tools/Notion                   # Notion REST / MCP access pattern (NOTION_TOKEN)
  writes:
    - Signals/Cancellations    # rewrites the section on the parent Learnings page
---

# update-cancellations-top10

Update the **Top 10 Learnings — Cancellations** section on the AI Knowledge `Learnings` page based on the current state of the `Signals/Cancellations` database. Run on demand after new rows land from `data:harvest-cancellation-feedback`. Recommended cadence: every ~20 new rows or weekly.

**Column contract:** [`Signals/Cancellations` schema](../../../shared/skills/record-knowledge/references/schemas/cancellations.md) is canonical for the properties this skill reads, including which are legitimately blank (`Support Style` is blank on every row by design, so an empty value there is not missing data). A doc reference, not a runtime read.

Sister skills: [`data:update-conversations-top10`](../update-conversations-top10/SKILL.md), [`data:update-experiments-top10`](../update-experiments-top10/SKILL.md) — same shape, different DB and synthesis question.

## When to use

Trigger when the user says any of:

- "update cancellations top 10"
- "refresh cancellation learnings"
- "rerun cancellations top 10"
- "resynthesize cancellation learnings"
- or otherwise asks to update the Cancellations Top 10 on the Learnings page.

If the user asks to *investigate a specific cancellation* or *harvest new entries*, use `data:investigate-cancellation-feedback` or `data:harvest-cancellation-feedback` instead.

## What the synthesis is *for*

The synthesis question is:

> "What recurring patterns across cancellation events should change how we build the product, set pricing, design onboarding, or configure Aurie — and which user profiles are most at risk of churning?"

A Cancellations top-10 bullet looks like:

> **Price exits cluster in deepProcessor users with high engagement (≥8 sessions) from Scale and CreativeTest campaigns (8 rows).** These users actively engage with Aurie (average 65 turns) but cite cost as the exit driver — the product worked; the value equation didn't land. Consider a lower-tier paywall test targeting this archetype. Sources: [MVBIKlWK…](https://notion.so/...), [4kbmMz4R…](https://notion.so/...), …

The synthesis should surface actionable cross-row patterns, not restate individual rows.

## Inputs

None required. Optional flags:

- `dry_run: true` — produce the new section text and diff but skip the Notion write.
- `max_bullets: <int>` — cap on bullets (default `10`). Ship fewer when the corpus is small.
- `min_rows: <int>` — minimum row count per cluster to earn a bullet (default `1`; raise to `2`+ once the DB has 15+ rows).

## Workflow

### Step 1 — Pull every row from the Cancellations database

Use `notion-fetch` on the data source:

```
collection://edce288d-5d89-4b4d-8083-e40d0ed7c1ee
```

Or query via `notion-query-data-sources` with SQL:

```sql
SELECT * FROM "collection://edce288d-5d89-4b4d-8083-e40d0ed7c1ee"
```

For each row, extract:

- `Title` (page title — contains UID prefix + reason + date)
- `Reason` (select)
- `Feedback Text` (free text)
- `Days to Cancel` (number)
- `Sessions` / `Turns` (numbers, if populated)
- `Acquisition` (select)
- `Archetype` (select)
- `Support Style` (select)
- `Age Group` (select)
- `Email Domain` (text — for diversity signal, not PII)
- `Signal` (text — qualitative analysis written during investigation)
- `Slack Thread URL` (URL — use as the Notion page link for Sources)
- `Vertical` (select, if populated)
- `Cancellation Date` (date)
- Notion page URL (canonical `https://www.notion.so/<id>` form)

**Skip rows where** both `Signal` and `Feedback Text` are empty and `Sessions` is null — these are stub rows with no synthesisable content. Rows with only the structured fields (Reason, Days, Archetype, etc.) are still valid for quantitative clustering.

If zero rows survive, stop and report — don't write an empty section.

### Step 2 — Cluster rows into pattern candidates

Cluster along these axes:

1. **Reason × Engagement.** Group rows by `Reason`, then compare `Sessions`/`Turns` within each group. High engagement + "Too expensive" or "Didn't help" is a different signal than low engagement + same reason.

2. **Reason × Archetype × Campaign.** Which archetypes churn on which reasons? Deep processors churning on "Too expensive" with 10+ sessions vs carrying-a-lot churning on "Not using it enough" with 1-2 sessions are different product problems.

3. **Support Style × Reason.** "Didn't help" exits concentrated in `clear/direct` or `professional/structured` users may signal the conversational format is wrong for a segment.

4. **Days to Cancel clustering.** Sub-1-day exits (technical issues or trial explorers) vs 1-3-day exits (onboarding failures) vs 7-14-day exits (habit failures) vs 10+ day exits (genuine product-fit or price failures) are fundamentally different churn shapes.

5. **Signal text themes.** For rows where `Signal` is populated, extract repeated themes (modality mismatch, habit failure, price sensitivity, technical friction). Surface themes that appear in 2+ rows.

6. **Feedback text patterns.** For rows with verbatim feedback — "made things worse", "the modality didn't work for me", "error message" — cluster paraphrases. These are the highest-signal exits because the user named the reason explicitly.

**Rank candidates by:**
- Number of supporting rows (more = stronger)
- Actionability (does this cluster suggest a specific product / pricing / onboarding change?)
- Surprise value (a pattern the team doesn't already know about scores higher)

### Step 3 — Write up to `max_bullets` bullets

Each bullet has four parts:

1. **Bold lead sentence** — the pattern, ≤25 words, includes the row count in parentheses.
2. One clause on **what drives the pattern** — mechanism or user state behind it.
3. One clause on **decision implication** — what this suggests for product, pricing, or Aurie config.
4. `Sources: [<title>](<url>), …` — Notion page URLs for every row in the cluster (use the page's Notion URL, not the Slack permalink).

**Quality bar.** Each bullet should be falsifiable, specific, and actionable. "Users churn because the product doesn't help" is not a valid bullet. "deepProcessor users with 8+ sessions churn on price at 10× the rate of carryingALot users — suggesting the core loop works but the paywall hits the wrong ceiling for high-engagement users" is.

**Mix of signals:**
- ~40% price/value-equation clusters (Too expensive, Didn't help)
- ~25% engagement/habit clusters (Not using it enough, sub-3-day churn)
- ~20% product-fit clusters (Didn't help, modality mismatch, Support Style)
- ~15% technical/other clusters

Adapt to actual data — don't manufacture a mix if the data is dominated by one signal.

### Step 4 — Build the replacement section

```markdown
## Top 10 Learnings — Cancellations
*Synthesized YYYY-MM-DD by agent from N rows. Refreshed as new rows land from data:harvest-cancellation-feedback.*

1. **<bold lead sentence (N rows)>.** <mechanism>. <decision implication>. Sources: [<title>](<url>), …
2. …
(up to max_bullets — ship fewer when the evidence doesn't support more)
```

Keep `## Top 10 Learnings — Cancellations` even when the list has fewer than 10 bullets.

### Step 5 — Show the diff and require approval

Present:
1. Row count considered, broken down by `Reason` and `Archetype`.
2. Rows skipped (stub rows with no content).
3. Clusters that survived `min_rows` and their row counts.
4. The new section text.
5. Request: *"Approve writing this back to the Learnings page?"*

**Never write silently.**

### Step 6 — Replace the existing section

Use `notion-update-page` with `command: update_content` against the Learnings page (id `34975e23-af20-8164-950c-c24797c2c625`).

Anchor the find-and-replace on the trailing Cancellations database tag (stable sentinel):

```
old_str:
  ## Top 10 Learnings — Cancellations
  *<placeholder or prior synthesis line>*
  ...
  <database url="https://app.notion.com/p/fcc2e9da2a1147b99eb5342f1977becd" inline="false" data-source-url="collection://edce288d-5d89-4b4d-8083-e40d0ed7c1ee">Cancellations</database>

new_str:
  ## Top 10 Learnings — Cancellations
  *Synthesized YYYY-MM-DD by agent from N rows...*
  1. ...
  ...
  <database url="https://app.notion.com/p/fcc2e9da2a1147b99eb5342f1977becd" inline="false" data-source-url="collection://edce288d-5d89-4b4d-8083-e40d0ed7c1ee">Cancellations</database>
```

If `old_str` doesn't match, stop and surface the error — don't guess the structure.

### Step 6b — Invalidate the local knowledge cache

After the write, delete `${AI_KNOWLEDGE_CACHE_DIR:-~/.cache/ai-knowledge}/learnings__cancellations.md` (if it exists) so the next `retrieve-knowledge` call picks up the new section. Missing file → skip silently.

### Step 7 — Confirm and report

After the write:
- Learnings page URL: `https://app.notion.com/p/34975e23af208164950cc24797c2c625`
- Number of rows considered, skipped, and included in the synthesis.
- Cluster count and their sizes.
- Final bullet count.
- Any shifts vs the prior synthesis (new patterns, patterns that dropped out).

## Edge cases

**Corpus too small.** With fewer than 5 rows, produce whatever genuine patterns exist (1-2 bullets is fine) and note the corpus size in the byline: `Synthesized YYYY-MM-DD by agent from 3 rows — synthesis will grow as more entries land.`

**`Signal` field unpopulated.** Many rows will have blank `Signal` at first (harvest fills the structured fields; Signal is filled later during investigation). The synthesis can still proceed on the structured fields (Reason, Days, Archetype, Sessions) — quantitative patterns don't require narrative Signal text. Note in Step 5's diff how many rows have Signal populated vs blank.

**Re-subscribing users.** A user who cancels and re-subscribes may have two rows (different Slack Thread URL). If both have the same archetype and reason, they count as 2 supporting sources for that pattern — that's intentional.

**Conflicting patterns.** If two clusters point in opposite directions (e.g. "deepProcessors churn on price" AND "deepProcessors churn on Didn't help"), surface both as separate bullets. Don't merge — the contrast is the signal.

## Out of scope

- This skill does **not** harvest new rows. Use `data:harvest-cancellation-feedback`.
- This skill does **not** modify the Cancellations database itself — only the Top 10 section on the parent Learnings page.
- This skill does **not** touch the Experiments / Conversations / Growth Readings sections.
- This skill is **not** scheduled — on-demand only.

## Related

- Harvest skill: [`data:harvest-cancellation-feedback`](../harvest-cancellation-feedback/SKILL.md)
- Source DB: [Signals/Cancellations](https://app.notion.com/p/fcc2e9da2a1147b99eb5342f1977becd)
- Data source: `collection://edce288d-5d89-4b4d-8083-e40d0ed7c1ee`
- Learnings page: `https://app.notion.com/p/34975e23af208164950cc24797c2c625`
- Sister skill: [`data:update-conversations-top10`](../update-conversations-top10/SKILL.md)
- Sister skill: [`data:update-experiments-top10`](../update-experiments-top10/SKILL.md)
