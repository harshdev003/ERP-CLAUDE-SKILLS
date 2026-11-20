---
name: scale-new-ads
description: "End-to-end skill for creating N new ad creatives without a specific reference ad. Surveys existing knowledge (competitor teardowns, template upvotes, past briefs, winning ad analyses), picks fresh angles + diverse templates, generates brief YAMLs, creates composites via Gemini + Pillow, runs creative-review and creative-scorer, and rebuilds the tracker. Use when: create N new ads, generate new creatives, make more ads, scale creative production, produce a batch of ads, come up with new ad ideas. Does NOT push to GitHub."
---

# Scale New Ads — Batch Creative Generation

Produce a batch of N new ad creatives when the user has no specific reference ad in mind. The skill is the counterpart to `scale-winning-ad`: instead of copying one reference, it **surveys what we already know** (competitor teardowns, template upvotes, past briefs) and generates a diverse batch that fills gaps in the creative portfolio.

---

## Phase 0: Gather Inputs (ask if missing)

Before doing anything, collect these inputs. If the user's message doesn't already specify them, **ask the user in a single message**. Do not proceed until each is answered.

| Input | Required | Default | What to ask |
|-------|----------|---------|-------------|
| **Source of inspiration** | Yes | `existing-knowledge` | "Should I base these on: (a) **existing knowledge** — competitor teardowns, past winners, template upvotes in the repo, or (b) **specific context** you provide — describe the angle/audience/vibe." |
| **App** | Yes | mix | "Which app? **Aurie**, **Aura**, or a **mix**?" |
| **Number of ads** | Yes | 3 | "How many ads? (Default: **3**)" |
| **Angle / vibe / theme** | Optional | — | Only ask if the user chose "specific context" above. Offer them concrete dimensions they can provide (any combination is fine):<br/>• **Emotional vertical** — what feeling/pain point to target. Examples: *anxiety, grief, burnout, people-pleasing, parenting reactivity, sleep onset, overthinking, dissociation, breakups, dopamine/motivation, procrastination, loneliness.*<br/>• **Audience** — who the ad should stop mid-scroll. Examples: *new moms, therapy-curious professionals, late-night scrollers, breakup recovery, high-functioning overachievers, men who don't talk about feelings.*<br/>• **Situation / use case** — the moment the reader is in when they see the ad. Examples: *3am spiral, Sunday scaries, after a fight with a partner, first week back at work, the drive home.*<br/>• **Tone / visual** — aesthetic direction. Examples: *DARK moody, LIGHT airy, illustrated character, pure typography, native phone mock, UGC testimonial feel.*<br/>• **Competitor reference** — a specific ad or brand to take inspiration from. Examples: *like Calm's EMDR bilateral audio ad, like BetterHelp's testimonial format, like Headspace's sleepcast style.*<br/>• **Hook style** — the opening move. Examples: *reframe, pattern-interrupt, knowledge-gap question, time-anchored ("My anxiety had a favorite time. 3am."), audience profile ("For the person who looks fine but isn't").*<br/>If the user says "surprise me" or similar, treat as `existing-knowledge` and skip this field. |

Once every required input is answered, proceed directly to Phase 1 — do not re-confirm.

---

## Phase 1: Survey What We Already Know

Run these reads in parallel to build a mental map. **Do not skip this phase** — fresh angles require knowing what's already done.

### 1A: Load knowledge files

- `CLAUDE.md` — copy rules, template registry, composite defaults
- `references/generation/angle-divergence.md` — core principle and 5 suggested axes that variants can diverge on across the batch
- `references/messaging/aurie/verticals-and-copy.md` — 11 emotional verticals + reusable copy bank
- `references/brand/aurie.md` and `references/brand/aura.md` — product details, ICPs, social proof

### 1B: Scan existing work

