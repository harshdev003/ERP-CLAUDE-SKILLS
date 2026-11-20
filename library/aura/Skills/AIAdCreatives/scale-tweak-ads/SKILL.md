---
name: scale-tweak-ads
description: "Reads the current top-performing Meta ads from output/analysis/meta-ads/top/index.md, devises N iterations per requested ad (referencing tweak-divergence.md), presents the full plan to the user, then fires parallel tweak-ads subagents for every iteration — one subagent per tweak, no waiting for user input mid-run. Use when: iterate on top ads, tweak the winners, produce variants of our best ads, scale tweaks from top performers."
---

# Scale Tweak Ads — Parallel Iteration of Top Meta Performers

Read the current top-performing ads, plan N iterations per requested ad from the tweak-divergence playbook, confirm the plan with the user, then execute all iterations in parallel via the `tweak-ads` skill.

---

## Phase 0: Load Context

Read all files in parallel before doing anything else:

1. **`output/analysis/meta-ads/top/index.md`** — The ranked list of top ads (ad ID, name, description, visual system, metrics).
2. **`output/tweaks/index.md`** — Full history of previous tweak runs across all ads (descriptions, labels, ad IDs).
3. **`references/generation/tweak-divergence.md`** — The iteration playbook. This is **mandatory** for generating iteration ideas.
4. **`references/brand/aurie.md`** and **`references/brand/aura.md`** — Brand voice, ICPs, visual themes, and ad avoids.

Check `.env` exists. If missing, ask the user to create it from `.env.sample` before proceeding.

After reading, build two dedup tables to use in Phase 2:

**A — Prior tweaks for each target ad** (from `tweaks/index.md`): extract every row whose description mentions the target `ad_id`. Note the key change made (hook swap, palette, subject, background, etc.).

**B — Visual/hook coverage of existing top performers** (from `top/index.md`): for each top-5 ad, note its distinctive visual and hook (e.g. "black canvas + orb", "warm rose-gold character", "anxiety hook"). An iteration that produces something visually or conceptually identical to an existing top performer is redundant — it already exists and is already being tested at scale.

---

## Phase 1: Resolve Source Images

For the requested top ads (or all if not specified), find the primary 9:16 source image:

```bash
ls output/analysis/meta-ads/top/{ad_id}/
```

**Selection rule:** Use the first `.jpg` file alphabetically in the folder that has the 9x16 aspect ratio. If no 9:16 image exists, ask the user to choose from the available options.

Record a mapping: `{ ad_id → primary_image_path }` for use in the subagent prompts.

---

## Phase 2: Plan Iterations

For the requested top ads, devise N iterations (default 3 per ad unless the user asked for more), using `tweak-divergence.md` for ideas.

**Broad rules:** 
- The visuals should differ significantly across iterations — not just a color swap or background change.
- ~70% of iterations should keep the copy unchanged and vary only visuals
- ~30% should be persona shifts as well — targeting a different audience segment with an updated hook and supporting copy changes

### Deduplication (mandatory before finalising the plan)

Before locking in any iteration, check it against both dedup tables from Phase 0:

1. **Against prior tweaks (table A):** If a proposed iteration is substantially the same change already attempted for the same ad (same hook swap, same background type, same palette direction), discard it and pick a different dimension.
2. **Against existing top performers (table B):** If the proposed output would look like an existing top-5 ad (same canvas colour, same subject type, same hook), discard it — that creative already exists and is being tested at scale.

An iteration passes dedup only if it is genuinely novel on both checks. If you can't find N non-duplicate iterations from `tweak-divergence.md`, reduce N and explain why in the plan.

### Iteration plan format

Build a structured plan for all N iterations:

```
Ad #{rank} — {ad_name} (ID: {ad_id})
Source image: output/analysis/meta-ads/top/{ad_id}/{primary_image}

  Iteration A — {short label, e.g. "orb-color-amber"}
    Dimension: {which tweak-divergence dimension, e.g. "Color and Theme — accent color swap"}
    Change: {one precise sentence describing exactly what changes}
    Hypothesis: {one sentence — what this tests}

  Iteration B — {short label, e.g. "hook-relationships"}
    Dimension: {which tweak-divergence dimension}
    Change: {one precise sentence}
    Hypothesis: {one sentence}
```

---

## Phase 3: Present to User

