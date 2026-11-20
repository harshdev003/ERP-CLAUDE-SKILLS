---
name: similar-ads
description: "Generate N ad images visually similar to a reference ad. User shares an image; skill extracts the visual formula and generates N new images using the same layout, style, and structure — no brief writing, no metadata, no tracker updates. Use when: make more like this, generate similar images, N variations of this ad."
---

# Similar Ads — Image Generation Only

User shares a reference ad image. This skill reads the visual formula and generates N new images that follow the same structure. No copy, no briefs, no data.json changes.

**Before starting:** determine the app (Aurie or Aura) and load the matching brand guidelines — `references/brand/aurie.md` or `references/brand/aura.md`. These inform both the formula read and the ideation.

---

## What the User Must Provide

| Input | Required | Default |
|-------|----------|---------|
| Reference image | Yes | — |
| Number of images | No | 3 |
| App | No | Infer from image (Aurie or Aura if ambiguous) |
| Provider | No | `openai` (`gemini` also supported) |

If the reference image is not provided, ask for it before proceeding.

---

## Phase 1: Read the Reference

View the reference image and extract the visual formula using [`references/generation/formula-extraction.md`](../../../references/generation/formula-extraction.md).

Do not save this. Keep it in conversation context.

---

## Phase 2: Ideate N Variants

Generate N distinct visual concepts using [`references/generation/angle-divergence.md`](../../../references/generation/angle-divergence.md) for divergence dimensions. Draw hook angles from the app's messaging verticals — for Aurie: [`references/messaging/aurie/messaging.md`](../../../references/messaging/aurie/messaging.md) + [`verticals-and-copy.md`](../../../references/messaging/aurie/verticals-and-copy.md); for Aura: [`references/messaging/aura/messaging.md`](../../../references/messaging/aura/messaging.md) + [`verticals-and-copy.md`](../../../references/messaging/aura/verticals-and-copy.md). Each variant should feel like it could stop a different person mid-scroll — vary the subject, not the layout concept.

Present the N concepts to the user (one line each: subject + angle) and ask which to generate images for.

---

## Phase 3: Generate Images

**Check `.env` first** — verify it exists and has the required key for the chosen provider (`OPENAI_API_KEY` for OpenAI, `GEMINI_API_KEY` for Gemini). If missing, stop and ask the user.

Output folder: `output/similar/<YYYYMMDD-HHMMSS>/characters/` for raw images, `output/similar/<YYYYMMDD-HHMMSS>/images/` for composites. Create both before generating.

**Run all approved variants in parallel** — one Bash call per variant in a single message.

### Step 1: Generate images

Use `scripts/generate-image.py`. Run all approved variants in parallel — one Bash call per variant in a single message:

```bash
# OpenAI (default)
python3 scripts/generate-image.py \
    --prompt "<full image generation prompt — no text on image>" \
    --out "output/similar/<timestamp>/characters/<slug>.png"

# Gemini
python3 scripts/generate-image.py \
    --provider gemini \
    --prompt "<full image generation prompt — no text on image>" \
    --out "output/similar/<timestamp>/characters/<slug>.png"
```
### Step 2: Composite text overlays

Write an inline Pillow script for each variant — do not call `generate-ads.py` (it requires a full brief). The script should:

1. Open the saved character PNG
2. Draw the hook text and bullets at the layout zones extracted in Phase 1
3. Save to `output/similar/<timestamp>/images/<slug>-composite.png`

Use font paths from `scripts/compositor.py`'s `FONT_MAP` (Lato Semibold for hook/bullets, DejaVu Sans for checkmarks). Fall back to the default composite text settings in CLAUDE.md if the reference ad's zones are ambiguous.

---

## Phase 4: Deliver

Show each generated composite inline and give the file paths. One sentence per image describing how it diverges from the reference visually.

No tracker update, no data.json changes, no brief folders, no YAML files.

---

## Key Rules

- **Never let the image model render text.** All text via Pillow only.
- **Don't regenerate without asking.** If a composite looks wrong, ask the user before re-running.
- **Brand guidelines only.** No competitor teardowns, no template stats, no analysis files.
- **Images only.** No copy, no metadata, no tracker rows.