- `output/analysis/winners/` — list all winning-ad teardowns; skim for winning hooks, formats, and validated mechanisms (nervous system framing, timeline progression, text-gradient hero, etc.)
- `output/analysis/competitors/` — list all competitor analyses; skim for competitor intel, angles they're running, and hooks to differentiate against
- `output/briefs/` — list folder names to understand which angles are already covered (e.g., briefs 001–047 cover anxiety, trauma, burnout, relationships, nervous system, procrastination, breakups, dopamine, parenting, sleep, meditation, overthinking, grief, stress, personalization)
- `output/data.json` — read the last 5 BRIEFS rows to find the last brief number (next brief starts at `last+1`)

### 1C: Audit template saturation

Run `python3 scripts/generate-ads.py --template-stats` to see template upvote counts. Then scan the **last 10 brief folder names** (not just stats) in `output/briefs/` and tally which template each brief used. Build a saturation map:

```
subject_hook_bullets  → used in briefs #41 #42 #44 #46 #48 #50 #51 #52 #53  (9 recent uses — SATURATED)
custom text-hero → used in briefs #45 #49  (2 recent uses — available)
```

**Saturation rule:** a template is **saturated** once it appears in 3 or more of the last 10 briefs. A saturated template should not be chosen for this batch unless:
- the user explicitly requested it, OR
- it is the only template that fits the brief's concept and no new format is viable.

When all registered templates are saturated (or only one remains unsaturated), **the batch must include at least one new format from the Format Repertoire below.** Propose it as a `custom` layout with a concrete `layout:` spec — do not fall back to repeating an existing template.

### 1D: Format Repertoire

These are formats proven in mental-health / emotional-app advertising that are **not** the plush character or text-gradient hero. When the registered templates are saturated, pick from this list — or combine two to invent something genuinely new. Each entry includes a brief sketch of the layout spec so you can write the `custom` YAML without inventing dimensions from scratch.

| Format | When it works | Rough layout sketch |
|--------|--------------|---------------------|
| **Phone / iMessage mockup** | Conversational, relatable moments — inner critic, partner argument, late-night spiral | Dark phone chrome top 15%; chat bubbles 15–75%; gradient fade + hook text 75–90%; CTA strip 90–100% |
| **Notes-app / voice-memo screenshot** | Raw, authentic — unsent letter, late-night thought, 3am voice memo | Off-white or light-yellow canvas; monospace or system font text block 10–70%; subtle crumple or app UI chrome; minimal brand treatment |
| **Testimonial card** | Social proof, high-trust — therapy-curious skeptics, "is this real?" audience | Head/shoulders photo or illustrated avatar 0–40%; pull-quote in large type 40–72%; name + credential small text 72–78%; brand logo + CTA 85–100% |
| **Timeline / before-after split** | Progression narrative — recovery, habit change, sleep improvement | Two-panel horizontal split OR top-to-bottom timeline with 3–4 labeled moments; each panel gets its own mini-text |
| **Typography poster** | Bold hooks for pattern-interrupt or reframe — no character, pure words | Near-black canvas; oversized headline centered 20–65% (100–140px); contrasting accent line 68–75%; small bullets or sub-copy 78–88%; minimal logo 92–96% |
| **Journaling / paper texture** | Introspective, slow-scroll — grief, delayed emotion, inner child | Cream or aged-paper texture fill; handwriting-style font for headline; ink-style ruled lines; emotional prompt as primary text |
| **Stats / data card** | Credibility play — skeptics, professionals, evidence-seekers | Clean white or dark card; large number as hero (e.g. "73%") at 20–45%; stat explanation text 48–65%; source credit small 67–70%; feature bullets 73–88% |
| **Split-screen contrast** | Before/after emotional state, two audiences, two outcomes | Canvas divided 50/50 left-right or top-bottom; left = "before" dark treatment, right = "after" warm treatment; shared CTA strip at bottom |
| **UGC / screenshot style** | Organic, native — looks like a real post, not an ad | Phone bezel or Instagram/TikTok app chrome; user avatar + handle + caption in platform-native font; "comment" or "reply" UI element; no polished brand overlay |
| **Ambient scene / no character** | Mood-first concepts — sleep, late-night, dissociation, 3am | Photorealistic or painterly scene (bedroom, window, night sky); no character; minimal text overlay centered or bottom-anchored |

When proposing a new format from this list, write a complete `layout:` block in the brief YAML (not just a concept name). The `custom` template will render it — no new Python code needed unless the zone type itself is new.

