---
name: report-livekit-quota
description: >
  Answers "how many LiveKit minutes this period, are we about to blow the plan's allotments,
  should we move up a tier?" for the Aurie voice agent. LiveKit exposes NO billing API, so
  this is DERIVED from the daily cost store (logs as fallback). Reports minutes used vs included, burn/day,
  projected minutes and overage $, the EFFECTIVE $/min (the published figure is only the
  floor), and a table of EVERY metered pool:
  agent-session and WebRTC minutes, data transfer, Krisp minutes, observability, Inference
  credits. Nothing omitted: an unmeasurable pool is marked
  UNMEASURED, and a partly-measured month reports a PARTIAL lower bound, never a figure.
  `--alert` prints OK/WARN/CRITICAL, states the scope it did NOT check, and exits by
  severity. Trigger on "livekit minutes used", "livekit quota", "how many agent-session minutes
  left", "are we over the livekit plan", "should we upgrade livekit", "livekit burn rate",
  "livekit inference credits", "are we near the krisp limit", "livekit quota alert".

metadata:
  version: "3.7.1"
knowledge:
  reads:
    - Guide/Index    # baseline routing aid (universal)
    - Tools/LiveKit  # plan of record + pricing (canonical), agent id, forwarded-log schema
    - Tools/GCP                      # Cloud Logging access (GCP_KEY)
---

# Report LiveKit Quota

Answers the recurring budget question for LiveKit Cloud — **how many agent-session minutes have we
used this billing period, are we about to blow the plan's included minutes, should we move up a
tier?**

LiveKit exposes **no** billing/usage API, so (like Cartesia) this is a **derived** estimate: consumed
minutes vs the plan's included minutes (a config flag).

**Agent-session minutes come from the daily cost store.** A month of the forwarded stream is far
past any workable `--max-entries`, so a windowed walk can only ever read its tail;
`eng:store-aurie-user-daily-cost` aggregates the same quantity per day and reconciles with the
LiveKit dashboard to ~5%. The store writes complete days only, so the report states the date its
minutes run **through** and computes burn over the days actually covered.

The forwarded logs are the fallback when BigQuery is unreachable. A fallback read that hits the cap
reports its period total as a lower bound and divides burn by the span the retained entries cover,
never by the full period. See
[`references/methodology.md`](references/methodology.md) for the derivation, the minute proxy, and why
the included minutes / base fees are flags.

## The Inference credit pool is read, not derived

`inference_credits_usd` is the one pool whose usage appears in **neither** the forwarded agent
logs nor any LiveKit API. It is readable only because `eng:store-aurie-user-daily-cost` stores
the per-day drawdown (`deepgram_inference_drawdown`), so this skill reads
`aura-main.aurie_cost.aurie_per_user_daily_cost` via the bundled `bq.py`.

The pool's `derivable` flag stays **`False`**: what changed is the source, not the logs.

**A month that spans days the store could not measure reports a bound, not a value.** The
voice agent only logs STT duration from **v1.11.0**; days before that recorded no measurement
at all, and summing across them yields a floor. Those days are identified from stored columns
alone — LiveKit minutes present, `voice_deepgram` zero, drawdown zero-or-null — which is
exactly the condition the store tags `deepgram_transport_unknown`. The row then renders as
`≥ $X (N of M days unmeasured)` with status `PARTIAL`.

Reporting that bound as the figure would be a confident understatement of how full the block
is, which defeats the only question a quota report exists to answer — worse than the honest
`UNMEASURED` it replaced. An unreachable cost store degrades to `UNMEASURED` for the same
reason: a `0` there reads as "block untouched".

## Step 0 — Check prerequisites

```bash
if [ -n "$GCP_KEY" ]; then echo "OK: GCP_KEY set"; \
elif [ -n "$GOOGLE_APPLICATION_CREDENTIALS" ] && [ -f "$GOOGLE_APPLICATION_CREDENTIALS" ]; then echo "OK: ADC file"; \
else echo "No SA env — will try ADC (gcloud auth application-default login)"; fi
python3 -m pip install --user -r ${CLAUDE_PLUGIN_ROOT}/skills/report-livekit-quota/scripts/requirements.txt >/dev/null 2>&1 || true
```

