# Schema — `Learnings/Growth Readings`

**Status:** stable. Derived from the DB created under [ENG-398](https://linear.app/aura-health/issue/ENG-398). The companion skill [`marketing:summarize-youtube-to-notion`](https://github.com/AuraHealth/AINative/tree/main/plugins/marketing/skills/summarize-youtube-to-notion) is the primary agent producer (dual-writes after [ENG-399](https://linear.app/aura-health/issue/ENG-399)).

Rows are write-once. Each row captures the distilled learnings from one growth-related video, podcast, or article. The full narrative summary lives in the [marketing Growth Readings database](https://www.notion.so/2aa75e23af20801ebfbcf40645cb7469); this DB mirrors just the parts an agent needs.

**Key property of this schema: rows are self-contained.** `overview` and `detailed_learnings` are both on the row — one as a Notion property, one as the page body. A caller fetching this row does NOT need to follow the `growth_readings_page` cross-link to see the learnings.

## Fields

All [base-schema](../base-schema.md) fields apply, with one adaptation: `sources` is represented in the page body (`# Sources` section) rather than a separate property. In addition:

| Field | Type | Required | Notes |
|---|---|---|---|
| `title` | string | yes | Video / podcast / article title |
| `overview` | string | yes | 3–5 sentences. Stored as the **Overview** Notion property AND repeated as the opening `## Overview` section of the page body. |
| `detailed_learnings` | markdown | yes | 5–10 `### N. <insight>` subsections with quotes and `**For Aura:**` implications. Goes in the **page body** under a `## Detailed Learnings` heading. Not stored as a property (Notion text properties cap at 2000 chars and don't render markdown). |
| `source_url` | URL | yes | Canonical YouTube / podcast / article URL. Also included as the primary `sources` entry in the page body. |
| `growth_readings_page` | URL | no | Cross-link to the full narrative summary in the marketing Growth Readings DB. Also included as a second `sources` entry in the page body when present. |
| `date_published` | ISO date | no | When the source was originally published. |

**Dropped on purpose:**
- `vertical` — most growth content doesn't map cleanly to an Aurie vertical. Forcing the field adds noise.

## Notion layout

- **Properties** set on the row:
  - `Title` (title)
  - `Overview` (rich text — the 3–5 sentence summary)
  - `Source URL` (url)
  - `Growth Readings Page` (url)
  - `Date Published` (date)
- **Page body** (Markdown):
  ```markdown
  ## Overview
  <overview, verbatim — same content as the Overview property>

  ## Detailed Learnings
  ### 1. <declarative insight>
  <2–4 paragraphs, embedded blockquote, **For Aura:** implication>

  ### 2. …
  ### 3. …  (5–10 total)

  # Sources
  - [<Growth Readings page title>](<growth_readings_page>) (parent) — full narrative summary in the marketing Growth Readings database
  - [<Video / podcast / article title>](<source_url>) — source
  ```

## Example

```yaml
title: "Head of Growth (Anthropic): \"Claude is growing itself at this point\""
overview: "Mike Krieger-era conversation with Anthropic's head of growth on how the Claude team is structured and how AI tools are changing growth work itself. The team runs a mixed model — horizontals (growth platform, monetization) plus audience-focused pods (B2B, Claude Code, knowledge workers, API growth) — and uses Claude internally to accelerate research, copy iteration, and pattern-spotting. Useful reference for how a fast-growing AI company organizes growth without splitting between product and marketing."
source_url: "https://www.youtube.com/watch?v=V5HnuK7N_qc"
growth_readings_page: "https://www.notion.so/33b75e23af208011b35fc1838db59bfc"
date_published: "2025-10-21"
detailed_learnings: |
  ### 1. Horizontals + audience pods beats a pure functional split
  …
  **For Aura:** when we think about organizing Aurie growth vs. Aura growth, …

  ### 2. Claude writing its own growth assets compresses the creative loop
  …
```

See the seed row at [Head of Growth (Anthropic)](https://www.notion.so/34c75e23af208107b224d6b1cc0c8674) for the full rendered shape.

## Validation rules

1. `title`, `overview`, `source_url`, `detailed_learnings` all present.
2. `source_url` is a valid URL (http/https).
3. `growth_readings_page` (if present) points at the marketing Growth Readings database under `2aa75e23af20801ebfbcf40645cb7469`.
4. `overview` is between 100 and 2000 characters (roughly 3–5 sentences; hard cap matches Notion property limit).
5. `detailed_learnings` has at least 3 `### ` subsections (5–10 recommended per the source template).
6. `date_published` (if present) is a valid ISO date and not in the future.
7. Page body ends with a `# Sources` section containing at least the source URL. Per [base-schema § sources](../base-schema.md), multiple sources with no primary entry emit a validation warning.

## Idempotency

No hard dedup at write time. Callers are responsible for avoiding duplicate rows; the convention is to check for an existing row by `source_url` before writing. Accidental duplicates surface as `healthcheck-knowledge` findings rather than being blocked here.
