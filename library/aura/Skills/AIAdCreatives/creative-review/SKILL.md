---
name: creative-review
description: "Visual review of composite ad images after generation. Views each composite, checks against the winning ad formula, and automatically re-composites or flags for character regeneration if issues are found. This is the quality gate between generation and the tracker. Use when: review composites, check images, review after generation, do these look right, visual review. MANDATORY TRIGGERS: review composites, check images, visual review, review after generation."
---

# Creative Review Skill — Post-Generation Visual Gate

Review every composite ad image immediately after generation. If a composite has issues, **fix it automatically** (re-composite with adjusted parameters) or flag it for character regeneration. This is the quality gate — nothing goes to the tracker until it passes.

**This skill replaces manual visual review.** It automates the kind of feedback like "character is too cropped", "there's a bar on the right", "too high", "background rectangle visible".

---

## When to Use

- **Automatically after generating composites** — this skill runs as part of the post-generation loop (see CLAUDE.md "Post-Generation Workflow")
- When the user says "review the composites", "check the images", "do these look right"
- After batch re-compositing to verify fixes
- **Iteration limit:** If auto-fixes don't resolve issues after **3 iterations**, stop and ask the user for input instead of looping further

---

## Step 0: Load Data & Briefs

1. Read `output/data.json` to get BRIEFS, CREATIVES arrays.
2. For each row to review, read the brief YAML from the brief folder (e.g., `output/briefs/{folder}/brief-{###}.yaml`).
3. Identify which rows to review (all, or specific rows if requested).

### Knowledge Files to Load

Load these for visual reference and known failure patterns:

- `output/analysis/winners/` — Winning ad teardowns; scan for pixel-level specs to use as visual benchmarks
- **Every file under `references/rendering/`** — compositor rules that explain why specific rendering issues happen (bullet truncation, checkmark boxes, hook wrapping, opaque fills). Load these before proposing auto-fixes so the fix addresses the underlying rule, not the symptom.

**Do not compare to the winning ad (001).** Each creative is evaluated only against its own brief, its own data row, and the composite specs in CLAUDE.md.

---

## Step 1: View & Evaluate Each Composite

**Use the `Read` tool to view each composite image.** Then check:

### Brief Match (cross-reference composite against the brief YAML)

| Check | What to compare |
|-------|----------------|
| Hook text | Does the text on the image match the `hook` zone `content` in `visual.layout.zones`? Flag any mismatches, typos, or outdated hooks. |
| Bullet copy | Do the 3 bullet lines match the `bullets` zone `content` in `visual.layout.zones`? Check for correct app-specific bullets (Aurie vs Aura). |
| Character vs visual direction | Does the character match `visual.subject` in the brief (species, pose, emotion)? |
| Color palette | Does the character color match `visual.color_palette`? Especially important for color variant rows. |
| App branding | Is this labeled as the correct app (Aurie vs Aura)? Check bullets, link URL, and overall tone match. |

### Character Issues (auto-fixable via re-composite)

| Issue | How to detect | Fix |
|-------|--------------|-----|
| Too cropped | Important parts cut off (ears, question marks, tail, paws) | Reduce `scale_factor` by 0.05–0.10 |
| Too small | Character looks lost, doesn't fill the zone | Increase `scale_factor` by 0.05–0.10 |
| Too high | Character floating near top, bottom of zone empty | Increase `y_shift` by +20–40px |
| Too low / overlaps text | Character bleeds into hook text area | Decrease `y_shift` or reduce `scale_factor` |
| Background rectangle | Visible lighter rectangle around character | Increase `bg_threshold` (try 70–80) in background removal |
| Bar/strip artifact | Pink bar extending from platform on right side | Paint out the specific region before compositing (target x > 75% of image width, below character body) |
| Warm glow too bright | Visible brownish patch in top-right | Reduce glow alpha (target ~8/255 max) |
| Platform bleeds into text | Platform visible below the gradient fade line | Ensure hard-clip at `char_zone_h` with 80px gradient fade |

### Character Issues (requires regeneration — flag only, don't auto-fix)

| Issue | How to detect |
|-------|--------------|
| Wrong style | 3D render instead of flat 2D illustration |
| Wrong color | Body color doesn't match `visual.color_palette` in the brief |
| Dark eye mask | Dark patches around eyes that look painted on (common with raccoon characters) |
| AI text on image | Gemini rendered words/letters on the character image |
| Fundamentally wrong pose | Character doesn't match the brief's visual direction |

### Text Issues (auto-fixable via re-composite)

| Issue | How to detect | Fix |
|-------|--------------|-----|
| Wrong hook text | Doesn't match CREATIVES `image_hook` field | Re-composite with correct text |
| Checkmarks too big/small | Visually mismatched with bullet text | Ensure font_check is 36px DejaVu |
| Too much check-text gap | Checkmark and bullet text feel disconnected | Reduce gap to 4px |
| Text overlaps character | Hook text sits on top of character pixels | Increase `char_zone_pct` or adjust `text_y_pct` |

