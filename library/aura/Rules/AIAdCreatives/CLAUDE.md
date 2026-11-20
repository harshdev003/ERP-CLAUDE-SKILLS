# Ad Creative — Shared Conventions

## Memory Rule

Do not save project conventions, formats, or learnings to auto-memory. Instead, add them to the appropriate file under `references/` — it is the shared knowledge base for this project, accessible to everyone working in this folder. You may also create new files within the existing `references/` subfolders when the content warrants its own file. Only add content directly to this file if it is a purely operational convention (how to run scripts, manage files, or use the tracker) that does not fit any `references/` file.

## References Folder

`references/` is the canonical knowledge base for this project. Whenever you need strategy, copy guidelines, brand voice, ad formats, generation rules, rendering specs, or competitor research — read from here.

```
references/
├── README.md                           — index and quick-start guide
├── brand/
│   ├── aurie.md                        — Aurie product identity, voice, taglines, visual themes, ICPs, ad avoids
│   └── aura.md                         — Aura product identity, voice, social proof, ad angles
├── messaging/
│   ├── aurie/
│   │   ├── messaging.md                — canonical Aurie messaging spine + 7 messaging concept categories
│   │   └── verticals-and-copy.md       — 11 emotional verticals + reusable copy bank
│   └── aura/
│       ├── messaging.md                — Aura messaging spine
│       └── verticals-and-copy.md       — Aura verticals + copy bank
├── formats/
│   ├── image/
│   │   └── image-gen-spec.md           — brief YAML format, field definitions, provider selection, filled examples
│   ├── video/
│   │   ├── video-scripts.md            — 28 video types, 7-stage script arc, wound/healing mirror
│   │   ├── video-gen-spec.md           — Sora brief format, production SOP, podcast ad workflow
│   │   └── video-gen-brief-template.md — ready-to-fill brief template
│   └── carousel/
│       ├── carousel-spec.md            — carousel format, card structure, 6 carousel types
│       └── carousel-brief-template.md  — ready-to-fill brief template
├── generation/
│   ├── visual-themes.md                — LIGHT and DARK theme specs — when to use each, what to avoid
│   ├── image-zone.md                   — visual.image_zone field: scene types, provider behavior, auto-derived spacing
│   ├── angle-divergence.md             — how to vary angles without redundancy
│   └── formula-extraction.md          — extracting structural DNA from winning ads
├── rendering/
│   └── composite-rendering-rules.md   — canvas sizing, winning ad composite specs, font limits, checkmark glyphs
├── research/
│   ├── ad-research-findings.md         — industry benchmarks, creative insights, platform strategy
│   ├── customer-research.md            — how to mine reviews and team knowledge to feed the brief pipeline
│   ├── performance-tracking.md         — learnings log fields, hook-level metrics
│   └── competitors.md                  — 19 competitors with Meta Ad Library links
└── assets/
    └── aurie-character.png             — reference character image for visual consistency
```

## Unicode in data.json

**Always write literal unicode characters** when editing `output/data.json` — never escape them as `\uXXXX` sequences. Write `✓` not `✓`, `—` not `—`, `💙` not `💙`, etc. This applies to every field: primary text, bullets, QA notes, hooks.

If you suspect escapes crept in, run `python3 scripts/build-tracker.py` — it normalizes the file back to literal unicode on every run.

## Credential handling

OpenAI image generation uses Viktor's secure OpenAI connection through the SDK.
It does not load `.env` or read `OPENAI_API_KEY`. The selected connection is
configurable with the non-secret `VIKTOR_OPENAI_CONNECTION_ID` environment
variable; the workspace-selected service-account connection is the default.
Run the image tooling in a Viktor SDK environment. Worker-sandbox unit tests use
mock connections and never need a credential.

Never save secrets in scripts, `.env`, briefs, output, logs, or documentation.
Older instructions to put OpenAI keys in `.env` are superseded.
Gemini and Slack's existing clients are outside this OpenAI migration; migrate
their credential handling separately before treating the repository as fully
remediated. Do not create new plaintext credentials for them.

Image writes retain native approval requirements. A pending request raises
`OpenAIApprovalRequired` with its `draft_id`; approve and submit that same draft
through Viktor, and save the approved image response rather than rerunning the
generator (which would create a second request).

## Slack posting (`scripts/post-to-slack.py`)

