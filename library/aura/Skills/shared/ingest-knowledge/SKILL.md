---
name: ingest-knowledge
description: >
  Adds or updates the AI Knowledge Notion teamspace from any source — Notion URL, PDF,
  web page, Slack/Linear thread, current conversation, or pasted text. Re-learns the
  current taxonomy each run, decides new-page vs update-existing itself, and always
  preserves the source via a # Sources section. Trigger when the user says "ingest into
  AI Knowledge", "add to AI Knowledge", "update AI Knowledge from this", "digest this
  for agents", "sync this doc to the knowledge layer", or provides a source and asks
  for it to be made available to agents. Callers that run unattended (scheduled routines)
  pass `unattended: true` to skip the approval wait.
metadata:
  version: "3.4.0"
knowledge:
  # Cross-cutting: ingest decides placement at runtime from the live taxonomy.
  reads:
    - Compliance/Privacy     # PII redaction rules — strip full email / givenName / familyName before writing into AI Knowledge
    - Guide/Index            # baseline routing aid (universal)
    - Guide/Placement Rules  # explicit so it's pre-fetched even with the wildcard
    - Tools/Notion           # explicit — REST + MCP access patterns this skill leans on
    - "*"                    # cross-cutting: reads anywhere in the teamspace
    - Tools/Slack                    # Slack post/read conventions + footer format
    - Tools/Linear                   # Linear MCP / GraphQL access
  writes: ["*"]
---

# Ingest Knowledge

Takes any durable knowledge source and routes it into the AI Knowledge teamspace — the canonical L4 layer for both humans and AI agents. The skill decides placement itself based on the current taxonomy, so the user only provides the source.

