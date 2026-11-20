---
name: build-aurie-cost-report
description: >
  Build ONE self-contained, theme-aware HTML report aggregating the whole Aurie cost/economics
  chart family into a single page — a key-reads stat strip, daily cost plus active users,
  daily contribution margin, the cohort retention heatmap, ten per-user economics and
  retention views (cost decay curve, activity retention, cumulative cost-to-date, projected
  lifetime cost, concentration and Gini, tenure, cohort payback, LLM-vs-voice mix, free-user
  cost, cost-by-channel), and two revenue views — each with a data-driven analysis paragraph.
  It COMPOSES the `eng:chart-aurie-*` skills to collect JSON, and the agent publishes the
  result as an Artifact. Two windows, both complete-weeks-only: since beginning (default) or
  the last N weeks. Aggregate-only, never emits a UID. Trigger on "build the aurie cost
  report", "cost economics report", "full cost report", "combine the cost charts into one
  report", "aurie cost dashboard", "rebuild the cost report", "cost report with margin", or
  "last 4 weeks cost report".

metadata:
  version: "3.4.0"
knowledge:
  reads:
    - Guide/Index                  # baseline routing aid (universal)
    - Tools/BigQuery               # dataset/table conventions, GCP_KEY auth (used by the composed charts)
    - Compliance/Privacy           # composed charts read PII-bearing tables — output must stay aggregate
---

# Build Aurie Cost Report

## Goal

Produce one page that tells the whole Aurie cost/economics story — spend over time and who showed up,
whether the day's amortized revenue actually covers that spend (daily contribution margin), how a
joined user's cost decays, how concentrated cost is, and the per-segment / per-channel economics — so
it can be shared as a single Artifact instead of many separate charts. Every number and every analysis
line is generated from live data, so re-running it refreshes the report for the current window.

This is the **composite report** member of the cost-chart family. It doesn't compute anything new: it
runs the existing `eng:chart-aurie-*` skills to collect their JSON, then a bundled self-contained
`render.py` lays them out into HTML. Composition happens here (this workflow), the same way
`eng:store-aurie-user-daily-cost` composes `eng:analyze-openai-cost-per-user`.

## Step 0 — Prerequisites

The composed chart scripts read BigQuery (`aura-main.aurie_cost` + the Mixpanel Nessie
`ai_subscription_activated` event) via their bundled `bq.py` — they need `GCP_KEY` +
`google-cloud-bigquery` (API-first; no BigQuery MCP). `render.py` itself is **stdlib-only** (it just
reads the collected JSON and writes HTML). Use the framework `python3` that has
`google-cloud-bigquery` installed.

## Step 1 — Bring the source data and coverage dimensions up to date

**Run this step every time.** A stale dimension doesn't make the report fail or warn — it quietly
shrinks the denominators, so the page looks healthy while undercounting. Run these in order:

1. [`eng:store-aurie-user-daily-cost`](../store-aurie-user-daily-cost/SKILL.md) — the cost store must
   cover through the report's end date **before** the backfills run. The backfills derive their
   work-list from the distinct `user_id`s in `aurie_per_user_daily_cost`, so backfilling first misses
   the users a newly-stored day introduces.
2. [`eng:backfill-aurie-user-env`](../backfill-aurie-user-env/SKILL.md) — the `aurie_user_env`
   (environment) dimension behind `aurie_per_user_daily_cost_prod`, the view **every** composed chart
   except `eng:chart-aurie-daily-cost` reads. That view joins on `aurie_user_env` with an
   `INNER JOIN`, so a `user_id` missing from the dimension is dropped from every section of the
   report — cohort, margin, retention, concentration, tenure, payback, mix, free-user, channel.
   Confirm the run's missing count reaches ~0; it also refreshes the view when it finishes.
3. [`eng:backfill-aurie-user-ai-created`](../backfill-aurie-user-ai-created/SKILL.md) — the
   `aurie_user_ai_created` (signup) dimension used by the heatmap, curve, tenure, free-user charts.
4. [`eng:backfill-aurie-user-channel`](../backfill-aurie-user-channel/SKILL.md) — the
   `aurie_user_channel` dimension used by cost-by-channel.

`margin.json`'s `excluded` block reports what the prod filter dropped, and the rendered report shows
it as a callout under the header (see Notes). Read its `untagged_cost` after a run: a non-zero value
is an env-coverage gap — re-run step 2 and rebuild rather than publishing the undercount.