**Only use this script when a skill's instructions explicitly call for it, or when the user explicitly asks.** Never post to Slack automatically after generation. It uploads images **natively** via Slack's `files.getUploadURLExternal` + `files.completeUploadExternal` flow — images render inline, no GitHub push or public URL required.

**Setup (one time):**
- Reads `SLACK_BOT_TOKEN` and `SLACK_CHANNEL_ID` from `.env` (same file as `GEMINI_API_KEY`).
- `SLACK_CHANNEL_ID` is the default channel — use it when no channel is specified.
- Bot needs `chat:write` + `files:write` scopes and must be invited to each target channel.

**Common invocations:**

```bash
# Plain text message
python3 scripts/post-to-slack.py --channel C0AU7C07R8X --text "hello"

# Single image with caption
python3 scripts/post-to-slack.py --channel C0AU7C07R8X \
    --file output/briefs/051-AURIE-3am-anxiety/images/051-3am-anxiety-imessage.png \
    --comment "Brief #51 — 3am Anxiety"

# Single message with header copy + multiple images attached, in order
python3 scripts/post-to-slack.py --channel C0AU7C07R8X \
    --text "*5 new Aurie ads* :sparkles:" \
    --file output/briefs/051-.../images/051-...png \
    --file output/briefs/052-.../images/052-...png

# Batch mode: header + per-file captions as threaded replies
cat <<'JSON' | python3 scripts/post-to-slack.py --channel C0AU7C07R8X --batch -
{
  "header": "5 new Aurie ads :sparkles:",
  "threaded": true,
  "posts": [
    {"text": "Brief #51 — 3am Anxiety", "file": "output/briefs/051-AURIE-3am-anxiety/images/051-3am-anxiety-imessage.png"},
    {"text": "Brief #52 — Inner Critic", "file": "output/briefs/052-AURIE-inner-critic/images/052-inner-critic-notes.png"}
  ]
}
JSON
```

**Agent rule:** Prefer this script over posting raw GitHub URLs. If no channel is specified, use `SLACK_CHANNEL_ID` from `.env` as the default. If `SLACK_BOT_TOKEN` or `SLACK_CHANNEL_ID` is missing, prompt the user to add it to `.env` — do not fall back to pushing a feature branch just so Slack can unfurl an image.

## Brand Guidelines

See [`references/brand/aurie.md`](references/brand/aurie.md) (product identity, voice, visual themes, ICPs) and [`references/brand/aura.md`](references/brand/aura.md). For the canonical Aurie messaging spine, also load [`references/messaging/aurie/messaging.md`](references/messaging/aurie/messaging.md).

**Visual themes (LIGHT/DARK):** [`references/generation/visual-themes.md`](references/generation/visual-themes.md)

## Copywriter Persona and Copy Guidelines

See the brand files above and [`references/messaging/`](references/messaging/) for full guidance.

Key specifics not to look up every time:
- **Aurie on-image bullets:** "✓ Built by real therapists and clinicians / ✓ Grounded in 20+ proven therapeutic modalities / ✓ Designed to help you heal, not just cope"
- **Aura on-image bullets:** "✓ Trusted by 8 million+ people / ✓ Apple's #1 New App of the Year / ✓ AI-personalized for your needs"
- **Aurie link URL:** `https://www.aurie.ai/signup` | **Aura link URL:** `https://www.aurahealth.io/`

## Folder Structure

- `output/` — Final deliverables: tracker files, data, and per-brief folders
  - `output/tracker.html` — The visual tracker
  - `output/data.json` — Single source of truth for all tracker data (BRIEFS, CREATIVES, QA arrays)
  - `output/briefs/{NUM:03d}-{APP}-{description}/` — One subfolder per brief, containing:
    - `characters/` — Raw character/scene images from Gemini (no text)
    - `images/` — Final composite images with text overlays
    - The brief YAML file
  - `output/analysis/winners/` — Winning ad teardowns, named `ad-analysis-<ad-library-id>/`
  - `output/analysis/competitors/` — Competitor analyses, one subfolder per competitor slug
- `references/` — Shared knowledge base (see above)
- `scripts/` — Python scripts for image generation, text compositing, and automation
- `.claude/skills/<skill-name>/SKILL.md` — Reusable skill instructions, one folder per skill

### Brief Folder Naming Convention

