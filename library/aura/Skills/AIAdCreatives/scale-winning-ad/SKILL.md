---
name: scale-winning-ad
description: "End-to-end skill that turns a winning or competitor ad into a new Aurie/Aura creative. Every brief produced MUST trace to a real reference ad in `output/analysis/winners/` (or a competitor ad the user just provided that gets torn down in Phase 1) — does not fabricate or extend untested in-house formats. Runs the winning-creative-analysis teardown, produces a brief with a `reference_winner` pointer, generates character + composite images, runs creative review, scores QA, and rebuilds the tracker. Does NOT push to GitHub. Use when: scale this ad, replicate this ad, make our version of this ad, turn this competitor ad into a brief, clone this winning ad, reproduce this formula, generate N ads from current winners. Do NOT use for net-new ideas without a reference — that's `scale-new-ads`."
---

# Scale Winning Ad — End-to-End Skill

Turn a competitor or winning ad into a production-ready Aurie/Aura creative. The skill runs the full pipeline:

1. **Analyze** — pixel-level teardown of the reference ad
2. **Brief** — write a brief derived from the winning formula
3. **Generate** — Gemini character → Pillow composite
4. **Review** — visual quality gate
5. **Score** — QA scoring
6. **Publish** — rebuild tracker + sync Supabase

---

## Hard Constraint: Every Brief Must Trace to a Real Winner

This skill is *only* for scaling proven winners. Every brief produced by this skill must trace to a specific reference ad in `output/analysis/winners/` (an ad with a teardown `analysis.md` file) or to a competitor ad the user just provided that you teardown in Phase 1.

**Forbidden:**

