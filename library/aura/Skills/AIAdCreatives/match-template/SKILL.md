---
name: match-template
description: "Classify an ad (competitor, winning, or internal) against the ad layout template registry at scripts/templates/. Records the match as an upvote in scripts/templates/usage.json or adds a proposal for a new template if no existing layout fits. Use this skill whenever another skill needs to answer: 'which template does this ad layout match, and should we propose a new one?' Called by competitor-analysis, winning-creative-analysis, and any future skill that analyzes an ad layout. MANDATORY TRIGGERS: match template, classify ad layout, template upvote, propose template, which template does this use."
---

# Match Template — Ad Layout Classification

Classify a single ad's visual layout against the registered templates in `scripts/templates/`. Output one of:

- A **match** to an existing template (records an upvote)
- A **proposal** for a new template (records the layout pattern so it can be promoted later)

This skill is meant to be called from another skill mid-analysis. It does one thing: decide which bucket the ad falls into and persist the outcome.

---

## When to Use

- Called by `winning-creative-analysis` during the pixel-level teardown (section 3, Image).
- Called by `competitor-analysis` for each of the top 5 ads (between data extraction and writing the analysis).
- Called by any future skill that analyzes an ad layout — e.g. a periodic "audit which layouts are trending" scan.

Do **not** use this skill for generating ads or writing briefs. It only classifies existing ads.

---

## Inputs

The calling skill must provide:

- **Ad identity** — Meta Ad Library ID, competitor/brand name, or equivalent stable identifier.
- **Visual reference** — an image, screenshot, or a detailed description of the ad's layout (character/subject, text blocks, spatial arrangement). A URL alone is not enough.
- **Source** — the name of the calling skill (e.g. `competitor-analysis`, `winning-creative-analysis`). Used to tag the match entry.
- **`direct_add`** *(optional, default: false)* — when `true`, new templates are added directly to `templates` in `usage.json` instead of `proposed`. Use this when the caller has already validated the layout as worth keeping (e.g. `winning-creative-analysis`). When `false` (the default), new layouts go to `proposed` for the standard 2-match promotion review.

---

## Step 1 — Load the Template Registry

1. Read `scripts/templates/usage.json` — this is the tracker.
2. Read each template module's top-of-file docstring at `scripts/templates/<name>.py`. The docstring describes the layout, zones, and visual signature.
3. Quick summary commands:
   ```
   python3 scripts/generate-ads.py --list-templates
   python3 scripts/generate-ads.py --template-stats
   ```

You need to understand every registered template before classifying — otherwise you'll over-propose new ones.

---

## Step 2 — Compare the Ad to Each Template

For each registered template, check the ad's structural signature, not surface-level decoration:

- **Character/subject zone** — shape, size vs canvas, pose, position
- **Text zones** — how many, what role (hook, bullets, headline, CTA), placement
- **Visual hierarchy** — which element dominates, the size ratios
- **Background treatment** — solid, gradient, layered, photo

**Keep matches flexible — templates are concepts, not pixel contracts:**

- Different background, colors, fonts, or sizes → **same template**. Those live in the brief's `composite:` block.
- Slightly different layout that shares the same concept (e.g. character placed higher, 2 bullets instead of 3, wider gradient, different margins) → **still a match**. Don't fork a template over minor positioning or count tweaks.
- Only propose a new template when the structural *concept* is different — new zones, different element types, or text blocks playing different roles (e.g. a testimonial quote block instead of checkmark bullets, a split-screen before/after, a pure-typography hero with no image).

### Confidence levels

- **high** — core concept matches; only decorative values or minor positioning differ.
- **medium** — concept matches but with one meaningful difference (e.g. different bullet count, hook placement shifted). Still a useful upvote.
- **low** — loose resemblance only. Do **not** record as a match. Treat as no-match.

---

## Step 3A — If an Existing Template Matches

Append an entry to `scripts/templates/usage.json` under `templates.<name>.matches`:

```json
{
  "source": "<calling-skill-name>",
  "brand": "<brand-or-competitor-name>",
  "ad_id": "<library_id_or_identifier>",
  "date": "YYYY-MM-DD",
  "confidence": "high" | "medium",
  "notes": "1 sentence — what structural elements triggered the match"
}
```

### Return to the caller

Tell the calling skill the match in this exact shape so it can embed it in the analysis output:

```
MATCH: `<template_name>` (<confidence>) — <one-sentence rationale>
```

---

## Step 3B — If No Existing Template Fits

This is the "create a new one" path. Two sub-cases:

### 3B.1 — The layout is a pattern worth reusing

**Branch on the `direct_add` flag:**

#### `direct_add: false` (default) — add to `proposed`

Append a proposal to `scripts/templates/usage.json` under `proposed`:

```json
{
  "name": "<snake_case_name>",
  "source": "<calling-skill-name>",
  "brand": "<brand-or-competitor-name>",
  "ad_id": "<library_id_or_identifier>",
  "proposed_date": "YYYY-MM-DD",
  "description": "1-2 sentences describing the layout pattern",
  "zones_sketch": [
    {"type": "image",    "bounds": "[x1, y1, x2, y2]", "role": "subject"},
    {"type": "gradient", "bounds": "[x1, y1, x2, y2]"},
    {"type": "text",     "bounds": "[x1, y1, x2, y2]", "role": "hook"},
    {"type": "text",     "bounds": "[x1, y1, x2, y2]", "role": "bullets"}
  ]
}
```