**Core contract:**
- Source material is **never modified** — humans own source docs; AI Knowledge is the digested layer.
- Every new or edited page ends with a `# Sources` section linking back to the canonical human-authored source(s).
- The skill **always presents a plan** before writing and waits for user approval — except in [unattended mode](#unattended-mode), where it applies only the operations that need no human judgment and holds the rest.
- Taxonomy is **re-learned every run** — the skill follows the About router each time, not hardcoded IDs.
- **PII is stripped before any write.** Full emails, given names, family names, phone numbers, journal/free-text user content, and payment identifiers must be redacted out of the digested page per `Compliance/Privacy`. Source material may contain PII (a Slack thread quoting a user, a PDF with email addresses); the ingested AI Knowledge page must not. When in doubt, paraphrase rather than quote.

---

## AI Knowledge — what it is (context the skill needs)

The teamspace is organized into seven knowledge types (defined on the Guide page):

| Type | Question it answers | Lives under |
|---|---|---|
| **Identity** | Who are we and who do we serve? | Identity/ (Company, Brand, Audience) |
| **Products** | What do we ship? | Products/ (one subtree per app — Screens, Features, Subscription, Technology) |
| **Domain** | What external world do we operate in? | Domain/ (Competitors, Market, Glossary) |
| **Procedural** | How do we do things? | Procedures/ (narrative; executable logic lives in repos) |
| **Learnings** | What have we learned from a specific decision? | Learnings/ (Experiments, Ad Campaigns, databases) — write-once |
| **Signals** | What are we observing in production over time? | Signals/ (Aurie Conversations, Conversation Patterns, and future NPS / error-rate / funnel snapshots) — periodic |
| **Ephemeral** | What's happening right now? | **Not here** — lives in Linear, Slack, session context |

**Learnings vs Signals.** Both are durable, but a Learning is distilled once from a decision (experiment closed, campaign retro) and referenced forever; a Signal is a periodic snapshot of production state (Aurie conversations this week, NPS this month) with full history preserved. If the source reads like a recurring export of the same shape, it's a Signal.

House style for pages:
- `**At a glance:**` opener (one-paragraph summary)
- Terse bullets, link-heavy, no narrative bloat
- `# Related` section with parent + cross-links
- `# Sources` section at the bottom
- No duplication of executable logic (lint configs, code snippets, skill bodies stay in repos)

---

## Hardcoded values

Only one ID is hardcoded. Everything else is discovered at runtime.

```
AI_KNOWLEDGE_ABOUT_PAGE = "9ee75e23af20830aa49101a40466a789"
```

This is the About (router) page. From it, the skill follows links to discover current sections. If this page is ever renamed or moved, update this constant.

---

## Notion access — REST-first by necessity

The repo default is [MCP-first](../../../../CLAUDE.md#tokens-are-soft-requirements). This
skill deviates because every common ingestion path hits one of MCP's structural limits:
table-heavy source reads truncate silently via `notion-fetch`; placement decisions need
to walk arbitrary subtrees (no MCP equivalent for recursive `get_block_children`); and
the rich-text in surgical section updates needs `update_block`, not `notion-update-page`.
Every Notion read and write below is **REST-first when `NOTION_TOKEN` is set**, MCP
fallback otherwise — see [CLAUDE.md → "Notion REST integration"](../../../../CLAUDE.md).
The REST path uses [`scripts/notion_rest.py`](scripts/notion_rest.py) (this skill ships
its own copy per the self-contained-skills rule). It is preferred because:

- Reads (`get_page`, recursive `get_block_children`) return structured blocks, paginate cleanly, and don't truncate the way `notion-fetch` can on table-heavy pages.
- Page creation uses `create_page(parent, title, blocks)` with the `block_*` builders (`block_heading`, `block_paragraph`, `block_bullet`, `block_code`, `block_callout`, `block_table`, `block_divider`) + the `rich_text` parser for `**bold**` / `[label](url)` — no hand-assembled rich_text arrays.
- Updates fetch the page's blocks, find the target heading, and PATCH the right block via `update_block` / `append_block_children` — surgical and idempotent.

```python
from notion_rest import (
    notion, MCPFallback,
    block_heading, block_paragraph, block_bullet, block_table,
    block_code, block_callout, block_divider, rich_text,
)

if notion.available:
    blocks = [block_callout("At a glance: …"), block_heading("Section", 2), …]
    page = notion.create_page(parent_page_id, "New Page Title", blocks)
else:
    raise MCPFallback("use the MCP path noted inline in this step")
```

The MCP path (`notion-fetch`, `notion-create-pages`, `notion-update-page`, `notion-search`) is the fallback when running inside **Cowork** (host env stripped, PROD-1305) or on machines without the integration token. Both paths produce identical writes. Non-Notion sources (Slack, Linear, web, PDF) always use their own tools regardless.

---

## Step 1: Normalize the input

The skill accepts one or more sources, provided as skill arguments or collected from the conversation. Detect the type from the argument shape:

| Input shape | Type | How to read it |
|---|---|---|
| `https://notion.so/...` or `https://*.notion.site/...` | Notion | REST `notion.get_page` + `get_block_children(..., recursive=True, max_depth=3)`; MCP `notion-fetch` fallback |
| Path ending in `.pdf` | PDF | `Read` tool — use `pages` param for PDFs over 10 pages |
| Path ending in `.md` / `.txt` | Local doc | `Read` tool |
| `https://*.slack.com/archives/...` | Slack thread | invoke [`shared:read-slack`](../read-slack/SKILL.md) (its `replies` mode); that skill owns the connection (MCP-first, `SLACK_BOT_TOKEN` script fallback) — graceful degrade if unavailable |
| `https://linear.app/.../issue/...` | Linear issue | `get_issue` + `list_comments` |
| Any other `https://...` | Web | `WebFetch` |
| `--from conversation` | Session | Scan the current conversation for the relevant thread |
| Raw pasted text | Inline | Treat as markdown |

Build a uniform internal representation: `{ source_ref, source_type, source_title, fetched_at, chunks[] }`, where each chunk has `{ title, body, attribution? }`.

**If a source's reader is unavailable**, tell the user how to proceed and stop. Slack reads are delegated to [`shared:read-slack`](../read-slack/SKILL.md), which is MCP-first with a `SLACK_BOT_TOKEN` script fallback — so only stop for a Slack source when *neither* the Slack connector nor a bot token is available (don't fail a session that has the token but no connector). For other MCP-backed sources (e.g., Notion when `NOTION_TOKEN` is also unset), stop with: *"Install the relevant MCP or paste the content inline."*

**Ephemeral content check:** if the source is clearly a sprint update, weekly status, live incident, or other time-bound state, refuse the ingest and redirect: *"This reads as ephemeral. Keep it in Linear/Slack, not Notion — the Guide's knowledge-types rule."*

---

## Step 2: Learn the current taxonomy + authoring rules

**Always do this fresh every run. Do not rely on cached structure or rules.**

Read each page below REST-first (`notion.get_block_children(<page-id>)` for the body, `get_page` for properties); fall back to `notion-fetch(<page-id>)` when `NOTION_TOKEN` is unavailable (Cowork / no token).

1. **About page** (`AI_KNOWLEDGE_ABOUT_PAGE`) — resolve the top-level sections from **the links in About's body**, not from its child pages. See [references/about-sections.md](references/about-sections.md) — read it before implementing this step.
2. **Guide page** — read the **current** knowledge types table and authoring rules (`# Sources` discipline, voice-playbook, ephemeral rule, "AI scope of edits"). Honour whatever the Guide currently says — the type list, the ephemeral redirect message, and the authoring requirements all come from here, not from this SKILL.md. New types, renamed types, or updated rules are picked up automatically on the next run.
3. **Guide/Placement Rules page** — read the placement policy. **The decisions in Step 3 below MUST honour this page** (which section a chunk belongs in, when to file under an existing subsection vs propose a new one, the prohibition on root-level pages). If Placement Rules disagrees with the heuristics in Step 3, Placement Rules wins.
4. For each section resolved from About, read its children in parallel to enumerate direct children.

Build a map: `{ section_name → { page_id, children: [{ title, page_id }] } }` plus the classification rules pulled from the Guide.

Note any data sources (databases) by their `collection://` URLs — the skill may need to
append rows. **Databases are not only under Learnings.** `Products › Aurie › Screens` is a
database with one row per screen, and a section whose content lives in rows is one an ingest
must never answer with a child page: the page would shadow the rows, look correct, and be
invisible to every query that reads the database.

When a section holds a database, route content into it as a **row**, or hand it to the skill
that owns that database and stop. `Products › Aurie › Screens` is owned by
`data:sync-screens`, which projects it from the app repo — the repo is the system of record
there, so a row written by hand is overwritten on the next sync.

### Resolving About's sections — body links, not child pages

**The sections are inline links in About's body rich_text, not child pages of About.** A `child_page` walk returns exactly one result (`Test Page - Delete`) and zero sections, handing every downstream step an empty taxonomy while looking like it succeeded.

Full recipe, the verified section list, and the hard-error rule: **[references/about-sections.md](references/about-sections.md)**. Read it before implementing Step 2 — an empty section map aborts the run rather than continuing.
---

## Step 3: Classify + match each chunk

For each chunk from Step 1:

### Placement preconditions (from `Guide/Placement Rules`)

Before applying the heuristics below, enforce these hard rules:

- **No root pages.** Every new page MUST land under one of the top-level sections resolved from About in Step 2 — today: Compliance, Domain, Guide, Identity, Learnings, Procedures, Products, Signals, Team, Tools. Use the resolved set rather than this list, which is only its current value; About itself is the router page, not a section, so it is never a valid parent. Never create a page with the workspace or teamspace as its parent.
- **Pick the most specific existing subsection.** Don't drop a page at a section root if a more specific home (existing sub-page or database) exists.
- **Propose a new subsection rather than filing at section root.** If nothing fits, create a subsection page (with the standard `At a glance:` / `What lives here vs elsewhere` / `# Sources` skeleton) and add a one-line entry to `Guide/Index` in the same plan.

If a chunk would otherwise land at the root or at a section root, surface it in the plan's *Open questions* and ask before writing.

### Classify
Pick the knowledge type using these rules:

- **Voice / claims / ethics / CTA rules / banned words** → Identity › Brand
- **App-specific positioning, taglines, features, differentiators** → Products › <app>
- **Screens, screenshots, subscription SKUs, pricing, per-app technology** → Products › <app> › <Screens|Subscription|Technology>
- **ICPs, audience segments, emotional verticals, who-we-serve language** → Identity › Audience
- **Company info, team, mission** → Identity › Company
- **Competitors, market landscape, industry glossary** → Domain
- **Research results, experiment outcomes, ad campaign retros** → Learnings
- **Periodic observational exports (Aurie conversation snapshots, NPS snapshots, error-rate dumps, funnel stats)** → Signals › <source> (create a new snapshot page dated `YYYY-MM-DD`; never overwrite prior snapshots)
- **How-we-do-things narrative (not code)** → Procedures

If a chunk straddles types, prefer the more specific home + cross-link from the other.

### Match against existing pages
For the chosen section, scan the `children` map (from Step 2) for titles with strong overlap. Where overlap is ambiguous, search:

- **REST (preferred):** `notion.search("<chunk title or key terms>")` — note the REST `search` endpoint is workspace-wide and does **not** accept page/teamspace scoping, so post-filter results client-side to the chosen section's subtree (match against the `children` map or the section page-id in each hit's ancestry).
- **MCP fallback:** `notion-search(query: "<chunk title or key terms>", page_url: "<section-page-id>", page_size: 5)` — accepts `page_url` scoping directly.

