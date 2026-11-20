---
name: analyse-meta-ads
description: "Analyse top-performing Aurie Meta ads. Fetches yesterday's top spenders grouped by campaign type (WO, Appstack, Other), resolves creatives, identifies the top 5 creatives by total spend, downloads their images, generates formula-extraction breakdowns, and updates index.md. Also writes campaign-spend.md and creative-spend.md tables. Use when the user wants to see what ads are winning on Meta, understand current creative patterns, or track top creative performance over time."
---

# Analyse Meta Ads

Fetch yesterday's top spenders, group by campaign type, identify the top 5 creatives by total spend, analyse their images, and update the output files.

---

## Knowledge to Load First

Before starting, read both files in full:

- `references/research/performance-tracking.md` — fields to track per ad
- `references/generation/formula-extraction.md` — the Copy / Image / Why It Works breakdown framework used in Phase 7

---

## Phase 1: Validate Environment

```bash
grep -q META_TOKEN_AURIE .env && echo "OK" || echo "MISSING"
```

If missing, tell the user to add `META_TOKEN_AURIE=<token>` to `.env` and stop.

---

## Phase 2: Fetch Yesterday's Top Spenders

```bash
python3 scripts/meta/top-ads.py --days 1 --sort spend --limit 200 --json --all
```

Returns all ads from yesterday across all campaigns, sorted by spend descending. Each row includes: `ad_id`, `ad_name`, `adset_name`, `campaign_name`, `spend`, `impressions`, `ctr`, `cpc`, `_spend`, `_cpr`, `_results`.

Create output directory:

```bash
mkdir -p output/analysis/meta-ads/top
```

---

## Phase 3: Group by Campaign and Write campaign-spend.md

Classify each ad into a campaign group based on `campaign_name`:
- **WO** — campaign name contains `WO` (case-insensitive)
- **Appstack** — campaign name contains `appstack` (case-insensitive)
- **Other** — everything else

Within each group, take the **top 5 by `_spend`**.

Write `output/analysis/meta-ads/top/campaign-spend.md`:

```markdown
# Campaign Spend — Yesterday

**Date:** YYYY-MM-DD  
**Window:** Yesterday

---

## WO Campaigns

| # | Ad Name | Ad ID | Campaign | Spend | CTR | CPC | CPR |
|---|---------|-------|----------|-------|-----|-----|-----|
| 1 | ... | `ad_id` | ... | $X.XX | X.XX% | $X.XX | $X.XX / — |

## Appstack Campaigns

| # | Ad Name | Ad ID | Campaign | Spend | CTR | CPC | CPR |
|---|---------|-------|----------|-------|-----|-----|-----|

## Other Campaigns

| # | Ad Name | Ad ID | Campaign | Spend | CTR | CPC | CPR |
|---|---------|-------|----------|-------|-----|-----|-----|
```

Use `—` for CPR when `_cpr` is null. Include all rows that have spend > 0. If a group has fewer than 5 ads, include all of them. If a group has no ads, write `_No ads in this group yesterday._` under its heading.

---

## Phase 4: Resolve Creative IDs

Collect the union of all ads selected across the three groups (up to 15 unique ad IDs). Run in one command:

```bash
python3 scripts/meta/resolve-creatives.py <ad_id1> <ad_id2> ... <ad_idN>
```

Returns JSON: `[{ad_id, creative_id, creative_name, headline, primary_text, description, cta}]`.

Build a lookup: `ad_id → {creative_id, creative_name, headline, primary_text, description, cta}`. Drop any rows where `creative_id` is null.

> **Note:** The same creative can have different metadata across ads if the creative was set up with different copy. Always carry the metadata alongside the representative `ad_id` so per-creative tables reflect the actual copy shown.

---

## Phase 5: Aggregate by Creative and Write creative-spend.md

For each ad in the union set, combine its creative_id, ad_name, spend, and campaign group. Preserve `ad_name` from the Phase 2 data.

### 5a — Global aggregation (for Phase 6 and index.md)

