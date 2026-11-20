---
name: generate-new-ad
description: "Research-led ideation and generation of a fresh 9:16 ad creative for Aurie using GPT image-2 for the entire visual (background, illustration, and all text in one pass — no Pillow). Reads brand guidelines, messaging spine, user research, and Meta performance data to pick a hook and a visual concept that is distinct from recent output. Use when: generate a new ad, create a fresh ad creative, make a new ad from scratch, new 9:16 ad."
---

# Generate New Ad

Research the brand, current Meta performance, and recent creative history, then ideate and generate a fresh 9:16 Aurie ad in one GPT image-2 pass — visual and text baked together.

---

## What the User Can Provide

| Input | Required | Default |
|-------|----------|---------|
| Hook direction or angle hint | No | Derive from research |
| Visual style hint | No | Derive from research |
| Number of ads to generate | No | 1 |

If nothing is provided, run the full research phase and pick independently.

---

## Phase 1: Research

Load all of the following in parallel:

- [`references/brand/aurie.md`](../../../references/brand/aurie.md) — brand voice, product identity, visual avoids, ICPs
- [`references/messaging/aurie/messaging.md`](../../../references/messaging/aurie/messaging.md) — messaging spine, hook categories, value props, avoid list
- [`references/research/conversation-topics.md`](../../../references/research/conversation-topics.md) — real emotional themes users bring to Aurie
- [`references/research/onboarding-quiz.md`](../../../references/research/onboarding-quiz.md) — user coping patterns, load levels, entry motivations
- [`output/analysis/meta-ads/top/index.md`](../../../output/analysis/meta-ads/top/index.md) — current top-5 Meta creatives with hooks, visuals, and spend signals

Then load both recency indexes to understand what visuals already exist and must be avoided:

- [`output/new-ads/index.md`](../../../output/new-ads/index.md)
- [`output/visual-reskin/index.md`](../../../output/visual-reskin/index.md)

---

## Phase 2: Ideate

Synthesise the research into a creative brief for one ad. The brief must address all five dimensions:

### 2A — Hook

Pick a hook using the messaging concept categories from `messaging.md` (Recognition Moment, Outcome, JTBD, Use Case, Selfish Desire, Audience Profile). Evaluate against Meta performance:

- Which hooks/formulas are currently winning? Which angles are untested or undersaturated?
- Which user pain is most statistically significant (onboarding data)?
- Which conversation themes are emotionally live but not yet used in recent ads?

Write the final hook text — the exact words that will appear on the image. Keep it short (≤ 8 words per line, ≤ 2 lines).

### 2B — Supporting copy

Pick one supporting line (4–6 words) and the brand wordmark placement. Typical structure:

```
[HOOK — large, top half]
[SUPPORTING LINE — small, lower half]
[Aurie — wordmark, bottom]
```

Optionally add credential bullets (✓ Built by real therapists and clinicians / ✓ Grounded in 20+ proven therapeutic modalities / ✓ Designed to help you heal, not just cope) only if the ad angle benefits from credentialing — don't default to them.

### 2C — Visual concept

Design a scene that:

- **Conveys the hook** — does not need to be literal, but should amplify the emotional state it names
- **Is fresh** — subject type, style, and dominant colour family must differ from every entry in both recency indexes loaded in Phase 1
- **Stays on-brand** — apply all avoids from `references/brand/aurie.md`: no neon, no clutter, no diagnostic framing, no "companion" imagery, no low-trust AI aesthetics
- **Pairs well with GPT image rendering** — abstract, conceptual, or atmospheric scenes work better than photorealistic faces for text legibility

Pick a colour theme: **DARK** (deep navy, charcoal, midnight) or **LIGHT** (airy cream, soft warm white, pale sky). DARK is generally more reliable for white text legibility. Choose based on which serves the concept.

### 2D — Text readability plan

GPT image-2 renders text most reliably when:
- Text is short and prominent
- High contrast is explicit in the prompt (white text on dark bg, or dark text on light bg)
- Text placement zones are unambiguous
- The visual in those zones is simple (sky, gradient, abstract, dark fill) — not busy

Plan which part of the canvas will hold each text element and how contrast is guaranteed.

### 2E — Brief summary

Before generating, output a one-paragraph brief:

```
BRIEF
─────
Hook: "<hook text>"
Supporting line: "<supporting line>"
Visual: <one-sentence description of scene>
Colour theme: DARK / LIGHT
Text plan: <where hook sits, contrast source>
Why this is fresh: <how it differs from recent output>
Why now: <what research signal backs this hook>
```

