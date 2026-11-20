---
name: store-aurie-user-daily-cost
description: >
  Stores ONE day of per-user Aurie cost (LLM plus multi-vendor voice) to BigQuery and a
  maintained CSV: one row per UID with LLM cost, nine per-vendor voice columns,
  stored LiveKit minutes, totals and subscription state — plus a reconciling `Other` row,
  so each day sums EXACTLY to the authoritative totals. ElevenLabs gets three columns,
  split by CALLING API KEY. LiveKit is booked from the INVOICE: a per-day subscription
  accrual plus measured overage, never a per-minute rate. Also maintains a unit-cost and an
  engineering-KPI store. BigQuery is the source of truth; the CSVs are rebuildable.
  Idempotent: refuses to re-record a stored date. Defaults to yesterday. Trigger on "store
  aurie user daily cost", "append yesterday to the per-user cost dataset", "update the
  per-user daily cost csv", "add a day to the cost dataset", or "record daily unit costs".
  Do NOT use for ad-hoc per-user ranking (the `analyze-*-cost-per-user` skills), workspace
  daily totals (`eng:report-aurie-daily-costs`), or one user's all-source cost.
metadata:
  version: "3.27.0"
knowledge:
  reads:
    - Guide/Index          # baseline routing aid (universal)
    - Tools/OpenAI         # Costs API + per-user allocation method, stored-completion retention
    - Tools/ElevenLabs         # ConvAI conversations endpoint, fiat_units_spent (cents) semantics
    - Tools/Cartesia       # TTS usage/credits API + Scale-tier estimate rate
    - Tools/Deepgram       # STT usage API + project scoping (prod 7e42b984), nova-3 streaming rate
    - Tools/LiveKit        # forwarded GCP logs (session minutes), Cloud Scale estimate rate
    - Tools/Aura API       # /subscriptions/list authoritative subscription state
    - Tools/Mixpanel       # per-day engaged/active cohorts (JQL) for the engagement columns
    - Compliance/Privacy   # store holds full Firebase UIDs; external surfacing must truncate
    - Tools/GCP                      # Cloud Logging access (GCP_KEY)
    - Tools/BigQuery                 # aura-main.aurie_cost store access (GCP_KEY)
---

# Store Aurie User Daily Cost

## Goal

Maintain a per-user-per-day cost dataset for Aurie — **LLM + voice, attributed to each Firebase UID**, with authoritative subscription state — by appending one day at a time. Each day reconciles to the cent against the authoritative OpenAI + ElevenLabs totals (via a single `Other` catch-all row), so the file is self-balancing and trustworthy for margin / cohort / heavy-user analysis.

This is the **store/ETL** member of the cost-skill family:
- [`eng:analyze-openai-cost-per-user`](../analyze-openai-cost-per-user/SKILL.md) — LLM per-user ranking for a window (this skill calls it for the LLM side).
- [`eng:analyze-elevenlabs-cost-per-user`](../analyze-elevenlabs-cost-per-user/SKILL.md) — voice per-user for a window.
- [`eng:report-aurie-daily-costs`](../report-aurie-daily-costs/SKILL.md) — workspace daily totals (no per-user).

## Row schemas

**Per-user store** (`per_user_daily_cost.csv` in the cache dir; legacy override `$AURIE_COST_CSV`):

`date, user_id, llm_cost, voice_11labs_agent, voice_11labs_intelligence, voice_11labs_livekit, voice_cartesia, voice_fish, voice_inworld, voice_deepgram, deepgram_inference_drawdown, voice_livekit, voice_livekit_platform, livekit_minutes, voice_cost, total_cost, subscription_state, engaged, active, activity_source`

