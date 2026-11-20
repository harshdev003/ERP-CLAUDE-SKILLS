---
name: sync-mixpanel-events
description: >
  Seed and re-sync the four Mixpanel metadata databases under
  `Tools/Mixpanel/` in the AI Knowledge Notion teamspace: `Events`,
  `Cohorts`, `Super Properties`, and `User Properties`. Pulls live data
  from the Mixpanel MCP (events, properties, cohorts) and merges
  canonical descriptions from the team's tracking-plan Google Sheet at
  https://docs.google.com/spreadsheets/d/1wBG9-3SDyrupAGdGTA01haWEV0hxK00K6REtdk_zPf8.
  Mixpanel Lexicon wins for descriptions when present; the Sheet is the
  fallback. Upserts row-by-row by Name — never trashes a database to
  "clean up". Drift (rows that disappeared upstream) is recorded as a
  body note, not a deletion. Re-runnable any time. Trigger when the user
  says "sync mixpanel events", "refresh the mixpanel metadata", "rebuild
  Tools/Mixpanel", "import mixpanel lexicon into AI Knowledge", "update
  the mixpanel event inventory", "refresh the cohorts/super properties
  databases", or asks to bootstrap / refresh the canonical Mixpanel
  vocabulary for agents.
metadata:
  version: "3.0.1"
knowledge:
  reads:
    - Guide/Index   # baseline routing aid (universal)
    - Tools/Mixpanel                       # access pattern + project ID + lexicon URL
    - Tools/Notion                   # Notion REST / MCP access pattern (NOTION_TOKEN)
  writes:
    - Tools/Mixpanel/Events                # one row per event
    - Tools/Mixpanel/Cohorts               # one row per useful saved cohort
    - Tools/Mixpanel/Super Properties      # one row per super property
    - Tools/Mixpanel/User Properties       # one row per user (people) property
---

# sync-mixpanel-events

Build and maintain the canonical Mixpanel events database in AI Knowledge so
agents can reason about funnels, retention, and experiment metrics without
round-tripping the Mixpanel MCP for every lookup.

## When to use

Trigger when the user says any of:

- "sync mixpanel events"
- "refresh the mixpanel events database"
- "rebuild `Tools/Mixpanel/Events`"
- "import the mixpanel lexicon into AI Knowledge"
- "update the mixpanel event inventory"
- "bootstrap the mixpanel events DB"

Also trigger after a major instrumentation change or when another skill
reports stale / missing event metadata.

## Sources

The database is the **merged** view of two upstream sources. Both must be
cited in every row's `# Sources` section.

1. **Mixpanel Lexicon** — pulled live via the Mixpanel MCP:
   - `Get-Lexicon-URL` — base URL for source citations
   - `Get-Events` — event names + last-seen timestamps + status
   - `Get-Properties` — property catalog
   - `Get-Property-Values` — value enums per property
2. **Tracking-plan Google Sheet** — canonical descriptions:
   - https://docs.google.com/spreadsheets/d/1wBG9-3SDyrupAGdGTA01haWEV0hxK00K6REtdk_zPf8
   - The Sheet has **multiple tabs** (one per surface / domain). Read every tab and merge all rows into a single `event name → description` map. Tabs are discovered at runtime, not hard-coded.

## Merge rules

| Field          | Source of truth                                        |
|----------------|--------------------------------------------------------|
| Name           | Mixpanel (canonical event name as fired)               |
| Description    | Mixpanel Lexicon if present, else Sheet                |
| Properties     | Mixpanel (with value enums from `Get-Property-Values`) |
| First seen     | Mixpanel                                               |
| Last seen      | Mixpanel                                               |
| Lexicon URL    | Mixpanel                                               |
| Sources        | **Both** — Lexicon URL + Sheet row reference (when Sheet has the event) |

Events present in Mixpanel but missing from the Sheet are still synced —
the Sheet is consulted only as a description fallback. Events present in
the Sheet but not in Mixpanel are skipped (we sync the source of truth,
not the wishlist).

## Workflow

### 1. Resolve the target database

- Use `retrieve-knowledge` to fetch `Tools/Mixpanel` and confirm the
  database lives at `Tools/Mixpanel/Events`.
- If the database does not exist yet, create it via the Notion MCP under
  the `Tools/Mixpanel` page with the schema below before seeding rows.

### 2. Pull from Mixpanel

Call the Mixpanel MCP tools in sequence:

```
Get-Lexicon-URL
Get-Events                              # all events, both active and deprecated
Get-Properties                          # full property catalog
Get-Property-Values  (per property)     # only for properties referenced by events
```

