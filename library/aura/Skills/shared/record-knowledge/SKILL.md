---
name: record-knowledge
description: >
  Writes structured, schema-validated rows and snapshots back to the AI Knowledge teamspace.
  Two actions: `create` (new row or snapshot) and `update` (enrich an existing Learnings row
  in place). Handles rows in Learnings databases and Signals writes — both dated snapshot
  pages and observational-table rows. Companion to `retrieve-knowledge`, this is the write
  side; distinct from `ingest-knowledge` (external sources) and `harvest-knowledge` (upstream
  sync). Validates every write against that path's schema, always appends a `# Sources`
  section, and refuses to write outside the paths the caller declared in `knowledge.writes`.
  Trigger when the user or another skill says "record a learning", "log this experiment
  result", "save the ad campaign readout", "record an Aurie conversations snapshot", "update
  the learnings row with the new analysis", "enrich this experiment row", "write this to the
  Learnings database", "save a signal snapshot", or invokes the skill through a
  `knowledge.writes` manifest.

metadata:
  version: "3.8.0"
knowledge:
  reads:
    - Compliance/Privacy     # PII redaction rules — reject writes containing full email / givenName / familyName
    - Guide/Index            # baseline routing aid (universal)
    - Guide/Placement Rules  # placement policy enforced on every write
    - Tools/Notion                   # Notion REST / MCP access pattern (NOTION_TOKEN)
---

# Record Knowledge

Write side of the AI Knowledge access layer. Turns a declared write (path + action + fields) into a validated Notion create or update, preserving schemas and provenance.

**Scope:** writes only to `Learnings/*` (database rows) and `Signals/*` — both dated snapshot pages and observational-table rows (the schema for each Signals path declares which kind).
**Not in scope:** reads (see `retrieve-knowledge`), free-form page creation (see `ingest-knowledge` / `harvest-knowledge`), edits to Identity / Domain / Procedures (human-edited or covered by ingest/harvest).

## Core contract

