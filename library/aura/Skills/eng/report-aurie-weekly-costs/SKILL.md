---
name: report-aurie-weekly-costs
description: >
  Produces the weekly LLM plus Voice cost row for the Aurie cost-tracking spreadsheet, plus an
  LLM breakdown by AI premium tier for a Sun-Sat week, a per-vendor voice split (ElevenLabs vs
  the LiveKit path), and an optional voice-by-tier breakdown. Tier labels are AI-prefixed to
  disambiguate from Aura content premium. Sums the OpenAI Costs API for the LLM total,
  allocates across tiers by token weight from the LLM-call ledger, and reads the billed amount
  from ElevenLabs; LiveKit-path vendors report actual usage with derived dollars. Emits a
  markdown row plus TSV line, then the vendor and tier tables Defaults to the last completed
  Sun-Sat. Trigger on "report aurie weekly costs", "weekly cost report", "weekly llm + voice
  costs", "fill in the cost spreadsheet row", "weekly aurie spend", or "weekly costs by
  subscription tier". Do NOT use for date-range OpenAI breakdowns
  (`eng:analyze-openai-costs`), ElevenLabs quota reports (`eng:analyze-elevenlabs-costs`), or
  per-user drill-downs (the `*-cost-per-user` skills).

metadata:
  version: "3.9.1"
knowledge:
  reads:
    - Guide/Index   # baseline routing aid (universal)
    - Tools/OpenAI  # Costs API context; three-state semantic on aiPremium booleans
    - Tools/ElevenLabs  # workspace usage endpoint (fiat_units_spent), ConvAI conversations walk
    - Tools/Cartesia  # TTS usage/credits API + Scale-tier estimate rate
    - Tools/Deepgram  # STT usage API, prod project 7e42b984, nova-3 streaming rate
    - Tools/LiveKit   # forwarded GCP logs (session minutes), Cloud Scale estimate rate
    - Tools/GCP       # LiveKit session-minutes source (forwarded logs, optional vendor split input)
    - Tools/BigQuery                 # aura-main.aurie_cost store access (GCP_KEY)
    - Tools/Aura API                 # Aura API access pattern (AURA_API_TOKEN)
  writes:
    - Signals/Aurie Weekly Cost   # opt-in Step 4: record this week's cost row as a dated snapshot
---

# Report Aurie Weekly Costs

## Goal

Produce the weekly cost row Jerome pastes into the cost-tracking spreadsheet, **plus** an LLM breakdown by AI premium tier so the row has context. Three things per Sun–Sat week:

- **LLM costs** — total OpenAI spend (`/v1/organization/costs`, summed across all projects)
- **Voice costs** — total ElevenLabs spend pulled directly from the `fiat_units_spent` metric on `/v1/usage/character-stats`. The API returns the value in cents; the script divides by 100. This is the authoritative billed amount and reconciles within ~1% of the dashboard's "Cost before overage surcharge" tile — no credits-per-minute or overage-rate assumptions. (The top-line `Voice costs` column in the paste row remains this ElevenLabs figure for spreadsheet continuity; the multi-vendor total appears in the per-vendor breakdown below.)
- **Voice breakdown by vendor (additive, PROD-3180)** — an ElevenLabs-vs-LiveKit-path split. ElevenLabs itself serves TWO roles and is shown split by calling API key: the **ConvAI agent** bundle and a **LiveKit-path TTS** provider (both actual billed, summing to the ElevenLabs total). The LiveKit-path vendors — Cartesia (TTS) + Fish Audio (TTS) + Inworld (TTS) + Deepgram (STT, including LiveKit Inference) + LiveKit (agent minutes, overage included) — are read from the daily cost store for every day it holds (the dollars `eng:store-aurie-user-daily-cost` booked, same columns as `--from-bigquery`; a `livekit_path_source` note says how many days). Only an unstored day falls back to the vendor DAILY-WORKSPACE-TOTALS, whose $ is DERIVED from rates and misses the month-to-date LiveKit overage and LiveKit Inference STT (flagged by a `livekit_path_unstored` note); there Fish and Inworld come from the store's own helpers — Fish from the agent-logged characters, Inworld from its usage API with the agent logs as fallback (`--cartesia-rate-per-million 38.00` = Cartesia Scale overage, `Tools/Cartesia`; `--deepgram-rate-per-hour 0.348` = Nova-3 Multilingual PAYG (current); `--livekit-rate-per-min 0.01` = LiveKit Ship agent-session), clearly labelled and swappable via the flags. **Deepgram reads the production project** `7e42b984-…` (**“Aura Health”**) — audio-hours are ACTUAL. Being LiveKit-path-only, it shows `\$0` for a week with no LiveKit traffic (or a `deepgram_pending` note if the key is missing / loses access). These totals reuse the sibling `eng:store-aurie-user-daily-cost` skill's vendor helpers. `--json` output carries `elevenlabs_agent_usd` / `elevenlabs_livekit_usd` and `fish_usd` / `inworld_usd`; `livekit_path_usd` = Cartesia + Fish + Inworld + Deepgram + LiveKit. Suppress with `--no-voice-vendor-breakdown`.
- **LLM breakdown by AI premium tier** — AI Premium Paid / Trial / Free / Payment Pending / Non AI Premium / Never AI Premium / (untagged). Each project's dollar total is allocated across tiers in proportion to token weight per call. **Attribution reads the Intelligence Service LLM-call ledger first** (BigQuery `aura-main.llm.llm_call` — one row per OpenAI call, Chat Completions AND Responses APIs, with `aiPremiumState`/`premiumState` and token counts as columns; docs: [LLM Calls](https://app.notion.com/p/aurahealth/LLM-Calls-3a675e23af208127b217e53af47524e8)) for the part of the window at/after the ledger epoch (**2026-07-23T03:25Z**, Intelligence Service 0.285.0), with **automatic failover** to the OpenAI stored-chat-completions walk (`metadata.aiPremium` + `metadata.aiPremiumState` tags from [ENG-498](https://linear.app/aura-health/issue/ENG-498)) when the ledger can't be read. Pre-epoch slices ALWAYS use stored completions — the ledger has no rows before the epoch. Note the fallback is chat-API-only and cannot see Responses-API traffic; the report annotates which source it used via an `Attribution source:` line. The Costs API remains the $ authority — the ledger only replaces the token-weight/attribution walk. Labels are AI-prefixed throughout to disambiguate from Aura's content premium (which is a separate subscription tracked under `metadata.premium*` and rendered by the sibling skill `eng:analyze-openai-cost-per-subscription-state`). Suppress with `--no-tier-breakdown` when only the paste row is needed.
- **Voice breakdown by AI premium tier (optional, opt-in only)** — same tier labels, applied to ElevenLabs spend. The workspace `/v1/usage/character-stats` endpoint doesn't carry subscription metadata, so this slice requires walking `/v1/convai/conversations` for per-user voice $ and joining on Firebase UID against the same OpenAI metadata as the LLM breakdown. Slow (~15–20 min for a typical week). After the main row renders, Step 3 prompts the user whether they want it — the default flow stays fast and unchanged when declined.