`GCP_KEY` (single-line SA JSON in `~/.claude/settings.json` env) is the same key the other GCP log
skills use; falls back to ADC. **Plan facts** come from [`scripts/livekit_plan.py`](scripts/livekit_plan.py)
— the plan of record (**Scale**, renewal day 1, verified against the LiveKit Cloud dashboard), every
pool's allotment, and the Inference rate card. Override per run with `--plan`,
`--included-minutes`, `--renewal-day`, `--overage-rate-per-min`. Canonical prose home for plan +
pricing is `Tools/LiveKit`; the module exists because a script needs real values.

## Step 1 — Run the report

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-livekit-quota/scripts/report.py            # defaults: Scale, renewal day 1
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-livekit-quota/scripts/report.py --plan scale
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-livekit-quota/scripts/report.py --json out.json
```

Prints minutes used / included, % used, burn/day, projected period-end minutes, projected overage $,
the effective $/min, the all-pools table, and a recommendation.

## Read the output in this order

**The verdict covers agent-session minutes only, and that is the pool furthest from binding.** In
August 2026 session minutes sat at 11,381 of 50,000 (23%) while Krisp background minutes hit
10,000 of 10,000 and went 955 over — \$1.14, the only variable dollar we have ever paid LiveKit. So:

1. **The all-pools table first.** Only agent-session minutes are derivable from the forwarded
   logs; Inference credits is read from the **cost store**, and the remaining five print as
   `not derivable from logs` with their allotment. That is deliberate — a pool that is simply
   absent reads exactly like a healthy one. Check those five on the dashboard.
2. **Then the effective $/min.** `\$0.010/min` is the OVERAGE rate and the *floor*: the plans are
   priced so subscription ÷ allotment equals it at 100% utilization, so the real cost of a minute is
   always higher and falls as volume grows. Quote the effective rate, never the overage rate.
3. **Then the minutes verdict**, which is what `--alert` exits on.

## Step 1b — Alert mode (proactive threshold check)

```bash
# defaults: warn at 80% of included, crit at 100% (overage) — on current AND projected minutes
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-livekit-quota/scripts/report.py --alert
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-livekit-quota/scripts/report.py --alert --slack
```

The verdict covers every pool it can actually measure — agent-session minutes **and**
Inference credits once the store has a drawdown — and its `scope` names them. A measured pool
that stayed out of the check would let `--alert` print OK while that pool was OVER, which is
the unqualified-OK shape the Krisp overage came through. A `PARTIAL` pool is called out as a
lower bound even when the verdict is OK: a floor can only understate.

Appends an **✅ OK / ⚠️ WARN / 🚨 CRITICAL** verdict and **exits by severity** — `0`=OK, `10`=WARN,
`20`=CRITICAL — so a manual run (or a future cron/CI wrapper) can gate on `$?`. `1` (query failure)
and `2` (config error) stay reserved, so `$? -ge 10` means a genuine alert. `--slack` posts to
`SLACK_WEBHOOK_URL` (env only) when status != OK.

## Step 2 — Read the result to the user

Lead with minutes used / included and % used, then the projected period-end + overage $ if over.
Carry the caveat: minutes are a derived connection-time proxy from the logs, the included minutes are
a config flag, and the **LiveKit dashboard is the source of truth** for exact usage and spend. Note
that early after go-live the log-derived minutes are dominated by QA traffic until real usage flows
(cross-check with `analyze-livekit-usage`).

## Relationship to other skills

- `analyze-livekit-usage` — sessions / concurrency / minutes over a window (shares `livekit_core`).
- `analyze-livekit-cost` — derived spend (minutes × rate).
- Sibling quota skills: `report-cartesia-quota`, `report-elevenlabs-quota`, `report-deepgram-quota`.
- Composed by the unified `report-quota` (PROD-3182).
