---
name: report-livekit-user-cost
description: >
  Report LiveKit-path voice cost for ONE specific user (Firebase UID) over a chosen window —
  month-to-date (default), previous-month, last-7d, last-30d, week-to-date, today,
  yesterday-pt, today-pt, or a custom range. Reads the per-user daily cost store in BigQuery
  (written by `eng:store-aurie-user-daily-cost`) and sums the LiveKit-path vendors: LiveKit
  agent minutes, every TTS vendor on the LiveKit path, and Deepgram STT, plus
  the user's LiveKit minutes. Days not yet stored (today, or a store gap) render as pending,
  never $0.00. Output is per-UID and internal. Trigger on "what is user X costing in LiveKit",
  "livekit cost for user {UID}", "report livekit user cost", "how much did this user's LiveKit
  voice cost", or a pasted UID with a request for their LiveKit voice cost. Do NOT trigger for
  workspace LiveKit totals (`eng:report-livekit-daily-cost`, `eng:analyze-livekit-cost`),
  ElevenLabs ConvAI voice (`eng:report-elevenlabs-user-cost`), or all-source per-user cost
  (`eng:report-user-costs`).
metadata:
  version: "1.3.1"
knowledge:
  reads:
    - Guide/Index             # baseline routing aid (universal)
    - Tools/LiveKit           # LiveKit plan + how the subscription is booked
    - Tools/BigQuery          # aura-main.aurie_cost access via GCP_KEY
    - Compliance/Privacy      # per-UID output: internal only, UID prefix in anything shared
---

# Report LiveKit Cost Per User

Single-user LiveKit-path voice cost — the fourth leg of `eng:report-user-costs`, alongside
OpenAI, ElevenLabs and listens. Use it when you have **one** Firebase UID and want to know what
their voice on the LiveKit agent cost over a window.

**Source: the per-user daily cost store**, `aura-main.aurie_cost.aurie_per_user_daily_cost`,
written once per completed UTC day by
[`eng:store-aurie-user-daily-cost`](../store-aurie-user-daily-cost/SKILL.md). That skill owns
how each vendor's day total is allocated to users (minute share, invoice-booked LiveKit
subscription); this skill only sums what it stored. Read its *Voice attribution* table before
explaining a figure.

## When to use

- "what is user `{UID}` costing in LiveKit"
- "livekit cost for user `{UID}`"
- "report livekit user cost"
- a pasted UID with a request for their LiveKit voice cost specifically

**Do NOT trigger for:** workspace LiveKit totals (`eng:report-livekit-daily-cost`,
`eng:analyze-livekit-cost`), ElevenLabs ConvAI voice (`eng:report-elevenlabs-user-cost`), or the
all-source figure (`eng:report-user-costs`).

## Inputs

- `user_id` (required) — Firebase UID
- `--window` (default `mtd`) — `today`, `wtd`, `mtd`, `prev-month`, `last-7d`, `last-30d`,
  `yesterday-pt`, `today-pt`; or `--from YYYY-MM-DD --to YYYY-MM-DD` (UTC, `--to` exclusive)
- `--no-by-vendor` — total only (per-vendor table is on by default)
- `--json <path>` — structured report, full UID retained

## What this leg counts — and what it does not

The script sums exactly these store columns (`LIVEKIT_COST_COLUMNS` in `scripts/report.py`):

| Column | Vendor |
|---|---|
| `voice_livekit` | LiveKit agent minutes (credit rate) |
| `voice_livekit_platform` | LiveKit unused subscription — booked on the `Other` row only, so \$0 for a real user |
| `voice_cartesia` | Cartesia TTS on LiveKit sessions |
| `voice_fish` | Fish Audio TTS on LiveKit sessions |
| `voice_inworld` | Inworld TTS on LiveKit sessions |
| `voice_deepgram` | Deepgram STT (all LiveKit sessions) |
| `voice_11labs_livekit` | ElevenLabs TTS on LiveKit sessions |

`livekit_minutes` is reported as usage, never summed into dollars.
`deepgram_inference_drawdown` is prepaid capacity already inside the subscription, so it is not counted.

