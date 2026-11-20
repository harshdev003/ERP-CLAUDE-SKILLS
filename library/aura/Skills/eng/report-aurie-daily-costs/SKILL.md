---
name: report-aurie-daily-costs
description: >
  Renders a PER-DAY combined AI cost table — OpenAI LLM plus multi-vendor voice (ElevenLabs,
  Cartesia, Inworld, Deepgram, LiveKit) — over a window: month-to-date (default), last-7d, last-30d, or
  a custom range. One row per calendar day with OpenAI spend, a per-vendor voice split, the
  voice total and the combined total, plus a totals row and an ElevenLabs plan/overage context
  line. OpenAI and ElevenLabs figures are authoritative actuals; the three LiveKit-path
  vendors report ACTUAL usage but DERIVED dollars from pricing-sheet estimate rates. The
  latest day is flagged partial. A vendor-day that cannot be read renders as an em-dash and
  marks Voice and Combined as lower bounds — never as $0.00, which is indistinguishable from a
  legitimate quiet day. Trigger on "daily ai costs", "daily cost for openai and elevenlabs",
  "per-day cost table", "cost per day this month", "aurie daily costs", or "show daily costs
  month to date". Do NOT use for the weekly spreadsheet row (`eng:report-aurie-weekly-costs`).

metadata:
  version: "3.8.1"
knowledge:
  reads:
    - Guide/Index   # baseline routing aid (universal)
    - Tools/OpenAI  # Costs API context, per-model rates
    - Tools/ElevenLabs  # voice plan economics; fiat_units_spent is cents (÷100), plan+overage invoice model
    - Tools/Cartesia  # TTS usage/credits API + Scale-tier estimate rate
    - Tools/Deepgram  # STT usage API, prod project 7e42b984, nova-3 streaming rate
    - Tools/LiveKit   # forwarded GCP logs (session minutes), Cloud Scale estimate rate
    - Learnings/Aurie Cost Engineering/Cost Changes  # checked before escalating a ⚠ spike (Step 3)
    - Tools/Slack                                   # Slack post/read conventions + footer format
    - Tools/GCP                                     # Cloud Logging access (GCP_KEY)
    - Tools/BigQuery                                # aura-main.aurie_cost store access (GCP_KEY)
---

# Report Aurie Daily Costs

## Goal

A daily trend of combined AI spend — OpenAI LLM + **multi-vendor voice** (ElevenLabs + Cartesia + Inworld + Deepgram + LiveKit) — so you can eyeball day-over-day movement, spot spikes, and watch the ElevenLabs → LiveKit-path voice migration. One row per day over the chosen window with a per-vendor voice split, plus a totals row.

**Voice is multi-vendor (PROD-3180).** ElevenLabs (ConvAI path) $ is the actual billed value. The LiveKit-path vendors — Cartesia and Inworld (TTS), Deepgram (STT), LiveKit (transport) — are DAILY WORKSPACE TOTALS whose usage is ACTUAL but whose $ is DERIVED from **PRICING-SHEET ESTIMATE** rates, clearly labelled in the output:

