---
name: analyze-aura-cost
description: >
  Report the total Aura cost (coach payout from listens) in a chosen window — today,
  week-to-date, month-to-date (default), previous month, last 7 days, last 30 days, or a
  custom range. Renders a headline (qualifying listens, real $ coach payout, distinct users,
  $/day run rate, $/user average), a cost-breakdown-by-tier table, and optionally a daily time
  series via `--by-day`. Uses the production payout-rate logic — tiered rates, music split,
  daily cap, premium-paid only, podcaster and deleted-coach exclusions — so cost is real $,
  not an estimate. Aggregate-only, no PII. Trigger on "aura cost this month", "aura cost
  report", "how much is aura spending", "aura coach payout total", "weekly aura cost",
  "monthly aura cost", "aura cost breakdown", "what are we paying coaches", "analyze aura
  cost", or "aura spend report". Do NOT use for per-user ranking
  (`eng:analyze-aura-cost-per-user`), per-coach earnings (`eng:analyze-aura-cost-per-coach`),
  or daily top-N (`eng:report-top-aura-listeners`).

metadata:
  version: "3.1.0"
knowledge:
  reads:
    - Guide/Index         # baseline routing aid (universal)
    - Tools/Aura API      # PaymentService endpoint reference + auth conventions
    - Tools/Slack         # channel directory + Post format footer spec for #eng-budget post
---

# Analyze Aura Cost

Reports the aggregate Aura listen cost (= coach payout from listens) in a chosen window. The macro view; siblings give per-user, per-coach, and condensed daily views:

- [`eng:analyze-aura-cost-per-user`](../analyze-aura-cost-per-user/SKILL.md) — listener-spend ranking
- [`eng:analyze-aura-cost-per-coach`](../analyze-aura-cost-per-coach/SKILL.md) — coach-earnings ranking
- [`eng:report-top-aura-listeners`](../report-top-aura-listeners/SKILL.md) — condensed daily Slack-ready ranking