Aggregate spend per `creative_id` across all groups:
- `total_spend` = sum of `_spend` across all ads sharing that creative
- `wo_spend`, `appstack_spend`, `other_spend` = subtotals per group

Take the **top 5 creatives by `total_spend`**. For each, record one representative `ad_id` (the highest-spend ad using that creative) and its `ad_name`. Store this global top 5 — it is used by Phase 6 (download) and Phase 8 (index.md). Do **not** change this logic.

### 5b — Per-group aggregation (for creative-spend.md)

For **WO** and **Appstack** separately:
1. Filter the union set to ads in that group.
2. Aggregate spend by `creative_id`: `group_spend` = sum of `_spend` for all ads in the group sharing that creative.
3. Take the **top 5 creative_ids by `group_spend`**.
4. For each, the representative ad is the highest-`_spend` ad in that group using that creative. Record its `ad_id` and `ad_name`.

Write `output/analysis/meta-ads/top/creative-spend.md`:

```markdown
# Creative Spend — Yesterday

**Date:** YYYY-MM-DD  
**Window:** Yesterday

---

## WO Campaigns — Top 5 Creatives

| # | Creative ID | Creative Name | Headline | CTA | Spend | CPR | CTR | CPC | Ad Name | Rep Ad ID |
|---|-------------|---------------|----------|-----|-------|-----|-----|-----|---------|-----------|
| 1 | `123456` | Creative name... | Headline text... | LEARN_MORE | $X.XX | $X.XX | X.XX% | $X.XX | Ad name... | `ad_id` |

## Appstack Campaigns — Top 5 Creatives

| # | Creative ID | Creative Name | Headline | CTA | Spend | CPR | CTR | CPC | Ad Name | Rep Ad ID |
|---|-------------|---------------|----------|-----|-------|-----|-----|-----|---------|-----------|
| 1 | `123456` | Creative name... | Headline text... | LEARN_MORE | $X.XX | $X.XX | X.XX% | $X.XX | Ad name... | `ad_id` |
```

Metrics (CTR, CPC, CPR) come from the representative ad (highest `_spend` in the group for that creative). Headline and CTA come from the Phase 4 lookup for that representative ad. Use `—` for CPR when `_cpr` is null, and `—` for Headline/CTA when not available. Truncate Creative Name, Ad Name, and Headline to 50 chars each. If a group has fewer than 5 distinct creatives after resolution, include all of them. If a group has no resolved creatives, write `_No resolved creatives in this group yesterday._` under its heading.

---

## Phase 6: Download Assets (Parallel)

For each of the top 5 creatives, download assets using the representative `ad_id`:

```bash
python3 scripts/meta/get-images.py <rep_ad_id> --out output/analysis/meta-ads/top/<creative_id>
```

**Spawn 5 subagents in parallel** — one per creative. Each subagent runs the command above and reports back what files were saved.

`get-images.py` handles all creative types automatically:
- **Static image** → downloads full-size `.jpg`
- **Video** → downloads `.mp4` source if the token has permission; otherwise saves the largest available poster frame as `-poster.jpg`
- **64×64 thumbnail only** → last-ditch fallback when no full asset is available

For each creative, record its **asset type** based on what was downloaded:
- `.mp4` present → `video`
- `-poster.jpg` present (no `.mp4`) → `video (poster only)`
- `.jpg` only → `image`
- 64×64 thumbnail only → `video — no static frame`

Wait for all subagents to complete before proceeding.

---

## Phase 7: Generate Formula-Extraction Breakdowns (Parallel)

**Spawn 5 subagents in parallel** — one per creative. Each subagent:

1. Reads the asset(s) downloaded to `output/analysis/meta-ads/top/<creative_id>/`
2. Reads `references/generation/formula-extraction.md` for the breakdown framework
3. Produces a breakdown (Copy, Image, Why It Works) and saves it to `output/analysis/meta-ads/top/<creative_id>/breakdown.md`

