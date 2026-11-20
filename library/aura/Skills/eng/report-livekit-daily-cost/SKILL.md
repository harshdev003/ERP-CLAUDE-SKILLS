---
name: report-livekit-daily-cost
description: >
  Renders a PER-DAY LiveKit-path voice cost table — the LiveKit voice path ONLY, decomposed by
  role: transport (LiveKit Cloud), STT (Deepgram) and TTS (Cartesia plus ElevenLabs-as-TTS) —
  with approximate session-minutes, distinct users on the LiveKit path, and all-in dollars per
  voice-minute. Reads the already-reconciled daily cost store in BigQuery rather than
  re-deriving from LiveKit logs or vendor APIs. LiveKit-path dollars are DERIVED upstream from
  pricing-sheet estimate rates; only the ElevenLabs-TTS column is an authoritative billed
  figure. Aggregate-only, never emits a UID. Trigger on "livekit daily cost", "report livekit
  daily cost", "what did livekit cost yesterday", "livekit cost per day", "livekit voice cost
  report", "how much is the livekit path costing", "daily cost of the voice migration",
  "livekit path spend", or "break down livekit cost by vendor". Do NOT use for the combined
  all-source daily table (`eng:report-aurie-daily-costs`).

metadata:
  version: "3.3.1"
knowledge:
  reads:
    - Guide/Index          # baseline routing aid (universal)
    - Tools/LiveKit        # transport role, forwarded GCP logs, Cloud Scale estimate rate
    - Tools/Cartesia       # TTS role on the LiveKit path + Startup-tier estimate rate
    - Tools/Deepgram       # STT role; prod project 7e42b984 (LiveKit-path-only)
    - Tools/ElevenLabs         # ElevenLabs' second role: a selectable TTS on the LiveKit path
    - Compliance/Privacy   # reads a UID-bearing store; output must stay aggregate-only
    - Tools/Slack         # channel directory + Post format footer spec for the Step 4 post
    - Tools/BigQuery                 # aura-main.aurie_cost store access (GCP_KEY)
---

# Report LiveKit Daily Cost

## Goal

A **per-day, LiveKit-path-only** voice cost readout: one row per calendar day with the
path decomposed by role, ≈session-minutes, the number of distinct users on the path, and
all-in $/voice-min. The daily-cadence LiveKit counterpart to
[`eng:report-aurie-daily-costs`](../report-aurie-daily-costs/SKILL.md), which reports
combined all-source spend and only ever shows LiveKit blended in with the other vendors.

Use it to watch the **ElevenLabs ConvAI → LiveKit voice migration** in dollars, and to see
which LiveKit-path vendor is driving a day's spend.

## Data source — the stored daily cost store, not a re-derivation

This skill **reads** BigQuery `aura-main.aurie_cost`, the source of truth maintained by
[`eng:store-aurie-user-daily-cost`](../store-aurie-user-daily-cost/SKILL.md). It does
**not** re-walk the forwarded LiveKit GCP logs or call the vendor APIs — that work already
happened upstream, per-day and per-user, and re-deriving would risk a second set of numbers
that disagrees with the reconciled store. Two tables:

| Table | Used for |
|---|---|
| `aurie_per_user_daily_cost` | the four LiveKit-path vendor $ columns, distinct LiveKit users, subscription split |
| `aurie_daily_unit_costs` | `cost_per_voice_min_livekit`, inverted to recover ≈session-minutes |

For a re-derived, log-sourced windowed analysis instead, use
[`eng:analyze-livekit-cost`](../analyze-livekit-cost/SKILL.md).

### The four columns and their roles

| Role | Column | Basis |
|---|---|---|
| Transport | `voice_livekit` | LiveKit Cloud — **ESTIMATE** rate |
| STT | `voice_deepgram` | Deepgram — **ESTIMATE** rate off actual audio-hours |
| TTS | `voice_cartesia` | Cartesia — **ESTIMATE** rate off actual credits |
| TTS | `voice_11labs_livekit` | ElevenLabs as a LiveKit TTS — **actual billed**, split by **calling API key** (ENG-982). Swings between `\$0` and the whole LiveKit TTS bill as the selected provider changes; so does `voice_cartesia`, in the opposite direction. |

**LiveKit-path total = all four summed.** The LiveKit agent picks a TTS per session, so
Cartesia and ElevenLabs-TTS are each allocated only to the sessions that used them, while
Deepgram and transport allocate across all LiveKit minutes. Only the ElevenLabs-TTS column
is a billed figure — never present the other three as dollars owed. Rate overrides live
upstream on the store skill (`--cartesia-rate-per-million` / `--deepgram-rate-per-hour` /
`--livekit-rate-per-min`); re-run the store to change them, not this skill.

## Step 0 — Prerequisites

