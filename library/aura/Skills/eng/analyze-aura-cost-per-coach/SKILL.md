---
name: analyze-aura-cost-per-coach
description: >
  Rank Aura coaches by total earnings from listen payouts in a chosen window — today,
  week-to-date, month-to-date (default), previous month, last 7 days, last 30 days, or a
  custom range. Renders a top-N table with coach ID prefix, listens, real $ earned, share and
  cumulative share, plus a per-row tier breakdown, a window-wide earnings-by-tier table, and a
  distribution stats block (mean / median / p90 / p95 / p99 / max / std dev) framing how
  concentrated earnings are. Coach-side complement to `analyze-aura-cost-per-user`; uses the
  production payout-rate logic so figures match what coaches actually got paid. Trigger on
  "top coach earnings", "highest paid coaches", "coach payout ranking", "who is earning the
  most on Aura", "aura coach earnings this week", "ranked coach payouts", "coach revenue
  ranking", "analyze aura cost per coach", or "top earning coaches". Do NOT use for aggregate
  spend (`eng:analyze-aura-cost`) or per-listener cost (`eng:analyze-aura-cost-per-user`).

metadata:
  version: "3.1.0"
knowledge:
  reads:
    - Guide/Index         # baseline routing aid (universal)
    - Tools/Aura API      # PaymentService endpoint reference + auth conventions
    - Compliance/Privacy  # coach ID truncation rule in rendered output
---

# Analyze Aura Cost Per Coach

Ranks Aura coaches by **real earnings** from listen payouts in a chosen window. Coach-side complement to [`eng:analyze-aura-cost-per-user`](../analyze-aura-cost-per-user/SKILL.md) — same shared API (PROD-1961), same authoritative rate logic, just grouped by coach instead of listener.

Family siblings:

- [`eng:analyze-aura-cost`](../analyze-aura-cost/SKILL.md) — aggregate spend, no per-actor dimension
- [`eng:analyze-aura-cost-per-user`](../analyze-aura-cost-per-user/SKILL.md) — listener-spend ranking
- [`eng:report-top-aura-listeners`](../report-top-aura-listeners/SKILL.md) — condensed daily Slack-ready ranking