Cache the responses in memory; do not re-call per row.

### 3. Pull from the Google Sheet (description fallback only)

Fetch the tracking plan via the Drive MCP if configured, or by exporting
each tab as CSV (`/export?format=csv&gid=<gid>`). **Read every tab** —
the Sheet is partitioned by surface/domain, and any tab can carry the
canonical description for an event.

For each tab, identify the columns by header (case-insensitive, ignore
whitespace):

- An event-name column — usually `Event Name`, `Event`, or `Name`
- A description column — usually `Description`, `Desc`, or `Definition`

Merge all tabs into a single `event_name → description` map. If two tabs
disagree on the same event name, prefer the longer non-empty description
(team rarely shortens by mistake; truncation is the more common error).

The Description is used **only** when the Mixpanel Lexicon has no
description for that event.

If the Sheet is not accessible (HTTP 401 / 403 / no Drive MCP), log a
warning and continue with Mixpanel-only data. Events without a Mixpanel
description and no Sheet fallback are recorded with an empty description
and flagged in the final report.

### 4. Merge

Build a dict keyed by event name. For each event in Mixpanel, derive:

- `Name` — canonical Mixpanel name
- `Description` — Mixpanel Lexicon description if non-empty, else the Sheet
  description (any tab), else empty string
- `Lexicon URL` — `https://mixpanel.com/project/<project-id>/view/<view-id>/app/lexicon/transformations/events/<urlencoded-name>`

Source citations live once at the **database-level description** (Mixpanel
Lexicon project URL + Sheet URL), not on every row. Don't write a
per-row `# Sources` block — it duplicates information and bloats the page
body. The page body stays empty unless a human adds notes.

### 4.5 Show the plan and confirm (before any write)

**Plan-and-confirm gate.** This skill writes directly to four Notion databases, bypassing `record-knowledge` — so it owns its own pre-write gate, the same way its sibling `data:sync-experiments` shows a dry-run before touching the catalog. After the merge in step 4, compute the planned change set **without writing** and show it to the user:

```
Planned Mixpanel sync (no writes yet):
- Events:          create N, update M (description-only M1, other M2), drift-flag K
- Cohorts:         create …, update …
- Super Properties: create …, update …
- User Properties:  create …, update …
```

Include a short sample of the actual creates/updates (a few event names) so the user can sanity-check before the batch write. Wait for explicit approval, then proceed to step 5. If nothing would change, report "already in sync" and stop — no confirmation needed for a zero-write run.

### 5. Upsert (do not delete and recreate)

The sync is **idempotent and incremental**. For each merged row:

1. **Look up the existing page** by event Name. Use either:
   - Notion search restricted to the data source: `notion-search` with
     `data_source_url: collection://<data-source-id>` and `query: <event name>` — accept the result whose `title` matches exactly.
   - Or maintain a local `name → page_id` cache populated on first run
     (faster, but requires the DB to fit in one paginated enumeration).
2. **If found:** call `notion-update-page` with `command: update_properties`
   and only the fields that changed (typically just `Description` when the
   Lexicon adds or improves text). Empty `content_updates: []` so the
   body is left alone.
3. **If not found:** call `notion-create-pages` with the full property set.

**Never trash the database to "clean up".** Drift is handled by the
healthcheck, not by destruction. Re-creating the DB invalidates every
inbound link, every saved view, every comment a human added.

### 6. Reconcile drift

After upsert, walk the existing DB names that were **not** seen in this
run's Mixpanel snapshot:

- For each, fetch the page and append `# Drift: not seen in Mixpanel as
  of <YYYY-MM-DD>` to the page body (or update an existing drift note's
  date).
- Do **not** delete drifted rows automatically — `healthcheck-knowledge`
  surfaces them for human review.

### 7. Report

Print a short summary to the user:

```
Mixpanel events sync complete.
- Created: N
- Updated: M (description-only: M1, other: M2)
- Drift-flagged: K
- Sheet-only (skipped, not in Mixpanel): P
- No description (Mixpanel + Sheet both blank): Q
```

## Schema

### `Tools/Mixpanel/Events` (one row per event)

| Property     | Type        | Notes                                       |
|--------------|-------------|---------------------------------------------|
| Name         | Title       | Canonical Mixpanel event name (merge key)   |
| Description  | Rich text   | Mixpanel Lexicon, falling back to Sheet     |
| Lexicon URL  | URL         | Direct link to the Lexicon entry            |

