# Schema — `Learnings/Experiments`

**Status:** stable. Finalised via [ENG-384](https://linear.app/aura-health/issue/ENG-384). Further schema evolution coordinates with [PROD-1215](https://linear.app/aura-health/issue/PROD-1215) when the Ad Campaigns DB lands.

A/B tests and feature-flag rollouts across Aura and Aurie surfaces. One row per experiment. Write-once for bulk imports; **updatable in place** by [`data:analyze-experiment`](../../../../../data/skills/analyze-experiment/SKILL.md) when it enriches a row with Mixpanel actuals, synthesized learnings, and follow-up ideas.

**Important:** `Learnings/Experiments` rows are the **only Learnings DB rows that accept `action: update`** in `record-knowledge`. Signals snapshots stay immutable; other Learnings DBs default to write-once. See SKILL.md for the guardrail.

## Properties

| Field | Type | Required | Notes |
|---|---|---|---|
| `Title` | title | yes | Experiment identifier as used in Slack / Linear / Mixpanel (e.g. `hardPaywall`, `newHomeOnboarding`). Keep the exact camelCase so it matches the `Experiment: <name>` user property in Mixpanel. |
| `App` | select | yes | `Aura` \| `Aurie` |
| `Platform` | select | yes | `Aurie Mobile` \| `Aurie Web` \| `Aura Mobile` \| `Aura Web` \| `Core AI` \| `App Store` \| `CRM` \| `Desktop` \| `Email`. Both Aura and Aurie ship on web and mobile, so the enum carries Mobile/Web for each app. Maps 1:1 to the source sheet's tab when the sheet distinguishes; otherwise inferred from `App` + the experiment-server `targetPlatform`. |
| `Experiment Type` | multi-select | no | Any combination of `Core feature`, `Onboarding`, `Conversation`, `Subscription`, `Email`. Multi because the source sheet has combos like "Subscription, Conversation". Blank whenever the creating source carries no type — see validation rule 1. |
| `Status` | select | no | `Running` \| `Stopped splitting` \| `Won` \| `Lost` \| `Turned off` \| `Inconclusive` \| `Feature flag`. Blank when not inferable. `analyze-experiment` should update this on enrichment. See *Two states that are not verdicts* below for the last two. |
| `Start Date` | date | yes | When the experiment started |
| `End Date` | date | no | Blank if still running |
| `Hypothesis` | text | no | Often blank from the source sheet; `analyze-experiment` fills it in from the corresponding Linear ticket on enrichment |
| `Variants` | text | no | Short summary of all variants — one line per variant (e.g. `a: Unskippable paywall` / `b: Control`). Scales from 2 to N variants; experiments like `AurieInvitePushCopy` with 4 variants (`Control A / Variant B / Variant C / Variant D`) fit cleanly. Full verbatim variant detail lives in the page body under `## What was tested`. |

## Page body

The body follows the structure of [`analyze-experiment` Step 7 output](../../../../../data/skills/analyze-experiment/SKILL.md) so enrichment updates individual sections in place without restructuring:

```markdown
## What was tested
<one-sentence summary + variant definitions>

## Result
<metrics block — dashboards, lift %, significance, n>

## Learning
<2–3 sentences of interpretation — currently from sheet's Result column, later enriched by analyze-experiment>

## Follow-up experiments
<2–4 concrete ideas — blank on bulk import, filled in by analyze-experiment>

# Sources
- [Google Sheet — Aurie AI AB Experiments (<tab>)](<sheet URL>) (primary)
- <Mixpanel dashboard URLs when present>
```

## Example row (bulk-import shape)

```yaml
Title: "hardPaywall"
App: "Aurie"
Platform: "Aurie Mobile"
Experiment Type: ["Subscription", "Conversation"]
Status: null  # inferred on enrichment
Start Date: 2026-04-17
End Date: 2026-04-21
Hypothesis: null
Variants: |
  a: Unskippable paywall
  b: Control

# Body
## What was tested
Makes paywall unskippable, users have to start trial to be able to use the app
a : Unskippable paywall
b : Control

## Result
Old Dashboard
New dashboard
Paywall -> Trial start

## Learning
Higher Paywall -> trial start 34% 97%s, trial cancellation is currently
neutral, and too early for paid data, this is also resulting in lower WAU +
sessions activity

## Follow-up experiments
_To be added via `analyze-experiment` enrichment._

# Sources
- [Google Sheet — Aurie AI AB Experiments (Aurie Mobile)](https://docs.google.com/spreadsheets/d/1XsxniLks_r5wELsrcFjujZha1qeMAnFuMQTprLXoFB8/edit?usp=sharing) (primary)
```

## Validation rules

1. `Title`, `App`, `Platform`, `Start Date` are required on create. **`Experiment Type` is not** — it is set only when the creating source carries a type (the original Google-Sheet import; `marketing:aurie-web-onboarding-experimentation-ideas`, which is always `Onboarding`). The path that creates most rows today, [`data:sync-experiments`](../../../../../data/skills/sync-experiments/SKILL.md), reads the Aura experiment-server catalog, which has no type field at all — so a "required" here would either block every sync create or force a guessed classification into a durable table. Blank is the correct write. It shows: 2,947 of 3,058 rows (96.4%) were blank as of 2026-08-12, and nothing back-fills it — `analyze-experiment` enrichment does not set it either. Classifying an existing row is a human edit.
2. `Title` should match the Slack `Experiment: <name>` convention — camelCase, no spaces. Warn if whitespace/hyphens are present (common sheet-entry mistake).
3. `Experiment Type` values must come from the enum; if the source sheet has a new type, update the DB schema before importing.
4. `Status` transitions: rows start with `Status = null` or `Running`. `analyze-experiment` is the primary flipper to terminal states (`Won` / `Lost` / `Inconclusive` / `Turned off`). `data:sync-experiments`' reconcile may move `Running` / `In flight` / blank → `Stopped splitting`, and nothing else; `Feature flag` is set only by a human.
5. `End Date` present implies the experiment is no longer `Running` — flag as a warning if `Status = Running` and `End Date` is set. The converse does **not** hold: `Stopped splitting` and `Feature flag` carry a blank `End Date` on purpose (see below), so absence of a date is not evidence a row is live.
6. `sources` (in the page body `# Sources` section): at minimum the Google Sheet URL. `analyze-experiment` must add the Mixpanel dashboard when enriching. **Linear cards never appear in `# Sources`** — `Guide` → Authoring rules bans card links in AI Knowledge content, `# Sources` included, because a card moves or closes and the citation degrades into a pointer at something that no longer says what it said. Name the ticket inline as durable text instead (`the paywall test shipped under PROD-1109`); `healthcheck-knowledge` detector #8 flags a card link in Sources as its own finding.

## Two states that are not verdicts

Added under [PROD-6116](https://linear.app/aura-health/issue/PROD-6116). The
experiment server never sets `endedAt` when a winner ships to everyone — it
collapses `active` to a single variant instead. Every consumer keyed on
`endedAt`, so 133 finished experiments read as `Running` on 2026-09-17, which is
what `product:build-idea`'s collision gate and `product:learn-idea`'s wait both
read as the live set.

| Value | Written by | Means | `End Date` |
|---|---|---|---|
| `Stopped splitting` | `data:sync-experiments` (`scripts/reconcile.py`) | Not splitting traffic. Says nothing about **why**. | blank |
| `Feature flag` | a human, once | Not an experiment — a kill switch kept live on purpose. Will never have a verdict. | blank |

**Neither may be read as an outcome.** One arm covers a rollout, a variant forced
before release, a release still pending, a kill by an unreachable `minVersion`
and a permanent flag; `result` is empty in all five and `notes` records intent
rather than state. Mapping `Stopped splitting` to `Won` would have marked
`chatImageUpload` a win the day after it was created.

**`End Date` stays blank on both, deliberately.** The obvious filler is
`updatedAt`, and it is a last-touch stamp rather than an end — median 916 days
stale across these rows, and a feature flag has no end at all. A date written
here would flow straight into `analyze-experiment`'s Mixpanel window and into
every duration the top-10 synthesises. The state carries the fact; the date
column stays honest.

A row leaves `Stopped splitting` only when a person resolves it — into a verdict
(by hand, or by running `data:analyze-experiment` once they know the window), or
into `Feature flag`. The reconcile never overwrites either.

**A resolve must land on a terminal value, not on `In flight`.** `Stopped
splitting` rows have a null `endedAt` by definition, and `In flight` is what a
reader of `endedAt` alone concludes from that. Writing it puts the row back in
the reconcile's writable set, so the next pass returns it to `Stopped splitting`
— a loop that never settles and reads as drift rather than as the collision it
is. `data:analyze-experiment` Step 1d checks `active` before calling a null
`endedAt` running, for this reason.

**The reconcile withdraws its own marker, and only its own.** If the catalogue
shows a `Stopped splitting` row splitting again — someone resumed the test — the
status goes back to `Running`. That is the one write toward `Running` it makes,
and it is safe precisely because nobody's judgement is being overwritten: a
verdict, a `Feature flag`, or a human `Turned off` is left alone and reported
instead.

## Sources to pull from on enrichment

When `analyze-experiment` enriches a row, these are the canonical sources to read (in addition to Mixpanel):

1. **#support-bot** (`C02FQE15YGP`) — `end experiment <name> with result ... and winner <a|b>` AuraBot messages. Gives End Date, winner, and the team's called outcome. Use to set `Status`:
   - `winner a` + positive language ("Higher ...", "Winning") → `Won`
   - `winner a` where `a` is explicitly control (e.g. "note that a is control") → `Lost` (treatment lost)
   - "Issues with assignment" / "not working as intended" → `Turned off`
   - "nothing confirmable" / "not significant" → `Inconclusive`
2. **#wins** — full analyses (often posted by Mohammad "Yawar" or Jerome as `<experiment> — analysis` or `WIN — <experiment>`) with Mixpanel variant tables, significance, follow-up ideas. This is where the richest prose learning typically lives.
3. **Linear ticket** — hypothesis + what was built; the ticket title matches the experiment's camelCase name.

Enrichment should treat #wins content as the authoritative `## Learning` source and #support-bot end-note as the authoritative `## Result` summary. If the two disagree (it happens — small-N mid-experiment calls get revised), prefer #wins.

## Update semantics (for `analyze-experiment` enrichment)

When `analyze-experiment` updates an existing row, it calls `record-knowledge` with `action: update`. The payload shape:

```yaml
action: update
path: Learnings/Experiments
page_id: <notion page UUID or URL>
fields:
  Status: Won           # updated from Running / blank to a terminal state
  Hypothesis: "..."     # populated if blank; skipped otherwise
body_replace_sections:
  "## Result": |
    Mobile Mixpanel (2026-04-17 → 2026-04-22, filtered on Experiment: hardPaywall):
    Variant  N (SUL)  Trial Start  Rate
    a        ...      ...          34% (97%s)
    b        ...      ...
    ...
  "## Learning": |
    Higher Paywall → trial start 34% (97%s), trial cancellation neutral,
    too early for paid data. Lower WAU + sessions activity. Ship with a
    logout escape hatch as implemented.
  "## Follow-up experiments": |
    - Softer hard-paywall appearing only after first completed session
    - A/B test logout-escape hatch vs no escape
    - Revisit paid retention at week 4
body_append_sources:
  - label: "Mixpanel — Aurie Experiments dashboard"
    url: "https://mixpanel.com/project/963821/..."
  - label: "Slack — #wins hardPaywall analysis"
    url: "https://auramobile.slack.com/archives/..."
```

Key rules (same as base-schema's update-mode semantics):

- **Property merges** — only updates properties in `fields`. Existing properties preserved.
- **Body section replaces** — overwrites only named sections. `## What was tested` is never touched (captures the source-sheet framing; canonical).
- **Sources append-only** — new Mixpanel + Slack entries added; the Google Sheet primary entry from bulk import stays in place. No Linear card links (see validation rule 6).

See the `record-knowledge` SKILL.md Recipe C for the exact update contract and rejection rules.

## Idempotency

Check for an existing row by `Title` (case-sensitive match) before creating — the experiment name is the deduplication key. Re-running the bulk import should be a no-op: if a row with the same title exists, callers should switch to `action: update` rather than creating a duplicate. `analyze-experiment` enrichment is inherently idempotent via `action: update` — running it multiple times on the same row replaces the named body sections with the latest analysis and appends any new source URLs.
