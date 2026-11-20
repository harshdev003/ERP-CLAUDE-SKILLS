---
name: analyze-aura-cost-per-user
description: >
  Rank individual Aura users by actual coach-payout cost — the dominant driver of Aura's
  variable spend, computed with the production payout-rate logic (tiered rates by listen
  count, music vs non-music split, daily cap, premium-paid only). Windows: today,
  week-to-date, month-to-date (default), previous month, last 7 days, last 30 days, or custom.
  Renders a top-N table with UID prefix, listens, real coach payout, share and cumulative
  share, plus an optional per-tier breakdown, a window-wide cost-by-tier table, and a
  distribution stats block (mean / median / p90 / p95 / p99 / max / std dev) framing top
  spenders against the long tail. Trigger on "top aura listeners", "top aura users", "who's
  costing us the most", "aura cost per user", "aura listen cost ranking", "most expensive aura
  users", "aura coach payout by user", "analyze aura cost per user", or "rank users by
  listens". Do NOT use for aggregate totals (`eng:analyze-aura-cost`) or per-coach earnings
  (`eng:analyze-aura-cost-per-coach`).

metadata:
  version: "3.1.0"
knowledge:
  reads:
    - Guide/Index         # baseline routing aid (universal)
    - Tools/Aura API      # PaymentService endpoint reference + auth conventions
    - Compliance/Privacy  # UID prefix + email-domain-only rule; never full UIDs in rendered output
---

# Analyze Aura Cost Per User

Ranks individual Aura users by **real coach-payout cost** in a chosen window. Uses PaymentService's authoritative `userContentListen` table via the shared `POST /payouts/listens/aggregate` endpoint — same rate logic as the monthly payout job (`src/jobs/payout.js`), not an estimate.

Family siblings:

- [`eng:analyze-aura-cost`](../analyze-aura-cost/SKILL.md) — total aggregate spend, no per-user dimension
- [`eng:analyze-aura-cost-per-coach`](../analyze-aura-cost-per-coach/SKILL.md) — same endpoint, `groupBy=coachId`
- [`eng:report-top-aura-listeners`](../report-top-aura-listeners/SKILL.md) — condensed daily Slack-ready version of this skill