## Step 2 — Collect the chart JSON (pick a window)

Each `eng:chart-aurie-*` script prints its JSON to stdout (warnings go to stderr). Run them into a
temp directory under the canonical key names `render.py` expects (`daily cohort curve retention cumulative lifetime concentration
tenure payback mix free channel margin revenue revenuetier`). Choose ONE of the two windows — both show **complete weeks only**
(the cohort completeness rule is never relaxed, so the newest still-forming cohort never appears):

**Mode A — Since beginning (default): all data since the store started.**

```bash
DDIR="$(mktemp -d)/aurie-cost"; mkdir -p "$DDIR"; R="${CLAUDE_PLUGIN_ROOT}/skills"
python3 "$R/chart-aurie-daily-cost/scripts/aggregate.py"             --all            > "$DDIR/daily.json"
python3 "$R/chart-aurie-cohort-cost/scripts/cohort.py"               --max-week 8     > "$DDIR/cohort.json"
python3 "$R/chart-aurie-cost-curve/scripts/curve.py"                 --max-week 8     > "$DDIR/curve.json"
python3 "$R/chart-aurie-retention/scripts/retention.py"             --max-week 8     > "$DDIR/retention.json"
python3 "$R/chart-aurie-cumulative-cost/scripts/cumulative.py"       --max-week 8     > "$DDIR/cumulative.json"
python3 "$R/chart-aurie-lifetime-cost/scripts/lifetime.py"          --max-week 8     > "$DDIR/lifetime.json"
python3 "$R/chart-aurie-cost-concentration/scripts/concentration.py" --all           > "$DDIR/concentration.json"
python3 "$R/chart-aurie-tenure-cost/scripts/tenure.py"               --all           > "$DDIR/tenure.json"
python3 "$R/chart-aurie-cohort-payback/scripts/payback.py"           --max-week 8     > "$DDIR/payback.json"
python3 "$R/chart-aurie-cost-mix/scripts/mix.py"                     --all --max-week 8 > "$DDIR/mix.json"
python3 "$R/chart-aurie-free-user-cost/scripts/free_cost.py"                          > "$DDIR/free.json"
python3 "$R/chart-aurie-cost-by-channel/scripts/channel_cost.py"     --all           > "$DDIR/channel.json"
python3 "$R/chart-aurie-daily-margin/scripts/margin.py"              --all           > "$DDIR/margin.json"
python3 "$R/chart-aurie-revenue/scripts/revenue.py"                 --monthly       > "$DDIR/revenue.json"
python3 "$R/chart-aurie-revenue-by-tier/scripts/revenue_by_tier.py"                 > "$DDIR/revenuetier.json"
echo "$DDIR"
```

**Mode B — Last N weeks: a trailing window (set `N`).** Snapshot charts take `--last N*7` days; the
cohort family (heatmap / curve / payback) takes `--last-weeks N` (keeps the last N *complete*
cohort-weeks — so the visible rows may be fewer than N near the boundary, since the newest
still-forming week is excluded).

```bash
N=4; DAYS=$((N*7)); DDIR="$(mktemp -d)/aurie-cost"; mkdir -p "$DDIR"; R="${CLAUDE_PLUGIN_ROOT}/skills"
python3 "$R/chart-aurie-daily-cost/scripts/aggregate.py"             --last $DAYS                 > "$DDIR/daily.json"
python3 "$R/chart-aurie-cohort-cost/scripts/cohort.py"               --max-week 8 --last-weeks $N > "$DDIR/cohort.json"
python3 "$R/chart-aurie-cost-curve/scripts/curve.py"                 --max-week 8 --last-weeks $N > "$DDIR/curve.json"
python3 "$R/chart-aurie-retention/scripts/retention.py"             --max-week 8 --last-weeks $N > "$DDIR/retention.json"
python3 "$R/chart-aurie-cumulative-cost/scripts/cumulative.py"       --max-week 8 --last-weeks $N > "$DDIR/cumulative.json"
python3 "$R/chart-aurie-lifetime-cost/scripts/lifetime.py"          --max-week 8                 > "$DDIR/lifetime.json"  # projection: always full-history (a trailing window destabilises the decay fit)
python3 "$R/chart-aurie-cost-concentration/scripts/concentration.py" --last $DAYS                 > "$DDIR/concentration.json"
python3 "$R/chart-aurie-tenure-cost/scripts/tenure.py"               --last $DAYS                 > "$DDIR/tenure.json"
python3 "$R/chart-aurie-cohort-payback/scripts/payback.py"           --max-week 8 --last-weeks $N > "$DDIR/payback.json"
python3 "$R/chart-aurie-cost-mix/scripts/mix.py"                     --last $DAYS --max-week 8     > "$DDIR/mix.json"
python3 "$R/chart-aurie-free-user-cost/scripts/free_cost.py"                                      > "$DDIR/free.json"
python3 "$R/chart-aurie-cost-by-channel/scripts/channel_cost.py"     --last $DAYS                 > "$DDIR/channel.json"
python3 "$R/chart-aurie-daily-margin/scripts/margin.py"              --last $DAYS                 > "$DDIR/margin.json"
python3 "$R/chart-aurie-revenue/scripts/revenue.py"                 --weekly                     > "$DDIR/revenue.json"     # full-history trend (see note)
python3 "$R/chart-aurie-revenue-by-tier/scripts/revenue_by_tier.py"                              > "$DDIR/revenuetier.json" # all-time plan mix
echo "$DDIR"
```