Other spreadsheet columns (LLM cost / active user, Processing, Margin $/%) are out of scope and rendered as `—`.

## Week boundary semantics

- **Default boundary detection** uses `America/Los_Angeles` (PDT in summer, PST in winter, auto-switched). The question "what's the most recent completed Sun–Sat week?" is answered in Pacific time so it matches your local sense of when "this week" became "last week".
- **OpenAI query window** is **UTC-aligned daily buckets**: the calendar date of the Pacific Sunday is used as the start of a UTC midnight → UTC midnight window. Matches how `/organization/costs` natively aggregates.
- **ElevenLabs query window** uses the same calendar dates re-anchored at **Pacific (America/Los_Angeles) midnight**. The ElevenLabs dashboard's date picker is timezone-aware to the viewer's browser — a teammate in Vancouver picking "May 3 → May 8" gets a different UTC window than a teammate in Arabic Standard Time picking the same dates. Pinning the script to Pacific (where the team is based) makes the voice number match what teammates see in the dashboard, regardless of where the script happens to run. Reconciles exactly with the dashboard's "Cost before overage surcharge" tile for the same Sun–Sat range.
- **`--since/--until` and `--week`** accept calendar dates; OpenAI sees them as UTC midnights, ElevenLabs as Pacific midnights.

## BigQuery backfill mode (`--from-bigquery`)