| Vendor | Flag | Default | Basis |
|---|---|---|---|
| Cartesia | `--cartesia-rate-per-million` | `38.00` | Scale overage rate \$38/1M (plan of record: Scale, `Tools/Cartesia`) |
| Inworld | `--inworld-rate-per-million` | `25.00` | TTS-2 On-Demand list price \$25 per 1M characters ([inworld.ai/pricing](https://inworld.ai/pricing), checked 2026-10-19); characters ACTUAL from Inworld's usage API |
| Deepgram | `--deepgram-rate-per-hour` | `0.348` | Nova-3 Multilingual streaming PAYG \$0.0058/min (current) |
| LiveKit | `--livekit-rate-per-min` | `0.01` | LiveKit Cloud Scale **overage** rate — applies only beyond the allotment |

Replace the defaults with contract rates via the flags when they land. **Deepgram comes from the cost store** for every stored day (see *Deepgram comes from the cost store* below) — the dollars `eng:store-aurie-user-daily-cost` booked, including LiveKit Inference overage the Deepgram API cannot see. Because Deepgram is LiveKit-path-only it shows `\$0` on days with no LiveKit traffic — a real measurement. A day it could **not** read is `—` instead, never `\$0` (see *`—` is not `\$0`* below). These LiveKit-path vendor totals reuse the sibling `eng:store-aurie-user-daily-cost` skill's helpers (self-contained import, not a fork).

**ElevenLabs serves three callers (ENG-982).** Under the single `11Labs $` column, the report additionally splits the ElevenLabs day total by **calling API key** into the **ConvAI agent** bundle (`has_api_key=False` — ConvAI runs inside the ElevenLabs platform, so it has no caller key), the **Intelligence Service** raw-TTS path (what mobile calls), and **LiveKit TTS** (the LiveKit agent picks one TTS provider per session from Cartesia / ElevenLabs / OpenAI / Inworld, so this line swings between `\$0` and the entire LiveKit TTS bill as the selection changes — **neither extreme is a bug**; cross-check a surprising value against that day's per-provider LiveKit minutes before calling it misattribution). All three are actual billed values and sum to the `11Labs $` column; the split appears as a note under the table and as `elevenlabs_agent_usd` / `elevenlabs_intelligence_usd` / `elevenlabs_livekit_usd` in the `--json` output. **Never use `product_type` to identify the caller** — both raw-TTS callers report `product_type=TTS`. Canonical: `Tools/ElevenLabs` → *Attributing spend to the calling service*.

This is the **daily** member of the cost-skill family:

- [`eng:report-aurie-weekly-costs`](../report-aurie-weekly-costs/SKILL.md) — one Sun–Sat row for the spreadsheet + per-tier breakdown.
- [`eng:analyze-openai-costs`](../analyze-openai-costs/SKILL.md) — OpenAI project/model/prompter breakdowns for a window.
- [`eng:analyze-elevenlabs-costs`](../analyze-elevenlabs-costs/SKILL.md) — ElevenLabs quota / overage / model detail.

## Step 0 — Check prerequisites

Both admin keys are required; this skill has no MCP fallback (both endpoints are org/workspace-admin only).

```bash
test -n "$OPENAI_ADMIN_API_KEY" && echo "openai: OK" || echo "openai: MISSING"
if [ -n "$ELEVENLABS_ADMIN_API_KEY" ]; then echo "elevenlabs: OK"; \
elif [ -n "$ELEVENLABS_API_KEY" ]; then echo "elevenlabs: WARN (personal key, may under-count)"; \
else echo "elevenlabs: MISSING"; fi
```

If either is missing, send the user to the sibling skill's Step 0 for create + store + restart:

- `OPENAI_ADMIN_API_KEY` — see [`eng:analyze-openai-costs`](../analyze-openai-costs/SKILL.md).
- `ELEVENLABS_ADMIN_API_KEY` — see [`eng:analyze-elevenlabs-costs`](../analyze-elevenlabs-costs/SKILL.md). Minimum scope **Workspace Analytics: Access**; add **User: Read** for the plan/overage context line.

**Optional — LiveKit-path voice vendors** (a missing key makes that vendor's cells **unreadable, not zero** — they render `—` and are held out of the totals; the report still shows OpenAI + ElevenLabs actuals):

- `CARTESIA_ADMIN_API_KEY` — see [`eng:analyze-cartesia-costs`](../analyze-cartesia-costs/SKILL.md). Without it, Cartesia's cells are `—` for the whole window — Cartesia genuinely may be carrying the LiveKit TTS bill, so an unread Cartesia is not a \$0 one.
- `INWORLD_API_KEY` — Inworld's usage API (`GET /usage/v1/records`); the LiveKit agent's key. Without it, a day this run walked the LiveKit logs for is derived from the agent's own `tts_characters` (noted `inworld_derived`), and every other day's Inworld cell is `—`.
- `DEEPGRAM_OWNER_API_KEY` — see [`eng:analyze-deepgram-costs`](../analyze-deepgram-costs/SKILL.md) (reads the prod project `7e42b984-…`). Only needed for **unstored** days with direct (non-Inference) STT; stored days read the cost store.
- `GCP_KEY` / `GOOGLE_APPLICATION_CREDENTIALS` / ADC — reads the BigQuery cost store for stored LiveKit minutes, and the forwarded GCP logs for the days it lacks; see [`eng:analyze-livekit-usage`](../analyze-livekit-usage/SKILL.md).

Other prereqs: `python3` with `requests` (`pip3 install requests`); `google-cloud-bigquery` for the cost-store read and `google-cloud-logging` for the live LiveKit walk. Without `google-cloud-bigquery` every day falls back to the live walk — correct, but slow, and quota-bound past ~2 days.

## Step 1 — Resolve the window

Presets:

- **Month-to-date** (`mtd`, **default**) — 1st of the current calendar month (UTC) → today.
- **Last 7 days** (`last-7d`) — rolling 7 days ending today.
- **Last 30 days** (`last-30d`) — rolling 30 days ending today.
- **Custom** — `--from YYYY-MM-DD --to YYYY-MM-DD` (both inclusive, UTC).

> **ElevenLabs retention:** the workspace usage endpoint reliably returns ~the last 10 days. For windows older than that, the ElevenLabs column may be partial/empty — prefer `eng:report-aurie-weekly-costs` (which backfills with `--since/--until`) for historical voice cost.

If the user names a month ("April", "2026-04"), translate to `--from`/`--to` for that calendar month.

## Step 2 — Run the report

```bash
# Month-to-date (default)
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-aurie-daily-costs/scripts/report.py --window mtd

# Last 7 / 30 days
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-aurie-daily-costs/scripts/report.py --window last-7d
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-aurie-daily-costs/scripts/report.py --window last-30d

# Custom range + JSON
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-aurie-daily-costs/scripts/report.py \
  --from 2026-06-01 --to 2026-06-05 --json /tmp/daily_costs.json

# Re-derive every day's LiveKit minutes from the forwarded logs instead of the
# cost store. Only for auditing the store — see "LiveKit minutes come from the
# cost store" below for why this is quota-bound past ~2 days.
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-aurie-daily-costs/scripts/report.py \
  --window last-7d --no-bigquery
```

The script prints a markdown table and (with `--json`) writes the structured payload (`rows[]` with a per-vendor `<vendor>_usd` each — `inworld_usd` included — per-source `total_<vendor>_usd`, `elevenlabs_subscription`). A vendor value that could not be read is `null` in the JSON and `—` in the table; `voice_incomplete` and `unread_vendor_days` say whether the totals are complete.

## Step 3 — Show the report inline

Show the script's stdout directly. Lead with a one-line headline pairing the two sources, e.g. _"June so far: \$798 OpenAI + \$549 ElevenLabs voice consumption = ~\$1,346 combined; ElevenLabs is in overage at 154% quota."_

The report diagnoses its own spikes — a day standing materially above the window median is
attributed to the project that moved, and labelled either a **recurring scheduled batch** or an
**unexplained jump**. Relay those lines as they come out; the classification is computed from the
per-project cost history, not judged at read time.

**The ⚠ marker is the whole signal — act on it, not on the batch/jump label.**

- **No ⚠** — a scheduled batch running at its usual level. Report it as routine and in range. Do
  **not** escalate or open a root-cause pass.
- **⚠** — **check `Learnings/Aurie Cost Engineering/Cost Changes` before escalating.** Resolve
  that path with `shared:retrieve-knowledge` and look for a row whose `Impact` names the flagged
  day. If one does, report the day as **explained** — cause, fix status, and the row's date — and
  do **not** open a root-cause pass. Only when no row accounts for it: describe the number and
  hand off for root-cause to `eng:analyze-openai-costs` (by project/prompter) or
  `eng:analyze-elevenlabs-costs` (quota/overage). Don't diagnose it here either. A batch day
  carries ⚠ when the line says so, and the line names why: the batch came in off its usual
  level, and/or a second project moved on the same day.

That second case is why a batch day is never waved through on the label alone. A batch's own
quiet-to-batch delta is large enough to account for the day by itself, so the script re-tests what
the batch does *not* explain; if the day is still elevated with the batch set aside, it names the
other project and marks the day ⚠. A recurring batch therefore suppresses escalation only for the
cost it actually accounts for.

A recurring batch is only recognisable with enough same-weekday history, so the script pulls ~35
days of per-project cost regardless of the requested window. On a short window the batch day is
still classified correctly; the window itself stays as asked.

**Why the ledger comes first.** A ⚠ is a statement about the cost history, not about whether
anyone already knows the cause — the classifier reads per-project cost and has no view of the
ledger, so a spike that is diagnosed *and fixed* keeps carrying ⚠ for every run until it leaves
the window. On 2026-08-21 `Conversation [Production]` ran \$1,125.78 (+\$956.95 over its usual) and
the day was flagged ⚠ with no recurring Friday pattern, while a `Cost Changes` row dated
2026-08-24 already attributed ~87% of the day to prompt-testing traffic on the `Claude`-named
service-account keys and recorded the fix (both QA skills moved to `OPENAI_EVALUATIONS_API_KEY`).
Escalating on the marker alone re-opens a closed question on every tick, which is how a cadence
teaches its operator to ignore ⚠.

## Step 4 — Ask where to route it

Do NOT route automatically. Offer:

1. **Post to Slack** *(optional)* — post to `#eng-budget` (`C05PC18APLH`, canonical ID in `Tools/Slack`) by composing [`shared:post-to-slack`](../../../shared/skills/post-to-slack/SKILL.md), passing `--footer eng:report-aurie-daily-costs`. The helper resolves `:<version>` from this skill's `metadata.version`; never hand-type the footer line, which silently drops the version.
2. **Save Markdown** to a path the user provides.
3. **Nothing, just show it.**

## Notes & edge cases

- **Scheduled batches make some weekdays legitimately expensive.** `Insights [Production]` runs a
  per-user compile+renderer batch weekly, which lands on one weekday (Sundays, in UTC buckets) at
  roughly an order of magnitude above its quiet-day level, and can straddle UTC midnight into the
  following day. It sits in the **ambient** cost bucket, so it does not distort `$/message`
  (`eng:store-aurie-user-daily-cost` owns that classification). Its scope is users active in the
  prior week, not the whole base. The spike diagnosis above detects this from the cost history
  rather than from a hardcoded project or day, so a batch that moves to another weekday, changes
  level, or a second batch appearing elsewhere are all picked up without a code change.
- **ElevenLabs $ is consumption value, not the invoice.** The column is `fiat_units_spent ÷ 100` (the API returns cents) — it reconciles to the dashboard "Cost before overage surcharge" tile, but the actual bill is **flat plan + `current_overage`**, surfaced in the context line below the table. Never present the consumption column as the amount owed.
- **Timezone.** Both axes are bucketed **UTC** for clean per-day alignment. The ElevenLabs dashboard is PT-anchored, so a given calendar date can differ by a few hours' usage from the dashboard. `eng:report-aurie-weekly-costs` deliberately anchors ElevenLabs to PT to match the dashboard for its weekly row — this skill prioritises OpenAI-aligned daily buckets instead.
- **Partial last day.** OpenAI's Costs API lags ~24h, so the latest day is incomplete and flagged `*(partial)*`. ElevenLabs is near-real-time.
- **Numbers may lag the dashboards** by minutes (ElevenLabs) to ~24h (OpenAI's most recent bucket).
- **Voice is multi-vendor and mostly ESTIMATED.** Only the ElevenLabs column is an actual billed figure. Cartesia / Inworld / Deepgram $ are DERIVED from pricing-sheet ESTIMATE rates (their *usage* — credits / characters / hours — is actual). LiveKit $ is the ACTUAL invoice: a subscription accrual plus measured overage. The estimate-rate note prints under the table; never present the LiveKit-path columns as billed dollars. Swap in contract rates via `--cartesia-rate-per-million` / `--deepgram-rate-per-hour` / `--livekit-rate-per-min`.
- **`—` is not `\$0`.** A vendor-day the report could not read renders `—`, is excluded from that vendor's column total, and marks that row's Voice and Combined figures `≥` (lower bounds, since the components that *were* read are still real). This distinction is load-bearing rather than cosmetic: Deepgram and LiveKit are LiveKit-path-only, so a genuine `\$0.00` day is legitimate and common, and a placeholder zero is indistinguishable from one. The report used to render exactly that — on a `last-7d` window on 2026-08-31, four consecutive days showed `\$0.00` LiveKit against \$34.84 actually stored, and the Voice and Combined totals were short by the same amount with nothing in the table looking wrong. The sibling store helpers already return a note **only** on a real read failure (never for a quiet day), so the signal was always there; this report simply stopped discarding it. [ENG-1214](https://linear.app/aura-health/issue/ENG-1214).
- **LiveKit minutes come from the cost store, not a log walk per day.** A day of forwarded LiveKit logs is ~21k entries ≈ 20 Cloud Logging read requests, against a **60/min** project cap (`logging.googleapis.com/read_requests`, ReadRequestsPerMinutePerProject) — so one query per day exhausted the quota partway through the third day and every day after it failed. Minutes and dollars are read from `aura-main.aurie_cost` for every stored day, and only unstored days — normally just today's partial — walk the logs live. A stored day is shown at the dollars **booked** for it (subscription accrual + overage); the live fallback is priced on the same basis, so a window mixing the two is on one scale. `--livekit-rate-per-min` sets the **overage** rate only — it does not price minutes inside the plan allotment, which are covered by the subscription.
  Recovery divides the store's own LiveKit rate back out of `voice_livekit`, and deliberately does **not** join `aurie_daily_unit_costs` the way [`eng:report-livekit-daily-cost`](../report-livekit-daily-cost/SKILL.md) does. That join was the first implementation here and is the intuitive one; it is wrong for this skill on two measured counts. The unit-cost table carries a rate for **41 of 91** stored days, so 50 stored days — 47 of them genuinely quiet — fell through to a live walk, which put the quota pressure straight back on any window past about a week. And its rate is a *blended* (Cartesia + Deepgram + LiveKit) ÷ minutes figure rounded to 4 dp, landing within ~0.2% where the direct inversion lands within 0.001%.
  **A stored `\$0` LiveKit day stays `\$0.00`.** Absence from the store — not a zero in it — is the only thing that means "walk this day"; a recorded quiet day is a measurement, and promoting it to `—` would both waste quota and lose information the store already has. `--no-bigquery` forces the live path for auditing; the live walk paces itself under the quota and retries a rejection, so a wide window is slow rather than wrong.
- **Deepgram comes from the cost store, not the Deepgram API.** The LiveKit agent's STT runs on **LiveKit Inference**, which Deepgram's usage API cannot see — it answers a well-formed `\$0` for an Inference day. The store books that day as overage past the plan's included Inference credit block (`deepgram_inference_day`), so on 2026-09-29 it held `voice_deepgram` = \$10.40 while this report, reading the API, printed `\$0.00` and understated Voice and Combined by the same amount — the ENG-1214 failure class again. Stored days now read `SUM(voice_deepgram)` plus `deepgram_inference_drawdown` from `aura-main.aurie_cost.aurie_per_user_daily_cost` (base table, a separate query from LiveKit's). A stored `\$0` is `\$0.00` when the day drew on the block (billed `\$0` by design — the block is part of the subscription already in the LiveKit column; a note says so) or had no traffic; a stored `\$0` with traffic and no drawdown is `—`, because the store's write-time tag isn't persisted. **Unstored days** (normally today) are priced the way the store would: the LiveKit walk's STT transport split picks the source, Inference minutes go through the store's credit-block helper against the month-to-date drawdown, direct minutes through `DEEPGRAM_OWNER_API_KEY` × `--deepgram-rate-per-hour`. No transport, unmeasured Inference, or an unreadable drawdown → `—`, never `\$0.00`.
- **Scope.** OpenAI total = all projects summed (LLM). Voice total = ElevenLabs + Cartesia + Inworld + Deepgram + LiveKit. Fish Audio TTS is not a column here (it is derived from agent logs only, which this report reads for unstored days alone) — read it from the store's `voice_fish`. Aura content-listen coach payouts and other cost sources are out of scope — see the `eng:analyze-aura-cost*` family.
