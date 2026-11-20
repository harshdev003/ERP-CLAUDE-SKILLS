---
name: report-elevenlabs-quota
description: >
  Answers "how many ElevenLabs credits are left, when does overage start, how much overage
  will we pay this period?" for the Aura workspace. Unlike `report-cartesia-quota`, ElevenLabs
  exposes an ACTUAL quota. Going past the plan limit is normal: Aura pays on-demand overage
  every month and voice keeps working. Surfaces used vs the plan's base grant, percent used,
  remaining, reset date, burn rate, days until on-demand overage starts, overage $ so far and a
  derived projected overage $. With `--alert` it warns each time overage reaches another 500-dollar step
  (`--overage-step-usd`) and exits by severity so a wrapper can gate on it. Trigger on
  "elevenlabs credits left", "elevenlabs quota", "how many elevenlabs credits remaining",
  "how long until elevenlabs runs out", "elevenlabs runway", "elevenlabs overage",
  "elevenlabs burn rate", "are we over the elevenlabs plan", or "elevenlabs quota alert".
  Also triggers on "alert me if elevenlabs is about to run out".

metadata:
  version: "3.2.0"
knowledge:
  reads:
    - Guide/Index       # baseline routing aid (universal)
    - Tools/ElevenLabs  # API base, auth (ELEVENLABS_API_KEY), subscription/quota fields
---

# Report ElevenLabs Quota

Answers the recurring budget question for ElevenLabs — **how many credits are left, when does
on-demand overage start, and how much overage will we pay this period?** — from the workspace's live
subscription.

**Going past the plan limit is normal on this plan.** Aura pays usage-based on-demand overage every
month on purpose, and voice keeps working past the limit (Jerome, 2026-10-30). So the limit is not a
run-out date, and overage is not an emergency; the alert watches the overage dollars.

ElevenLabs, unlike Cartesia, gives used / limit / reset **directly** (`GET /v1/user/subscription`).
**Overage is not in that response, though.** ElevenLabs bills on-demand overage by raising
`character_limit` in blocks as usage reaches it, so `used` never passes the live limit and
`current_overage` stays at zero dollars (ENG-1726). The script measures overage against the plan's **base
grant**: `--base-credits`, or by default the plan price (`next_invoice.subtotal_cents`) divided by the
period's spend per credit from the Usage API. Base grant and overage $ are derived; burn rate, days
until overage and projected overage $ are extrapolations. See
[`references/methodology.md`](references/methodology.md) for the field map and the math.

## Step 0 — Check prerequisites

```bash
{ [ -n "$ELEVENLABS_ADMIN_API_KEY" ] || [ -n "$ELEVENLABS_API_KEY" ]; } \
  && echo "OK: ElevenLabs key present" || echo "MISSING: ELEVENLABS_ADMIN_API_KEY / ELEVENLABS_API_KEY"
```

`ELEVENLABS_ADMIN_API_KEY` (preferred, workspace-scoped) or `ELEVENLABS_API_KEY` (fallback). If
neither is set, walk the user through adding one via the `update-config` skill and restarting Claude
Code. The key is read from the environment only — never printed, logged, or passed on argv.

Other prereq: Python 3 with `requests` (`pip3 install -r scripts/requirements.txt`).

## Step 1 — Run the report

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-elevenlabs-quota/scripts/report.py
```

Prints the quota view (used / base grant / %, the live limit and its extensions, remaining, reset
date, burn rate, days until on-demand overage starts, overage $ so far, `current_overage` as a
cross-check, and the derived **projected overage $** at reset). `--base-credits N` sets the base
grant when the plan changes or the derived one looks wrong.
`--json out.json` also writes the structured payload.

## Step 1b — Alert mode (proactive threshold check)

```bash
# default: WARN once overage reaches a 500-dollar step; % and days thresholds are opt-in
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-elevenlabs-quota/scripts/report.py --alert

# post to Slack (only when not OK) via SLACK_WEBHOOK_URL (read from env, never argv)
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-elevenlabs-quota/scripts/report.py --alert --slack
```

Appends an **✅ OK / ⚠️ WARN / 🚨 CRITICAL** verdict and **exits by severity** — `0`=OK, `10`=WARN,
`20`=CRITICAL — so a manual run (or a future cron/CI wrapper) can gate on `$?`. `1` (API failure) and
`2` (missing key) stay reserved for real failures, so `$? -ge 10` means a genuine alert.

- `--overage-step-usd` (default `500`): WARN when overage has reached at least one step. The
  payload's `overage_step` is the step reached, so a caller alerts once per new step rather than on
  every run.
- Opt-in only (no default): `--warn-pct` / `--crit-pct` (% of base grant) and `--warn-runway-days` /
  `--crit-runway-days` (days until on-demand overage starts).
- `--slack` posts a non-OK verdict to `SLACK_WEBHOOK_URL`.

## Step 2 — Read the result to the user

Lead with the overage $ so far and the **projected overage $** at reset (say both are derived, and
from which rate), then % of base grant and the days until on-demand overage starts. Never call the limit a
run-out: overage is normal and paid on demand. Carry the migration caveat: ElevenLabs ConvAI is being
migrated to LiveKit, so burn will fall and the projection is a **current-pace worst case**. For exact
figures, the ElevenLabs dashboard is authoritative; this skill is the burn/overage/alert view on top
of the same API numbers.

## Relationship to other skills

- `analyze-elevenlabs-costs` — spend/credits over a window, broken down by product/model/voice. This
  skill is the quota/overage/alert companion (same `/v1/user/subscription` source for the plan block).
- Sibling quota skills: `report-cartesia-quota`, `report-livekit-quota`, `report-deepgram-quota`.
- Composed by the unified `report-quota` (PROD-3182).