The two **revenue** views are **full-history** in both modes (not clipped to the report window):
`revenue.py` shows the whole revenue trend at monthly (Mode A) / weekly (Mode B) grain, and
`revenue_by_tier.py` is an all-time plan-mix snapshot — a trailing window would distort the
bookings-vs-recognized story and the tier mix. They read the `aurie_user_subscription` revenue
dimension (build it first with `eng:build-aurie-user-subscription`).

`free_cost.py` has no window flag in either mode — it measures the full observed free period for the
signup cohort (cost-to-convert), which a trailing window would distort; it always reports the whole
free history.

Any single chart that fails is fine — `render.py` degrades that one section to "data unavailable" and
still builds the rest. (Equivalently, invoke each `eng:chart-aurie-*` skill via the Skill tool and
save its JSON to the matching `<key>.json`.)

## Step 3 — Render the HTML

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/build-aurie-cost-report/scripts/render.py \
  --data-dir "$DDIR" --out /tmp/aurie-cost-report.html --title "Aurie Cost & Economics Report"
```

`render.py` reads every `<key>.json`, computes the key-reads tiles + one analysis paragraph per chart
(from the data, never hardcoded), and writes a single self-contained HTML file (inline CSS + SVG +
vanilla JS; light/dark theme-aware). Flags: `--data-dir` (or per-key `--daily` / `--cohort` / …
paths), `--out` (required), `--title`, `--window` (header label; else inferred from the daily/cohort
span), `--generated` (date label; default today UTC).

## Step 4 — Publish

In **host Claude Code**, publish the HTML with the **Artifact tool**: `file_path` = the rendered
file, `favicon` = `📊`, a one-line `description`, and let the `<title>` name it. To refresh an
existing shared report in place instead of minting a new URL, pass that artifact's `url`.

The report **footer stamps the skill version** — `eng:build-aurie-cost-report:<version>`, read by
`render.py` from this skill's `metadata.version` (see `/CLAUDE.md` → "Skill versioning") — so a
published artifact is traceable to the exact revision that produced it. **Each chart card's skill
label also carries its own version** (e.g. `eng:chart-aurie-daily-margin:1.0.1`): every composed
chart script emits its `metadata.version` as a `skill_version` field in its JSON, and `render.py`
stamps it onto the card label — so `render.py` stays self-contained (it never reads a sibling
SKILL.md; the version rides in the data). A chart whose JSON predates the field simply shows the
bare skill id.

If the Artifact tool isn't available (Cowork / headless), leave the file at `--out` and tell the user
the path, or open it in a browser. On an unattended run that is not a publish: record `publish` as
`failed` for Step 5, so the report that never went out raises a follow-up.

## Step 5 — Raise a follow-up for every stop (unattended runs)

A scheduled run (the Monday `weekly-aurie-cost-report` routine) has nobody reading its reply, so
every step that did not finish becomes a `shared:raise-followup` post. Keep an outcomes file in
the session scratchpad and append one JSON object per line as each step ends, e.g.
`{"step": "publish", "status": "failed", "reason": "Artifact tool not available in this session"}`.
Steps are `prerequisites`, `freshness`, `charts`, `render`, `publish`. Status is `ok`, `failed`
or `blocked`. A run that stops instead of publishing an undercount (`margin.json`'s
`excluded.untagged_cost` still non-zero after the env backfill) records `blocked` at `charts`.
Then:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/build-aurie-cost-report/scripts/outcomes.py \
  --cadence build-aurie-cost-report --session "${AINATIVE_SESSION_NAME:-scheduled}" \
  --in "$SCRATCH/cost-report-outcomes.jsonl" \
  --due prerequisites,freshness,charts,render,publish
```