If the user provided no direction, show this brief and proceed. If the user asked for multiple ads, show all N briefs and proceed unless they flag a concern.

---

## Phase 3: Craft the Generation Prompt

Write one richly-specified prompt per ad. The prompt is the only layout spec — GPT image-2 must receive everything it needs to position and render text correctly.

### Prompt structure

```
A premium 9:16 vertical mobile ad creative for Aurie, an emotional wellness app.

[BACKGROUND / SCENE]
<Full scene description — dominant colours with hex codes, atmosphere, style, lighting.
Be specific. For abstract/conceptual visuals, describe exact shapes, light behaviour, and
colour family. For photographic-style, describe the setting, time of day, and mood.
No faces unless explicitly required. Abstract and atmospheric scenes are preferred.>

[TEXT LAYOUT]
All text must be rendered sharply, with high contrast against its background.

Top area (<Y1>% from top), centered:
<HOOK LINE 1> in bold heavy white sans-serif, large (<~96–120px equivalent>)
<HOOK LINE 2 if applicable> directly below, same style

<Lower area (<Y2>% from top) if supporting line present>, centered:
<Supporting line> in clean light white sans-serif, smaller (~48px equivalent)

Bottom strip (bottom 7%), centered:
Italic wordmark "Aurie" in white (capital A — always)

<If credential bullets are included:>
Bullet area (<Y3>% from top), centered:
✓ <BULLET 1> in light-weight white sans-serif, small
✓ <BULLET 2>
✓ <BULLET 3>

[STYLE]
<Premium wellness brand. Minimal, editorial. DARK: deep-dominant palette, white text.
LIGHT: airy-dominant palette, dark text. No borders, no gradients on text. Think
Apple-quality visual clarity meets premium mental-health brand.>
```

Fill every placeholder. Quote hook text verbatim. Specify hex codes for background colours.

---

## Phase 4: Generate

**Check `.env` first** — verify `OPENAI_API_KEY` is set. If missing, stop and ask.

Create the output folder and run generation. For N ads, run all in parallel — one Bash call per ad in a single message:

```python
python3 - <<'EOF'
import sys
sys.path.insert(0, "scripts")
from pathlib import Path
from openai_client import generate_image

out = Path("output/new-ads/<YYYYMMDD>/<slug>/<slug>.png")
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

Slug format: `<hook-in-kebab-case>` (e.g. `chatgpt-for-overthinkers`).

After generation, **view the output image** with the Read tool. Check:
- All text elements are present and readable
- No garbled or missing words
- Visual concept matches intent
- Contrast is sufficient

If text is missing or illegible, note it for the user — do not silently deliver a broken image. Optionally retry with a tighter prompt if a single element is broken.

---

## Phase 5: Deliver and Update Index

Show each image inline and give the file path.

Write one sentence covering: what hook it uses, which research signal backs it, and how the visual conveys the emotional state.

### Update `output/new-ads/index.md`

Prepend each new ad to the table, keeping the index up-to-date:

```markdown
| <Hook text> | <One-line visual description. Note DARK/LIGHT theme and key research backing.> | <YYYY-MM-DD> | ![preview](<YYYYMMDD>/<slug>/<slug>.png) |
```

The index table columns are: **Hook | Description | Ran on | Ad**

No tracker update. No data.json changes. No brief YAML unless the user asks.

---

## Key Rules

1. **Full-visual + text in one pass** — this skill uses GPT image-2 CREATE (no Pillow). The prompt must encode every text element, position, weight, colour, and alignment. Do not add a Pillow compositing step.
2. **No reference image required** — hook and visual are derived from research, not copied from a reference. Do not call `images.edit()`; always call `images.generate()`.
3. **Research-led hooks** — the hook must be backed by at least one signal: a Meta performance pattern, an onboarding data point, or a live conversation theme. State the backing signal in the brief.
4. **Visual freshness is mandatory** — before generating, verify the concept is distinct from every entry in `output/new-ads/index.md` and `output/visual-reskin/index.md`. Different subject type, different style, different dominant colour family.
5. **Stay on-brand** — all avoids from `references/brand/aurie.md` apply: no neon, no clutter, no "companion" framing, no diagnostic language, no attachment-optimised imagery.
6. **View before delivering** — always read the output. Flag text rendering issues rather than silently delivering them.
7. **Keep text minimal** — fewer, shorter text elements improve GPT rendering accuracy. Hook ≤ 8 words per line. Supporting line ≤ 6 words. Wordmark only (not URL or CTA) at bottom.