Needs `GCP_KEY` (the `claude@firebase-auratech16` SA, granted `bigquery.jobUser` +
`bigquery.dataEditor` on `aura-main`) and `google-cloud-bigquery`
(`python3 -m pip install --user google-cloud-bigquery`). No BigQuery MCP exists, so this is
API-first by necessity; a missing key or library exits with a clear message rather than
degrading — BigQuery **is** the record.

The store must already be populated for the days you ask about. An unpopulated window exits
with a pointer to `eng:store-aurie-user-daily-cost`.

**BigQuery target.** Defaults to `aura-main.aurie_cost`; override with `--project` /
`--dataset` (or `$AURIE_BQ_PROJECT` / `$AURIE_BQ_DATASET`).

## Step 1 — Resolve the window

Pick exactly one (default is the **latest stored day**, matching the daily cadence):

| Flag | Window |
|---|---|
| _(none)_ | The latest day present in the store |
| `--date YYYY-MM-DD` | That single day |
| `--last N` | The most recent N calendar days ending at the latest stored day |
| `--from YYYY-MM-DD --to YYYY-MM-DD` | Inclusive range (either bound optional) |

All dates are the store's own **UTC** day buckets. If the user names a month, translate to
`--from`/`--to`.

## Step 2 — Run the report

```bash
# Latest stored day (default)
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-livekit-daily-cost/scripts/report.py

# A specific day / a trailing window
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-livekit-daily-cost/scripts/report.py --date 2026-07-24
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-livekit-daily-cost/scripts/report.py --last 14

# Custom range + JSON
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-livekit-daily-cost/scripts/report.py \
  --from 2026-07-01 --to 2026-07-26 --json /tmp/livekit_daily.json
```

The script prints the markdown table, a headline line, and a basis footnote; `--json` also
writes `{skill_version, source, summary, days[]}`.

## Step 3 — Show the report inline

Show the script's stdout directly — it is already the deliverable. Lead with the headline it
prints, e.g. _"LiveKit-path voice, Jul 20 → Jul 26: \$43.25 over ≈767 session-min
(≈\$0.0564/min); peak Jul 24 \$16.21."_

Then add one line of interpretation the script can't: which vendor drove the window (read it
off the numbers — whichever TTS is currently selected carries essentially all of the TTS
spend, so don't assume which), and whether $/voice-min is trending toward or away from the ≤\$0.07
blended voice target that [`eng:chart-aurie-daily-cost`](../chart-aurie-daily-cost/SKILL.md)
tracks. For a root cause on a spike, hand off — `eng:analyze-livekit-usage` (session volume),
`eng:analyze-livekit-cost` (log-sourced re-derivation), or `eng:investigate-livekit-logs`.

## Step 4 — Ask where to route it

Do **not** route automatically. Offer:

1. **Post to Slack** — `#eng-budget` (`C05PC18APLH`, canonical ID in `Tools/Slack`). Build
   the footer with the helper so the version can't drift:
   `python3 <post-to-slack>/scripts/slack_post.py footer --skill eng:report-livekit-daily-cost`,
   or pass `--footer eng:report-livekit-daily-cost` on the post call. Never hand-type it.
2. **Save Markdown** to a path the user provides.
3. **Nothing, just show it.**

## Output

A markdown table plus headline and footnote:

| Date | Transport $ | Deepgram (STT) $ | Cartesia (TTS) $ | 11Labs (TTS) $ | LiveKit-path $ | Session min | LK users | $/voice-min |
|---|---:|---:|---:|---:|---:|---:|---:|---:|

