---
name: summarize-youtube-to-notion
description: >
  Generate a very detailed, learnings-first summary of one or more YouTube videos and file
  each into two places: the AuraHealth "Growth Readings" Notion database for the full
  narrative, and the `Learnings/Growth Readings` database in the AI Knowledge teamspace for
  the agent-discoverable distilled copy. Fetches the transcript directly (no external API key
  needed), then writes a summary organized by thematic learnings rather than a chronological
  timeline, with direct quotes and concrete examples woven into each learning, plus an
  Aura-specific Takeaways section. Use whenever the user says: "summarize this YouTube video",
  "summarize YouTube", "add this video to the Growth Readings database", "summarize and file
  {url}", "file this video in Notion", or pastes a youtube.com / youtu.be URL and asks for a
  detailed summary saved to Notion. Also trigger when the user shares multiple YouTube URLs
  and wants each summarized and filed.

metadata:
  version: "3.0.4"
knowledge:
  reads:
    - Guide/Index   # baseline routing aid (universal)
    - Tools/Notion                   # Notion REST / MCP access pattern (NOTION_TOKEN)
    - Tools/YouTube                  # YouTube fetch path
  writes:
    - Learnings/Growth Readings   # agent-discoverable distilled copy — full narrative lives in the marketing Growth Readings DB (outside AI Knowledge)
---

# Summarize YouTube to Notion

Given one or more YouTube URLs, this skill fetches the transcript, composes a
learnings-first summary (quotes and examples woven into thematic sections, not
relegated to a separate quotes list), and creates a page for each video in the
AuraHealth "Growth Readings" Notion database.

Arguments: `$ARGUMENTS` (expected to contain one or more YouTube URLs; if empty,
ask the user for URLs).

The transcript fetch needs **no API key** — it uses YouTube's own captions (auto-
generated or creator-uploaded) fetched via the `youtube-transcript-api` Python
library, then Claude writes the summary from the transcript. The Notion write,
however, requires `NOTION_TOKEN` (REST-only — see Setup).

## Setup

### 1. Verify Notion REST access (`NOTION_TOKEN`)