`{NUM:03d}-{APP}-{description}` — e.g. `001-AURIE-anxiety-overwhelm`, `011-AURA-sleep-insomnia`

- `{NUM:03d}` = Zero-padded brief number — **must match** `generate-ads.py`'s `find_brief()` lookup
- `{APP}` = `AURIE` or `AURA` (uppercase)
- `{description}` = Short kebab-case label

The Brief # must match the Brief # (index 1) in all data.json arrays. This is the primary key that links folders, tracker rows, and image paths.

## Creative Tracker

The visual tracker is `output/tracker.html`. It fetches `data.json` live from the server at runtime — no data is baked into the HTML.

**The tracker requires the server to be running:**

```
python3 scripts/tracker-server.py
```

Open `http://localhost:7433/tracker.html`. The server exposes:
- `GET /data.json` — reads fresh from disk on every page load
- `POST /save-reviews` — persists review state to `output/reviews.json`

**After editing `data.json`, just reload the browser** — no rebuild step needed.

**To validate `data.json` stats:**
```
python3 scripts/build-tracker.py
```

### Data Source: `output/data.json`

Single source of truth. Three arrays:

- **`BRIEFS`** — `[date, #, app, angle, hook_type, hook, format, ad_type, tone, strategy, competitor_intel, rationale, risk, priority, status, source, folder]`
- **`CREATIVES`** — `[date, #, app, angle, primary_text, headline, description, cta, image_direction, image_path, image_hook, image_proof]`
- **`QA`** — `[date, #, app, hook_check, risk, audience, freshness, score, notes]`

Joined by Brief # (index 1). Rules for new rows:
1. Unique Brief # (index 1), Date in `YYYY-MM-DD` format
2. All 3 arrays must stay in sync — same Brief # in each
3. Self-contained rows — never use "Same as Brief #N" references
4. Image paths relative to `output/` (e.g., `briefs/001-AURIE-anxiety/images/composite.png`)
5. `folder` field matches actual folder name under `output/briefs/`
6. Create the corresponding brief folder under `output/briefs/` following the naming convention above

## Image Generation Pipeline

Composite pipeline: **image generator** (Gemini or OpenAI, no text on image) → **Pillow** (all text overlays).

**Never render text via the image model.** AI text is unreliable — always use Pillow.

**Do not regenerate characters unless explicitly asked.** Try compositing fixes first.

### Provider selection

```bash
python3 scripts/generate-ads.py 33                              # OpenAI (default)
python3 scripts/generate-ads.py 33 --provider gemini           # Gemini gemini-2.5-flash-image
python3 scripts/generate-ads.py 33 --provider openai --model gpt-image-2
```

| `--provider` | Default model | Requires |
|---|---|---|
| `openai` *(default)* | `gpt-image-2` | Secure Viktor OpenAI connection |
| `gemini` | `gemini-2.5-flash-image` | `GEMINI_API_KEY` |

Provider is a **runtime flag** — same brief can be run with multiple providers. Outputs are namespaced by provider (e.g. `foo-openai.png`) so they never overwrite each other.

**Font paths (Cowork sandbox):** Poppins at `/usr/share/fonts/truetype/google-fonts/Poppins-Medium.ttf` (and `-Regular.ttf`), DejaVuSans at `/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf`.

### Meta Ad Image Sizes

- **Stories / Reels (9:16):** 1080x1920px
- **Feed vertical (4:5):** 1080x1350px — crop from the 9:16 output
- **Feed square (1:1):** 1080x1080px

For Gemini, always pass `image_config` in `GenerateContentConfig`:
```python
image_config=types.ImageConfig(aspect_ratio="9:16", image_size="2K")
```

## Ad Templates

Template-based pipeline. Templates live in `scripts/templates/`, shared primitives in `scripts/compositor.py`.

**Registered templates:**

- **`subject_hook_bullets`** *(default)* — Dominant visual subject (top ~50–65%) + gradient fade + hook (2 lines) + 3 checkmark bullets. All overridable via `composite:` section in the brief. Historical name: `character_plush`.
- **`custom`** — Spec-driven renderer. Brief provides explicit `layout:` block with zones (`image`, `fill`, `gradient`, `text`). Use when no named template fits.

```bash
python3 scripts/generate-ads.py --list-templates   # list templates
python3 scripts/generate-ads.py --template-stats   # view upvote counts
```