Three outcomes per chunk:
- **Strong match on an existing page** → propose **update** (targeted section add/replace)
- **No strong match, but fits cleanly in a section** → propose **create** (new child page)
- **Research/experiment-shaped content** → propose **append to Learnings DB row** (if a schema fits), else a standalone page under Learnings
- **Periodic observational export (Aurie conversation snapshot, NPS dump, error snapshot, etc.)** → propose **create snapshot under Signals › <source>** titled `YYYY-MM-DD snapshot`, and update the parent's rolling summary + snapshots list

---

## Step 4: Present the plan (always)

Before any write, output a plan like this:

```
## Source inventory
- <source_title> (<source_type>, fetched <date>) — N chunks

## Target mapping
| Chunk | Classification | Target | Operation |
|---|---|---|---|
| Voice rules | Identity › Brand | Brand / Marketing Messaging Playbook (new) | Create |
| Hook examples | Identity › Brand | Brand / Messaging Hierarchy (existing) | Update § "Tier 1" |
| Competitor benchmark | Domain › Competitors | Competitors / Aurie vs Ash vs ChatGPT (new) | Create |
...

## Changes to existing pages (diff preview)
- **Audience** — adding new section "Why users love Aurie" before # Related; appending to existing # Sources

## Open questions
- <flag cross-app vs app-specific ambiguities, schema uncertainties, placement ambiguities>
```

