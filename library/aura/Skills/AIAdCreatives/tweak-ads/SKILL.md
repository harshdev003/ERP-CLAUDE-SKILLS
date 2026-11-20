---
name: tweak-ads
description: "Apply targeted tweaks to an existing ad image without regenerating the creative from scratch. Use when: the user shares an existing ad and asks to: tweak it, adjust it, try a change, test a variant, recolor the character, change the background, add a badge, update the hook text, swap the CTA, adjust the gradient, make it warmer/cooler, add a social logo, or produce a quick A/B variant.
---

# Tweak Ads — Targeted Edits to Existing Creatives

Create meaningfully different ad variants from a source image. Multiple elements can change simultaneously — subject, background, color, copy, layout adjustments are all in scope. The goal is a fresh creative that shares structural DNA with the source, not a near-identical copy.

**Reference:** [`references/image-edit-directives.md`](../../../references/image-edit-directives.md) — full catalog of allowed tweaks, prompt patterns, execution methods, and quality checks.

---

## What the User Must Provide

| Input | Required | Default |
|-------|----------|---------|
| Ad image | Yes | — |
| Change direction | No | Ask in Phase 2 |
| App (Aurie / Aura) | No | Infer from image |
| Output folder | No | `output/tweaks/<YYYYMMDD-HHMMSS>/` |

If no image is provided, ask for it before doing anything else.

---

## Phase 0: Load Context

**Source image path:** If the user attached an image directly in the conversation, copy it to `/tmp/tweak-source.png` with a Bash `cp` command and use that path for all API calls. Do not search `data.json` or scan the filesystem to find the file.

```bash
cp "<attachment-path>" /tmp/tweak-source.png
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

**Hard rule:** a single tweak run modifies **at most 2 of these 5 categories**. More than that is a new ad — redirect to `similar-ads` or `scale-winning-ad`.

For each category mark whether it is **changing** (under test) or **preservable** (must survive untouched). This split drives the Phase 3 plan.

---

## Phase 2: Direction

Tell the user what you see in a few sentences, then offer potential changes as one-liners:

- Change subject
- Change background
- Change copy
- Change subject color / lighting
- Add new elements (badges, logos, proof panels)
- Move existing elements

**Default to Complete AI edit** unless the user specifically requests otherwise.

- **Complete AI edit** *(default)* — Quicker. AI handles everything in one pass.
- **AI edit / generate + Overlay** — Slower. Use when Complete AI edit has not produced an exact match after a couple of attempts and the user wants pixel-accurate reproduction of branded elements (social proof panels, award logos, "FROM THE TEAM BEHIND AURA" lockup, inline-text visuals). Those elements are cropped or sourced from `references/assets/` and pasted back via Pillow.

Do **not** ask the user to choose an execution approach — proceed with Complete AI edit automatically. Only switch to AI edit / generate + Overlay if the user explicitly asks for it.

If the user already stated a direction before this phase, skip the menu and confirm their intent with one sentence.

---

## Phase 3: Plan the Changes

**MANDATORY: Read [`references/image-edit-directives.md`](../../../references/image-edit-directives.md) — Execution Methods section before planning any changes.** It contains the classification table, edit-vs-generate decision, preserve-and-overlay logic, prompt patterns, and batch constraint (at most 2 of 5 categories per run).

### 3a. Classify each change

For each requested change, determine the execution method (Pillow-only / AI edit / composite rebuild / AI generate + Pillow composite / preserve-and-overlay) using the classification table in the directives doc.

### 3b. Preserve-and-overlay decision (when AI is needed)

When the changing item requires AI **and** the ad contains preservable branded elements (custom social proof panels, award badges, brand logos, exact copy), the AI will drift those elements every time. Take them out of the AI's responsibility and put them back via Pillow.

For each element to preserve, follow this priority order:

1. **[`references/assets/index.md`](../../../references/assets/index.md)** — check first. If the element is listed there (orb, rating badge, award logos, "FROM THE TEAM BEHIND AURA" lockup), use that file directly.
2. **Pillow rendering** — for all text (hook, bullets, CTA). Always render copy via Pillow, never crop pixels for type.
3. **Crop from source (last resort)** — only when the element is absent from `references/assets/` AND cannot be Pillow-rendered (e.g. a bespoke panel layout). See the "Cropping Elements from Source Image" section in the directives doc.

### 3c. Draft prompts and confirm

Use the preservation-anchor pattern from the directives doc. For preserve-and-overlay, also add the blank-zone instruction.

Show the plan to the user — one line per change: method + prompt summary + which elements come from where (assets / crop / AI). **Confirm before generating.**

---

## Phase 4: Execute

Create the session output directory once at the start of the run:
```
output/tweaks/{YYYYMMDD-HHMMSS}/
```

For **each tweak**, create a named subfolder under the session directory:
```
output/tweaks/{YYYYMMDD-HHMMSS}/{tweak-slug}/
```
where `{tweak-slug}` is a short kebab-case label for the change (e.g. `nature-bg`, `sparkles-orb`, `new-character`).

**File placement rules:**

| File type | Where it goes |
|-----------|---------------|
| Python scripts (patch, composite) | `/tmp/` — never in `output/` |
| Raw AI-generated image (before Pillow) | `output/tweaks/{datetime}/{tweak-slug}/raw-ai.png` |
| Final ad image (after Pillow composite) | `output/tweaks/{datetime}/{tweak-slug}/final.png` |
| Generation prompt | `output/tweaks/{datetime}/{tweak-slug}/prompt.txt` |

**`prompt.txt` contents:** Write the exact prompt string sent to the AI model (image edit or generate call). If multiple prompts were used in sequence (e.g. an edit then a generate), write each one under a labeled header (`## Edit prompt`, `## Generate prompt`). Include the model name and provider on the first line.

