# Signal schemas

Shape of pages under `Signals/*`. Unlike Learnings (DB rows), each Signal snapshot is its own Notion page with a dated title. The schema is the page body structure that producers (like `data:report-aurie-conversations`) agree to follow.

## Shared base

Every snapshot page, regardless of signal type:

- **Title format:** `<Producer> — YYYY-MM-DD snapshot` (e.g. `Braintrust Topics — 2026-04-22 snapshot`).
- **Header `At a glance`** — one-paragraph summary with the top figure(s).
- **`## Window`** — explicit time range the snapshot covers (`Queried:`, `Effective:`, `Pulled:` lines).
- **Body** — one or more tables of observed values.
- **`# Sources`** — required, per Guide authoring rules. Points to the upstream dashboard / API that produced the numbers.

Callers should treat each snapshot page as immutable once written.

## Querying signals (Recipe C)

A snapshot parent page lists its child snapshot pages. Filter by the date in the title:

- `since` / `until`: compare against the date extracted from the title.
- Sort descending; default `limit` = 1 (latest).
- Return each page body verbatim — callers parse the tables themselves.

## Future signal types

As new Signals subsections come online (NPS, error rates, funnel snapshots), add their body structure here. Each one:

1. Has its own parent page under `Signals/`.
2. Uses dated child pages for snapshots.
3. Agrees on a body schema (headings + tables) so `retrieve-knowledge` can list them without parsing ambiguity.

If the caller requests a `Signals/*` path that isn't documented here, return the raw page body and note that the schema isn't known locally.