This skill writes to Notion via the **REST API — no MCP**. It requires `NOTION_TOKEN`
(the `Aura AI Knowledge` integration secret) in `~/.claude/settings.json` env. See
the root [CLAUDE.md → "Notion REST integration"](../../../../CLAUDE.md#notion-rest-integration)
for one-time setup.

There is **no MCP fallback** (same posture as `product:record-ideas`). If
`NOTION_TOKEN` is unset the create script exits with a clear message; in a Cowork
sandbox (host env stripped per [PROD-1305](https://linear.app/aura-health/issue/PROD-1305))
paste-export the token for the session.

**The integration must be shared with the target database.** The marketing "Growth
Readings" DB lives **outside** the AI Knowledge teamspace, so the `Aura AI Knowledge`
integration has to be connected to it explicitly: open the **Growth Readings**
database (or its parent) in Notion → top-right `···` → **Connections** →
**Connect to** → `Aura AI Knowledge`. Without this, REST calls 404 with *"Make sure
the relevant pages and databases are shared with your integration."* One-time step
per database.

### 2. Install Python deps

```sh
python3 -c "import youtube_transcript_api, requests" 2>&1 \
  || python3 -m pip install --user --break-system-packages youtube-transcript-api requests
```

`requests` is also used by the Notion REST client ([`scripts/notion_rest.py`](scripts/notion_rest.py)). The transcript fetch needs no API key; the Notion write uses `NOTION_TOKEN` (Setup step 1).

## Workflow

### Step 1: Collect and normalize URLs

Extract all YouTube URLs from `$ARGUMENTS` or the conversation. Match:

- `https://www.youtube.com/watch?v=<ID>`
- `https://youtu.be/<ID>`
- `https://www.youtube.com/shorts/<ID>`
- `https://m.youtube.com/watch?v=<ID>`

Normalize each to `https://www.youtube.com/watch?v=<ID>` (strip `&t=`, `&si=`,
`&list=`, `&pp=`).

If the user provided no URL, ask for one and stop.

### Step 2: Discover the target database schema

The create script ([`scripts/file_to_notion.py`](scripts/file_to_notion.py)) discovers
the schema at runtime via REST (`GET /data_sources/<id>`), so property names are
never hardcoded — they may drift. You don't need a separate fetch here; this step
is informational. As of 2026-04-24 the database is "Growth Readings" with:

| Property | Type | Role |
|---|---|---|
| `Title` | title | Video title |
| `Source` | text | YouTube URL (plain text) |
| `Date Published` | date | Today's date (or video publish date) |
| `Reviewed` | select | Leave empty — human review flag |

Data source id as of that date: `2aa75e23-af20-8020-8ec4-000bd69ced6d` (the script
falls back to resolving the current id from the database id if it ever drifts).

The script maps canonical fields (Title / Source / Date) to whatever properties
actually exist; if Source or Date is missing it degrades gracefully and warns (see
`references/notion-schema.md`).

### Step 3: Fetch transcript + metadata per URL

Run the fetch script for each URL:

```sh
python3 scripts/fetch_transcript.py "<youtube_url>"
```

The script prints a JSON object to stdout:

```json
{
  "url": "https://www.youtube.com/watch?v=...",
  "video_id": "...",
  "title": "Video title (from YouTube oEmbed)",
  "author": "Channel name",
  "thumbnail": "https://i.ytimg.com/vi/.../hqdefault.jpg",
  "duration_seconds": null,
  "transcript": [
    {"timestamp": "00:01:23", "seconds": 83.0, "text": "..."}
  ],
  "transcript_language": "en",
  "transcript_is_auto_generated": true,
  "errors": []
}
```

If `errors` contains `no_transcript_available`, the video has captions disabled.
Fall back to summarizing just title + author + any description the user pastes;
flag the page as "short summary — transcript unavailable".

See `references/transcript-api.md` for details on the script and how to handle
edge cases.

### Step 4: Compose the learnings-first summary

Read the transcript end-to-end, then write a summary organized by **thematic
learnings, not chronology**. Quotes and concrete examples should be woven into
each learning — not collected into a separate "Notable Quotes" section.

Target length: **1500–2500 words** for a standard 30–60 minute long-form video
or podcast. Shorter videos get shorter summaries. Don't pad. Density of insight
per word is what matters.

**Required sections (in this order):**

1. **Header blockquote** — a single blockquote at the top with Channel, Host,
   Guest (if applicable), Duration, Transcript status. One blockquote, one line
   per field.

2. **Overview** — 3–5 sentence setup: what the video is, who's in it, and why
   it's worth reading. State the *kind* of value it delivers (strategy
   learnings, technical deep-dive, founder story, etc.) so the reader can
   decide whether to keep going.

3. **Detailed Learnings** — the meat. **5–10 thematic `### N. <crisp insight>`
   subsections**, each:
   - **Headline-style title** that states the insight directly
     (e.g., `### 3. Growth came from technical step-functions, not marketing polish`)
   - **2–4 paragraphs** of narrative that explain the insight using concrete
     examples, numbers, and context from the episode
   - **At least one direct quote** embedded in the narrative as a blockquote,
     attributed with speaker name and timestamp: `— Connor Zwick [15:28]`
   - **A closing bolded implication or "so what"** — `**For AI-native product
     leaders:** the biggest retention wins may be waiting behind the next model
     capability, not the next UX tweak.`
   - Cluster by theme, not chronology. Merge related points across the video.

4. **Optional narrative sections** — include when the video warrants. Skip
   otherwise.
   - **The Founding Story / Origin** — when the video spends meaningful time
     on how a person or company got started, and that narrative carries
     learnings beyond what the thematic section captures. Tell it as a
     compressed 3–5 paragraph story with at least one quote.
   - **The Lightning Round** — when the video has a rapid-fire Q&A segment
     that the host calls out. Parse their answers into a few short
     bolded-question / paragraph-answer pairs. Keep their quotes.
   - **Closing Thoughts** — when the final answer or two carry a strong
     philosophical or forward-looking take worth preserving verbatim.

5. **Takeaways & Action Items for Aura** — always include. 4–8 bullets with
   **bolded lead-ins**, each tying a learning from the video to a concrete
   decision, test, or investigation relevant to Aura (Aurie, journaling,
   sleep coaching, growth, org design, AI product strategy). This section is
   what justifies the page existing in the Growth Readings database.

6. **Further Watching** — 2–5 bullets. Only items the video *explicitly*
   references: other podcasts, papers, people, concepts. Always include
   timestamps so the reader can jump to the reference. Skip this section if
   the video doesn't reference anything.

7. **Footer callout** — a single blockquote footer: `🤖 Generated by
   summarize-youtube-to-notion on YYYY-MM-DD from the <auto-generated |
   creator-uploaded> English transcript (<N> segments, ~<W> words).` Don't worry about
   the version segment — `file_to_notion.py` deterministically stamps `:<version>` (from
   `metadata.version`) onto the `Generated by summarize-youtube-to-notion` token at write
   time, so the footer always names the exact revision regardless of what you type here.
   Any revision notes can be appended on the same line.

**Writing style:**

- Use full prose paragraphs for the learnings sections — not bullet dumps.
  Bullets belong only in the Aura Takeaways and Further Watching sections.
- **Cut ruthlessly.** Skip sponsor reads, "welcome to the show" intros,
  pleasantries, the host repeating the guest's point back to them, and
  low-density biographical details. Keep whatever contributes to a learning
  or a memorable story.
- Preserve specific numbers, statistics, and claims exactly as stated in the
  transcript. If a stat is unclear or possibly a caption error, qualify it
  ("roughly 1% of GDP") rather than stating it flat.
- **Never invent quotes or timestamps.** If a quote is close but uncertain,
  paraphrase without quotes. Timestamps come from the transcript JSON.
- Silently correct obvious auto-caption errors (homophones, dropped
  punctuation) in your own prose, but **leave quoted text as-is** — quotes
  are verbatim.
- The `### N.` learning titles should be **declarative**, stating the insight
  the reader takes away. Not topic labels. Bad: "On Probabilistic Models".
  Good: "The hardest engineering problem is shipping on probabilistic models
  with no human in the loop."

### Step 5: Create the Notion page (REST)

Write the composed summary (Step 4) to a temp file, then run the create script:

```sh
python3 scripts/file_to_notion.py \
  --markdown-file <path-to-summary.md> \
  --title "<video title>" \
  --source-url "<canonical YouTube URL>" \
  --date <YYYY-MM-DD> \
  --icon <emoji>
```

Pick the icon by content: `🎧` (podcast), `🧠` (educational), `📺` (general video),
`🎙️` (interview), `📚` (book talk).

The script:

- discovers the data source + property schema via REST (Step 2) and maps Title /
  Source / Date to the live property names, degrading gracefully if one is missing;
- converts the Markdown body to Notion blocks via [`scripts/notion_blocks.py`](scripts/notion_blocks.py)
  — it supports the bounded construct set this skill emits: `#`/`##`/`###` headings,
  paragraphs, `-` bullets, `>` quotes, `---` dividers, and inline **bold** / *italic* /
  `` `code` `` / `[links](url)`;
- creates the page via `POST /pages` (parent = data source id), keying the title by
  the literal `title` id per the 2025-09-03 API, and sets the icon + Source/Date;
- appends body blocks beyond Notion's 100-per-request cap automatically;
- prints the new page URL on success (capture it — Step 6 needs it as a cross-link).

Pass `--dry-run` to print the converted block payload **without writing** — use it to
sanity-check the body before a real create.

**Body the summary file must contain** (Step 4 output, in this block order). Do
**not** put the video title in the body — it comes from the title property:

- `> **Channel:** … ` header blockquote with fields on separate lines
- `## Overview` + paragraph
- `## The Founding Story` (or similar) + paragraphs (optional)
- `## Detailed Learnings` + `### N. <insight>` subsections, each with paragraphs,
  embedded `>` blockquotes attributed with `— Speaker [MM:SS]`, and a bolded closing
  implication
- `## The Lightning Round` (optional)
- `## Closing Thoughts` (optional)
- `## Takeaways & Action Items for Aura` + `-` bulleted list with bolded lead-ins
- `## Further Watching` + `-` bulleted list (only if populated)
- `---` divider
- `> 🤖 Generated by summarize-youtube-to-notion on YYYY-MM-DD from the <transcript
  type> transcript (<N> segments, ~<W> words).` (`file_to_notion.py` stamps `:<version>`
  onto the skill-name token at write time.)

See `references/notion-schema.md` for the REST call shape and
`references/summary-structure.md` for a worked example of a full page.

### Step 6: Mirror the distilled learnings into AI Knowledge

Invoke [`shared:record-knowledge`](../../../shared/skills/record-knowledge/SKILL.md)
to write a second, agent-discoverable row into `Learnings/Growth Readings`
inside the AI Knowledge teamspace. Full narrative stays in the marketing
Growth Readings DB (Step 5); this step mirrors just the parts an agent
needs to answer "what have we learned about X from growth content?" without
leaving the AI Knowledge teamspace.

Schema per
[`record-knowledge/references/schemas/growth-readings.md`](../../../shared/skills/record-knowledge/references/schemas/growth-readings.md).

**Inputs to `record-knowledge`:**

- `path` → `Learnings/Growth Readings`
- `fields` object:
  - `title` — video title (same as Step 5 `Title` property)
  - `overview` — the verbatim text of the `## Overview` section from the
    summary you composed in Step 4. Extract it by splitting on markdown
    headings, not by re-summarising.
  - `source_url` — canonical YouTube URL
  - `growth_readings_page` — the URL returned by Step 5's Notion create
  - `date_published` — video publish date (`YYYY-MM-DD`)
  - `detailed_learnings` — the verbatim contents of the `## Detailed
    Learnings` section. Preserve the `### N. <insight>` subsections,
    embedded blockquotes, and `**For Aura:**` closing implications.
- `sources` (required by the base schema):
  - primary: `{ label: "<video title>", url: "<YouTube URL>", primary: true }`
  - secondary: `{ label: "Growth Readings — <video title>", url: "<Step 5 URL>" }`

`record-knowledge` handles schema validation and the `# Sources` block
in the page body before any Notion write. It will refuse if the payload
is malformed (missing required fields, empty `detailed_learnings`,
invalid URLs).

Capture the returned row URL for Step 7.

### Step 7: Report back

Per URL, surface **both** writes:

- ✅ / ❌ for Step 5 (Growth Readings marketing DB) with its Notion URL
- ✅ / ❌ for Step 6 (`Learnings/Growth Readings` in AI Knowledge) with its Notion URL
- Any warnings (e.g., "transcript auto-generated — quotes may have minor errors")

**Partial-state behaviour.** Both writes are attempted unconditionally —
if one fails, the other still runs. No rollback, no automatic retry.
Both successes, a single success, or both failures are all valid end
states; surface whichever happened and let the user decide whether to
re-run. This is safe because both writes are additive (no destructive
ops) and any partial state is recoverable by re-running the skill.

Example:

> Filed 3 of 3 videos:
> - ✅ [The State of LLM Agents — Andrej Karpathy](https://www.notion.so/aurahealth/...)
>   — Growth Readings ✅ · AI Knowledge ✅
> - ⚠️ [How We Built Claude — Anthropic](https://www.notion.so/aurahealth/...)
>   — Growth Readings ✅ · AI Knowledge ❌ (schema validation: `overview`
>   under 100 chars). Re-run or file manually.
> - ⚠️ [Creator live stream](https://www.notion.so/aurahealth/...) — no
>   transcript available, summary based on title + author only;
>   AI Knowledge write skipped (no `detailed_learnings` to mirror)

## Error handling

| Condition | Action |
|-----------|--------|
| Invalid / unparseable URL | Skip and report "could not extract video id" |
| Private or age-restricted video | oEmbed returns 401 → report "Private/restricted — cannot access" |
| Captions disabled on the video | Summarize from title + author only, flag the page |
| Auto-translated captions only | Use them; note the language in the "Notes" section if material |
| YouTube rate limiting | Wait 30s and retry once |
| `NOTION_TOKEN` unset | Script exits with code 2 and a setup message. REST-only — no MCP fallback. Add the token via `update-config` (or paste-export in Cowork) and retry. |
| Notion 404 `object_not_found` on the DB | The `Aura AI Knowledge` integration isn't shared with the Growth Readings DB. Connect it (Setup step 1: DB → `···` → Connections → `Aura AI Knowledge`) and retry. |
| Notion property name mismatch | Script degrades gracefully (maps by preference, then by type) and warns on stderr which property was skipped. |
| Multiple URLs, one fails | Process the rest and report per-URL status |
| Step 5 succeeds, Step 6 fails | Surface both; do not retry; user can re-run manually. `record-knowledge` failures usually mean schema validation — the error message names the field. |
| Step 5 fails, Step 6 still runs | `record-knowledge` call gets `growth_readings_page: null` in `fields` (the Step 5 URL is missing). Schema allows this since the field is optional. |
| No `## Detailed Learnings` section in the summary | Skip Step 6 for that URL (e.g. title-only summaries with no transcript). Record-knowledge rejects empty `detailed_learnings`; surface the skip in Step 7. |

## Reference files

- `references/summary-structure.md` — worked template + stylistic rules; consult before writing the summary
- `references/transcript-api.md` — youtube-transcript-api usage, edge cases, fallbacks
- `references/notion-schema.md` — target database id, expected properties, REST call shape
- `scripts/fetch_transcript.py` — the transcript fetch script
- `scripts/file_to_notion.py` — REST create driver (schema discovery → blocks → `POST /pages`)
- `scripts/notion_blocks.py` — Markdown → Notion-block converter (skill-local; bounded construct set)
- `scripts/notion_rest.py` — Notion REST client (byte-identical copy of the canonical helper)

## Canonical example

The Connor Zwick / Speak page at
<https://www.notion.so/34c75e23af208142a9e0d7edb3ead3ca> is the reference
output for this skill. When calibrating structure, length, or density, open
that page and match its style.
