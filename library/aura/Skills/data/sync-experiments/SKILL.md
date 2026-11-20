---
name: sync-experiments
description: >
  Bulk-imports the full Aura experiment catalog from the `GET /experiments`
  API into the `Learnings/Experiments` Notion database. Creates row stubs for
  any experiment that doesn't already have one, and updates `End Date` when the
  API shows it ended. Also reconciles what `endedAt` cannot see: an experiment
  whose winner shipped to everyone keeps a null `endedAt` and collapses to one
  variant, so those rows are marked `Stopped splitting` instead of sitting at
  `Running` forever, and every run reports how many rows still disagree with
  the live catalogue. Idempotent. Companion to `data:analyze-experiment`.
  Trigger on "sync experiments", "import experiments to Notion", "refresh
  Learnings/Experiments from the Aura API", "sync the experiment catalog",
  "reconcile the experiment catalog", "which experiments actually stopped
  splitting", "why does this rolled-out experiment still say Running", or a
  question about whether `Learnings/Experiments` is up to date with the live
  experiment server.
metadata:
  version: "3.6.0"
knowledge:
  reads:
    - Guide/Index   # baseline routing aid (universal)
    - Products/Aurie
    - Learnings/Experiments
    - Tools/Aura API                                        # auth + base URL conventions
    - Tools/Aura API/Experiments/List Experiments           # GET /experiments — pagination quirks, targetApp == null means Aura
    - Tools/Slack                                 # Slack post/read conventions + footer format
    - Tools/Notion                                # Notion REST / MCP access pattern (NOTION_TOKEN)
  writes:
    - Learnings/Experiments
---

# sync-experiments

One-shot sync from the Aura experiment server (`GET /experiments`) into the AI Knowledge `Learnings/Experiments` database. Closes the gap between "an experiment exists in production" and "the team can see it in Notion."

## When to use

Trigger when the user says any of:

- "sync experiments"
- "import experiments to Notion"
- "bulk import all aura experiments"
- "refresh Learnings/Experiments from the Aura API"
- "sync the experiment catalog"
- "pull all experiments from the API into Notion"
- "is `Learnings/Experiments` up to date?"

Run on demand any time the team has shipped or ended a batch of experiments and wants the L4 layer caught up. The skill is idempotent — running it again costs nothing if the catalog hasn't changed.

## Inputs

The skill takes no required arguments. Optional flags the user can pass:

- `since=<ISO date>` — only sync experiments where `createdAt >= <date>`. Useful to skip the long tail of pre-Aurie experiments from 2020–2022 on a first import.
- `dry-run` — fetch the catalog and produce the create/update plan, but make no Notion writes. Prints what *would* happen.
- `limit=<N>` — only process the first N catalog rows after sorting by `createdAt` desc. For testing.

If the user didn't pass any flags, sync the full catalog (~660 rows today — see the size note in Step 1 before reading that number as a loss).

## Workflow

### Step 0 — Prerequisites

Confirm:

- `AURA_API_TOKEN` is set in the environment (it lives in `~/.claude/settings.json` for normal Claude Code sessions). If not, prompt the user to add it via `update-config` and stop.
- The user is invoking from outside a Cowork sandbox — Cowork's workspace bash strips host env, so `AURA_API_TOKEN` won't be available there. If detected, fail fast with a pointer to [PROD-1305](https://linear.app/aura-health/issue/PROD-1305).
- **Detect `NOTION_TOKEN`** to choose the dedup path in Step 2. If set, use the REST-first path (one filtered `data_source.query` returns every existing row by `Title` in a paginated call). If not, fall back to the per-row `notion-search` MCP path documented below. Both paths produce identical writes — only the dedup mechanics change. The skill ships its own [`scripts/notion_rest.py`](scripts/notion_rest.py) (each Notion-touching skill carries a self-contained copy — see the root [CLAUDE.md → Notion REST integration](../../../../CLAUDE.md#notion-rest-integration) for setup and the sync convention).

### Step 1 — Fetch the catalog

```bash
curl -A "AuraClaude/$(printf %s "${AINATIVE_UA_WHO:-$(git config --global user.email || git config --global user.name || id -un)}" | sed -E 's/@.*//; s/^[0-9]+\+//; s/[^A-Za-z0-9._-]+/-/g; s/^[-.]+|[-.]+$//g' | grep . || echo unknown)/data:sync-experiments (session ${AINATIVE_SESSION_NAME:-adhoc})" -sS -H "Authorization: Bearer $AURA_API_TOKEN" \
  "https://services.aurahealth.io/experiments?limit=10000" \
  > /tmp/aura_experiments.json
```

**API quirks worth remembering:**

- Default `limit` is `100`. `?limit=N` is the only working query knob — `?offset=`, `?page=`, and `?cursor=` are silently ignored, so always request `?limit=10000` to be sure you got everything.
- The API returns **~660 rows today (2026-09-22)**, down from ~3,000 before the 2026-09-21 archive tranche. The list endpoint filters on `deletedAt IS NULL`, so archiving removes rows from it.

**A shrunken catalogue is not a loss, and the check is not "did the number drop".** `/experiments` is read **in full** by every Aura client on the conversion-critical paywall path — the RN app holds the node with `keepSynced(true)`, UserWeb/AIWeb fetch it on every webview paywall load — so its size is latency and money. [`eng:archive-ended-experiments`](../../../eng/skills/archive-ended-experiments/SKILL.md) therefore moves ended rows from the Firebase `/experiments` node to `/backup/experiments` in tranches, and the catalogue is *meant* to shrink. Expect more tranches; a guard that fires on every one trains whoever runs this to wave it through.

So when the count drops between two runs, distinguish the two shapes before doing anything:

| | Archive-shaped shrink — **benign** | Genuine catalogue loss — **stop** |
|---|---|---|
| `GET /experiments/<id>` for a missing row | **200**, with a `deletedAt` stamp | **404** — the server never held it, or it is truly gone |
| Notion rows for the missing ids | all still there, verdicts intact | missing too |
| Status of the missing rows | terminal (`Won`/`Lost`/…) | some read `Running` |

```bash
# the one call that settles it — a row the LIST endpoint no longer returns
curl -A "AuraClaude/$(printf %s "${AINATIVE_UA_WHO:-$(git config --global user.email || git config --global user.name || id -un)}" | sed -E 's/@.*//; s/^[0-9]+\+//; s/[^A-Za-z0-9._-]+/-/g; s/^[-.]+|[-.]+$//g' | grep . || echo unknown)/data:sync-experiments (session ${AINATIVE_SESSION_NAME:-adhoc})" -sS -H "Authorization: Bearer $AURA_API_TOKEN" \
  "https://services.aurahealth.io/experiments/travelBackgroundSounds"
# 200 + "deletedAt": "2026-09-21T…"  ->  archived. Nothing to do.
```

**Sync cannot lose data to a shrunken catalogue, because sync never deletes.** Every write in Step 4 is a create or a patch; there is no delete path and there never should be. A row that leaves the catalogue keeps its Notion record, its verdict and its body — all 3,087 rows survived the 2026-09-21 tranche with 2,347 of them newly absent from the catalogue. What a shrink actually costs is *enrichment*: those rows will never again be updated from the API, which is fine, because they ended.

The one thing genuinely worth stopping for is a missing row that **reads `Running`** — see Step 4c, where `reconcile.py` probes exactly that set and reports it as a disagreement.

Per-row fields used downstream: `id`, `active`, `createdAt`, `endedAt`, `notes`, `result`, `targetApp`, `targetPlatform`. Other fields (`minVersion`, `maxVersion`, `targetLocales`, `targetTrafficSource`, `preventNewUsers`, `preventOldUsers`, `deletedAt`, `updatedAt`, `rowid`) are ignored on first version — add them later if a downstream skill needs them.

If `since=<date>` was passed, filter the array to `createdAt >= <date>` after the fetch.

### Step 2 — Dedup against the existing Notion catalog

Two paths, same outcome. **Pick path 2a if `NOTION_TOKEN` is set** — it's an order of magnitude faster on full-catalog syncs.

#### Step 2a — REST path (preferred when `NOTION_TOKEN` is available)

One filtered `data_source.query` call paginated to completion gives every existing `Learnings/Experiments` row up front. From the result, build a `{Title → page_id, end_date_set}` map in memory. The Step 3 classification then becomes a dict lookup per catalog row — no per-row Notion call required.

```python
# Pseudocode using plugins/shared/scripts/notion_rest.py
from notion_rest import notion, title_text, date_start

DS = "c0209685-17ac-49e2-849a-85fa8178decd"
existing = {}
for row in notion.query_data_source(DS, page_size=100):
    title = title_text(row, "Title")
    existing[title] = {
        "page_id": row["id"],
        "start_date": date_start(row, "Start Date"),
        "end_date": date_start(row, "End Date"),
        "status": (row["properties"].get("Status", {}).get("select") or {}).get("name"),
    }
```

Then in Step 3, for each catalog row, look up `existing.get(api_row["id"])` instead of running a search. CREATE / CREATE-CLOSED / UPDATE / SKIP classification uses the cached `start_date`, `end_date`, and `status` — no extra fetch needed. The cached `start_date` also lets the Step 6 SKIP re-registration bucket compare the row's *actual* Notion dates rather than re-warning off the API flag alone.

#### Step 2b — MCP path (fallback, used in Cowork or when no token is available)

We deliberately do **not** try to enumerate the whole `Learnings/Experiments` database upfront via MCP. Two reasons:

1. The `query_data_sources` Notion MCP tool isn't always loaded, and `notion-search` against a `data_source_url` caps at 25 results with no cursor — there is no reliable way to walk every row through the MCP layer.
2. Direct REST-API enumeration is the right tool when available (Step 2a), but it needs `NOTION_TOKEN` (an internal-integration secret). Cowork sandboxes don't see host env (PROD-1305), so the MCP fallback is required there.

Instead, MCP-path dedup is done **per-row, immediately before each create**. For every catalog row, search Notion by exact title:

```yaml
notion-search:
  query: "<experiment id>"     # the exact string, e.g. "pricingWebHardPaywall"
  data_source_url: "collection://c0209685-17ac-49e2-849a-85fa8178decd"
  page_size: 5
  max_highlight_length: 0
```

Then look at the returned `results` for an entry where `title` equals the catalog row's `id` **exactly** (case-sensitive — the experiment-server `id` is camelCase and authoritative). Three outcomes:

- **No match** → the row doesn't exist; classify as CREATE.
- **Exact-title match** → the row already exists; capture its `id` (Notion page UUID) and continue to Step 3 to decide CREATE vs UPDATE vs SKIP based on `endedAt`.
- **Multiple exact matches** → a duplicate already slipped in. Abort the run, list the matching page URLs, and ask the user to merge them by hand before re-running. Don't try to auto-resolve — past mistakes deserve human review.

This adds one search per catalog row, which is fine for incremental syncs (a handful of new experiments). For full-catalog sweeps prefer Step 2a — that's exactly the case the REST path was added to handle.

**Why the exact-title check matters:** `notion-search` is semantic, so a query for `pricingWebHardPaywall` will return `pricingWebHardPaywall` first but may include `pricingWebSoftPaywall` or `pricingHardPaywall` as nearby matches. Always filter the results to a strict string-equality check on `title` before treating it as a hit.

### Step 3 — Decide CREATE vs UPDATE vs SKIP per row

**Feature flags are never synced.** A feature flag is stored as an experiment record with one
`active` letter (for example `editEmailEnabled`, Aura's Edit email kill switch). SKIP a record with `featureFlag: true`, or whose `notes` (or `description`) starts with "FEATURE FLAG": no
Learnings row is created or updated, and the report states `skipped.featureFlag: N` with the keys.
`reconcile.py` does the same (bucket `skipped_feature_flag`).

#### Step 3a — Stub detection (cheap signal applied to every row)

Before classifying into CREATE/UPDATE/SKIP, evaluate the **stub heuristic** on the API row. A row is a "stub" — an experiment that was registered but never actually launched — when **all** of:

- `endedAt != null` (i.e. ended), AND
- `0h <= endedAt - createdAt < 24h` (same-day create-and-end — the lower bound excludes re-registration rows, whose `endedAt` precedes `createdAt` and would otherwise satisfy a bare `< 24h` check; those are handled by Step 3a2, not the stub path), AND
- `result` is null or empty after trim, AND
- No `end experiment <name>` AuraBot note in `#support-bot` (`C02FQE15YGP`) within the API window.

The Slack check is the only non-trivial part. **Cache the `#support-bot` end-note set once per sync run**, not per row: at the start of Step 3, search the workspace by **invoking [`shared:post-to-slack`](../../../shared/skills/post-to-slack/SKILL.md)**'s `search` with `query="end experiment"` and a high `--count` (e.g. `1000`, matching the prior `limit=1000` — the default of 20 would truncate the end-note set and mislabel launched experiments as stubs) — or scan the channel directly — and bucket the results by `<name>` so each per-row check is an O(1) map lookup. Workspace search runs `search.messages`, which needs a `SLACK_USER_TOKEN` (a bot token can't run `search.messages`); if the slack skill reports search isn't available, the per-row stub check degrades (rows fall through to the normal CREATE/UPDATE/SKIP path and wait for human / `analyze-experiment` enrichment). That skill owns the connection — MCP-first, with a script fallback that uses `SLACK_USER_TOKEN` for search specifically (the `SLACK_BOT_TOKEN` fallback covers posting and reading, not `search.messages`) — so don't call Slack directly here. Without this cache the heuristic costs one Slack search per catalog row, which is wasteful on full-catalog syncs.

If a row matches all four conditions, mark it `is_stub: true` and carry that flag into Step 3b. Otherwise `is_stub: false`.

The heuristic is intentionally conservative — same-day + null result + no Slack note is a strong "never launched" signal. Anything that fails any one of the four conditions falls through to the normal CREATE/UPDATE/SKIP path and waits for human/`analyze-experiment` enrichment.

#### Step 3a2 — Re-registration detection (cheap signal applied to every row)

Also evaluate the **re-registration flag** on every API row: `is_reregistration = (endedAt != null) AND (endedAt < createdAt)`.

`endedAt` earlier than `createdAt` means the experiment `id` was **re-registered** in the experiment server with a fresh `createdAt` while retaining the original historical `endedAt` from a previous run (observed 2026-07-09: seven Aura experiments carried a 2026-07-06 `createdAt` but a 2022–2023 `endedAt`). `createdAt` is therefore not the true start — writing it as `Start Date` produces a row where `Start Date > End Date` (negative duration), which reads as a brand-new experiment when sorted by start and breaks any downstream window math (`analyze-experiment` can't query a Mixpanel window that predates the row's own start, and Aura experiments this old predate the `Experiment:` super-property).

When `is_reregistration == true`, carry the flag into Step 3b/4 so the row gets a sensible `Start Date` (Step 4a), backfills `End Date` from the accurate historical `endedAt`, and is surfaced in the report (Step 6). The true start isn't recoverable from the API and `Start Date` is a **required create field** (can't be blank), so **set `Start Date` equal to the historical `endedAt` (i.e. `Start Date == End Date`)** rather than writing the misleading re-registration `createdAt`. That yields a zero-duration row instead of a `Start Date > End Date` one and still passes create validation; the body caveat records that the true start predates it and is unknown.

`is_reregistration` and `is_stub` are mutually exclusive by construction — the stub heuristic (Step 3a) requires `0h <= endedAt - createdAt`, which a re-registration row (negative duration) never satisfies. A re-registration row is therefore never auto-closed as a "never launched" stub, never lands in `CREATE-CLOSED` or a stub `UPDATE`, and never receives the same-day "never launched" `## Learning`; it's a real legacy experiment that ended, and its readout comes from `analyze-experiment` (or the server `result`), not the stub boilerplate.

#### Step 3b — Classify

After the per-row Step 2 search, classify the catalog row into one of four buckets:

1. **CREATE** — Step 2 found no exact-title match AND `is_stub == false`. Stub the row (Step 4a).
2. **CREATE-CLOSED** — Step 2 found no exact-title match AND `is_stub == true`. Create the row with `Status: Turned off` and an auto-classified `## Learning` (Step 4a, stub variant).
3. **UPDATE** — Step 2 found an exact-title match AND either:
   - The existing row's `End Date` is blank AND the API's `endedAt` is set, OR
   - The existing row's `Status` is blank AND `is_stub == true` (retro-close a previously-imported stub).

   Patch the row (Step 4b). Stay narrow: do *not* flip `Status` from `Running` → terminal *unless* `is_stub == true`. Setting the terminal state in the general case (`Won` / `Lost` / `Inconclusive`) requires Slack + Mixpanel context that this skill doesn't fetch — that's `analyze-experiment`'s job. The stub case is the one exception, because the heuristic is strong enough to classify confidently.

   To check the existing row's properties, fetch the matching page (`notion-fetch` with the page UUID) and inspect `End Date` and `Status` — and also `Start Date` when the API row is a re-registration (`is_reregistration == true`), so the SKIP comparison below works on the MCP path too. One extra fetch per match is acceptable — matches are rare relative to creates on a fresh sync.

4. **SKIP** — exact-title match exists and the existing row is already consistent with the API (End Date set, Status non-blank or row is genuinely Running). For SKIP rows, compare the row's **actual Notion dates** — `Start Date` and `End Date` from the Step 2a cache on the REST path, or from the per-match `notion-fetch` above on the MCP path: if `Start Date > End Date`, the row carries a stale re-registration start (e.g. a row synced before this guard) — flag it in the report's SKIP re-registration bucket (Step 6) even though no write happens. Gate on the real dates, **not** on the API `is_reregistration` flag alone: a row this guard already created correctly (`Start Date == End Date`) or a human has fixed has consistent Notion dates and must not be re-warned on every subsequent run.

   **MCP-path note:** on the REST path (Step 2a) `Start Date`/`End Date` come from the upfront cache. On the MCP path (Step 2b) there is no upfront cache, so read `Start Date` in the same per-match `notion-fetch` used for UPDATE detection whenever `is_reregistration == true` — that fetch already runs for every exact-title match, so the SKIP re-registration comparison costs nothing extra there.

Print the running plan summary as you go (each row resolves in real time):

```
[ 1/47] testAurieOnboarding             CREATE
[ 2/47] quickProcessingResponseAPI      CREATE
...
[18/47] communityFreeEventPostExperience CREATE-CLOSED (stub: same-day, null result)
...
[34/47] pricingWebHardPaywall           SKIP (exists, running)
[35/47] autoplayPlayer                   CREATE (⚠ re-registration: ended 2022-11-30 < created 2026-07-06 — Start Date set to end date)
...
Plan: 33 CREATE, 2 CREATE-CLOSED, 1 UPDATE, 11 SKIP  (3 re-registration rows)
```

Append a `⚠ re-registration` marker to any CREATE/UPDATE row where `is_reregistration == true`, and to any SKIP row whose existing Notion dates are inconsistent (`Start Date > End Date`) — matching the Step 6 report buckets. Add the count to the plan tally line so the user sees them before applying.

**Always dry-run first on the very first introduction of the stub heuristic.** Run with `dry-run` and report the CREATE-CLOSED + UPDATE-stub counts to the user before applying. The point is to sanity-check the heuristic against the live catalog (e.g. confirm it doesn't sweep up legitimate fast-iteration experiments that happened to start and end within 24 hours).

If `dry-run`, stop here and return the plan without writing.

### Step 4 — Apply the plan

#### Step 4a — CREATE stubs

For each row in the CREATE bucket, build the payload below and call `shared:record-knowledge` (`action: create`, `path: Learnings/Experiments`).

**Field mapping:**

| Notion field | API source | Notes |
|---|---|---|
| `Title` | `id` | Exact, case-sensitive — this is the dedup key |
| `App` | derived from `targetApp` | `Aurie` if `targetApp == "ai"` (server-side codename for the AI-app surface — Aurie). `Aura` if `targetApp` is null/unset (legacy default). Anything else falls back to `Aura` and gets logged as a warning so the mapping table can be revisited. **Verified against live data 2026-04-27**: 44/47 rows since 2026-01-01 had `targetApp == "ai"`, 3 had `null`. The string `"aurie"` does *not* appear in production — don't match on it |
| `Platform` | derived from `targetPlatform` + `App` | Both apps have web and mobile surfaces, so the Platform value is a combination of App and the API's `targetPlatform`. Mapping: `App=Aurie + targetPlatform=web` → `Aurie Web`; `App=Aurie + targetPlatform in {ios, android, mobile, null}` → `Aurie Mobile` (null defaults to mobile for Aurie); `App=Aura + targetPlatform=web` → `Aura Web`; `App=Aura + targetPlatform in {ios, android, mobile, null}` → `Aura Mobile` (null defaults to mobile for Aura). The Platform enum was extended in 2026-04 to add `Aura Mobile` and `Aura Web` alongside the existing `Aurie Mobile` / `Aurie Web` |
| `Experiment Type` | — | leave blank; this requires human judgment or analysis |
| `Status` | `endedAt` + stub flag | `endedAt == null` → `Running`; `is_stub == true` → `Turned off` (CREATE-CLOSED bucket); else leave blank for `analyze-experiment` to set the terminal state. A create for an experiment that has already stopped splitting lands on `Running` here and is corrected by Step 4c on the same run — the create path deliberately stays a straight read of `endedAt` rather than growing a second rule |
| `Start Date` | `createdAt` | ISO date portion. **Exception:** when `is_reregistration == true` (Step 3a2), set it to the `endedAt` ISO date (equal to `End Date`) instead — `createdAt` is a re-registration date (writing it would make `Start Date > End Date`), and `Start Date` is required on create so it can't be left blank |
| `End Date` | `endedAt` | ISO date portion if set; else leave blank. Set even when `is_reregistration == true` — the historical `endedAt` is accurate |
| `Hypothesis` | — | leave blank; populated by `analyze-experiment` from the matching Linear ticket |
| `Variants` | `notes` | Arm names, from `python3 ${CLAUDE_PLUGIN_ROOT}/skills/sync-experiments/scripts/variants.py` (`variants_value(notes)`): one `a: <name>` line per arm when the notes name every arm from `a` in order ("a: new hero copy, b: control, current copy"). Otherwise **leave `Variants` empty** for `analyze-experiment`. **Never write bare letters** (`a:\nb:`): they read as arms with no description. The API has no variant field, and `active` is only the letters still splitting |

**Body sections:**

- `## What was tested` — if the API row has a non-empty `notes` field, mirror it verbatim under a brief framing line; otherwise the placeholder `_To be added — see Linear ticket or Slack `create experiment <name>` thread._`. The API's `notes` is short, often a one-line description from the engineer who registered the experiment. When `is_reregistration == true`, append a one-line caveat: `_Legacy experiment: ended <endedAt> but re-registered in the catalog <createdAt>; Start Date is set to the end date because the true start predates it and is unknown. Mixpanel per-variant may be unavailable this far back._`
- `## Result` — if the API row has a non-empty `result` field, mirror it verbatim; otherwise `_Awaiting end of experiment._`. Note: the API `result` is a free-text field set manually on the experiment server — treat it as a hint, not authoritative. `analyze-experiment` overwrites this section with Mixpanel-derived metrics on enrichment.
- `## Learning` — for stubs (`is_stub == true`), the literal line: `Auto-classified as Turned off at sync time — same-day start/end with no result recorded.` Otherwise the placeholder `_To be added via `analyze-experiment` enrichment._`
- `## Follow-up experiments` — for stubs, leave the placeholder; the stub itself isn't a learning to chase. Otherwise `_To be added via `analyze-experiment` enrichment._`
- `# Sources` — single entry: `[Aura experiment server](https://services.aurahealth.io/experiments)`. `analyze-experiment` appends Mixpanel / Slack URLs later (never a Linear card link — `Guide` → Authoring rules bans those in `# Sources`).

#### Step 4b — UPDATE existing rows

Two sub-cases share this bucket:

**End-date-only update** (the existing row's `End Date` was blank but the API now has `endedAt`):

```yaml
action: update
path: Learnings/Experiments
page_id: <Notion page UUID returned by the Step 2 exact-title match>
fields:
  End Date: <ISO date from API endedAt>
```

**Stub-retro-close** (existing row has `Status` blank AND `is_stub == true`):

```yaml
action: update
path: Learnings/Experiments
page_id: <Notion page UUID returned by the Step 2 exact-title match>
fields:
  Status: Turned off
  End Date: <ISO date from API endedAt>      # only if previously blank
body_replace_sections:
  "## Learning": |
    Auto-classified as Turned off at sync time — same-day start/end with no result recorded.
```

In both cases, no source appends — the original `# Sources` block (the API URL) is sufficient. Keep the touch surface as small as possible: the signal is "this row is done." Anything richer is `analyze-experiment`'s territory.

**Re-registration guard on the End-date-only update.** When `is_reregistration == true` (Step 3a2), the backfilled `End Date` (the historical `endedAt`) can land *before* the existing row's `Start Date` — producing a negative-duration row. Still write the `End Date` (it's accurate), but do **not** silently leave the row inconsistent: surface it in the Step 6 report's re-registration list so a human can blank/correct the stale `Start Date`. Don't overwrite `Start Date` here — this skill doesn't know the true start, and clobbering a human-entered value would lose real data.

#### Step 4c — Reconcile what `endedAt` cannot see

`endedAt` is not the only way an experiment finishes. When a variant ships to
everyone the server never sets it — it collapses `active` to a single variant
instead. Every step above keys on `endedAt`, so those rows are not late, they
are **invisible**: 133 of them sat at `Status: Running` on 2026-09-17, and
running the sync more often would never have found one.

Run the bundled reconcile after the plan is applied:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/sync-experiments/scripts/reconcile.py report
python3 ${CLAUDE_PLUGIN_ROOT}/skills/sync-experiments/scripts/reconcile.py apply
```

`report` is read-only and prints the disagreement count whether or not it is
zero. It also **probes the provenance** of every Notion row that is absent from
the live catalogue *and* still reads `Running` or `In flight`, via
`GET /experiments/<id>`: the list endpoint hides soft-deleted rows but the per-id
endpoint still answers, so `200` means the server sourced the row and later
deleted it (a real disagreement — Notion calls live something the catalogue has
removed) and `404` means the server never held it at all (a row for something run
outside the experiment server — `AurieInvitePushCopy`, `App Store Screenshots
(Round 3)` — which is nobody's contradiction). That is a handful of calls per run.
Pass `--probe-orphans` to probe every absent row instead, which resolves the
untested bulk into archived vs never-held at one call each (2,425 on 2026-09-22).

**Absence from the catalogue is never evidence about origin, and the report may
only say what it has tested.** A row that was not probed is reported as
*untested*, never as unsourced; a probe that fails to answer counts as untested
too, because a check that could not run must not look like a check that passed.
An untested row that reads `Running` therefore stays in the headline. Archiving
moves thousands of rows out of the catalogue at once, so a rule that infers
"never sourced" from absence is wrong for almost all of them. `apply` makes exactly two writes and no third: it sets
`Status: Stopped splitting` on rows the catalogue shows have stopped while
Notion still calls them `Running` (or `In flight`, or blank), and it **withdraws
that same marker back to `Running`** on any row that has started splitting
again. Pass `--dry-run` to see the plan first.

The withdrawal is safe for the reason the marking is: it only ever touches this
script's own reading of the catalogue. A verdict, a human `Turned off`, or a
`Feature flag` on a row that resumed splitting is **reported and left alone** —
somebody's judgement is not a stale status. Without that bucket a resumed row
would classify as agreement (no `End Date`, no verdict) and the stale
`Stopped splitting` would never be reported or corrected — the same failure this
step exists to end, pointed the other way.

**`Stopped splitting` is a state, never a verdict.** One arm covers at least
five situations — a rollout, a variant forced before release, a release still
pending, a kill by an unreachable `minVersion`, and a permanent feature flag —
and `result` is empty in all of them while `notes` records intent rather than
state. Which one it is, is a fact only a person holds. Reading it as `Won`
would have marked `chatImageUpload` a win the day after it was created.

So the script never writes a verdict, never touches a row a human or
`analyze-experiment` already answered, and **never writes an `End Date`**:
`updatedAt` is a last-touch stamp (median 916 days stale across these rows) and
a feature flag has no end at all, so a date written here would be a fabrication
that then drives `analyze-experiment`'s Mixpanel window.

Two follow-on states a **person** sets, which the script then leaves alone
forever:

| Set by | Value | Means |
|---|---|---|
| `analyze-experiment`, or a human | `Won` / `Lost` / `Inconclusive` / `Turned off` | resolved — the experiment had an outcome |
| a human, once | `Feature flag` | not an experiment. A kill switch kept live on purpose; it will never produce a verdict and must never be offered for resolution again |

**Resolving a row must land on a terminal value, never `In flight`.** These rows
have a null `endedAt` by definition, which is exactly what a reader of `endedAt`
alone turns into `In flight` — and `In flight` is in this step's writable set, so
the next pass returns the row to `Stopped splitting` and the two alternate
forever. An oscillation reads as drift rather than as the collision it is.
[`data:analyze-experiment`](../analyze-experiment/SKILL.md) Step 1d checks
`active` before calling a null `endedAt` running, for this reason.

A row whose `active` **repeats a letter** (`newPricingPage` and `placeholder`
carry `"bb"` — two characters, one distinct arm) is held: the catalogue does not
say whether that means one arm or two, so it is listed in the report every run
and left exactly as it is.

### Step 5 — Cache invalidation

`record-knowledge` does its own cache invalidation per write (`learnings__experiments.md` deleted on every successful create or update). No additional work needed here — but verify the file is gone after the run as a sanity check:

```bash
ls "${AI_KNOWLEDGE_CACHE_DIR:-$HOME/.cache/ai-knowledge}/learnings__experiments.md" 2>/dev/null \
  && echo "WARN: cache file still present" \
  || echo "cache invalidated"
```

### Step 6 — Report

Print a summary:

```
sync-experiments: <N created>, <S auto-closed stubs>, <M updated>, <R retro-closed stubs>, <K already in sync>
- CREATE failures: <list of ids that failed validation, with reason>
- UPDATE failures: <list>
- ⚠ Re-registration rows (endedAt < createdAt), newly created: <list of ids> — Start Date set equal to End Date (the historical endedAt; true start unknown).
- ⚠ Re-registration rows (endedAt < createdAt), existing rows updated: <list of ids> — End Date backfilled from the historical endedAt; Start Date left as-is and may be a stale re-registration date (correct by hand).
- ⚠ Re-registration rows (endedAt < createdAt) already in sync (SKIP) whose Notion `Start Date > End Date`: <list of ids> — no write this run, but the existing row still carries a stale re-registration Start Date (e.g. the PROD-3445 rows synced before this guard); correct by hand. Omit rows whose Notion dates are already consistent (`Start Date <= End Date`) so a corrected row stops appearing on later runs.
- Review the experiment-server catalog if any re-registration rows weren't expected.
- Plan completed in <elapsed>s

Catalogue vs Learnings/Experiments — <N> row(s) disagree.
  stopped splitting, still reads Running   <n>   <- reconcile `apply` writes these
  ended, Notion still says Running         <n>   <- needs `analyze-experiment`
  splitting again, still marked stopped    <n>   <- reconcile `apply` withdraws these
  ...
```

**Always print the reconcile block, including when it is zero.** A run that
reports "nothing newly ended" while 173 rows disagree with the catalogue looks
exactly like a healthy one — that is the failure PROD-6116 recorded, and a
count only printed when non-zero reproduces it the moment someone stops
noticing its absence.

Split the re-registration rows by bucket, because the three paths leave `Start Date` in different states: **CREATE** rows get `Start Date == End Date` (Step 4a — required field, set to the historical `endedAt`); **UPDATE** rows keep whatever `Start Date` the existing row already had (Step 4b never overwrites it); **SKIP** rows aren't written at all, but a pre-guard sync may have left them with a `Start Date > End Date`. The CREATE and UPDATE buckets key off the API `is_reregistration` flag (those are the rows written this run); the **SKIP** bucket instead keys off the row's **actual Notion dates** (`start_date > end_date` — from the Step 2a cache on the REST path, or the per-match `notion-fetch` on the MCP path), so a row this guard already created correctly, or one a human has fixed, drops out of the warning on later runs rather than being re-flagged forever. Emit only the buckets that are non-empty; omit the whole block when nothing qualifies.

If any writes failed, leave the run in a partial-success state (don't roll back successful writes) and surface the failures to the user. Re-running the skill is the recovery path — failed writes will retry automatically since the dedup key (`Title`) is intact.

No persistent state is written between runs — per-row dedup means each invocation re-checks Notion for every catalog row, so there's nothing for a "last count" file to protect.

## Idempotency guarantees

Re-running with no upstream catalog change must produce `0 created, 0 updated, all-rows in sync`. This is the contract. Test it as part of any change to this skill:

1. Run once → expect non-zero creates.
2. Run again immediately → expect `0, 0, all`.

If the second run reports any writes, there's a bug in the dedup or update-detection logic.

## Field-mapping rationale

A few choices in the field mapping deserve a note since they're not obvious:

- **`App` defaulting to `Aura` when `targetApp` is null** — this matches the experiment server's convention. The natural read of `targetApp: null` is "unspecified," but server-side it means the Aura mobile app. Confirmed with Jerome 2026-04-27.
- **`Status` left blank (not `Inconclusive`) when `endedAt` is set** — the API's `result` field is free-text and unreliable for inferring `Won` / `Lost`. Leaving `Status` blank tells `analyze-experiment` the row needs analysis; setting it prematurely would mask the "needs analysis" signal. The one exception is the stub case (Step 3a) — same-day + null result + no Slack note is a strong enough "never launched" signal that we can safely classify as `Turned off` without burning an `analyze-experiment` cycle.
- **`Variants` from the notes, or empty.** The API has no variant field; the names live in the free-text `notes`, and `variants.py` reads them deterministically. Bare letters (`b:`) read as arms whose descriptions were lost, so they are never written. `python3 ${CLAUDE_PLUGIN_ROOT}/skills/sync-experiments/scripts/variants.py repair [--title <experiment> …] [--apply]` fixes rows that carry them: names from the notes, else cleared. Without `--title` it lists every such row; clear legacy rows in bulk only on Jerome's say-so.
- **`## Result` mirroring API `result`** — the API field is sometimes informative (e.g. `"a/b tied on trial-paid"`) and sometimes empty or stale. Mirroring it is cheap and gives readers something to anchor on before enrichment. `analyze-experiment` always overwrites this section, so there's no drift risk.
- **`Start Date == End Date` on re-registration (`endedAt < createdAt`)** — a re-registered experiment carries a fresh `createdAt` but a stale historical `endedAt`, so `createdAt` isn't the true start; writing it would produce a `Start Date > End Date` row that reads as brand-new and breaks downstream window math. `Start Date` is required on create so it can't be blank — setting it to the historical `endedAt` (a zero-duration row) plus the accurate `End Date`, the body caveat, and a report warning is the honest shape; the true start isn't recoverable from the API. See [PROD-3445](https://linear.app/aura-health/issue/PROD-3445).

## Open behavior the user can override

- **Date filter.** The default is "sync everything." Most pre-2024 experiments are pre-Aurie and have no Mixpanel data — they bloat the DB. If the user wants the legacy stuff filtered, they pass `since=2024-01-01`.
- **Update boundary.** This skill currently only patches `End Date` on existing rows. It deliberately does NOT update `Status`, `Variants` (except `variants.py repair`, which only replaces bare letters), or any body section — those require richer context that `analyze-experiment` is set up to gather. If a future need arises (e.g. backfill `Variants` letter placeholders for legacy rows), add a separate flag rather than widening this skill's update surface.

## Cowork caveat

The `mcp__workspace__bash` tool inside Cowork strips host env vars including `AURA_API_TOKEN` (see [PROD-1305](https://linear.app/aura-health/issue/PROD-1305) and `/CLAUDE.md` → "Known Cowork constraints"). Step 0 detects this and refuses gracefully — there is no graceful-degradation path for this skill since the API call is the entire point. The workaround (paste the token into the workspace bash session manually) works but is per-session; document it for the user if they hit it.

## Related skills

- [`data:analyze-experiment`](../analyze-experiment/SKILL.md) — enriches the stubs this skill creates with Mixpanel actuals, hypothesis from Linear, and synthesised learnings.
- [`shared:record-knowledge`](../../../shared/skills/record-knowledge/SKILL.md) — does the actual Notion writes. This skill is a thin orchestrator on top.
- [`shared:retrieve-knowledge`](../../../shared/skills/retrieve-knowledge/SKILL.md) — used in Step 2 to load existing rows for dedup.
- [`data:sync-mixpanel-events`](../sync-mixpanel-events/SKILL.md) — sister skill, same shape but for Mixpanel metadata. Naming and structure intentionally parallel.
- [`data:monitor-all-experiments`](../monitor-all-experiments/SKILL.md) — reads the same catalogue and the same `active` signal for the running-set table; its `resolve_running.py` and this skill's `reconcile.py` take deliberately opposite sides on a repeated-letter `active` (it shows the row, this holds it), pinned by [`tests/test_reconcile_experiments.py`](../../../../tests/test_reconcile_experiments.py).

## References

- API endpoint: `https://services.aurahealth.io/experiments` (full schema in [`Tools/Aura API`](https://www.notion.so/34c75e23af208102b7dcd0d53a5776f7) on Notion).
- Schema: [`record-knowledge/references/schemas/experiments.md`](../../../shared/skills/record-knowledge/references/schemas/experiments.md).
- Linear: [ENG-422](https://linear.app/aura-health/issue/ENG-422), [PROD-1330 — auto-close zero-duration stubs](https://linear.app/aura-health/issue/PROD-1330), parent [PROD-1165 — Global Knowledge System for AI Agents](https://linear.app/aura-health/issue/PROD-1165), [PROD-6116 — a rolled-out experiment is invisible to `endedAt`](https://linear.app/aura-health/issue/PROD-6116).