All four share [PROD-1961](https://linear.app/aura-health/issue/PROD-1961) as their data dependency.

## Compliance

Coach IDs are **public-facing creator identifiers** (coaches appear with names in the Aura app) and are less sensitive than Firebase UIDs. Rendered output still truncates each coach ID to the first 8 chars + `…` for consistency with the per-user sibling. Full IDs are preserved in `--json` output for downstream joins.

The underlying PaymentService endpoint may include `givenName` / `familyName` / `email` joined from `"user"` / `"coach"` tables on response rows. The formatter never prints those — `_safe_row()` whitelists only `key`, `listen_count`, `cost_cents`, `music_listens`, `non_music_listens`, `tier_breakdown`, `distinct_coaches`, `distinct_content`.

## Step 0 — Check prerequisites

- `AURA_API_TOKEN` must be set in `~/.claude/settings.json` under `env` (see CLAUDE.md → Secrets and environment). Token needs **admin scope** on PaymentService.
- `python3` on the path (stdlib only).
- Default base URL is `https://services.aurahealth.io`. Override via `$AURA_API_BASE_URL` env or `--base-url` flag on `call_aggregate.py`.

If `AURA_API_TOKEN` is missing, `call_aggregate.py` fails fast with a clear error pointing to `update-config`. No silent degradation.

## Step 1 — Resolve the time window

Same presets as the rest of the family:

- **Today** — UTC start-of-day → now (daily snapshot)
- **Week-to-date** — Monday 00:00 UTC → now
- **Month-to-date** (default) — 1st of current calendar month UTC → now
- **Previous month** — full prior calendar month (UTC)
- **Last 7 days** — rolling 7d ending now
- **Last 30 days** — rolling 30d ending now
- **Custom range** — explicit `--from YYYY-MM-DD --to YYYY-MM-DD` (UTC, half-open)

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-aura-cost-per-coach/scripts/resolve_window.py --window mtd
```

Keep `from_iso` / `to_iso` / `label` / `days_in` for Steps 2 and 3.

## Step 2 — Fetch the per-coach aggregate

Use `--group-by coachId` — that's the only change from the per-user sibling:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-aura-cost-per-coach/scripts/call_aggregate.py \
  --since <from_iso> \
  --until <to_iso> \
  --group-by coachId \
  --output /tmp/aura_per_coach.json
```

Common variants:

```bash
# Bigger top-N from the server (default 100; max 5000)
... call_aggregate.py --since ... --until ... --group-by coachId --limit 500 --output /tmp/aura_per_coach_500.json

# Filter to a known coach set (look at a specific cohort)
... call_aggregate.py --since ... --until ... --group-by coachId \
  --coaches coach-id-1 coach-id-2 coach-id-3 --output /tmp/aura_cohort.json

# Drop music listens (lower-rate tier)
... call_aggregate.py --since ... --until ... --group-by coachId --no-music

# Production rule overrides (defaults match the payout job)
... call_aggregate.py --since ... --until ... --group-by coachId --ignore-daily-cap
```

See [`eng:analyze-aura-cost-per-user`](../analyze-aura-cost-per-user/SKILL.md#step-2--fetch-the-aggregate-from-paymentservice) for the full flag table on `call_aggregate.py` — same script, only `--group-by` differs.

## Step 3 — Format the report

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-aura-cost-per-coach/scripts/format_report.py \
  --input /tmp/aura_per_coach.json \
  --label "<label from Step 1>" \
  --days-in <days_in from Step 1>
```

Common variants:

```bash
# Default: top 10 + headline + per-row tier breakdown for top-3 + window tier table + stats
... format_report.py --input /tmp/aura_per_coach.json --label "yesterday" --days-in 1

# Deeper rank cutoff
... format_report.py --input /tmp/aura_per_coach.json --label "yesterday" --days-in 1 --top 25

# Compact (no tiers, no stats, no headline — just the ranking table)
... format_report.py --input /tmp/aura_per_coach.json --label "yesterday" --days-in 1 \
    --no-tiers --no-stats --no-headline

# Full structured payload (full coach IDs, all coaches — keep this file local)
... format_report.py --input /tmp/aura_per_coach.json --label "yesterday" --days-in 1 \
    --json /tmp/aura_per_coach_full.json
```

Flags:

| Flag | Default | Effect |
|---|---|---|
| `--top N` | 10 | Rank cutoff on the table |
| `--tiers-for N` | 3 | Show per-row tier breakdown for the top N rows |
| `--no-tiers` | off | Suppress per-row tier rows + window-wide tier table |
| `--no-stats` | off | Suppress the distribution stats block |
| `--no-headline` | off | Suppress the one-line headline |
| `--json PATH` | n/a | Write the structured payload (full coach IDs) to disk |

## Step 4 — Show the report inline

The formatter renders:

1. **Headline** — total qualifying listens, total payout ($), coach count, exclusion footnote, top coach's earnings + share-of-payout + multiplier vs the average coach.
2. **Top-N table** — rank, short coach ID, listens, earnings ($), share, cumulative share. Top-`--tiers-for` rows include a tier-mix line.
3. **Window-wide earnings-by-tier table** — Listens + earnings + share by tier (`<500`, `500–1999`, `2000–4999`, `≥5000`, `music`).
4. **Distribution table** — count + mean / median / p90 / p95 / p99 / max / std dev across the enumerated coaches (earnings per coach).

Show the script's stdout verbatim. Don't paraphrase the numbers.

## JSON shape (`--json` output)

```json
{
  "window": { "label": "...", "days_in": 7 },
  "summary": { "total_listens": ..., "total_cost_cents": ..., "groups": ..., "tier_breakdown": { ... }, "...": "..." },
  "stats_cost_cents": { "count": ..., "mean": ..., "median": ..., "p90": ..., "p95": ..., "p99": ..., "max": ..., "stddev": ... },
  "top_coaches": [
    { "key": "<coachId>", "listen_count": ..., "cost_cents": ..., "music_listens": ..., "non_music_listens": ..., "tier_breakdown": {...}, "distinct_coaches": ..., "distinct_content": ... }
  ],
  "coaches_all": [ "... full ranked list, same row shape as top_coaches ..." ]
}
```

Coach IDs are truncated to `first8…` in the rendered markdown; the JSON output keeps the full ID so it's grep-able for downstream joins. Note that when `groupBy=coachId`, the per-row `distinct_coaches` field is always 1 (each row represents exactly one coach) — kept in the shape for consistency with the per-user sibling.

## Per-listen $ logic (from PaymentService)

A listen earns the coach money only if:

- `userContentListen.premiumPaid IS TRUE`
- `userContentListen.dailyCoachListenedCount <= 6`
- `content.type <> 'podcast'`
- `coach.podcaster IS NOT TRUE`
- `coach.deletedAt IS NULL`
- `content.deletedAt IS NULL OR content.deletedAt > userContentListen.createdAt`

Rate per qualifying listen (from `config.payout` in PaymentService):

| Tier | Rate | Condition |
|---|---:|---|
| `music` | 5¢ | `content.type IN ('music', 'soundscape')` |
| `lt500` | 6¢ | Non-music, `contentListenedCount < 500` |
| `lt2000` | 10¢ | Non-music, `500 ≤ contentListenedCount < 2000` |
| `lt5000` | 13¢ | Non-music, `2000 ≤ contentListenedCount < 5000` |
| `other` | 17¢ | Non-music, `contentListenedCount ≥ 5000` |

These are the **same rates and rules** as the monthly payout job — see [PROD-1961](https://linear.app/aura-health/issue/PROD-1961) for the canonical definition.

## Notes & edge cases

- **Earnings are authoritative, not estimated.** Same payout-rate logic as `src/jobs/payout.js`. A coach's window earnings from this endpoint sum to the same dollar amount that would land in their monthly ledger entry — modulo monthly-boundary edge cases.
- **Empty windows** render `No qualifying listens in this window.` and exit 0.
- **Distribution method.** Percentiles use `statistics.quantiles(..., method="inclusive")` because we have the full enumerated coach set, not a sample.
- **Coach IDs vs coach names.** v1 shows IDs only. Hydrating coach names would require a second API call per top-N row. Deferred — file a follow-up if needed.
- **v2 idea: cross-cut with `analyze-aura-cost-per-user`.** A "which listeners drove this coach's earnings" view would join both groupings. Out of scope for v1.
- **Cowork sandboxes** strip host env (PROD-1305) — paste-export `AURA_API_TOKEN` per session per CLAUDE.md → "Cowork constraints → Workaround".

## References

- Endpoint: [PROD-1961](https://linear.app/aura-health/issue/PROD-1961) — `POST /payouts/listens/aggregate` on PaymentService.
- This card: [PROD-1965](https://linear.app/aura-health/issue/PROD-1965).
- Family parent: [PROD-1928](https://linear.app/aura-health/issue/PROD-1928).
- PaymentService home: `~/Development/Aura/Services/PaymentService`.