Wait for user approval. If the user redirects placement for any chunk, re-plan that chunk and re-present.

### Unattended mode

A caller with nobody at the keyboard — a scheduled routine such as
`shared:distill-sessions-daily` — passes `unattended: true` (and usually the target page it
already resolved). Waiting for approval would hang that run, so the skill does not wait.
It still builds the plan above and prints it as the record of the run, then splits it:

| Applied without approval | Held — reported, never guessed |
|---|---|
| A new page inside an existing **subsection** | Replacing or rewording existing prose |
| A new section appended to an existing page | A conflict with existing content |
| Appending to a page's `# Sources` | A new page directly under a top-level section (a section-root page), a new subsection, or a new Learnings DB schema |
| A row in an existing Learnings / Signals DB whose schema fits | Any chunk with an open question, or an unclear classification |
| | Anything past the first stage of a staged ingest |

Held chunks are returned to the caller under **Held for review**, each with its proposed
target and the reason it was held, so the caller can log them. Every other rule still
applies in full — PII redaction, `# Sources`, the ephemeral refusal, re-learning the
taxonomy. When a caller passes a target page, write there and don't re-derive placement;
if that page doesn't exist or the chunk doesn't fit it, hold the chunk.

Unattended mode errs toward holding: when unsure whether an operation is additive, hold it.
Only a caller that runs without a person should pass it — an interactive session asks.

---

## Step 5: Execute