- **Validate first, write second.** Every write passes schema validation *before* any Notion call. Schema violations fail loud — no silent drops, no partial writes.
- **Declared paths only.** If the calling skill has a `knowledge.writes` list in its SKILL.md frontmatter, the target path must appear there. No declaration → refuse with a clear message. Covers scope creep automatically.
- **Honour `Guide/Placement Rules`.** No write may land at the workspace or teamspace root, and no write may target a section root when a more specific subsection exists. Refuse with the placement-rule citation if asked. The rule applies even to wildcard (`writes: ["*"]`) callers.
- **Honour `Compliance/Privacy`.** Reject any write whose `fields` or `body` contains a full email address (anything matching `<local>@<domain>` where `<local>` is more than a literal `*` or `…`), a `givenName` / `familyName` from `/users/<id>`, or any other PII per the canonical rule. AI Knowledge is the shared institutional layer — it must never hold identifiers that belong only in private engineering systems. Surface the violation to the caller with the offending value redacted in the error message; never echo the raw PII back.
- **Respect the write boundary.** This skill never writes to `Identity/`, `Products/`, `Domain/`, `Procedures/`, `Compliance/`, `Guide`, `About`, or `Health Check`. Those are human-edited or handled by ingest/harvest. Refuse cleanly if asked.
- **Traceability baked in.** Every written row/page carries a `# Sources` block. Non-negotiable per [Guide authoring rules](https://www.notion.so/34975e23af20810bac17c192eed68134). Authorship is captured by Notion's native `Created by` / `Last edited by` fields — no separate mode axis on rows (see [ENG-404](https://linear.app/aura-health/issue/ENG-404)).
- **Fail loud on Notion downtime.** Inherit the MCP client's default retry/backoff. If the call still fails, raise — do not queue, do not cache-then-flush. Write path is fail-fast. Revisit the policy only if we see recurring failures in practice.
- **Immutable Signal *snapshots*.** Signal snapshot pages are write-once. Never edit a prior snapshot; always create a new dated page. `action: update` is **rejected** for any Signals path whose schema declares kind `snapshot` (e.g. `Signals/Aurie Conversations`).
- **Signal *observational tables* and Learnings rows accept updates.** `Learnings/*` rows and Signals paths whose schema declares kind `table` (e.g. `Signals/Conversation Patterns`) are updatable, **unless the schema declares `Writable: no`** — a table written by skills outside this one (`Signals/Cancellations`) is refused for every action, see the first action guardrail below. Updates are *narrow by design*: property merge (not replace), named body-section replace only, sources append-only. `## What was tested` and the original `# Sources` entries are never overwritten by an update. (A `table` schema may declare itself **create-only / write-once** — e.g. `Signals/Conversation Patterns`, one immutable row per `Rating ID`. `record-knowledge` rejects `action: update` on a create-only schema, exactly as it does for a snapshot; only enrichable tables accept updates.)

---

## Inputs

A caller invokes this skill with:

- `path` — target path (e.g. `Learnings/Experiments`, `Signals/Aurie Conversations`).
- `action` — `create` (default) or `update`. `update` valid on `Learnings/*` paths and on Signals `table`-kind paths; rejected on Signals `snapshot`-kind paths.
- `fields` — an object matching the schema for that path. See [references/schemas/](references/schemas/).
- `sources` (optional, required for leaf rows on create) — list of `{ label, url, primary? }` entries.
- `snapshot_date` (signals only) — ISO date for the snapshot title; defaults to today.
- `caller` — `<plugin>:<skill>` of the invoking skill, or the skill's directory (the base directory the host printed when it loaded) when no plugin id is known. A subagent passes the path to its agent file (`…/agents/<name>.md`, the path the `shared` hook named when it started) — a path, never a bare `<plugin>:<agent>`, which would read as a skill id. Step 1 authorises the write against that caller's `knowledge.writes` **as read from disk** (its frontmatter is never in context at run time). Omit only for a direct human invocation, and then pass `allow_undeclared: true` as well.

### Update-specific inputs

When `action: update`:

- `page_id` (required) — Notion URL or UUID of the existing row to enrich.
- `fields` — **partial** object with only the properties to update. Omitted properties are preserved. (Contrast with create, where required-field checks apply.)
- `body_replace_sections` — object mapping section heading to new body markdown. Only these sections get rewritten; the rest of the page body is untouched. Standard allowed sections per path:
  - `Learnings/Experiments`: `## Result`, `## Learning`, `## Follow-up experiments`
  - `Learnings/Growth Readings`: `## Overview`, `## Detailed Learnings`
  - `Learnings/Ad Campaigns`: `## Result`, `## Learning`, `## Follow-up experiments`
  - `Learnings/Conversations`: `## Headline`, `## What worked`, `## What didn't`, `## Hypotheses to validate`, `## Recommended follow-up`
- `body_append_sources` — list of `{ label, url, primary? }` entries to append to the `# Sources` section. Never overwrites existing sources.

The caller is usually another skill — e.g. `analyze-experiment` creating a new experiment row, or `analyze-experiment` enriching a bulk-imported row with `action: update` after Mixpanel + Slack analysis. Humans can invoke directly too.

---

## Step 0: Confirm authoring rules from the Guide (once per session)

Before the first write of a session, fetch the Guide so the `# Sources` requirement and write boundary come from the live source of truth — not from this SKILL.md.

1. `notion-fetch(AI_KNOWLEDGE_ABOUT_PAGE)` to resolve the current Guide page id (cache for the session).
2. `notion-fetch(<Guide page-id>)` — read the authoring rules and the "AI scope of edits" rule. Cache the parsed result for the session.

Apply the cached rules through the rest of the steps:

- The `# Sources` block requirement (Step 4) is whatever the Guide currently mandates.
- The write boundary in Step 1 (refuse `Identity/`, `Products/`, `Domain/`, `Procedures/`, `Guide`, `About`, `Health Check`) is reconciled with the Guide's current "AI scope of edits" rule. If the Guide has loosened or tightened the boundary, honour the Guide.
- If the Guide fetch fails, fall back to the rules baked into this SKILL.md and surface a one-line warning in the write result so the caller knows the live rules weren't checked.

Subsequent calls in the same session reuse the cache — don't re-fetch on every write.

---

## Step 1: Resolve inputs and check permission

1. **Parse the request** into `{ path, action, fields, sources, snapshot_date, page_id, body_replace_sections, body_append_sources }`. Default `action` to `create` if omitted.
2. **Look up the caller's `knowledge.writes` list from disk.** The Skill tool injects a skill's body only and a subagent gets only its agent file's body, so the caller's frontmatter is never in context — run the bundled resolver instead of expecting it:

   ```bash
   python3 ${CLAUDE_PLUGIN_ROOT}/skills/record-knowledge/scripts/manifest.py --skill <caller> --check-write "<path>"
   # or, when caller is a directory:  manifest.py <caller-dir> --check-write "<path>"
   # or, when caller is an agent file: manifest.py <agent.md> --check-write "<path>"
   ```

   Exit `0` → allowed (`reason` says whether by declaration or wildcard). Exit `1` → refuse, quoting the JSON `reason`. Exit `2` → the caller could not be resolved or parsed: surface that as a tooling fault; it is **not** an allow, and it is not the same as "no `knowledge.writes` declared". The script encodes exactly the rules in item 3 below and consults nothing else.
3. **Authorize the path:**
   - If the calling skill has `knowledge.writes` declared, `path` must appear in it. Refuse otherwise.
   - **Wildcard:** if `knowledge.writes` contains `"*"` (cross-cutting skills like `ingest-knowledge` and `harvest-knowledge`), treat the caller as pre-authorized for any path inside the normal write boundary (`Learnings/*` and `Signals/*`). The boundary still applies — `"*"` does not grant writes to Identity, Products, Domain, Procedures, Guide, About, or Health Check. See [CLAUDE.md → Knowledge manifest](../../../../CLAUDE.md#knowledge-manifest) for the wildcard policy.
   - If no `knowledge.writes` manifest is available, refuse by default. Callers that intentionally bypass (direct human invocation, one-off imports) must pass `allow_undeclared: true` explicitly in the input. This prevents scheduled/chained skills from leaking writes silently.
4. **Guardrail on section.** Path must start with `Learnings/` or `Signals/`. Anything else → refuse with an explanation pointing to the right skill (ingest/harvest for narrative pages).
5. **Action-specific guardrails:**
   - **If the resolved schema declares `Writable: no` → refuse, for every action, before any other check here.** The path is documented so callers can read its column contract, not so they can write it: another skill owns the writes and needs paging or filtering the recipes below cannot express. Naming the path in the enrichable-table lists above does not override this — `Writable` is checked first, and it is the only line that decides whether this skill may write at all. Today: `Signals/Cancellations`.
   - If `action: update` and `path` is a Signals `snapshot`-kind path (per its schema) → refuse. Snapshot pages are write-once by design. Signals `table`-kind paths (e.g. `Signals/Conversation Patterns`) and all `Learnings/*` paths may be updated (subject to the schema's own idempotency rule, and to the `Writable` gate above). **Default when `Kind` is absent:** a `Signals/*` schema that declares no `Kind` line is treated as `snapshot` (write-once) — the safe default, so `action: update` is refused rather than silently allowed. Only an explicit `Kind: table` opens a Signals path to updates.
   - If `action: update` and the target schema is **create-only / write-once** (declares immutable rows — e.g. `Signals/Conversation Patterns`, one row per `Rating ID`) → refuse, even though it is a `table`-kind path. Create-only tables go through Recipe A2 (create-or-skip) only. Only enrichable tables accept `action: update` — today that means `Learnings/*` rows.
   - If `action: update` and `page_id` is missing → refuse.
   - If `action: update` and the resolved page's parent database does NOT match the `path`'s data source → refuse (prevents cross-DB writes via a stale `page_id`).

---

## Step 2: Load and apply the schema

1. **Resolve the schema file** for the path:
   - `Learnings/Experiments` → [references/schemas/experiments.md](references/schemas/experiments.md)
   - `Learnings/Ad Campaigns` → [references/schemas/ad-campaigns.md](references/schemas/ad-campaigns.md)
   - `Learnings/Growth Readings` → [references/schemas/growth-readings.md](references/schemas/growth-readings.md)
   - `Learnings/Conversations` → [references/schemas/conversations.md](references/schemas/conversations.md)
   - `Signals/Conversation Patterns` → [references/schemas/conversation-patterns.md](references/schemas/conversation-patterns.md)
   - `Signals/Conversation Topics` → [references/schemas/conversation-topics.md](references/schemas/conversation-topics.md)
   - `Signals/Aurie Conversations` → [references/schemas/aurie-conversations.md](references/schemas/aurie-conversations.md)
   - `Signals/Aurie Weekly Cost` → [references/schemas/aurie-weekly-cost.md](references/schemas/aurie-weekly-cost.md)
   - All start from [references/base-schema.md](references/base-schema.md).
   - `Signals/Cancellations` → [references/schemas/cancellations.md](references/schemas/cancellations.md)
     — resolvable for its column contract, and **`Writable: no`**, so Step 1's first action guardrail
     has already refused it before this step runs. The writes belong to
     [`data:harvest-cancellation-feedback`](../../../data/skills/harvest-cancellation-feedback/SKILL.md)
     and [`data:enrich-cancellation-feedback`](../../../data/skills/enrich-cancellation-feedback/SKILL.md).
     Point the caller at the schema; do not fall through to a recipe.
   - **Picking between the five conversation/feedback paths is a routing decision, not a schema one.**
     Route by grain per [`Guide/Placement Rules` → *Conversation & feedback entries: route by grain*](https://www.notion.so/35a75e23af20812ca987c2bcedf27dd7);
     each schema above restates which grain it takes.
2. **Validate `fields`**:
   - **On `action: create`:** all required fields present. Enum values match allowed sets. Types match (numbers, ISO dates, etc.). `sources` non-empty for leaf rows / snapshot pages.
   - **On `action: update`:** only validate fields that are present in the payload. Enum values + type checks still run. Required-field presence is NOT checked (the row already has those filled). Never downgrade required fields to blank.
3. **On validation failure**: collect *all* errors, report them together, refuse the write. No partial writes, no "fix it and retry with half the fields."
4. **Auto-fill** system fields:
   - On create: `created_at = now`.
   - On update: preserve `created_at`.
5. **Body section allowlist check (update only).** The sections in `body_replace_sections` must be in the path's allowed list (see Inputs). Rewriting `## What was tested` or other non-allowed sections is refused.

If a path has no known schema (a new `Signals/*` subsection that hasn't been documented yet), refuse and ask the caller to add a schema file first. Do not guess field shape.

---

## Step 3: Execute the write

**Execution path — REST-first by necessity.** The repo default is
[MCP-first](../../../../CLAUDE.md#tokens-are-soft-requirements); this skill deviates
because every write recipe below hits a structural MCP limit: dedup on Conversation
ID (Recipe A2) needs `query_data_source` with a property filter, which the MCP
doesn't expose; surgical body-section replace needs `update_block` on a specific
block id, not `notion-update-page`; and `notion-search` against a `data_source_url`
caps at 25 results with no cursor, so re-finding an existing row by title fails on
any database past 25 rows. All recipes below are **REST-first when `NOTION_TOKEN`
is set**, MCP fallback otherwise. The REST path uses [`scripts/notion_rest.py`](scripts/notion_rest.py)
(this skill ships its own copy — see CLAUDE.md → "Notion REST integration" for the
per-skill copy convention). It is preferred because:

- Property merges (`update_page_props`) are atomic and don't require fetching the page first.
- Body section replace (find heading → walk children → PATCH the right paragraph block via `update_block`) is straightforward and idempotent.
- Source appends use `append_block_children` with dedup by URL — no risk of partial writes.

The MCP path (`notion-update-page` etc.) is the fallback when running inside Cowork (host env stripped per PROD-1305) or on machines without the integration token. Both paths produce identical writes; the contract is the same.

```python
from notion_rest import notion, MCPFallback

if notion.available:
    notion.create_database_row(ds_id, props, blocks=body)  # Recipe A: new Learnings row
    notion.create_page(parent_id, title, blocks=body)      # Recipe B: new Signals snapshot page
    notion.update_page_props(page_id, props)        # property merge
    notion.update_block(block_id, paragraph_body)   # body section replace
    notion.append_block_children(parent_id, kids)   # source append
else:
    raise MCPFallback("use the MCP path documented per recipe below")
```

### Recipe A — Write a learnings row

For `Learnings/Experiments` and `Learnings/Ad Campaigns` (and the write-once create for `Signals/Conversation Patterns` — see Recipe A2 for its `Rating ID` dedup):

1. Resolve the database's data source ID (use the seed registry in `retrieve-knowledge` or, MCP fallback, `notion-fetch` on the database page).
2. Map validated `fields` to Notion properties per the schema (`make_title` / `make_select` / `make_rich_text` / `make_date`).
3. Create the row — REST `notion.create_database_row(ds_id, properties, blocks=[…])` (key the title as `"title"` per the title-by-ID gotcha), MCP `notion-create-pages` fallback with parent = data source ID.
4. Include the `# Sources` block in the page body (Notion database pages support a body under the row) — pass it as `blocks` to `create_database_row`.
5. Return `{ id, url, path, type }`.

**No deduplication.** Learnings rows are not deduped by title or content — callers are responsible for avoiding duplicates. Accidental duplicates surface as findings in `healthcheck-knowledge` rather than being blocked here.

### Recipe A2 — Write a row that dedups on a natural key

Two DBs carry a natural key and dedup before creating — same as Recipe A, plus one lookup step:

- **`Learnings/Conversations`** — dedup on `Conversation ID` (Braintrust UUID). Re-running the analysis on the same conversation must not create a duplicate. DS ID `2895c7d1-a921-49ac-ad16-91b3ac70951f`.
- **`Signals/Conversation Patterns`** — dedup on **`Rating ID`** (the Slack ts of the k8bot rating message), *not* on `Conversation ID`. Rows are **write-once**: a re-run must *skip* an already-recorded rating, never enrich or duplicate it. DS ID `86ba6f79-e9dc-4ffb-9f05-2bd991b2ae8f`.

Steps:

1. Resolve the data source for the path.
2. Query it filtered on **that path's** natural key — `"Conversation ID" == <value>` (case-insensitive) for `Learnings/Conversations`, `"Rating ID" == <value>` (exact; a ts has no case) for `Signals/Conversation Patterns`. If a row exists:
   - Refuse the create with `{ "error": "duplicate", "existing_url": "<row URL>", "existing_id": "<page_id>" }`.
   - The caller is expected to **skip** (for `Signals/Conversation Patterns`, always skip — the row is write-once) or, for `Learnings/Conversations`, switch to `action: update` with the returned `page_id` (Recipe C).
3. If no existing row: proceed with Recipe A steps 2–5.

**The two keys are not the same column, and the difference is the whole point.** `Learnings/Conversations` is one curated row per *conversation*, so a k8bot re-rate of that conversation must land on the existing row — `Conversation ID` is correct there. `Signals/Conversation Patterns` is one row per *rating event*, and a thread can be rated many times (`chat` rates per message), so keying it on the conversation collapses distinct ratings into one row and drops the rest as false duplicates ([ENG-1115](https://linear.app/aura-health/issue/ENG-1115) measured the cost: 53 rows in one 7-day window). Others (Experiments, Ad Campaigns) rely on the caller.

### Recipe B — Write a signal snapshot

**Applies to `snapshot`-kind Signals paths only** (per the schema's `Kind` line; absent `Kind` defaults to `snapshot`). Today: `Signals/Conversation Topics`, `Signals/Aurie Conversations`, `Signals/Aurie Weekly Cost`, and future snapshot subsections. A `table`-kind Signals path (`Signals/Conversation Patterns`) is **not** a snapshot — it uses a database-row recipe instead (Recipe A2 dedup-on-natural-key create for write-once/append tables, Recipe C for enrich-in-place tables), exactly as the equivalent `Learnings/*` tables do. Each table's own schema states which. Route on the schema's `Kind` before picking a recipe.

1. Resolve the parent page ID (e.g. `Signals/Aurie Conversations`).
2. Build the page title: `<Producer> — YYYY-MM-DD snapshot` (e.g. `Aurie Conversations — 2026-04-22 snapshot`).
3. **Idempotency check:** list child pages of the parent. If one with today's title already exists, refuse — snapshots are immutable. Caller should pick a different `snapshot_date` or use the existing snapshot.
4. Render the page body per the snapshot schema (At a glance → Window → facet tables → Related → `# Sources`) using the `block_*` builders.
5. Create the page — REST `notion.create_page(parent_page_id, title, blocks)`, MCP `notion-create-pages` fallback.
6. Return `{ id, url, path, snapshot_date }`.

### Recipe C — Update a Learnings row (enrichment)

For `action: update` on any `Learnings/*` path (Experiments, Growth Readings, Ad Campaigns, Conversations). Used by `analyze-experiment` to enrich a bulk-imported row with fresh Mixpanel actuals, synthesised learnings, and follow-up ideas — **in place**, not as a duplicate row. Also used by `analyze-user-conversations` / `profile-user` when re-analysing the same conversation (typically after a prompt-version change). Not used for `Signals/Conversation Patterns` — that table is write-once (Recipe A2 create-or-skip only), never enriched in place.

1. **Resolve the row.** Fetch the existing page via `notion-fetch` on `page_id`. Confirm its parent data source matches `path`; reject cross-DB updates.
2. **Property merge.** Build the property update payload from `fields`:
   - Only properties present in `fields` are touched; everything else is preserved.
   - Call `notion-update-page` with `command: update_properties`, passing only the updated properties.
3. **Body section replace.** For each entry in `body_replace_sections`:
   - Find the section heading in the existing page body.
   - Replace its contents (everything up to the next heading of same or higher level) with the new markdown.
   - Use `notion-update-page` with `command: update_content` and per-section `old_str` / `new_str` pairs.
   - If a named section doesn't exist in the body, **insert** it just above the `# Sources` block. Never create it in a random place.
4. **Sources append.** For each entry in `body_append_sources`:
   - Append a new line under the existing `# Sources` section in the page body.
   - Preserve existing source lines verbatim — never rewrite or reorder them.
   - If a source with the same URL is already present, skip it (dedupe by URL).
5. **Return** `{ id, url, path, type: "update", updated_fields: [...], updated_sections: [...], appended_sources: [...] }`.

**Guardrails on update specifically:**

- Never modify `## What was tested` (original framing from the source sheet is canonical).
- Never modify `# Sources` entries that already exist — only append.
- Never downgrade required-enum properties to blank values.
- If a section marked for replacement doesn't exist AND the caller didn't specify `allow_insert: true`, fail with a clear error ("Section `## Result` not found in row body; pass `allow_insert: true` to insert").

See the [Experiments schema reference](references/schemas/experiments.md) for the intended update payload shape during `analyze-experiment` enrichment.

---

## Step 4: Confirm and return

Tell the caller:
- What was written (path, ID, URL).
- Which schema validated it.
- Any warnings (e.g. vertical not in the canonical Audience list but accepted anyway).

The caller is responsible for surfacing the write to the user or chaining into another step.

---

## Step 4b: Write-through cache invalidation

After a successful Notion write, **delete** the corresponding cache file so the next `retrieve-knowledge` read refetches fresh. Delete-only — don't try to overwrite with the just-written content (subtle drift between write payload and `notion-fetch` rendering isn't worth chasing). Spec: [retrieve-knowledge/references/cache.md](../retrieve-knowledge/references/cache.md).

- **Learnings row create or update** → delete `${AI_KNOWLEDGE_CACHE_DIR:-~/.cache/ai-knowledge}/learnings__<dbname>.md` (the cached database header page). Per-row caching is intentionally not part of the layout.
- **Signal snapshot create** → delete `${AI_KNOWLEDGE_CACHE_DIR:-~/.cache/ai-knowledge}/signals__<topic>.md` (the cached parent index). Dated snapshots themselves are not cached.
- **Signal observational-table row create** (e.g. `Signals/Conversation Patterns`) → delete `${AI_KNOWLEDGE_CACHE_DIR:-~/.cache/ai-knowledge}/signals__<topic>.md` (the cached parent/header page), same as a Learnings row create. Per-row caching is not part of the layout.

If the cache root or the target file doesn't exist, skip silently. Cache invalidation must NOT fail the write — log a warning and continue.

---

## Rejection reasons

Be explicit when refusing. Pattern:

> Cannot write to `<path>`: <reason>.
> - Allowed section: `Learnings/*` or `Signals/*`
> - Declared writes for calling skill: `<list from frontmatter>`
> - Schema: `<path to schema file>`

Known rejection cases:
- Path outside `Learnings/` or `Signals/` → "this skill only writes to Learnings and Signals databases. For narrative pages, use `ingest-knowledge` or `harvest-knowledge`."
- Path not in caller's `knowledge.writes` → "calling skill has not declared this path. Add it to the `knowledge.writes` block in its SKILL.md frontmatter, or pass `allow_undeclared: true` if this is a direct human invocation."
- Schema violation → itemized list of field errors.
- Snapshot for today already exists → "snapshots are immutable; a snapshot dated `YYYY-MM-DD` already exists at `<URL>`."
- Unknown path → "no schema registered for `<path>`. Add a schema file under `references/schemas/` before writing."
- Schema declares `Writable: no` → "`<path>` is documented here for its column contract but is written by `<owning skills>`, not by this skill. Read `references/schemas/<file>` and write through that skill."
- `action: update` on a Signals path → "Signal snapshots are write-once by design. Create a new dated snapshot instead."
- `action: update` without `page_id` → "action: update requires page_id to identify the row being enriched."
- `action: update` with cross-DB `page_id` → "page_id `<id>` is under `<other-path>`, not `<path>`. Refusing to prevent cross-DB writes."
- `action: update` tries to modify `## What was tested` or other non-allowed section → "only sections `## Result`, `## Learning`, `## Follow-up experiments` are updatable on `Learnings/Experiments`. `## What was tested` is canonical and immutable."
- `action: update` with a section not present in the body and no `allow_insert: true` → "Section `<name>` not found in row body; pass `allow_insert: true` to insert, or check the row structure."
- `action: create` on `Learnings/Conversations` where `Conversation ID` matches an existing row → "duplicate: a row for Conversation ID `<id>` already exists at `<URL>`. Use `action: update` with `page_id: <id>` to enrich it, or skip if the existing analysis is current."

---

## Out of scope

- **Reads** → `retrieve-knowledge`
- **Free-form page edits** → `ingest-knowledge` / `harvest-knowledge`
- **Schema design** → lives in this skill's `references/schemas/` directory; new Signals schemas land per-producer
- **Voice-playbook linting of narrative fields** (future — stretch goal): would check `summary`/`learning` text against banned-words list from [Identity/Brand](https://www.notion.so/34a75e23af2081dab6fde3a3d9f74bf6). Not implemented yet.

---

## References

- [../retrieve-knowledge/references/cache.md](../retrieve-knowledge/references/cache.md) — local cache layer; this skill performs write-through updates per Step 4b
- [references/base-schema.md](references/base-schema.md) — fields shared by every written row/page
- [references/schemas/experiments.md](references/schemas/experiments.md) — `Learnings/Experiments` (stable; documents the Recipe C update payload shape used by `analyze-experiment` enrichment)
- [references/schemas/ad-campaigns.md](references/schemas/ad-campaigns.md) — `Learnings/Ad Campaigns` (placeholder; finalised when the database lands)
- [references/schemas/growth-readings.md](references/schemas/growth-readings.md) — `Learnings/Growth Readings` (stable)
- [references/schemas/conversations.md](references/schemas/conversations.md) — `Learnings/Conversations` (stable; recorded by `data:analyze-user-conversations` / `data:profile-user` with explicit caller approval)
- [references/schemas/conversation-patterns.md](references/schemas/conversation-patterns.md) — `Signals/Conversation Patterns` (`table` kind; **one write-once row per rating event**, recorded by `data:harvest-conversation-patterns`; dedups on `Rating ID` — create-or-skip via Recipe A2, never updated)
- [references/schemas/cancellations.md](references/schemas/cancellations.md) — `Signals/Cancellations` (`table` kind, enriched in place; one row per cancellation event, dedup on `Slack Thread URL`). **`Writable: no`** — the contract for `data:harvest-cancellation-feedback` (creates the row) and `data:enrich-cancellation-feedback` (fills it), both straight to REST. Names which phase owns each property.
- [references/schemas/conversation-topics.md](references/schemas/conversation-topics.md) — `Signals/Conversation Topics` snapshot body structure (produced by `data:fetch-conversation-topics`)
- [references/schemas/aurie-conversations.md](references/schemas/aurie-conversations.md) — `Signals/Aurie Conversations` snapshot body structure (produced by `data:report-aurie-conversations`)
- [references/schemas/aurie-weekly-cost.md](references/schemas/aurie-weekly-cost.md) — `Signals/Aurie Weekly Cost` snapshot body structure (produced by `eng:report-aurie-weekly-costs`)
