---
description: Wipe the local AI Knowledge cache so the next read pulls fresh from Notion. Optional path argument scopes the wipe to a single section or page.
---

# /refresh-knowledge

Force a refresh of the on-disk AI Knowledge cache used by `retrieve-knowledge`.

## Usage

- `/refresh-knowledge` — wipe the entire cache root.
- `/refresh-knowledge <path>` — wipe just one taxonomy path. Examples:
  - `/refresh-knowledge Products/Aurie`
  - `/refresh-knowledge Learnings/Experiments`
  - `/refresh-knowledge Guide/Index`
  - `/refresh-knowledge Identity` (matches the whole `Identity/` subtree)

Path syntax matches the AI Knowledge taxonomy used by skill `knowledge.reads` manifests — case-sensitive, slash-separated, exactly as it appears in Notion.

## What it does

1. Resolve the cache root: `$AI_KNOWLEDGE_CACHE_DIR` if set, else `~/.cache/ai-knowledge`.
2. If a `<path>` argument was given, slugify it (`/` → `__`, lowercased) and target only that file:
   - Page paths → delete `<root>/<slug>.md`.
   - Section prefixes (e.g. `Identity`) → delete every cache file whose slug starts with the prefix (e.g. wipes `identity__apps__aurie.md`, `identity__brand.md`, etc.).
3. If no argument was given, delete every file under the cache root (but keep the root directory itself).
4. Report what was deleted: count of files + total bytes freed. Do NOT pre-warm — the next `retrieve-knowledge` read will lazily refetch.

The cache layout is flat — one file per page, no subdirectories, no per-row or per-snapshot files. Filtered database queries and dated signal snapshots aren't cached at all (they always go straight to Notion).

If the cache root doesn't exist, say so and exit cleanly.

## When to use this

- After a manual edit in Notion that you want available immediately, before the 24h TTL expires.
- When debugging unexpected staleness.
- After a `harvest-knowledge` run on another machine, to bring this machine's cache in line without waiting for the TTL.

Sibling skills (`record-knowledge`, `ingest-knowledge`, `harvest-knowledge`, the top-10 updaters, `sync-mixpanel-events`, `healthcheck-knowledge`) already delete the relevant cache files automatically when they write to Notion — you only need this command for human edits or cross-machine sync.

## What it does NOT do

- Does not call Notion. The cache is wiped; refetches happen lazily on next read.
- Does not touch any Notion content. Read-only on Notion's side; destructive only on the local cache directory.
- Does not affect skills' in-session memory (taxonomy mappings, cached page IDs in the current Claude session). Those persist for the session.

## Spec

Cache root, file format (5-field frontmatter), 24h TTL, and write-through invalidation rules live in [`plugins/shared/skills/retrieve-knowledge/references/cache.md`](../skills/retrieve-knowledge/references/cache.md).
