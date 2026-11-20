---
name: scale-ad-variants
description: "Reads the current top-performing Meta ads from output/analysis/meta-ads/top/index.md, devises N bold variants per requested ad (referencing tweak-divergence.md), presents the full plan to the user, then fires parallel ad-variants subagents for every iteration — one subagent per variant, no waiting for user input mid-run. Use when: iterate on top ads, create bold variants of the winners, produce meaningfully different creatives from our best ads, scale variants from top performers."
---

# Scale Ad Variants — Parallel Bold Variants of Top Meta Performers

Read the current top-performing ads, plan N bold variants per requested ad from the tweak-divergence playbook, confirm the plan with the user, then execute all variants in parallel via the `ad-variants` skill.

---

## Phase 0: Load Context

Read all files in parallel before doing anything else:

1. **`output/analysis/meta-ads/top/index.md`** — The ranked list of top ads (ad ID, name, description, visual system, metrics).
2. **`references/generation/tweak-divergence.md`** — The iteration playbook. This is **mandatory** for generating variant ideas.
3. **`references/brand/aurie.md`** and **`references/brand/aura.md`** — Brand voice, ICPs, visual themes, and ad avoids.
4. **`output/variants/index.md`** — The log of all previous variant runs. Read this to avoid duplicating work that has already been done.

Check `.env` exists. If missing, ask the user to create it from `.env.sample` before proceeding.

---

## Phase 1: Resolve Source Images

For the requested top ads (or all if not specified), find the primary 9:16 source image:

```bash
ls output/analysis/meta-ads/top/{ad_id}/
```

**Selection rule:** Use the first `.jpg` file alphabetically in the folder that has the 9x16 aspect ratio. If no 9:16 image exists, ask the user to choose from the available options.

Record a mapping: `{ ad_id → primary_image_path }` for use in the subagent prompts.

---

## Phase 2: Plan Variants

For the requested top ads, devise N variants (default 3 per ad unless the user asked for more), using `tweak-divergence.md` for ideas.

**Broad rules:** ~70% of variants should vary visuals while keeping the source ad's overall style and feel — same layout structure, same general aesthetic, but combine 2–3 visual changes at once so the result is clearly distinct. ~30% should be persona shifts — targeting a different audience segment with an updated hook and supporting copy changes, plus 1–2 visual changes to match the new persona.

Variants should feel like siblings of the source, not completely different creatives. The goal is to change enough that a different person might stop scrolling, while still being recognizable as the same ad family. A single accent color swap or background scene change is too little — always combine at least 2 dimensions; a full art-direction overhaul is too much.

### Deduplication check (mandatory before finalising the plan)

Before locking in any variant, cross-check it against two sources:

1. **Other top-5 ads** (from `output/analysis/meta-ads/top/index.md`): If a proposed variant closely mirrors the visual system of another top-5 ad (e.g. proposing a pure black canvas when a black-canvas orb ad is already in the top 5, or proposing a warm painterly female figure when that style is already running), replace it with a genuinely novel direction. The account is already testing those visual systems — generating a near-duplicate adds no signal.

2. **Previous variant runs** (from `output/variants/index.md`): If a proposed variant closely matches a description in the index (same persona shift, same scene, same color palette swap), replace it with a different angle. Check the description column, not just the label.

A variant passes the deduplication check only if neither its primary visual system nor its persona/copy angle is already represented in the top-5 or the variant history.

### Variant plan format

Build a structured plan for all N variants:

```
Ad #{rank} — {ad_name} (ID: {ad_id})
Source image: output/analysis/meta-ads/top/{ad_id}/{primary_image}

  Variant A — {short label, e.g. "couple-blush-garden"}
    Dimension: {which tweak-divergence dimension(s), e.g. "Subject + Background + Color"}
    Change: {one precise sentence describing exactly what changes}
    Hypothesis: {one sentence — what this tests}

  Variant B — {short label, e.g. "hook-burnout-persona"}
    Dimension: {which tweak-divergence dimension(s)}
    Change: {one precise sentence}
    Hypothesis: {one sentence}
```

---

## Phase 3: Present to User

Show the full variant plan as a readable table before executing anything.

