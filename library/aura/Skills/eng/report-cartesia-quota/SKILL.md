---
name: report-cartesia-quota
description: >
  Answers "how many Cartesia credits are left, are we about to blow the plan, should we move
  to Scale?" for the Aura workspace. Cartesia's dashboard shows credits remaining but there is
  NO public API for balance, plan or renewal, so this skill DERIVES quota from actual
  consumption over the current billing period against the known plan allotment, clearly
  labelled as an ESTIMATE — the dashboard stays the source of truth. Computes
  derived-remaining, burn rate and projected period-end, tracks the Voice-Agent dollar pool,
  and recommends a plan. Trigger on "cartesia credits left", "cartesia quota", "how many
  cartesia credits remaining", "are we over the cartesia plan", "should we upgrade cartesia to
  scale", "cartesia burn rate", or "cartesia credits running out". An `--alert` mode prints
  OK/WARN/CRITICAL against thresholds and exits non-zero by severity; trigger it on "cartesia
  quota alert" or "warn me when cartesia credits are low". Also triggers on "set up a cartesia
  alert", "check cartesia against a threshold".

metadata:
  version: "3.0.3"
knowledge:
  reads:
    - Guide/Index      # baseline routing aid (universal)
    - Tools/Cartesia   # API base, version header, credit rates, plan tiers
---

# Report Cartesia Quota

Answers the recurring budget question — **how many Cartesia credits are left, are
we about to blow the plan, should we move to Scale?**

Cartesia's dashboard shows **Model Credits Remaining** and **Voice Agent Dollars
Remaining**, but there is **no public API** for balance, plan, or renewal (every
such endpoint 404s — verified). So this skill does not read the balance; it
**derives** quota from actual consumption over the current billing period versus
the configured plan allotment, and labels the result as an **estimate**. The
**Cartesia dashboard is the source of truth** for the exact remaining figure — the
report prints that caveat on every run.

See [`references/methodology.md`](references/methodology.md) for the full
derivation, the no-public-balance-API finding, why the derived number won't match
the dashboard (incl. the observed ~400k-credit gap), the plan-tier table, and the
overage / renewal-day assumptions.

## Step 0 — Check prerequisites

```bash
[ -n "$CARTESIA_ADMIN_API_KEY" ] && echo "OK: CARTESIA_ADMIN_API_KEY set" \
  || echo "MISSING: CARTESIA_ADMIN_API_KEY"
```

If the key is missing, walk the user through adding `CARTESIA_ADMIN_API_KEY` via
the `update-config` skill and restarting Claude Code. No MCP fallback exists — a
missing key is a hard fail. The key is read from the environment only; it is never
printed, passed on argv, or written to a file.

Other prereq: Python 3 with `requests` (`pip3 install -r scripts/requirements.txt`).

**Config caveat.** Plan facts (allotment, prepaid agent $, renewal day) are NOT
API-exposed, so they're flags with defaults: **Scale** plan, **8,000,000**
credits/month (\$299/mo), **overage enabled**, and the renewal day from `Tools/Cartesia` → Plan &
pricing (canonical; the script's default must match it). Override with `--plan`,
`--allotment`, `--renewal-day`, and `--overage-rate-per-million` if the plan
changes. Confirm the current plan / renewal day with the user if there's any doubt.

## Step 1 — Run the estimate

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-cartesia-quota/scripts/report.py
```

Defaults to the current Aura config (Scale, the renewal day in `Tools/Cartesia`). Common overrides:

- `--plan scale` — model the report against a different tier
- `--allotment 1250000` — override included credits directly
- `--renewal-day 1` — anchor the billing period to a different renewal day
- `--overage-rate-per-million 12.0` — if you know the true marginal overage rate
- `--json out.json` — also write the structured payload

The script prints stderr progress lines (`# auth`, `# query`, `# fetched`) and the
rendered Markdown report to stdout.

## Step 1b — Alert mode (proactive threshold check)

Add `--alert` to turn the report into a proactive check: it appends an **Alert**
block (✅ OK / ⚠️ WARN / 🚨 CRITICAL) and **exits with a severity code** so a manual
run — or a future cron / CI wrapper — can gate on it. Run it manually as often as
you like; there is no scheduled job.

Exit codes are chosen so a verdict is never confused with a crash: `0`=OK,
**`10`=WARN**, **`20`=CRITICAL** for the alert verdict, while `1` (Cartesia API
failure) and `2` (missing `CARTESIA_ADMIN_API_KEY` / bad `--renewal-day`) stay
reserved for actual failures. So a wrapper reads `$? -ge 10` as a real alert and a
lower non-zero as a script error to surface differently.

```bash
# default thresholds: warn at 80% of allotment, critical at 100% (overage)
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-cartesia-quota/scripts/report.py --alert

# post to Slack (only when status != OK) via SLACK_WEBHOOK_URL (read from env, never argv)
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-cartesia-quota/scripts/report.py --alert --slack
```

Threshold flags:

- `--warn-pct` (default 80) — WARN when credits used **or projected period-end**
  reaches this % of the allotment. The projection uses the burn rate, so the alert
  fires days before the allotment is actually exhausted, not only after.
- `--crit-pct` (default 100) — CRITICAL at this % (i.e. overage is happening or on
  track to). The higher of the current-vs-projected severities wins.
- `--agent-usd-warn-pct` (default 80) — WARN when the separate Voice-Agent-$ prepaid
  pool is this % consumed.
- `--slack` — POST the alert block to `SLACK_WEBHOOK_URL` when status is not OK
  (no-op if the env var is unset). The webhook is read from the environment only.

The alert block also prints the projected overage $ (when the plan's marginal rate
is known) and the report's own recommendation headline. Like the rest of the
report, thresholds are evaluated on **DERIVED** consumption — the alert flags a
trend to verify on the Cartesia dashboard, it is not a billing statement.

## Step 2 — Read the report to the user

The report leads with a headline (plan, consumed / allotment with % used, over-by
if negative, projected period-end, and the recommendation), then a Credits
breakdown block, a Voice-Agent-$ block, and the source-of-truth caveat. Relay the
headline and recommendation, and always carry the caveat — the derived remaining is
an estimate, not the dashboard figure.

## Step 3 — Act on the recommendation

- **On track within plan** — no action.
- **We'll finish over plan** — overage billing applies; if the overage rate is
  unknown, note that Cartesia doesn't publish it (pass
  `--overage-rate-per-million` when you know it for a dollar estimate).
- **Consider Scale (\$299 / 8M)** — consumption is trending toward or over the next
  tier's allotment; raise upgrading with whoever owns the Cartesia account.
- **Even Scale short — talk to Cartesia / Enterprise** — projected usage exceeds
  the top published tier; start a capacity / Enterprise conversation.

## Step 4 — Cross-check against the dashboard

Because the derived remaining will not match Cartesia's dashboard exactly (rollover
/ period-boundary / grants aren't API-exposed — we've observed the API-derived
consumed figure running ~400k credits above the dashboard), confirm the exact
remaining balance on the Cartesia dashboard before making a plan-change decision.
Use this skill for the trend, burn rate, and projection; use the dashboard for the
precise current balance.
