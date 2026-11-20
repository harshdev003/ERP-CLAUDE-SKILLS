# AI Knowledge — local cache layer

Authoritative spec for the on-disk cache that sits in front of the AI Knowledge Notion teamspace. Owned by `retrieve-knowledge`; honoured by `record-knowledge`, `ingest-knowledge`, and `harvest-knowledge` (write-through delete); refreshable via `/refresh-knowledge`.

Goal: agents that declare `knowledge.reads` should not pay a Notion round-trip for stable pages on every call. Cache hits return immediately. Correctness comes from a fixed 24-hour TTL plus write-through invalidation — no freshness probes against Notion.

The cache is **per-machine**, **per-user**, **markdown-on-disk**. It is grep-able and inspectable on purpose — this is not an opaque store.

## Design in one paragraph

Every cached entry has a 24-hour TTL. While fresh, serve from disk with zero Notion calls. When a sibling skill (`record-knowledge`, `ingest-knowledge`, `harvest-knowledge`) writes to Notion, it **deletes** the corresponding cache file before returning — the next read will refetch and rewrite. When a human edits Notion directly (outside our skills), the change becomes visible after the 24h TTL or via `/refresh-knowledge`. There are deliberately no `last_edited_time` checks, no envelope HEADs, no per-section TTL tables — those experiments showed the latency win was illusory and the complexity wasn't worth it.

---

## Cache root

```
${AI_KNOWLEDGE_CACHE_DIR:-~/.cache/ai-knowledge}/
```

Resolution order:

1. `$AI_KNOWLEDGE_CACHE_DIR` env var if set.
2. `~/.cache/ai-knowledge` (default — XDG-ish; works on macOS too).

Create the directory on first write. Never write outside this root.

## Layout

One flat file per cached page. Path slug = taxonomy path with `/` replaced by `__` and lowercased; spaces preserved as `-`. Examples:

```
~/.cache/ai-knowledge/
  identity__apps__aurie.md
  identity__brand.md
  domain__competitors.md
  guide__index.md
  learnings__experiments.md       # the database header page only — filtered queries are not cached
  signals__aurie-conversations.md # the parent page only — dated snapshots are not cached
```

Rules:

- Cache **page bodies** only. The unit is "what `notion-fetch(id)` returns for one page id."
- `Learnings/*` databases: cache the database's **header page** (the introductory page that documents columns). Do **not** cache filtered query results — those are caller-specific and go straight to Notion every time.
- `Signals/*`: cache the **parent index page** only. Individual dated snapshots are immutable on Notion's side and small enough to fetch on demand; no caching needed.
- No subdirectories, no `_meta.md`, no per-row files. The earlier multi-file design earned its keep only with a freshness-probe model; we no longer have that.

## File format

YAML frontmatter + Markdown body.

```markdown
---
notion_id: 34a75e23-af20-81d5-9267-e8eb5f5914e7
path: Products/Aurie
fetched_at: 2026-04-26T02:47:40Z
url: https://www.notion.so/34a75e23af2081d59267e8eb5f5914e7
schema_version: 2
---

# Aurie

…page body in Markdown, including the `# Sources` section verbatim…
```

Field rules:

- `notion_id` — the Notion page UUID.
- `path` — the taxonomy path the caller used to reach this page.
- `fetched_at` — ISO-8601 UTC with `Z` suffix; the only time field. Drives TTL.
- `url` — canonical Notion URL.
- `schema_version` — currently `2`. Readers seeing `1` (the freshness-probe shape from ENG-416) or any other value should treat as stale and refetch. Bump on every shape change.

No `last_edited_time`. No `content_hash`. No `kind`. The simpler shape is the point.

## TTL

Universal **24 hours** for every cached file.

- `now() - fetched_at < 24h` → **fresh**, serve cached body, no Notion call.
- `now() - fetched_at ≥ 24h` → **stale**, delete the file and refetch from Notion, write a new one.

No per-section overrides. No `max_age` parameter on the read API. The single knob keeps the spec small. If a caller needs guaranteed-fresh, use `force_refresh=true` (see below) or `/refresh-knowledge`.

## Read flow

Pseudocode for `retrieve-knowledge` Step 2.5:

```
def read(path, *, force_refresh=False):
    file = cache_path_for(path)
    if force_refresh:
        if file.exists():
            file.unlink()
        return refetch_and_write(path)

    if not file.exists():
        return refetch_and_write(path)

    fm, body = read_with_frontmatter(file)
    if fm.schema_version != 2:
        file.unlink()
        return refetch_and_write(path)

    age = now() - fm.fetched_at
    if age < timedelta(hours=24):
        return body                     # cache hit

    # Stale — delete and refetch
    file.unlink()
    return refetch_and_write(path)