Create pages and edit pages **in parallel** where independent. REST-first (`notion_rest.py`), MCP fallback — see [Notion access](#notion-access--rest-first-mcp-fallback). Enforce:

**For new pages (REST):** build the body with the `block_*` builders and create in one call:
```python
from notion_rest import notion, block_callout, block_heading, block_paragraph, block_bullet, block_table

blocks = [
    block_callout("At a glance: <one paragraph>", "💡"),
    block_heading("<section>", 2),
    block_paragraph("<text with **bold** and [links](url)>"),
    block_bullet("<bullet>"),
    # block_table([...headers], [[...row], ...]),
    block_heading("Related", 2),
    block_bullet("[<parent>](<url>) — parent"),
    block_heading("Sources", 2),
    block_bullet("[<source title>](<source url>) — <source type description>"),
]
page = notion.create_page("<section-or-subpage-id>", "<title>", blocks)
```

**`Tools/` pages — set a company icon.** A tool's own page directly under the **Tools/** section must carry the tool's brand icon (its favicon) as its page icon, matching every existing Tools page (OpenAI, Notion, GCP, GitHub, Braintrust, …) — don't leave it null, and never use a generic emoji placeholder (a real brand icon, not 🐛/💳). `create_page` doesn't set an icon, so PATCH it right after creation (`domain` is the tool's site, e.g. `spinnaker.io`):
```python
import json
icon = {"type": "external", "external": {"url": f"https://www.google.com/s2/favicons?domain={domain}&sz=256"}}
notion._request("PATCH", f"/pages/{page['id']}", data=json.dumps({"icon": icon}))
```
Where a site's favicon is too low-res, point the icon at a direct brand PNG instead of the favicon service (e.g. Cartesia uses its `apple-touch-icon` PNG). This is specific to `Tools/` (each maps to an external system with a domain); other sections don't use icons. A subpage of a tool page (e.g. `Tools/Mixpanel/Data Design` 🧱, `Tools/Mixpanel/Event Approval` ✅) takes a fitting emoji instead, since the brand icon already marks its parent.

**Pages with an inline database** (a register, log or inventory whose entries share fields): `create_page` cannot create a database, so the page is built in three writes and the Placement Rules bookkeeping is done by hand. Follow [references/inline-database.md](references/inline-database.md).

**MCP fallback for new pages:**
```
notion-create-pages(
  parent: { type: "page_id", page_id: "<section-or-subpage-id>" },
  pages: [{ properties: { title: "..." }, content: "..." }]
)
```

Page body template:
```
**At a glance:** <one paragraph>.

<body sections>

# Related
- [<parent>](<url>) — parent
- <cross-links to related pages in other sections>

# Sources
- [<source title>](<source url>) — <source type description>
```

**For updates (REST):** fetch the page's blocks, locate the target section, and patch surgically:
```python
from notion_rest import notion, block_heading, block_paragraph, block_bullet

blocks = notion.get_block_children("<target>")          # current body
# find the heading block for the section being changed, then:
notion.update_block("<block-id>", {"paragraph": {"rich_text": [...]}})   # replace one block's text
```
Be surgical — only touch the section being added/updated; leave the rest of the body intact.

**A new section goes above the page's `# Sources`, through [`scripts/insert_section.py`](scripts/insert_section.py).**
Never add one with `append_block_children`: it always appends at the end of the page, so the section
lands below Sources (it did on three Tools pages on 2026-10-29). `add_section` reads the page once,
inserts the section after the block just before the Sources heading, and adds the source lines after
the last block of the Sources list, skipping one already listed. With no Sources heading it appends
the section and a new Sources section at the end. It raises `ValueError` (hold the write for review)
when Sources is the page's first block or a batch is over 100 blocks.
```python
from insert_section import add_section

add_section(notion, "<target>",
            [block_heading("<New section>", 2), block_paragraph("…")],
            [block_bullet("[<source title>](<source url>) — <source type>")])
```
The section carries no "Sources:" paragraph of its own; its source line goes in the page's list.

**MCP fallback for updates:**
```
notion-update-page(
  page_id: "<target>",
  command: "update_content",
  content_updates: [{ old_str: "...", new_str: "..." }]
)
```
Fetch the target page's current content immediately before the update to get exact `old_str` matches.

**For `# Sources` maintenance:**
- If the target page already has `# Sources`, add new source lines to the end of its list (REST: `add_section`'s `sources` argument, which inserts after the list's last block; MCP: `old_str`/`new_str` on the last Sources line). Never as a "Sources:" paragraph inside the new section.
- If not, add the section at the very bottom.
- Deduplicate: if the source is already listed, skip.

---

## Step 6: `# Sources` formatting

Consistent format by source type:

| Source | Line format |
|---|---|
| Notion | `- [<title>](<url>) — human-maintained Notion doc` |
| PDF | `- PDF: "<filename>" (ingested <YYYY-MM-DD>)` |
| Web | `- Web: [<title>](<url>) — fetched <YYYY-MM-DD>` |
| Slack | `- Slack: [<thread subject>](<url>) — <channel>` |
| Conversation | `- Conversation: session on <YYYY-MM-DD>` |
| Pasted | `- Pasted text (ingested <YYYY-MM-DD>)` |

Date-stamp web/PDF/pasted sources — they rot.

**No Linear row.** `Guide` → Authoring rules bans Linear card links in AI
Knowledge content, `# Sources` included — a card moves or closes and the citation degrades into
a pointer at something that no longer says what it said. When a Linear thread *is* the source,
capture its durable content in the page body and name the ticket inline as text; `healthcheck-knowledge` detector #8 flags a card link in Sources as its own finding.

---

## Step 6b: Invalidate the local cache

For each Notion page this skill created or modified, **delete** the corresponding cache file so the next `retrieve-knowledge` read picks up the new content. Spec: [../retrieve-knowledge/references/cache.md](../retrieve-knowledge/references/cache.md).

For each affected path, slugify it (`/` → `__`, lowercased) and delete `${AI_KNOWLEDGE_CACHE_DIR:-~/.cache/ai-knowledge}/<slug>.md`. Delete-only — don't try to overwrite with the rendered ingest payload.

If the cache root or the target file doesn't exist, skip silently. Cache invalidation must never fail the ingest.

---

## Step 7: Summarise

After writes complete:

```
Ingested <source_title> into AI Knowledge:

New pages:
• [<title>](<url>) — under <section>
• [<title>](<url>) — under <section>

Updated pages:
• [<title>](<url>) — added section "<section>"; updated # Sources
• [<title>](<url>) — appended to # Sources only

Deferred (per your request or flagged):
• <chunk title> — <reason>
```

---

## Rules the skill enforces

1. **Never modify the source.** Humans own source docs.
2. **Always `# Sources`.** New pages get one. Edits append to the existing one or add one.
3. **Plan first, always.** No writes before user approval. In unattended mode, only the additive operations in [the table above](#unattended-mode) are applied; the rest are held.
4. **Re-learn the taxonomy every run.** Fetch from About — don't assume structure.
5. **No duplicate canonical locations.** Cross-link instead of copy-pasting a section into two pages.
6. **Ephemeral content is refused.** Sprint-level or time-bound state belongs in Linear/Slack.
7. **Voice-playbook compliant.** Respect banned-words and CTA rules even inside agent-facing docs.
8. **Show diffs before overwriting prose.** Updates that replace wording must be visible in the plan.
9. **Date-stamp rotting sources.** Web and PDF get `fetched <date>`.
10. **Preserve attribution.** If a Slack thread or Linear comment is the source, keep the speaker/author in `# Sources`.
11. **Ask before creating a new Learnings DB schema.** Default to a standalone page under Learnings if no existing DB fits.
12. **Graceful degradation.** If an MCP dependency is missing, tell the user how to proceed — don't silently drop the source.

---

## Edge cases

**Conflicting updates.** If the source contradicts existing content, surface the conflict in the plan and ask which wins (in unattended mode, hold it). Don't silently overwrite.

**Very long source.** Propose a staged ingest (highest-value chunks first); let the user approve one stage at a time.

**Image-heavy source.** Don't re-upload images. Link to the source doc for visuals and note this in `# Sources`.

**Cross-app content.** If it governs both Aura and Aurie, default to Identity › Brand (cross-app); only place under Products/<app> if it's app-specific.

**Unclear classification.** Include it as an open question in the plan rather than guessing.

**Re-ingesting a source already ingested.** Detect by scanning target pages' `# Sources` for the source URL. If found, frame the plan as an *update pass* — which sections changed since last ingest.

**Batch inputs.** Multiple sources in one run produce one combined plan with source-level grouping in the summary.