A **Total** row is appended only for multi-day windows (over one day it would just repeat the
row). `--json` adds `summary.totals`, `summary.blended_cost_per_voice_min` (plus its
`blended_basis_usd` / `blended_excluded_usd` / `blended_excluded_days` provenance),
`summary.peak`, `summary.latest`, and per-day objects carrying the subscription split
(`lk_paid` / `lk_trial` / `lk_free` / `lk_grace`) and both rate variants
(`cost_per_voice_min_all_in` and the store's `stored_cost_per_voice_min_livekit`).

## Edge cases

- **Window with stored days but no LiveKit spend** (anything before 2026-07-01, the first
  LiveKit-path day) → renders the zero rows and says so explicitly, pointing at
  `eng:report-aurie-daily-costs` for the ElevenLabs ConvAI path. Not an error.
- **Window with no stored days at all** → exits with a pointer to
  `eng:store-aurie-user-daily-cost`. The store currently starts 2026-06-01.
- **Cost with 0 LiveKit users** — real and expected. The $ columns sum **every** row
  including the reconciling `Other` row (that is what makes each day tie to the
  authoritative vendor total), while user counts exclude `Other` because it is not a user.
  So an unattributable remainder shows as spend with no users (e.g. 2026-07-20: \$0.43 of
  ElevenLabs-TTS on `Other`).
- **Session-minutes are `—`** when the day has no `cost_per_voice_min_livekit` in the unit
  store (unit store not yet written for that day, or zero LiveKit minutes). The unit store
  is optional; the cost columns still render.
- **Re-runs are safe.** Read-only — no writes to BigQuery, no side effects. Running twice
  produces the same table.

## Notes & caveats

- **Session-minutes are ≈, and the two $/min figures differ on purpose.** Minutes are
  recovered by inverting the store's `cost_per_voice_min_livekit`, whose basis is
  **transport + Deepgram + Cartesia only — it excludes `voice_11labs_livekit`**. The script
  therefore divides by that same 3-vendor basis; dividing the 4-vendor LiveKit-path total by
  that rate would overstate minutes ~2x. The recovered minutes cross-check to within a minute
  against `voice_livekit ÷ \$0.01`. The table's `$/voice-min` is deliberately **all-in** over
  the full 4-vendor total, so it is always ≥ the store's stored rate; both are in `--json`.
- **The blended $/voice-min divides matched quantities only.** A day can carry LiveKit-path
  spend with no recoverable minutes (unit-store lag, or an 11Labs-TTS-only remainder on
  `Other`). Such days are excluded from the blended rate's **numerator** as well as its
  denominator — including their $ over minutes that don't exist would inflate the rate, the
  same rule the upstream unit-cost store applies. They stay in the reported **total**, and
  the footnote names the excluded $ and day count whenever any exists (`blended_basis_usd` /
  `blended_excluded_usd` / `blended_excluded_days` in `--json`). So the Total row's
  `$/voice-min` can cover slightly less spend than the LiveKit-path total beside it — by
  design.
- **A `\$0` in EITHER TTS column is a provider selection, not missing data** — and so is one
  carrying the entire TTS bill. The LiveKit agent picks one provider per session, so
  `voice_cartesia` and `voice_11labs_livekit` trade places as the selection changes; check
  the day's per-provider LiveKit minutes before treating either as a gap. Separately, until
  2026-07-28 the 11Labs-TTS column carried ~\$5/week of **Intelligence Service** raw TTS
  (what mobile calls) that was mis-booked here by a `product_type`-based split — it is not,
  and never was, LiveKit spend. ENG-982 re-split ElevenLabs by calling API key, so the
  column now shows only genuine LiveKit-agent TTS. That also means
  the LiveKit-path total is ~14% lower than pre-fix (e.g. 2026-07-20→26: \$37.95, not
  \$43.25). Intelligence Service TTS now lives in `voice_11labs_intelligence` and is
  correctly **excluded** from this report. See `Tools/ElevenLabs` → *Attributing spend to the
  calling service*.
- **Deepgram \$0 is usually legitimate, not a gap.** Deepgram bills to the production project
  `7e42b984-…` (“Aura Health”) — the one the LiveKit voice agent authenticates against — and
  `DEEPGRAM_OWNER_API_KEY` reads it, so the column carries ACTUAL audio-hours. Deepgram is
  **LiveKit-path-only**, so \$0 on a day with no LiveKit traffic is expected. The footnote
  flags a possible key gap only when actual LiveKit **sessions** ran (non-zero transport or
  recovered minutes) and Deepgram is still \$0 — so neither an all-zero pre-traffic window nor
  a lone 11Labs-TTS remainder day (path spend with no session traffic behind it) misfires it.
  Measured audio-hours track LiveKit session-minutes closely (~90% over 2026-07, 94–98% on
  high-volume days), since STT bills real audio rather than session wall-clock.
- **Estimate rates dominate the total.** Three of four columns are pricing-sheet estimates,
  and they are the larger share, so treat the LiveKit-path total as a well-grounded estimate
  rather than a bill. Swap in contract rates upstream in the store skill.
- **Base table, not the `_prod` view.** Same deliberate choice as
  `eng:chart-aurie-daily-cost`: reading the env-filtered
  `aurie_per_user_daily_cost_prod` view would drop the `Other` remainder and the not-yet-tagged
  users, breaking the reconcile-to-the-vendor-total guarantee that makes this store worth
  reading. Dev/QA traffic on the LiveKit path is consequently included.
- **Privacy.** Aggregate-only — per-day sums and counts, never a Firebase UID, even though the
  underlying store is UID-bearing (see `Compliance/Privacy`). For a UID-bearing per-user view,
  `eng:report-aurie-margin-users` is the sanctioned internal-only precedent.
- **Read-only.** This skill never writes the store. To add a day, use
  `eng:store-aurie-user-daily-cost`.

## Origin

[ENG-959](https://linear.app/aura-health/issue/ENG-959) — the LiveKit family covered usage,
quota, health, latency, and logs, but LiveKit cost was only ever visible blended across
vendors inside the combined daily/weekly reports.