**Keep templates flexible.** A template captures structural concept, not paint. Minor positioning tweaks = same template. Only fork when the structural concept itself is different (new zones, different element types). See CLAUDE.md "Template Upvote System" below.

**Promotion rule:** if a `custom` layout wins on two or more briefs, promote it to a named template.

### Template Upvote System

Every analyzed ad gets classified against the template registry. This surfaces which layouts competitors keep investing in.

- **Storage:** `scripts/templates/usage.json` — `templates.<name>.matches` (upvotes) and `proposed` (awaiting promotion)
- **Promotion:** when 2+ analyses propose the same layout, create `scripts/templates/<name>.py`, register in `scripts/templates/__init__.py`, move entry from `proposed` to `templates`
- **When to read stats:** before writing a new brief
- **Who classifies:** the `match-template` skill — `competitor-analysis` and `winning-creative-analysis` delegate to it, never duplicate the logic

## Default Composite Text Settings

Fallback values when a brief's YAML has no `composite:` section:

```
headline_font: "Lato Semibold (sans-serif), 88px"
bullet_font:   "Lato Semibold, 40px"
check_font:    "DejaVu Sans, 40px"
bullet_line_height: 68
bottom_margin: 350
hook_to_bullet_gap: 44
check_to_text_gap:  8
gradient_start: "42%"
gradient_end:   "62%"
```

## Ad Formats and Sizes

Three format types supported. Full specs, brief templates, and type/hook taxonomies live in [`references/formats/`](references/formats/).

| Format | Reference files |
|---|---|
| **Static image** | [`formats/image/image-gen-spec.md`](references/formats/image/image-gen-spec.md) — brief YAML + provider rules |
| **Video** | [`formats/video/video-scripts.md`](references/formats/video/video-scripts.md) — 28 video types, 7-stage arc · [`formats/video/video-gen-spec.md`](references/formats/video/video-gen-spec.md) — Sora brief + production SOP |
| **Carousel** | [`formats/carousel/carousel-spec.md`](references/formats/carousel/carousel-spec.md) — card structure, 6 carousel types |

### Meta Ad Sizes

| Placement | Ratio | Dimensions |
|---|---|---|
| Stories / Reels | 9:16 | 1080 × 1920 px |
| Feed vertical | 4:5 | 1080 × 1350 px |
| Feed square | 1:1 | 1080 × 1080 px |

Pipeline generates 9:16 by default; 4:5 is a crop. Full canvas and layout specs in [`references/rendering/composite-rendering-rules.md`](references/rendering/composite-rendering-rules.md).

## Creative Testing Dimensions

For full divergence guidance across a batch (5 axes, core principle, anti-redundancy test), see [`references/generation/angle-divergence.md`](references/generation/angle-divergence.md).

Four test axes at a glance:
1. **Color** — same character, different body color
2. **Character** — different character design, same message
3. **Message** — same character, different headline/copy angle
4. **Angle** — different target emotion (changes everything; highest-effort variant)

Name variant files with the axis being tested: e.g., `AUR-XXX-char-N-color-coral.png`.

## Skills and Agents

Skills live in `.claude/skills/` (loaded automatically by Claude Code). Each skill has a `SKILL.md` with the full methodology.

- **`winning-creative-analysis`** — Pixel-level teardowns of competitor/winning ads from Meta Ad Library. Output to `output/analysis/winners/ad-analysis-<ad-library-id>.md`. Delegates template classification to `match-template`.
- **`competitor-analysis`** — Scrapes top-5 active Meta ads for a competitor. Delegates template classification to `match-template`.
- **`match-template`** — Classifies a single ad against the template registry in `scripts/templates/usage.json`. Called by any skill that analyzes an ad layout.
- **`creative-review`** — Visual review of composites after generation. Checks brief match, quality, artifacts, text correctness. Auto-fixes or flags for character regeneration. Quality gate before tracker.
- **`creative-scorer`** (name: `creative-qa`) — Strategy scoring of every brief+creative pair. Populates QA array in `data.json`. Scores 1–10 (most land 6–8). All Medium/High risk items need specific mitigation notes.
- **`similar-ads`** — Fast creative riffing from a reference image. Produces N new ads with different angles/hooks. No analysis files saved.

Agents live in `.claude/agents/`:
- **`image-generator.md`** — Generates character illustration + Pillow composite for a single brief.
