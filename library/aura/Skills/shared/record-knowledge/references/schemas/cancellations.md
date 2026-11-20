# Schema — `Signals/Cancellations`

**Kind:** `table` — an observational database, **one row per cancellation event**, dedup on `Slack Thread URL`. Rows are **enriched in place**: a row is created thin by the harvest pass and filled in by a later enrich pass.

**Writable:** `no` — `record-knowledge` refuses every action on this path. The two owning skills write it directly, and `Kind: table` here describes how the table behaves in Notion, not a permission for this skill. See "Written outside `record-knowledge`" below.

**Which conversation path?** Five AI Knowledge paths hold conversation- or feedback-shaped entries and they are **not** interchangeable — each has a different grain, and the same fact belongs in exactly one. Route by grain per [`Guide/Placement Rules` → *Conversation & feedback entries: route by grain*](https://www.notion.so/35a75e23af20812ca987c2bcedf27dd7). This path takes one **cancellation event**.

**Status:** stable. This file is the canonical column contract for the two skills that write the table, and the only place that states which of them owns each property. Both read it; neither can read the other's folder, so a change here is the change.

## Where it lives

- **Notion path:** `AI Knowledge → Signals → Cancellations`
- **Database URL:** <https://app.notion.com/p/fcc2e9da2a1147b99eb5342f1977becd>
- **Data source ID:** `edce288d-5d89-4b4d-8083-e40d0ed7c1ee`

## Producer & consumers

Two-phase, and the phases are separate skills:

1. **`data:harvest-cancellation-feedback`** — sweeps the k8bot cancellation feed in Slack (`#ai-cancellation-feedback`, `#aurie-feedback`) and **creates** a row per new cancellation, with whatever profile fields the Aura API returned at harvest time.
2. **`data:enrich-cancellation-feedback`** — scans for rows with `Sessions` empty and **updates** them in place with the conversation-derived and agent-judgement fields.

Consumers: `data:investigate-cancellation-feedback` (per-entry review), `data:update-cancellations-top10` (the Top 10 synthesis on the parent page), `data:run-cancellations-harvest-cadence` (schedules phase 1 + 2).

## Properties

All [base-schema](../base-schema.md) fields apply. In addition — **`Owner` names which phase writes the field**, because that is the part no single file stated before:

| Property | Type | Owner | Notes |
|---|---|---|---|
| `Title` | title | harvest | `<uid8> — <reason, 35 chars> — <cancel date>`. Keyed as the lowercase property id `"title"` on create — see the title-by-ID gotcha in CLAUDE.md. |
| `User ID` | rich_text | harvest | **A `uid[:8]` prefix, not the full UID** — deliberately unlike `Learnings/Conversations`, which stores the full string. See "On `User ID`" below. |
| `Cancellation Date` | date | harvest | Date of the cancellation event. |
| `Reason` | select | harvest | `Too expensive` · `Not using it enough` · `Didn't help me the way I hoped` · `Technical issues or bugs` · `Just trying it out` · `Other`. Normalised to `Other` when the feed's value is outside the set. |
| `Feedback Text` | rich_text | harvest | The user's typed cancellation reason, verbatim. |
| `Slack Thread URL` | url | harvest | k8bot message permalink. **The natural key** — see "Idempotency". |
| `Creator` | select | harvest | `Agent` · `Human`. Harvest always writes `Agent`; `Human` is for hand-added rows. |
| `Days to Cancel` | number | harvest | Days from purchase to cancellation, 2dp. The two timestamps behind this are **on different clocks** — `Purchased` is a US Pacific wall clock, the cancellation instant is the Slack `ts` in UTC — so it is computed via `parse_purchased` / `PURCHASED_TZ_NAME`, never by subtracting raw. Getting it wrong reads as noise on a long cancel and as a category error on a short one. |
| `Email Domain` | rich_text | harvest | Domain only, never the local part. |
| `Archetype` | select | harvest, enrich | `deepProcessor` · `carryingALot` · `unknown` |
| `Acquisition` | select | harvest, enrich | `organic` · `Scale` · `CreativeTest` · `other` |
| `Age Group` | select | harvest, enrich | `26-35` · `36-45` · `46-55` · `55+` · `unknown` |
| `Support Style` | select | **nobody** | `gentle/supportive` · `practical/grounding` · `encouraging/momentum` · `clear/direct` · `professional/structured` · `unknown`. **Always blank — 0 of 718 rows set (checked 2026-09-16).** The Aura `/users/{uid}` response carries no such field and nothing else provides it, so both skills leave it empty rather than fabricate a value. `harvest.py` still carries a `props["Support Style"] = …` branch, but its profile builder hardcodes `support_style` to `""`, so the branch is unreachable. |
| `Sessions` | number | enrich | Conversation count. **Also the "already enriched" marker** the enrich scan filters on (`Sessions is_empty`) — so it is written last, only once the agent-judgement fields are complete. |
| `Turns` | number | enrich | Total turns across those conversations. |
| `Signal` | rich_text | enrich | Agent-written product signal from the conversation excerpt. |
| `Vertical` | select | enrich | `grief` · `anxiety` · `relationships` · `stress` · `trauma` · `loneliness` · `self_worth` · `parenting` · `work` · `health` · `unknown`. A value outside the set is warned about and **dropped, not written**. |

**Three properties have two owners, and that is intentional.** Harvest writes `Archetype` / `Acquisition` / `Age Group` when the Aura API answered at harvest time, and **omits them otherwise** rather than writing a placeholder — a blank cell is the honest reading of "no profile". Enrich then backfills exactly those blanks. So a row can legitimately carry a profile field before it is enriched, and a permanently blank one means the API never answered for that UID. Measured 2026-09-16: `Archetype` set on 713 of 718 rows, `Sessions` on all 718.

**`Support Style` is owned by nobody, and a dead branch says otherwise.** `harvest.py` contains a write for it; its profile builder five functions up hardcodes the value to `""`, so the branch cannot fire. Trust the table above over either write site: every row in it is checked against the live data source, because the write sites disagree with the data here.

## On `User ID`

`User ID` holds an 8-character prefix, derived by `harvest.py`'s `uid_prefix()` — the same
derivation the rest of the repo uses, not a bare `uid[:8]`. The distinction matters because a
bare truncation assumes the first 8 characters distinguish users, and for a provider-prefixed
id they do not: every `google:112…` yields the literal `google:1`, so two users would share one
value in a durable column and in the row title built from it.

Every stored value is 8 characters with **no colon**: the cancellation feed carries Firebase-28
ids, so the provider branch guards against a shape this table has not yet seen rather than one it
transforms. `tests/test_uid_prefix_rule.py` holds this script to the other three copies of the
derivation and, separately, fails if any plugin script writes a bare-truncated uid into a Notion
property.

## Idempotency

**Natural key: `Slack Thread URL`.** Harvest reads every existing row's URL into a set and skips any entry whose permalink is already present, so re-running over an overlapping window adds only genuinely new cancellations. The key is the *message* permalink, not the UID — a user who cancels twice gets two rows, which is correct: two events.

Enrich is idempotent through `Sessions`: a row with `Sessions` set is never rescanned.

**Dedup degrades loudly, not silently.** Without `NOTION_TOKEN` the harvest dedup check cannot run; it warns and treats every entry as new rather than proceeding quietly.

## Written outside `record-knowledge`

Both writers go straight to the Notion REST API through their own bundled `notion_rest.py`, because each needs behaviour the skill's recipes don't express — harvest needs a full-table URL sweep for its dedup set, enrich needs a property filter plus a two-phase fetch/write split. `record-knowledge` therefore refuses this path on the `Writable: no` declaration above, which its Step 1 checks before anything else, and points the caller here.

**The rule-6 cache bookkeeping does not apply either, and that is worth stating so nobody adds it.** `Guide/Placement Rules` rule 6 requires a direct REST write to replicate what the skills do, cache invalidation included. For this path there is nothing to invalidate: per [`cache.md`](../../../retrieve-knowledge/references/cache.md) the cache stores a database's **header page** only, and filtered database queries bypass the cache entirely. Appending or enriching rows changes no cached artifact.

# Sources

- Live data source schema, read from the Notion API on 2026-09-16 (17 properties, the authority for the types and enum values above).
- [`harvest.py`](../../../../../data/skills/harvest-cancellation-feedback/scripts/harvest.py) — phase 1: the create path, the dedup set, and the conditional profile writes.
- [`enrich.py`](../../../../../data/skills/enrich-cancellation-feedback/scripts/enrich.py) — phase 2: the `Sessions is_empty` scan and the enrich write.
