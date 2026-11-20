---
name: chart-aurie-daily-cost
description: >
  Renders a 4-panel Aurie cost dashboard from the daily cost stores: stacked LLM plus
  multi-vendor voice bars to a daily total (split into ElevenLabs vs the LiveKit path) with a
  paid-user line; $/paying-msg, $/voice-min and ambient cost per DAU per month, each against
  target — plus a daily rollup table. Reads BigQuery (`aura-main.aurie_cost`); aggregate-only,
  never emits a UID. LiveKit-path dollars are DERIVED from pricing-sheet estimate rates.
  Windows: `--last N`, `--from`/`--to`, `--all`. Trigger on "chart aurie daily cost", "chart
  the daily cost", "chart the cost store", "graph the daily cost", "plot daily aurie cost",
  "daily cost chart", "visualize the per-user cost store", "chart cost with paid users", "show
  the kpi dashboard", or "graph kpis". Do NOT use for a live per-day table from the vendor
  APIs (`eng:report-aurie-daily-costs`), the weekly spreadsheet row
  (`eng:report-aurie-weekly-costs`), filling the store (`eng:store-aurie-user-daily-cost`), or
  per-user drill-downs.

metadata:
  version: "3.3.1"
knowledge:
  reads:
    - Guide/Index          # baseline routing aid (universal)
    - Compliance/Privacy   # reads a UID-bearing store; aggregate-only output must never surface a UID
    - Tools/BigQuery                 # aura-main.aurie_cost store access (GCP_KEY)
---

# Chart Aurie Daily Cost

## Goal

Turn the daily cost stores maintained by
[`eng:store-aurie-user-daily-cost`](../store-aurie-user-daily-cost/SKILL.md) into a
**4-panel cost dashboard** over a chosen window:

- **Panel 1 — Daily spend:** stacked LLM + voice bars to a daily total, with an
  active-paying-users count line on a secondary axis. Voice is split into
  **ElevenLabs** vs the **LiveKit path** (Cartesia + Fish + Inworld + Deepgram +
  LiveKit) so the
  ElevenLabs → LiveKit-path voice migration shows over time.
- **Panel 2 — $/paying-msg** trend line vs the ≤\$0.018 target (dashed reference line).
- **Panel 3 — $/voice-min** trend line vs the ≤\$0.07 target.
- **Panel 4 — Ambient cost/DAU/month** trend line vs the ≤\$0.10 target.
- **Panel 5 — Engagement DAU:** `engaged` (sent a message) and `active` (opened the app —
  any mobile AI event) lines, with `reached` (billed users, incl. ambient/batch) as a
  context line so the gap is visible. Makes clear that reached ≫ real DAU. Lines are
  `null` (gap) on days whose store rows predate the engagement columns.
- **Metric cards** above the charts: window total, daily avg, latest-day cost, active
  paying users, cost/paying-user, **latest engaged / active / reached DAU**, latest KPI
  values vs each target.
- A **markdown daily rollup table** so the readout survives headless / Cowork sessions.

This is the **chart/read-only** member of the cost-skill family — it never writes the
store. Siblings: `eng:store-aurie-user-daily-cost` (the ETL that fills the store),
`eng:report-aurie-daily-costs` (a per-day table from the live APIs, no chart, no per-user).

## Step 0 — Prerequisites

**Reads from BigQuery** (`aura-main.aurie_cost`, the source of truth) via the bundled
`bq.py` — needs `GCP_KEY` (the same `claude@firebase-auratech16` SA, granted
`bigquery.jobUser` + `bigquery.dataEditor` on `aura-main`) and `google-cloud-bigquery`
(`python3 -m pip install --user google-cloud-bigquery`). No BigQuery MCP exists, so this
is API-first; a missing key/library exits with a clear message (BigQuery is the record).

Both tables must already be populated by `eng:store-aurie-user-daily-cost`. If the
per-user table is empty the script exits with a pointer to that skill; if the KPI table
is empty, KPI panels are omitted.

**BigQuery target.** Defaults to `aura-main.aurie_cost`; override with
`--project` / `--dataset` (or `$AURIE_BQ_PROJECT` / `$AURIE_BQ_DATASET`).

## Step 1 — Resolve the window

Pick exactly one (default is `--last 30`):

| Flag | Window |
|---|---|
| _(none)_ / `--last N` | Most recent N days present in the store (default 30) |
| `--from YYYY-MM-DD --to YYYY-MM-DD` | Inclusive date range (either bound optional) |
| `--all` | Every day in the store |

`--no-paid-line` drops the active-paying-users line from Panel 1 (sets `render.paid_line = false`).

