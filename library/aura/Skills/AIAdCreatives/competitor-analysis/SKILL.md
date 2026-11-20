---
name: competitor-analysis
description: "Scrape top-5 active Meta ads for a competitor, write a strategic analysis, and save metadata for incremental re-runs. Handles both first-run (new competitor) and update-run (adds only newly appeared ads since last_analyzed). Use when the user says: 'analyze competitor X', 'analyze more competitors', 'update competitor analysis', 'check what [competitor] is running', 'run competitor analysis'. Uses the existing analyses (Calm, BetterHelp, Talkspace, Headspace) as the format template."
---

# Competitor Analysis — Meta Ad Library Scraping

Pull a competitor's top-5 active ads from the Meta Ad Library (sorted by total impressions), write a strategic analysis, and save metadata so future runs only add new ads. Output lives in `output/analysis/competitors/{slug}/`.

---

## When to Use

- User says "analyze competitor X" / "analyze more competitors" / "run competitor analysis"
- User says "update competitor analysis" / "check for new ads from [competitor]"
- User references `references/competitors.md`

**Skip trial-and-error.** The browser interactions below (URL patterns, page ID discovery, JS extraction) were painful to figure out the first time. Follow them exactly.

---

## Step 0 — Load the Competitor List

1. Read `references/competitors.md`. It has three tables (Direct Competitors, Therapy/Telehealth, Wellness/Sleep/Focus) with pre-filled Ad Library URLs that already contain `view_all_page_id=...`.
2. Extract the page ID for each competitor from the URL (`view_all_page_id=(\d+)`).
3. If the user named a specific competitor, pick it from the list. If the user said "analyze more" without names, pick the most Aurie-relevant ones not yet analyzed — **prioritize Direct Competitors** (AI therapy/emotional health) over Therapy/Telehealth over Wellness.
4. Check `output/analysis/competitors/` for existing folders — skip competitors already fully analyzed unless the user asks for an update.

---

## Step 1 — Check Existing Analysis (Update Path)

For each competitor, compute `slug = lowercase(name).replace(' ', '').replace('.', '')`. Check `output/analysis/competitors/{slug}/metadata.json`:

- **Does not exist** → new run, skip to Step 2.
- **Exists** → read `last_analyzed` and `analyzed_ad_ids`. If the user asked for an update, proceed to Step 2 but **preserve** old ad entries in the analysis and only add new ones. If the user did not ask for an update and `last_analyzed` is recent (< 7 days), tell the user the analysis is fresh and skip.

When updating: the old metadata already tells you what was in the top 5 last time. You'll compare new top-5 IDs to `analyzed_ad_ids` — any ID not in the old list is new.

---

## Step 2 — Open the Ad Library in the Browser

Use **Claude in Chrome** MCP tools. Browser flow that works (do not deviate):

1. **Get tab context** via `tabs_context_mcp`. Create a tab if none exists.
2. **Navigate** to the Ad Library URL for this competitor. The exact pattern:

   ```
   https://www.facebook.com/ads/library/?active_status=active&ad_type=all&country=US&is_targeted_country=false&media_type=all&search_type=page&sort_data[direction]=desc&sort_data[mode]=total_impressions&view_all_page_id={PAGE_ID}
   ```

   This is the exact URL format in `references/competitors.md`. Using anything else (e.g. `country=ALL`, or missing `search_type=page`) often yields "No ads match" or the wrong ordering.

3. **Page IDs are already in `competitors.md`.** Do not go hunting on facebook.com/{company} — the page's `actorID` is NOT the page ID, that leads to 0-result pages. Only use IDs from `competitors.md` or from a Google search that returns an actual Ad Library URL.

### If the Competitor Is Not in `competitors.md`

Fallback order to find the page ID:

