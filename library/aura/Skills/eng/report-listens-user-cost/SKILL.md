---
name: report-listens-user-cost
description: >
  Report Aura content-listen coach-payout cost for ONE specific user (Firebase UID) over a
  chosen window — month-to-date (default), previous-month, last-7d, last-30d, week-to-date,
  today, yesterday-pt, today-pt, or a custom range. Point-lookup counterpart to the rank-N
  `eng:analyze-aura-cost-per-user`. Calls PaymentService's authoritative listens-aggregate
  endpoint with a server-side user filter, using the same rate logic as the monthly payout job
  rather than an estimate. Output is a markdown report with total dollars, total qualifying
  listens and a per-tier breakdown table. Trigger on "what is user X costing in Aura content
  payouts", "how much did this user cost in listens", "listens cost for user {UID}", "Aura
  content payout for user", "report listens user cost", or a pasted UID with a request for
  their content-listen cost. Do NOT trigger for top-N ranking
  (`eng:analyze-aura-cost-per-user`) or project-level totals (`eng:analyze-aura-cost`).

metadata:
  version: "3.1.0"
knowledge:
  reads:
    - Guide/Index             # baseline routing aid (universal)
    - Products/Aurie     # brand-voice priors for report framing (light)
    - Tools/Aura API                 # Aura API access pattern (AURA_API_TOKEN)
---

# Report Listens Cost Per User

Single-user counterpart of `eng:analyze-aura-cost-per-user`. Use this when
you have **one** Firebase UID in hand and want to know what their Aura
content-listen coach-payout cost is over a window — not when you're
hunting for the top-N spenders.