- Do NOT extend unproven in-house formats (recent `scale-new-ads` briefs, custom layouts that haven't been tested, `proposed` template entries) and label the result `source: scale-winning-ad`. Those are net-new ideas, not winner scales.
- Do NOT invent a "winner" by analogy ("this is similar to the orb format, so it counts"). The reference must be an actual ad that was tested in market — Aurie's own or a competitor's.
- Do NOT mix sources within one brief. Each brief scales exactly one reference ad. If you need a different reference, that's a different brief.

**Required for every brief:**

- A **`reference_winner`** field in the brief YAML pointing at the teardown file path (e.g. `output/analysis/winners/ad-analysis-803825368978289/analysis.md`)
- A **`competitor_intel`** field that names the specific reference and the specific element being adapted (hook formula, visual layout, character formula, etc.)

If the user asks for N briefs and you only have M < N distinct winners on file, do **not** invent more reference ads. Instead:

1. Run multiple horizontal/vertical iterations off the same winner (different angle, different ICP, different aesthetic variant). Each iteration is a separate brief but legitimately scales the same winner.
2. Or, if more truly fresh formats are needed, stop after M and tell the user: "I produced M winner-scaled briefs; producing the remaining N–M would require either a new competitor ad to teardown, or running `scale-new-ads` instead."

This is non-negotiable. A `scale-winning-ad` run that includes a brief without a real winner reference is a bug.

---

## What the User Must Provide

Before starting, collect all of the following. If anything is missing, ask before proceeding.

| Input | Required | Notes |
|-------|----------|-------|
| Ad image(s) | Yes | Full-resolution screenshot(s) of the creative. Also the expanded primary text if truncated in Ad Library. |
| Ad Library ID | Preferred | e.g. `803825368978289`. Used for file naming. If unavailable, ask the user for a short slug (e.g. `headspace-sleep-rabbit`). |
| App to produce for | Yes | Aurie or Aura. Default: Aurie if unspecified and the reference is a mental-health/emotional processing ad. |
| Angle adaptation | Optional | Any specific angle or emotion to test (e.g. "target grief instead of anxiety"). If not provided, adapt the dominant angle from the reference ad. |
| Variants to produce | Optional | Default: 1 composite. User can request color variants, message variants, etc. |

---

## Phase 1: Identify or Teardown the Reference Ad

Every brief must trace to one specific winner teardown. Two paths:

### Path A — User provided a fresh ad (or asked to scale a competitor)

Run the **winning-creative-analysis** skill against the provided ad material.

Follow the full `winning-creative-analysis` methodology:

1. Load knowledge files: `references/competitors.md`, `references/research/ad-research-findings.md`, scan `output/analysis/winners/` for past teardowns
2. Analyze all 7 sections: Why It's a Winner, Ad Components, Copy Strategy, Image Pixel Spec, Why This Image Works, Strategic Analysis, Key Learnings
3. Save to `output/analysis/winners/ad-analysis-<ad-library-id>/analysis.md` (or `ad-analysis-<slug>/analysis.md`)
4. Record the path — it becomes the brief's `reference_winner` value.

### Path B — User asked to scale from existing winners ("generate N ads from current winners")

Don't invent. Inventory:

```bash
ls output/analysis/winners/
```

---

## Phase 2: Write the Brief

Adapt the winning formula to our brand and the target app. Do not copy — translate.

- [`references/generation/angle-divergence.md`](../../references/generation/angle-divergence.md) — core principle and 5 axes that variants must diverge on across the batch

### Iteration Mode: Horizontal vs. Vertical

Before writing the brief, decide which scaling mode applies.

- **Horizontal** — same angle, different execution (hook type, format, CTA, psychology lever). Use when widening volume on the proven message.
- **Vertical** — same proven structure, different emotional vertical or audience. Use when expanding reach to a new segment.

### Brand and Messaging

- **Brand voice, identity, and audience** — load `references/brand/{aurie|aura}.md` for the target app.
- **Messaging spine, verticals, and reusable copy bank** — load `references/messaging/{aurie|aura}/messaging.md` and `references/messaging/{aurie|aura}/verticals-and-copy.md`. The on-image bullets and signup/link URL live in the brand and messaging files — pull them from there, do not hardcode.

### Determine the Brief Number

1. Read `output/data.json` → `BRIEFS` array
2. Find the highest existing Brief # → new brief gets Brief # + 1
3. Zero-pad to 3 digits for the folder name

### Brief YAML structure

Create a YAML file at `output/briefs/{NUM:03d}-{APP}-{angle}/brief-{NUM:03d}.yaml` following [`references/formats/image/image-gen-spec.md`](../../references/formats/image/image-gen-spec.md).

Two fields are specific to this skill and must be added:
- `reference_winner:` — path to the teardown file, e.g. `output/analysis/winners/ad-analysis-{id}/analysis.md`
- `source: scale-winning-ad`

### Add rows to `output/data.json`

Append one new row to each of the 3 arrays. Use today's date. Brief # must match across all three arrays.

**BRIEFS row:**
`[date, #, app, angle, hook_type, hook, "Static image", "Cold traffic", tone, strategy, competitor_intel, rationale, risk, "High", "New", "scale-winning-ad", folder]`

**CREATIVES row:**
`[date, #, app, angle, primary_text, headline, description, cta_button, image_direction, "", image_hook, image_proof]`
- `image_path` (index 9) starts blank — filled after images are generated

**QA row:**
`[date, #, app, "", "", "", "", "", "", ""]`
- Scores left blank — filled during Phase 5

---

## Phase 3: Generate the Creative

### Step 3A: Generate the character image

Run `scripts/generate-ads.py` for the new brief number, or call `scripts/generate-char.py` directly:

```bash
cd /Users/rohan/AuraHealth/AIAdCreatives
python3 scripts/generate-ads.py --brief {NUM}                        # OpenAI (default)
python3 scripts/generate-ads.py --brief {NUM} --provider gemini      # Gemini
```

If `generate-ads.py` doesn't support `--brief`, run `generate-char.py` with the brief folder and image direction from the YAML:

```bash
python3 scripts/generate-char.py --pose "..." --name slug            # OpenAI (default)
python3 scripts/generate-char.py --pose "..." --provider gemini      # Gemini
```

**Before running:** verify `.env` exists with `OPENAI_API_KEY` (default provider). If using `--provider gemini`, check for `GEMINI_API_KEY` instead. If the required key is missing, stop and ask the user.

The character image saves to `output/briefs/{folder}/characters/`.

### Step 3B: Composite the text overlay

Run the compositing pipeline (Pillow) to produce the final image at 1080×1920 with all text overlays.

The generator dispatches by the brief's `template:` field — `subject_hook_bullets` (default), `custom`, or any promoted template. All on-image text and styling is driven by `visual.layout.zones` in the brief YAML.

**MANDATORY:** Load every file under `references/rendering/` before running the compositor — these rules govern bullet font sizing, checkmark glyph coverage, hook line wrapping, raw-file requirements, and fill-zone alpha. Apply them to the brief YAML's `composite:` and `layout:` blocks before generation.

Follow the full canvas spec from CLAUDE.md:
- Canvas: 1080×1920, background from `visual.color_palette` in the brief YAML (default `#11161A`)
- Hook: Poppins-Medium 80px, white (default `#FFFFFF`), centered — adjustable per brief
- Bullets: Poppins-Regular 36px, light gray (default `#DCDCDC`) — adjustable per brief
- Checkmarks: DejaVuSans 36px (Poppins lacks this glyph)
- Hard-clip character at char zone with 80px gradient fade

Output goes to `output/briefs/{folder}/images/`.

### Step 3C: Update image_path in data.json

After compositing, fill in the `image_path` field (CREATIVES index 9) with the relative path from `output/`:

```
briefs/{folder}/images/{filename}.png
```

---

## Phase 4: Creative Review

Run the **creative-review** skill on the new composite(s).

### Auto-fix loop

- View each composite, check against the brief YAML
- Auto-fix compositing issues (scale, crop, artifacts) — up to 3 iterations
- If character regeneration is needed, **stop and ask the user** — never regenerate automatically
- Pass Minor-only issues; only escalate Moderate+ that don't auto-fix

---

## Phase 5: QA Scoring

Run the **creative-scorer** skill on the new brief+creative pair.

---

## Phase 6: Rebuild Tracker

```bash
python3 scripts/build-tracker.py
```

Verify it completes without errors.

Then run the `preview` skill to start the tracker server (if not already running) and give the user a clickable link.

---

## Phase 7: Delivery Summary

Output a completion report. For multi-brief runs, include a winner-mapping table — every brief must show which winner it scaled. If you cannot fill in `Winner referenced` for any row, the brief should not have been written under this skill.

```
# Scale Winning Ad — Complete

## Briefs

| # | Angle | Format | Winner referenced | QA |
|---|-------|--------|-------------------|----|
| {NUM} | {angle} | {template/aesthetic} | [{winner-id}]({reference_winner path}) | {N}/10 |
| ... |

## Per-brief artifacts
- Brief #{NUM}: [brief YAML]({brief path}) · [composite]({image path}) · QA {N}/10 — {one-line verdict}
- ...

## Review Result
{Pass / Auto-fixed (what changed) / Flags for user}

## Tracker
Rebuilt: [output/tracker.html](output/tracker.html)

## Next Steps
- [ ] Push to GitHub when ready: /marketing:push-project-updates
- [ ] {Any flagged issues that need user attention}
```

---

## Key Rules

- **Every brief must trace to a real winner.** Path must point to a file under `output/analysis/winners/`. No winner → don't write the brief under this skill; use `scale-new-ads` instead.
- **Don't pad batches with non-winner-derived briefs.** If the user asks for N and you only have material for M < N, stop at M and report. Inventing a "fresh format" brief and labelling it `scale-winning-ad` is a bug.
- **`competitor_intel` must be specific.** Name the reference ad and the specific element being adapted. "Inspired by competitor research" is not acceptable.
- **Never let Gemini render text.** All text via Pillow compositing only.
- **Don't regenerate characters without user confirmation.** Compositing fixes first.
- **Never auto-push.** Always stop after tracker rebuild and let the user push when ready.
- **Do not copy the reference ad.** Translate the formula — different character, adapted copy, our brand voice.
- **All 5 Meta components required.** Primary Text, Headline, Description, CTA, Image Direction — never skip one.
- **Check `.env` before running generators.** If missing, ask the user.
- **Maximum 3 review iterations.** If compositing issues persist after 3 attempts, stop and ask the user.
