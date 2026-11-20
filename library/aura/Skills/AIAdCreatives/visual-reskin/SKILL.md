---
name: visual-reskin
description: "Take a reference ad image, extract its layout formula and copy exactly, then generate a completely different visual using GPT image-2 CREATE API where the prompt encodes the exact layout zones, copy, and font specs so the model renders everything. No Pillow. Use when: reskin this ad, same copy different visual, new scene same layout, visual swap."
---

# Visual Reskin

Given a reference ad image, this skill produces a new ad that is visually unrecognisable — different scene, different character, different colour palette — while keeping every word of copy and every layout proportion identical.

GPT image-2 (CREATE, not EDIT) renders the complete image — background, visual, and all text — from a single richly-specified prompt. No Pillow step.

---

## What the User Must Provide

| Input | Required | Default |
|-------|----------|---------|
| Reference image (path or inline) | Yes | — |
| Number of reskins to generate | No | 1 |
| Visual direction hint (e.g. "nature scene", "abstract geometric") | No | infer from brand |
| Theme override (LIGHT / DARK) | No | match reference |

If the reference image is not provided, stop and ask.

---

## Reference Files to Load

Before starting, read these files — they inform visual ideation:

- [`references/brand/aurie.md`](../../../references/brand/aurie.md) — brand voice, visual avoids, ICPs
- [`references/research/conversation-topics.md`](../../../references/research/conversation-topics.md) — real emotional themes users bring to Aurie
- [`references/research/onboarding-quiz.md`](../../../references/research/onboarding-quiz.md) — who our users are and what they carry

---

## Phase 1: Extract the Layout Formula

Read the reference image. Extract everything needed to reconstruct the copy and text placement in the generation prompt.

### 1A — Visual extraction

**Canvas**
- Aspect ratio: 9:16 (1080×1920), 4:5 (1080×1350), or 1:1 (1080×1080)

**Hook text**
- Exact words and line breaks
- Font weight (bold / semibold / light)
- Approximate size relative to canvas (large / medium)
- Alignment (centered / left)
- Vertical position as % of canvas height

**Bullets**
- Exact text of each bullet line (with ✓ prefix)
- Font weight and relative size
- Vertical position as % of canvas height

> **Note:** Do NOT lock hook or bullet colours from the reference. Text colour is chosen in Phase 3 to suit the new scene — dark text on light backgrounds, light text on dark ones.

### 1B — Confirm copy

Output a layout summary block and wait for the user to confirm the copy is correct before proceeding.

```
EXTRACTED LAYOUT SUMMARY
─────────────────────────
Canvas:   1080×1920 (9:16)
Theme:    DARK

Hook (bold sans-serif, large, centered, ~60% height):
  "Holding the kid.
   Holding the parent.
   Nobody's holding you."

Bullets (medium sans-serif, smaller, ~83% height):
  ✓ Built by real therapists and clinicians
  ✓ Grounded in 20+ proven therapeutic modalities
  ✓ Designed to help you heal, not just cope

[Text colours TBD — assigned in Phase 3 based on new scene]
```

Do NOT proceed to image generation until copy is confirmed.

---

## Phase 2: Ideate New Visual(s)

Design N completely different scene concepts. Each must:

- **Diverge fully** from the reference — different subject, different setting, different dominant colour family. If reference has a plush bear, the reskin cannot.
- **Stay on-brand** — warm, emotionally resonant. LIGHT: airy/warm. DARK: intimate/deep. No neon, no clutter.
- **Resonate with our audience** — draw from `conversation-topics.md` themes (relationship insecurity, hypervigilance, overwhelm, boundary-setting) and `onboarding-quiz.md` (48% overthinkers carrying heavy loads, want to process not distract).
- **Serve the hook** — the visual should amplify the emotional state the hook names, without illustrating it literally.

For each concept:
```
Concept N: <one-line subject + scene>
Scene: <what fills the image zone>
Colour palette: <3–4 descriptors with hex>
Why it works: <how it amplifies the hook for our audience>
```