Always write Python scripts to `/tmp/tweak_<slug>.py` (or similar) and run them from there. Delete them when the run completes — they are build artifacts, not deliverables.

Run all changes. If multiple changes are independent, run them in parallel (one Bash call per change in a single message).


---

## Phase 5: Quality Check

For each output, verify against the checklist in `references/image-edit-directives.md`:

1. Text is legible and not obscured
2. Subject is intact — no morphed features or unexpected drift
3. Brand alignment holds
4. No AI artifacts at edges or in shadow areas
5. Composition is balanced

If an AI edit drifted (morphed subject, unexpected element added, text zone disturbed), retry once with a tightened preservation anchor before escalating to the user.

---

## Phase 6: Deliver

Show each final ad image inline and state its output path: `output/tweaks/{datetime}/{tweak-slug}/final.png`. One sentence per image: what changed and what hypothesis it tests. Also mention `raw-ai.png` in the same folder so the user can compare before and after Pillow compositing, and note that `prompt.txt` contains the exact prompt used.

No tracker update, no data.json rows, no brief YAML, no QA scoring.

---

## Phase 7: Update Tweak Index

After delivering results, update `output/tweaks/index.md` to record this run.

**For each tweak generated in this session**, prepend one row to the table (newest at top). Row format:

| Description | Ran on | Generated ad |
|---|---|---|
| `<one-liner>` | `<session datetime>` | `![preview](<session>/<slug>/final.png)` |

**Description one-liner rules:**
- Lead with the ad ID if this came from a scale run (e.g. `Ad \`120246848434760184\` —`), otherwise omit
- Describe what changed in plain English: `Hook → "X"`, `Background → Y`, `Subject → Z`, `Orb → X, Y background`, etc.
- Pull the key phrase from the first meaningful line of `prompt.txt` when the slug alone is ambiguous
- Keep it under 100 characters

**Ran on:** parse the session folder name:
- `YYYYMMDD-HHMMSS` → `YYYY-MM-DD HH:MM`
- `YYYYMMDD-<label>` (no time component) → `YYYY-MM-DD`

**Image path:** relative from `output/tweaks/` — e.g. `20260506-scale-variants/120246849049600184-hook-burnout-professional/final.png`

**Keep only the 15 newest rows** in the table body after inserting. Drop oldest rows beyond 15.

**Create the file if it doesn't exist** with this header:

```markdown
# Tweak Generations Index

Last 15 generations, newest first. Updated automatically after each tweak run.

| Description | Ran on | Generated ad |
|---|---|---|
```

Update the index in a single Write/Edit operation — no interim files needed.
