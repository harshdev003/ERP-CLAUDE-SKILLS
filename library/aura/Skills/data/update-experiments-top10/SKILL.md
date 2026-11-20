---
name: update-experiments-top10
description: >
  Update the "Top 10 Learnings — Experiments" section on the AI Knowledge
  Learnings page from the current state of the Learnings/Experiments
  database. Reads only ended rows (Status in {Won, Lost, Inconclusive,
  Turned off} — never Running, never blanks awaiting analyze-experiment
  enrichment), ranks by impact for Aurie's product/marketing decisions,
  and rewrites the inline section above the Experiments database in
  place. Trigger when the user says "update experiments top 10", "update
  top 10 experiments", "refresh experiment learnings", "rerun experiments
  top 10", "resynthesize experiment learnings", or asks to update the Top
  10 Experiments section on the Learnings page.
metadata:
  version: "3.0.1"
knowledge:
  reads:
    - Guide/Index   # baseline routing aid (universal)
    - Learnings/Experiments        # source rows for the Top 10 synthesis
    - Tools/Notion                   # Notion REST / MCP access pattern (NOTION_TOKEN)
  writes:
    - Learnings/Experiments        # rewrites the section on the parent Learnings page that hosts the inline DB
---

# update-experiments-top10

Update the **Top 10 Learnings — Experiments** section on the AI Knowledge `Learnings` page based on the current state of the `Learnings/Experiments` database. Run on demand whenever new experiments have wrapped.

## When to use

Trigger when the user says any of:

- "update experiments top 10"
- "update top 10 experiments"
- "refresh experiment learnings"
- "rerun experiments top 10"
- "resynthesize experiment learnings"
- or otherwise asks to update the experiments Top 10 on the Learnings page.

If the user asks to *analyze a specific experiment* or *enrich a row*, that's a different skill — use `data:analyze-experiment` instead. This skill only re-renders the Top 10 summary; it does not enrich individual rows.

## Inputs

None required. Optional flags the user might pass:

- `dry_run: true` — produce the new section text and diff but skip the Notion write.
- `quality_bar: <example>` — override the canonical quality reference (default: the 2026-04-24 synthesis).

## Workflow

### Step 1 — Pull every ended row from the Experiments database

Use the Notion MCP tool `notion-fetch` against the data source:

```
collection://c0209685-17ac-49e2-849a-85fa8178decd
```

Enumerate child pages. For each row, fetch its body via `notion-fetch <page-id>`.

**Filter rules — apply both:**

1. **Status filter.** Skip any row whose `Status` is `Running` or blank. Only keep `Won`, `Lost`, `Inconclusive`, `Turned off`. Losses are learnings too — do not drop them.
2. **Enrichment filter.** Skip any row whose body still contains the literal string `no learning captured yet — awaiting analyze-experiment enrichment`. Those rows have no synthesizable content yet.

If fewer than 10 rows survive both filters, emit a warning to the user and stop — there isn't enough material for a meaningful refresh.

### Step 2 — Rank by impact

For each surviving row, extract:

- Title
- Status (Won / Lost / Inconclusive / Turned off)
- Hypothesis
- Variants
- Result body (the "what we learned" section in each row)
- Notion URL (canonical form: `https://www.notion.so/<id-without-dashes>`)

Rank by **impact/importance for Aurie's product and marketing decisions** — the question is "what would actually change a future decision?" Not by recency, not by p-value, not by status. Group rows that back the same learning into a single bullet.

Aim for a mix:

- At least 1–2 Lost or Inconclusive results if any are in the corpus — losses are signal.
- Don't repeat the same surface (e.g. paywall) more than 3 times in 10 bullets.
- Prefer learnings with a quantified lift over qualitative observations when both are available.

### Step 3 — Write the 10 bullets

Each bullet has three parts on a single line:

1. **Bold lead sentence** — the learning itself, ≤25 words, decision-ready, includes the headline metric where available.
2. One short clause on **why it matters** for the next decision.
3. `Source: [<title>](<notion-url>)` — multiple sources allowed when bullets group rows, comma-separated.

**Quality bar.** Each bullet should be at the level of:

> **Two-step paywall lifts trial starts +41% while preserving engagement.** Adds a "here's the deal" explanation screen before the paywall, reframing pressure into transparency. Source: [twoStepPaywall](https://www.notion.so/34c75e23af20812281caeda84c7d3d6f)

If you can't write a bullet that crisp, the row probably doesn't belong in the Top 10.

### Step 4 — Build the replacement section

The full block to write back into the Learnings page:

```markdown
## Top 10 Learnings — Experiments
*Synthesized YYYY-MM-DD by `data:update-experiments-top10` from `<N>` of `<T>` rows below. Refreshed as new experiments wrap, and on every `data:run-experiments-harvest-cadence` run that enriches a row.*

1. **<bullet 1>** — <why it matters>. Source: [<title>](<url>)
2. **<bullet 2>** — <why it matters>. Source: [<title>](<url>)
... (10 total)
```

Use today's date for `YYYY-MM-DD`. `<N>` is the count of rows the synthesis actually drew on; `<T>` is the total row count in the database.

**The byline names the maintaining skill, and both counts.** A reader who finds the section stale needs to know what to run without grepping for it — so the skill id goes in the byline, not just "by agent". Both counts go in because one number reads as the corpus size: a synthesis over a filtered slice otherwise looks authoritative over a database it barely sampled. When `<N>` is a small fraction of `<T>`, say why in the same line (e.g. `from 61 of 3,051 rows below (rows with a terminal Status and a non-placeholder Learning)`).

**No Linear references in the body.** The byline reports what the synthesis is and when it ran — it does NOT cite the Linear ticket that introduced the skill. AI Knowledge content stays Linear-free (page bodies, row bodies, synthesis bylines, `# Sources` blocks). Linear lives in the commit / PR / SKILL.md "Related" section, not in Notion.

### Step 5 — Show the diff and require approval

Before writing to Notion, present the user with:

1. The list of rows that were considered (count + titles).
2. The list of rows that were skipped and why (Status=Running, blank-enrichment, etc.).
3. The new section, rendered.
4. A request for explicit approval: *"Approve writing this back to the Learnings page?"*

**Never write silently.** AI Knowledge writes always require user approval.

### Step 6 — Replace the existing section

Use `notion-update-page` with `command: update_content` against page id `34975e23-af20-8164-950c-c24797c2c625`.

Anchor the find-and-replace on the trailing database tag, which is stable:

- `old_str` should start at `## Top 10 Learnings — Experiments\n*Synthesized` and run through to (but **not** include) the next line, which is the database tag:
  `<database url="https://www.notion.so/2cbe45a3246040658b9f20559b5b452b" inline="false" data-source-url="collection://c0209685-17ac-49e2-849a-85fa8178decd">Experiments</database>`
- `new_str` is the freshly built section from Step 4, followed by the same trailing newline so the database tag stays exactly where it was.

Practical pattern: include the database tag as a **sentinel suffix** in both `old_str` and `new_str`. That keeps the match unambiguous and guarantees the database block isn't disturbed.

```
old_str:
  <existing Top 10 section>
  <database url="https://www.notion.so/2cbe45a3246040658b9f20559b5b452b" inline="false" data-source-url="collection://c0209685-17ac-49e2-849a-85fa8178decd">Experiments</database>

new_str:
  <new Top 10 section>
  <database url="https://www.notion.so/2cbe45a3246040658b9f20559b5b452b" inline="false" data-source-url="collection://c0209685-17ac-49e2-849a-85fa8178decd">Experiments</database>
```

If `old_str` doesn't match (e.g. the section header was renamed), stop and surface the error to the user — do not guess the structure.

### Step 6b — Invalidate the local cache

After the Notion write succeeds, delete `${AI_KNOWLEDGE_CACHE_DIR:-~/.cache/ai-knowledge}/learnings.md` so the next `retrieve-knowledge` read on `Learnings` picks up the new Top 10 section. Spec: [`shared:retrieve-knowledge/references/cache.md`](../../../shared/skills/retrieve-knowledge/references/cache.md).

If the cache root or the file doesn't exist, skip silently. Cache invalidation must never fail the write.

### Step 7 — Confirm and report

After the write succeeds, tell the user:

- The page URL: `https://www.notion.so/34975e23af208164950cc24797c2c625`
- The number of rows considered, skipped, and included.
- Any 1–2 noteworthy shifts vs the previous Top 10 (new entrants, bumped rows).

## Out of scope

- This skill does **not** enrich individual experiment rows. Use `data:analyze-experiment` for that.
- This skill does **not** modify the Experiments database itself, only the Top 10 section on the parent Learnings page.
- This skill does **not** touch `# Sources` at the bottom of the Learnings page, or the Growth Readings / Ad Campaigns sections.

## Related

- Sister skill: `marketing:update-growth-readings-top10` — same shape, different DB.
- Source-of-truth Linear ticket: [PROD-1267](https://linear.app/aura-health/issue/PROD-1267) — original synthesis task.
- Parent: [PROD-1215](https://linear.app/aura-health/issue/PROD-1215) — Learnings teamspace migration.
