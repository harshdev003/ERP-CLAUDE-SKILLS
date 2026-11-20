# Aurie Cost Engineering — page contract

The target of every harvest is one Notion page in the AI Knowledge teamspace:

- **Path:** `Learnings/Aurie Cost Engineering`
- **Page ID:** `37b75e23af208189b4e2de18e56546e0`
- **URL:** <https://app.notion.com/p/aurahealth/Aurie-Cost-Engineering-37b75e23af208189b4e2de18e56546e0>
- **Created by:** [PROD-2583](https://linear.app/aura-health/issue/PROD-2583) as the cost-change ledger
- **Downstream consumer:** the finance "Cash flow 2026" page links to it. That page is
  NOT shared with the `Aura AI Knowledge` integration and is never written by this skill.

The page's own footer documents the maintenance contract this skill automates:

> Update path: append a row to Cost Changes when a cost-relevant change ships;
> refresh headline metrics from the daily stores.

## Page layout (top to bottom)

| Section | Block types | Harvest interaction |
|---|---|---|
| Intro + callout | `paragraph`, `callout` | read-only |
| **Headline metrics** | `heading_2` + `bulleted_list_item`s | refreshed via `block_updates` (opt-in step) |
| **The story in N chapters** | `heading_2` + `heading_3`/`paragraph` pairs | narrative proposals via `block_updates` / manual (rare) |
| **Cost Changes** | `child_database` | the ledger — `new_rows` + `row_updates` land here |
| Sources | `heading_2` + bullets | read-only |
| Footer | `paragraph` ("Compiled … Update path: …") | watermark stamped here: `Last harvested: YYYY-MM-DD` |

`fetch_sources.py` returns this map with live block IDs under the `page` key —
never hardcode block IDs other than the page and database IDs.

## Cost Changes ledger schema

Database ID `37b75e23af2081e2b6cbc58e36eaa69c` (the data-source ID is resolved
at runtime and echoed in fetch output as `ledger.data_source_id`).

| Property | Type | Values / format |
|---|---|---|
| `Change` | title | One-line description of the cost change |
| `Area` | select | `LLM`, `Voice`, `Limits`, `Tooling`, `Process` |
| `Status` | select | `Shipped`, `Reverted`, `In progress`, `Idea` |
| `Date` | date | `YYYY-MM-DD` — when the change shipped/reverted (omit for ideas) |
| `Refs` | rich_text | Comma-separated tickets/PRs: `ENGBACK-1136, IntelligenceService#412` |
| `Impact` | rich_text | Expected or measured $ effect + one-line context |

`apply_updates.py` validates `Area`/`Status` against these exact values and
aborts on anything else — Notion would otherwise silently create a new select
option.

### Refs conventions

- Linear: bare identifier — `PROD-2308`, `ENGBACK-1136`, `ENG-690`
- IntelligenceService PR: `IntelligenceService#<number>` (older rows say `IS#<number>`; dedup treats both as the same PR)
- AuraConsumerApp PR: `ACA#<number>`
- Slack thread: the permalink URL

`apply_updates.py` skips a new row whose normalized `Change` title already
exists, and a row that shares a Refs identifier with an existing row **and** has a
similar title (`TITLE_DUP_SIMILARITY`). A shared ref alone is not a duplicate:
related changes cite the same parent ticket, so such a row is added with a
`SHARED REFS` line naming the closest existing row, and counted in the summary.
Always populate Refs.

## Plan JSON schema (`apply_updates.py --plan`)

```json
{
  "new_rows": [
    {
      "change": "Reduce context instructions",
      "area": "LLM",
      "status": "Shipped",
      "date": "2026-06-12",
      "refs": "ENGBACK-1136, IntelligenceService#418",
      "impact": "~$X/day expected from shorter system context"
    }
  ],
  "row_updates": [
    {
      "row_id": "<notion page id of the existing ledger row>",
      "props": { "Status": "Shipped", "Date": "2026-06-12", "Refs": "ENG-639" }
    }
  ],
  "block_updates": [
    {
      "block_id": "<block id from fetch output page.headline_bullets>",
      "type": "bulleted_list_item",
      "text": "May 2026 OpenAI total: $5,445.67 (~$176/day)"
    }
  ],
  "watermark_date": "2026-06-12"
}
```

- All four keys are optional; an empty plan only stamps the watermark.
- `row_updates.props` accepts `Change`, `Area`, `Status`, `Date`, `Refs`,
  `Impact` (display names — the title-by-ID gotcha only applies to creates,
  which `apply_updates.py` handles internally).
- `block_updates.type` must be the block's actual type from the fetch output
  (`bulleted_list_item`, `paragraph`, `heading_3`).
- `watermark_date` defaults to today when omitted.

**Stale block ids under concurrency.** This page is curated by several sessions
at once, so a `block_id` captured in the source fetch may be archived or
replaced by a parallel edit before you apply. `apply_updates.py` pre-checks each
`block_update` target live and **skips** archived / deleted blocks with a
`BLOCK SKIP (… re-fetch sources and re-plan)` note — it never crashes, and the
watermark stamp + cache invalidation always run. A skip means your fetch was
stale: re-run `fetch_sources.py`, rebuild the block edit against the fresh block
id, and re-apply (rows already written are idempotently skipped). `row_updates`
are not pre-checked — a Learnings row is far less likely to be archived mid-run
than a headline block. Origin: [PROD-2695](https://linear.app/aura-health/issue/PROD-2695).

## Classification rubric

What counts as **cost-relevant** when scanning the fetched sources:

- Prompt/model changes that alter token spend: model switches, reasoning-effort
  or verbosity changes, context trimming, prompt-run frequency changes → `LLM`.
- Voice stack changes: ElevenLabs plan/quota moves, TTS routing, voice limits → `Voice`.
- Usage caps, paywalls, trial limits, premium gating → `Limits`.
- Observability/billing tooling: cost dashboards, metadata tagging, log-volume
  reductions, Braintrust/Mixpanel spend → `Tooling`.
- Workflow/process changes: review cadences, monitoring practices, skills → `Process`.

Not cost-relevant (leave out): pure feature work, bugfixes without a spend
angle, routine dependency bumps, conversations that mention cost only in passing.
**One exception to "pure feature work":** a client (AuraConsumerApp/UserWeb)
release that exposes a feature whose backend already sits `In progress` in the
ledger *is* cost-relevant — it's the shipping signal for that precursor. Surface
it even though it reads as feature work, and flip the precursor to `Shipped`
(see "Cost impact requires a user-facing release" below).

**Cost impact requires a user-facing release.** A change only affects spend once
real users hit it — so only a **mobile/web client release** (AuraConsumerApp,
UserWeb) that exposes the change to users can be `Shipped` in this ledger. A
merged backend-only PR (Intelligence Service, a new prompter, an API endpoint)
that no client build calls yet costs nothing today: classify it `In progress`
(a precursor to watch), never `Shipped`, and note in Impact that it flips to
`Shipped` — and cost measurement begins — when the client release lands. Watch
for the backend/client split: an Intelligence Service feature (e.g. Daily
Practices, Jul 2026) can merge days or weeks before the app build that calls it.
Confirm a client release exists before marking any backend-driven feature
`Shipped`.

**Status transitions matter as much as new rows.** Many ledger entries start as
`Idea` or `In progress` — when a source shows the work merged/shipped (PR
merged, Linear issue → Done), emit a `row_update` flipping Status and setting
Date/Refs instead of a duplicate new row. For a backend-only precursor row, the
shipping trigger is the **client release**, not the backend merge — don't flip
it to `Shipped` on the Intelligence Service PR alone.

## Source-specific notes

- **Linear cycle issue.** Titled `AI Cost Review and Changes [Cycle N]`
  (current example: [PROD-2274](https://linear.app/aura-health/issue/PROD-2274),
  umbrella: [PROD-2155](https://linear.app/aura-health/issue/PROD-2155)). The
  fetch picks the most recently updated issue matching the title; override with
  `--issue PROD-XXXX` right after a cycle rollover if needed.
- **GitHub.** `AuraHealth/IntelligenceService` merged PRs are almost all
  prompt/config changes (high signal) — but Intelligence Service is a **backend**
  service, so such a PR alone doesn't move spend until a client build calls it. A
  prompt/model tweak to an *already-live* path is `Shipped` immediately; a *new
  feature's* backend (new prompter, new endpoint) is `In progress` until the
  mobile/web release ships (see "Cost impact requires a user-facing release"
  above). `AuraHealth/AuraConsumerApp` (with UserWeb) is the client side — **two
  kinds of PR survive its filter:** (1) limits/paywall/trial/gating changes, and
  (2) the release that exposes a feature already tracked `In progress` as a
  backend cost precursor. The latter reads as ordinary feature work, so keyword
  filtering misses it — match client PRs against the open precursor rows in the
  ledger, not just the limits/paywall keywords, and treat a match as the signal
  that flips that precursor to `Shipped` and starts cost measurement. Everything
  else in AuraConsumerApp is NOT cost-relevant; filter hard.
- **Slack `#ai-prompt-engineering-cost`** (`C0B3GN41MDX`). Threads discuss
  firefights, spike root-causes, and strategy. A thread is ledger-worthy only
  when it describes a change that shipped/reverted or a concrete decision —
  not ongoing debugging chatter.

## Headline metrics refresh (opt-in step)

The page's headline bullets cite monthly totals and KPI targets sourced from
the daily stores maintained by
[`eng:store-aurie-user-daily-cost`](../../store-aurie-user-daily-cost/SKILL.md).
**BigQuery is the source of truth** — read the numbers from `aura-main.aurie_cost`
(`aurie_per_user_daily_cost` / `aurie_daily_unit_costs` / `aurie_daily_eng_kpis`)
via [`eng:query-bigquery`](../../query-bigquery/SKILL.md) (e.g.
`query.py --sql "SELECT ... FROM aurie_cost.aurie_daily_unit_costs WHERE date BETWEEN ..."`).
When BigQuery is reachable, recompute the headline numbers and emit
`block_updates` for stale bullets; when it isn't (no `GCP_KEY` /
`google-cloud-bigquery`), skip with a note — never guess metric values. (The
local CSVs are just a rebuildable copy — don't source headline metrics from them.)