```

`refetch_and_write` does the right Recipe A `notion-fetch`, writes the body + frontmatter to disk atomically (write to `<file>.tmp`, rename), and returns the body.

Filtered database queries (Recipe B) and dated-signal queries (Recipe C/D) **bypass the cache entirely**. They run straight against Notion — caching them per-filter combination earns nothing.

## Write-through invalidation

When one of our sibling skills writes to Notion, it MUST delete the corresponding cache file before returning. Delete-only (not overwrite) — the next read will refetch and write a fresh entry. Overwriting on the write side risks subtle drift (the markdown we'd render from a write payload doesn't always match what `notion-fetch` returns for the same page), and the simpler rule is easier to verify.

| Skill | Action |
|---|---|
| `record-knowledge` | Wrote a row to a `Learnings/*` database → delete `<root>/learnings__<dbname>.md` (the cached header page). Wrote a `Signals/*` snapshot → delete `<root>/signals__<topic>.md` (the cached parent index). Per-row and per-snapshot files don't exist in this layout. |
| `ingest-knowledge` | Touched any cached page → delete `<root>/<slug>.md` for each affected path. |
| `harvest-knowledge` | Touched any cached page → delete each affected `<root>/<slug>.md`. The `Guide/Index` page is the hot one — always delete `<root>/guide__index.md` after a regeneration. For renames, delete the old slug. |

If the cache root or a specific file doesn't exist, skip silently. Cache invalidation must never fail the underlying Notion write — log a warning and continue.

## Manual refresh

The `/refresh-knowledge` slash command is the human escape hatch:

- `/refresh-knowledge` → wipe the entire cache root.
- `/refresh-knowledge <path>` → wipe one path (e.g. `Products/Aurie`) or section prefix (e.g. `Identity`).

Wiping is sufficient — the next read repopulates lazily.

Use it when:

- A teammate edited Notion directly and you want the change visible before the 24h TTL expires.
- You're debugging unexpected staleness.
- A `harvest-knowledge` run on another machine updated something and your local cache is behind.

## Concurrency

Reads are safe to interleave. Writes use `<file>.tmp` + atomic rename. If two skills race to refetch the same path, last-writer-wins is fine — both fetch the same content. No locks.

## Offline mode

If the Notion MCP call fails (network, rate limit, auth) and a cache entry exists — even an expired one — serve the cached body and surface a warning (`stale=true, reason="notion-unreachable"`). If no cache entry exists, propagate the error. Don't fabricate.

## What the cache deliberately does NOT do

- **No freshness probe.** No `last_edited_time` comparison, no envelope HEAD, no search-as-HEAD. Empirically (see ENG-417 testing) `notion-search` and `notion-fetch` are in the same wall-clock ballpark, so the only "save" was token cost on stale-but-unchanged — not enough to justify the spec complexity.
- **No vector store / embeddings.** Out of scope.
- **No cross-machine sharing.** Per-user only.
- **No write-side caching.** Writes go straight through Notion via `record-knowledge`; the cache is only invalidated *after* the write returns.
- **No background refresh / pre-warming.** Lazy on read, explicit via `/refresh-knowledge`. No daemons, no cron.
- **No per-row or per-snapshot caching.** The unit is a Notion page. Filtered queries always hit Notion fresh.
- **No partial-page caching.** Don't cache individual blocks.

## Migration from schema_version 1

Files written under the original ENG-416 spec carry `schema_version: 1` and extra fields (`kind`, `last_edited_time`, `content_hash`). Readers see the version mismatch, delete the file, and refetch. No migration script needed; first read of each path replaces the file.