- `voice_cost` = `voice_11labs_agent + voice_11labs_intelligence + voice_11labs_livekit + voice_cartesia + voice_fish + voice_inworld + voice_deepgram + voice_livekit + voice_livekit_platform` (the sum of the **nine** voice columns; kept so single-figure downstream consumers keep working). `livekit_minutes` and `deepgram_inference_drawdown` are **usage** columns, not cost ones — deliberately not in the sum. The drawdown is dollar-denominated but it is spend against *prepaid* capacity the subscription already books, so adding it would double-count.
- **Engagement vs cost-attribution — a row usually exists because we *spent* on the user, which is NOT the same as the user being active.** The three engagement columns separate the two so DAU isn't inflated by ambient/batch jobs:
  - `engaged` (`1`/`0`) — the user **sent a conversation message** that day (Mixpanel `AI Conversation Message Sent`, a mobile-only event). The strict "did something" cut. `engaged DAU = COUNT(engaged=1)`.
  - `active` (`1`/`0`) — the user fired **any AI event from a mobile client** that day (opened/used the AI app at all) — any `AI `- or `AIAura`-prefixed event (standalone Aurie and Aurie-in-Aura both count — PROD-6096) where `Source App ∈ {Aurie Mobile, Aura Companion App}` or `mp_lib ∈ {swift, android}` (a positive allowlist: Aurie Web and backend `node` events are excluded). A mobile-app DAU; superset of `engaged`, typically ~1.5–1.8× it (users who opened the app but didn't send a message). `active DAU = COUNT(active=1)`.
  - `activity_source` — why the row is billed today: `engaged` / `active` (they did something), else the dominant cost driver → `insights-batch` (weekly Insights compile+renderer batch), `ambient` (Notification / Feed / Onboarding daily jobs), `usage` (conversation-pipeline cost — memory/summary/profile — with no same-day event), or `reached` (unknown driver). The `Other` row is `reconcile`.
  - **The row count is *reached* users** (everyone billed, incl. ambient/batch) — an order of magnitude above engaged/active DAU. Use `engaged`/`active` for DAU; keep the raw row count only for cost, and never call it DAU. Cohorts come from Mixpanel JQL keyed by the Firebase `UserID` (see `engagement.py`); a user reached only by a batch job — or who merely qualified for the weekly Insights run by having chatted *last* week — is correctly `engaged=0, active=0`.
  - **A Mixpanel-active user with NO cost today still gets a row, all-zero except `engaged`/`active`/`activity_source`** (`mixpanel_active_zero_cost_users` in `store.py`) — so activity is observable independent of billing. A user with no cost that day, or billed by an async pipeline on an adjacent day, is otherwise indistinguishable from a user who was never reached at all: "no row" and "inactive" collapse to the same thing without this. `activity_source` still resolves to `engaged`/`active` for these rows through the existing logic, since a member of either set never has an empty `activity_source`. These rows never move a dollar total (`total_cost=0.0` by construction), but they widen the distinct-user count any `chart-aurie-*` reader derives from the store (e.g. the active-users line on `eng:chart-aurie-daily-cost`) relative to a cost-only count — treat that as this column's intended denominator, not drift. [PROD-6143](https://linear.app/aura-health/issue/PROD-6143).
- **ElevenLabs is called by THREE Aura systems**, so it occupies three columns: `voice_11labs_agent` (the ConvAI **agent** bundle, the legacy ElevenLabs voice path), `voice_11labs_intelligence` (**Intelligence Service** raw TTS — what mobile calls), and `voice_11labs_livekit` (ElevenLabs as a **TTS provider on the LiveKit path** — the LiveKit agent is multi-TTS: Cartesia / ElevenLabs / OpenAI / Inworld, chosen per session). The day's ElevenLabs workspace total is split **by calling API key**: `has_api_key=False` is the ConvAI slice (ConvAI runs inside the ElevenLabs platform, so it has no caller key), and `all_api_keys` splits the API-called remainder between the two raw-TTS callers. **Never use `product_type` to identify the caller** — both raw-TTS callers report `product_type=TTS`, so it cannot separate them. Canonical mapping: `Tools/ElevenLabs` → *Attributing spend to the calling service*; the key→caller tuples live in `scripts/store.py` and are pinned by [`tests/test_el_caller_mapping.py`](../../../../tests/test_el_caller_mapping.py). **A new ElevenLabs key must be added to those tuples in the same change that deploys it** — an unmapped key folds into Intelligence Service and still reconciles the day total, so the misattribution is silent.
- `subscription_state` ∈ `paid | trial | free | grace | other`.
- One **`Other`** row per day (`user_id = Other`, `subscription_state = other`) carries the **per-vendor** remainders (each vendor's authoritative day total − that vendor's attributed sum: ElevenLabs conversations with no `userId`, the ElevenLabs-TTS slice not tied to an ElevenLabs-TTS LiveKit session, any LiveKit-path minutes not attributable to a user, plus untagged LLM calls). It makes `GROUP BY date SUM(total_cost)` equal the real multi-vendor daily spend.

**Voice attribution (MULTI-VENDOR, PER-SESSION-TTS + PROPORTIONAL ALLOCATION):** A user's total voice = `11labs_agent$` (their ConvAI sessions) + `11labs_livekit$` + `Cartesia$` + `Deepgram$` + `LiveKit$` (their LiveKit-path share). The two TTS vendors (ElevenLabs-TTS, Cartesia) are allocated **only to the sessions that used that provider**; STT + transport allocate across all LiveKit minutes:

| Column | Path | Day total ($) | Per-user attribution |
|---|---|---|---|
| **voice_11labs_agent** | ConvAI (legacy) | ElevenLabs `product_type=ConvAI` bucket (ACTUAL billed) | per-conversation `userId` × ConvAI voice-minute share (unchanged walk) |
| **voice_11labs_livekit** | LiveKit (ElevenLabs TTS) | ElevenLabs `product_type=TTS` bucket (ACTUAL billed) | reply-character share **among ElevenLabs-TTS LiveKit sessions** (minute share without the ledger) |
| **voice_cartesia** (TTS) | LiveKit (Cartesia TTS) | daily credits × rate (credits ACTUAL, $ DERIVED) | reply-character share **among Cartesia-TTS LiveKit sessions** (minute share without the ledger) |
| **voice_fish** (TTS) | LiveKit (Fish Audio TTS) | the agent's own synthesised characters (`tts_characters` on `Session usage totals`, summed over Fish sessions) × rate (characters ACTUAL from our logs, $ DERIVED) — Fish has **no usage API**, only a credit-balance snapshot | reply-character share **among Fish-TTS LiveKit sessions** (minute share without the ledger) |
| **voice_inworld** (TTS) | LiveKit (Inworld TTS — aiVoiceProvider arm `a`) | daily characters from Inworld's usage API (`GET /usage/v1/records`, `services=tts`) × rate (characters ACTUAL, $ DERIVED); without `INWORLD_API_KEY` or on an API failure, the agent's own `tts_characters` over Inworld sessions, noted `inworld_derived` | reply-character share **among Inworld-TTS LiveKit sessions** (minute share without the ledger) |
| **voice_deepgram** (STT) | LiveKit (all sessions) | **transport-dependent** — direct: daily audio-hours × rate (hours ACTUAL, $ DERIVED); LiveKit Inference: drawdown BEYOND the plan's included credit block, so **\$0** while inside it | overall LiveKit voice-minute share |
| **voice_livekit** (overage) | LiveKit (all sessions) | agent-session minutes BEYOND the plan allotment × the overage rate; **\$0 inside it** | overall LiveKit voice-minute share |
| **voice_livekit** (pool overage) | LiveKit (all sessions) | Krisp + observability-audio overage ACCRUED from month-to-date session minutes past each pool's allotment; observability audio stops from 2026-11-05, since session recording was turned off on 11-04 (`SESSION_PROXY_OFF_FROM`, ENG-1861) (`livekit_plan.session_proxy_overage_usd`; Sep 2026 invoice billed them at 0.93× / 0.99× session minutes, so the estimate runs slightly high), replaced by the invoiced figure when `rebook_livekit.py` books a read statement | overall LiveKit voice-minute share, added into `voice_livekit` — the accrual is itself computed from session minutes. Rows stored 2026-10-15 → 10-25 carry it whole on `Other` (~\$60/day, 12% of voice), which understates paid voice |
| **voice_livekit_platform** (unused capacity) | LiveKit (all sessions) | the day's month-to-date delta of subscription paid for but not consumed ($ ACTUAL subscription from the invoice) — **signed**: negative on a day consuming above its accrual share, reversing what earlier days booked | none — `Other` row only |
| **deepgram_inference_drawdown** (usage, not cost) | LiveKit Inference | dollars drawn against the plan's included credit block that day | overall LiveKit voice-minute share |
| **livekit_minutes** (usage, not cost) | LiveKit (all sessions) | the day's session-minutes, stored rather than recovered | n/a — it IS the allocation basis |

Per-user LiveKit minutes come from the forwarded LiveKit GCP logs (`logName=projects/firebase-auratech16/logs/aurie-voice-agent`, `resource=global`) via the self-contained `livekit_minutes.py` (a partial derived copy of `analyze-livekit-usage/scripts/livekit_core.py` — see the AINative CLAUDE.md canonical note). It reads the **whole** UTC day: a day with all prod voice on the LiveKit path runs ~105–135k forwarded entries, and the read ceiling (`LIVEKIT_LOG_MAX_ENTRIES`, default 1,000,000) is a runaway guard — reaching it raises `TruncatedReadError` and `store.py` refuses the day, because a capped read keeps only the newest part of the day and understates minutes, users and LiveKit transport $ with nothing to flag it. If `# livekit: … entries=` sits on a round number, check it against that ceiling before trusting the day. It reads each session's TTS provider from its `TTS provider created: <name>` / `Initializing <Name> TTS` log line and buckets minutes per `(userId, TTS-provider)`; a session with no detectable provider line defaults to the agent's configured default (`cartesia`). The same minutes are split one level further by **voice** (`minutes_by_provider_voice` / `by_user_tts_voice` in `meta`), read from the structured `voice_id` field the agent stamps on `TTS provider created` (the *resolved* voice, re-emitted on `TTS hot-swapped`) — two arms of a voice experiment on one provider differ by voice id alone, so the provider bucket cannot tell them apart. A session with a provider but no voice id books under the explicit `(unknown)` key, never under a real voice; `sessions_without_voice_id` / `sessions_with_requested_voice_only` say how much of the day predates the resolved field. The per-voice split is a **read-out dimension**, not a column: the CSV/BigQuery schema is per vendor, and `store.py` prints the split (`# livekit voices[<provider>]`) only on a day where a provider ran more than one voice. For Deepgram + LiveKit: `user_vendor$ = vendor_day_total$ × user_min / total_min`; the TTS vendors split by reply characters (Step 3), on per-provider minutes only without the ledger.

**Estimated rates (ESTIMATES from pricing sheet — replace with contract rates via the flags):**

| Vendor | Flag | Default | Source |
|---|---|---|---|
| Cartesia | `--cartesia-rate-per-million` | `38.00` | Scale overage rate \$38/1M — plan of record is **Scale** (\$299/mo, 8M credits); canonical: `Tools/Cartesia` → *Plan & pricing* |
| Inworld | `--inworld-rate-per-million` | `25.00` | TTS-2 **On-Demand** list price, \$25 per 1M characters ([inworld.ai/pricing](https://inworld.ai/pricing), checked 2026-10-19). Inworld's own `characters_est_cost` uses this public rate; a subscription tier (Builder \$17.50 … Growth \$12.50) needs the flag |
| Fish Audio | `--fish-rate-per-million` | `15.00` | `s2-pro` list price, \$15 per 1M **UTF-8 bytes** ([fish.audio/developers](https://fish.audio/developers), checked 2026-09-15). Usage is counted in characters, so non-Latin text is under-counted |
| Deepgram | `--deepgram-rate-per-hour` | `0.348` | Nova-3 Multilingual streaming PAYG \$0.0058/min × 60 (current) |
| LiveKit | `--livekit-rate-per-min` | `0.01` | LiveKit Cloud Scale agent-session |

**Who bills the speech is decided by the transport, and reading the wrong source returns a well-formed \$0.** STT reaches either Deepgram directly or **LiveKit Inference**; Deepgram's own usage API reports nothing for the Inference path, and \$0 is also what a quiet day looks like, so the two are indistinguishable in the column. Three rules follow:

- **The transport comes from the agent's structured log fields**, not a regex over a message: `livekit_minutes.py` reads `stt_provider` / `stt_seconds` off the payload and `_stt_totals` splits the day's STT minutes by transport. A session that never logged a duration is **excluded from the minutes and counted** (`stt_sessions_measured`), never folded in as zero — a day where the field is merely absent must read as unmeasured. Its **transport is still recorded** (a key at 0.0 minutes, plus `stt_sessions_with_transport`): dropping it would make an unmeasured Inference day look like a day with no Inference at all.
- **On Inference the honest figure is \$0, and it is a decision rather than a rounding.** Those minutes draw down the plan's included credit block, and that block is *part of* the subscription `voice_livekit` + `voice_livekit_platform` already book in full — pricing them again double-counts. Only spend **beyond** the block reaches the column, marginal against the month to date. The note states which of the three states the day is in — unmeasured, inside the block, or past it — because one note that always reads "\$0, inside the block" makes an overflow look like another included-block zero.
- **How full the credit block is has to be stored, because nothing can derive it.** `voice_deepgram` is \$0 inside the block *by design*, so the dollar column erases the very quantity needed to reconstruct the drawdown — and the store is append-only per day, so it never reads prior days back on its own. `deepgram_inference_drawdown` records it; `mtd_inference_drawdown_before` sums the month to date **before** the day being written and hands it to `deepgram_inference_day`, which books only the increment past the block. **The drawdown is computed before any branch decides who bills the day** — a *mixed* day (some speech direct to Deepgram, some through Inference) draws against the block too, and Inference overage is **additive** to Deepgram's own invoice rather than an alternative to it. Marginal, so a month's daily values sum to the month's overage exactly. The read raises rather than defaulting to 0 — a 0 puts the whole month back inside the block, so a day that *is* past it books \$0.
- **A \$0 nothing explains is tagged, loudly — and the day is still written.** `unexplained_deepgram_zero` returns a `deepgram_transport_unknown` note when `voice_deepgram` is \$0, LiveKit minutes are > 0 and nothing else explains it; the note names the transports it saw, so a broken read stays distinguishable from a routing change, and it says **UNMEASURED STT**, never "\$0 of speech". It is printed at the point of detection as well as in the run's notes. It does **not** halt the run: the day carries LLM and ElevenLabs cost too, and stopping all of it over one vendor's unknown is the withholding-the-deliverable shape — an *explained* failure (`deepgram_pending`) was already allowed to store.
- **The Inference credit leg is STT-only, so TTS on Inference is tagged, loudly.** Every Aurie voice is a clone on a direct vendor plugin, which Inference can't serve, so all TTS is billed by the vendor and read from its own meter. If a voice ever moves onto `inference.TTS`, its spend draws on the same credit block, the vendor's usage API reads \$0, and `deepgram_inference_drawdown` (priced from STT minutes alone) misses it. `livekit_minutes.py` flags such a session from its log rows — an `-inference` provider token (structured `tts_provider` or the `TTS provider created:` message, the STT key's convention) or a `<vendor>/<model>` `tts_model` (Inference model ids; direct plugins log bare ones) — and still buckets its minutes under the vendor. Any flagged session makes `inference_tts_note` print a `WARNING:` and add a `tts_via_livekit_inference` note naming the sessions per provider. The day is still written: the logs can't split Inference credits by model, so pricing Inference TTS is the follow-up when it fires.

**Deepgram reads the production project** (the direct-transport path). `DEEPGRAM_OWNER_API_KEY` is an Owner key on `7e42b984-…` (**“Aura Health”**) — the project the LiveKit voice agent authenticates against — so `voice_deepgram` carries ACTUAL audio-hours (× the estimate rate). Deepgram is **LiveKit-path-only**, so a day where every project answered with no audio is legitimately \$0 and emits **no note**. A `deepgram_pending` note is reserved for a failure to *read*: missing key, `/projects` unauthorised, key in no project, no project usage read succeeded (`NOT_FOUND` / `INSUFFICIENT_PERMISSIONS` / 5xx / exception), or a **partial** read where one project failed and the total may undercount. A failed read is never reported as a bare \$0 — that would be indistinguishable from a quiet day and could be persisted to the append-only store.

**Backward compatibility (old → new header):** NEW files get the current 14-column header. When appending to a store carrying an older header, `store.py` **migrates it in place first**, preserving `voice_cost`/`total_cost` and idempotency:
- **ORIGINAL 6-col** (`date, user_id, llm_cost, voice_cost, total_cost, subscription_state`): pre-migration ElevenLabs was 100% ConvAI agent ⇒ `voice_11labs_agent = voice_cost`, every other `voice_*` = 0.
- **INTERMEDIATE 10-col** (single `voice_11labs`, before the agent/LiveKit-TTS split): ⇒ `voice_11labs_agent = voice_11labs`, `voice_11labs_livekit = 0`, LiveKit-path vendor columns carried over.
- **PRE-INWORLD 19-col** (no `voice_inworld`): ⇒ column added at `0.0`. **Not** exact: Inworld served LiveKit sessions before the column, and those dollars reached no column — not even `Other`. Re-store those days with `--force` (Inworld's usage API answers for past days) and roll BigQuery forward with `bq_sink.py --recreate`.
- **PRE-FISH 18-col** (no `voice_fish`): ⇒ column added at `0.0`. Exact, not reconstructed — no Fish traffic predates the column. Roll the BigQuery table forward with `bq_sink.py --recreate` (the partition load carries no schema-update option).
- **PRE-DRAWDOWN 17-col** (no `deepgram_inference_drawdown`): ⇒ column added at `0.0`. Zero is honest rather than reconstructed — the drawdown is not recoverable from any dollar those rows carry. Re-store the affected days to record it.
- **PRE-ENGAGEMENT 11-col** (multi-vendor cost, no engagement columns): ⇒ `engaged`/`active`/`activity_source` added **blank**. Fill historical rows with `backfill_engagement.py` (below) — the daily append can't reconstruct past engagement, only stamp the day it runs.

Consumers reading an un-migrated CSV apply the same rules.

**Backfilling engagement on an existing store:** `backfill_engagement.py --csv <store> [--walk-dir /tmp]` stamps `engaged`/`active`/`activity_source` for every date already in the store, pulling each day's cohorts from Mixpanel (with 429 retry). `engaged`/`active` are exact for all dates (Mixpanel retention is long); the `activity_source` reason tag for reached-but-inactive users needs the day's OpenAI walk JSON (`cpu_<date>.json` in `--walk-dir`) — dates whose walk JSON is gone (past OpenAI's ~30d stored-completions retention) tag those users the generic `reached`. Idempotent; atomic rewrite.

**Unit-cost store** (`daily_unit_costs.csv` in the cache dir; legacy override `$AURIE_UNIT_COST_CSV`), one row per day:

`date, openai_total, msg_driven_cost, memory_cost, ambient_cost, visible_messages, cost_per_msg, cost_per_msg_paying, cost_per_msg_free, voice_cost, voice_minutes, cost_per_voice_min, cost_per_voice_min_11labs, cost_per_voice_min_livekit, livekit_voice_users, elevenlabs_agent_voice_users`

- `voice_cost` / `voice_minutes` / `cost_per_voice_min` are **all-vendor, all-path** (ElevenLabs + LiveKit-path $ over ElevenLabs ConvAI + LiveKit session minutes) — the blended rate. ElevenLabs **TTS minutes are not counted**: they are Aurie's speech inside LiveKit sessions that `livekit_minutes` already counts (PROD-7584). TTS **dollars** stay in `voice_cost` and are LiveKit-path $ for the data-gap guard below. **Data-gap guard:** if the LiveKit-path $ (Cartesia/Deepgram/LiveKit) is > 0 but LiveKit minutes are 0 (GCP auth failure, missing logs, pre-traffic day), the LiveKit-path $ is **excluded** from this blended figure — dividing it by ElevenLabs-only minutes would silently inflate the rate. The excluded $ is flagged in the run log so the number is never a silent lie; numerator and denominator always cover the same paths.
- `cost_per_voice_min_11labs` = ElevenLabs ConvAI-agent $ / ConvAI minutes (legacy agent path; split on ElevenLabs' `product_type`, TTS excluded from both sides). `cost_per_voice_min_livekit` = LiveKit-path $ / LiveKit session-minutes, both read from the per-user store for the day (so the month-to-date LiveKit overage and LiveKit Inference STT are included; a day with no per-user rows falls back to the vendor APIs and warns that it undercounts). Tracking both proves the migration saving (LiveKit path should be cheaper once prod voice traffic moves onto it). Each is left EMPTY on a day with no minutes on that path.
- `livekit_voice_users` / `elevenlabs_agent_voice_users` = per-day **distinct users** on each voice path, derived from the per-user store: a user counts toward `livekit_voice_users` when any LiveKit-path column (`livekit_minutes` / `voice_livekit` / `voice_livekit_platform` / `voice_deepgram` / `voice_cartesia` / `voice_11labs_livekit`) is > 0, and toward `elevenlabs_agent_voice_users` when `voice_11labs_agent` > 0 (a user active on both paths in a day counts in both — these are per-path adoption counts, not a split). Tracks the ElevenLabs→LiveKit migration by **users**, not just $. Left BLANK on a day with no per-user store rows (blank ≠ a real 0). The `Other` reconciling row is excluded.
- **Schema migration:** appending to a `daily_unit_costs.csv` on an older header rewrites it in place to the current schema first, backfilling columns new to this schema blank for historical rows (never faked). Handles the 12-col (single blended `cost_per_voice_min`), 14-col (adds the per-path rate split), and current 16-col (adds the two voice-path user counts) headers. Idempotency + existing values preserved.

- `msg_driven_cost` = LLM $ in the **message-driven** projects (scales with engagement); `ambient_cost` = the **ambient** projects (per-user daily jobs); `memory_cost` broken out so Memory-pipeline cost changes are trendable. The two buckets partition the OpenAI `[Production]` projects by their cost *driver*:

| Bucket | Projects | Driver (verified) |
|---|---|---|
| **message-driven** → `msg_driven_cost`, feeds `$/paying msg` | Conversation, Memory, Summary, **Profile**, Journal | Fire as a function of conversation activity. Summary + Profile are statistical twins (~0.185–0.189 requests per visible message — both fire ~once per conversation), so they share this bucket. |
| **ambient** → `ambient_cost`, feeds `$/DAU/month` | Notification, Feed, Onboarding, **Insights** | Per-user daily / per-new-user jobs that run regardless of chat volume (~1.66–1.68 requests per DAU, cv 0.01 — they track active-user count, not messages). Insights added 2026-06-30 (per-user insight generation + renderers). |

  Rationale: per-message is the right denominator for engagement-driven cost; per-DAU is right for the fixed per-user daily load. Putting an ambient project into `$/message` would make a light user look hugely expensive; putting a per-conversation project (Profile) into `$/DAU` understates the per-message KPI. Profile was reclassified ambient → message-driven in [PROD-2568](https://linear.app/aura-health/issue/PROD-2568) (it was a per-conversation side-effect mis-bucketed as ambient — `$/paying-msg` and `$/DAU` history was re-based then).
- `visible_messages` = Conversation [Production] `num_model_requests` (≈ one per visible user turn; one visible turn triggers ~2.7 API completions across all projects).
- `cost_per_msg` = `msg_driven_cost / visible_messages` (blended, all users); `cost_per_msg_paying` = `cost_per_msg` × the day's measured non-free/blended per-completion cost ratio, computed from the Step 2 walk JSON + the Step 3 store's subscription states (paying users' messages carry more tokens — deeper context + permanent memory). `cost_per_msg_free` = same construction for the free cohort. Both left empty when per-user data for the date is unavailable, never estimated.

**Engineering-KPI store** (`daily_eng_kpis.csv` in the cache dir; legacy override `$AURIE_KPI_CSV`), one row per day — the engineering cost KRs and nothing else:

`date, llm_cost_per_paying_msg, voice_cost_per_min, ambient_per_dau_month, ambient_per_active_user_month, ambient_per_engaged_user_month`

- `llm_cost_per_paying_msg` and `voice_cost_per_min` mirror the unit-cost store.
- **Three ambient KPIs, same numerator (`ambient_cost` × 30), three denominators from the per-user store** (per the engaged/active columns):
  - `ambient_per_dau_month` = ÷ **reached** users (any billed row, ambient/batch included) — the **original KR, unchanged**.
  - `ambient_per_active_user_month` = ÷ **active** users (opened the app that day).
  - `ambient_per_engaged_user_month` = ÷ **engaged** users (sent a message).
  - reached is ~5× active, so the per-active/engaged figures run much higher — they measure ambient cost per *genuinely-active* user, not per batch-reached user. Both are **blank** on days whose per-user rows predate the engagement columns. Caveat: `ambient_cost` includes the weekly Insights batch, which scales with reached (not active), so per-active/engaged spikes on Insights-batch days.
- Derived, never refetched: the KPI store is **rewritten in full each run** (not appended) from the unit-cost + per-user stores — offline and idempotent, so it self-heals: an older 4-col file adopts the current schema and the active/engaged columns fill for every derivable date in one pass. Rebuildable anytime the other two stores exist.
- These are the two business-model planning numbers ($/message, $/voice-minute); trend them to catch drift.

## Step 0 — Prerequisites

```bash
test -n "$OPENAI_ADMIN_API_KEY"    && echo "openai: OK"    || echo "openai: MISSING"
test -n "$ELEVENLABS_ADMIN_API_KEY" && echo "elevenlabs: OK" || echo "elevenlabs: MISSING"
test -n "$AURA_API_TOKEN"          && echo "aura: OK"      || echo "aura: MISSING"
# Optional — LiveKit-path voice vendors (a missing key refuses a day that vendor carried traffic):
test -n "$CARTESIA_ADMIN_API_KEY"  && echo "cartesia: OK"  || echo "cartesia: missing (days with Cartesia traffic are refused)"
test -n "$INWORLD_API_KEY"         && echo "inworld: OK"   || echo "inworld: missing (voice_inworld derived from agent logs)"
test -n "$DEEPGRAM_OWNER_API_KEY"  && echo "deepgram: OK"  || echo "deepgram: missing (days with direct Deepgram traffic are refused)"
test -n "$GCP_KEY"                 && echo "gcp: OK"       || echo "gcp: check GOOGLE_APPLICATION_CREDENTIALS/ADC (livekit minutes)"
```

`OPENAI_ADMIN_API_KEY`, `ELEVENLABS_ADMIN_API_KEY`, `AURA_API_TOKEN` are required (no MCP fallback). The three LiveKit-path vendor keys are **optional**, but a Cartesia or direct-Deepgram read that fails (missing key, 401/403, 5xx, rate limit, partial read) on a day that vendor carried traffic **refuses the day** rather than storing its `\$0` — see *A failed vendor read refuses the day* in Step 3. Inworld falls back to the agent's own character counts; a LiveKit GCP auth failure keeps its store-and-flag path. `MIXPANEL_SERVICE_ACCOUNT_USERNAME` / `_SECRET` (+ optional `_PROJECT_ID`, default `963821`) are **optional for the daily append** — absent, the `engaged`/`active`/`activity_source` columns are written **blank** with a note (cost is unaffected) and can be filled later via `backfill_engagement.py`; they are **required** for the backfill itself. LiveKit minutes use the GCP auth ladder (`GCP_KEY` inline JSON → `GOOGLE_APPLICATION_CREDENTIALS` → ADC). `livekit_minutes.py` needs `google-cloud-logging` (`python3 -m pip install --user google-cloud-logging`). **BigQuery is the source of truth and is REQUIRED** (a documented exception to the repo's soft-token convention — BQ is the record, so the daily flow must not run BQ-less): every step hard-fails without `GCP_KEY` (the same SA, granted `bigquery.jobUser` + `bigquery.dataEditor` on `aura-main`) + `google-cloud-bigquery` (`python3 -m pip install --user google-cloud-bigquery`). Idempotency (Step 3/5) is decided against BQ, and Step 6 writes the authoritative record. See the sibling skills' Step 0 for key creation. `python3` + `requests`.

**Store locations — BigQuery is primary, the CSVs are an auto-managed local cache.** The authoritative record lives in `aura-main.aurie_cost` (`aurie_per_user_daily_cost` / `aurie_daily_unit_costs` / `aurie_daily_eng_kpis`); the three CSVs are a local copy the pipeline writes and Step 6 mirrors up. **No settings needed:** by default all three live in `~/.cache/aurie-cost/` (`per_user_daily_cost.csv` / `daily_unit_costs.csv` / `daily_eng_kpis.csv`) — the same self-managed-cache pattern as the AI Knowledge cache. Override the whole cache directory with `AURIE_COST_CACHE_DIR`, a single store with the legacy `AURIE_COST_CSV` / `AURIE_UNIT_COST_CSV` / `AURIE_KPI_CSV` env vars (still honored, no longer required), or per-run with `--csv` / `--unit-csv` / `--kpi-csv`. Resolution precedence per store: `--flag` > `$AURIE_*_CSV` > `$AURIE_COST_CACHE_DIR/<file>` > `~/.cache/aurie-cost/<file>`. **Seed / rebuild the cache from BigQuery any time** (fresh machine, corrupted/partial file, after a `--recreate`) with `bq_pull.py` (`--all` or `--from`/`--to`) — the CSVs are disposable; BigQuery is authoritative. Override the BQ target with `AURIE_BQ_PROJECT` / `AURIE_BQ_DATASET`.

## Step 1 — Resolve the date

Default is **yesterday (UTC)** — the most recent complete day (OpenAI's Costs/stored-completions lag ~24h). Accept an explicit `YYYY-MM-DD`. The skill is idempotent — re-running a stored date is a no-op.

## Step 2 — Produce the LLM per-user JSON (authoritative)

Run the OpenAI per-user walk for the date (this is the slow ~4-min step — it walks stored completions, the authoritative source):

```bash
DATE=2026-06-08; NEXT=2026-06-09   # NEXT = DATE + 1
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-openai-cost-per-user/scripts/report.py \
  --since "$DATE" --until "$NEXT" --no-stats --no-headline --top 1 \
  --json "/tmp/cpu_${DATE}.json"
```

(Equivalently, invoke `eng:analyze-openai-cost-per-user` via the Skill tool with the same window + `--json`.) The JSON's `total` is the authoritative OpenAI $ for the day; `users_all` is the per-UID allocation.

**Step 2 hard-fails on an LLM-ledger outage ([ENG-1054](https://linear.app/aura-health/issue/ENG-1054)).** `eng:analyze-openai-cost-per-user` exits 2 rather than falling back to stored chat completions, which resolve nothing for almost every project on a post-2026-07-23 window. That is deliberate for this skill above all others: a degraded run here would be **persisted** to `aurie_per_user_daily_cost` and every `chart-aurie-*` readout would inherit it. Don't paper over it with `--allow-degraded-attribution` — a day is better missing than wrong, and the store is idempotent per day, so re-running once the ledger is reachable costs nothing.

**Step 3 re-checks it, on the degraded *share* ([ENG-1114](https://linear.app/aura-health/issue/ENG-1114)).** `store.py` reads the walk JSON's `attribution.materiality` and refuses the day when more than **0.5%** of attributed spend (`MAX_DEGRADED_ATTRIBUTION_PCT`) was split by a stored-completions fallback. Two reasons the writer checks as well as the walk:

- **Defence in depth.** The walk's guard can be bypassed with a flag or an env var, and this skill is the thing that turns a walk into permanent warehouse history. The last writer before a persistent store should not inherit its correctness solely from an upstream exit code.
- **A share, not a substring.** Gating on the presence of a marker in `attribution.sources` is what caused the ENG-1114 mistake: `completions-non-ledger` fires for projects the ledger never serves, so on 2026-08-09 it was set by `Front` at **\$0.01** while \$350.11 of \$350.12 came through the ledger — and the day was withheld as though attribution were broken. `completions-non-ledger` is now reported as a note and never refuses; only `completions-fallback` (a ledger read that actually failed) can.

The threshold isn't zero because a ledger-served project can legitimately bill a rounding-level amount, and discarding a whole day over a cent is the failure this fixes. Override deliberately with `--allow-degraded-attribution`. A walk JSON predating ENG-1114 carries no `materiality` block; the check then falls back to refusing on the `completions-fallback` marker alone rather than silently accepting.

**Step 3 also checks that the attribution actually *happened* ([ENG-1120](https://linear.app/aura-health/issue/ENG-1120)).** The check above gates on attribution **source**; this one gates on **outcome**, and they fail independently. A walk can use exactly the right source for the window and resolve nothing: pre-epoch windows legitimately use stored completions (so there is no `completions-fallback` marker), and a walk that resolves *zero* users has no degraded *share* either. Two guards, both refusing by default, both overridden by `--allow-low-coverage`:

- **Coverage** — refuses when less than **75%** (`MIN_ATTRIBUTION_COVERAGE_PCT`) of walkable LLM spend reached a user, read from the walk JSON's `attribution.coverage`. Calibrated on the store itself: over the 70 days held at 2026-08-10, healthy days ran **91.44%–99.78%** and the collapsed days ran **0.00%**, with nothing in between.
- **User-count plausibility** — refuses when the day's attributed-user count falls below **40%** (`MIN_USER_COUNT_RATIO`) of the trailing 14-day median already in BigQuery. This is the case coverage *cannot* see: allocation is by token share, so when only **some** completions survive, the survivors absorb 100% of the money — coverage still reads ~99% while the cost is concentrated on a fraction of the users. Skips itself when fewer than 7 prior days exist, so a first backfill can still seed the store.
  A low count is **stored with a note** when at least **80%** (`MIN_ENGAGED_COST_SHARE`) of the day's Mixpanel-engaged users still carry LLM cost — a population shift, not a collapse ([ENG-1559](https://linear.app/aura-health/issue/ENG-1559)): retiring the ambient feed/notification jobs on 2026-10-07 cut users-with-cost ~2,300 → 934 while engaged coverage held at ~89%, and the count alone would have refused every day until the median caught up. A lost completion drops *engaged* users (2026-06-23 ran 72%), so that shape is still refused. An unmeasurable share (Mixpanel down, under 30 engaged users) never exonerates.

**Why this needs a guard at all: the failure is invisible once written.** The unattributed remainder lands on the `Other` reconciling row, so the day still balances to the authoritative bill, no total is wrong and nothing errors. Only the distribution is empty — and that is exactly what `chart-aurie-cost-concentration`, `-cost-mix`, `-cohort-cost`, `-tenure-cost`, `-cost-curve`, `-cumulative-cost`, `-lifetime-cost` and `-cohort-payback` read. Three days got through before the guard existed: **2026-06-23** (317 users instead of ~1,630 — each carrying ~5x their true cost), **2026-06-24** and **2026-06-25** (0 users; \$144.88 and \$151.00 wholly on `Other`). A walk JSON predating ENG-1120 carries no `coverage` block; the check then warns on stderr that coverage was **not verified**, rather than reporting a pass.

**Step 3 runs the same two checks on the VOICE side ([ENG-1134](https://linear.app/aura-health/issue/ENG-1134)).** The LLM guards above cannot answer for voice: they read the LLM walk, and the decisive one counts *users carrying LLM cost*, so voice — the larger half of the bill — needs its own pair. `summarize_voice_coverage` emits a per-path figure (attributed ÷ attributable, per vendor); `assert_voice_coverage` and `assert_voice_user_count_plausible` refuse below the same **75%** / **40%-of-trailing-median** thresholds, under the same `--allow-low-coverage` flag rather than a second idiom.

- **Why voice has more ways to fail quietly.** Four of the five paths — Deepgram, Cartesia, ElevenLabs-TTS, LiveKit transport — are allocated by `user_min / total_min` from the forwarded GCP logs. A GCP auth failure, a renamed `logName`, or a change to the `TTS provider created:` line empties the minute map while each vendor's billing API keeps returning a perfectly good daily total, so `allocate_by_minutes` divides by zero and returns `{}` for all four without erroring.
- **The test is money-with-no-owner, not "did the log query fail".** A vendor total of `\$0` is a genuinely quiet day and passes; a vendor total above `\$0` with no minutes to spread it over means traffic happened and we cannot say whose. Deepgram is LiveKit-path-only and legitimately bills \$0, so a "path returned nothing" rule alone would fire on ordinary days.
- **Per-path, not one blended ratio.** A large healthy path would otherwise mask a smaller dead one. A path that billed and reached no user while the blend passes is **stored and flagged**, not refused ([PROD-6992](https://linear.app/aura-health/issue/PROD-6992)). The run prints a `WARNING:` naming each dead path and its dollars, and the dollars sit on the `Other` row. `eng:chart-aurie-daily-margin` finds those vendor-days again from the stored data, and the cost report lists them. Refusing would withhold the day's sound vendors over one dead one. The case that set this was 2026-09-24: the ElevenLabs agent billed \$33.10 to nobody while the LiveKit-path vendors carried the blend.
- **`voice_11labs_intelligence` is excluded from the denominator.** Intelligence Service TTS has no per-user signal on the ElevenLabs side at all, so its slice is unattributable *by construction* and lands on `Other` every day. Counting it as uncovered would make every ordinary day look partly collapsed.
- **Calibration.** Thresholds match the LLM guard's rather than being tuned separately: the failure they catch is a hard 0%, not a gentle slide, so any bar between ~1% and ~90% separates the two cases equally well and one number is easier to reason about than two. Across the 70 days in the store (2026-06-01 → 2026-08-09) voice coverage does not fall below **98.27%** (floor: 2026-08-01), so the guard passes the entire known-good history — the test any threshold has to meet before it is trustworthy. No voice collapse has been observed: this pair is preventive, and the reasoning for it is the same one the LLM pair was written from.

## Step 3 — Store the day

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/store-aurie-user-daily-cost/scripts/store.py \
  --date "$DATE" --llm-json "/tmp/cpu_${DATE}.json"
```

The per-user store defaults to `~/.cache/aurie-cost/per_user_daily_cost.csv` (override with `--csv`, `$AURIE_COST_CSV`, or `$AURIE_COST_CACHE_DIR`).

`store.py` fetches the voice side (splits the ElevenLabs day total by `product_type` into the ConvAI-agent and LiveKit-TTS slices; walks ConvAI conversations → per-UID minutes and allocates the agent slice by minute share; buckets LiveKit minutes per `(user, TTS-provider)` to allocate the ElevenLabs-TTS and Cartesia slices to their own sessions, and Deepgram/LiveKit across all sessions), the subscription state (Aura `/subscriptions/list`), joins with the LLM JSON, appends per-user rows + the `Other` row, and prints a reconciliation line (`reconciles: True`) plus a `voice_cost == Σ(9 voice_* cols) per row` check. It skips a date already in the store.

**A failed vendor read refuses the day.** Each LiveKit-path leg returns `\$0` with a `<vendor>_pending` note when its usage read fails, and the notes are printed, not stored. So `assert_vendor_reads` exits `REFUSING to store <day>` when the Cartesia or direct-Deepgram read failed (missing key, 401/403, 5xx, rate limit past retries, a partial Deepgram read) on a day that vendor carried traffic. A failed read during a LiveKit log outage counts too, since the traffic is then unknown. A failed read on a day with no traffic for that vendor stays a note. Fix the key or wait for the vendor, then re-run; `--allow-unmeasured-vendor` stores the day deliberately with the column unmeasured. `unit_costs.py` applies the same guard, with the same flag, when it recomputes a day from the vendor APIs. On 2026-11-03 a 401 on the Cartesia key booked Cartesia at \$0 for a day it billed 6.58M credits ([ENG-1800](https://linear.app/aura-health/issue/ENG-1800)).

**TTS is split by reply characters, and a short LiveKit log read is refused ([ENG-1859](https://linear.app/aura-health/issue/ENG-1859)).** Every TTS vendor bills characters, and characters per minute rose from ~713 to ~1,050 over October, so a minute split moved 10–13% of daily TTS \$ between users. `store.py` reads each LiveKit coach user's reply characters from the llmCall ledger (BigQuery `aura-main.llm.llm_call`, last call per dialogue) and splits each provider's day total by them; a user on several providers splits their characters by their minute share on each, and a user with minutes but no ledger characters keeps their minute share (count and \$ noted). The vendor day totals are unchanged. The run prints `tts_alloc_basis: chars|minutes`; a failed ledger read falls back to minutes with a `ledger_pending` note. The same read checks the LiveKit logs: fewer users with minutes than **0.85×** the ledger's LiveKit coach users refuses the day (`--allow-short-livekit-log` stores it). On 2026-10-04 the logs found 147 users against the ledger's 228; a re-read found 263.

## Step 4 — Confirm reconciliation

The script prints `LLM $X + voice $Y = $Z | CSV day total $Z (reconciles: True)`. If `reconciles` is False, do not trust the day — investigate (usually a partial OpenAI day, or an ElevenLabs fetch gap).

## Step 5 — Record the day's unit costs

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/store-aurie-user-daily-cost/scripts/unit_costs.py \
  --date "$DATE" --llm-json "/tmp/cpu_{date}.json"
```

The unit-cost store, the per-user store it reads for cohort states, and the KPI store all default to `~/.cache/aurie-cost/` (override with `--csv` / `--user-csv` / `--kpi-csv`, the legacy env vars, or `$AURIE_COST_CACHE_DIR`). The KPI store is derived and rewritten fresh on every run.

This step is part of **every** daily run — not optional. Run it **after Steps 2–4**: the cohort rates (`cost_per_msg_paying` and `cost_per_msg_free`) are computed from the Step 2 walk JSON (per-user $ + completion counts) joined with the Step 3 store's subscription states — each day's actual cohort/blended cost ratios, never a fixed factor. In the normal daily flow those inputs always exist, so both cohort columns are recorded on every run; only an out-of-band backfill (e.g. past the 30-day stored-completions window, where Steps 2–3 can't run) leaves them empty.

`unit_costs.py` pulls the day's OpenAI cost + request counts grouped by project (Costs + Usage APIs) and ElevenLabs `fiat_units_spent` + `minutes_used`, computes the unit-cost row, and appends it. It also rewrites the day's engineering-KPI row every run (it's fully derived from the unit + per-user stores); that rewrite reads the full local copies, so it refuses to run when the local cache is behind BigQuery and points you to `bq_pull.py`. Idempotent per date. For catch-up, pass an inclusive range: `--date 2026-06-01 --until 2026-06-09` (only missing dates are fetched). The aggregate metrics need no walk (~5s/day); the two cohort columns are the only fields that depend on Steps 2–3.

**Correcting a per-user day that was already stored — `store.py --force`.** `store.py` refuses a date already in BigQuery or already in the local CSV. `--force` recomputes and **replaces** that date's rows:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/store-aurie-user-daily-cost/scripts/store.py \
  --date "$DATE" --force --llm-json "/tmp/cpu_${DATE}.json"
python3 ${CLAUDE_PLUGIN_ROOT}/skills/store-aurie-user-daily-cost/scripts/bq_sink.py --date "$DATE"
```

- Bypasses **both** guards for the forced date only; both stay enforced for every other date.
- Rewrites the local CSV (temp file + atomic move) rather than appending a second copy of the day's ~1,700–2,600 rows. Rows for other dates are preserved byte-for-byte, including the file's existing line terminator.
- **Requires an explicit `--date`** — it never rides the implicit yesterday-UTC default.
- Does **not** touch BigQuery; the partition holds the pre-force version until `bq_sink.py --date <d>` replaces it. The run prints that reminder on stderr.
- Reconciliation and the per-row voice-sum check run exactly as on a normal store, so a forced day is verified the same way.
- **Keeps a per-user split whose source has expired** (`EXPIRING_SOURCES`; today `voice_11labs_agent`: ElevenLabs ConvAI keeps conversations 10 days). Past that, the walk finds nothing, so the stored split is read back from BigQuery and kept when its day total matches the fresh vendor total within a cent, else scaled to it; never moved to `Other` for that reason alone. With no stored split the day lands on `Other` and the run says so. Re-storing 2026-10-04 moved \$10.97 to `Other` before this ([ENG-1859](https://linear.app/aura-health/issue/ENG-1859)).

Use it to correct a day stored from bad inputs; never as part of the normal daily flow.

**Re-storing a run of days: oldest first, and `bq_sink.py --date` after each one before the next.** The LiveKit booking reads month-to-date minutes and the Deepgram-inference drawdown for the days *before* the one being stored from BigQuery, not from the local CSV, so a later day stored before its predecessor is synced books its unused-capacity and credit-block figures against the stale predecessor. Re-storing an early-month day changes every later day of that month the same way — re-store through the month's last stored day.

**Correcting the unit-cost row for a stored day — `unit_costs.py --force`.** The cohort columns (`cost_per_msg_paying`, `cost_per_msg_free`) are computed **only when a row is written**, so a day recorded from bad inputs can't be repaired by re-running: the date is already present and is skipped. `--force` recomputes and **replaces** the row(s) for an explicit `--date` (optionally through `--until`):

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/store-aurie-user-daily-cost/scripts/unit_costs.py \
  --date "$DATE" --force --llm-json "/tmp/cpu_${DATE}.json"
python3 ${CLAUDE_PLUGIN_ROOT}/skills/store-aurie-user-daily-cost/scripts/bq_sink.py --date "$DATE"
```

- Rewrites the local CSV in place (temp file + atomic move) rather than appending a duplicate.
- A forced day whose walk JSON is gone **keeps its stored cohort columns** instead of blanking them, so voice-only corrections can be re-stored long after the walk.
- **Exempts only the forced dates** from the CSV-behind-BigQuery guard — that guard exists so the derived KPI rewrite can't silently drop BQ-only days, and it still applies to every other date. A date you are explicitly rewriting isn't the stale-copy case it protects against.
- **Requires an explicit `--date`** — it never rides the implicit yesterday-UTC default.
- Does **not** touch BigQuery. Always follow it with `bq_sink.py --date <d>`, whose normal idempotent per-partition `WRITE_TRUNCATE` replaces the stored row. The run prints that reminder on stderr.

Use it to correct a day recorded from bad inputs; never as part of the normal daily flow.

**Header guard on the per-user store.** `store.py` refuses to append when the store's existing header doesn't match the columns the running build writes, and names both column lists in the error. `csv.DictWriter` emits *this build's* `COLS` regardless of what the file says, so an older build appending to a store a newer build already migrated would otherwise write rows with the wrong field count — which aborts the per-user BigQuery sync and corrupts every figure derived from those rows. On a mismatch, re-run with the build matching the store, or rebuild the local copy with `bq_pull.py`.

## Step 6 — Write the day to BigQuery (the source of truth)

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/store-aurie-user-daily-cost/scripts/bq_sink.py --date "$DATE"
```

Writes all three stores for `$DATE` into the authoritative warehouse tables
(`aura-main.aurie_cost.aurie_per_user_daily_cost` / `aurie_daily_unit_costs` /
`aurie_daily_eng_kpis`). BigQuery is the source of truth; the local CSVs are the
rebuildable copy the pipeline just wrote. Run it last — after Steps 3 + 5 have
produced the day's rows locally.

- **Idempotent partition replace.** Each table is partitioned by `date`; the
  sync loads only `$DATE`'s rows into that day's partition with
  `WRITE_TRUNCATE`, so re-running a date (or re-storing a corrected boundary
  day per the Notes below) cleanly overwrites just that partition — never
  appends duplicates. Backfill everything with `--backfill`; migrate the schema
  (drop + recreate every table, then backfill from the CSVs) with `--recreate`
  — **back the tables up first**, see *Back up the three tables before `--recreate`*
  below, and note that BigQuery snapshots are denied to this service account.
  Paths default to the `~/.cache/aurie-cost/` cache (override with `--csv` /
  `--unit-csv` / `--kpi-csv`, the legacy `$AURIE_*_CSV` vars, or
  `$AURIE_COST_CACHE_DIR`).
- **A partition's `last_modified_time` is when it was UPLOADED, not when its
  numbers were computed.** `bq_sink.py` mirrors rows out of the CSVs, so the
  timestamp records this step — not the Step 2/3 walk that produced the day.
  Under `--backfill` the whole store is uploaded in one pass, which stamps every
  partition within minutes of each other regardless of when each day was actually
  walked (~57 days landed inside 7 minutes on 2026-07-28). So
  `INFORMATION_SCHEMA.PARTITIONS` cannot date an attribution problem, and any
  read-age or staleness argument built on it is measuring the upload — the derived
  lags look plausible either way, so nothing signals that the query answered a
  different question than the one asked. Nothing in-band carries a computed-at
  time; `INFORMATION_SCHEMA.JOBS_BY_PROJECT` can separate loads from queries, but
  the `GCP_KEY` SA is not granted `roles/bigquery.resourceViewer` and gets a 403 on
  it. Context: [ENG-1130](https://linear.app/aura-health/issue/ENG-1130).
- **Auth reuses `GCP_KEY`** (the same `claude@firebase-auratech16` service
  account as the LiveKit-minutes path), granted `bigquery.jobUser` +
  `bigquery.dataEditor` on `aura-main`. Needs `google-cloud-bigquery`
  (`python3 -m pip install --user google-cloud-bigquery`).
- **Hard-fails without BigQuery.** A missing `GCP_KEY` or absent
  `google-cloud-bigquery` exits **non-zero** (BigQuery is the record — the run
  must not proceed BQ-less). Per store it is best-effort within a run: if one
  table's load raises (schema drift, load error), the other tables still sync
  and the command exits non-zero listing the failed store(s) — BigQuery has no
  cross-table transaction, so that leaves a mixed state for the date until you
  **re-run `bq_sink.py --date "$DATE"`** (idempotent per-partition replace, so a
  re-run fully reconciles). A run that writes nothing (every store skipped) also
  exits non-zero. The KPI store defaults to the cache dir like the other two
  (it's rewritten fresh by Step 5 each run, so the cache copy isn't stale); a
  date with no KPI rows yet is skipped, not pushed empty.
- **Rebuild the local copy from BigQuery** with `bq_pull.py` (`--all` or
  `--from`/`--to`) — the CSVs are disposable; BigQuery is authoritative.
- **Privacy.** The per-user table holds full Firebase UIDs, so the `aurie_cost`
  dataset is access-restricted (cost/eng + the SA). Any broad team surface must
  go through a UID-truncated authorized view, never the raw table (per
  `Compliance/Privacy`).
- **Schema drift.** `bq_sink.py`'s `STORES` schema is the contract. If a store
  gains a column, add it there in the same PR (a CSV column absent from the
  schema is dropped with a warning; a schema column absent from the CSV aborts
  that store's sync).
- **A preflight gates every sync** (`bq_preflight.py`, run automatically by
  `bq_sink.py` on any non-`--recreate` invocation; runnable on its own to get the
  lines without writing anything):

  ```bash
  python3 ${CLAUDE_PLUGIN_ROOT}/skills/store-aurie-user-daily-cost/scripts/bq_preflight.py
  ```

  It asks BigQuery's own `INFORMATION_SCHEMA` two questions, and **errs toward
  refusing**: a run stopped early is cheap to re-run, a day that stores locally and
  cannot sync is invisible until someone reads a chart and believes it.
  - **A column the store declares and the warehouse lacks refuses the run** (exit
    **1**), naming the column and the `--recreate` remedy. There is no benign
    cause: the partition load carries no schema-update option, so *every* future
    day would 400 with `Cannot add fields`. `--recreate` itself is exempt — it is
    the fix.
  - **A max-date gap is reported, never refused** (exit **0**): each store's local
    max date next to its warehouse max date, plus a **MIXED STATE** line when the
    three warehouse tables disagree with each other. Divergence has benign causes
    (a store legitimately skips a date it has no rows for) that drift does not.
  - **A probe that could not run exits 2** and does not block the sync — an
    unresolvable probe must not read as a clean one, but it must not make the
    store unusable either.

  It exists because `voice_fish` was added to the store and never to BigQuery:
  `bq_sink.py` printed the 400 and exited non-zero *every day for three days*, the
  days kept accumulating locally because nothing upstream treated a failed sync as
  a reason to stop, and every `chart-aurie-*` read of `aurie_per_user_daily_cost_prod`
  stayed clean, plausible and **understated** throughout. A missing day has no
  shape ([PROD-6241](https://linear.app/aura-health/issue/PROD-6241) /
  [PROD-6242](https://linear.app/aura-health/issue/PROD-6242); same failure class as
  [ENG-1120](https://linear.app/aura-health/issue/ENG-1120)).

### Back up the three tables before `--recreate`

`--recreate` **drops all three tables** and rebuilds them from the local CSVs. It is
the most destructive operation in this skill, and `bq_preflight.py` prints it as the
remedy in every schema-drift refusal — so the person running it is typically acting
on an error message, mid-incident.

**Copy each table** before `--recreate`. It copies the rows, so it isn't instant, but at
`aurie_cost`'s size that is a fine backup, and it needs only the service account's WRITER on the
dataset:

```sql
CREATE OR REPLACE TABLE `aura-main.aurie_cost.<table>_bak_<ticket>`
OPTIONS(expiration_timestamp = TIMESTAMP_ADD(CURRENT_TIMESTAMP(), INTERVAL 7 DAY))
AS SELECT * FROM `aura-main.aurie_cost.<table>`;
```

Do all three (`aurie_per_user_daily_cost`, `aurie_daily_unit_costs`,
`aurie_daily_eng_kpis`). Restore with `CREATE OR REPLACE TABLE <table> AS SELECT * FROM
<table>_bak_<ticket>`, then drop the copies once the rebuilt tables are verified day for day.

**Not a snapshot.** `CREATE SNAPSHOT TABLE` fails with a 403 on `bigquery.tables.deleteSnapshot`:
the service account holds dataset WRITER, which grants `createSnapshot` but not `deleteSnapshot`,
and a bare `CREATE SNAPSHOT TABLE` needs both. That is the intended least-privilege setup
(ENGBACK-2333), not a syntax problem; use the copy.

The `_bak_<ticket>` suffix follows the existing `aurie_per_user_daily_cost_bak_eng982`
precedent.

**The backup is the rollback; it does not replace the superset check.** The gate on
`--recreate` is still that the local CSV is a strict superset of BigQuery — same day
span, no day present in BigQuery and absent from the CSV, and every shared day
agreeing on row count and `total_cost`. Re-verify that **in the same session**, since
`--recreate` inverts the usual source of truth for one run. The check is what stops
you rebuilding from an inadequate source; the backup is what saves you when the check
was run wrong. Order matters: back up, verify, then recreate.
([PROD-6246](https://linear.app/aura-health/issue/PROD-6246)).

## LiveKit is booked from the invoice, not a per-minute rate

LiveKit bills a **flat monthly subscription** (\$500/mo on Scale) that buys allotments across
several pools, plus measured overage beyond each. The per-minute figure LiveKit publishes is an
**overage** rate — it applies to nothing while usage sits inside the allotment. So the store
books two separate things:

- **`voice_livekit`** — the user's **billed** minutes × the plan's **credit rate**
  (`subscription ÷ included allotment`; Scale = **\$0.01/min**). The subscription buys a block
  of prepaid minutes, so a used minute costs what the block paid for it. Stable by
  construction: the rate does not move with how busy the month was, which is what a
  per-minute cost question actually wants.
- **Billed minutes** = logged minutes × `LIVEKIT_LOG_TO_BILLED` (in `store.py`, with the
  invoices it was fitted on): the forwarded GCP logs overcount the minutes LiveKit bills. They
  price every LiveKit dollar figure (consumed, unused capacity, pool overage);
  `livekit_minutes` holds the logged minutes. The run prints the factor and the day's LiveKit
  and total cost on logged and billed minutes.
- **`voice_livekit_platform`** — capacity that was **paid for and not used**, booked on the
  reconciling `Other` row only. It belongs to nobody, and splitting it by minute share would
  bill the users who showed up for the ones who did not — and this is the per-user figure
  that feeds margin, payback and lifetime-cost.

`consumed + unused = the subscription`, so a full month still reconciles to the invoice.
Unused is tracked as a **month-to-date delta**, not per day independently: a month that later
crosses into overage drives unused-to-date to zero, so that day carries a negative which
reverses what the earlier light days booked. A per-day `max(0, accrual − consumed)` leaves
those days permanently over-booked and the month quietly stops reconciling.

Past the allotment the two regimes meet without a seam, because each plan is priced so the
credit rate **equals** its published overage rate — `minutes × credit_rate` is then the
invoice exactly.

Plan facts (which plan, each pool's allotment, the overage rates) come from the bundled
[`scripts/livekit_plan.py`](scripts/livekit_plan.py); override with `--livekit-plan` and
`--livekit-rate-per-min`.

**Restating days stored under an older model.** Run
[`scripts/rebook_livekit.py`](scripts/rebook_livekit.py) — dry run by default; it recovers each
day's minutes, reports the per-month before/after, and reconciles against the invoice. It
refuses a day that already carries a platform figure unless `--force`.

**After any BigQuery-only rewrite of the per-user store** (`rebook_livekit.py --confirm` is one), run in this order ([ENG-1864](https://linear.app/aura-health/issue/ENG-1864)): rebook dry run (delta 0.00) → `--confirm` → `bq_pull.py --from <d1> --to <d2>` → `unit_costs.py --force --date <d1> --until <d2>` → `bq_sink.py --date <d>` for each day → `bq_pull.py` again to confirm. `unit_costs.py` reads the **local** per-user CSV, so it refuses a day whose local rows differ from BigQuery (row count or Σ`voice_cost`). Fix it from whichever side is newer: `bq_pull.py` after a BigQuery-only rewrite, `bq_sink.py --date <d>` after `store.py --force` (a pull would overwrite that correction).

**Booking a closed statement for a month `store.py` wrote itself** ([ENG-1860](https://linear.app/aura-health/issue/ENG-1860)). Those days carry `livekit_minutes` and the pool accrual, so the same script books the **whole month** to its closed statement instead: agent-minute dollars by measured minutes (the statement's total, our split), unused capacity and the invoiced pools (`INVOICED_EXTRA_OVERAGE`, replacing the accrual) spread per day on `Other`, and `voice_deepgram` scaled to the Inference line by one factor. Needs a closed `DASHBOARD_READINGS` entry; a partial month or one without a statement is refused. Never re-store such a month with `store.py` instead.

**Invoiced overage on a pool the forwarded logs cannot measure** — Krisp, for instance — is
carried in that script's `INVOICED_EXTRA_OVERAGE`, keyed by month, and booked to the `Other`
row. Splitting it by agent-session minute share would assert a per-user allocation of a
quantity nothing measures per user; leaving it out would make the month fail to reconcile.

## Notes & caveats

- **Per-user attribution only exists since 2026-05-30** — ElevenLabs stamps `userId` on conversations from then; earlier days have no voice attribution. This is a **forward-growing** dataset; don't try to backfill voice before that.
- **OpenAI stored completions retain ~30 days** — the LLM per-user walk can't go back further than ~30 days.
- **Refresh a boundary day on the next run.** OpenAI's Costs / stored-completions lag ~24h, so a day stored *right after* its UTC rollover can still gain late-arriving completions. On the next session, re-run the prior day's LLM walk and compare the authoritative `total` to what's stored: if it moved, drop that date's rows and re-store; if the delta is `\$0.0000`, the stored day stands. Run this only for days stored within ~24h of their close (i.e. captured at/near the boundary); skip it for days stored well after the lag window.
- **Tier is authoritative from `/subscriptions/list`**, evaluated on the row's date (`purchaseAt` ≤ date ≤ `expirationAt`). Do **not** use the OpenAI `aiPremium` metadata — it mis-tags some paying users (cross-service sync gap; see `Tools/OpenAI`).
- **Allocation, not per-call billing.** `llm_cost` = authoritative project $ × the user's token share; `voice_11labs_agent` = the day's ElevenLabs ConvAI-agent $ × the user's ConvAI voice-minute share; `voice_11labs_livekit` / `voice_cartesia` / `voice_fish` / `voice_inworld` = that TTS vendor's day total × the user's reply-character share **among the LiveKit sessions that used that TTS provider** (minute share when the ledger read fails); `voice_deepgram` / `voice_livekit` = each vendor's DAILY WORKSPACE TOTAL × the user's **overall LiveKit** voice-minute share (STT + transport run on every session); `voice_livekit_platform` (signed unused capacity) books on the `Other` row only. All calibrate to the real daily vendor totals; the `Other` row absorbs each vendor's unattributed remainder.
- **LiveKit-path vendor $ is DERIVED from ESTIMATE rates.** Cartesia credits, Deepgram audio-hours, and LiveKit session-minutes are ACTUAL; the dollar figures are `usage × pricing-sheet rate`. These rates (`--cartesia-rate-per-million 38.00` = Scale overage, `Tools/Cartesia`; `--inworld-rate-per-million 25.00` = Inworld TTS-2 On-Demand list; `--deepgram-rate-per-hour 0.348` = Nova-3 Multilingual PAYG (current), `--livekit-rate-per-min 0.01` = LiveKit Ship agent-session) — pass the flags to override on a plan change. ElevenLabs $ is the actual billed value.
- **ElevenLabs is split by CALLER, not by product type (ENG-982).** ElevenLabs has three
  callers: the ConvAI agent (`voice_11labs_agent`), the **Intelligence Service** raw-TTS
  path that mobile calls (`voice_11labs_intelligence`), and the LiveKit agent's TTS
  (`voice_11labs_livekit`). The day total is split via `has_api_key` (False = ConvAI,
  which runs inside the ElevenLabs platform and has no caller key) then per calling key
  via `all_api_keys`. `product_type=TTS` is **not** LiveKit — assuming it was booked ~8
  weeks of Intelligence Service spend into `voice_11labs_livekit`. `voice_11labs_livekit`
  correctly reads **\$0** until LiveKit moves off Cartesia. Intelligence Service TTS is not
  attributable per user from the ElevenLabs side, so its whole slice lands on the `Other`
  row. Key→caller mapping and the 2026-07-28 rotation cutover: `Tools/ElevenLabs` in AI
  Knowledge → *Attributing spend to the calling service*.
- **`all_api_keys` needs workspace admin.** A Service Account key 403s on it, so
  `ELEVENLABS_ADMIN_API_KEY` must stay a personal (admin) key. If it isn't, the split
  degrades — the whole API-called slice is booked to Intelligence Service with a
  `11labs_caller_degraded` note rather than zeroing a vendor.
- **ElevenLabs 5xx / 429 / transport errors are retried, 4xx are not.** Every ElevenLabs GET on this
  path (`character-stats` in `store.py` and `unit_costs.py`, the ConvAI list and detail walk) goes
  through `_el_get`: capped exponential backoff with jitter, ~4 min budget, `Retry-After` honoured.
  The endpoint has flapped with 500s for minutes ([ENG-1439](https://linear.app/aura-health/issue/ENG-1439)).
  Exhaustion raises `ElevenLabsUnavailable` naming the status and attempt count — re-run the day
  later; a transient outage is never stored as the lossy degraded split. A 403 on `all_api_keys`
  is a fact about the key and still degrades at once.
- **Deepgram is live, and LiveKit-path-only.** `DEEPGRAM_OWNER_API_KEY` reads the production project `7e42b984-…` (**“Aura Health”**), so `voice_deepgram` is ACTUAL audio-hours × the estimate rate. It is \$0 on days with no LiveKit traffic (a plain \$0, no note). If the key is missing or loses access on a day with direct Deepgram traffic, the day is refused (`deepgram_pending`), never stored at \$0.
- **Fish is measured from our side, not the vendor's.** Fish Audio exposes no usage endpoint (`GET /wallet/self/api-credit` is a balance, and top-ups confound a delta), so `voice_fish` is the agent's `tts_characters` over Fish-TTS sessions × the s2-pro rate. A Fish session that never logged `Session usage totals` is **counted and noted** (`fish_partial: N of M … UNMEASURED TTS`), never folded in as zero — a partial day must not read as a cheap one. Reconcile against the Fish credit balance monthly; the two should drift only by the byte-vs-character gap.
- **Inworld is measured by the vendor.** `INWORLD_API_KEY` (the LiveKit agent's key) reads `GET /usage/v1/records` — every Inworld TTS character on the workspace, so it is the bill rather than our count of it. Like Cartesia, the whole day is spread over the day's Inworld-TTS sessions by minute share; only the user-less sessions' share (or the whole day, if no Inworld session carries a user) lands on `Other`. Without the key `voice_inworld` falls back to the agent's `tts_characters` (`inworld_derived: …`, unmeasured sessions counted), never a silent \$0.
- **Sessions with no provider line book under the agent's default TTS on that day** — `elevenlabs` since PROD-4079 (2026-07-31), `cartesia` before (`livekit_minutes.default_tts_provider`). `sessions_without_provider` in the meta says how many that was.
- **LiveKit-path attribution requires LiveKit logs.** On a day with no LiveKit sessions (e.g. before prod voice moves onto the LiveKit path), `total_livekit_min = 0`, so nothing is attributed and any nonzero Cartesia/Deepgram/LiveKit day total lands entirely in the `Other` row. This is correct — there is no user to attribute it to yet.
- **Privacy.** The store holds **full Firebase UIDs** (it's a local data file). Per `Compliance/Privacy`, any *external* surfacing (Slack/Notion/Linear/sheet shared out) must truncate to a UID prefix — never publish full UIDs or user PII.
- **Cost.** ~4 min/day (dominated by the OpenAI stored-completions walk) + ~1–2 min for the ElevenLabs walk; the unit-cost step adds ~5s.
- **Unit-cost store has no 30-day limit.** It reads the Costs/Usage aggregate APIs (long retention), not stored completions — so `--until` backfills work well past the per-user store's 30-day horizon. The dataset starts 2026-06-01.

## Origin

Promoted from the working prototype built during the [PROD-2525](https://linear.app/aura-health/issue/PROD-2525) cost investigation (`append_day.py` + the reconciled `per_user_daily_cost.csv`). Skill tracked in [PROD-2553](https://linear.app/aura-health/issue/PROD-2553).