1. `WebSearch` for `"Ad Library" {competitor} "view_all_page_id" facebook` — look for a result URL like `facebook.com/ads/library/?...view_all_page_id=NNNN`.
2. Navigate to `https://www.facebook.com/{vanity}/` and grep the HTML for `page_id["\:]+(\d{10,18})` (not `actorID` — that's the session, not the page).
3. As a last resort, in the Ad Library type the name into the advertiser search and let the dropdown fill `view_all_page_id` in the URL.

**Once found, add the competitor to `references/competitors.md`** (see Step 3.5 below).

### If the Existing URL in `competitors.md` Is Broken

A URL is broken when any of these are true:

- The Ad Library page shows "No ads match your search criteria" with the `view_all_page_id` filter active (and the brand is clearly still advertising elsewhere — check with `country=ALL`).
- The header shows a different brand name than expected (e.g. `competitors.md` says "Alinea" but the page loads "Alinea" the investing app — wrong entity).
- The page loads regional Similar Ads only but no primary brand ads (brand may have changed their page).

When broken: rediscover the correct page ID via the fallback order above, then **update the row in `competitors.md`** with the new `view_all_page_id` (see Step 3.5).

---

## Step 3.5 — Update `references/competitors.md` When Needed

Trigger conditions:

- **New competitor** — user named a competitor not in `competitors.md`, and you found a valid page ID.
- **Broken URL** — existing row's page ID yields 0 results or points to the wrong entity.
- **Renamed brand** — page name in the header differs from the `competitors.md` label (note this as a name change).

How to update:

1. Read `references/competitors.md`.
2. Pick the correct section based on what the competitor does:
   - `Direct Competitors (AI Therapy / Emotional Health)` — AI journaling, AI therapy, AI self-discovery
   - `Therapy / Telehealth` — online therapy, therapy marketplaces, telehealth
   - `Wellness / Sleep / Focus` — meditation, sleep apps, focus/productivity
   - If none fit, pick the closest or add a new section at the bottom with a clear heading.
3. Add or replace the row. Format must match the existing rows exactly:

   ```markdown
   | {Name} | {What they do, 2–5 words} | [View Ads](https://www.facebook.com/ads/library/?active_status=active&ad_type=all&country=US&is_targeted_country=false&media_type=all&search_type=page&sort_data[direction]=desc&sort_data[mode]=total_impressions&view_all_page_id={PAGE_ID}) |
   ```

   Keep the alphabetical ordering within each section loose — just add near similar entries.
4. If replacing a broken row, delete the old row entirely (don't leave both).
5. Commit the `competitors.md` update in the same commit as the competitor analysis files, or in its own commit if multiple analyses are being added — use `Update competitors.md — {reason}` when standalone.

**Do not invent page IDs.** If you couldn't verify that the URL actually loads the expected brand's ads in Step 3, don't write it to `competitors.md`. Better to tell the user the competitor couldn't be located than to poison the list with a bad entry.

---

## Step 3 — Extract the Top Ads via JavaScript

Run this in the tab (via `javascript_tool`). It parses `document.body.innerText`, splits into ad blocks starting at each `Library ID:` line, and returns a summary:

```javascript
const text = document.body.innerText;
const lines = text.split('\n');
const ads = [];
let cur = null;
for (const line of lines) {
  const t = line.trim();
  if (t.startsWith('Library ID:')) {
    if (cur) ads.push(cur);
    cur = { id: t.replace('Library ID: ',''), lines: [] };
  } else if (cur && t) cur.lines.push(t);
}
if (cur) ads.push(cur);
const skip = new Set(['​','Open Drop-down','See ad details','See summary details','Sponsored','This ad has multiple versions','EU transparency']);
const header = text.slice(0, text.indexOf('Library ID:'));
const pageName = (header.match(/\n([A-Za-z][^\n]{1,30})\nAds\nAbout/) || [])[1] || 'unknown';
const count = (header.match(/([\d,~]+) results/) || [])[1] || '?';
'PAGE: ' + pageName + ' | COUNT: ' + count + '\n\n' +
ads.slice(0,8).map((a,i) => `AD${i+1}[${a.id}]: ${a.lines.filter(l=>!skip.has(l)).join(' | ')}`).join('\n\n')
```

**Important notes on this extraction:**

- The `country=US` filter limits to US ads. If `COUNT` returns `?` or the page shows "No ads match your search criteria," the page ID is wrong or the competitor isn't running US ads. Try `country=ALL`.
- Results are truncated by the tool; if `AD5` or later gets cut off, re-run with `.slice(3,8)` or `.slice(5,10)`.
- `Platforms` lines and `0:00 / 0:00` (video duration) are not in the skip set on purpose — they help you identify format (video vs static). Filter them out when writing the narrative.
- The advertiser name line appears right before or as part of each ad. Watch for creator/UGC ads where the advertiser is `"{Creator} with {Brand}"` (e.g. "Bri & Lo with Rosebud.app") — these are partnership ads, note them as UGC.

### When the Same Copy Repeats

Competitors often run the same primary text across multiple Library IDs (different creative variants). De-dupe by copy before picking the top 5:

```javascript
const seen = new Set();
const unique = ads.filter(a => { const k = a.lines.join('|'); if(seen.has(k)) return false; seen.add(k); return true; });
```

Report "top 5 unique creatives" rather than top 5 Library IDs when this happens.

---

## Step 4 — Gather Enough Data for 5 Ads

Re-run the extraction with different slices if needed until you have:

- For each of the top 5: Library ID, start date, format (video/static/UGC), primary text, headline, CTA.
- Platform breadth (count of platform icons — usually 4–5: FB, IG, Messenger, Audience Network, Threads/Quora).
- Count of total active ads (from `COUNT`).
- Whether the ad uses the creator-UGC format (`"{Creator} with {Brand}"` advertiser name).

If the top ads are just variants of the same copy (e.g. Talkspace runs mostly the same "insurance" text), that IS the finding — write it up.

---

## Step 4.5 — Match Each Ad Against the Template Registry

Before writing the analysis, classify each of the top 5 ads against our ad layout templates. This gives us signal on which formats competitors keep investing in — the **upvote** system.

For each ad, also determine the `visual.image_zone` value a brief recreating it should set: it is simply **where the main subject ends** as a % of canvas height. Estimate this from the visual (e.g. character occupies top 55% → `"55%"`). If the subject or scene fills the full canvas height — even if text is composited over a gradient at the bottom — the value is `"100%"`. Record this as the `**image_zone:**` line in the per-ad section.

**Delegate to the `match-template` skill** — it owns the full methodology (registry lookup, confidence levels, proposal format, promotion rule). For each of the top 5 ads, invoke it with:

- `source: "competitor-analysis"`
- `brand: "{Competitor Name}"`
- `ad_id: "{library_id}"`
- Visual reference (screenshot or detailed layout description)

The skill returns exactly one of:

- `MATCH: <template> (<confidence>) — <rationale>` — upvote recorded in `scripts/templates/usage.json`
- `PROPOSE: new:<name> — <rationale>` — proposal recorded in `usage.json` under `proposed`
- `NO_MATCH: <reason>` — layout not worth reusing; nothing persisted

Use the returned string in the per-ad section of the analysis (see Step 5) as the `**Template:**` line.

---

## Step 5 — Write the Analysis

Save to `output/analysis/competitors/{slug}/analysis.md`. **Use the existing analyses as the format template** — read one of `output/analysis/competitors/{calm,betterhelp,talkspace,headspace}/analysis.md` for the exact structure. Match:

### Required structure (exact headings)

```markdown
# Competitor Analysis — {Name}
**Last analyzed:** YYYY-MM-DD
**Source:** Meta Ad Library, sorted by total impressions (descending)
**Active ads at time of analysis:** ~{N}
**Page ID:** {page_id}

---

## Top 5 Active Ads (by impressions)

### Ad 1 — Library ID: {id}
**Started:** {date} | **Format:** {Video/Static/UGC} | **Platforms:** {list}

**Primary Text:**
> {quote}

**Headline:** {text}
**CTA:** {button}

**Template:** `{existing_template_name}` (upvote) or `new:{proposed_name}` (no existing match — proposed)

**image_zone:** `"{value}"` — {one sentence: which scene type applies}

> **Image-availability constraint:** Color Palette and Layout Zones below require **direct visual access** to the ad creative. The default Meta Ad Library scrape does **not** give you that — Facebook CDN blocks `<img>` downloads (403/CORS) and the in-browser screenshot path is also blocked. Fill these sections in only when one of the following is true:
> 1. The user provided a screenshot or image file directly
> 2. The ad has an existing pixel-level teardown in `output/analysis/winners/ad-analysis-{id}/` — link to it instead of duplicating
> 3. The user explicitly asks you to estimate from the in-Ad-Library preview thumbnail (clearly mark estimates with `(estimated)`)
>
> If none of these apply, write `*Pixel-level visualization not captured — image not downloadable from Meta Ad Library. Request a screenshot from the user to fill in.*` and skip the two sections below for that ad.

#### Color Palette
Sample exact hex values from the image for every distinct color element:
- Background (may have multiple zones)
- Character/subject body (main, highlight, shadow)
- Platform/surface elements
- Accent elements
- Text colors (headline, body, supporting)

Use a table:

| Element | Hex | RGB | Description |
|---|---|---|---|
| {e.g. Background — top zone} | `#XXXXXX` | `(R, G, B)` | {one-line context — gradient direction, where it appears, etc.} |
| {e.g. Subject body — main} | `#XXXXXX` | `(R, G, B)` | {…} |
| ... | ... | ... | ... |

#### Layout Zones
Map the vertical layout as % of canvas height. Use an ASCII diagram showing where each zone starts and ends, with pixel estimates at 1080w (i.e. 9:16 = 1080×1920, so each 1% ≈ 19px vertically).

```
0%   ┌──────────────────────────┐  0px
     │  {zone 1: e.g. headline} │
N%   ├──────────────────────────┤  Npx
     │  {zone 2: e.g. subject}  │
M%   ├──────────────────────────┤  Mpx
     │  {zone 3: e.g. gradient} │
P%   ├──────────────────────────┤  Ppx
     │  {zone 4: e.g. CTA}      │
100% └──────────────────────────┘  1920px
```

Notes (1–2 lines): which zones are most pixel-dense, where the visual weight sits, how the gradient transitions between zones.

**What's notable:** {1-paragraph strategic read — what's the hook doing, why it works, what it signals about their positioning}

---

(repeat for Ads 2–5)

## Key Strategy Patterns

### 1. {Pattern name in bold sentence form}
{Why it matters, what it reveals}

(3–6 patterns)

---

## Gaps / Angles {Name} Isn't Running

- **{Gap 1}** — {how Aurie can exploit}
(list 4–6)

---

## What to Steal

1. **{Device name}** — {how to adapt for Aurie}
(3–5 items)
```

### Analytical voice to match

- Specific not generic. "Take the quiz" is a CTA device; "'Take the quiz' removes commitment to the lowest possible step" is analysis.
- Quote actual ad copy in blockquotes — the specific language is the evidence.
- Call out what's missing (no AI angle, no character creative, no nervous system language) because those are Aurie's moves.
- No hedging ("seems like", "probably"). State what the ads show.

### Update-run variations

If this is an update (metadata.json already existed), the goal is to keep the file useful as a *current-state* document while preserving the historical entries that explain how things got here. Apply all of the following — these are not optional:

#### 1. Add a "New Ads Since {old_last_analyzed}" preamble
At the top of the Top Ads section (above the per-ad list), write a short subsection summarizing:
- Library size delta (e.g. `~85 → ~68 active ads`)
- Top-5 churn count (which IDs dropped out, which entered)
- The single most important strategic shift the churn implies (1–2 sentences)

Naming: `## New Ads Since {old_last_analyzed}` placed *between* the file header and the Top Ads section, not inside it.

#### 2. Write a full per-ad breakdown for every new top-5 entrant
Do not just list new IDs in the preamble and call it done. **Each new top-5 ad gets its own `### Ad N — Library ID: ...` block with the same fields as a first-run entry** (Started / Format / Platforms / Primary Text / Headline / CTA / Visual / What's notable). Tag the heading `*(new entrant since {old_last_analyzed})*` so the new entries are scannable.

If a competitor has all-new top 5 (full churn — e.g. Liven Apr 2026 → 100% Spanish-language refresh), and all five share identical copy, you may write one shared `Primary Text / Headline / CTA` block at the top of the section and then give each ad its own short per-ad note (Started date, "What's notable" with the visual / variant / volume detail). This is preferable to repeating the same copy block 5 times.

#### 3. Reorder the per-ad list to match the current impression ranking
Top of the section = currently #1, then #2, then #3, etc. New entrants slot into their actual rank position, not appended at the end. Tag each heading with the rank, e.g. `### Ad 1 (currently #1) — Library ID: ...`.

#### 4. Keep dropped-out ads, don't delete them
Ads that were in the prior top 5 but have slipped out remain in the file for continuity. Place them *after* the current top 5 with a heading that flags the shift, e.g. `### Ad 6 (was #3, now just outside top 5) — Library ID: ...`. Add 1–2 sentences on what likely displaced them — that's where strategic signal lives. Do **not** rename the section "Top 5 Active Ads" to "Top 6/7/9 Active Ads"; instead rename to **"Top Active Ads (in order of current ranking)"** since the count fluctuates run-to-run.

#### 5. Strategy/gaps/steal sections
- If 3+ of the old top 5 dropped out (or the dominant hook has shifted), rewrite these sections to reflect the current library.
- Otherwise leave them — they describe the brand's playbook, not just one creative wave.

#### 6. Bump dates and counters
- Header: bump `**Last analyzed:**` and `**Active ads at time of analysis:**` (show the delta inline if useful: `~68 (down from ~85)`).
- Metadata.json: see Step 6 below.

#### Anti-patterns to avoid
- ❌ Listing new ads only in the "New Ads Since" preamble without writing per-ad breakdowns.
- ❌ Keeping the per-ad list in its old order with new entries appended at the bottom.
- ❌ Deleting dropped-out ads (you lose the why-it-changed signal).
- ❌ Wholesale rewriting the strategy section when only 1–2 ads changed (you lose continuity with the prior analysis).

---

## Step 6 — Write `metadata.json`

Save to `output/analysis/competitors/{slug}/metadata.json`. Format:

```json
{
  "competitor": "{Name}",
  "app": "{domain}",
  "meta_page_id": "{page_id}",
  "meta_page_name": "{exact name from header}",
  "last_analyzed": "YYYY-MM-DD",
  "total_active_ads": {integer},
  "analyzed_ad_ids": ["id1", "id2", "id3", "id4", "id5"],
  "top_ad_ids": ["id1", "id2", "id3", "id4", "id5"],
  "primary_angles": ["angle 1", "angle 2", "angle 3"],
  "notes": "~{N} active ads. {1-2 sentences on the dominant pattern.} {1 sentence on gaps.} Images could not be downloaded due to Facebook CDN restrictions — view at https://www.facebook.com/ads/library/?id={ID}"
}
```

On an update run: merge new ad IDs into `analyzed_ad_ids` (keep historical), overwrite `top_ad_ids` with the current top 5, update `total_active_ads`, update `last_analyzed`, update `notes` if the angle shifted.

---

## Step 7 — Commit

Commit title: `Add competitor analysis — {Name}` (or `Update competitor analysis — {Name}` for updates). Body: 2–3 sentences on the dominant finding and anything Aurie can steal or avoid. Co-authored-by line per project convention.

Do not commit the `images/` subfolder — Facebook CDN images can't be downloaded from the browser (403 / CORS / screenshot blocked). The metadata stores the ad IDs so the user can open each ad manually via `facebook.com/ads/library/?id={ID}`.

---

## Known Gotchas

- **`actorID` on facebook.com/{brand} is NOT the page ID.** It's a session/browser actor. Only use `page_id` from the HTML or IDs from `competitors.md`.
- **The SPA rewrites URLs.** Your `navigate()` to `?q=Brand&search_type=page` often redirects to `search_type=keyword_unordered` — you'll get ads that just mention the brand, not the brand's ads. Always use `view_all_page_id=`.
- **`country=US` is strict.** A brand with no US ads (ever seen with Headspace's old page ID) returns empty. Try `country=ALL` before concluding the page ID is wrong.
- **Advertiser page might have moved.** If a known brand returns 0 ads, their Facebook page may have been renamed or they may have stopped Meta advertising (e.g. Headspace moved from one page to another). Document that as a finding, don't chase it for more than ~5 minutes.
- **Image download is not possible** from the in-browser session (Facebook CDN returns 403 without session cookies, canvas is CORS-tainted, and the MCP browser can't write screenshots to the local filesystem). Don't promise images in the output.
- **Keep `images/` folders empty** — they exist for future manual population but don't block on populating them.

---

## Output Checklist

For each competitor analyzed:

- [ ] `output/analysis/competitors/{slug}/analysis.md` written and follows the exact structure above
- [ ] `output/analysis/competitors/{slug}/metadata.json` written with all required fields
- [ ] `output/analysis/competitors/{slug}/images/` folder exists (empty is fine)
- [ ] `references/competitors.md` updated if the competitor was new or had a broken/changed URL
- [ ] Committed with `Add/Update competitor analysis — {Name}` message
- [ ] User told which competitors were analyzed, the single most important finding per competitor, and whether `competitors.md` was updated