---

## Phase 2: Plan the Batch

Before writing any YAML, plan all N ads in a single design pass. Output a short plan to the user so they can course-correct before generation.

### Diversity rules (apply to every batch)

- **Template saturation check (mandatory):** Before picking any template, re-read the saturation map built in Phase 1C. A saturated template (3+ of last 10 briefs) must not be used unless explicitly requested. When both registered templates are saturated, at least one ad in the batch **must** use a new format from the 1D repertoire. This is a hard rule — not a suggestion.

- **Format diversity over template loyalty:** The goal is that each ad in the batch looks visually distinct at a glance. If you can't tell two ads apart by their layout/format alone, you've failed this rule. Ask: "Could a user scrolling through the tracker tell these apart without reading the copy?" If not, change one.

- **Prioritize new formats when the library is saturated:** If `subject_hook_bullets` and `custom text-hero` already account for the last 5–6 briefs, the default should be to propose new formats — phone mockup, testimonial card, typography poster, etc. — not to keep adding to the same visual family. The angle should suggest the format: a conversational angle → iMessage mockup; a raw honest moment → Notes app; a bold reframe → typography poster.

- **Angle freshness**: Avoid any angle already covered by a brief in `output/briefs/`. If the user asked for a specific angle, still vary the hook/mechanism across the N ads.

- **App split**: If the user specified a single app (Aurie OR Aura), produce all N ads for that app. Only split between apps if the user said "mix" or didn't specify.

- **Hook type diversity**: No two ads in the batch should use the same `hook_type` (pattern-interrupt, reframe, use-case, knowledge-gap, audience-profile, etc.).

- **Bullet copy diversity**: The standard Aurie bullet set ("✓ Built by real therapists...") is locked brand copy. But the **hook** and **primary text** must still be meaningfully different — different opening emotion, different narrative arc, different payoff. If two ads in the batch have the same hook structure, cut one and replace it with a different format or angle.

- **Risk spread**: All ads should target Low risk. If the copy warrants Low-Medium, flag it explicitly in the QA notes.

### Brief planning table (share with user)

Always show this table before generating. It forces you to justify the format choice and catch "same family" problems before writing any YAML.

```
Ad 1 — Brief #{N+1} — {App}: {Angle}
  Format: {template or "new: phone-mockup / testimonial / typography-poster / etc."}
  Why this format: {one sentence — how the format serves the angle}
  Hook: "{hook}"
  Hook type: {pattern-interrupt / reframe / use-case / knowledge-gap / audience-profile}
  Why fresh: {what no competitor or past brief does}

Ad 2 — Brief #{N+2} — {App}: {Angle}
  Format: ...
  Why this format: ...
  Hook: ...
  Hook type: ...
  Why fresh: ...

...

Visual-distinctness check: "Can you tell these apart by format alone, without reading the copy?" → YES / NO
(If NO, replace the duplicate with a different format before proceeding.)
```

Get silent buy-in (user can interrupt). If no pushback within a few seconds, continue.

---

## Phase 3: Create Brief Folders + YAMLs

For each ad, in parallel:

1. Create `output/briefs/{NUM:03d}-{APP}-{slug}/{characters,images}/` folders.
2. **Structural-element classification** (mandatory, before writing the YAML). For each brief, classify what the AI is being asked to draw:
   - **Atmospheric only** (texture, ambient scene, painterly background, character) → AI is the right tool. Proceed normally.
   - **Includes structural elements** (cards, frames, mockup chrome, dividers, geometric shapes, "near-empty" canvases) → prefer drawing the structural elements with Pillow `fill`/`gradient` zones rather than the image model. Where this fits, update the `image_direction` to ask the AI for *only* the atmospheric layer (e.g. "flat warm cream background, absolutely empty") and build the structure as Pillow zones with exact coordinates. See [`references/rendering/composite-rendering-rules.md`](../../../references/rendering/composite-rendering-rules.md) — "Structural elements: draw with Pillow, not the AI image model".
