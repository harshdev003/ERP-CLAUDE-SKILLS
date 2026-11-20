# Boilerplate templates & example invocations

Reference material for the `data:analyze-experiment` skill: the sweep-mode boilerplate close-out templates (Step 1f), the over-stamping funnel worked example (Step 5.5), and end-to-end example invocations per mode (Step 8 / Output).

## Sweep-mode boilerplate templates (Step 1f)

Applied to the cheap buckets without further fetches.

### `NEVER_LAUNCHED` template

`End Date` stays blank: a never-launched experiment has no `endedAt`, so it has no
end date. Writing the sweep's own run date there is what closed six live rows
([PROD-6200](https://linear.app/aura-health/issue/PROD-6200)).

```yaml
Status: Turned off
End Date: (blank)
Hypothesis: <derived from Aura API `notes` if present; else "Pre-registered catalog entry; never produced traffic.">
Variants: <derived from Aura API `active` letters; mark control b unless notes say otherwise>

## What was tested:
  <Aura API `notes`, or "Reserved variant for <inferred surface>. Catalog-registered <createdAt> with `minVersion: <minVersion>`. The version gate never opened (the live mobile build is still on a v<X>.x track as of <today>) — no audience ever entered the test.">

## Result:
  Never launched. Mixpanel: <SUL count> SUL events with the property across the <age>-day window (all variant `undefined` if applicable). <Slack mention count> mentions in #data-experiment/#support-bot (create/end log lines only). <Linear count> tickets matching `<name>`.

## Learning:
  Catalog hygiene issue: experiments pre-registered with future `minVersion` gates or as single-variant stubs accumulate as 'Running' rows that never produce signal. Going forward, gate via feature flag once the build is ready, or only register the catalog row when the version that will execute it has shipped.

## Follow-up experiments:
  Process-level follow-ups (none specific to this row — see cross-cutting):
  - Add a catalog hygiene rule: any experiment with `minVersion >` shipped version + 14 days should auto-flag for review.
  - Distinguish 'reserved' vs 'live' states in the experiment server so reserved rows don't show as 'Running' in dashboards.
```

### `TOO_YOUNG` template

```yaml
Status: In flight
End Date: (blank)
Hypothesis: <derived from Aura API `notes` if present; else "[Pending — too young to enrich from Linear yet.]">
Variants: <derived from Aura API `active`>

## What was tested:
  <Aura API `notes`>. Created <createdAt> — <age> days old as of this readout.

## Result:
  **Too young to analyze (<age> days in.)** Mixpanel: <SUL> SUL events with the property, <Trial> trial-start events. Watch closely over the next ~3 weeks; re-evaluate once N exceeds 100 per arm.

## Learning:
  [Placeholder — re-run analyze-experiment on this row in 2–3 weeks once data has accumulated.]

## Follow-up experiments:
  - Re-evaluate at 3+ weeks of data via `analyze-experiment <name>`.
  - Pair with a guardrail metric appropriate to the surface being tested.
```

## Sweep-mode cheap-signal queries (Step 1f, step 1)

Gather these four sources in parallel for every input row — all non-blocking and quick:

- **Aura API** — `GET /experiments/<name>` → `endedAt`, `notes`, `result`,
  `minVersion`, `targetApp`, `active`
- **Mixpanel SUL count** — segmentation on `AI Sign Up Login` filtered by
  `Experiment: <name>` being set, over the row's full lifetime; returns
  per-variant counts (see Step 5's "Mixpanel access ladder")
- **Slack mention count** — bulk dump `#data-experiment` and `#support-bot`
  once for the run, then in-memory grep for each experiment name (one HTTP
  fetch covers every row)
- **Linear ticket count** — GraphQL `issues(filter: { title: { containsIgnoreCase: <name> } })`

Validated 2026-05-29 on a 39-row Running-Aurie sweep: 11 of 39 were never-launched stubs whose answer was knowable from cheap signals alone — running the full pipeline on them would have burned tokens for boilerplate output.

## Sweep-mode false-positive & split-report examples (Step 1f)

**False-positive Slack-ended hit.** When an end-note for `X` mentions another experiment `Y` (e.g. `"Experiment ended: limitedfree2 [471 users] - Turned off — replaced by limitedFreeNewUsers/limitedFreeOldUsers"`), `Y` will *look* ended-in-Slack but isn't — only treat as ended when the experiment's own name is in the post's `` `<name>` `` slot, not just in the free-text description.

**Split report** shown to the user before any deep work begins:

```
Sweep classification (N=39):
  NEVER_LAUNCHED:     11   (will close as 'Turned off — never launched')
  TOO_YOUNG:           1   (will mark 'In flight — awaiting data')
  RUNNING_WITH_DATA:  27   (will run Steps 2–7 per row)
```

## Over-stamping concrete example (Step 5.5)

From the 2026-04-29 `onboardingPromptResults` re-analysis: 717 unique users were stamped, but only ~107 ever completed onboarding QP — the gate before the experimental Results screen. The raw super-property cut showed trial-start dead flat (108/108), while the team's `#wins` exposure-cohort cut showed +44%. The disagreement was not a calculation bug — it was that the SDK registered the super-property at `AI Sign Up Onboarding Notification Screen Seen`, ~5 screens before the experimental surface rendered.

## Over-stamping funnel worked example (Step 5.5)

For Aurie onboarding, sensible candidates in funnel order: `AI Sign Up Start`, `AI Sign Up Screen Seen`, `AI Sign Up Onboarding Notification Screen Seen`, `AI In App Notification Permission Set`, `AI Sign Up Onboarding Credentials Screen Seen`, `AI Sign Up Login`. The worked example output from `onboardingPromptResults`:

```
AI Sign Up Start                                    16  ← near-zero, property not yet attached
AI Sign Up Screen Seen                             496  ← partial, SDK has fired for some users
AI Sign Up Onboarding Notification Screen Seen   1,049  ← fully attached
AI In App Notification Permission Set            1,037
AI Sign Up Onboarding Credentials Screen Seen      994
AI Sign Up Login                                   919
```

Read top-to-bottom: the SDK reliably attaches the property at `AI Sign Up Onboarding Notification Screen Seen`. The experimental Results screen renders several screens later. Over-stamping confirmed.

## Example invocations

### Create mode

> "analyze the last 5 ended experiments and post to #ai-product"

1. Read `#support-bot`, pull the 5 most recent end-experiment messages.
2. For each: Linear lookup + Mixpanel query.
3. Compose the per-experiment sections and cross-cutting summary.
4. Draft the `#ai-product` parent + thread, send for approval, then post.
5. Return the thread URL.

### Enrich mode — single row

> "enrich <https://www.notion.so/34c75e23af208171afcad7210a898da0>" (or just: "enrich the hardPaywall row")

1. `retrieve-knowledge` on `Learnings/Experiments` filtered by the page ID. Pre-populate Title, Variants, Start/End Date, Experiment Type from the row (skip `#support-bot` create-note lookup).
2. Still run `#support-bot` end-note lookup and `#wins` analysis search (richer prose than bulk-import `## Learning`).
3. Linear lookup for hypothesis only if the row's `Hypothesis` is blank.
4. Mixpanel query for the experiment's date window.
5. Compose the per-experiment update payload.
6. Step 8 defaults to option 4b — update the row in place via `record-knowledge` `action: update`. Other routing (`#ai-product` post, Linear tickets) still available.
7. Return the updated row URL.

### Enrich mode — batch DB query

> "enrich all Learnings/Experiments rows where Status is blank"

1. `retrieve-knowledge` on `Learnings/Experiments` filtered to `Status is blank`. Typically ~10 rows in the immediate post-import state.
2. For each row, run Steps 1c → 7 (with short-circuits where row already has data).
3. For each, Step 8 option 4b updates the row in place.
4. Report: N rows enriched; surface any that failed mid-loop with the specific error (validation, Mixpanel timeout, etc.).

Use this for the Phase 3 batch enrichment after bulk import.

### Sweep mode — large-list pre-classification

> "analyze all Running Aurie experiments" / "sweep stale experiment rows" / "analyze all Status: Running"

1. Resolve the input list via `retrieve-knowledge` filter on `Status is Running` (combined with `App is Aurie` if the user scoped to one app).
2. Bulk-fetch the four cheap signals **in parallel** across all rows: Aura API, Mixpanel SUL count (Tier 2 segmentation), Slack mentions (bulk-dump `#data-experiment` + `#support-bot` once and in-memory grep), Linear ticket counts.
3. Run Step 1f pre-classification: bucket each row into `NEVER_LAUNCHED` / `TOO_YOUNG` / `RUNNING_WITH_DATA`.
4. Report the split to the user and confirm before writing.
5. Apply boilerplate close-outs (Step 1f templates) to `NEVER_LAUNCHED` + `TOO_YOUNG` rows.
6. Run Steps 2–7 only on `RUNNING_WITH_DATA` rows.
7. Final report: per-bucket counts + per-experiment URLs + cross-cutting learnings that explicitly call out the classification breakdown (e.g. *"X% of Running rows were never-launched stubs — recommend catalog hygiene rule Y"*).

**Validated 2026-05-29** on a 39-row Running-Aurie sweep: 11 NEVER_LAUNCHED, 1 TOO_YOUNG, 27 RUNNING_WITH_DATA. Total wall-clock for the cheap-signal sweep: ~10 seconds. Total Notion writes: ~250 (props + section updates + sources appends).

## Outcome precedes exposure (Step 5.7)

`aiLiveKitIntegration4` — a four-arm A/A/B/B voice-engine test (`a`/`d` LiveKit, `b`/`c`
ElevenLabs) — assigns the arm at the **first voice tap, which is after the paywall**. About 80% of
payers had already paid three to five minutes before the engine existed for them.

The all-time paid rate was therefore mostly a pre-treatment covariate. It came out imbalanced by
chance (a 41% / b 32% / c 23% / d 52%), the imbalance happened to fall along engine lines, and it
passed an arm-level nested test at p=0.012. That was reported as a LiveKit win and retracted the
same day, once the data was cut on "voice before paid". Every post-exposure metric was flat:

| metric | LiveKit | ElevenLabs |
|---|---|---|
| paid after voice, of users unpaid at first tap | 13.9% | 11.2% |
| dismiss paywall → convert later | 17.1% | 14.7% |
| pre-paid then voiced → cancelled | 12.4% | 6.7% |

Two things made the retraction possible, and both are worth copying. The duplicate same-config arms
gave a noise floor — the two same-engine arms were 9.5pp apart on the headline rate while the two
engines were 2.7pp apart. And someone asked which event came first. Only the second is available on
a conventional two-arm test, which is why Step 5.7 is unconditional.

Sizing, for the "just run it longer" reflex: at the observed enrolment, confirming the 2.7pp gap at
80% power needed roughly 372 more days. One more week moved the minimum detectable effect from
13.2pp to 10.1pp, against an effect of 2.7pp. Duration was never the constraint.

### Reporting blocks

Add an **Exposure timing** line beside Step 5.5's Exposure quality block:

```
**Exposure timing** — assigned at <event / call site>. Pre-exposure: <metrics>. Post-exposure: <metrics>.
```

When the primary metric is pre-exposure, add:

```
⚠ **Methodology — outcome precedes exposure.** Assignment happens at <moment>, after <primary
metric> is already decided for <share>% of users. The reported <metric> difference is a
randomisation balance check, not a treatment effect. Verdict: Inconclusive by design.
Fix: move exposure before <metric>, or change the primary metric to one measured after assignment.
```