**Asset-type handling:**
- **Image** → analyse directly
- **Video (poster only)** → analyse the poster frame; note in the breakdown that this is a video creative and the analysis is based on the poster frame
- **Video (MP4)** → analyse the poster frame extracted from the format renditions (the `-poster.jpg` that `get-images.py` would have saved alongside); note in the breakdown that this is a video creative
- **Video — no static frame** → skip the breakdown; write a one-line `breakdown.md` noting `[video — no static frame available]`

Wait for all subagents to complete before proceeding.

---

## Phase 8: Build or Update index.md

Output path: `output/analysis/meta-ads/top/index.md`

Index entries are keyed by **creative_id** (not ad_id), since the same creative can run across multiple campaigns.

### If index.md does NOT exist

Create it fresh:
- All 5 creatives get `streak: 1`
- Insights synthesized from all 5 breakdowns
- Changelog entry: "Initial run"

### If index.md ALREADY exists

1. Read the existing file and extract: all creative_ids listed, their current streak values, and existing insights.
2. Compare with the new top 5 creative_ids:
   - **Present in both** → keep, increment streak by 1
   - **New** → add with streak = 1
   - **In old top 5 but not in new** → retire to `output/analysis/meta-ads/top/history.md`
3. **Retire to history.md**: Append retiring creatives with final spend snapshot, peak streak, and retirement date.
4. **Insights**: Merge — keep non-contradicted existing insights, prefer new observations on conflicts, append new patterns.
5. **Changelog**: Add a new row.

---

## index.md Format

```markdown
# Top Aurie Creatives — Meta Performance

**Last updated:** YYYY-MM-DD HH:MM
**Window:** Yesterday (YYYY-MM-DD)
**Ranked by:** Total spend across campaigns

---

## Top 5 Creatives

### #1 — [Creative Name]

**Creative ID:** `<creative_id>`
**Representative Ad ID:** `<ad_id>`
**Type:** image / video / video (poster only) / video — no static frame
**Campaigns:** WO ($X.XX) · Appstack ($X.XX) · Other ($X.XX)
**Streak:** X runs in top 5

**Headline:** [headline text or —]
**Primary Text:** [primary text or —]
**Description:** [description or —]
**CTA:** [CTA type or —]

| Metric | Value |
|--------|-------|
| Total Spend | $X.XX |
| WO Spend | $X.XX |
| Appstack Spend | $X.XX |
| Other Spend | $X.XX |
| Best CTR | X.XX% |
| Best CPR | $X.XX / — |

> [2–3 sentence visual description]

---

[Repeat for #2–#5]

---

## Insights

### What messaging concepts and hooks are winning
### What emotional angles are winning
### What visuals are winning
### What personas are being targeted

---

## Changelog

| Date | New creatives | Dropped | Notes |
|------|---------------|---------|-------|
| YYYY-MM-DD | [creative_ids or "—"] | [creative_ids or "—"] | Initial run |
```

---

## history.md Format

```markdown
# Top Creatives — History

---

### [Creative Name] — retired YYYY-MM-DD

**Creative ID:** `<creative_id>`
**Peak streak:** X runs in top 5

| Metric | Final value |
|--------|-------------|
| Total Spend | $X.XX |
| Best CPR | $X.XX |

---
```

---

## Output Checklist

Before reporting done, verify:

- [ ] `META_TOKEN_AURIE` confirmed in `.env`
- [ ] `output/analysis/meta-ads/top/` directory exists
- [ ] `campaign-spend.md` written with WO / Appstack / Other sections, top 5 per group
- [ ] Creative IDs resolved for all selected ads
- [ ] `creative-spend.md` written with top 5 creatives per group for WO and Appstack, each with ad name and group spend
- [ ] Images downloaded for all 5 creatives (parallel subagents)
- [ ] Breakdowns generated for all 5 creatives (parallel subagents), saved to `output/analysis/meta-ads/top/<creative_id>/breakdown.md`
- [ ] `index.md` written or updated (keyed by creative_id, streak counts correct)
- [ ] Retiring creatives (if any) appended to `history.md`
- [ ] Insights section updated
- [ ] Changelog row added
- [ ] Output file links printed for user
