---
name: retrieve-knowledge
description: >
  Reads from the AI Knowledge teamspace. Resolves taxonomy paths like `Products/Aurie`,
  `Domain/Competitors`, `Learnings/Experiments`, or `Signals/Aurie Conversations` to the right
  Notion page or database and returns the content with provenance (`# Sources`) intact. Owns
  the recipes for fetching a page, querying a learning or signal database with filters,
  getting the latest signal snapshot, and falling back to a teamspace-scoped search. Peer to
  `ingest-knowledge`, `harvest-knowledge`, and `healthcheck-knowledge` — this is the read
  side. Trigger when the user says "get knowledge about", "look up from AI Knowledge", "fetch
  the brand voice", "pull learnings for {vertical}", "find the latest Aurie conversations
  snapshot", "what does AI Knowledge say about…", "retrieve from the knowledge store", "search
  the knowledge base", or when another skill or a subagent declares `knowledge.reads` in its
  frontmatter and needs those paths pre-loaded.

metadata:
  version: "3.5.0"
knowledge:
  reads:
    - Guide/Index   # baseline routing aid (universal)
    - Tools/Notion                   # Notion REST / MCP access pattern (NOTION_TOKEN)
---

# Retrieve Knowledge

Read side of the AI Knowledge access layer. Turns a taxonomy path or database query into the right Notion content, without callers reasoning about page IDs or structure.

**Scope today:** Notion AI Knowledge teamspace (`34975e23-af20-8110-89ec-004231809285`).
**Not in scope:** writes (see `record-knowledge`), free-form page edits (see `ingest-knowledge` / `harvest-knowledge`).

## Core contract

