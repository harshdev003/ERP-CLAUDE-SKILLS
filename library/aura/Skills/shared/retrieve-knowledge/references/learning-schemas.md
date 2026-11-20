# Learnings database schemas

Shape of rows in the `Learnings/*` databases. Use these to build filters for Recipe B and to validate what you got back before handing it to the caller.

**Status:** `Experiments` and `Growth Readings` schemas are finalised as implemented; `Ad Campaigns` is still a placeholder pending [PROD-1215](https://linear.app/aura-health/issue/PROD-1215).

## Shared base fields

Every learning row, regardless of database, carries these:

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | Notion page ID | auto | — |
| `created_at` | datetime | auto | — |
| `vertical` | enum | yes | From `Identity/Audience` vertical list |
| `title` | string | yes | Short, search-friendly |
| `summary` | string | yes | 1–2 sentence context |
| `learning` | string | yes | The takeaway — what we actually learned |
| `sources` | list of `{label, url, primary?}` | yes | Provenance per Guide authoring rules |

## `Learnings/Experiments`

A/B tests and feature flag rollouts across Aura and Aurie surfaces. One row per experiment. Bulk-imported from the [Aurie AI AB Experiments Google Sheet](https://docs.google.com/spreadsheets/d/1XsxniLks_r5wELsrcFjujZha1qeMAnFuMQTprLXoFB8/edit?usp=sharing) as the operational source of truth; `data:analyze-experiment` enriches rows in place with Mixpanel actuals, synthesized learnings, and follow-up ideas.

**Properties:**

| Field | Type | Required | Notes |
|---|---|---|---|
| `Title` | string | yes | Experiment identifier as used in Slack / Linear / Mixpanel (e.g. `hardPaywall`, `newHomeOnboarding`) |
| `App` | select | yes | `Aura` \| `Aurie` |
| `Platform` | select | yes | `Aurie Mobile` \| `Aurie Web` \| `Core AI` \| `App Store` \| `CRM` \| `Desktop` \| `Email` |
| `Experiment Type` | multi-select | no | Any combination of `Core feature`, `Onboarding`, `Conversation`, `Subscription`, `Email`. Blank on most rows — only set when the creating source carries a type; nothing back-fills it. Filtering on it silently excludes the blanks |
| `Status` | select | no | `Running` \| `Won` \| `Lost` \| `Turned off` \| `Inconclusive` — blank if not inferable |
| `Start Date` | date | yes | |
| `End Date` | date | no | Blank if still running |
| `Hypothesis` | text | no | Often blank in source sheet |
| `Variants` | text | no | Short summary of all variants — one line per variant (e.g. `a: Unskippable paywall` / `b: Control`). Scales from 2 to N variants; the CRM/email experiments with `Control A / Variant B / Variant C / Variant D` fit cleanly. Full variant detail lives in the page body under `## What was tested`. |

**Page body** holds the narrative, matching [`analyze-experiment` Step 7 output](../../../../data/skills/analyze-experiment/SKILL.md) so enrichment updates the same sections cleanly:

```markdown
## What was tested
## Result
## Learning
## Follow-up experiments

# Sources
```

Example filters callers will pass:
- `{ Platform: "Aurie Mobile", Status: "Won" }`
- `{ Experiment Type: contains "Subscription" }`
- `{ Status: "Won", End Date: { since: "2026-01-01" } }` — wins from this year
- `{ End Date: { since: "2026-01-01" } }`

## `Learnings/Ad Campaigns`

Extends the base with:

| Field | Type | Required | Notes |
|---|---|---|---|
| `creative_ref` | string | yes | Link or ID pointing to the creative asset |
| `channel` | `meta` \| `tiktok` \| `google` \| `youtube` \| `reddit` | yes | — |
| `spend_usd` | number | yes | Total spend |
| `ctr` | number | yes | Observed CTR |
| `cpa_usd` | number | yes | Observed CPA |
| `winning_variant` | string \| null | no | Fill in on retro if a variant test ran |

Example filters:
- `{ channel: "meta", vertical: "anxiety" }`
- `{ since: "2026-03-01" }`

## Validation rules

Before returning rows to the caller:

1. Each row must have all base required fields filled. If a row is malformed, include it in an `errors` array rather than dropping it silently.
2. `vertical` should match one of the values in `Identity/Audience`. If it doesn't, flag as a validation warning but still return the row.
3. `sources` may be an empty list on index/rolling pages, but never on leaf learning rows. A leaf learning without a source is a schema violation.

## `Learnings/Growth Readings`

Distilled, agent-facing learnings from growth-related videos, podcasts, and articles the team has read. One row per source. The full narrative summary lives in the marketing [Growth Readings](https://www.notion.so/2aa75e23af20801ebfbcf40645cb7469) database (outside AI Knowledge); this DB is the agent-discoverable mirror.

**Unlike Experiments / Ad Campaigns, this is not from a decision we made** — it's learnings the team extracted from external content. Still write-once, still reference-forever, so it fits the Learnings type.

Schema extends the base with:

| Field | Type | Required | Notes |
|---|---|---|---|
| `title` | string | yes | Video / podcast / article title |
| `overview` | string | yes | 3–5 sentence summary, stored as a DB property AND repeated at the top of the page body |
| `source_url` | URL | yes | Canonical YouTube / podcast / article URL |
| `growth_readings_page` | URL | no | Cross-link to the full narrative in the marketing Growth Readings DB |
| `date_published` | date | no | When the source was originally published |
| `detailed_learnings` | markdown | yes | Lives in the **page body**, not a property. Preserves `### N. <insight>` subsections with quotes and `**For Aura:**` implications. |

**Self-contained rows.** `overview` and `detailed_learnings` are both stored on the row itself — one as a property, one as the page body. Agents querying this DB via Recipe B get everything needed in a single fetch; the `growth_readings_page` cross-link is provenance, not a data dependency.

Example filters:
- `{ source_url__contains: "youtube.com" }` — YouTube videos only
- `{ since: "2026-01-01" }` — recent readings
- (No `vertical` filter — this DB drops the vertical field since most growth content doesn't map cleanly to an Aurie vertical.)

## `Learnings/Aurie Cost Engineering/Cost Changes`

The Aurie cost-change ledger — one row per shipped/reverted/planned change to AI cost, with the
measured impact. Written by `eng:harvest-cost-changes` (plan-before-apply); read by
`eng:report-aurie-daily-costs` Step 3 to decide whether a flagged ⚠ cost spike is already
accounted for before escalating it.

Data source: `37b75e23-af20-8181-b293-000b74738ff2` (database `37b75e23-af20-81e2-b6cb-c58e36eaa69c`,
on the `Learnings/Aurie Cost Engineering` page).

**Properties:**

| Field | Type | Required | Notes |
|---|---|---|---|
| `Change` | title | yes | What changed, one line (e.g. `Prompt-testing spend bills into Conversation [Production]`) |
| `Area` | select | yes | `LLM` \| `Voice` \| `Limits` \| `Tooling` \| `Process` |
| `Status` | select | yes | `Shipped` \| `Reverted` \| `In progress` \| `Idea` |
| `Date` | date | yes | When the change (or its diagnosis) landed — **not** the date of the cost day it explains |
| `Impact` | rich_text | yes | The measured effect, and where a specific cost day is attributed it is named here |
| `Refs` | rich_text | no | Slack / Linear / PR provenance, ` · `-separated |

**Matching a cost day to a row.** There is no date-of-impact property — the day a row explains is
named inside `Impact` free text, and `Date` is when the change landed, which is typically *later*
than the spike it diagnoses. So a lookup for a flagged day scans recent rows' `Impact` for the
date rather than filtering on `Date`. Worked example: the row dated `2026-08-24` is the one that
accounts for the `2026-08-21` spike.

## `Learnings/Loop/{Loop}`

One database per **Loop** — the pipeline that turns an app observation into a shipped, measured
experiment. Each loop has its own database, Slack channel and Linear parent issue, and they all
share this schema and lifecycle.

Live today:

| Loop | Database | Data source | Channel | Parent |
|---|---|---|---|---|
| `Aurie Features Ideas` | [7e721e75…](https://app.notion.com/p/7e721e75374645a7acfa085e3aeca6df) | `8d14f3eb-0c55-4f25-9322-ab2b2a69f092` | `#ai-loop-aurie-features` `C0C1JTK3YCC` | PROD-5917 |
| `Aura Recommendations Ideas` | [3e375e23…](https://app.notion.com/p/3e375e23af20818d995ffe1a27cfb465) | `3e375e23-af20-815b-80d5-000ba798e625` | `#ai-loop-aura-recommendations` `C0C0ZSMU7C7` | PROD-5931 |

Planned: `Aura Features`, `Aurie Conversations`.

Unlike every other `Learnings/*` database, these rows are **mutable and long-lived** — a row is
created at proposal time and edited at every stage until the experiment it produced concludes.
Written by `product:record-ideas` (create), `product:manage-ideas` and the loop's harvest /
build / ship legs (transitions). The process itself is documented on the
[Loop page](https://app.notion.com/p/35d75e23af208195b333d4158c46aed2).

**Properties:**

| Field | Type | Required | Notes |
|---|---|---|---|
| `Title` | title | yes | Short, action-oriented |
| `Description` | text | yes | What to build / change |
| `Status` | select | yes | Lifecycle — see below |
| `Quick status note` | text | no | One line per transition: who, when, why. Human-owned; automation appends, never rewrites |
| `Fingerprint` | text | yes | `sha1(screen + category + title)[:12]` — the dedup key, never edited |
| `Domain` | select | no | `onboarding`, `paywall`, `home/feed`, `conversation`, `journal`, `grounding`, `affirmations`, `profile`, `settings`, `notifications`, `growth/referral`, `other`. Drives ideation rotation |
| `Screen` | text | no | Screen identifier, e.g. `welcome_screen`. Blank for non-screen-specific ideas |
| `Category` / `Lever` | text | no | Free text by design — the analyzers generate a wider vocabulary than any fixed enum |
| `Impact` | select | no | `high` / `medium` / `low` |
| `Effort` | select | no | `S` / `M` / `L` |
| `Rationale` | text | no | Why this lifts the lever |
| `Hypothesis` | text | no | If we X then Y because Z |
| `Rejection reason` | select | on rejection | `already tested`, `no longer valid`, `low impact`, `off-strategy`, `tech cost`, `duplicate`, `timing`, `other`. Read back by the ideation step so a rejected idea is not re-proposed blind |
| `Decided by` / `Decided at` | people / date | on decision | Who settled it in Slack, and when |
| `Slack thread` | url | no | The idea's thread in its loop channel (older rows point at the `#ai-ideas` archive) |
| `Linear issue` | url | no | The `ab:{experiment}` card, created when the build starts |
| `PR` | url | no | Pull request implementing it |
| `Screenshot` / `Video` | files | no | Observed screen, then the built treatment |
| `Experiment name` | text | no | Backend flag key, e.g. `aiPersonalizedGreeting` |
| `Experiment` | relation → `Learnings/Experiments` | no | Set once the experiment is live, so the verdict rolls back onto the idea |
| `First seen` / `Last seen` | date | yes | First proposal, and the most recent time any source re-surfaced it |

**Per-loop vocabulary and extensions.** The property *types* are shared; two of them carry a
different vocabulary per loop, and one loop adds three properties.

`Aura Recommendations` reads the shared schema like this:

| Property | Its meaning in that loop |
|---|---|
| `Screen` | the **surface**, from the `sent_from` property on the listen events, so a row joins to its own telemetry — `sleepHome`, `exploreAll`, `todayRow`, `mood`, `notifications`, `personalizedEmail` |
| `Domain` | the **pipeline layer** — `retrieval`, `ranking`, `blend weights`, `filters`, `candidate supply`, `instrumentation`. What the collision gate filters on and what ideation rotates over |
| `Rejection reason` | the shared vocabulary **plus `offline bar`** — the one value automation may write without a human, when a candidate fails the offline evaluation |

and adds three:

| Property | Type | Notes |
|---|---|---|
| `Offline score` | text | ratios to the popularity baseline on the same split, e.g. `NDCG@10-new 1.31x · coverage 0.94x · cohort 2021-22 -6.1pp`. Written by `data:evaluate-recommender`, never by hand |
| `Primary metric` | select | declared **before** the build, not chosen after the readout |
| `Guardrail` | select | what must not get worse; default `sub-3s skip rate` |

Its row ids use the prefix **`REC`**, not `IDEA` — Notion unique-id prefixes are unique across
the workspace and `IDEA` belongs to Aurie Features. The `Status` enum is **unchanged**: an
offline failure is a `rejected` row with its own rejection reason, not a new state.

**Status lifecycle** — a row moves left to right and never skips backwards:

`pending` → `shared` → `approved` / `rejected` / `deferred` → `in_progress` → `in_review` →
`merged` → `released` → `experiment_running` → `won` / `lost` / `inconclusive`

**Validation rules:**

- `Status` and `Quick status note` are human-owned. Re-ingesting an idea updates enrichment and
  `Last seen` only — never the status or the note. This is the invariant the whole loop rests on.
- A `rejected` or `deferred` row without a `Rejection reason` is incomplete: the reason is the
  only thing that stops the same idea being proposed again next month.
- `Fingerprint` is derived, never authored. Two rows sharing one is a bug in the ingest.
- Querying for "what can we build next" is `Status = approved`; "what has the loop shipped" is
  `Status` in `released`, `experiment_running`, `won`, `lost`, `inconclusive`.

## Unknown types

If the caller asks for a `Learnings/*` path that isn't listed here (e.g. a new Retros DB), fall back to Recipe A on the parent `Learnings/` page and report that the requested DB's schema isn't known locally yet — ask the caller to add it. **Say so explicitly rather than returning the parent page as though it answered**: a caller looking for a specific row reads an unqualified parent-page result as "no such row exists", which is the opposite of "I could not look".