Same authoritative dollar source as the monthly payout job:
PaymentService's `POST /payouts/listens/aggregate` (see
[PROD-1961](https://linear.app/aura-health/issue/PROD-1961)) with a
`users` server-side filter. Per-listen rates and exclusions exactly
match `src/jobs/payout.js`.

## When to use

Trigger when the user asks any of:

- "what is user `<UID>` costing in Aura content payouts"
- "how much did this user cost in listens"
- "Aura content payout for user `<UID>`"
- "listens cost for user `<UID>`"
- "report listens user cost"
- "report content-listen cost for a user"
- pastes a Firebase UID and asks for their content-listen cost specifically

**Do NOT trigger for:**

- Cohort or top-N ranking → `eng:analyze-aura-cost-per-user`
- Project-level totals (no per-user dimension) → `eng:analyze-aura-cost`
- Daily snapshot of top listeners → `eng:report-top-aura-listeners`
- OpenAI inference cost → `eng:report-openai-user-cost`
- ElevenLabs TTS cost → `eng:report-elevenlabs-user-cost`
- All-source per-user cost (OpenAI + ElevenLabs + LiveKit + listens combined) →
  parent `eng:report-user-costs`

## Inputs

- `user_id` (required) — Firebase UID
- `--window` (optional, default `mtd`) — one of `today`, `wtd`, `mtd`,
  `prev-month`, `last-7d`, `last-30d`, `yesterday-pt`, `today-pt`. Custom
  range via `--from YYYY-MM-DD --to YYYY-MM-DD`.
- `--by-tier` (default on; `--no-by-tier` for total only) — show the
  per-tier breakdown table (`<500`, `500-1999`, `2000-4999`, `≥5000`,
  `music`).
- `--by-coach` (optional) — add a per-coach breakdown of the user's
  listens (one row per coach they've listened to).
- `--ignore-daily-cap` — bypass the 6-listens-per-(user,coach)-per-day
  production cap. Useful for *"what if the cap were lifted?"* analyses.
- `--include-free-listens` — include free / trial / payment-pending
  listens (zero-cost; inflates listen counts).
- `--base-url` — override the PaymentService host (default
  `https://services.aurahealth.io`; use staging via env or this flag).
- `--json <path>` — write structured report (full UID retained) to disk
  for downstream consumers.

## Step 0 — Check prerequisites

```bash
test -n "$AURA_API_TOKEN" && echo "OK" || echo "MISSING AURA_API_TOKEN"
```

- `AURA_API_TOKEN` — admin scope on PaymentService. Required.

If missing, hand off to `update-config`.

**Cowork caveat.** `mcp__workspace__bash` strips host env (PROD-1305). Use
the per-session paste-export workaround in CLAUDE.md → "Cowork constraints",
or run from the host harness.

## Step 1 — Run the report

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-listens-user-cost/scripts/report.py <UID>
```

Common variants:

```bash
# Default: month-to-date with per-tier breakdown
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-listens-user-cost/scripts/report.py <UID>

# Previous calendar month
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-listens-user-cost/scripts/report.py <UID> --window prev-month

# Custom range
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-listens-user-cost/scripts/report.py <UID> \
  --from 2026-04-11 --to 2026-05-27

# Total only (compact for orchestrator composition)
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-listens-user-cost/scripts/report.py <UID> --no-by-tier

# Per-coach breakdown (CX use case: who does this user listen to most?)
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-listens-user-cost/scripts/report.py <UID> --by-coach

# Hypothetical "no daily cap" view
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-listens-user-cost/scripts/report.py <UID> --ignore-daily-cap

# Structured JSON for downstream callers (parent `eng:report-user-costs`)
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-listens-user-cost/scripts/report.py <UID> \
  --window last-30d --json /tmp/listens_user.json
```

The script handles: window resolution (via the canonical sibling
`resolve_window.py`), a single POST to `/payouts/listens/aggregate` with
`users=[<UID>]`, optional second POST with `groupBy=coachId` for
`--by-coach`, and rendering. See
[`references/algorithm.md`](references/algorithm.md) for the
per-listen rate table, exclusion rules, and what this skill explicitly
does not do.

## Step 2 — Show the report inline

The script prints a single markdown document to stdout. Render it
verbatim; don't paraphrase the numbers.

Example shape:

```
## Listens cost — `e8XFD61a…`  (\$1.65, 22 qualifying listens, \$0.04/day over 47d)

| Tier | Listens | Cost |
|---|---:|---:|
| < 500 | 9 | \$0.54 |
| 500 – 1,999 | 8 | \$0.80 |
| 2,000 – 4,999 | 3 | \$0.39 |
| music | 2 | \$0.10 |

**Exclusions:**
- 0 listens dropped by the 6-per-(user,coach)-per-day cap.
- 3 free / trial / payment-pending listens excluded (zero-cost). Pass `--include-free-listens` to count them.
```

## Step 3 — Compose, route, or return

This skill is **inert by default** — it just returns the report. The
caller decides what to do with it:

- `eng:report-user-costs` (parent,
  [PROD-2242](https://linear.app/aura-health/issue/PROD-2242)) composes
  this with `eng:report-openai-user-cost`, `eng:report-elevenlabs-user-cost`
  and `eng:report-livekit-user-cost`.
- Direct CLI use for CX inquiries — paste the markdown into a Slack
  thread with `--no-by-tier` if you want a compact one-liner.

## Notes & edge cases

- **User has zero qualifying listens.** Renders `No qualifying listens in
  this window.` and exits 0 — not an error. Exclusion lines still appear
  if any listens were dropped by the daily cap or premium filter.
- **`--by-coach` with no listens.** The per-coach POST returns an empty
  rows array; the **By coach** block is suppressed.
- **Staging.** Set `--base-url https://services.dev.aurahealth.io` or
  `AURA_API_BASE_URL` env var to point at dev. The `AURA_API_TOKEN`
  must match the environment.
- **Empty windows.** Future date ranges, or windows where no qualifying
  listens occurred, render "No qualifying listens in this window."
  and exit 0.
- **Monthly-boundary edge cases.** A listen at 11:59 PM on the last of
  the month vs 00:01 AM on the 1st lands in different payout cycles;
  per-listen cost is identical so the answer is the same for THIS
  skill, but the rendered $ may not align with a specific monthly
  invoice if your window straddles a month boundary.

## Out of scope (v1)

- **Cohort / top-N ranking.** Use `eng:analyze-aura-cost-per-user`.
- **Cost forecasting / unit economics.** Descriptive only.
- **OpenAI inference or ElevenLabs TTS cost.** Siblings — see
  [PROD-2243](https://linear.app/aura-health/issue/PROD-2243) /
  [PROD-2244](https://linear.app/aura-health/issue/PROD-2244).

## References

- [`references/algorithm.md`](references/algorithm.md) — per-listen rate
  table, exclusion rules, what this skill explicitly doesn't do.
- Existing siblings:
  [`eng:analyze-aura-cost-per-user`](../analyze-aura-cost-per-user/SKILL.md)
  (rank-N), [`eng:analyze-aura-cost`](../analyze-aura-cost/SKILL.md)
  (project-level), [`eng:report-top-aura-listeners`](../report-top-aura-listeners/SKILL.md)
  (daily snapshot).
- Endpoint: [PROD-1961](https://linear.app/aura-health/issue/PROD-1961) —
  `POST /payouts/listens/aggregate` on PaymentService. Full schema + SQL
  + rate table.
- Linear: [PROD-2245](https://linear.app/aura-health/issue/PROD-2245)
  (this card), [PROD-2242](https://linear.app/aura-health/issue/PROD-2242)
  (parent family).
- PaymentService home: `~/Development/Aura/Services/PaymentService`.