The default (live-API) path can't produce an accurate row for a week older than ~10 days: ElevenLabs only retains that many days of usage buckets, so voice comes back partial with no signal that it's incomplete. `--from-bigquery` sidesteps this by reading the **persisted** daily cost store — `aura-main.aurie_cost.aurie_per_user_daily_cost`, maintained by [`eng:store-aurie-user-daily-cost`](../store-aurie-user-daily-cost/SKILL.md) — which has gapless per-user daily coverage (from 2026-06-01). Use it to backfill an older/missed week, or when a late run has already crossed the retention wall. It also makes the voice-by-tier cut instant (the store already has per-user voice $, so there's no ~15–20 min `/convai/conversations` walk).

What it does:

- Authenticates to BigQuery by `GCP_KEY` (the same service account the other cost skills use) **or, when that is unset, Application Default Credentials** — whatever `gcloud auth application-default login` left behind, read-only in practice and enough for this path. Neither OpenAI nor ElevenLabs keys are needed. The 10-day retention guard is skipped — the store persists full history. The report names the winning route in a `note:` line, so the basis is never implicit. **A workstation with a working `gcloud` login has this path available even with no `GCP_KEY` export** — check both before concluding BigQuery is unavailable and falling back to a ~20-minute vendor-API walk.
- Aggregates the per-user rows over the resolved Sun–Sat window: LLM = `SUM(llm_cost)`, Voice = `SUM(voice_cost)`, vendor split from the `voice_11labs_agent / voice_11labs_intelligence / voice_11labs_livekit / voice_cartesia / voice_fish / voice_inworld / voice_deepgram / voice_livekit (+ voice_livekit_platform)` columns. Fish and Inworld each get their own row in `--detail`'s vendor table and key in `--json`'s `voice_by_vendor`.
- Renders the **same** spreadsheet row + TSV line (so it pastes into the same sheet), plus LLM and voice breakdowns **by the store's 5-bucket `subscription_state`** — `paid` / `trial` / `free` / `grace` / `other`. The `Other` bucket is the store's unattributed reconciling remainder (`user_id='Other'`), labelled as such.

**The two paths' `Voice costs` cells are different quantities.** The live path's cell is the ElevenLabs workspace total alone; the BigQuery cell is every voice vendor, LiveKit transport and Cartesia included. The same week reads \$2,291.08 live and \$2,412.27 from the store — a real \$121 spread, not drift. Keep one basis per spreadsheet column: paste the live figure into a column whose history is ElevenLabs-only, and say which basis a backfilled row used. The per-vendor table under each row is what makes the basis legible.

**Taxonomy caveat.** This is a *different* cut from the live path's 7-bucket `aiPremium*` tiers (AI Premium Paid/Trial/Free/Payment Pending/Non/Never/untagged) — the two are not directly comparable at the tier level, and the output labels this explicitly. The **top-line LLM/Voice numbers do match** the live path: LLM reconciles to the OpenAI org-cost total to the penny; voice is the per-user rate-derived sum, which runs ~1–2.5% under ElevenLabs' billed workspace total (the same gap the live path's voice-tier reconciliation line already documents).

**Partial weeks.** The store is written a day in arrears, so a run made right after a week closes can find the final day missing — those days sum as \$0 and understate every figure. The report now counts the distinct dates present against the days the window asks for and emits a `PARTIAL WEEK:` note naming the shortfall. Treat that note as disqualifying for a spreadsheet row: backfill the missing dates with `eng:store-aurie-user-daily-cost`, or take the top-line row from the live path.

**Coverage.** Windows that start before 2026-06-01 (or before the store first ran for those dates) come back understated with a `note:` line — the store simply has no rows there. The live path remains the only source for pre-store weeks.

Uses the bundled `bq.py` helper (canonical in `eng:query-bigquery`); `--no-tier-breakdown` and `--no-voice-vendor-breakdown` behave the same as the live path.

**This is the preferred path whenever the question is paid vs non-paid** — confirmed 2026-09-27. It returns both the LLM and the voice cut in one ~30-second run against the ~15–20 minute `/convai/conversations` walk Step 3 needs for the voice half, and it depends on no per-project OpenAI key — the failure mode that makes the live voice split understate paid cannot occur. Keep the live path for the 7-bucket AI-premium taxonomy, or when a week's ElevenLabs numbers must come from the billed source directly.

The decisive reason is coverage, not speed: the Step 3 walk sees ElevenLabs Conversational AI only. In the week of 9/20/2026 that left the LiveKit-path TTS — **38% of voice spend** — with no attribution at all, and reporting the ConvAI-only share as though it covered the voice row understated paid badly (76.4% ConvAI-only against the store's 90.5% all-vendor). The store carries every vendor per user.

Measured against each other on the matched 6-day window 9/20–9/25/2026:

| | Live | Store |
|---|---:|---:|
| LLM | \$3,453.81 | \$3,453.81 |
| Voice | \$1,868.44 | \$2,007.28 |
| LLM paid share | 81.2% | 84.9% |

- **LLM ties to the penny** on a matched window. An apparent gap is a window artifact (see the partial-week note below), not a denominator difference — do not explain one away as "the store only covers Aurie users".
- **The voice cells are different quantities.** The store's ElevenLabs subtotal runs ~1.3% over the live cell (per-user attribution), plus ~\$115/week of Cartesia, Deepgram and LiveKit transport the live cell structurally cannot see. Never diff them directly. **`Other` counts as non-paid, so a fat `Other` understates paid:** the paid/non-paid table prints a `WARNING:` when `Other` holds >5% of voice, with paid's share of *attributed* voice beside it (week of 10/18/2026: \$425.91 of LiveKit pool overage on `Other`, 12.0% of voice — paid read 83.2% vs 94.5% attributed; the store now allocates it per user). On a warned week, quote paid from attributed voice and name the `Other` dollars.
- **The tier taxonomies disagree by ~3.7pp on paid** on an identical total — the store books as paid what the ledger's per-call `aiPremiumState` books as free. Mechanism unconfirmed (per-user-day vs per-call granularity is the plausible one). Don't mix the two cuts in one spreadsheet column.


## Inputs

- `--week YYYY-MM-DD` — Sunday (calendar date) of the week to report. Default: the most recent completed Sunday in Pacific time.
- `--since/--until YYYY-MM-DD` — explicit half-open range, queried at UTC midnight. Use to backfill an older row or override the Sun–Sat default. Skips the 10-day retention safety check.
- `--no-tier-breakdown` — skip the per-tier LLM walk. Faster (no per-project chat-completions iteration) — use when you only need the TSV row.
- `--no-voice-vendor-breakdown` — skip the additive per-vendor voice split (ElevenLabs vs LiveKit path). Use when only the LLM/voice paste row is needed.
- `--cartesia-rate-per-million` / `--deepgram-rate-per-hour` / `--livekit-rate-per-min` — override the rates used to DERIVE the LiveKit-path vendor $ (defaults 38.00 = Cartesia Scale overage; 0.348 = Deepgram Nova-3 Multilingual PAYG; 0.01 = LiveKit Ship agent-session — all confirmed 2026-07-02). Replace on a plan change. `--fish-rate-per-million` / `--inworld-rate-per-million` do the same for Fish Audio and Inworld TTS; they default to the store skill's own rates (`eng:store-aurie-user-daily-cost`), so only an unstored day's recompute uses them.
- `--from-bigquery` — source the whole report from the BigQuery daily cost store instead of the live OpenAI/ElevenLabs APIs. See "BigQuery backfill mode" below. Needs `GCP_KEY` **or** ADC (not the OpenAI/ElevenLabs keys); bypasses the 10-day retention guard; reports tiers by the store's 5-bucket `subscription_state`, not the 7-bucket AI-premium taxonomy. Its default output is the row + a paid/non-paid summary; add `--detail` for the per-vendor voice table and the 5-bucket LLM/voice tier tables (Step 4d records them in the snapshot body).
- `--json PATH` — also emit structured JSON for downstream tooling (adds a `voice_by_vendor` block; `voice_cost_usd` stays the ElevenLabs figure for backward-compat). In `--from-bigquery` mode the JSON shape differs — see that section.

## Workflow

### Step 0 — Check prerequisites

Both env vars are required **for the default live-API path**. This skill has no MCP fallback: the OpenAI Costs API and the ElevenLabs `/usage/character-stats` workspace endpoint both demand admin-scoped keys. (The `--from-bigquery` path is the exception — it needs `GCP_KEY` **or** ADC instead of these two; see "BigQuery backfill mode" above.)

```bash
test -n "$OPENAI_ADMIN_API_KEY" && echo "openai: OK" || echo "openai: MISSING"
test -n "$ELEVENLABS_ADMIN_API_KEY" && echo "elevenlabs: OK" || echo "elevenlabs: MISSING"
test -n "$GCP_KEY" && echo "ledger (BigQuery): OK" || echo "ledger (BigQuery): MISSING GCP_KEY (tier attribution falls back to stored completions)"
# --from-bigquery mode only:
# Either route works; ADC is the fallback when GCP_KEY is unset.
test -n "$GCP_KEY" && echo "gcp key: OK" || echo "gcp key: unset (will try ADC)"
python3 -c "import google.auth; google.auth.default()" 2>/dev/null && echo "gcp adc: OK" || echo "gcp adc: MISSING"
```

If either is missing, send the user to the sibling skill's setup before retrying:

- `OPENAI_ADMIN_API_KEY` — see [`eng:analyze-openai-costs`](../analyze-openai-costs/SKILL.md) Step 0 for create + store + restart.
- `ELEVENLABS_ADMIN_API_KEY` — see [`eng:analyze-elevenlabs-costs`](../analyze-elevenlabs-costs/SKILL.md) Step 0. The minimum scope is **Workspace Analytics: Access**; **User: Read** is not required by this skill (we don't read the subscription).
- `GCP_KEY` — **recommended, not hard-required.** The service account that reads the Intelligence Service LLM-call ledger (BigQuery `aura-main.llm.llm_call`). The ledger is the primary attribution source for post-2026-07-23 slices; when the key is missing or the read fails, the script prints a stderr warning and falls back to the stored-chat-completions walk for that slice — which is chat-API-only and cannot see Responses-API traffic, so Responses-API tokens land in `(untagged)`.

**Required for the Step 3 voice-by-tier breakdown — `OPENAI_<PROJECT>_API_KEY`.** The UID → tier join walks each production project's stored completions with a project-scoped key (PROD-3572); the Admin key cannot hold the `model.request` scope that endpoint enforces. The env var name is derived from the project display name, so `Conversation [Production]` reads `OPENAI_CONVERSATION_API_KEY`. A project whose key is unset is skipped, and its users reach the join with no tier signal — they land in the free bucket and understate paid share. Step 3 fails closed on this (see below), so check before committing to the ~15–20 minute walk:

```bash
for p in CONVERSATION MEMORY FEED DEVELOPMENT FRONT PRODUCTION TRANSLATION CONTENT; do
  v="OPENAI_${p}_API_KEY"
  test -n "${!v}" && echo "$v: OK" || echo "$v: MISSING"
done
```

`--from-bigquery` needs none of these — the store carries `subscription_state` per user, so it is the better source whenever the question is paid vs non-paid rather than the 7-bucket AI-premium cut.

**Optional — LiveKit-path voice vendors** (for the per-vendor voice split; a missing key degrades that vendor to `\$0` + a note, and the LLM/ElevenLabs row is unaffected):

- `CARTESIA_ADMIN_API_KEY` — see [`eng:analyze-cartesia-costs`](../analyze-cartesia-costs/SKILL.md).
- `DEEPGRAM_OWNER_API_KEY` — see [`eng:analyze-deepgram-costs`](../analyze-deepgram-costs/SKILL.md) (reads the prod project `7e42b984-…`; LiveKit-path-only, so `\$0` on a no-LiveKit week).
- `GCP_KEY` / `GOOGLE_APPLICATION_CREDENTIALS` / ADC — LiveKit session-minutes from the forwarded GCP logs; see [`eng:analyze-livekit-usage`](../analyze-livekit-usage/SKILL.md).

Other prereqs: `python3` with `requests`. Install via `pip3 install requests` if the script errors on import; `google-cloud-logging` for the LiveKit minutes.

### Step 1 — Run the report

**Run BOTH commands. They answer different questions and neither substitutes for the other.**

```bash
# 1of2 — the BILLED spreadsheet row (live APIs, ~10–15 min: the LiveKit GCP-log walk dominates — run in the background).
#        --no-tier-breakdown is deliberate: the store does the tier cut better and faster.
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-aurie-weekly-costs/scripts/report.py \
  --no-tier-breakdown

# 2of2 — the PAID vs NON-PAID cut, all voice vendors (BigQuery store, ~30s). --detail adds the vendor + tier tables Step 4d records.
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-aurie-weekly-costs/scripts/report.py \
  --from-bigquery --detail
```

Why both, and why this order:

- **The row must come from the live path.** Its `Voice costs` cell is ElevenLabs' billed
  workspace total, which is the basis the spreadsheet column's history is on. The store's
  voice figure is a different quantity (all vendors) and pasting it silently changes the
  column's meaning.
- **The tier cut must come from the store.** The live path's voice tier walk (Step 3) sees
  ElevenLabs Conversational AI only. In the week of 9/20/2026 that left the LiveKit-path
  TTS — **38% of voice spend** — with no attribution, and the ConvAI-only paid share
  (76.4%) read as if it covered the whole voice row against the store's all-vendor 90.5%.
  It also costs ~15–20 minutes to be wrong.
- **Never present the two totals adjacently without the bridge.** They differ by both
  vendor coverage and (on a just-closed week) one day. See "Reporting the two bases
  together" below — readers check the arithmetic first and stop there.

The live per-tier walk (`report.py` with no flags) and the Step 3 ConvAI walk remain
available for the 7-bucket `aiPremium*` taxonomy specifically. They are NOT the default
route to "paid vs non-paid" any more.

Other forms:

```bash
# Explicit week (the Sunday that starts it).
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-aurie-weekly-costs/scripts/report.py \
  --week 2026-05-04

# Backfill / non-Sun–Sat range (also skips the 10-day retention check).
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-aurie-weekly-costs/scripts/report.py \
  --since 2026-04-27 --until 2026-05-04

# Skip the per-tier walk (faster — only emits the paste row).
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-aurie-weekly-costs/scripts/report.py \
  --no-tier-breakdown

# JSON sidecar.
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-aurie-weekly-costs/scripts/report.py \
  --json /tmp/aurie_week.json
```

What the script does:

1. Resolves the Sun–Sat window. Default uses Pacific time to pick the most recent completed Sunday; that calendar date is then anchored at UTC midnight for the query. `--week` must be a Sunday — the script errors out otherwise. `--since/--until` are passed through as-is (UTC).
2. Validates the window: not in the future, and (for default / `--week` runs) the start is within the 10-day retention envelope. `--since/--until` skips the retention check.
3. Calls `/v1/organization/costs?bucket_width=1d&group_by=project_id` over UTC-midnight boundaries and sums `amount.value` per project → **LLM USD per project** (and total).
4. Calls `/v1/usage/character-stats?metric=fiat_units_spent&aggregation_interval=day` over **Pacific-midnight** boundaries (so the window matches the ElevenLabs dashboard exactly as viewed by the team) and sums every per-bucket series → **total cents**, divided by 100 → **Voice USD**.
5. Unless `--no-tier-breakdown` is set: splits the window at the **ledger epoch (2026-07-23T03:25Z)**. The post-epoch slice reads the Intelligence Service LLM-call ledger (BigQuery `llm.llm_call`, paged newest-first by moving the `to` bound back; production projects mapped by feature key, e.g. `Memory [Production]` → `memory`); rows are normalized into the completion shape (`userId` → `metadata.user`, `aiPremiumState`/`premiumState` → tier signal, `inputTokens`/`outputTokens`/`cachedTokens` → usage). If the ledger read fails, a stderr warning fires and that slice falls back to the stored-chat-completions walk (chat-API-only — Responses-API traffic invisible). The pre-epoch slice always walks each project's stored chat completions and classifies by `metadata.aiPremium` / `aiPremiumState`. The two slices merge with a dedupe on the OpenAI object id (`openaiId` == stored completion `id`), then the project's dollars are allocated across tiers in proportion to token weight. See the sibling skill [`eng:analyze-openai-cost-per-subscription-state`](../analyze-openai-cost-per-subscription-state/references/allocation.md) for the allocation math and trade-offs. Surfaces a rollout banner when the window straddles or predates the 2026-05-12 tagging rollout, and an `Attribution source:` line naming which source(s) fed the tier table (ledger / stored completions / mixed).
6. Renders a markdown table + a TSV line for the spreadsheet, followed by the tier breakdown table.

### Which method, and why

**The goal: the right total spend, and how many DOLLARS of it are paid vs non-paid.**
Not percentages — dollars. Three routes exist; only one answers the second half.

| Method | Total spend | Paid vs non-paid $ | Cost |
|---|---|---|---|
| **Store** (`--from-bigquery`) | all vendors; LLM exact, voice ~1–2.5% under billed | **LLM and voice, every vendor, per user** | ~30s |
| **Live** (default flags) | **billed — the spreadsheet basis** | LLM only; voice not splittable | ~10–15 min (LiveKit log walk) |
| **Live + ConvAI walk** (Step 3) | same as live | voice, but **ElevenLabs ConvAI only** | ~15–20 min |

**Use the store for the split, the live path for the billed row.** Run both; they are
different questions.

The ConvAI walk is the one to avoid. It sees a shrinking slice of voice as the LiveKit
migration proceeds — 85% of voice spend in the week of 9/13/2026, 59% a week later — and
reports what it covered as though it covered everything. For 9/20 it returned a 76.4%
paid share against the store's true 90.5%, after 20 minutes. It degrades silently, so
the number looks healthier as it gets worse. Keep it only when the 7-bucket
`aiPremium*` taxonomy is specifically wanted.

Why not make the store the sole source: its voice figure is all-vendor, while the
spreadsheet's Voice column is ElevenLabs-billed. Pasting one into the other silently
redefines the column.

### Reporting the two bases together

Any paid/non-paid table carries **its own total and its own window**, and that total must
equal the parts shown. When the billed row appears in the same message, render the bridge
as a table rather than a caveat — e.g. for 9/20–9/26/2026:

| Step | Amount |
|---|---:|
| Live row, ElevenLabs only, billed | \$2,054.62 |
| + non-ElevenLabs vendors (Cartesia, Deepgram, LiveKit transport) | +\$161.72 |
| = store, all vendors | **\$2,216.34** |

A figure that cannot be split (the billed ElevenLabs row has no per-user attribution) is
labelled "not splittable" and left whole. Do not show parts that almost cover it.

### Step 2 — Show the report inline

**Deliver the report as a standalone message that ends the turn.** Never put the report text and an `AskUserQuestion` call in the same turn — text emitted immediately before a question prompt may not render for the user, and the tables get silently swallowed. Issue the Step 3 prompt at the start of the next action, after this message has landed. If results and a prompt ever do share a turn, restate the full results in the message that follows the prompt's resolution.

Show the script's stdout directly. Lead with a one-line headline that names both numbers and reminds the user where to paste:

> _"**Week of 5/4 (Sun) → 5/10 (Sat)** — LLM **$X**, Voice **$Y**. Paste the TSV row into the sheet starting at the `Week of` column. Tier breakdown below."_

If the tier table shows a meaningful concentration (e.g. >50% of LLM spend in a single tier, or a non-paying tier like Premium Free / Never AI Premium dominating), note it in one sentence — that's the kind of context the row alone doesn't give. Otherwise stay terse; this is a data-entry helper, not an analysis skill. If the user wants per-user signal or the content-premium cut, route them to `eng:analyze-openai-cost-per-user` or `eng:analyze-openai-cost-per-subscription-state`.

If the rollout banner is present (window touches or predates 2026-05-12), surface it inline so the user doesn't misread the `(untagged)` slice as a bug.

### Step 3 — Offer the voice tier breakdown (opt-in)

After Step 2's report has rendered, prompt the user — via `AskUserQuestion` — whether they also want the **voice cost** broken down by AI premium tier. **Always offer it, every run** (the prompt is the contract of this skill). The default behaviour when the user declines is to do nothing else; this is purely additive.

When you ask, **warn explicitly that it takes ~15–20 minutes** — the long step is walking ElevenLabs `/v1/convai/conversations` paginated, which can be 1000+ calls for a typical Aurie week. Sample phrasing:

> "Want the voice cost broken down by AI premium tier too? It joins ElevenLabs per-user voice $ with OpenAI's AI premium tier metadata (Firebase UID match). Heads-up: it takes ~15–20 minutes because it walks every conversation in the week. Default flow is done — this is optional."

If the user says yes, run:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-aurie-weekly-costs/scripts/voice_tier_breakdown.py \
  --since <SAME_SINCE_AS_STEP_1> --until <SAME_UNTIL_AS_STEP_1> \
  --voice-workspace-total <VOICE_USD_FROM_STEP_1>
```

Reuse the **same** `--since/--until` (or `--week`) the main report resolved in Step 1 so the breakdown lines up exactly with the row. Passing `--voice-workspace-total` is optional — it adds a coverage line showing how much of the spreadsheet row's voice total is non-ConvAI TTS and therefore outside this table. The reconciliation itself does not depend on it: the script fetches the billed Conversational AI figure directly.

What the script does:

1. Reuses `resolve_window` from `report.py` (same Sun—Sat semantics) and prints the window to stderr. Then reads `/v1/usage/character-stats` with `breakdown_type=product_type` over the **Pacific-anchored** window, keeping the `Conversational AI` series only — that gives both the billed dollars the table reconciles against and the dollars-per-credit rate every per-user figure is priced at. Runs BEFORE the slow walk so a bad key or empty window fails in seconds, not 20 minutes.
2. Invokes the sibling `eng:analyze-elevenlabs-cost-per-user/scripts/report.py` as a subprocess with `--since/--until --json <tmp>` and parses its `by_user` array → per-user **billed credits** (`total_credits`, aggregated from each conversation's `metadata.cost`), priced at the rate derived in step 1. The sibling's own duration-based dollar figures are ignored.
3. Invokes the sibling `eng:analyze-openai-cost-per-user/scripts/report.py` as a subprocess with `--since/--until --no-stats --no-headline --json <tmp>` and parses its `users_all` array → uid → `ai_tier` lookup.
4. Reads the OpenAI sibling's `attribution.coverage.coverage_pct` — the share of walkable spend that reached a real UID — and **exits 2 below 99%**. A project whose `OPENAI_<PROJECT>_API_KEY` is unset contributes no tier signal, so its users carry voice cost into the free bucket while the dollars still reconcile against billed Conversational AI: the table looks healthy and the split is wrong. Pass `--allow-degraded-attribution` (or set `AINATIVE_ALLOW_DEGRADED_ATTRIBUTION=1`) to accept it deliberately. A window with no walkable OpenAI spend is exempt — a coverage ratio is undefined there, not bad.
5. Joins on Firebase UID. Users present on the ElevenLabs side but absent from OpenAI bucket into `(no OpenAI activity in window)`. Aggregates voice $ per tier.
6. Renders a Markdown table identical in shape to the LLM tier breakdown, plus a reconciliation line against billed Conversational AI, a tier-lookup coverage line, and (when `--voice-workspace-total` is passed) a coverage line naming the non-ConvAI remainder.

Show the script's stdout directly (same standalone-message rule as Step 2 — the table must land in its own message, not share a turn with a prompt). If a single tier dominates voice spend (e.g. >50%) note it in one sentence — the same pattern as the LLM breakdown headline.

On an exit 2, report the missing keys rather than the table, and offer `--from-bigquery` as the route that answers the same question without them. The reconciliation line cannot catch this failure: it measures voice dollars against the ElevenLabs bill, and those are right regardless of which tier they were filed under.

### Step 4 — Offer to record the snapshot in AI Knowledge → Signals → Aurie Weekly Cost

After the main row (Step 2) and the optional voice tier breakdown (Step 3, if the user opted in) have rendered, finish the run by offering to **persist this week's cost row** under `Signals/Aurie Weekly Cost`. Strict Sun–Sat weeks recorded as immutable dated snapshots — what gets pasted into the spreadsheet on Monday also lands in the knowledge layer, so anyone (or any future skill) asking "what did Aurie cost the week of 5/24?" can answer it without re-running this skill.

Always offer it, every run. **Opt-in and requires explicit per-turn approval** — writing to AI Knowledge is a side-effect visible to others.

**Step 4a — Pre-check for an existing snapshot.** List child pages of `Signals/Aurie Weekly Cost`. If a snapshot for this `snapshot_date` (the week's starting Sunday) already exists, surface the existing URL and stop — the week is already recorded. Re-runs after a Costs API lag refresh should NOT create a second snapshot for the same week.

**Step 4b — Ask.** Phrase it tightly:

> *"Want me to record this week's cost row as a snapshot under `Signals/Aurie Weekly Cost`? It'll be findable later as the authoritative cost number for the week of `<week_label>` without having to re-run this skill."*

If the caller declines, stop — the spreadsheet TSV from Step 2 already covers the immediate need.

**Step 4c — Draft + write.** On approval, compose the payload per the [`Signals/Aurie Weekly Cost` schema](../../../shared/skills/record-knowledge/references/schemas/aurie-weekly-cost.md):

```yaml
path: Signals/Aurie Weekly Cost
action: create
fields:
  producer: "Aurie Weekly Cost"
  snapshot_date: <Sunday that starts the week, ISO date>
  week_label: <e.g. "5/24 – 5/30">
  since_iso: <window start, ISO datetime>
  until_iso: <window end, ISO datetime>
  headline: <one-line summary built from LLM + Voice totals + dominant tier(s)>
  llm_cost_usd: <number>
  voice_cost_usd: <number>
  total_cost_usd: <llm + voice>
  tier_breakdown_llm:
    - { tier: "AI Premium Paid", spend_usd: <n>, share_pct: <n>, active_users: <int>, per_user_usd: <n> }
    # …one row per tier present in Step 2's table…
  tier_breakdown_voice:                # only when Step 3 ran (or always in --from-bigquery mode)
    - { tier: "AI Premium Paid", spend_usd: <n>, share_pct: <n>, active_users: <int>, per_user_usd: <n> }
    # …one row per tier present in Step 3's table…
  tier_rollout_window_status: <"pre" | "mixed" | "post">   # match the banner in Step 2
  body_md: |
    <the full rendered report markdown from Step 2 — verbatim; if Step 3 ran, append its table below>
sources:
  - label: "Producer skill — eng:report-aurie-weekly-costs"
    url: "https://github.com/AuraHealth/AINative/tree/main/plugins/eng/skills/report-aurie-weekly-costs"
    primary: true
  - label: "OpenAI Costs API"
    url: "https://platform.openai.com/usage"
  - label: "ElevenLabs workspace usage"
    url: "https://elevenlabs.io/app/usage"
```

Invoke `shared:record-knowledge` with the payload. Surface the resulting Notion URL inline so the caller can verify the snapshot landed.

If `--json` was passed in Step 1, you already have the structured fields ready — the JSON sidecar exists precisely for this handoff. Pull `tier_breakdown_voice` from the Step 3 JSON sidecar if it ran; omit the field otherwise.

**Step 4d — `--from-bigquery` runs record the 5-bucket cut, and say so.** In BigQuery backfill mode the tier tables are the store's 5-bucket `subscription_state` (`paid` / `trial` / `free` / `grace` / `other`), not the live path's 7-bucket `aiPremium*` taxonomy — so the snapshot must not imply the two are comparable:

- Populate `tier_breakdown_llm` **and** `tier_breakdown_voice` from the `by_subscription_state` array in the `--json` sidecar, and render `body_md` from a `--detail` run so the vendor and tier tables are in it (BigQuery mode gives both for free — no ~15–20 min `/convai/conversations` walk, so Step 3 doesn't gate the voice table here).
- **Copy `tier` straight from each entry's `label` field** — `Paid (AI Premium)` / `Trial` / `Free` / `Grace / payment pending` / `Other (unattributed / reconciling)`. Do **not** substitute the raw `state` key (`paid` / `grace` / …) or invent your own wording: `label` is `report.py`'s `BQ_STATE_LABELS`, the same strings the rendered table and the schema use, and snapshots are write-once — a third label set persists permanently beside the weeks already recorded.
- Set `tier_rollout_window_status: "post"` — the AI-premium rollout banner doesn't apply to this cut.
- Lead `headline` with the backfill provenance, and note in the body that numbers came from `aura-main.aurie_cost.aurie_per_user_daily_cost` rather than the live APIs (voice runs ~1–2.5% under ElevenLabs' billed workspace total).
- Swap the two live-API `sources` entries for the BigQuery table + `eng:store-aurie-user-daily-cost`, keeping the producer-skill entry as `primary`.

**Weeks the store can't cover.** `--from-bigquery` has gapless per-user coverage only from **2026-06-01**. A window starting earlier comes back understated with a `note:` line, and the live path is past ElevenLabs retention for any such week — so there is no accurate source. Do **not** record a snapshot in that case: report the shortfall and leave the week absent rather than persisting a knowingly-low number as the authoritative figure.

## Output

Stdout:

```markdown
**Week of 5/4 (Sun) → 5/10 (Sat)** — Pacific calendar week, UTC-aligned buckets

| Week of | LLM costs | LLM cost per active user | Voice costs | Processing costs | Margin ($) | Margin (%) |
|---|---:|---|---:|---|---|---|
| 5/4 – 5/10 | **\$1,234.56** | — | **\$234.56** | — | — | — |

**TSV (paste into the sheet, starting at the `Week of` column):**

```
5/4 – 5/10	\$1,234.56		\$234.56			
```

**LLM breakdown by AI premium tier**

| Tier | Spend | Share | Active users | $/user |
|---|---:|---:|---:|---:|
| AI Premium Paid | \$144.70 | 11.7% | 118 | \$1.23 |
| AI Premium Trial | \$661.48 | 53.6% | 490 | \$1.35 |
| AI Premium Free | \$14.67 | 1.2% | 53 | \$0.28 |
| AI Premium Payment Pending | \$9.53 | 0.8% | 57 | \$0.17 |
| Non AI Premium | \$26.57 | 2.2% | 380 | \$0.07 |
| Never AI Premium | \$258.21 | 20.9% | 2,675 | \$0.10 |
| (untagged) | \$119.40 | 9.7% | — | — |
```

With `--json`, an additional file at the chosen path:

```json
{
  "week_of": "5/4 – 5/10",
  "start_iso": "2026-05-04T00:00:00+00:00",
  "end_iso": "2026-05-11T00:00:00+00:00",
  "llm_cost_usd": 1234.5678,
  "voice_cost_usd": 234.5601,
  "attribution_source": "LLM ledger (BigQuery llm.llm_call, per-call rows)",
  "by_ai_premium_tier": [
    { "tier": "AI Premium Paid", "amount": 144.70, "active_users": 118 },
    { "tier": "AI Premium Trial", "amount": 661.48, "active_users": 490 },
    { "tier": "AI Premium Free", "amount": 14.67, "active_users": 53 },
    { "tier": "AI Premium Payment Pending", "amount": 9.53, "active_users": 57 },
    { "tier": "Non AI Premium", "amount": 26.57, "active_users": 380 },
    { "tier": "Never AI Premium", "amount": 258.21, "active_users": 2675 },
    { "tier": "(untagged)", "amount": 119.40, "active_users": 0 }
  ],
  "rollout": {
    "tagging_started_iso": "2026-05-12T00:00:00+00:00",
    "window_status": "post",
    "warning": null
  }
}
```

With `--no-tier-breakdown`, the `by_ai_premium_tier` and `rollout` keys are omitted.

### Voice tier breakdown (Step 3, if the user opts in)

Stdout from `voice_tier_breakdown.py`:

```markdown
**Voice cost by AI premium tier — 5/17 – 5/23** (joined via Firebase UID)

| Tier | Voice spend | Share | Active voice users | $/user |
|---|---:|---:|---:|---:|
| AI Premium Paid | \$162.09 | 16.1% | 61 | \$2.66 |
| AI Premium Trial | \$733.09 | 72.9% | 89 | \$8.24 |
| AI Premium Free | \$11.04 | 1.1% | 14 | \$0.79 |
| AI Premium Payment Pending | \$8.04 | 0.8% | 5 | \$1.61 |
| Non AI Premium | \$47.27 | 4.7% | 26 | \$1.82 |
| Never AI Premium | \$43.68 | 4.3% | 58 | \$0.75 |
| (no OpenAI activity in window) | \$0.06 | 0.0% | 5 | \$0.01 |

_Total: \$1,005.27 across 258 voice users._

_Reconciliation: per-user sum \$1,005.27 vs. billed Conversational AI \$1,004.67 — \$0.60 / 0.06% gap. Expect well under 1%; the residual is conversations with no `userId` (agent-internal or test calls), which bill to the workspace but cannot be attributed to a user._

_Coverage: the spreadsheet row's voice total is \$1,139.37 (all ElevenLabs products). This table covers Conversational AI only; the remaining \$134.70 is non-ConvAI TTS — chiefly the LiveKit-path and Intelligence Service keys — which carries no per-user attribution and cannot be split by tier._
```

With `--json`, an additional file at the chosen path:

```json
{
  "window_label": "5/17 – 5/23",
  "start_iso": "2026-05-17T00:00:00+00:00",
  "end_iso": "2026-05-24T00:00:00+00:00",
  "voice_per_user_sum_usd": 1005.27,
  "voice_workspace_total_usd": 1004.67,
  "voice_users_total": 258,
  "voice_users_matched": 253,
  "voice_cost_matched_usd": 1005.21,
  "by_ai_premium_tier": [
    { "tier": "AI Premium Paid", "cost_usd": 162.09, "active_users": 61, "duration_secs": 108060.0, "share": 16.1 },
    { "tier": "AI Premium Trial", "cost_usd": 733.09, "active_users": 89, "duration_secs": 488727.0, "share": 72.9 },
    { "tier": "AI Premium Free", "cost_usd": 11.04, "active_users": 14, "duration_secs": 7360.0, "share": 1.1 },
    { "tier": "AI Premium Payment Pending", "cost_usd": 8.04, "active_users": 5, "duration_secs": 5360.0, "share": 0.8 },
    { "tier": "Non AI Premium", "cost_usd": 47.27, "active_users": 26, "duration_secs": 31513.0, "share": 4.7 },
    { "tier": "Never AI Premium", "cost_usd": 43.68, "active_users": 58, "duration_secs": 29120.0, "share": 4.3 },
    { "tier": "(no OpenAI activity in window)", "cost_usd": 0.06, "active_users": 5, "duration_secs": 40.0, "share": 0.0 }
  ]
}
```

## Edge cases

**Allocation is service-tier aware** ([ENG-1106](https://linear.app/aura-health/issue/ENG-1106)).
The OpenAI tier split scales each call's token weight by the ledger's `serviceTier`:
OpenAI prices Flex at exactly **50%** of Standard and Fast (renamed from `priority` on
2026-07-30) at **2×**, so `flex`/`batch` weigh ×0.5, `default`/`auto`/`scale` ×1.0, and
`fast`/`priority` ×2.0. Per-tier list prices live in `Tools/OpenAI` → "Per-tier list prices".
A tier-homogeneous project cancels the factor out; the skew appears where tiers mix inside
one project. A null `serviceTier` (rows before the
[ENGBACK-1315](https://linear.app/aura-health/issue/ENGBACK-1315) deploy) weighs 1.0, so a
week straddling the deploy degrades toward the old numbers rather than skewing.

- **The stored-completions fallback resolves nothing for most projects (measured 2026-08-04).** What matters is whether a project's completions are **stored and listable**, not which API it calls — `Feed` still spends daily but has had no stored completion since **2026-07-23**. On a post-2026-07-23 window the ledger is effectively the only attribution source: every production project except `Conversation` has nothing stored, and `Conversation` itself is only **partially** covered — the `aiResponseAPI` A/B (since 2026-07-31) serves its treatment arm on the Responses API, so one conversation can span both surfaces. If the ledger is unreachable (or `GCP_KEY` is unset) this run does not degrade gracefully: dollars still come from the Costs API, so the report looks plausible while attribution collapses into the untagged bucket. **Treat a post-2026-07-23 run whose ledger call failed as unusable, not degraded** — re-run it rather than reading the numbers. Canonical per-project detail: `Tools/OpenAI` → "Responses API migration — reading stored data"; don't restate it here. [ENG-1049](https://linear.app/aura-health/issue/ENG-1049)
- **A post-2026-07-23 ledger outage now fails closed (exit 2) — [ENG-1054](https://linear.app/aura-health/issue/ENG-1054).** The caveat above says such a run is unusable rather than degraded; this is the code that enforces it, rather than a stderr warning that scrolls past. Pass `--allow-degraded-attribution` (or set `AINATIVE_ALLOW_DEGRADED_ATTRIBUTION=1`) to accept the degraded attribution deliberately. **Unaffected:** pre-epoch slices (the ledger has no rows before 2026-07-23T03:25Z, so stored completions are the correct source), projects the ledger doesn't serve, and the roll-out-ramp union — none of those is a ledger *failure*.

- **Either env var missing → hard fail.** No MCP fallback for the OpenAI Costs API or the ElevenLabs workspace usage endpoint. Walk the user through `update-config` to add the missing key, then prompt to restart Claude Code.
- **Window older than 10 days → script errors out** (default / `--week` only). ElevenLabs keeps 10 days of usage buckets; older windows return partial data with no signal that they're incomplete. The script refuses rather than producing a quietly-wrong number. To backfill an older row knowingly, pass `--since/--until` — that branch skips the check and accepts whatever the APIs return.
- **`--week` not a Sunday → script errors out.** Forces explicit thinking about the boundary; ambiguity here would silently drift numbers off the spreadsheet's columns.
- **Window in the future → script errors out.** Wait for the week to complete.
- **OpenAI Costs API lag.** The most recent daily bucket can lag by up to ~24h. If the user is comparing to a value they just saw in the OpenAI dashboard, mention this; otherwise the number is what OpenAI will bill.
- **Re-runs are no-ops.** The skill reads APIs and prints — nothing is written. Running twice in a row is safe and produces the same numbers (modulo API lag refreshes).
- **Cowork.** `mcp__workspace__bash` strips host env (PROD-1305), so both keys are unset inside Cowork sandboxes. There is no degrade-gracefully path here — the script needs both keys to produce both numbers. In Cowork, the user must paste-export both keys at the start of the session (see `/CLAUDE.md` → "Known Cowork constraints").
- **Plugin scripts unreadable by spawned Python (sandboxed shells, seen on Windows).** On some machines the shell sandbox lets the harness's file tools read the installed plugin directory but blocks spawned processes from opening files there — `python3 ${CLAUDE_PLUGIN_ROOT}/…/report.py` fails with `[Errno 2] No such file or directory` even though `Test-Path`/Glob confirm the file exists. Workaround: copy the scripts to the session scratchpad and run from there. Both scripts resolve sibling skills relative to their own path, so **replicate the `skills/<name>/scripts/` tree structure — do not copy flat**. Required siblings: `store-aurie-user-daily-cost` (vendor helpers for `report.py`'s voice vendor split; missing → the split degrades to a `livekit_path_pending` note), and `analyze-elevenlabs-cost-per-user` + `analyze-openai-cost-per-user` (subprocess dependencies of `voice_tier_breakdown.py`; missing → it errors out).
- **Rollout window — 2026-05-12.** `aiPremium*` metadata tagging shipped on that date ([ENG-498](https://linear.app/aura-health/issue/ENG-498)). Windows that predate it return a "pre-rollout" banner (every dollar lands in `(untagged)` — tier table is meaningless). Windows that straddle it return a "mixed" banner; the `(untagged)` slice is inflated by the pre-rollout days but the post-rollout tier shares are still correct in proportion to each other. Post-rollout windows render cleanly with no banner.
- **Ledger-first attribution + failover (post-2026-07-23 slices).** The tier walk's primary source for any part of the window at/after the ledger epoch (2026-07-23T03:25Z, Intelligence Service 0.285.0) is the Intelligence Service LLM-call ledger; the stored-chat-completions walk is the automatic fallback (missing `GCP_KEY`, BigQuery error → stderr warning + fallback, never a crash) and the only source for pre-epoch slices. The fallback is chat-API-only — it cannot see Responses-API traffic, so Responses-API tokens degrade into `(untagged)` while it's active. The `Attribution source:` line under the tier table (and `attribution_source` in `--json`) says which source produced the numbers; a `mixed (...)` value means the window straddled the epoch. `--no-tier-breakdown` and the `--from-bigquery` path never touch the ledger.
- **Tier breakdown speed.** The per-tier walk iterates each project's stored chat completions (~30 days retention on OpenAI's side), which adds 5–30 seconds to the runtime depending on volume. The ledger has no such retention limit going forward — post-epoch slices stay attributable indefinitely; only the pre-epoch/fallback stored-completions path is bound by the ~30-day wall. Pass `--no-tier-breakdown` when you only need the paste row.
- **The `--from-bigquery` vendor table ties out to `SUM(voice_cost)`.** Every vendor column the query selects reaches the rendered table, Intelligence Service TTS included, and LiveKit transport is `voice_livekit` plus `voice_livekit_platform` (PROD-5998 — the first column alone is overage-only and reads `\$0` inside the plan allotment). A vendor table that sums short of the voice total means a column was selected but never carried into the vendor dict; reconcile the two before reading the split.
- **Voice is unsplit in the default flow by design.** ElevenLabs' workspace usage endpoint doesn't carry per-user / per-subscription metadata, so the row's voice total is intentionally not allocated across tiers. Step 3 offers an opt-in voice tier breakdown that joins via Firebase UID against OpenAI metadata — accept it knowingly, given the speed cost. Per-user voice cost (no tier dimension) is also in [`eng:analyze-elevenlabs-cost-per-user`](../analyze-elevenlabs-cost-per-user/SKILL.md).
- **Voice tier breakdown — tier is "as-of OpenAI completion metadata in the same week".** The breakdown sources each user's AI premium tier from `metadata.aiPremium` / `metadata.aiPremiumState` on their OpenAI chat completions in the window, then attributes their voice $ to that tier. Implications:
  - **Mid-week tier transitions** (e.g. a user upgrading from Trial → Paid mid-week) show under whichever tier OpenAI last logged for them. The same approximation already shapes the LLM breakdown, so the two tables stay internally consistent.
  - **Voice users with no OpenAI activity** in the window have no tier signal and bucket into `(no OpenAI activity in window)`. In healthy weeks this is <1% of voice spend; if it climbs higher, that's a meaningful signal (voice-only users, or an OpenAI tagging gap).
  - **Per-user voice $ is priced from billed credits, not minutes** ([PROD-6019](https://linear.app/aura-health/issue/PROD-6019)). ElevenLabs bills ConvAI per credit consumed; a flat \$0.09/min assumption overstated every tier by ~20% — the week of 2026-09-06 ran 455 credits/min against the ~545 that \$0.09 implies, an effective \$0.0751/min. The script now derives $/credit per run from `fiat_units_spent / credits` on the ConvAI series, so a plan or price change self-corrects with no code edit. Measured on that week: credits-based reconciled to **−0.74%**, duration-based to **+19.85%**.
  - **Reconcile against ConvAI, never the all-product total.** The walk only ever covers Conversational AI, so measuring it against the whole workspace silently credits it with TTS spend it never counted. That masked roughly half the rate error above, and the masking **grows with the LiveKit voice experiment** — its TTS bills to a separate key, so the reconciliation line looks healthier as the tier numbers get worse. The `Coverage:` line names that remainder explicitly.
  - **The spreadsheet row's Voice cell is unaffected** by any of this — it comes from `fiat_units_spent` and is ElevenLabs' actual billed amount. Only the tier split and the `$/user` figures were ever wrong.
  - **The breakdown depends on sibling skills.** `eng:analyze-elevenlabs-cost-per-user` and `eng:analyze-openai-cost-per-user` must both be installed in this plugin. The helper script invokes them as subprocesses with `--json`; it errors out clearly if either is missing.

## Related skills

- [`eng:analyze-openai-costs`](../analyze-openai-costs/SKILL.md) — full OpenAI cost report with project breakdown, drill-downs, and `--by-model` rate detection. Use when you want signal, not a row.
- [`eng:analyze-openai-cost-per-subscription-state`](../analyze-openai-cost-per-subscription-state/SKILL.md) — full per-state cut with both AI and content premium dimensions, top-spenders per tier, and coverage warnings. Use for the deeper drill-down beyond what this skill's table shows.
- [`eng:analyze-elevenlabs-costs`](../analyze-elevenlabs-costs/SKILL.md) — workspace ElevenLabs view with quota / overage / projection. Use when answering "are we tracking to exceed our plan?", not "what was last week's number?".
- [`eng:analyze-elevenlabs-cost-per-user`](../analyze-elevenlabs-cost-per-user/SKILL.md) — per-Firebase-UID ElevenLabs voice cost (Top-10 + distribution). The Step 3 voice tier breakdown calls this script as a subprocess.
- [`eng:analyze-openai-cost-per-user`](../analyze-openai-cost-per-user/SKILL.md) — per-Firebase-UID OpenAI spend with `ai_tier` per user. The Step 3 voice tier breakdown calls this script for the UID → tier lookup.
