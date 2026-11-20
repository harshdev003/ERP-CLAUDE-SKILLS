---
name: sweep-recommender
description: >
  The ideation leg of the Aura Recommendations loop — proposes ideas from what production and
  the offline harness actually measure, not from a blank page.
  Reads per-model empty rates, empty recommendation responses, catalogue coverage and sub-3s
  skip rate against stored thresholds and priors, turns each breach that has genuinely moved
  into a candidate with its evidence, then applies a materiality filter and a priors check so a
  marginal finding reaches the digest rather than the backlog. Emits the findings JSON that
  product:record-ideas consumes. Trigger on "sweep the recommender", "run the recommendation
  ideation", "what should we change about recommendations", "find recommendation candidates",
  "recommender signals", "check the recommendation thresholds", or a loop tick asking for the
  sweep leg. Do NOT use to score a candidate
  (data:evaluate-recommender), to check whether the recommender is healthy
  (eng:monitor-recommendation-health), or to write ideas to Notion (product:record-ideas).

metadata:
  version: "1.2.1"
knowledge:
  reads:
    - Guide/Index      # baseline routing aid (universal)
    - Tools/BigQuery   # GCP_KEY service account, aura-main access
    - Tools/GCP        # same key for Cloud Logging — the audit-log signals
    - Learnings/Loop/Aura Recommendations Ideas   # the loop, its layers and its rejection vocabulary
---

# sweep-recommender

This loop's answer to "where do ideas come from". `product:features-loop` ideates by driving a
simulator through a screen; there is no screen here, so the sweep proposes against measurement:
**a model that stopped contributing is an idea, and so is a surface whose abandonment is
drifting.**

Runs as `product:recommendations-loop sweep`, one pipeline layer per tick.

## Prerequisites

```bash
test -n "$GCP_KEY" && echo "gcp: OK" || echo "gcp: MISSING"
python3 -c "import google.cloud.bigquery" 2>/dev/null && echo "bq: OK" || echo "bq: MISSING"
```

`signals` and `layers` need neither — they degrade to whatever they can reach and say which
source they skipped.

## Usage

```bash
S="${CLAUDE_PLUGIN_ROOT}/skills/sweep-recommender/scripts/sweep_recommender.py"

python3 "$S" layers                      # the rotation
python3 "$S" signals --layer ranking     # read-only: value vs threshold, no proposals
python3 "$S" telemetry --layer ranking   # breached-and-moved -> candidate JSON

python3 "$S" sweep --layer ranking \
  --priors-file  .loop/priors.json \
  --backlog-file .loop/backlog.json \
  --out          findings_recs.json
```

Then hand the output to the record leg — the shape is already what it expects:

```bash
product:record-ideas --from-json findings_recs.json --data-source-id <recs DB>
```

## What it watches

Thresholds, provenance and the measured baseline for each are in
[references/signals.md](references/signals.md). The set today:

| Signal | Layer | Breach when |
|---|---|---|
| `empty_model_rate` | ranking | a model in the blend returns nothing, > 50/h |
| `empty_response_rate` | filters | the recommender returns nothing to the caller, > 50/h |
| `catalogue_coverage` | candidate supply | under 30% of the catalogue heard in 30 days |
| `sub3s_skip_rate` | ranking | over 18% of plays abandoned inside three seconds |

Each carries a **prior**. A breach that has held steady for months is a known condition, not
news — only a breach that has *moved* becomes a candidate. With no prior, every breach is news,
which is the right behaviour on a first run.

An unreachable source is reported and skipped, never fatal: a Cloud Logging outage must not
read as "nothing to propose". A log signal that **saturates the entry cap at every window**
refuses rather than returning a rate — a truncated count would report the wrong magnitude and
let the `moved` check compare two bounds and conclude nothing had changed.

## The two filters, and why they are strict

The sweep's failure mode is not missing a signal. It is proposing enough marginal ones that the
loop channel stops being read.

**Materiality.** Two hard rules, both required:

1. **Clears the offline bar by a margin** (default 3%), not by a hair inside the confidence
   interval. A grid search will surface a ratio of 1.004 indefinitely.
2. **Names a mechanism** — one sentence saying what changes for a *user*. "Tune a constant" is
   not a mechanism; "surface content from coaches the user already follows" is.

A candidate that passes the first and fails the second is a **tuning finding**: real, but it
belongs in the weekly digest, not the backlog. The tuning heuristic applies to harness grid
output only — a telemetry signal legitimately reports that a constant is wrong.

**Priors.** Before anything is written, check the loop database. An idea rejected `already
tested` or `no longer valid` is retired and suppressed. One rejected for `timing` or `tech
cost` is **re-proposable** — that distinction is the whole reason the rejection vocabulary is
fixed, and suppressing those would quietly retire them. Matching is conservative (normalised
title within the same layer); `record-ideas` fingerprint dedup is the real safety net.

## Rotation

One **pipeline layer** per tick, least-recently-swept first:

```
retrieval · ranking · blend weights · filters · candidate supply · instrumentation
```

These are the `Domain` values in the loop's database, so the rotation reads out of rows the
loop already loads — no separate cursor. Features rotates over product screens; this loop
rotates over the part of the pipeline a change would touch, because that is also what the
collision gate filters on.

## Output contract

A JSON array of idea objects for `product:record-ideas --from-json`. This loop's reading of the
shared schema:

- **`screen`** → the **surface**, from the `sent_from` property on the listen events, so a row
  joins directly to its own telemetry
- **`domain`** → the **pipeline layer**

## Notes

- **It never writes.** No Notion, no Slack, no BigQuery writes — it emits JSON and a summary.
  The record and share legs act on it.
- **The harness sweep is not implemented yet.** Source 2 in the loop design — running
  `data:evaluate-recommender` over a declared grid of recipe variations — is not here; today
  the sweep is telemetry-only. `is_material` already handles a candidate carrying an
  `offline_ratio`, so the plumbing is ready for it.
- **Every threshold is a measurement, not a guess**, and each is dated in
  [references/signals.md](references/signals.md). Re-measure before trusting a drift: a
  threshold set against a broken baseline encodes the breakage as normal.