```
## Scale Ad Variants — Variant Plan

**Source:** output/analysis/meta-ads/top/index.md
**Total variants:** N

| # | Ad | Var | Label | Dimension(s) | Change summary |
|---|----|-----|-------|--------------|----------------|
| 1 | ChatGPT Mental Health | A | couple-blush-garden | Subject + Background + Color | Replace solo female with couple in a blush-toned garden setting |
| 1 | ChatGPT Mental Health | B | hook-burnout-persona | Copy + Persona + Visual | Shift hook to burnout survivors, darker hair, indoor cozy scene |
| 2 | Orb Winners + Testimonials | A | ... | ... | ... |
...

**Ready to generate?** I'll fire all N in parallel via the ad-variants skill.
Reply "go" (or any confirmation) to proceed, or give feedback to adjust the plan first.
```

Wait for the user to confirm or redirect before moving to Phase 4. If the user suggests changes, update the plan and re-show the table.

---

## Phase 4: Execute in Parallel

Once the user confirms, fire **one subagent per variant** in a single message (all N in parallel).

### Subagent prompt template

Each subagent receives a self-contained prompt. No back-and-forth with the user — every decision has already been made in Phase 2–3.

```
Run the ad-variants skill on the following ad. All information needed is provided here — do NOT ask for direction or confirmation at any phase; proceed autonomously through all phases.

**Source image path:** `output/analysis/meta-ads/top/{ad_id}/{primary_image}`

**Requested change:**
{the precise change sentence from the variant plan — be explicit about every element that must change AND every element that must stay the same. For persona/subject changes, lead with what is being removed or replaced BEFORE listing what to preserve. Do NOT list the element being changed in the preservation anchor.}

**Execution notes:**
- Default to Complete AI edit (do not ask the user to choose the method).
- The source image is already in place — do NOT copy it to /tmp; pass the path directly to the API.
- Make sure ALL requested changes are visibly present in the generated image — verify before saving. If a change is absent (e.g. a person is still there after being instructed to remove them), retry once with a more explicit lead instruction.
- Save output as `final.png` in `output/variants/{YYYYMMDD-scale-variants}/{ad_id}-{variant_label}/` (create folder if needed).
- After generating, show the final image inline and state its path.
- No tracker update, no data.json rows, no QA scoring needed.
- No index.md update needed — the orchestrator will update `output/variants/index.md` after all variants complete.
```

Populate `{YYYYMMDD-scale-variants}` with today's date (e.g. `20260505-scale-variants`) as a shared session folder across all N variants.

### Parallel execution

All N subagents must be launched in a **single message** with N Agent tool calls. Do not stagger them.

---

## Phase 5: Report Results

Once all subagents complete, collect their outputs and present a summary:

```
## Scale Ad Variants — Complete

**Session folder:** output/variants/{YYYYMMDD-scale-variants}/

| # | Ad | Var | Label | Output |
|---|----|-----|-------|--------|
| 1 | ChatGPT Mental Health | A | couple-blush-garden | [final.png](output/tweaks/…/final.png) |
| 1 | ChatGPT Mental Health | B | hook-burnout-persona | [final.png](output/tweaks/…/final.png) |
...

**Completed:** {N}/{N}
**Any failures:** {list subagent ID + reason, or "none"}

Next steps:
- Review images inline above.
- Push to GitHub when ready: /marketing:push-project-updates
```

If any subagent failed or produced a drifted result, note it and suggest a manual retry with `/ad-variants`.

After presenting the summary, update `output/variants/index.md` with all N new rows (successful variants only). Follow the same Phase 7 rules as `ad-variants`: prepend rows newest-first, keep only 15 total, create the file with the standard header if it doesn't exist. Use the session folder date as "Ran on" and relative paths from `output/variants/` for the image links.

---

## Key Rules

- **Always read tweak-divergence.md before planning.** Every variant should trace to dimensions in that file.
- **N variants per ad must target different top-level dimensions.** No two same-dimension variants for the same ad.
- **Deduplication is mandatory.** Every variant must pass the deduplication check against both the top-5 visual systems and `output/variants/index.md` before being included in the plan.
- **Aim for bold, meaningfully different creatives.** A single color swap is not enough — combine visual dimensions to create something that would stop a genuinely different scroller.
- **Never ask the user mid-run.** Everything is decided in Phase 2–3 and confirmed before execution.
- **All N subagents launch in a single message.** Parallel, not sequential.
- **No tracker updates, no data.json rows.** Variants live under `output/variants/` only.
- **Default to Complete AI edit.** Do not escalate to AI + Pillow overlay unless the subagent explicitly notes it's needed after a failed AI edit attempt.
