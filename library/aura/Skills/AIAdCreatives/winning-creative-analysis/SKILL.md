---
name: winning-creative-analysis
description: "Analyze competitor or winning ads from Meta Ad Library and produce pixel-level teardowns. Use this skill whenever the user wants to: analyze a competitor ad, tear down a winning creative, study an ad from Meta Ad Library, reverse-engineer an ad's visual or copy formula, or says things like 'analyze this ad', 'why is this ad working', 'tear down this creative', or shares an Ad Library ID or URL. Also use when the user shares a screenshot of an ad and wants to understand what makes it effective. MANDATORY TRIGGERS: ad analysis, ad teardown, ad library, winning ad, competitor ad, analyze this ad, why is this ad working."
---

# Winning Creative Analysis Skill

Analyze competitor or high-performing ads from Meta Ad Library and produce a detailed teardown that's actionable enough to reproduce the creative's formula.

---

## When to Use

Use this skill when:
- Analyzing a competitor ad from Meta Ad Library
- Tearing down a winning ad to extract replicable patterns
- The user shares an Ad Library ID, URL, or screenshot of an ad

---

## Knowledge Files to Load

Load these before starting — they provide the competitive landscape and past findings:

- `references/competitors.md` — 19 known competitors grouped by category with Ad Library links
- `references/research/ad-research-findings.md` — Benchmarks, industry trends, competitive intel
- `output/analysis/winners/` — Scan past teardowns so you don't repeat observations already captured
- **`scripts/templates/`** — The ad layout template registry. Read `scripts/templates/usage.json` for current match counts, and read each template module's top-of-file docstring to understand its layout. Run `python3 scripts/generate-ads.py --list-templates` and `--template-stats` for a quick summary. The teardown must decide whether this ad matches an existing template (an upvote) or whether it represents a new layout pattern worth proposing.

---

## Step 1: Gather the Ad

Before analyzing, collect as much raw material as possible.

### What to Ask the User

1. **Ad Library ID or URL** — e.g. `803825368978289` or the full facebook.com/ads/library link
2. **Screenshot(s) of the ad** — both the image creative and the full primary text (Ad Library often truncates). Ask for:
   - The ad image (full resolution if possible)
   - The expanded primary text (click "See more")
   - The headline, description, and CTA button visible below the image
3. **Any context they already have** — brand name, landing page URL, how long it's been running, how they found it

### What to Look for in Meta Ad Library

> **Note:** The Meta Ad Library API (`/ads_archive`) only covers political/social issue ads and UK/EU commercial ads — it does not expose US commercial ads programmatically. All US competitor ad data must be gathered manually from the Ad Library UI (facebook.com/ads/library).

Pull these signals directly from the Ad Library listing:
- **Status**: Active or Inactive
- **Start date**: When did it first appear?
- **Platforms**: Facebook, Instagram, Messenger, Threads — which ones?
- **Versions**: Does it say "This ad has multiple versions"?
- **CTA button text**: Learn More, Download, Try Free, etc.
- **Landing page URL**: Check for UTM parameters — they reveal campaign maturity
- **Page name / advertiser**: Who's running it?

---

## Step 2: Analyze — The Questions to Answer

Work through each of these areas systematically. Every section must appear in the final output.

### 2A: Why We Believe This Ad Is a Winner

This is evidence-based, not opinion. Look for these specific signals:

- **Longevity**: How many days has it been running? Ads that survive 7+ days are likely profitable. 14+ days = strong signal. Most teams kill losers within 3-5 days.
- **Platform breadth**: Running on 1 platform vs. all 4 Meta platforms? Broad deployment = confidence or validated expansion.
- **Multiple versions**: This means they're A/B testing variants — you only do that after the base creative proves it converts.
- **Creative sophistication**: Is this a first draft or a polished piece? Look at narrative structure, character design, typography hierarchy, copy-image interplay. Polish implies investment, which implies returns.
- **UTM/tracking maturity**: Full UTM parameters with dynamic macros (`{{placement}}`, `{{adset.name}}`) = well-instrumented performance team that kills losers fast.

**Always include a "What we can't confirm" disclaimer.** We're reading circumstantial evidence — we can't see spend, CPA, ROAS, or conversion volume.

### 2B: Ad Components

Transcribe every component of the ad exactly:

- **Primary Text**: Full copy, word for word. Note approximate word count. If truncated in Ad Library, ask the user to expand it.
- **Headline**: The line below the image
- **Description**: The line below the headline (may not be visible)
- **CTA Button**: Exact button text
- **Landing page**: URL + any UTM parameters visible

### 2C: Copy Strategy Deep-Dive

Break down the copy like a copywriter would:

- **Hook analysis**: What are the first 1-2 lines doing? How many words? What techniques (personification, specificity, pattern interrupt, curiosity gap)?
- **Narrative arc table**: Map the copy to acts (Setup → Insight → Alternatives Fail → Discovery → Transformation → Close). Use a table with Act, Content, and Purpose columns.
- **Key copywriting techniques**: Number each one. Name the technique, quote the specific line(s) that demonstrate it, explain why it works. Look for:
  - Reframes / dichotomies (e.g., "managing vs. processing")
  - Specificity over abstraction (concrete details vs. vague claims)
  - Rhythm and pacing (fragment vs. long sentence alternation)
  - Social proof nesting (multiple layers of validation)
  - Close style (hard sell vs. empathic close)

### 2D: Image — Pixel-Level Visual Specification

**This is the most important section for reproduction.** It must be detailed enough that someone could recreate the visual formula without seeing the original.

Work through each of these sub-sections:

#### Canvas & Format
| Property | Value | Notes |
|----------|-------|-------|
| Aspect ratio | | e.g. 9:16, 4:5, 1:1 |
| Full resolution (est.) | | Scale to standard Meta sizes |
| Reference file | | Source resolution from Ad Library |

#### Color Palette
Sample exact hex values from the image for every distinct color element:
- Background (may have multiple zones)
- Character/subject body (main, highlight, shadow)
- Platform/surface elements
- Accent elements
- Text colors (headline, body, supporting)

Use a table: Element | Hex | RGB | Description

#### Layout Zones
Map the vertical layout as % of canvas height. Use an ASCII diagram showing where each zone starts and ends, with pixel estimates at 1080w.

#### Visual Image Zone (`visual.image_zone` recommendation)

Read directly off the layout zone diagram above: `image_zone` is the percentage where the **main subject ends**. If the character zone runs 0–55%, the value is `"55%"`. If the scene or subject fills the full canvas height, the value is `"100%"` — even if text is composited over a gradient at the bottom.

Output a single line: **`image_zone: "<value>"`** — and one sentence identifying which zone boundary you used.

#### Character / Subject Specification
If the ad uses an illustrated character or specific subject:
- Species / style / aesthetic
- Size vs canvas (% width and height)
- Pose and emotional state
- Color values (main, highlight, shadow)
- Eye style, facial detail level
- Outline/rendering approach

#### Supporting Visual Elements
- Platforms, shelves, surfaces (shape, colors, gradients)
- Background details (base color, glows, accents, strips)
- Any decorative elements and their exact positioning

#### Typography Specification
For each text element:
| Element | Font (est.) | Size (at 1080w) | Weight | Color | Alignment |
|---------|-------------|-----------------|--------|-------|-----------|

Include notes on line-height, hierarchy ratios, text shadows/effects.

#### Spacing & Padding
| Gap | Pixels (est.) | % of Height |
|-----|---------------|-------------|

Note any intentional empty space (e.g., crop safety zones for 4:5 feed).

### 2E: Why This Image Works

For each major visual choice, explain the strategic reasoning. Number each point. Cover:
- What the character/subject is doing emotionally and why that resonates
- The headline/text positioning strategy (scroll-speed vs. deep-read)
- Background color choice and feed context (dark mode, daytime, etc.)
- Trust/credibility elements and how they handle objections
- Depth and dimension techniques
- Color palette vs. category conventions
- Size and proportion choices for mobile thumb-stopping

### 2F: Strategic Analysis

- **Target audience**: Be specific — demographics, psychographics, where they are in their journey
- **Funnel position**: Cold/warm/hot traffic? Top/mid/bottom of funnel?
- **Competitive positioning table**: How does the ad dismiss or position against alternatives? Use a table: Alternative | How It's Dismissed. Note which competitors are named vs. implied, and which are validated rather than dismissed.
- **Why it's likely winning**: Synthesize all signals into a clear hypothesis

### 2F.5: Template Match (upvote or create)

**Delegate to the `match-template` skill** — it owns the full methodology (registry lookup, confidence levels, new-template creation). Invoke it with:

- `source: "winning-creative-analysis"`
- `brand: "{Brand}"`
- `ad_id: "{library_id}"`
- `direct_add: true` ← always pass this; new templates go directly into `templates`, never into `proposed`
- Visual reference (the ad image or detailed layout description)

The skill returns exactly one of:

- `MATCH: <template> (<confidence>) — <rationale>` — upvote recorded in `templates.<name>.matches`
- `NEW: <name> — <rationale>` — new template added directly to `templates` (not `proposed`)
- `NO_MATCH: <reason>` — one-off layout, nothing persisted

Embed the returned line in the teardown output under section 3 (Image) as the `**Template match:**` line.

### 2G: Key Learnings for Our Creative

Numbered, actionable takeaways. Each one must:
- Name a specific technique (not vague advice)
- Reference the exact element from this ad that demonstrates it
- Be directly applicable to our own creative work

Include the **character design formula** if the ad uses an illustrated character:
- Familiar comfort object + unexpected color + emotional honesty
- What does the character remind people of? (plush toy, stuffed animal, etc.)
- How does the color break category conventions?
- How does the character's emotional state mirror the audience?

---

## Step 3: Save Assets & Write the Output

### Folder Structure

Each analysis lives in its own folder:

```
output/analysis/winners/ad-analysis-<ad-library-id>/
  analysis.md          ← the teardown
  ad-image.png         ← the primary ad creative shared by the user
  ad-image-2.png       ← second image, if multiple creatives were shared
  ad-image-3.png       ← etc.
```

**Reference the image in the analysis** at the top of the file (after the metadata block):

```markdown
**Ad image:** ![Ad creative](./ad-image.png)
```

One analysis folder per ad. Never create separate "learnings" files.

### Output Format

```markdown
# Winning Ad Teardown — [Brand] [Topic/Angle] Ad

**Ad Library ID:** [ID]
**Status:** [Active/Inactive] since [Date]
**Platforms:** [List]
**Versions:** [Single/Multiple]
**CTA:** [Button text]
**Landing:** [URL]
**Analysis date:** [Today's date]

**Ad image:** ![Ad creative](./ad-image.png)

---

## 0. Why We Believe This Ad Is a Winner

[Evidence-based signals with explanations]

**What we can't confirm:** [Honest disclaimer]

---

## 1. Ad Components

### Primary Text (~[X] words, [format description])
> [Full copy]

### Headline (below image)
**"[headline]"**

### CTA Button
**[button text]**

### Description
[description or "Not visible in the Ad Library preview."]

---

## 2. Copy Strategy Deep-Dive

### Hook
*"[first line(s)]"*
[Analysis of what the hook does]

### Narrative Arc
| Act | Content | Purpose |
|-----|---------|---------|
| ... | ... | ... |

### Key Copywriting Techniques
**1. [Technique Name].** [Quote + explanation]
...

---

## 3. Image — Pixel-Level Visual Specification

**Template match:** `{template_name}` (upvote) or `none — proposing new:{snake_case_name}`

### Canvas & Format
[Table]

### Color Palette (exact hex from pixel sampling)
[Table]

### Layout Zones (% of canvas height)
[ASCII diagram]

**`image_zone: "<value>"`** — [one sentence: which scene type applies and why]

### Character Specification
[Table]

### [Supporting Elements — Platform/Shelf/etc.]
[Table]

### Background Details
[Table]

### Typography Specification
[Table + notes]

### Spacing & Padding (at [resolution])
[Table + notes]

---

## 4. Why This Image Works
**1. [Point].** [Explanation]
...

---

## 5. Strategic Analysis

### Target Audience
[Specific description]

### Funnel Position
[Position + reasoning]

### Competitive Positioning (never names brands)
| Alternative | How It's Dismissed |
|-------------|-------------------|
| ... | ... |

### Why It's Likely Winning
[Synthesis]

---

## 6. Key Learnings for Our Creative

1. **[Learning].** [Explanation with specific reference]
...
```

---

## Quality Checklist

Before delivering the analysis, verify:

- [ ] All 7 sections present (0 through 6)
- [ ] "Why We Believe" section uses specific signals, not opinions
- [ ] Full primary text transcribed (not truncated)
- [ ] All 5 ad components captured (Primary Text, Headline, Description, CTA, Image)
- [ ] Hook analysis includes word count and specific techniques
- [ ] Narrative arc table has Act / Content / Purpose columns
- [ ] Color palette has exact hex values from pixel sampling (not guesses)
- [ ] Layout zones use % of canvas with pixel estimates
- [ ] `image_zone` value set to where the main subject ends (read from layout zone diagram) — `"100%"` if the subject or scene fills the full canvas height
- [ ] Character spec includes size vs canvas, pose, colors, eye style, rendering
- [ ] Typography spec includes font, size, weight, color, alignment for each element
- [ ] Spacing table has both pixel and % values
- [ ] "Why This Image Works" explains strategic reasoning, not just describes
- [ ] Competitive positioning table shows how alternatives are dismissed
- [ ] Key learnings are numbered, specific, and reference this ad's techniques
- [ ] Character design formula included if ad uses illustrated character
- [ ] "What we can't confirm" disclaimer is present
- [ ] Analysis folder created at `output/analysis/winners/ad-analysis-<id>/`
- [ ] Analysis saved as `output/analysis/winners/ad-analysis-<id>/analysis.md`
- [ ] Image referenced at top of analysis with `![Ad creative](./ad-image.png)`
- [ ] Template match decided — `scripts/templates/usage.json` updated: either an upvote added to `templates.<name>.matches` (existing match), OR a new entry added directly to `templates` (new template — never to `proposed`)
- [ ] The teardown itself names the template match under section 3 (Image — Pixel-Level Visual Specification)
- [ ] Clickable file link output to user (see below)

## Output the File Link

After saving the analysis file, always end your response with a clickable markdown link so the user can open it directly:

```
Analysis saved: [output/analysis/winners/ad-analysis-<id>/analysis.md](output/analysis/winners/ad-analysis-<id>/analysis.md)
```

Then ask the user to manually save the ad image(s) into the analysis folder:

> Please save the ad image(s) into `output/analysis/winners/ad-analysis-<id>/` as `ad-image.png` (and `ad-image-2.png` etc. for multiple creatives).