3. Write `brief-{NUM:03d}.yaml` following [`references/formats/image/image-gen-spec.md`](../../../references/formats/image/image-gen-spec.md).

**MANDATORY:** Read every file under [`references/rendering/`](../../../references/rendering/) before writing any brief YAML or layout spec. These rules cover bullet truncation in `subject_hook_bullets`, checkmark glyph coverage in custom layouts, hook wrapping, raw-file requirements, opaque `fill` zones, canvas sizing, **and the structural-elements rule that determines whether you can trust the AI for a given brief**. The folder is the single source of truth — new rules are added there, not inline.

---

## Phase 4: Update data.json

Append one row per ad to each of the three arrays (`BRIEFS`, `CREATIVES`, `QA`). All three rows per ad must share the same Brief #.

- **BRIEFS row** (17 fields): `[date, #, app, angle, hook_type, hook, format, ad_type, tone, strategy, competitor_intel, rationale, risk, priority, status, source, folder]`
- **CREATIVES row** (12 fields): `[date, #, app, angle, primary_text, headline, description, cta, image_direction, image_path, image_hook, image_proof]`
- **QA row** (9 fields): `[date, #, app, hook_check, risk, audience, freshness, score, notes]` — placeholder values; creative-scorer refines in Phase 5.

Write literal unicode characters (`✓`, `—`, `💜`, `💙`), never `\uXXXX` escapes. See `CLAUDE.md`.

---

## Phase 5: Generate + Review + Score

### 5A: Generate images

```bash
python3 scripts/generate-ads.py {N+1} {N+2} {N+3}
```

This dispatches to whichever template each brief declares. Whatever templates exist in `scripts/templates/` are valid targets — no per-template handling needed here.

### 5B: Run creative-review

Invoke the `creative-review` skill with `briefs {N+1}, {N+2}, {N+3}`. It:

- Views each composite
- Flags truncated bullets, wrong hook text, checkmark rendering issues, too-cropped characters, bar artifacts, etc.
- Auto-fixes by adjusting `visual.layout` zone properties and re-running `generate-ads.py --composite-only --force`
- Max 3 iterations per ad — flag to user if unresolved

### 5C: Run creative-scorer

Invoke the `creative-scorer` skill with `briefs {N+1}, {N+2}, {N+3}`. It populates the QA rows in `data.json` with final hook_check, risk, audience, freshness, score, and notes.

### 5D: Rebuild tracker

```bash
python3 scripts/build-tracker.py
```

Confirm `BRIEFS` count equals `QA` count and `data.json` validates.

Then run the `preview` skill to start the tracker server (if not already running) and give the user a clickable link.

---

## Phase 6: Report

Present a compact summary to the user:

```
Created {N} new ads under parent workflow:

• Brief #{N+1} — {App}: {Angle} — Score {S}/10 — [image_path]
• Brief #{N+2} — {App}: {Angle} — Score {S}/10 — [image_path]
• Brief #{N+3} — {App}: {Angle} — Score {S}/10 — [image_path]

Templates used: {template-list}
Risk flags: {any Medium/High notes}
Next step: push to GitHub when ready.
```

Include markdown links to the composite images so the user can click to view.

---

## When to Use vs. Related Skills

- **Use this skill** when: "make 3 new ads," "generate new creatives," "come up with new ad ideas," "scale creative production"
- **Use `scale-winning-ad`** when: the user provides a specific reference ad (image, Ad Library ID, URL) to replicate
- **Use `winning-creative-analysis`** when: the user only wants a teardown of a reference ad, not a new creative
- **Use `competitor-analysis`** when: the user wants to scrape a competitor's top ads and write a strategic analysis

---

## Quality Standards

- **No two ads in the batch should feel interchangeable.** Template, angle, hook type, and copy arc should each vary.
- **Every new angle must not already exist in `output/briefs/`.** Scan before picking.
- **Every custom layout must be sized to 1080×1920** and use DejaVu Sans for any zone containing `✓`.
- **All ads score ≥ 7/10** before being marked ready. If scorer lands one at 6 or below, flag for iteration before reporting.
- **Do not push to GitHub** unless the user explicitly asks. Leave the work in the local repo for them to review.