## Step 2 — Aggregate the stores

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/chart-aurie-daily-cost/scripts/aggregate.py --last 30
```

Emits JSON to stdout:

```jsonc
{
  "source": "aura-main.aurie_cost.aurie_per_user_daily_cost",
  "render": { "paid_line": true },
  "summary": {
    "days": 21, "from": "2026-06-01", "to": "2026-06-21",
    "total": 5415.52, "avg": 257.88,
    "llm_total": 2835.05, "voice_total": 2580.49,
    "voice_11labs_total": 2578.15, "voice_11labs_agent_total": 2576.90,
    "voice_11labs_livekit_total": 1.25, "voice_livekit_path_total": 2.34,
    "voice_inworld_total": 0.0, "voice_fish_total": 0.0,
    "paid_user_days": 5779, "cost_per_paid_user": 0.9371,
    "latest": {
      "date": "2026-06-21", "total": 230.12, "paid": 323, "cost_per_paid": 0.7124,
      "cost_per_msg_paying": 0.0236, "cost_per_voice_min": 0.0916, "ambient_per_dau_month": 0.21
    },
    "peak": { "date": "2026-06-05", "total": 351.05 },
    "kpi_targets": {
      "cost_per_msg_paying": 0.018,
      "cost_per_voice_min": 0.07,
      "ambient_per_dau_month": 0.10
    },
    "kpi_source": "…/daily_eng_kpis.csv"
  },
  "days": [
    {
      "date": "2026-06-01", "llm": 139.04, "voice": 94.56,
      "voice_11labs": 94.51, "voice_11labs_agent": 94.42, "voice_11labs_livekit": 0.09,
      "voice_cartesia": 0.04, "voice_fish": 0.0, "voice_inworld": 0.0, "voice_deepgram": 0.0,
      "voice_livekit": 0.01, "voice_livekit_path": 0.05, "total": 233.59,
      "users_attributed": 1453, "active": 166, "engaged": 99,
      "livekit_voice_users": 4, "elevenlabs_agent_voice_users": 1449,
      "paid": 211, "trial": 93, "free": 1143, "grace": 6,
      "cost_per_msg_paying": 0.0349, "cost_per_voice_min": 0.0916, "ambient_per_dau_month": 0.22
    }
    // … one object per day. voice_11labs = voice_11labs_agent + voice_11labs_livekit
    // (ElevenLabs serves both the ConvAI agent and a LiveKit-path TTS role). Per-vendor
    // voice columns fall back to voice_11labs_agent = voice (others 0) when the store
    // predates the multi-vendor schema; voice_fish / voice_inworld read 0 on rows that
    // predate those columns. voice_livekit_path = cartesia + fish + inworld + deepgram +
    // livekit, so voice = voice_11labs + voice_livekit_path — aggregate.py prints a
    // WARNING on stderr for any day where that doesn't hold (an unread vendor column).
    // KPI fields are null when kpi_source is missing.
    // livekit_voice_users / elevenlabs_agent_voice_users = per-day distinct users on each
    // voice path (a user active on both counts in both) — tracks the ElevenLabs→LiveKit
    // migration by adoption; draw them as a two-line "voice users by path" trend.
  ]
}
```

## Step 3 — Render the 4-panel dashboard

Render a single widget with the visualization tool (host Claude Code). Use metric cards
at the top, then four Chart.js charts stacked below. If the widget tool is unavailable,
skip to the Step 4 table.

### Metric cards (above the charts)

From `summary`: window total, daily avg, latest-day total, active paying users latest,
cost/paying-user latest. From `summary.latest` KPI fields: show each KPI value and
whether it is above or below its target (`summary.kpi_targets`).

### Panel 1 — Daily spend (stacked bars + paying-user line)

- **Stacked bars (left `y` axis, dollars), all `stack: "cost"`:**
  - `LLM (OpenAI)` = `days[].llm` (`#185FA5` blue)
  - **Voice** — collapse to a single segment by default; split only once the
    LiveKit path is material:
    - **Collapse rule (default):** collapse whenever `summary.voice_total` is zero
      or missing (the split needs a positive voice total to mean anything — a window
      entirely before voice attribution began 2026-05-30 has none), **or** when
      `summary.voice_livekit_path_total / summary.voice_total < 0.01` (LiveKit path
      under 1% of voice — today's pre-migration state). Render one teal
      `Voice — ElevenLabs` bar = `days[].voice` (the full voice total, so the bars
      still sum to `days[].total`) and omit the purple segment. Add a one-line
      footnote: *LiveKit-path voice is negligible ($`summary.voice_livekit_path_total` total) — not shown.*
    - **Two-segment split** — only when `summary.voice_total > 0` **and**
      `summary.voice_livekit_path_total / summary.voice_total ≥ 0.01`:
      `Voice — ElevenLabs` = `days[].voice_11labs` (`#1D9E75` teal) +
      `Voice — LiveKit path` = `days[].voice_livekit_path` (`#8E44AD` purple, the
      Cartesia + Fish + Inworld + Deepgram + LiveKit sum, which grows as traffic moves off
      ElevenLabs). Footnote that LiveKit-path $ is DERIVED from ESTIMATE vendor
      rates (see `eng:store-aurie-user-daily-cost`).
  - (Bars sum to `days[].total`; `days[].voice` = ElevenLabs + LiveKit-path.)
- **Active-paying-users line (right `y1` axis, count):** `days[].paid`, dashed
  (`borderDash: [6,4]`), `#D85A30` coral, `pointStyle: "rectRot"`. Label it
  **"Active paying users"**. Omit when `render.paid_line` is false.
- x labels: short date (`Jun 1`); `autoSkip: false`, `maxRotation: 45`.
- Right axis: `grid.drawOnChartArea: false`.

### Panel 2 — $/paying-msg vs target

- Line chart, `days[].cost_per_msg_paying`, `#185FA5` blue.
- Horizontal annotation line at `summary.kpi_targets.cost_per_msg_paying` (\$0.018),
  dashed `#E24B4A` red, labelled "target \$0.018".
  Implement as a second dataset with `type: 'line'`, constant value, `borderDash: [4,4]`,
  `pointRadius: 0`, `borderColor: '#E24B4A'`.
- y axis label: `$/paying msg`; format ticks as `\$0.000`.
- Skip panel if all `cost_per_msg_paying` values are null.

### Panel 3 — $/voice-min vs target

- Same pattern as Panel 2 using `days[].cost_per_voice_min`, `#1D9E75` teal.
- Target line at `summary.kpi_targets.cost_per_voice_min` (\$0.07), dashed `#E24B4A` red.
- y axis label: `$/voice min`.
- Skip panel if all values are null.

### Panel 4 — Ambient cost/DAU/month vs target

- Same pattern using `days[].ambient_per_dau_month`, `#D85A30` coral.
- Target line at `summary.kpi_targets.ambient_per_dau_month` (\$0.10), dashed `#E24B4A` red.
- y axis label: `$/DAU/mo`.
- **Clamp the y-axis past attribution-gap artifacts (default).** On attribution-gap
  days the `users_attributed` (reached) denominator collapses to a small fraction of
  the norm, so `ambient_per_dau_month` explodes to 3–4+ (e.g. the Jun 23–25 window)
  and squashes the real ~0.2–0.9 trend + the 0.10 target line into an unreadable
  strip. Flag gap days as those whose `users_attributed` is **< 25% of the window's
  *max* `users_attributed`** — anchor on the max, **not** the median: a max-anchored
  test still fires when gap days are a large fraction of the window, whereas a median
  sinks toward the depressed values and stops flagging anything. Then set
  `scales.y.max` to **~1.15× the max `ambient_per_dau_month` among the non-gap days**
  (never below 1.2× the target, so the target line stays on-scale) so the meaningful
  range and the target line stay legible. The gap days' points render off the top —
  add a one-line footnote listing each clamped day and its true value (e.g. *Jun 24
  3.74, Jun 25 4.38 off-scale — attribution-gap artifact*). Auto-scale (no clamp)
  when no day is flagged, or when the computed `y.max` wouldn't clip anything. (A
  degenerate window that is *entirely* gap days has no healthy anchor and no real
  trend to protect — auto-scale is fine there.)
