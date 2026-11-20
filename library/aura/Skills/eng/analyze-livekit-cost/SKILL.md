---
name: analyze-livekit-cost
description: >
  DERIVED spend estimate for the Aurie LiveKit voice agent over a window — total session
  minutes times a configured per-minute rate, with a per-user breakdown and optional
  subscription-tier annotation. There is NO LiveKit billing or usage API, so this cost is
  DERIVED, not billed, and every output says so. This is LiveKit's OWN cost (realtime
  transport plus Cloud Agents hosting) — a SEPARATE line item from TTS and LLM, which the
  elevenlabs and openai skills cover; do not double-count. Minutes are the same log-span proxy
  as `analyze-livekit-usage`, read from the forwarded LiveKit logs, NOT the k8s container
  logs. The default rate is the Scale-plan overage rate, so the derived figure is an upper
  bound; `--rate` overrides. Defaults to yesterday; also today, last-7d, last-30d, custom.
  Trigger on "analyze livekit cost", "livekit spend", "how much is the voice agent costing",
  "voice transport cost", "livekit cost per user", "what does livekit cost us", or "aurie
  voice agent spend".

metadata:
  version: "3.5.1"
knowledge:
  reads:
    - Guide/Index         # baseline routing aid (universal)
    - Tools/LiveKit       # agent id, project, log-forwarding schema, plan/pricing pointer
    - Compliance/Privacy  # UID truncation + no-transcript rules
    - Tools/Slack                    # Slack post/read conventions + footer format
    - Tools/GCP                      # Cloud Logging access (GCP_KEY)
    - Tools/Aura API                 # Aura API access pattern (AURA_API_TOKEN)
---

# Analyze LiveKit Cost

Estimates LiveKit Cloud spend for the Aurie voice agent over a window: **total session minutes × a configured per-minute rate**, with a per-user cost breakdown and an optional subscription-tier annotation.

## Cost here is DERIVED, not billed

**LiveKit exposes no usage or billing API.** The `lk` CLI has no spend command, and actual spend lives only in the LiveKit Cloud dashboard (Project → Billing). This skill therefore **derives** cost from log-span minutes × a rate you supply. Every surface it prints carries a "⚠ derived, not billed" caveat. Treat the number as a sizing/trend estimate — reconcile against the dashboard invoice before quoting it as actual spend. Full reasoning in [`references/methodology.md`](references/methodology.md).

This is the cost sibling of [`eng:analyze-livekit-usage`](../analyze-livekit-usage/SKILL.md) (sessions/minutes/concurrency) and [`eng:analyze-livekit-latency`](../analyze-livekit-latency/SKILL.md) (turn latency). All three share `scripts/livekit_core.py` (the GCP auth ladder + log query + session parsing).

## This is LiveKit's own cost only — don't double-count

The derived figure covers **LiveKit's realtime transport + Cloud Agents hosting** — a separate line item from the other vendors a voice turn touches:

- **TTS** (Cartesia / ElevenLabs) → [`eng:analyze-elevenlabs-costs`](../analyze-elevenlabs-costs/SKILL.md) family
- **LLM** (OpenAI) → [`eng:analyze-openai-costs`](../analyze-openai-costs/SKILL.md) / [`eng:analyze-openai-cost-per-user`](../analyze-openai-cost-per-user/SKILL.md)

For an end-to-end "what does an Aurie voice minute cost", combine all three — counting each provider once. This skill answers only the LiveKit slice.

## Data source — forwarded LiveKit logs, not k8s

The Aurie voice agent runs on **LiveKit Cloud**, not the GKE cluster. Its logs are forwarded to GCP under a dedicated stream:

```
logName   = projects/firebase-auratech16/logs/aurie-voice-agent
resource  = global
```

`analyze-gcp-logs` / `investigate-gcp-logs` filter on `resource.type="k8s_container"`, so they won't see these lines — and this skill won't see theirs. Cleanly separated.

## Step 0 — Check prerequisites

```bash
if [ -n "$GCP_KEY" ]; then
  echo "OK: GCP_KEY set (SA path)"
elif [ -n "$GOOGLE_APPLICATION_CREDENTIALS" ] && [ -f "$GOOGLE_APPLICATION_CREDENTIALS" ]; then
  echo "OK: GOOGLE_APPLICATION_CREDENTIALS set"
else
  echo "No SA env — will try ADC (gcloud auth application-default login). See references/auth in analyze-gcp-logs."
fi
[ -n "$AURA_API_TOKEN" ] && echo "OK: AURA_API_TOKEN set (tier annotation on)" || echo "AURA_API_TOKEN unset — tier column will be omitted (fine)."
python3 -m pip install --user -r ${CLAUDE_PLUGIN_ROOT}/skills/analyze-livekit-cost/scripts/requirements.txt >/dev/null 2>&1 || true
```

`GCP_KEY` (single-line SA JSON in `~/.claude/settings.json` env) is the same key the GCP log skills use — `<redacted-email>` with `roles/logging.viewer`. If none is set the script falls back to ADC. `AURA_API_TOKEN` is optional — it only turns on the per-user subscription-tier column.

