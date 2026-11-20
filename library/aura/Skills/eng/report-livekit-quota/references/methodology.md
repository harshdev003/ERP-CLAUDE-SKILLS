# report-livekit-quota — methodology

## Data source — DERIVED (LiveKit has no billing/usage API)

LiveKit Cloud exposes **no** billing or usage API — spend and the plan's included-minute counter
live only in the dashboard. So, like `report-cartesia-quota`, this quota is **derived**:

- **Consumed agent-session minutes this billing period** come from the forwarded agent logs
  (`projects/firebase-auratech16/logs/aurie-voice-agent`, `resource.type=global`) via the shared
  `livekit_core` module — the same minute logic as `analyze-livekit-usage`. A "session" is one
  LiveKit `room`; its minutes are `(last_ts − first_ts)` (no explicit duration field is logged).
- **Included minutes / overage rate** are config flags, because LiveKit doesn't expose the plan:
  Build 1,000 / Ship 5,000 / Scale 50,000 included agent-session min per month, overage
  **$0.010/min**. Override with `--plan`, `--included-minutes`, `--overage-rate-per-min`.

## Billing period + projection

- `--renewal-day` (default 1) anchors the monthly period; `period_start` = the most recent renewal
  day at or before now, `period_days_total` = days to the next renewal.
- `burn_per_day = consumed / days_elapsed`; `projected = consumed + burn × days_remaining`.
- `projected_overage_$ = max(0, projected − included) × overage_rate`.

## Caveats

- Minutes are a **connection-time proxy** (`last_ts − first_ts` per room), not a billed duration, so
  they can differ from the dashboard's agent-session minutes.
- The plan's included minutes and base fee are **not** API-exposed — confirm the current plan +
  renewal day with the account owner. The **LiveKit dashboard is the source of truth** for exact
  usage and spend.
- Recommendation names the next tier but does not compute a cost-optimal switch, because LiveKit's
  per-tier **base fees are unconfirmed** (tracked alongside PROD-3140). It reports the overage $ so
  the decision can be made once the base fees are known.

## Alert model (`--alert`)

WARN/CRITICAL on **both** current and projected % of included minutes (higher severity wins),
defaults `--warn-pct 80` / `--crit-pct 100`. Exit codes match the quota-skill family: `0`=OK,
**`10`=WARN / `20`=CRITICAL**; `1` (query failure) and `2` (config error) stay reserved, so a wrapper
reads `$? -ge 10` as a real alert. `--slack` posts to `SLACK_WEBHOOK_URL` (env only) when not OK.

## Auth

`GCP_KEY` (single-line service-account JSON in `~/.claude/settings.json` env) — the same
`<redacted-email>` SA with `roles/logging.viewer` the GCP log
skills use — or ADC fallback. See `livekit_core.py`.
