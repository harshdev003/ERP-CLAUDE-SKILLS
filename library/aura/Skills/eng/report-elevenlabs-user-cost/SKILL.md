---
name: report-elevenlabs-user-cost
description: >
  Report ElevenLabs Conversational AI cost for ONE specific user (Firebase UID) over a chosen
  window — month-to-date (default), previous-month, last-7d, last-30d, week-to-date, today,
  yesterday-pt, today-pt, or a custom range. Point-lookup counterpart to
  `eng:analyze-elevenlabs-cost-per-user`. Recent days walk the conversations API (per-call
  minutes at in-plan vs burst rates); days past ElevenLabs' 10-day retention come from the
  per-user daily cost store in BigQuery, so long windows are not cut short. Trigger on "what is user X costing
  ElevenLabs", "how much did this user cost in TTS", "elevenlabs cost for user {UID}", "report
  elevenlabs user cost", "report voice cost for a user", or a pasted UID with a request for
  their voice cost. Do NOT trigger for cohort or top-N ranking
  (`eng:analyze-elevenlabs-cost-per-user`), workspace-level totals
  (`eng:analyze-elevenlabs-costs`), or daily snapshots (`eng:report-elevenlabs-costs`).

metadata:
  version: "3.4.1"
knowledge:
  reads:
    - Guide/Index             # baseline routing aid (universal)
    - Products/Aurie     # brand-voice priors for report framing (light)
    - Tools/ElevenLabs                   # ElevenLabs API access (ELEVENLABS_API_KEY)
    - Tools/BigQuery                 # per-user daily cost store for pre-retention days (GCP_KEY)
---

# Report ElevenLabs Cost Per User

Single-user counterpart of `eng:analyze-elevenlabs-cost-per-user`. Use
this when you have **one** Firebase UID in hand and want to know what
they cost in ElevenLabs Conversational AI over a window — not when
you're hunting for the top-N spenders.

**Actuals only.** Per-call $ is computed directly as
`call_duration_secs / 60 × ELABS rate` (\$0.09/min in-plan, \$0.19/min
burst). Both inputs are API actuals — no credits→minutes derivation,
no plan-packaging ratio. Same source as the rank-N sibling.

## When to use

Trigger when the user asks any of:

- "what is user `<UID>` costing ElevenLabs"
- "how much did this user cost in TTS / voice"
- "elevenlabs cost for user `<UID>`"
- "report elevenlabs user cost"
- "report voice cost for a user"
- pastes a Firebase UID and asks for their voice cost specifically

**Do NOT trigger for:**

- Cohort or top-N ranking → `eng:analyze-elevenlabs-cost-per-user`
- Workspace-level totals → `eng:analyze-elevenlabs-costs`
- Daily snapshot of top voice users → `eng:report-elevenlabs-costs`
- OpenAI inference cost → `eng:report-openai-user-cost`
- Aura content-listen cost → `eng:report-listens-user-cost`
- All-source per-user cost (OpenAI + ElevenLabs + LiveKit + listens combined) →
  parent `eng:report-user-costs`

## Inputs

- `user_id` (required) — Firebase UID
- `--window` (optional, default `mtd`) — one of `today`, `wtd`, `mtd`,
  `prev-month`, `last-7d`, `last-30d`, `yesterday-pt`, `today-pt`.
  Custom range via `--from YYYY-MM-DD --to YYYY-MM-DD`.
- `--by-bucket` (default on; `--no-by-bucket` for total only) — show
  in-plan vs burst breakdown table.
- `--json <path>` — write structured report (full UID retained) to
  disk for downstream consumers.

## Step 0 — Check prerequisites

```bash
test -n "$ELEVENLABS_ADMIN_API_KEY" || test -n "$ELEVENLABS_API_KEY" \
  && echo "OK" || echo "MISSING ELEVENLABS_ADMIN_API_KEY / ELEVENLABS_API_KEY"
test -n "$GCP_KEY" && echo "OK store" || echo "MISSING GCP_KEY (pre-retention days unavailable)"
```

- `ELEVENLABS_ADMIN_API_KEY` (preferred) or `ELEVENLABS_API_KEY` —
  required.
- `GCP_KEY` plus `google-cloud-bigquery` — needed only when the window
  reaches past the retention cutoff. Plain `python3` often lacks the
  library, so run with an interpreter that has it (the framework python
  on the team Macs). Without either, the store half reports
  `unavailable`, named, the script exits `2`, and the live half still
  reports.