- **Stateless, plan-free.** Reads are safe and idempotent. No approval flow — just return the content.
- **Taxonomy is discovered, not hardcoded.** Re-read `Guide` and `About` on first use per session so structural changes in Notion propagate without skill updates. Cache resolved path → page ID mappings within the session.
- **Always scoped to the teamspace.** Every `notion-search` call passes `teamspace_id=34975e23-af20-8110-89ec-004231809285`. Never let a search spill into other workspaces.
- **Provenance surfaces.** Returned content includes the `# Sources` block from the underlying page so the caller can cite where facts came from.
- **Guide-compliant.** Respects the Authoring rules on the [AI Knowledge Guide](https://www.notion.so/34975e23af20810bac17c192eed68134). If a caller asks for a page that should exist but doesn't, say so plainly — don't synthesise content.

---

## Inputs

A caller invokes this skill in one of three ways:

1. **Explicit path** — `path=Products/Aurie` or `path=Learnings/Experiments`.
2. **Query** — a database path plus filters, e.g. `path=Learnings/Experiments filters={vertical:"grief", since:"2026-01-01"}`.
3. **Declared manifest** — another skill, or a subagent, has a `knowledge.reads` block in its frontmatter and asks for all declared paths to be pre-loaded. For a skill pass `skill=<plugin>:<skill>` (or `skill_dir=<the base directory the host printed when that skill loaded>`). For an agent pass `agent_file=<path to its .md>` (what the `shared` hook hands a subagent), or `agent=<plugin>:<agent>` for a plugin agent, or `agent=<name>` for a project or user agent under `.claude/agents/`. The list itself is read **from disk** by Recipe F — never taken from context, because the Skill tool injects a skill's body only, a subagent gets only its agent file's body as its system prompt, and neither frontmatter is in anyone's context at run time.

Optional inputs:

- `limit` — max rows/results (databases and search only).
- `since` / `until` — ISO dates (databases and signals only).
- `section` — restrict a fallback search to a subtree (e.g. `Learnings`).
- `force_refresh` — boolean. Delete and refetch the cache file for this path before serving (see Step 2.5 + [references/cache.md](references/cache.md)). Default `false`.

---

## Step 1: Resolve inputs and learn the taxonomy

1. **Learn the taxonomy on first use this session.** Fetch the About page (`9ee75e23-af20-830a-a491-01a40466a789`), then the Guide page it links to. Read the current "Knowledge types" table and Guide → Index child (the live Path → Description map maintained by `harvest-knowledge`). Cache resolved path → page ID mappings within the session.
2. **Parse the caller's request** into `{ mode: "page"|"query"|"latest_signal"|"search"|"manifest", path, filters, limit, since, until, section }`.

**Wildcard handling.** If the caller's `knowledge.reads` manifest contains `"*"` (cross-cutting skills like `healthcheck-knowledge`), skip pre-fetching entirely in `manifest` mode — no single content blob can represent "everything." The skill will call specific recipes (A/B/C/D/E) on demand as its workflow proceeds. See [CLAUDE.md → Knowledge manifest](../../../../CLAUDE.md#knowledge-manifest) for the wildcard policy.

---

## Step 2: Resolve the path

A path looks like `Top/Section/Page` using the exact titles shown in Notion. Comparisons are case-sensitive and whitespace-preserving (use `Ad Campaigns`, not `ad-campaigns`).

Resolution order:

1. Look up the path in `Guide/Index` (cached from Step 1) — it gives you the page/database directly.
2. Walk from About → Guide → the matching top-level section → children, matching titles segment by segment. Use this when the index is missing the entry (recently added pages between harvest runs) or when the index itself is unavailable.
3. If still unresolved: tell the caller the path doesn't exist and list the closest sibling titles you found. Don't guess.

**Page vs database.** If the resolved node is a Notion database (data source), treat it as a queryable collection; otherwise treat it as a page. The taxonomy already makes this unambiguous:

- Pages: everything under `Identity/`, `Products/`, `Domain/`, `Procedures/`, and the Signals page bodies.
- Databases: children under `Learnings/` (Experiments, Ad Campaigns).
- Mixed: `Signals/<topic>/` is a page that contains dated child pages (each snapshot is its own page, not a DB row). Use "query by date range" recipe.

The live taxonomy is read from the [AI Knowledge Guide](https://www.notion.so/34975e23af20810bac17c192eed68134) every session — no bundled copy in this repo.

---

## Step 2.5: Consult the local cache

Before running any Notion recipe, check the on-disk cache. Full spec: [references/cache.md](references/cache.md).

Rules:

1. Resolve the cache file for the path: `${AI_KNOWLEDGE_CACHE_DIR:-~/.cache/ai-knowledge}/<slug>.md` (slug = path with `/` → `__`, lowercased).
2. If `force_refresh=true` → delete the cache file (if any) and skip to Step 3.
3. If the file is missing → skip to Step 3.
4. If the file exists and its `schema_version` is not `2` → delete it and skip to Step 3.
5. If the file exists and `now - fetched_at < 24h` → serve the cached body. You're done. Skip Step 3.
6. If the file exists and `now - fetched_at ≥ 24h` → delete it and run Step 3.
7. After any Step 3 fetch, write the body + frontmatter atomically to the cache file (`<file>.tmp` + rename).
8. On Notion errors (network, rate limit) with a cache hit available — even an expired one — serve cached and flag `stale=true, reason="notion-unreachable"` to the caller.

Filtered database queries (Recipe B) and dated-signal queries (Recipe C / D) **bypass the cache**. They run straight against Notion every time — caching them per-filter combination earns nothing.

Cache invalidation when *we* write to Notion happens in `record-knowledge`, `ingest-knowledge`, and `harvest-knowledge` (delete-on-write). When humans edit Notion directly, the change becomes visible after the 24h TTL or via `/refresh-knowledge`.

---

## Step 3: Execute the recipe

### Recipe A — Fetch a page by path

```
mcp__notion-fetch(id=<resolved-page-id>)
```

Return:
- `path` — the requested path
- `title` — the page title
- `body_md` — the page body in Markdown, with the `# Sources` section preserved verbatim
- `children` — list of child page titles (to help the caller drill down)
- `url` — canonical Notion URL

**Use for:** Identity, Products, Domain, Procedures, Signals index pages, any narrative page.

### Recipe B — Query a learnings database

For paths like `Learnings/Experiments` or `Learnings/Ad Campaigns`:

1. Resolve the path to the database's data source URL (`collection://...`).
2. Build a Notion filter from the caller's `filters` dict (vertical, date range, ship_decision, etc. — see [references/learning-schemas.md](references/learning-schemas.md) for the field shapes).
3. Query the data source. Apply `limit` if given.
4. Return rows as validated objects matching the schema for that database.

Callers that don't understand the schema should ask for it via Recipe A on the parent page (e.g. `Learnings/Experiments` page body describes its own columns).

**REST-first by necessity for Recipe B.** The repo default is
[MCP-first](../../../../CLAUDE.md#tokens-are-soft-requirements), and Recipe A (single-page
fetch) honours that — `notion-fetch` is fine for narrative pages. Recipe B is the
exception: `notion-search` against a `data_source_url` caps at 25 results with no
cursor and only does semantic search, so it cannot enumerate a database past 25 rows
or filter by property. When `NOTION_TOKEN` is set in the environment, run the query
through the REST API via [`scripts/notion_rest.py`](scripts/notion_rest.py) (this skill
ships the helper). Property filters and cursor pagination work cleanly — no 25-result
cap, no per-row search:

```python
from notion_rest import notion, MCPFallback

if notion.available:
    rows = list(notion.query_data_source(
        ds_id,
        filter=build_filter(caller_filters),
        page_size=100,
    ))
else:
    raise MCPFallback("use MCP recipe below")
```

Fall back to the MCP path (semantic search against `data_source_url`, 25-result cap) only when `NOTION_TOKEN` is unavailable — this is the case inside Cowork sandboxes (`mcp__workspace__bash` strips host env per PROD-1305) and on contributor machines that haven't run the integration setup yet.

### Recipe C — Query signal snapshots by date range

For paths like `Signals/Aurie Conversations`:

1. Resolve the path to the parent page.
2. List child pages (each is a dated snapshot — `YYYY-MM-DD snapshot` format).
3. Filter by `since` / `until` if given. Sort descending by date. Apply `limit`.
4. Fetch each matching snapshot page and return its body.

### Recipe D — Get the latest signal

Shortcut for Recipe C with `limit=1` sorted descending. Return exactly one snapshot or `null` if none exist.

### Recipe E — Search fallback

For open-ended queries the recipes above don't cover (e.g. "what did we learn about grief-related copy last quarter?"):

1. **Try the index first.** Fetch `Guide/Index` (resolved via About → Guide → Index child) and scan its `path — description` lines for matches. The index is curated by `harvest-knowledge` and is much cheaper and higher-precision than a teamspace search. If the query has a confident hit, return it directly.
2. **Fall back to teamspace search** when the index has no usable hit, when the index page is missing, or when the caller explicitly opts into deep search.

   ```
   mcp__notion-search(
     query=<query>,
     teamspace_id="34975e23-af20-8110-89ec-004231809285",
     filters={}
   )
   ```

If the caller passed `section`, post-filter results to only those whose page path starts with that section. Return each hit with `{ title, path, url, snippet, source }` where `source ∈ {"index", "search"}`.

For pure path-discovery (caller wants candidate paths but not content), prefer the dedicated `search-knowledge` skill — this recipe is the in-line fallback for callers who already have `retrieve-knowledge` loaded.

### Recipe F — Resolve a manifest

When another skill or a subagent asks for its `knowledge.reads` to be pre-loaded:

1. **Read the manifest from disk** — the caller's frontmatter is not in context at run time. Run the bundled resolver:

   ```bash
   python3 ${CLAUDE_PLUGIN_ROOT}/skills/retrieve-knowledge/scripts/manifest.py --skill <plugin>:<skill>
   # or: manifest.py <skill_dir>          (the base directory the host printed when the caller loaded)
   # or: manifest.py <agent_file>         (an agent: the path the shared hook named)
   # or: manifest.py --agent <plugin>:<agent> | --agent <name>
   ```

   It prints `{skill, kind, version, reads, writes, reads_wildcard, writes_wildcard}` as JSON — `skill` is the id for an agent too, and `kind` says which it is. It resolves `<plugin>:<skill>` and `<plugin>:<agent>` the way `slack_post.py` resolves a footer version — from this script's own install root and `CLAUDE_PLUGIN_ROOT`, never cwd — so it reads the copy the host actually loaded. A bare agent name is a project or user agent: `$CLAUDE_PROJECT_DIR/.claude/agents/`, then `~/.claude/agents/`, matched by filename or by frontmatter `name`; where `CLAUDE_PROJECT_DIR` is not set, pass the agent file's path instead. Exit 2 means the caller could not be located or parsed: report that and stop; do not guess a list from memory.
2. If `reads_wildcard` is true, skip pre-fetching (see *Wildcard handling* in Step 1).
3. For each path in `reads`, run the right recipe (A for pages, B for databases) with no filters.
4. Return a map of `{ path → content }` that the calling skill or agent can drop into its own prompt context.
5. If any path fails to resolve, include an error entry for that path but continue with the others.

---

## Step 4: Return

Hand the content back to the caller in a structured block. Keep Markdown intact — the caller may render, summarise, or forward it.

Always include:
- What was requested (the original path/query).
- Where it came from (resolved Notion URL).
- The content (page body, rows, or search hits).
- Any `# Sources` block from the underlying page.

---

## Out of scope

- **Open-ended path discovery** → `search-knowledge` (returns ranked candidate paths from `Guide/Index`)
- **Writes** → `record-knowledge`
- **Ingesting a new source** → `ingest-knowledge`
- **Periodic upstream sync (regenerates `Guide/Index`)** → `harvest-knowledge`
- **Auditing the teamspace (flags index drift)** → `healthcheck-knowledge`
- **Schema design** → owned by `record-knowledge`'s `references/schemas/`

---

## References

Taxonomy and path inventory are read from Notion at runtime — the [AI Knowledge Guide](https://www.notion.so/34975e23af20810bac17c192eed68134) defines the knowledge types, and `Guide/Index` is the live Path → Description map (maintained by `harvest-knowledge`). No bundled copy in this repo.

- [references/cache.md](references/cache.md) — local cache layer: file format, TTLs, freshness check, invalidation hooks
- [references/learning-schemas.md](references/learning-schemas.md) — shape of rows in `Learnings/*` databases
- [references/signal-schemas.md](references/signal-schemas.md) — shape of pages in `Signals/*`