**`voice_11labs_livekit` belongs to this leg, not the ElevenLabs leg — counted exactly once.**
The store splits the ElevenLabs day total **by calling API key**. The ConvAI slice has no caller
key (`has_api_key=False`) and becomes `voice_11labs_agent`. The LiveKit agent's raw-TTS calls are
keyed, and become `voice_11labs_livekit`. `eng:report-elevenlabs-user-cost` walks only
`/v1/convai/conversations`, so it only ever sees the ConvAI slice. Raw TTS calls are not ConvAI
conversations, so they are absent from that walk by construction. For days past retention it
reads `voice_11labs_agent` from this same store, never `voice_11labs_livekit`. The two legs are
disjoint.
`voice_11labs_agent` is therefore never read here (the ElevenLabs leg owns it).
`voice_11labs_intelligence` is booked on the `Other` row only, so it is never per-user.
[`tests/test_livekit_user_cost.py`](../../../../tests/test_livekit_user_cost.py) pins this set
against the store's schema, so a new LiveKit vendor column fails CI until it is classified here.

## Step 0 — Prerequisites

```bash
test -n "$GCP_KEY" && echo "OK" || echo "MISSING GCP_KEY"
```

`GCP_KEY` is the same service account the cost store writes with (`bigquery.jobUser` on
`aura-main`). The script also needs `google-cloud-bigquery`, which plain `python3` on the host
often lacks. **Run it with the framework python**
(`/Library/Frameworks/Python.framework/Versions/3.12/bin/python3` on the team Macs) or any
interpreter that has the package. There is no BigQuery MCP, so this leg is API-first by
necessity.

**Missing key or library degrades — it never drops.** The script prints
`LiveKit cost — … — **unavailable**` with the reason and exits `2`. With `--json`, it still
writes the file with `status: "unavailable"` and `total.cost_usd: null`, so a composing caller
names the leg instead of summing a silent zero.

## Step 1 — Run the report

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-livekit-user-cost/scripts/report.py <UID> --window last-30d
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-livekit-user-cost/scripts/report.py <UID> --from 2026-09-01 --to 2026-09-25
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-livekit-user-cost/scripts/report.py <UID> --window last-30d --no-by-vendor --json /tmp/user_cost_livekit_<prefix>.json
```

It runs two small partition-pruned queries (the dates the store holds in range, and this UID's
rows) and takes seconds. Algorithm detail and the JSON shape:
[`references/algorithm.md`](references/algorithm.md).

## Step 2 — Show the report inline

Render stdout verbatim. The shape:

```
## LiveKit cost — `AsSXRAKH…`  (\$0.00, 0.0 LiveKit min over 30 stored days, 1d pending)

No LiveKit voice on any stored day in this window.

**Notes:**
- 1 day(s) not yet in the per-user cost store (2026-09-25) — shown as pending, not \$0. …
```

**Pending is not zero.** Two different states:

- **A stored day with no row for the UID** is a genuine \$0 — the store wrote that day and this
  user had no attributable LiveKit voice.
- **A day with no rows in the store at all** is `pending`. This covers today, or a day the store
  has not written yet. The store runs up to yesterday, so same-day LiveKit use is never in the
  figure.

A window made only of pending days renders **pending**, and `total.cost_usd` is `null`. A mixed
window renders the stored-days total and names the pending dates. Never paraphrase either state
into `\$0.00`.

## Step 3 — Compose or return

Inert by default: it returns the report and posts nothing. **The output is per-UID and internal.**
Never post it to Slack or an Artifact unless someone explicitly asks. The parent
`eng:report-user-costs` composes it with the other three legs.

## Notes & edge cases

- **Rolling windows** (`last-7d`, `last-30d`) start mid-day; the store is daily, so the first
  partial day counts whole. The report says so.
- **The store is append-only per day and idempotent**, so a figure for a stored day does not
  change on re-run unless the store was re-booked (`rebook_livekit.py` / `store.py --force`).
- **`UNUSABLE_ATTRIBUTION_DATES`** (ENG-1120) affected the LLM join, not voice. This leg reads
  the base table, not the `_prod` view, so a dev-env UID still resolves.

## Out of scope

- **Same-day LiveKit cost** from the live GCP logs (`livekit_minutes.py`). Pending days stay
  pending.
- **Cohort / top-N ranking** — the `chart-aurie-*` family and `eng:report-aurie-margin-users`.

## References

- [`references/algorithm.md`](references/algorithm.md) — pending-vs-zero rule, window→dates, JSON shape.
- [`eng:store-aurie-user-daily-cost`](../store-aurie-user-daily-cost/SKILL.md) — writer and allocation rules.
- Parent: [`eng:report-user-costs`](../report-user-costs/SKILL.md). Linear: [ENG-1412](https://linear.app/aura-health/issue/ENG-1412).