**Two sources, split at the retention cutoff.** ElevenLabs purges
dynamic variables (including `userId`) after the workspace's 10-day
Conversations Retention Period, so the live walk can attribute only
recent calls. The script splits the window at the **first UTC midnight
after the cutoff**:

- **Whole days before the split** come from the per-user daily cost
  store, `voice_11labs_agent` in
  `aura-main.aurie_cost.aurie_per_user_daily_cost` (written by
  [`eng:store-aurie-user-daily-cost`](../store-aurie-user-daily-cost/SKILL.md)).
  That is the day's actual ConvAI bill allocated by minute share. A
  stored day with no row for the user is \$0. A day the store has not
  written is **pending**, never \$0.
- **From the split on**, including today, the live walk runs unchanged.

Each day is read from exactly one source, so the two halves add without
double-counting. A window entirely past retention (e.g. `prev-month`)
skips the walk. A recent window (e.g. `last-7d`) never touches BigQuery.

If `ELEVENLABS_*_API_KEY` is missing, hand off to `update-config`.

**Cowork caveat.** `mcp__workspace__bash` strips host env (PROD-1305).
Use the per-session paste-export workaround in CLAUDE.md → "Cowork
constraints", or run from the host harness.

## Step 1 — Run the report

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-elevenlabs-user-cost/scripts/report.py <UID>
```

Common variants:

```bash
# Default: month-to-date with per-bucket breakdown
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-elevenlabs-user-cost/scripts/report.py <UID>

# Last 30 days — older days from the cost store, recent ones live
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-elevenlabs-user-cost/scripts/report.py <UID> --window last-30d

# Last 7 days
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-elevenlabs-user-cost/scripts/report.py <UID> --window last-7d

# Custom recent range
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-elevenlabs-user-cost/scripts/report.py <UID> \
  --from 2026-05-20 --to 2026-05-27

# Total only (compact for orchestrator composition)
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-elevenlabs-user-cost/scripts/report.py <UID> --no-by-bucket

# Structured JSON for downstream callers (parent `eng:report-user-costs`)
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-elevenlabs-user-cost/scripts/report.py <UID> \
  --window last-7d --json /tmp/elabs_user.json
```

The script handles: window resolution (via the canonical sibling
`resolve_window.py`), the store read for pre-retention days, the
cursor-paginated walk of `/v1/convai/conversations`, per-conversation
detail fetch + `userId` matching, in-plan / burst split, and rendering.
A window with store days renders a **Source** table (store vs live)
above the bucket table. The JSON adds `status` (`ok` / `partial` /
`pending` / `unavailable` / `error`) and `sources.store` /
`sources.live`. See
[`references/algorithm.md`](references/algorithm.md) for the math, the
retention-cutoff handling, and what this skill explicitly doesn't do.

## Step 2 — Show the report inline

The script prints a single markdown document to stdout. Render it
verbatim; don't paraphrase the numbers.

Example shape:

```
## ElevenLabs cost — `e8XFD61a…`  (\$5.41, 18 calls, 36m 12s total over 7d, \$0.77/day)