All four share [PROD-1961](https://linear.app/aura-health/issue/PROD-1961) as their data dependency.

## Compliance

This skill reads Firebase UIDs (PII-adjacent — they're not emails but they're user-identifiable in the Aura DB). Per [`Compliance/Privacy`](https://www.notion.so/34a75e23af20813a943fdb6f389b8e6e), rendered output truncates each UID to the first 8 chars + `…`. Full UIDs are preserved in `--json` output for downstream joins; that file should not be shared publicly. **Never paste the markdown table or JSON into a public channel without scoping.**

The underlying PaymentService endpoint may include `givenName` / `familyName` / `email` on response rows when joined from the `user` table. The skill **never prints these fields** — the formatter only reads `key`, `listen_count`, `cost_cents`, and tier data.

## Step 0 — Check prerequisites

- `AURA_API_TOKEN` must be set in `~/.claude/settings.json` under `env` (see CLAUDE.md → Secrets and environment). The token needs **admin scope** on PaymentService.
- `python3` on the path (stdlib only — no third-party packages).
- Default base URL is `https://services.aurahealth.io`. Override via `$AURA_API_BASE_URL` env or `--base-url` flag on `call_aggregate.py`.

If `AURA_API_TOKEN` is missing, the skill fails fast with a clear error pointing to `update-config`. No silent degradation — there is no MCP fallback for PaymentService.

## Step 1 — Resolve the time window

Same presets as the rest of the family:

- **Today** — UTC start-of-day → now (daily snapshot)
- **Week-to-date** — Monday 00:00 UTC → now
- **Month-to-date** (default) — 1st of current calendar month UTC → now
- **Previous month** — full prior calendar month (UTC)
- **Last 7 days** — rolling 7d ending now
- **Last 30 days** — rolling 30d ending now
- **Custom range** — explicit `--from YYYY-MM-DD --to YYYY-MM-DD` (UTC, half-open)

Resolve via the helper script so the dates are deterministic:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-aura-cost-per-user/scripts/resolve_window.py --window mtd
# → {"from": "2026-05-01", "to": "2026-05-19", "from_iso": "...", "to_iso": "...", "label": "month to date (May 2026)", "days_in": 19}
```

Other forms:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-aura-cost-per-user/scripts/resolve_window.py --window today
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-aura-cost-per-user/scripts/resolve_window.py --window wtd
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-aura-cost-per-user/scripts/resolve_window.py --window last-7d
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-aura-cost-per-user/scripts/resolve_window.py --window prev-month
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-aura-cost-per-user/scripts/resolve_window.py --from 2026-05-10 --to 2026-05-17
```

Keep `from_iso` / `to_iso` / `label` / `days_in` for Steps 2 and 3.

## Step 2 — Fetch the aggregate from PaymentService

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-aura-cost-per-user/scripts/call_aggregate.py \
  --since <from_iso> \
  --until <to_iso> \
  --output /tmp/aura_per_user.json
```

Common variants:

```bash
# Coach-side view (same endpoint, different groupBy)
... call_aggregate.py --since ... --until ... --group-by coachId --output /tmp/aura_per_coach.json

# Drop music listens (only the lower-rate tier)
... call_aggregate.py --since ... --until ... --no-music --output /tmp/aura_no_music.json

# Bigger top-N from the server (default 100; max 5000)
... call_aggregate.py --since ... --until ... --limit 500 --output /tmp/aura_top500.json

# Production rule overrides (defaults match the payout job)
... call_aggregate.py --since ... --until ... --ignore-daily-cap --include-free-listens

# Staging
... call_aggregate.py --since ... --until ... --base-url https://services.dev.aurahealth.io --output /tmp/aura_stg.json

# Show what would be sent without making the call
... call_aggregate.py --since ... --until ... --dry-run
```

Flags on `call_aggregate.py`:

| Flag | Default | Effect |
|---|---|---|
| `--since` / `--until` | required | ISO timestamps, half-open `[since, until)` |
| `--group-by` | `userId` | `userId` (this skill), `coachId` (per-coach sibling), `none` (summary-only — used by analyze-aura-cost) |
| `--limit N` | 100 | Server-side top-N. Caps at 5000. |
| `--users` / `--coaches` | none | Restrict to a known set of listeners or coaches |
| `--no-music` / `--no-non-music` | both on | Per-bucket inclusion |
| `--ignore-daily-cap` | respect | Production rule caps at 6 listens per (user, coach) per day |
| `--include-free-listens` | premium-paid only | Free / trial / payment-pending listens earn \$0 but inflate counts |
| `--base-url` | `https://services.aurahealth.io` | Override for staging |
| `--output PATH` | stdout | Write response JSON to disk |
| `--dry-run` | off | Print the assembled body to stderr and exit; no network call |

## Step 3 — Format the report

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-aura-cost-per-user/scripts/format_report.py \
  --input /tmp/aura_per_user.json \
  --label "<label from Step 1>" \
  --days-in <days_in from Step 1>
```

Common variants:

```bash
# Default: top 10 + headline + per-row tier breakdown for top-3 + window-wide tier table + stats
... format_report.py --input /tmp/aura_per_user.json --label "yesterday" --days-in 1

# Deeper rank cutoff
... format_report.py --input /tmp/aura_per_user.json --label "yesterday" --days-in 1 --top 25

# Show per-row tier breakdown for top-5 instead of top-3
... format_report.py --input /tmp/aura_per_user.json --label "yesterday" --days-in 1 --tiers-for 5

# Compact (no tiers, no stats, no headline — just the ranking table)
... format_report.py --input /tmp/aura_per_user.json --label "yesterday" --days-in 1 \
    --no-tiers --no-stats --no-headline

# Full structured payload (full UIDs, all users — keep this file local)
... format_report.py --input /tmp/aura_per_user.json --label "yesterday" --days-in 1 \
    --json /tmp/aura_per_user_full.json
```

Flags on `format_report.py`:

| Flag | Default | Effect |
|---|---|---|
| `--top N` | 10 | Rank cutoff on the table |
| `--tiers-for N` | 3 | Show per-row tier breakdown for the top N rows |
| `--no-tiers` | off | Suppress both per-row tier rows and the window-wide tier table |
| `--no-stats` | off | Suppress the distribution stats block |
| `--no-headline` | off | Suppress the one-line headline |
| `--json PATH` | n/a | Write the structured payload (full UIDs) to disk |

## Step 4 — Show the report inline

The formatter renders:

1. **Headline** — total qualifying listens, total cost, group count, exclusion footnote (daily cap / premium / content type), top listener's cost + multiple over the median.
2. **Top-N table** — rank, short UID, listens, cost, share, cumulative share. The top-`--tiers-for` rows include a tier-mix line.
3. **Window-wide tier table** — Listens + cost + share by tier (`<500`, `500–1999`, `2000–4999`, `≥5000`, `music`).
4. **Distribution table** — count + mean / median / p90 / p95 / p99 / max / std dev across enumerated users (cost per user).

Show the script's stdout verbatim. Don't paraphrase the numbers.

## JSON shape (`--json` output)

The formatter writes a wrapper around the original endpoint response plus computed stats:

```json
{
  "window": { "label": "...", "days_in": 7 },
  "summary": {
    "total_listens": 64012,
    "total_cost_cents": 920460,
    "groups": 12340,
    "tier_breakdown": { "lt500": { "listens": 38400, "cents": 230400 }, "...": "..." },
    "excluded_by_daily_cap": 1102,
    "excluded_by_premium": 31204,
    "excluded_by_content_type": 0
  },
  "stats_cost_cents": {
    "count": 100, "mean": 4520.0, "median": 1820.0,
    "p90": 8200.0, "p95": 11400.0, "p99": 28500.0, "max": 45200, "stddev": 5310.4
  },
  "top_users": [
    { "key": "<uid>", "listen_count": 412, "cost_cents": 4520, "tier_breakdown": { "...": "..." } }
  ],
  "users_all": [ "... full ranked list ..." ]
}
```

UIDs are truncated to `first8…` in the rendered markdown; the JSON output keeps the full UID so it's grep-able for downstream joins.

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

- **Cost is authoritative, not estimated.** The endpoint applies the production payout-rate logic on the same `userContentListen` table that `src/jobs/payout.js` reads. Deltas vs the actual monthly coach payout come from monthly-boundary edge cases only (a listen at 11:59 PM on the last of the month vs 00:01 AM on the 1st lands in different payout cycles, but its per-listen cost is identical).
- **Empty windows.** Future date ranges, or windows where no qualifying listens occurred, render `No qualifying listens in this window.` and exit 0 — not an error.
- **Distribution method.** Percentiles use `statistics.quantiles(..., method="inclusive")` because we have the full enumerated user set, not a sample. Guarantees p99 ≤ max. Stddev is sample (n-1) and returns `None` when `count < 2`.
- **Daily-cap excluded listens.** The headline shows how many listens were dropped by the `dailyCoachListenedCount <= 6` cap. This is production behaviour — those extra listens didn't pay the coach. `--ignore-daily-cap` flips them back in for "what if the cap were lifted?" analysis.
- **Premium-filter excluded listens.** Free / trial / payment-pending listens are excluded by default because they earn the coach \$0. `--include-free-listens` brings them in (listen counts go up; total cost is unchanged because they're zero-cost listens).
- **Privacy posture.** Firebase UIDs are not PII on their own, but they're user-identifiable in the Aura DB. UID truncation is enforced in the markdown table; full UIDs only appear in `--json` output. Do not paste rendered output into public channels without scoping. **Never write the full UID in chat or in committed test fixtures.**
- **Cowork sandboxes** strip host env (PROD-1305), so `AURA_API_TOKEN` won't reach the sandbox automatically. The skill fails fast with a clear error — paste-export the token per session as documented in CLAUDE.md → "Cowork constraints → Workaround".

## References

- Endpoint: [PROD-1961](https://linear.app/aura-health/issue/PROD-1961) — `POST /payouts/listens/aggregate` on PaymentService. Full schema + SQL + rate table.
- This card: [PROD-1962](https://linear.app/aura-health/issue/PROD-1962) — v2 swap from Mixpanel.
- Parent skill family: [PROD-1931](https://linear.app/aura-health/issue/PROD-1931).
- Family umbrella: [PROD-1928](https://linear.app/aura-health/issue/PROD-1928).
- v1 (now replaced): used Mixpanel `Meditation done single` × \$0.15 placeholder. v2 swap removes Mixpanel entirely.
- PaymentService home: `~/Development/Aura/Services/PaymentService`.
