---
name: chart-aurie-cost-by-channel
description: >
  Aurie cost per user by acquisition channel / campaign — which channels bring cheap vs expensive
  users (marketing-spend efficiency; pairs with cohort payback for CAC-to-margin). Trigger on:
  "cost by channel", "cost by campaign", "cost per user by acquisition", "channel cost efficiency",
  "which channels bring expensive users", "paid vs organic cost", "cost by source". Joins per-user
  cost to the aurie_user_channel dimension; composes eng:backfill-aurie-user-channel. Reports avg
  cost/user by coarse channel (paid/organic/unknown), by campaign, and by platform. Aggregate-only —
  never emits a UID.
metadata:
  version: "3.2.1"
knowledge:
  reads:
    - Guide/Index                  # baseline routing aid (universal)
    - Tools/BigQuery               # dataset/table conventions, GCP_KEY auth, bq.py
    - Compliance/Privacy           # reads PII-bearing tables — output must stay aggregate
---

# Chart Aurie Cost by Channel

## Goal

Show **which acquisition channels bring cheap vs expensive users** — average AI cost per user by
channel, so marketing spend can be weighed against the cost each channel's users incur (pairs with
`eng:chart-aurie-cohort-payback` for CAC-to-margin). Three views:

- **by_channel** — coarse: **paid** (has a paid Appstack campaign) vs **organic** (no campaign) vs
  **unknown** (not yet in the channel dimension).
- **by_campaign** — per paid Appstack campaign (volume + avg cost/user), highest-volume first.
- **by_platform** — per `sourcePlatform` (AIiOS / AurieWeb / UserWeb …).

This is the channel member of the cost-skill family. Siblings:
`eng:backfill-aurie-user-channel` (the channel dimension this composes),
`eng:chart-aurie-cohort-payback` (revenue/margin per cohort).

## Step 0 — Prerequisites

Reads BigQuery (`aura-main.aurie_cost`) via the bundled `bq.py` — needs `GCP_KEY` and
`google-cloud-bigquery`. No BigQuery MCP, so this is API-first; a missing key/library exits with a
clear message.

## Step 1 — Coverage guard (ensure the channel dimension is complete)

Joins `aura-main.aurie_cost.aurie_user_channel`. Before charting, make that dimension complete by
invoking [`eng:backfill-aurie-user-channel`](../backfill-aurie-user-channel/SKILL.md) (via the Skill
tool). `channel_cost.py` reports coverage (`missing`) and warns if incomplete — missing users fall
into the `unknown` channel.

## Step 2 — Aggregate cost by channel

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/chart-aurie-cost-by-channel/scripts/channel_cost.py
```

Emits JSON:

```jsonc
{
  "cost_window": "2026-06-01..2026-07-12 (42 days present)",
  "coverage": { "cost_users": 8309, "with_channel": 8145, "missing": 164 },
  "by_channel": [
    { "channel": "organic", "users": 6100, "total_cost": 5200.0, "avg_cost_per_user": 0.85, "median_cost": 0.2 },
    { "channel": "paid",    "users": 1900, "total_cost": 3600.0, "avg_cost_per_user": 1.89, "median_cost": 0.6 }, …
  ],
  "by_campaign": [ { "campaign": "Aurie_Appstack_CreativeTest_06-09-26", "users": 320, "total_cost": 640.0, "avg_cost_per_user": 2.0 }, … ],
  "by_platform": [ { "source_platform": "AIiOS", "users": 7800, "total_cost": 8600.0, "avg_cost_per_user": 1.1 }, … ]
}
```

Window flags: `--all` (every day, the default — lifetime observed cost per user), `--last N` (last N
days present), `--from` / `--to` (inclusive `YYYY-MM-DD`); `--top-campaigns N` (max campaigns,
default 15). Cost is per-user total over the window; the `Other` unattributed row is excluded.

## Step 3 — Render

Render with the visualization tool (host Claude Code):

- **by_channel** — bars of avg cost/user for paid vs organic vs unknown, with the user-count as a
  label; the paid-vs-organic avg-cost gap is the headline (do paid-acquired users cost more?).
- **by_campaign** — ranked bars of avg cost/user per campaign (volume as a second axis/label) — the
  expensive vs cheap campaigns.
- **by_platform** — bars of avg cost/user per platform.

Metric cards: paid vs organic avg cost/user (+ the ratio), the most/least expensive campaign,
coverage. If the widget tool is unavailable, skip to Step 4.

## Step 4 — Print the tables

Always print the three tables (channel, campaign, platform) with users / total_cost /
avg_cost_per_user. Follow with a one-line read: paid vs organic cost/user, and the most-expensive
campaign — with the CAC-to-margin caveat.

## Notes & caveats

- **Channel granularity is thin.** Only a minority of users carry a paid `aiAppstackCampaign`; the
  rest are `organic` (no campaign) or `unknown` (no Aura record / not yet backfilled). So the primary
  signal is **paid vs organic**, with per-campaign detail for the paid slice.
- **Cost window is cost-store-limited.** Default `--all` is the full observed per-user cost (the store
  starts 2026-06-01), not lifetime-since-install — a user acquired earlier only has cost from the
  store start. Read avg cost/user as "observed-window cost," and compare channels over the same
  window.
- **Pairs with payback.** avg cost/user by channel is only half of CAC-to-margin; join with
  `eng:chart-aurie-cohort-payback` revenue to compare channels on contribution, not just cost.
- **Privacy.** Aggregate-only (channel/campaign/platform sums + averages), never a UID, though it
  reads the PII-bearing per-user cost table (channel dim stores non-PII attributes only).

- **Prod-only (PROD-3589 Part B).** Reads `aurie_per_user_daily_cost_prod` (env='prod') instead
  of the base table — dev/QA and not-yet-tagged users are excluded from these aggregates; see
  eng:backfill-aurie-user-env, which owns and refreshes the view. Still aggregate-only, never
  emits a UID.

## Origin

Idea #5 from the #ai cost thread (2026-07-13): cost per user by acquisition channel for
marketing-spend efficiency — [PROD-3508](https://linear.app/aura-health/issue/PROD-3508), part of
the cost-understanding chart set ([PROD-3498](https://linear.app/aura-health/issue/PROD-3498)).