Return: `PROPOSE: new:<snake_case_name> — <one-sentence rationale>`

#### `direct_add: true` — add directly to `templates`

Add the new template directly to `scripts/templates/usage.json` under `templates` with its first match entry inline. Do **not** add anything to `proposed`:

```json
"<snake_case_name>": {
  "description": "1-2 sentences describing the layout pattern. Include zone sketch: [0-X%] zone | [X-Y%] zone | ...",
  "matches": [
    {
      "source": "<calling-skill-name>",
      "brand": "<brand-or-competitor-name>",
      "ad_id": "<library_id_or_identifier>",
      "confidence": "high",
      "date": "YYYY-MM-DD",
      "rationale": "<why this is a new structural concept, with zone sketch>"
    }
  ]
}
```

Return: `NEW: <snake_case_name> — <one-sentence rationale>`

---

**Naming guidelines** (apply regardless of `direct_add`):
- `snake_case`, describes the structure not the brand. Good: `split_before_after`, `text_hero`, `ugc_screenshot`. Bad: `headspace_style`, `new_template_1`.
- If a similar proposal already exists in `proposed`, **reuse the same name** — this is the second upvote toward promotion, don't fork it.

Bounds in `zones_sketch` should be approximate. Pixel-level precision belongs in the full teardown or the eventual template module, not the proposal.

### 3B.2 — The layout is a one-off not worth reusing

If the ad's layout is clearly a one-off (e.g. an odd product shoot that no one else would copy), record neither a match nor a proposal. Return `NO_MATCH: <reason>` to the caller and move on. The calling skill can still include the ad in its analysis — we just don't pollute the registry.

### Return to the caller

For 3B.1 (`direct_add: false`): `PROPOSE: new:<snake_case_name> — <one-sentence rationale>`
For 3B.1 (`direct_add: true`): `NEW: <snake_case_name> — <one-sentence rationale>`
For 3B.2: `NO_MATCH: <one-sentence reason>`

---

## Step 4 — Promotion (when a proposal hits the threshold)

This step is **not** run on every call. It runs when the caller notices (or a periodic audit notices) that a proposal has 2+ entries with the same `name`.

Promotion turns a proposal into a real template module:

1. Read the proposal entries from `scripts/templates/usage.json` — use their `zones_sketch` as the starting point for a concrete layout.
2. Create `scripts/templates/<name>.py`. Start from `scripts/templates/custom.py` as the base — most new templates are just frozen `custom` specs with a name. Keep any per-brief overrides (fonts, colors, spacing) configurable via the brief YAML's `composite:` section.
3. Register it in `scripts/templates/__init__.py`:
   - Import the module
   - Add it to the `_TEMPLATES` dict
4. Update `scripts/templates/usage.json`:
   - Add a new entry under `templates.<name>` with the proposal's description
   - Copy the proposal entries into `templates.<name>.matches` (they become the template's first upvotes — not lost)
   - Remove the matching entries from `proposed`
5. Verify: `python3 scripts/generate-ads.py --list-templates` should show the new template, and `--template-stats` should show it with the carried-over upvotes.

Do not promote on a single proposal. The whole point of the upvote threshold is to filter out layouts that don't actually recur.

---

## Step 5 — Inspection Commands (for the user)

Tell the user to run these when they ask "what templates do we have?" or "what's trending?":

```
python3 scripts/generate-ads.py --list-templates
python3 scripts/generate-ads.py --template-stats
```

`--template-stats` prints both upvotes and pending proposals, so a user can eyeball which proposal is close to promotion.

---

## Return Shape (Contract for Callers)

Every invocation returns exactly one of:

| Return | Meaning | Calling skill should |
|--------|---------|----------------------|
| `MATCH: <template> (<confidence>) — <rationale>` | Ad matches an existing template; upvote recorded | Embed the template name in the analysis output |
| `PROPOSE: new:<name> — <rationale>` | No match; `direct_add: false`; new template added to `proposed` | Embed "Template match: none — proposing new:<name>" |
| `NEW: <name> — <rationale>` | No match; `direct_add: true`; new template added directly to `templates` | Embed "Template match: `new:<name>` (added)" |
| `NO_MATCH: <reason>` | No match; layout not worth reusing; nothing persisted | Embed "Template match: none — one-off layout, not proposed" |

This contract is stable. Callers should not depend on any other output from this skill.

---

## Checklist

Before returning to the caller:

- [ ] Loaded `scripts/templates/usage.json` and read each template module's docstring
- [ ] Compared structural signature, not color/brand
- [ ] Picked exactly one outcome: MATCH, PROPOSE, or NO_MATCH
- [ ] If MATCH or PROPOSE: persisted the entry in `usage.json` with today's date, source, ad_id, brand
- [ ] If PROPOSE and a similar proposal already exists: reused the same `name` (not a fork)
- [ ] Returned the string in the exact shape from the table above
