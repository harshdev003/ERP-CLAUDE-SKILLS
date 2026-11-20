---
name: update-growth-readings-top10
description: >
  Update the "Top 10 Learnings — Growth Readings" section on the AI
  Knowledge Learnings page from the current state of the
  Learnings/Growth Readings database. Reads every row (no status filter
  — Growth Readings have no status), ranks by impact for Aurie's growth
  strategy across product and marketing, and rewrites the inline section
  above the Growth Readings database in place. Trigger when the user
  says "update growth readings top 10", "update top 10 growth readings",
  "refresh growth readings learnings", "rerun growth readings top 10",
  or asks to update the Top 10 Growth Readings section on the Learnings
  page.
metadata:
  version: "3.0.2"
knowledge:
  reads:
    - Guide/Index   # baseline routing aid (universal)
    - Learnings/Growth Readings    # source rows for the Top 10 synthesis
    - Tools/Notion                   # Notion REST / MCP access pattern (NOTION_TOKEN)
  writes:
    - Learnings/Growth Readings    # rewrites the section on the parent Learnings page that hosts the inline DB
---

# update-growth-readings-top10

Update the **Top 10 Learnings — Growth Readings** section on the AI Knowledge `Learnings` page based on the current state of the `Learnings/Growth Readings` database. Run on demand whenever new readings have been ingested via `marketing:summarize-youtube-to-notion` (or any other source-ingest skill).

## When to use

Trigger when the user says any of:

- "update growth readings top 10"
- "update top 10 growth readings"
- "refresh growth readings learnings"
- "rerun growth readings top 10"
- or otherwise asks to update the Growth Readings Top 10 on the Learnings page.

If the user wants to *summarize a new video / podcast / article into the database*, that's a different skill — use `marketing:summarize-youtube-to-notion` (or the equivalent ingest skill). This skill only re-renders the Top 10 summary; it does not add new rows.

## Inputs

None required. Optional flags:

- `dry_run: true` — produce the new section text and diff but skip the Notion write.
- `quality_bar: <example>` — override the canonical quality reference (default: the 2026-04-24 synthesis).

## Workflow

### Step 1 — Pull every row from the Growth Readings database

Use the Notion MCP tool `notion-fetch` against the data source:

```
collection://0bd1fd9f-19f3-4a12-9ef2-25e9587831a5
```

Enumerate child pages. For each row, fetch its body via `notion-fetch <page-id>`.

**No status filter applies.** Every Growth Reading row has content by construction (`summarize-youtube-to-notion` writes the full summary on creation). The only rows you should skip are obvious stubs — pages with empty bodies or a single placeholder line. If you encounter one, surface it to the user as a data-quality flag rather than silently dropping it.

If fewer than 10 rows survive, emit a warning and stop — there isn't enough material for a meaningful refresh.

### Step 2 — Rank by impact

For each row, extract:

- Title
- Date Published
- Overview (the 3–5 sentence summary property)
- Body content (the long-form distilled learnings inside the page)
- Notion URL (canonical form: `https://www.notion.so/<id-without-dashes>`)

Rank by **impact for Aurie's growth strategy** — both product and marketing decisions, not just one or the other. The question is "which of these would actually change how we build, position, or market Aurie?"

Aim for a mix:

- Cover at least 3 distinct surfaces across the 10 (e.g. retention, paid acquisition, organic content, monetization, positioning, onboarding). Don't let a single surface dominate.
- Group rows that back the same learning into one bullet with multiple sources.
- Prefer learnings tied to a concrete behavior or playbook over generic observations ("focus on retention" is too vague; "bundle adjacent use cases to preserve retention at life transitions" is actionable).

### Step 3 — Write the 10 bullets

Each bullet has three parts on a single line:

1. **Bold lead sentence** — the learning itself, ≤25 words, decision-ready.
2. One short clause on **why it matters** for Aurie specifically.
3. `Source: [<title>](<notion-url>)` — multiple sources allowed when bullets group rows, comma-separated.

**Quality bar.** Each bullet should be at the level of:

> **Retention is growth — cohorts with healthy curves compound word-of-mouth beyond paid reach.** Fix retention before scaling acquisition spend. Flo hit 80M MAU with 50% organic; Opal got to \$10M ARR with 11 people, both retention-first. Sources: [Flo \$1B retention](https://www.notion.so/34c75e23af208175a280f8bdfbf1c40f), [Opal \$10M ARR](https://www.notion.so/34c75e23af2081f690f7fc5c20d19834)

If you can't write a bullet that crisp, the row probably doesn't belong in the Top 10.

### Step 4 — Build the replacement section

```markdown
## Top 10 Learnings — Growth Readings
*Synthesized YYYY-MM-DD by agent from rows below. Refreshed as new readings land.*

1. **<bullet 1>** — <why it matters>. Source: [<title>](<url>)
2. **<bullet 2>** — <why it matters>. Source: [<title>](<url>)
... (10 total)
```

Use today's date for `YYYY-MM-DD`.

**No Linear references in the body.** The byline reports what the synthesis is and when it ran — it does NOT cite the Linear ticket that introduced the skill. AI Knowledge content stays Linear-free (page bodies, row bodies, synthesis bylines, `# Sources` blocks). Linear lives in the commit / PR / SKILL.md "Related" section, not in Notion.

### Step 5 — Show the diff and require approval

Before writing to Notion, present:

1. The list of rows considered (count + titles).
2. Any data-quality flags (empty bodies, suspicious stubs).
3. The new section, rendered.
4. A request for explicit approval: *"Approve writing this back to the Learnings page?"*

**Never write silently.** AI Knowledge writes always require user approval.

### Step 6 — Replace the existing section

Use `notion-update-page` with `command: update_content` against page id `34975e23-af20-8164-950c-c24797c2c625`.

Anchor the find-and-replace on the trailing database tag, which is stable. Include the database tag as a sentinel suffix in both `old_str` and `new_str`:

```
old_str:
  <existing Top 10 Growth Readings section>
  <database url="https://www.notion.so/58994b5fa5d848b7a3f12634cc8ee8f1" inline="false" data-source-url="collection://0bd1fd9f-19f3-4a12-9ef2-25e9587831a5">Growth Readings</database>

new_str:
  <new Top 10 Growth Readings section>
  <database url="https://www.notion.so/58994b5fa5d848b7a3f12634cc8ee8f1" inline="false" data-source-url="collection://0bd1fd9f-19f3-4a12-9ef2-25e9587831a5">Growth Readings</database>
```

If `old_str` doesn't match (e.g. the section header was renamed), stop and surface the error to the user — do not guess the structure.

### Step 6b — Invalidate the local cache

After the Notion write succeeds, delete `${AI_KNOWLEDGE_CACHE_DIR:-~/.cache/ai-knowledge}/learnings.md` so the next `retrieve-knowledge` read on `Learnings` picks up the new Top 10 section. Spec: [`shared:retrieve-knowledge/references/cache.md`](../../../shared/skills/retrieve-knowledge/references/cache.md).

If the cache root or the file doesn't exist, skip silently. Cache invalidation must never fail the write.

### Step 7 — Confirm and report

After the write succeeds, tell the user:

- The page URL: `https://www.notion.so/34975e23af208164950cc24797c2c625`
- The number of rows considered and included.
- Any 1–2 noteworthy shifts vs the previous Top 10 (new entrants, bumped rows).

## Out of scope

- This skill does **not** ingest new Growth Reading rows. Use `marketing:summarize-youtube-to-notion` for that.
- This skill does **not** modify the Growth Readings database itself, only the Top 10 section on the parent Learnings page.
- This skill does **not** touch `# Sources` at the bottom of the Learnings page, or the Experiments / Ad Campaigns sections.

## Related

- Sister skill: `data:update-experiments-top10` — same shape, different DB.
- Source-of-truth Linear ticket: [PROD-1268](https://linear.app/aura-health/issue/PROD-1268) — original synthesis task.
- Parent: [PROD-1215](https://linear.app/aura-health/issue/PROD-1215) — Learnings teamspace migration.
- Upstream feeder: `marketing:summarize-youtube-to-notion` — produces the rows this skill summarizes.