A step with no entry counts as `stopped`, and a missing outcomes file means nothing was
recorded, so a run that ends early still reports what it never reached. The script prints one
object per follow-up: pass each to `shared:raise-followup` as is. The key is
`build-aurie-cost-report:<step>:stop`, one post per step, with a changed reason going in as a
reply. Exit 0 means nothing to raise. Run this step **before the turn ends, however it ends**.
An interactive run with someone reading the reply may skip it.

## Notes & caveats

- **Composition, not duplication.** This skill owns layout + analysis only; the charts and their SQL
  live in the `eng:chart-aurie-*` skills. `render.py` is self-contained (stdlib, reads JSON) so it
  never imports a sibling — the sibling scripts are run in Step 2 at the workflow level.
- **Data-driven analysis.** Tiles and the per-chart reads are computed in `render.py` from the JSON
  (Gini, top-1% share, paid-vs-organic ratio, conversion rate, premium week-0, LLM/voice split, …),
  so the prose stays correct when the window changes.
- **Windows — since beginning vs last N weeks (Step 2).** Both are complete-weeks-only. Snapshot
  charts (daily / concentration / tenure / mix / channel) take `--all` or `--last N*7` days; the
  cohort family (heatmap / curve / payback) takes the default (all cohorts since data start) or
  `--last-weeks N`. `--last-weeks` never introduces a forming cohort — it slices the tail of the
  already-complete cohort set, so a trailing window may show fewer than N rows when the newest week
  isn't done yet. The header window is inferred from the daily span, so it reflects whichever mode
  was run. `free_cost` is always full-history (see Step 2).
- **Privacy.** Every input is already aggregate (per-week / per-segment / per-channel sums and
  averages, ranked percentiles) — no UID enters the JSON or the HTML. Keep it that way.
- **Prod-only, with a prod-exclusions callout (PROD-3589 Part B).** Every composed chart reads
  `aurie_per_user_daily_cost_prod` (env='prod') instead of the base table, so dev/QA and
  not-yet-tagged users are excluded from every number in the report. `margin.json`'s `excluded`
  block (base-vs-view $ delta for the window) drives a callout right under the header that
  **splits two things the raw delta conflates**: (1) genuine dev + QA/unknown *user* traffic
  (dev + unknown env tags — the real non-prod exclusion, e.g. USD 27.96 / 0.039%), and (2) the
  `Other` reconciling row, labeled as unattributed spend, NOT dev/QA. **`Other` is not a voice
  remainder:** it is each authoritative vendor total minus its per-user attribution, **LLM
  included** — over 2026-06-01 → 2026-10-04 it was USD 2,287.50, of which LLM USD 1,266 (eval and
  `[Development]` projects that no user owns, plus attribution misses), unused LiveKit
  subscription capacity USD 849, and ElevenLabs/Cartesia remainders the rest
  ([PROD-6991](https://linear.app/aura-health/issue/PROD-6991)). The callout renders that split
  from `margin.json`'s `excluded.other_by_component`, largest first; LiveKit unused capacity is
  signed (a month that crosses into overage reverses it). A **Voice billed to no user** line follows
  when `excluded.collapsed_voice_paths` has entries. It names each vendor-day where a voice path
  billed and no user carried any of it. Those dollars are inside the `Other` figure but are a miss,
  not a by-design remainder ([PROD-6992](https://linear.app/aura-health/issue/PROD-6992)). A not-yet-tagged line appears only
  when that coverage gap is non-zero. The callout degrades silently to nothing if `margin.json`
  predates the `excluded` field. One exception to the env-filter: `daily.json` (from
  `eng:chart-aurie-daily-cost`) is **deliberately NOT env-filtered** — it keeps reading the base
  table so its headline total still reconciles exactly to the authoritative daily bill (including
  the `Other` remainder); see that skill's SKILL.md.

## Origin

Aggregates the cost-understanding chart set ([PROD-3498](https://linear.app/aura-health/issue/PROD-3498))
into one shareable report — the heatmap + daily-cost-with-active-users view plus the seven economics
charts ([PROD-3530](https://linear.app/aura-health/issue/PROD-3530)).