- Skip panel if all values are null.

### Panel 5 — Engagement DAU (engaged / active / reached)

- Three count lines on one shared y-axis (count): `days[].engaged` (`#185FA5` blue, solid),
  `days[].active` (`#1D9E75` teal, solid), `days[].users_attributed` = **reached**
  (`#888780` gray, dashed — context, not DAU).
- `engaged` and `active` are `null` on days predating the engagement columns → render as
  line gaps (`spanGaps: false`), same as the Jun 23–25 attribution-gap handling.
- Purpose: show that **reached ≫ real DAU** (reached is ~5–10× engaged) so nobody reads the
  billed-user count as DAU. Add a one-line note: *active = opened the app (any mobile AI
  event); engaged = sent a message; reached = billed incl. ambient/batch jobs.*
- Skip panel if every `active`/`engaged` value is null (store predates the columns).

### Shared chart rules

- Custom HTML legend per chart (small square swatch + dashed swatch for target line);
  disable Chart.js default legend.
- Dark-mode-safe: read `prefers-color-scheme` for grid/tick colors; series colors are
  fixed hex.
- Tooltips: dollar values formatted to 4 decimal places for KPI panels, 2 for spend panel.
- All panels share the same x-axis date labels from `days[].date`.

## Step 4 — Print the daily rollup table