| Bucket | Calls | Minutes | Rate | Cost |
|---|---:|---:|---:|---:|
| In-plan | 16 | 32.45 | \$0.09/min | \$2.92 |
| Burst   |  2 | 13.10 | \$0.19/min | \$2.49 |
```

## Step 3 — Compose, route, or return

This skill is **inert by default** — it just returns the report. The
caller decides what to do with it:

- `eng:report-user-costs` (parent,
  [PROD-2242](https://linear.app/aura-health/issue/PROD-2242)) composes
  this with `eng:report-openai-user-cost`, `eng:report-livekit-user-cost`
  and `eng:report-listens-user-cost`.
- Direct CLI use for CX inquiries — paste the markdown into a Slack
  thread with `--no-by-bucket` if you want a compact one-liner.

## Notes & edge cases

- **User has zero ElevenLabs calls.** Renders "No Conversational AI
  calls in this window with `userId` dynamic variable matching." and
  exits 0 — not an error. Note that this rules out *attributable*
  calls only; anonymous / pre-auth calls (see "Unattributable calls"
  in [`references/algorithm.md`](references/algorithm.md)) are
  silently skipped because they have no `userId`.
- **Anonymous / pre-auth calls.** ElevenLabs is called directly by
  the Aurie mobile / desktop clients; they only set the `userId`
  dynamic variable when the user is signed in. Conversations without
  a `userId` (anonymous voice attempts before account creation,
  unauthenticated browse sessions on web, etc.) can't be attributed
  to any specific UID and are skipped. Workspace-level cost still
  counts them — use
  [`eng:analyze-elevenlabs-costs`](../analyze-elevenlabs-costs/SKILL.md)
  if you need to size the unattributed bucket.
- **Pre-retention days are allocated, recent days are per-call.** The
  store books each day's actual ConvAI bill by minute share, while the
  walk prices each call at the in-plan / burst rate. The two agree
  closely but not to the cent. The Source table keeps them apart so
  the reader can see which is which.
- **A 404 inside the retention window** still counts as
  `scrubbed_conversations`, with a caveat. That is rare, since those
  days are within retention.
- **Runtime is `O(this user's calls)`, not `O(workspace voice volume)`.**
  The list call is filtered server-side with `user_id`, so only that
  user's conversations get a detail fetch. Measured 2026-08-18 on
  `--window today`: **318 → 18** detail fetches, 26.9s, cost identical
  to the unfiltered walk. A dormant user is cheap to query. The
  walk only ever covers days inside retention; older days are a store
  read. Detail is still one fetch per *candidate* conversation —
  `dynamic_variables` (and therefore `userId`) exist only on detail,
  which is the part with no shortcut.
- **A silently-ignored `user_id` degrades to slow, never to wrong.**
  ElevenLabs ignores unknown query params without erroring, so if that
  parameter is ever renamed the list would come back unfiltered. The
  per-detail `userId` check is retained precisely for that case: the
  cost figure stays correct and the walk just goes back to
  `O(workspace)`. The report surfaces it —
  `server_side_user_filter_effective: false`, a caveat, and a stderr
  warning — rather than quietly getting slower. See
  [`references/algorithm.md`](references/algorithm.md).
- **ElevenLabs TTS on the LiveKit path is not this skill's.** The
  walk covers `/v1/convai/conversations` only — the ConvAI agent. The
  LiveKit agent calls ElevenLabs as a raw-TTS provider, and those calls
  are not ConvAI conversations, so they never appear here. The per-user
  cost store books them as `voice_11labs_livekit`, split from ConvAI by
  calling API key, and `eng:report-livekit-user-cost` counts them. So
  the two legs are disjoint and add without double-counting. Keep this
  walk on ConvAI: `tests/test_livekit_user_cost.py` fails if this
  script starts reading raw-TTS usage.
- **Burst-vs-in-plan attribution.** Comes from `metadata.charging.is_burst`
  on each call. In-plan rate (\$0.09/min) applies even on workspace
  usage that has crossed into overage — we can't attribute the
  marginal overage minute to one specific call.

## Out of scope (v1)

- **Cohort / top-N ranking.** Use `eng:analyze-elevenlabs-cost-per-user`.
- **Day-over-day trend for one user.** Call the skill twice with
  different windows and diff.
- **OpenAI inference or content-listen cost.** Siblings — see
  [PROD-2243](https://linear.app/aura-health/issue/PROD-2243) /
  [PROD-2245](https://linear.app/aura-health/issue/PROD-2245).

## References

- [`references/algorithm.md`](references/algorithm.md) — math, the
  10-day retention cutoff handling, and what this skill explicitly
  doesn't do.
- Existing sibling:
  [`eng:analyze-elevenlabs-cost-per-user`](../analyze-elevenlabs-cost-per-user/SKILL.md)
  (rank-N).
- Existing workspace-level:
  [`eng:analyze-elevenlabs-costs`](../analyze-elevenlabs-costs/SKILL.md),
  [`eng:report-elevenlabs-costs`](../report-elevenlabs-costs/SKILL.md)
  (daily snapshot).
- AI Knowledge: `Tools/ElevenLabs` — rate constants (\$0.09/min in-plan,
  \$0.19/min burst, Business plan), workspace retention setting.
- Linear: [PROD-2244](https://linear.app/aura-health/issue/PROD-2244)
  (this card), [PROD-2242](https://linear.app/aura-health/issue/PROD-2242)
  (parent family).