## Step 1 — The rate (Scale plan overage rate)

The `--rate` flag is **USD per session-minute**, defaulting to **`\$0.01/min`** — the LiveKit **Scale** plan (\$500/mo) agent-session-minute **overage** rate ([livekit.com/pricing](https://livekit.com/pricing)). Scale includes **50,000 agent minutes/month**; while total monthly minutes stay under that allotment the true marginal cost is **~\$0** (covered by the \$500 flat fee), so the derived figure is an **overage-equivalent upper bound**, not incremental spend.

- The default rate comes from [`scripts/livekit_plan.py`](scripts/livekit_plan.py), the shared plan-of-record table (**Scale**). Pass `--rate <your-rate>` to model a different one.
- **The rate is a floor, not a cost.** `\$0.01/min` is the *overage* rate, and the plans are priced so subscription ÷ allotment equals it at 100% utilization — so the real per-minute cost is always higher. For the effective rate use [`eng:report-livekit-quota`](../report-livekit-quota/SKILL.md).
- Voice-isolation (\$0.0012/min beyond 10k min/mo) and observability (\$0.00003/entry beyond 5M/mo) are separate Scale line items not modelled here — see [`references/methodology.md`](references/methodology.md).

## Step 2 — Resolve the window

Default is **yesterday (UTC)** — stable and comparable day-over-day.

- `--window yesterday` *(default)* — full prior UTC day
- `--window today` — UTC midnight → now (partial)
- `--window last-7d` / `--window last-30d` — rolling windows
- `--since YYYY-MM-DD --until YYYY-MM-DD` — custom UTC range (half-open)

## Step 3 — Run the report

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-livekit-cost/scripts/report.py \
  --window yesterday \
  --rate 0.01 \
  --json /tmp/livekit_cost.json
```

### Common variants

```bash
# Today so far, with a daily budget guard
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-livekit-cost/scripts/report.py --window today --budget 25

# Rolling 30 days at a real plan-derived rate
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-livekit-cost/scripts/report.py --window last-30d --rate 0.006

# Custom range to reconcile against a LiveKit Cloud invoice period
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-livekit-cost/scripts/report.py --since 2026-06-01 --until 2026-07-01 --rate 0.006
```

The script writes `# auth`, `# query`, `# fetched N`, and `# tier` progress lines to stderr and the report to stdout.

## Step 4 — Show the report inline

The script prints a headline (window, sessions, minutes, `$spend` at the rate), a per-user cost table, and the "⚠ derived, not billed" caveat. Show it directly, keeping the caveat. Example:

> _"LiveKit cost (derived) yesterday: 42 sessions, 118 min → ~\$1.18 at \$0.01/min. ⚠ estimate, not billed — reconcile against the LiveKit Cloud dashboard. Excludes TTS + LLM."_

Things worth flagging when commenting:

- **Always restate that it's derived.** Never present it as an invoice figure. If asked for actual spend, point to the LiveKit Cloud dashboard.
- **`--budget` is a daily ceiling** — the comparison normalizes the window's total spend to a per-day rate first (`spend / window_days`), so `--window last-7d`/`last-30d` doesn't stack a week or month of spend against a one-day budget. Exceeded prints `WARN:`; under budget prints `OK:` with headroom — both stated per day.
- **A single UID dominating cost** in prod (not QA) is worth a look — the tier column (if `AURA_API_TOKEN` is set) shows whether it's a paying user. Cross-reference [`eng:analyze-livekit-usage`](../analyze-livekit-usage/SKILL.md).
- **Console/QA rooms** (prefixed `console-`) are LiveKit playground test traffic — they inflate derived minutes but may bill differently; mention, don't treat as production spend.

Then ask where to route it — post a summary to Slack `#eng-budget` via [`shared:post-to-slack`](../../../shared/skills/post-to-slack/SKILL.md) with `--footer eng:analyze-livekit-cost --footer-extra prod` (it stamps the versioned footer; don't hand-type one), save a Markdown summary, or nothing. Never route automatically; wait for the user's pick.

## Notes & edge cases

- **The number is an estimate, not a bill.** No LiveKit usage/billing API exists — see [`references/methodology.md`](references/methodology.md). The LiveKit Cloud dashboard is the only source of truth for actual spend.
- **Minutes are a log-span proxy** — `(last log ts − first log ts)` per room, identical to [`analyze-livekit-usage`](../analyze-livekit-usage/SKILL.md). A session that logs nothing after connect reads as ~0 min.
- **Excludes TTS + LLM + STT.** Those are separate providers owned by the ElevenLabs and OpenAI skills. Combine per-provider once for an end-to-end figure.
- **Privacy.** Aggregates by session/user; markdown truncates UIDs to an 8-char prefix. Full UIDs appear only in `--json` (opt-in). No transcripts or free-text content surfaced. Follows `Compliance/Privacy`.
- **Tier annotation degrades gracefully.** No `AURA_API_TOKEN` → column omitted; a failed lookup → `?`. Never hard-fails the report.
- **`--max-entries` caps the fetch** at 50k lines by default — a very wide window on a busy day can hit it. Narrow the window or raise the cap.