Always print a markdown table (one row per day) + a totals row regardless of the chart:

| Date | LLM $ | Voice $ | 11Labs $ | LiveKit-path $ | Total $ | Paying users | $/paying msg | $/voice min | Ambient/DAU/mo |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|

- `11Labs $` = `days[].voice_11labs`; `LiveKit-path $` = `days[].voice_livekit_path`
  (Cartesia + Fish + Inworld + Deepgram + LiveKit; Inworld is measured from its usage
  API, the rest DERIVED from ESTIMATE rates). They sum to `Voice $`. When
  `summary.voice_inworld_total` or `summary.voice_fish_total` is non-zero, add a
  footnote naming it, so the TTS experiment arms are visible.
- **Collapse the two voice-split columns into a single `Voice $` = `days[].voice` by
  default** — same rule as Panel 1: keep the `11Labs $` / `LiveKit-path $` split only
  when `summary.voice_total > 0` **and**
  `summary.voice_livekit_path_total / summary.voice_total ≥ 0.01` (LiveKit path ≥ 1%
  of voice). Otherwise — including a zero or missing `voice_total` — drop both split
  columns.
- Omit the KPI columns if all values are null (kpi_source was missing).

Follow with a one-line summary from `summary`: window total, daily average, latest day,
peak day, blended cost/paying-user, and latest KPI values vs each target.

## Notes & caveats

- **"Active paying users" = active paid, not the billing base.** The line (`days[].paid`,
  rendered as "Active paying users") counts UIDs with a `paid` row that day — i.e. paying
  subscribers who had *attributed usage* that day. It's an active-paid proxy, not a
  subscriber count: a paying user who didn't use Aurie that day produces no row and isn't
  counted, so the line moves with daily engagement as well as with subscriptions. The
  underlying JSON field stays `paid` / `cost_per_paid` for back-compat — only the display
  label is "active paying users".
- **Attributed usage is LLM-or-voice, and LLM spans all prompters.** A row exists if the user
  appears in the LLM per-user allocation *or* the voice per-user allocation. The LLM side
  covers **all prompters** (foreground chat *plus* ambient/background work — memory, journal,
  notification, feed), so a user can be counted from background activity on a day they never
  actively chatted. In practice this window is ~all-LLM: every counted paying user has LLM
  spend and none are voice-only (~12–14% also have voice). The store can't split foreground
  conversation from ambient per user, so the line is "had attributed Aurie activity", not
  "had a conversation".
- **Cost-per-active-paying-user is blended.** The numerator is *total* Aurie spend across all
  users (free + trial + paid + the unattributed `Other` row), so `cost_per_paid_user` is an
  efficiency ratio (total spend ÷ active-paying-user-days), not a true per-paid-user cost.
  `summary.cost_per_paid_user` blends the whole window; `latest.cost_per_paid` is the latest
  day only.
- **Voice attribution starts 2026-05-30.** Per-user voice (and therefore the per-day voice
  total in this store) only exists from 2026-05-30 — `store-aurie-user-daily-cost` is a
  forward-growing dataset. Windows that reach earlier will show no voice for those days.
- **Privacy.** Output is aggregate-only — per-day sums and subscription-state counts, never a
  Firebase UID. The underlying store holds full UIDs (see `Compliance/Privacy`); this skill
  reads it but must never surface a UID, even though it draws from PII-bearing rows.
- **Read-only.** This skill never writes the store. To add a day, use
  `eng:store-aurie-user-daily-cost`.
- **Deliberately NOT env-filtered (PROD-3589 Part B).** Unlike the rest of the `chart-aurie-*`
  family, this skill keeps reading the *base* `aurie_per_user_daily_cost` table, not the
  `aurie_per_user_daily_cost_prod` view. `load()` sums `total_cost` over **every** row
  unconditionally — including the `Other` reconciling row — because the whole point of this
  store is that each day's rows sum EXACTLY to the authoritative OpenAI Costs + multi-vendor
  voice daily total (see `eng:store-aurie-user-daily-cost`). Pointing this skill at the prod-only
  view would silently drop the `Other` remainder (plus dev/unknown/not-yet-tagged users) from the
  headline `total`, breaking that reconciliation guarantee without anyone asking for it. If a
  prod-only daily-cost view is wanted later, add it as a second output field alongside the
  reconciled total rather than replacing it.

## Origin

Promoted from an ad-hoc chart built repeatedly during the daily-cost storing sessions —
[PROD-2723](https://linear.app/aura-health/issue/PROD-2723).
