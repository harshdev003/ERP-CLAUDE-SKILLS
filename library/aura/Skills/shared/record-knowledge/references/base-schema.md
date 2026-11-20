# Base schema

Fields shared by every row and snapshot written to AI Knowledge. These apply *in addition* to the path-specific schemas in [schemas/](schemas/).

## Required on every write

| Field | Type | Source | Notes |
|---|---|---|---|
| `created_at` | ISO datetime | auto-filled | Set by the skill at write time; never supplied by caller |
| `sources` | list of `{ label, url, primary? }` | caller | See rules below |

> **Note on traceability.** Notion's native `Created by` / `Last edited by` / `Last edited time` fields cover authorship and last-touched provenance. We intentionally don't carry a separate `Created By: Human | Agent` mode axis on rows — see [ENG-404](https://linear.app/aura-health/issue/ENG-404) for the rationale (misleading name, redundant with native fields, not queried downstream).

## Required on leaf rows (`Learnings/*` rows, `Signals/*` snapshots)

| Field | Type | Notes |
|---|---|---|
| `title` | string | Short, search-friendly — appears in Notion sidebars and DB views |

## `sources` rules

Per the [Guide authoring rule](https://www.notion.so/34975e23af20810bac17c192eed68134) "Every page must have a `# Sources` section":

- **Leaf rows and snapshot pages must have a non-empty `sources` list.** Writing a learning with no source is a schema violation.
- **Each source entry:**
  - `label` (string, required) — one-line note on what the source contributes
  - `url` (string, required) — link to the canonical reference (dashboard, PR, upstream doc, PDF, etc.)
  - `primary` (boolean, optional) — mark the parent canonical source as `primary: true`. Zero or one entry may be primary. If `sources` has two or more entries and none is marked primary, emit a validation *warning* (not an error) — multi-source rows without a canonical parent are usually a smell.
- **Rendered as a `# Sources` section** at the bottom of the page body, one line per entry. Primary entries get the `(parent)` suffix per Guide convention.
- **Never a Linear card.** The same Guide rule bans card links in AI Knowledge content, `# Sources` included: a card moves or closes and the citation degrades into a pointer at something that no longer says what it said. Cite the durable canonical thing instead (Notion page, repo file, GitHub PR, vendor docs) and name the ticket inline as text where it adds context. `healthcheck-knowledge` detector #8 scores a card link in Sources as its own finding, so a row written this way is a finding the moment it lands.

Example:

```yaml
sources:
  - label: "Mixpanel dashboard — Aurie onboarding v3"
    url: "https://mixpanel.com/…"
    primary: true
  - label: "AuraConsumerApp#1266 — the shipped implementation"
    url: "https://github.com/AuraHealth/AuraConsumerApp/pull/1266"
```

Renders as:

```markdown
# Sources
- Mixpanel dashboard — Aurie onboarding v3 (parent) https://mixpanel.com/…
- AuraConsumerApp#1266 — the shipped implementation https://github.com/AuraHealth/AuraConsumerApp/pull/1266
```

## Enums shared across schemas

### `vertical`

Pulled from `Identity/Audience` in the AI Knowledge teamspace. Re-fetch the list on first use per session to catch additions. As of the current taxonomy:

`grief`, `anxiety`, `sleep`, `relationships`, `loneliness`, `stress`, `depression`, `self_worth`, `purpose`, `anger`, `trauma`

Values outside this list should emit a validation *warning* (not a hard error) — the Audience list is the source of truth and may drift ahead of this doc.

## Update-mode semantics (Learnings rows only)

`record-knowledge` supports `action: update` on `Learnings/*` rows (not Signals). Key rules governing every update regardless of which schema:

- **Property merge, not replace.** Only properties in the payload `fields` get updated. Omitted properties are preserved.
- **Required fields can't be blanked.** An update cannot set a required property to empty / null. Skip or fail instead.
- **Body section replace, not full-body replace.** Only sections listed in `body_replace_sections` are rewritten. Other sections (notably `## What was tested` for Experiments, or the raw sheet framing for any bulk-imported row) are preserved.
- **Sources append-only.** New entries added via `body_append_sources` go below existing ones. Existing source lines are never rewritten or reordered. Dedup by URL.
- **`created_at` is preserved** across updates — it records creation, not last-edit.

See individual schema files for the per-path allowed update sections (e.g. `Learnings/Experiments` allows `## Result`, `## Learning`, `## Follow-up experiments`).
