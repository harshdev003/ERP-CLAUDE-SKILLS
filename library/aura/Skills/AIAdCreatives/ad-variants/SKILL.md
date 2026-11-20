---
name: ad-variants
description: "Create bold, multi-element variants of an existing ad image. Any number of visual or copy elements can change simultaneously — subject, background, color theme, layout structure, hook, bullets, CTA are all fair game. Use when: the user shares an existing ad and wants to: create a compelling variant, overhaul the visual style, try a new character or scene, test a different persona or hook, swap the color theme, or produce a meaningfully different creative that shares structural DNA with the source. Do NOT use for animating ads (use convert-to-video)."
---

# Ad Variants — Bold Creative Variations from an Existing Ad

Create meaningfully different ad variants from a source image. Multiple elements can change simultaneously — subject, background, color, copy, layout adjustments are all in scope. The goal is a fresh creative that shares structural DNA with the source, not a near-identical copy.

**Reference:** [`references/image-edit-directives.md`](../../../references/image-edit-directives.md) — catalog of change types, prompt patterns, execution methods, and quality checks.

---

## What the User Must Provide

| Input | Required | Default |
|-------|----------|---------|
| Ad image | Yes | — |
| Change direction | Yes | — |
| App (Aurie / Aura) | No | Infer from image |
| Output folder | No | `output/variants/<YYYYMMDD-HHMMSS>/` |

If no image is provided, ask for it before doing anything else.

---

## Phase 0: Load Context

**Source image path:** If the user attached an image directly in the conversation, copy it to `/tmp/variant-source.png` with a Bash `cp` command and use that path for all API calls. Do not search `data.json` or scan the filesystem to find the file.

```bash
cp "<attachment-path>" /tmp/variant-source.png
```

If the attachment path is not evident from the message, ask the user for it — one short question is fine.

Load brand guidelines for the inferred app:
- Aurie: [`references/brand/aurie.md`](../../../references/brand/aurie.md)
- Aura: [`references/brand/aura.md`](../../../references/brand/aura.md)

Read [`references/assets/index.md`](../../../references/assets/index.md) to see what reusable assets are available for compositing (orb, social proof badges, award logos, brand lockups).

Check `.env` exists before running any generation step. If missing, ask the user to create it from `.env.sample`.

---

## Phase 1: Quick Teardown

View the ad image. Extract the visual formula using [`references/generation/formula-extraction.md`](../../../references/generation/formula-extraction.md). Keep this in conversation context — don't save to disk.

Catalog the ad in these five categories (the "teardown list"):

1. **Subject** — character type, color, pose, position on canvas
2. **Layout zones** — approximate % boundaries for image, gradient, hook text, bullets
3. **Color palette** — background, accent, text colors
4. **Copy** — hook text (line by line), bullet content, CTA text
5. **Existing elements** — badges, logos, social proof panels, brand marks

For each category, note what is **changing** (under test) or **anchoring** (intentionally kept for continuity). This split drives the Phase 3 plan. There is no cap on how many categories can change — choose based on what makes the variant meaningfully different and worth testing.

---

## Phase 3: Plan the Changes

**MANDATORY: Read [`references/image-edit-directives.md`](../../../references/image-edit-directives.md) — Execution Methods section before planning any changes.** It contains the classification table, edit-vs-generate decision, preserve-and-overlay logic, prompt patterns, and batch guidance.

Use the preservation-anchor pattern from the directives doc. Draft the prompt and proceed directly to execution — no user confirmation needed.

---

## Phase 4: Execute

Create the session output directory once at the start of the run:
```
output/variants/{YYYYMMDD-HHMMSS}/
```

For **each variant**, create a named subfolder under the session directory:
```
output/variants/{YYYYMMDD-HHMMSS}/{variant-slug}/
```
where `{variant-slug}` is a short kebab-case label for the change (e.g. `male-nature-bg`, `dark-theme-persona`, `couple-blush`).

**File placement rules:**

| File type | Where it goes |
|-----------|---------------|
| Python scripts (patch, composite) | `/tmp/` — never in `output/` |
| Raw AI-generated image (before Pillow) | `output/variants/{datetime}/{variant-slug}/raw-ai.png` |
| Final ad image (after Pillow composite) | `output/variants/{datetime}/{variant-slug}/final.png` |
| Generation prompt | `output/variants/{datetime}/{variant-slug}/prompt.txt` |

**`prompt.txt` contents:** Write the exact prompt string sent to the AI model (image edit or generate call). If multiple prompts were used in sequence (e.g. an edit then a generate), write each one under a labeled header (`## Edit prompt`, `## Generate prompt`). Include the model name and provider on the first line.

Always write Python scripts to `/tmp/variant_<slug>.py` (or similar) and run them from there. Delete them when the run completes — they are build artifacts, not deliverables.

Run all changes. If multiple changes are independent, run them in parallel (one Bash call per change in a single message).

---

## Phase 5: Quality Check

For each output, verify against the checklist in `references/image-edit-directives.md`:

1. Text is legible and not obscured
2. Requested changes are visibly present — verify every element that was supposed to change actually changed
3. Brand alignment holds for anchored elements
4. No AI artifacts at edges or in shadow areas
5. Composition is balanced

If an AI edit failed to apply a requested change (e.g. a person is still there after being instructed to remove them), retry once with a more explicit lead instruction before reporting the issue.

---

## Phase 6: Deliver

Show each final ad image inline and state its output path: `output/variants/{datetime}/{variant-slug}/final.png`. One sentence per image: what changed and what hypothesis it tests. Also mention `raw-ai.png` in the same folder so the user can compare before and after Pillow compositing, and note that `prompt.txt` contains the exact prompt used.

No tracker update, no data.json rows, no brief YAML, no QA scoring.

---

## Phase 7: Update Variant Index

After delivering results, update `output/variants/index.md` to record this run. Skip this phase if the subagent prompt explicitly says "No index.md update needed" (used by scale-ad-variants, which updates the index itself after all subagents complete).

**For each variant generated in this session**, prepend one row to the table (newest at top). Row format:

| Description | Ran on | Generated ad |
|---|---|---|
| `<one-liner>` | `<session datetime>` | `![preview](<session>/<slug>/final.png)` |

**Description one-liner rules:**
- Lead with the ad ID if this came from a scale run (e.g. `Ad \`120246849049600184\` —`), otherwise omit
- Describe all elements that changed: `Subject → X`, `Background → Y`, `Hook → "Z"`, `Color → X + Y`, etc.
- Pull the key phrase from the first meaningful line of `prompt.txt` when the slug alone is ambiguous
- Keep it under 100 characters

**Ran on:** parse the session folder name:
- `YYYYMMDD-HHMMSS` → `YYYY-MM-DD HH:MM`
- `YYYYMMDD-<label>` (no time component) → `YYYY-MM-DD`

**Image path:** relative from `output/variants/` — e.g. `20260506-scale-variants/120246849049600184-hook-burnout-professional/final.png`

**Keep only the 15 newest rows** in the table body after inserting. Drop oldest rows beyond 15.

**Create the file if it doesn't exist** with this header:

```markdown
# Ad Variant Generations Index

Last 15 generations, newest first. Updated automatically after each variant run.

| Description | Ran on | Generated ad |
|---|---|---|
```

Update the index in a single Write/Edit operation — no interim files needed.