Present concepts to the user. Ask which to generate. If only 1 reskin is requested, pick the strongest and proceed without asking.

---

## Phase 3: Craft the Generation Prompt

For each approved concept, write a single richly-specified prompt that encodes:
1. Canvas format and proportions
2. The new visual scene
3. How text readability is achieved
4. All text — exact words, position, font style, color, alignment

### Step A — Choose a layout approach

Two approaches are available. Pick whichever serves the visual concept best — **do not default to zoned**:

| Approach | When to use | Readability technique |
|---|---|---|
| **Zoned** — image fills upper portion, solid/gradient panel below | Scene has a strong focal element that sits naturally in the top half; clean separation suits the mood | Gradient or solid colour panel in the lower 35–50% |
| **Full-bleed** — image fills the entire canvas edge-to-edge | Scene has atmosphere or texture that would be stronger at full height (sky, water, forest, dusk, mist) | One of the techniques below |

**Full-bleed readability techniques** — pick one per concept:

- **Scrim** — a subtle semi-transparent dark (or light) wash over just the text zone. Prompt: *"A soft semi-transparent dark scrim covers the lower 45% of the image, dimming the scene just enough for white text to read clearly without masking the image underneath."*
- **Natural contrast** — place text in an area of the scene that is inherently light or dark. Prompt: *"Text sits in the open sky area at the top / the dark shadowed ground at the bottom — the scene naturally provides contrast."* — only use when the scene guarantees it.
- **Frosted panel** — a blurred, semi-transparent frosted rectangle behind the text block. Prompt: *"A frosted-glass semi-transparent panel (white at 30–40% opacity, blurred) sits behind the hook and bullets."*
- **Text shadow only** — bold text with a strong drop shadow. Prompt: *"Text rendered with a strong soft drop shadow to lift it from the background — no panel or scrim."* — only reliable when text is large and the scene is not busy.

### Step B — Text colour

| Text zone background | Hook colour | Bullet colour |
|---|---|---|
| Dark (night, charcoal, deep teal, scrim-darkened) | Near-white e.g. `rgb(235, 240, 245)` | Soft light tone e.g. `rgb(190, 210, 210)` |
| Light (cream, sky, pale, frosted panel, scrim-lightened) | Deep charcoal e.g. `rgb(40, 35, 30)` | Medium-dark tone e.g. `rgb(80, 70, 60)` |

Always maximise contrast. Never use the same colour family as the background the text sits on.

### Step C — Write the prompt

**Zoned layout template:**

```
Vertical 9:16 advertisement image.

[VISUAL — upper {IMAGE_ZONE_PCT}% of canvas]
{Scene description — atmosphere, subject, colours, lighting, style.}

[GRADIENT / TEXT BACKGROUND]
A smooth gradient transition begins at {GRAD_START_PCT}% canvas height,
deepening to solid {TEXT_BG_COLOR} by {GRAD_END_PCT}% and holding to the bottom.

[HOOK TEXT — centered at approximately {HOOK_Y_PCT}% canvas height]
Font: bold, clean sans-serif, large
Color: {HOOK_COLOR}
Render these exact lines centered:
"{LINE_1}
{LINE_2}"

[BULLET TEXT — centered, starting at approximately {BULLET_Y_PCT}% canvas height]
Font: medium-weight sans-serif, smaller than hook
Color: {BULLET_COLOR}
Render these exact lines:
✓ {BULLET_1}
✓ {BULLET_2}
✓ {BULLET_3}

High contrast text against its background. No additional copy beyond what is specified.
```

**Full-bleed layout template:**