### `Tools/Mixpanel/Cohorts` (one row per useful saved cohort)

| Property    | Type      | Notes                                     |
|-------------|-----------|-------------------------------------------|
| Name        | Title     | Cohort name (merge key)                   |
| Description | Rich text | What the cohort selects                   |
| Cohort URL  | URL       | Direct link to the Mixpanel cohort        |

Sourced from the `Useful Cohorts` Sheet tab. Mixpanel does not expose a
"useful" flag — curation is the team's responsibility in the Sheet.

### `Tools/Mixpanel/Super Properties` (one row per super property)

| Property      | Type      | Notes                                                |
|---------------|-----------|------------------------------------------------------|
| Name          | Title     | Super property name (merge key)                      |
| Description   | Rich text | What it captures                                     |
| Type          | Rich text | string / number / Integer / Object / List / etc.    |
| Example Value | Rich text | Example as written in the Sheet                     |

Sourced from the `Mixpnl super props` Sheet tab. Excludes Mixpanel-default
properties (they live in Mixpanel's own docs, not Aura's vocabulary).

### `Tools/Mixpanel/User Properties` (one row per people property)

| Property      | Type      | Notes                                              |
|---------------|-----------|----------------------------------------------------|
| Name          | Title     | User property name (merge key)                     |
| Description   | Rich text | What it represents                                 |
| Example Value | Rich text | From Sheet                                         |

Sourced from the `User Properties` Sheet tab. Multi-line example
continuations in the CSV are merged into a single comma-separated string
on the row's Example Value.

### Database-level descriptions

Each of the four databases stores its source citations once at the
database-description level (Sheet URL + Mixpanel project URL), not per
row. Page bodies stay empty unless a human or the drift step writes a
note.

### Cache invalidation

This skill writes directly to AI Knowledge (`Tools/Mixpanel/Events` and the sibling Cohorts / Super Properties / User Properties databases) via `notion-create-pages` / `notion-update-page`, bypassing `record-knowledge`. After a sync run, delete the cached header pages for the touched paths so `retrieve-knowledge` picks up the changes:

- `${AI_KNOWLEDGE_CACHE_DIR:-~/.cache/ai-knowledge}/tools__mixpanel.md`
- `${AI_KNOWLEDGE_CACHE_DIR:-~/.cache/ai-knowledge}/tools__mixpanel__events.md`
- `${AI_KNOWLEDGE_CACHE_DIR:-~/.cache/ai-knowledge}/tools__mixpanel__cohorts.md`
- `${AI_KNOWLEDGE_CACHE_DIR:-~/.cache/ai-knowledge}/tools__mixpanel__super-properties.md`
- `${AI_KNOWLEDGE_CACHE_DIR:-~/.cache/ai-knowledge}/tools__mixpanel__user-properties.md`

Spec: [`shared:retrieve-knowledge/references/cache.md`](../../../shared/skills/retrieve-knowledge/references/cache.md). Skip silently if the cache root or any file doesn't exist. Cache invalidation must never fail the sync.

### Out of scope (v0)

- `Tools/Mixpanel/Properties` — full per-property metadata DB (~3000
  properties; deferred until a skill actually needs it).
- `First seen` / `Last seen` columns on Events — not returned by
  `Get-Events`; would require a separate JQL query per event.
- Per-event `Tags` — Mixpanel tags exist on ~890 events but were
  intentionally excluded; revisit if the team starts using them as a
  query dimension.
- Mixpanel-default properties (`$browser`, `$device`, etc.) — covered in
  Mixpanel's own docs, not mirrored.

## Idempotency

The skill is safe to re-run any time. The upsert pattern in step 5 means
a re-run only writes deltas: existing rows are amended in place via
`notion-update-page`, never duplicated, never deleted. Renaming an event
in Mixpanel produces a new row + an old row that drifts out — handled by
step 6's drift note, not by destructive cleanup. Page bodies are
preserved across runs so any human notes survive.

## Failure modes

- **Mixpanel MCP not configured** → instruct the user to install the
  Mixpanel MCP via `update-config` and retry.
- **Google Sheet not accessible** → log a warning and continue with
  Mixpanel-only data (Sheet is description fallback only).
- **`Tools/Mixpanel` page missing** → instruct the user to run the
  Tools-section setup from ENG-408 first.

## Related

- Companion to `analyze-experiment` (consumes this DB to interpret event
  names in experiment readouts).
- Read by future skills doing funnel / retention reasoning.
- Maintained alongside the `Tools/Mixpanel` page from ENG-408.