Show the full iteration plan as a readable table before executing anything.

```
## Scale Tweak Ads — Iteration Plan

**Source:** output/analysis/meta-ads/top/index.md
**Total iterations:** N

| # | Ad | Iter | Label | Dimension | Change summary |
|---|----|------|-------|-----------|----------------|
| 1 | ChatGPT Mental Health | A | orb-color-amber | Color / Theme | Swap orb accent from lavender-pink to warm amber/gold |
| 1 | ChatGPT Mental Health | B | hook-trauma | Copy / Persona | Replace hook with "ChatGPT, but for Trauma Survivors" |
| 2 | Orb Winners + Testimonials | A | ... | ... | ... |
...

**Ready to generate?** I'll fire all N in parallel via the tweak-ads skill.
Reply "go" (or any confirmation) to proceed, or give feedback to adjust the plan first.
```

Wait for the user to confirm or redirect before moving to Phase 4. If the user suggests changes, update the plan and re-show the table.

---

## Phase 4: Execute in Parallel

Once the user confirms, fire **one subagent per iteration** in a single message (all N in parallel).

### Subagent prompt template

Each subagent receives a self-contained prompt. No back-and-forth with the user — every decision has already been made in Phase 2–3.

```
Run the tweak-ads skill on the following ad. All information needed is provided here — do NOT ask for direction or confirmation at any phase; proceed autonomously through all phases.

**Source image path:** `output/analysis/meta-ads/top/{ad_id}/{primary_image}`

**Requested change:**
{the precise change sentence from the iteration plan — be explicit about every element that must change AND every element that must stay the same. For persona/subject changes, lead with what is being removed or replaced BEFORE listing what to preserve. Do NOT list the element being changed in the preservation anchor.}

**Execution notes:**
- Default to Complete AI edit (do not ask the user to choose the method).
- The source image is already in place — do NOT copy it to /tmp; pass the path directly to the API.
- Make sure ALL requested changes are visibly present in the generated image — verify before saving. If the change is absent (e.g. a person is still there after being instructed to remove them), retry once with a more explicit lead instruction.
- Save output as `final.png` in `output/tweaks/{YYYYMMDD-scale-tweak}/{ad_id}-{iter_label}/` (create folder if needed).
- After generating, show the final image inline and state its path.
- No tracker update, no data.json rows, no QA scoring needed.
```

Populate `{YYYYMMDD-scale-tweak}` with today's date (e.g. `20260505-scale-tweak`) as a shared session folder across all N iterations.

### Parallel execution

All N subagents must be launched in a **single message** with N Agent tool calls. Do not stagger them.

---

## Phase 5: Report Results

Once all subagents complete, collect their outputs and present a summary:

```
## Scale Tweak Ads — Complete

**Session folder:** output/tweaks/{YYYYMMDD-scale-tweak}/

| # | Ad | Iter | Label | Output |
|---|----|------|-------|--------|
| 1 | ChatGPT Mental Health | A | orb-color-amber | [final.png](output/tweaks/…/final.png) |
| 1 | ChatGPT Mental Health | B | hook-trauma | [final.png](output/tweaks/…/final.png) |
...

**Completed:** {N}/10
**Any failures:** {list subagent ID + reason, or "none"}

Next steps:
- Review images inline above.
- Push to GitHub when ready: /marketing:push-project-updates
```

If any subagent failed or produced a drifted result, note it and suggest a manual retry with `/tweak-ads`.

---

## Key Rules

- **Always read tweak-divergence.md before planning.** Every iteration must trace to a named dimension in that file.
- **Always read tweaks/index.md and top/index.md before planning.** Every iteration must pass the Phase 2 dedup check — no repeating prior tweaks for the same ad, no producing a creative that already exists as a top performer.
- **N iterations per ad must target different top-level dimensions.** No two same-dimension tweaks for the same ad.
- **Preserve layout.** Hook line count, bullet structure, zone proportions are untouched.
- **Never ask the user mid-run.** Everything is decided in Phase 2–3 and confirmed before execution.
- **All N subagents launch in a single message.** Parallel, not sequential.
- **No tracker updates, no data.json rows.** Tweaks live under `output/tweaks/` only.
- **Default to Complete AI edit.** Do not escalate to AI + Pillow overlay unless the subagent explicitly notes it's needed after a failed AI edit attempt.