```
Vertical 9:16 advertisement image. The scene fills the entire canvas edge-to-edge.

[VISUAL — full canvas]
{Scene description — atmosphere, subject, colours, lighting, style. Rich and immersive.}

[TEXT READABILITY]
{Chosen readability technique — scrim / frosted panel / natural contrast / text shadow.
 Describe it precisely: position, opacity, colour, extent as % of canvas.}

[HOOK TEXT — centered at approximately {HOOK_Y_PCT}% canvas height]
Font: bold, clean sans-serif, large
Color: {HOOK_COLOR}
Render these exact lines centered:
"{LINE_1}
{LINE_2}"

[BULLET TEXT — centered, starting at approximately {BULLET_Y_PCT}% canvas height]
Font: medium-weight sans-serif, smaller than hook
Color: {BULLET_COLOR}
Render these exact lines:
✓ {BULLET_1}
✓ {BULLET_2}
✓ {BULLET_3}

All text must be fully legible against its background. No decorative elements overlapping
the text zones. No additional copy beyond what is specified above.
```

Fill every placeholder from Phase 1 (positions, copy) and Steps A–B above (approach, colours). The text must be quoted verbatim — never paraphrase.

---

## Phase 4: Generate

**Check `.env` first** — verify `OPENAI_API_KEY` is set. If missing, stop and ask.

Output folder: `output/visual-reskin/<YYYYMMDD>/<slug>/`. Create it before running.

**Run all approved variants in parallel** — one Bash call per variant:

```bash
python3 - <<'EOF'
import sys
sys.path.insert(0, "scripts")
from pathlib import Path
from openai_client import generate_image

out = Path("output/visual-reskin/<YYYYMMDD>/<slug>/<slug>.png")
out.parent.mkdir(parents=True, exist_ok=True)

prompt = """<full generation prompt from Phase 3>"""

ok = generate_image(
    prompt=prompt,
    aspect="9:16",
    out_path=out,
    label="<slug>",
    target_size=(1080, 1920),
)
print("Done" if ok else "Failed")
EOF
```

Use `aspect="4:5"` or `aspect="1:1"` if the reference was not 9:16.

After generation, **view the output image** with the Read tool to check text legibility and placement. If text is missing or garbled on a bullet line, note it for the user — do not silently deliver a broken image.

---

## Phase 5: Deliver

Show each image inline and give the file path. One sentence per image: what visual concept replaced the reference and how the scene connects to the hook's emotional state.

### Update the index

After delivering, prepend each new image to `output/visual-reskin/index.md`, keeping only the last 10 rows. Format matches the tweaks index:

```
| Ref `<ad-id>` — <style category>: <one-line description of the visual> | <YYYY-MM-DD> | ![preview](<YYYYMMDD>/<slug>/<slug>.png) |
```

Style category should be one of: `Object`, `Macro`, `Landscape`, `Human figure`, `Artistic figure`.

No tracker update. No data.json changes. No brief YAML unless the user asks.

---

## Key Rules

1. **Copy is sacred** — every word reproduced character-for-character from the reference. Never paraphrase, truncate, or reorder.
2. **CREATE not EDIT** — call `generate_image()` with no `reference_image` argument. This calls `client.images.generate`, not `images.edit`. The new visual must not inherit reference pixels.
3. **Full prompt carries the layout** — text position, color, weight, alignment, and readability technique are all in the prompt. Specificity is the reliability lever.
4. **Do not default to zoned layout** — choose zoned or full-bleed based on which serves the visual concept. Full-bleed is often stronger for atmospheric, abstract, or dreamy scenes.
5. **Text must be legible** — whatever approach is chosen, all copy (hook and all bullets) must be clearly readable. Pick the readability technique that best suits the scene; specify it precisely in the prompt.
6. **Stay on-brand** — apply all avoids from `references/brand/aurie.md`. No neon, no clutter, no diagnostic framing, no "companion" imagery, no attachment-optimised visuals.
7. **Visual must genuinely differ** — different subject type, different dominant hue. Confirm divergence before generating.
8. **View before delivering** — always read the output with the Read tool. Flag any text rendering issues rather than silently delivering them.