### Custom-layout structural misalignment (auto-fixable via measure-and-realign)

This applies to `template: custom` briefs where the AI was asked to draw structural elements (cards, mockup chrome, frames) AND Pillow zones overlay text inside those elements. Image models don't honor pixel-exact placement instructions, so the AI's structural elements rarely land where the YAML zones assume.

**The measure-and-realign workflow** (also documented in [`references/rendering/composite-rendering-rules.md`](../../../references/rendering/composite-rendering-rules.md)):

---

## Step 2: Auto-Fix What's Possible

For any issue that can be fixed by re-compositing:

1. Note the specific parameter change needed (e.g., `scale_factor: 0.82 → 0.75`)
2. Re-run the composite with the adjusted parameters
3. View the new composite to verify the fix
4. If still not right, try one more adjustment. After 2 attempts, flag for manual review.

**Use the standard compositing approach from CLAUDE.md** — create_canvas(), clean_and_place(), add_text().

---

## Step 3: Flag What Needs Regeneration

For character-level issues that compositing can't fix:

```
⚠ Row {#} — NEEDS CHARACTER REGENERATION
Issue: {specific problem}
Suggested prompt adjustment: {what to change in the Gemini prompt}
```

**Do not regenerate automatically.** Always flag and wait for user confirmation. Character regeneration changes the design and can break rows that were already approved.

---

## Step 4: Output the Review Report

```
# Creative Review — {date}

## Passed ({N} rows)
Rows: 1, 2, 3, 5, 7, ...

## Auto-Fixed ({N} rows)
- Row {#}: {what was wrong} → {what was changed} ✓
- Row {#}: {what was wrong} → {what was changed} ✓

## Needs Regeneration ({N} rows)
- Row {#}: {issue} — awaiting confirmation
- Row {#}: {issue} — awaiting confirmation

## Summary
Pass rate: X/Y
Auto-fixed: Z rows
Flagged: W rows
```

---

## Common Patterns From Past Reviews

These are recurring issues found during manual reviews. Check for them proactively:

1. **Gemini vertical strips**: Characters often have a vertical accent strip on the right edge. Paint it out during compositing (target rightmost columns where strip color matches character body).
2. **Raccoon eye masks**: Raccoon characters naturally get dark masks around the eyes. If the mask makes the character look "painted," flag for regeneration with explicit "NO dark eye mask, face same color as body" in the prompt.
3. **3D render style creep**: Some characters come back as 3D renders (fuzzy texture, ambient occlusion, reflections). These need full regeneration with "FLAT 2D illustration" emphasized.
4. **Platform bar artifacts**: The 3D platform sometimes generates a disconnected dark bar on the right side, especially below the platform. Paint it out by making those pixels transparent.
5. **Background gradient/vignette**: Gemini sometimes adds subtle gradients or vignettes to the background instead of the uniform background color. These show up as visible rectangles in the composite. Fix by increasing the background removal threshold.
6. **Brownish warm pixels**: Some characters have brownish/warm dark pixels (around R=60, G=50, B=40) that are different enough from the canvas BG to show through as artifacts. Clean them by targeting `R>25, R<120, G<90, B<70, R>B` pixels that aren't the character's accent color.
7. **No right accent strip**: The winning ad (001) has a vertical pink accent strip on the right edge, but this is **not** part of the current composite formula. Do not flag its absence as an issue. See CLAUDE.md: "No right accent strip needed."

---

## Severity Levels

Rate every issue as one of:

- **Minor** — Small imperfection that doesn't hurt the creative's effectiveness (e.g., slightly strong glow, character positioned a touch high, minor background gradient). Log it in the report but **pass the creative**. Do not ask the user or trigger a redo.
- **Moderate** — Noticeable issue that should be auto-fixed via re-composite (e.g., character too cropped, wrong hook text, checkmark size mismatch). Auto-fix and re-review, up to 3 iterations.
- **Critical** — Fundamental problem that blocks the creative (e.g., wrong style/3D render, wrong color, AI text on character, completely wrong layout). Flag for user input or character regeneration.

**Rule: If all issues on a creative are Minor or lower, the creative passes. Only ask the user when there are Moderate issues that can't be auto-fixed after 3 attempts, or Critical issues.**

## Output File Links

At the end of every review, output clickable markdown links for every composite image reviewed, so the user can open them directly:

```
Reviewed: [output/briefs/{folder}/images/{filename}.png](output/briefs/{folder}/images/{filename}.png)
```

## Quality Standards

- **Always view the image.** Never assess from filenames or metadata alone.
- **Evaluate against the brief and data row only.** Do not compare to other rows or the winning ad.
- **Fix before flagging.** If a compositing parameter change can solve the problem, do it. Only flag for regeneration when compositing truly can't help.
- **Be specific about fixes.** "Reduce scale_factor to 0.75" not "make it smaller."
- **One redo max for auto-fixes.** If the auto-fix doesn't work on second try, flag for manual review instead of looping.
- **Don't over-flag.** Minor issues are logged, not escalated. The goal is to ship good creatives, not perfect ones.