All four share [PROD-1961](https://linear.app/aura-health/issue/PROD-1961) — the PaymentService `/payouts/listens/aggregate` endpoint — as their data dependency.

## Compliance

This skill is **aggregate-only** — no UIDs, no PII. Safe to post to public channels.

## Step 0 — Check prerequisites

- `AURA_API_TOKEN` must be set in `~/.claude/settings.json` under `env` (see CLAUDE.md → Secrets and environment). The token needs **admin scope** on PaymentService.
- `python3` on the path (stdlib only — no third-party packages).
- Default base URL is `https://services.aurahealth.io`. Override via `$AURA_API_BASE_URL` env or `--base-url` flag on `call_aggregate.py`.

If `AURA_API_TOKEN` is missing, the call_aggregate.py script fails fast with a clear error pointing to `update-config`. No silent degradation.

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
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-aura-cost/scripts/resolve_window.py --window mtd
# → {"from": "2026-05-01", "to": "2026-05-19", "from_iso": "...", "to_iso": "...", "label": "month to date (May 2026)", "days_in": 19}
```

Keep `from_iso` / `to_iso` / `label` / `days_in` for Steps 2 and 3.

## Step 2 — Fetch the aggregate from PaymentService

Use `--group-by userId --limit 0` so the server returns the `summary` block (including `summary.groups` = distinct user count, used for the headline's `$/user average` figure) without enumerating per-user rows. Don't use `--group-by none` — that mode is functionally similar, but PaymentService intentionally zeros `summary.groups` in `null`-group mode to avoid a misleading "distinct userId" interpretation, which would break the headline's user-count figure.

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-aura-cost/scripts/call_aggregate.py \
  --since <from_iso> \
  --until <to_iso> \
  --group-by userId --limit 0 \
  --output /tmp/aura_cost.json
```

Common variants:

```bash
# Drop music listens (lower rate tier)
... call_aggregate.py --since ... --until ... --group-by userId --limit 0 --no-music --output /tmp/aura_cost_no_music.json

# What would it cost if we didn't cap at 6 listens / (user, coach) / day?
... call_aggregate.py --since ... --until ... --group-by userId --limit 0 --ignore-daily-cap --output /tmp/aura_cost_uncapped.json

# Staging
... call_aggregate.py --since ... --until ... --group-by userId --limit 0 \
  --base-url https://services.dev.aurahealth.io --output /tmp/aura_cost_stg.json

# Show what would be sent without making the call
... call_aggregate.py --since ... --until ... --group-by userId --limit 0 --dry-run
```

See [`eng:analyze-aura-cost-per-user`](../analyze-aura-cost-per-user/SKILL.md#step-2--fetch-the-aggregate-from-paymentservice) for the full flag table on `call_aggregate.py` — it's the same script.

### Optional: fetch a per-day series for `--by-day`

If the user wants a daily breakdown, fetch one summary per day in the window and drop them into a directory keyed by date:

```bash
mkdir -p /tmp/aura_cost_daily/
for day in <list of YYYY-MM-DD days in the window>; do
  next=<day + 1 day in YYYY-MM-DD>
  python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-aura-cost/scripts/call_aggregate.py \
    --since "${day}T00:00:00Z" --until "${next}T00:00:00Z" \
    --group-by userId --limit 0 --limit 0 \
    --output /tmp/aura_cost_daily/${day}.json
done
```

The skill loop is one API call per day. A 7-day window = 7 calls (fast); a 30-day window = 30 calls (still under a minute total). For longer windows or higher cadence, file a v2 card to add a server-side `unit=day` option on the endpoint (PROD-1961).

## Step 3 — Format the report

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-aura-cost/scripts/format_report.py \
  --input /tmp/aura_cost.json \
  --label "<label from Step 1>" \
  --days-in <days_in from Step 1>
```

Common variants:

```bash
# Window aggregate (default)
... format_report.py --input /tmp/aura_cost.json --label "May 2026 month-to-date" --days-in 19

# Add a daily time series
... format_report.py --input /tmp/aura_cost.json --label "last 7 days" --days-in 7 \
    --by-day-dir /tmp/aura_cost_daily/

# Compact (no tier table)
... format_report.py --input /tmp/aura_cost.json --label "last 7 days" --days-in 7 --no-tiers

# Structured JSON payload
... format_report.py --input /tmp/aura_cost.json --label "last 7 days" --days-in 7 \
    --by-day-dir /tmp/aura_cost_daily/ --json /tmp/aura_cost_summary.json
```

Flags:

| Flag | Default | Effect |
|---|---|---|
| `--no-tiers` | off | Suppress the cost-by-tier table |
| `--no-headline` | off | Suppress the one-line headline |
| `--by-day-dir DIR` | n/a | Directory of `YYYY-MM-DD.json` per-day responses — appends a time-series table |
| `--json PATH` | n/a | Write a structured payload (window + summary + daily series) to disk |

## Step 4 — Show the report inline

The formatter renders:

1. **Headline** — total qualifying listens, total coach payout ($), distinct users, $/day run rate, $/user average, exclusion footnote (daily cap / premium / content type).
2. **Cost breakdown by tier table** — Listens + cost + share by tier (`<500`, `500–1999`, `2000–4999`, `≥5000`, `music`).
3. **Daily breakdown table** *(when `--by-day-dir` is set)* — one row per day with listens / cost / users, plus a Total row.

Show the script's stdout verbatim.

**Posting to Slack.** Aggregate-only, safe to post anywhere. If the user routes the report to `#eng-budget` (or any other channel), post it via `shared:post-to-slack` with `--footer eng:analyze-aura-cost --footer-extra prod` — the helper stamps the versioned `Tools/Slack` footer (`_Generated by `eng:analyze-aura-cost:<version>` · <YYYY-MM-DD> · prod_`) from `metadata.version` automatically. Don't hand-type the footer in the body — a model-typed footer drops the version.

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

Same rates and rules as the monthly payout job — see [PROD-1961](https://linear.app/aura-health/issue/PROD-1961) for the canonical definition.

## Notes & edge cases

- **Cost is authoritative, not estimated.** Same payout-rate logic as `src/jobs/payout.js` in PaymentService. Deltas vs the actual monthly coach payout come from monthly-boundary edge cases only.
- **Empty windows** render `No qualifying listens in this window.` and exit 0.
- **`--by-day-dir` doesn't fetch the responses**; the SKILL.md flow shows the loop. The formatter only reads pre-fetched files. Separating fetch and render keeps the script stdlib-only and easy to re-run with different rendering options without re-hitting the API.
- **Daily date math.** When iterating days for `--by-day`, use UTC `YYYY-MM-DD` boundaries — the file names need to be `YYYY-MM-DD.json` for the formatter to pick them up.
- **Cowork sandboxes** strip host env (PROD-1305), so `AURA_API_TOKEN` doesn't reach the sandbox automatically. Paste-export per session as documented in CLAUDE.md → "Cowork constraints → Workaround".

## References

- Endpoint: [PROD-1961](https://linear.app/aura-health/issue/PROD-1961) — `POST /payouts/listens/aggregate` on PaymentService.
- This card: [PROD-1964](https://linear.app/aura-health/issue/PROD-1964).
- Family parent: [PROD-1928](https://linear.app/aura-health/issue/PROD-1928).
- Sibling skill family pattern: [`eng:analyze-openai-costs`](../analyze-openai-costs/SKILL.md) — same window flags, same tier-breakdown shape, OpenAI domain.
- PaymentService home: `~/Development/Aura/Services/PaymentService`.
