---
name: chart-aurie-cost-mix
description: >
  Decompose Aurie cost into LLM vs voice, by subscription segment and by tenure week — is the cost
  (and the premium week-0 spike) deep-context chat or voice minutes? Guides which lever (LLM context
  trimming vs voice caps) moves margin most, and for which users. Trigger on: "llm vs voice cost",
  "voice vs llm", "cost split llm voice", "cost mix", "is it chat or voice", "voice share of cost",
  "llm voice by segment", "llm voice by cohort". Reads BigQuery
  aura-main.aurie_cost.aurie_per_user_daily_cost (llm_cost + voice_cost per user/day); the tenure
  lens composes eng:backfill-aurie-user-ai-created. Aggregate-only — never emits a UID. For the
  multi-vendor voice breakdown (ElevenLabs vs LiveKit path) use eng:chart-aurie-daily-cost.
metadata:
  version: "3.2.1"
knowledge:
  reads:
    - Guide/Index                  # baseline routing aid (universal)
    - Tools/BigQuery               # dataset/table conventions, GCP_KEY auth, bq.py
    - Compliance/Privacy           # reads a UID-bearing table — output must stay aggregate
---

# Chart Aurie Cost Mix (LLM vs voice)

## Goal

Split Aurie cost into its two big drivers — **LLM** (OpenAI chat) and **voice** (ElevenLabs +
LiveKit-path minutes) — so you can see which lever moves margin and where:

- **by segment** — LLM vs voice per `subscription_state` (free / trial / paid / grace) over a window.
  Answers "do paying users skew voice or chat?" (they skew voice; free users skew LLM).
- **by tenure week** — LLM vs voice by weeks-since-AI-signup across all cost data. Answers "is the
  week-0 cost spike LLM or voice?" (it's roughly balanced — the spike is volume, not one lever).

This is the cost-driver member of the cost-skill family. Siblings:
`eng:chart-aurie-daily-cost` (daily totals with the multi-vendor voice split),
`eng:chart-aurie-cohort-cost` (total cost by join-week),
`eng:chart-aurie-cost-concentration` (heavy-user tail).

## Step 0 — Prerequisites

Reads BigQuery (`aura-main.aurie_cost`) via the bundled `bq.py` — needs `GCP_KEY` and
`google-cloud-bigquery`. No BigQuery MCP, so this is API-first; a missing key/library exits with a
clear message.

## Step 1 — Coverage guard (tenure lens only)

The `by_week` lens joins `aura-main.aurie_cost.aurie_user_ai_created`. Before charting, make that
dimension complete by invoking [`eng:backfill-aurie-user-ai-created`](../backfill-aurie-user-ai-created/SKILL.md).
`mix.py` reports its own coverage (`missing`) and warns if incomplete. (The `by_segment` lens needs
no dimension.)

## Step 2 — Aggregate the split

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/chart-aurie-cost-mix/scripts/mix.py --last 30 --max-week 8
```

Emits JSON:

```jsonc
{
  "segment_window": "2026-06-13..2026-07-12 (30 days present)",
  "tenure_scope": "all cost data (weeks since AI signup)",
  "coverage": { "cost_users": 8309, "with_ai_created": 8145, "missing": 164 },
  "by_segment": [
    { "subscription_state": "paid", "llm_cost": 3239.96, "voice_cost": 3907.65,
      "total_cost": 7147.61, "voice_share": 0.5467, "users": 964 }, …
  ],
  "by_week": [
    { "week_n": 0, "llm_cost": 3330.74, "voice_cost": 3602.18, "total_cost": 6932.92,
      "voice_share": 0.5196, "user_days": 22188 }, …
  ]
}
```

Flags: `--last N` / `--from` / `--to` / `--all` scope the **segment** lens (default last 30 present
days, matching the sibling charts); `--max-week N` bounds the **tenure** lens (default 8). The
tenure lens always spans all cost data (a lifecycle age view).

## Step 3 — Render the split

Render with the visualization tool (host Claude Code):

- **by segment** — grouped or stacked LLM/voice bars per `subscription_state`, with the
  `voice_share` printed. The paid-vs-free voice-share gap is the headline (which lever per tier).
- **by tenure** — stacked LLM/voice bars per `week_n`, with a `voice_share` line. Shows the week-0
  magnitude spike and that the mix stays ~balanced across ages.

Metric cards: paid voice_share, free voice_share, week-0 voice_share, overall voice_share. If the
widget tool is unavailable, skip to Step 4.

## Step 4 — Print the tables

Always print two markdown tables (by segment, by tenure) with `llm_cost`, `voice_cost`,
`voice_share`, and the count column. Follow with a one-line read: which segment/age is voice- vs
LLM-dominant and therefore which lever (voice caps vs context trimming) to pull.

## Notes & caveats

- **Two lenses, two windows.** `by_segment` respects the window flags; `by_week` spans all cost data
  (age analysis is a lifecycle view). Don't read a `by_week` total as "recent."
- **`voice_cost` is multi-vendor** (ElevenLabs + the LiveKit path: Cartesia + Deepgram + LiveKit),
  allocated per user/day upstream in `eng:store-aurie-user-daily-cost`; LiveKit-path $ is a
  pricing-sheet estimate. For the per-vendor voice breakdown over time use `eng:chart-aurie-daily-cost`.
- **`subscription_state`** is the state stored on each cost row (evaluated on the row's date). The
  `Other` unattributed row is excluded from both lenses.
- **Tenure denominator caveat.** `by_week` reports cost totals + `user_days` (active user-days at
  that age), not a per-joined-user average — so read the LLM/voice *share* (denominator-independent)
  as the mix signal, and totals as magnitude. For per-user cost by age use `eng:chart-aurie-cohort-cost`.
- **Privacy.** Aggregate-only (segment/age sums + shares), never a UID, though it reads the
  PII-bearing per-user cost table.

- **Prod-only (PROD-3589 Part B).** Reads `aurie_per_user_daily_cost_prod` (env='prod') instead
  of the base table — dev/QA and not-yet-tagged users are excluded from these aggregates; see
  eng:backfill-aurie-user-env, which owns and refreshes the view. Still aggregate-only, never
  emits a UID.

## Origin

Idea #4 from the #ai cost thread (2026-07-13): decompose cohort/segment cost into LLM vs voice to
pick the highest-leverage margin fix — [PROD-3507](https://linear.app/aura-health/issue/PROD-3507),
part of the cost-understanding chart set ([PROD-3498](https://linear.app/aura-health/issue/PROD-3498)).
