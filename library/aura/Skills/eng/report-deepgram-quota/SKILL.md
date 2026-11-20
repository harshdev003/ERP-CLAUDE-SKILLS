---
name: report-deepgram-quota
description: >
  Answers "how much Deepgram prepaid balance is left, how long until we run out, do we need to
  top up?" for the Aura STT project. Deepgram is prepaid pay-as-you-go — there is NO monthly
  quota, so the signal is RUNWAY (days of balance left at current burn), not percent used.
  Reads the ACTUAL prepaid balance and audio hours over a recent window, derives spend from
  hours times rate, then burn per day and days-until-empty, with a "top up soon" alarm. With
  `--alert` it prints an OK/WARN/CRITICAL verdict against thresholds on runway days plus an
  absolute balance floor, exits by severity so a wrapper can gate on it, and optionally posts
  to Slack. Trigger on "deepgram balance left", "deepgram prepaid balance", "how long until
  deepgram runs out", "deepgram runway", "do we need to top up deepgram", "deepgram credits
  running out", "alert me if deepgram balance is low", or "deepgram quota alert".

metadata:
  version: "3.0.1"
knowledge:
  reads:
    - Guide/Index    # baseline routing aid (universal)
    - Tools/Deepgram # API base, auth, project scoping, balance/usage endpoints, rates
---

# Report Deepgram Quota

Answers the recurring budget question for Deepgram — **how much prepaid balance is left, how long
until we run out at current burn, do we need to top up?** Deepgram is prepaid PAYG, so there is no
monthly quota; the signal is **runway** = balance ÷ burn. See
[`references/methodology.md`](references/methodology.md) for the balance-runway model and the
derived-vs-actual split.

## Step 0 — Check prerequisites

```bash
{ [ -n "$DEEPGRAM_OWNER_API_KEY" ] || [ -n "$DEEPGRAM_API_KEY" ]; } \
  && echo "OK: Deepgram key present" || echo "MISSING: DEEPGRAM_OWNER_API_KEY / DEEPGRAM_API_KEY"
python3 -m pip install --user -r ${CLAUDE_PLUGIN_ROOT}/skills/report-deepgram-quota/scripts/requirements.txt >/dev/null 2>&1 || true
```

`DEEPGRAM_OWNER_API_KEY` (preferred, management key) or `DEEPGRAM_API_KEY`. The key must be a member
of the STT project (a `usage:read` key on it) — a non-member key exits 2 with guidance (see
`analyze-deepgram-costs` / PROD-3179). Read from env only; never printed or passed on argv.

## Step 1 — Run the report

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-deepgram-quota/scripts/report.py                 # 30-day burn window
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-deepgram-quota/scripts/report.py --days 7
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-deepgram-quota/scripts/report.py --rate-per-hour 0.288  # model monolingual
```

Prints prepaid balance (actual), audio hours, derived spend + burn/day, and **runway** (days-to-empty).

## Step 1b — Alert mode (proactive threshold check)

```bash
# defaults: warn <=30 days runway, crit <=14 days; --min-balance sets an absolute $ floor
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-deepgram-quota/scripts/report.py --alert
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-deepgram-quota/scripts/report.py --alert --min-balance 25 --slack
```

Appends an **✅ OK / ⚠️ WARN / 🚨 CRITICAL** verdict and **exits by severity** — `0`=OK, `10`=WARN,
`20`=CRITICAL — so a manual run (or a future cron/CI wrapper) can gate on `$?`. `1` (API failure) and
`2` (config/scope error) stay reserved, so `$? -ge 10` means a genuine alert. `--slack` posts to
`SLACK_WEBHOOK_URL` (env only) when status != OK.

## Step 2 — Read the result to the user

Lead with the prepaid balance and **runway** ("~N days at current burn"). Note that with the LiveKit
agent's STT traffic still ramping post-go-live, burn is low and runway is large; runway tightens as
real usage flows. Carry the derived caveat (spend/burn = hours × rate; balance is the only actual $).
The Deepgram console is the source of truth for the exact balance.

## Relationship to other skills

- `analyze-deepgram-costs` — usage/spend over a window (same API + project scoping). This is the
  balance/runway/alert companion.
- Sibling quota skills: `report-cartesia-quota`, `report-elevenlabs-quota`, `report-livekit-quota`.
- Composed by the unified `report-quota` (PROD-3182).
- `--rate-per-hour 0.288` models the monolingual switch under evaluation in PROD-3204.
