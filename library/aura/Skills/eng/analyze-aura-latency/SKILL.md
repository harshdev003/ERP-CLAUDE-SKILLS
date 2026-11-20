---
name: analyze-aura-latency
description: >
  Daily LLM latency report for Aurie's IntelligenceService prompters, read straight from the
  Aura `/ai/dialogue/metrics` endpoint. Renders P50/P95/P99 per prompter (coach, memory,
  quick-processing, guardrails-handler, chat) for three server-aggregated timing fields —
  `ttft` (time to first token: how long until the user sees the first character streaming),
  `ttlt` (time to last token, the full LLM response), and `api.duration` (handler-level
  duration). Defaults to yesterday (full day UTC); also today, last-7d, or a custom
  `--since`/`--until` range. Output is markdown plus an optional `--json` payload consumed by
  `eng:analyze-latency`. Trigger on "analyze aura latency", "analyze braintrust latency",
  "braintrust latency", "prompter latency", "daily aura latency", "p95 latency for aurie
  features", "how slow is coach/memory/quick processing", "time to first token report",
  "latency by prompter", or a question about which Aurie prompter is slowest.

metadata:
  version: "3.1.0"
knowledge:
  reads:
    - Guide/Index   # baseline routing aid (universal)
    - Tools/Slack   # Slack-post footer convention (Step 4 route-to-Slack)
    - Tools/OpenAI  # /ai/dialogue/metrics contract — "Conversations > Metrics" page is OpenAI-adjacent
    - Tools/Aura API                 # Aura API access pattern (AURA_API_TOKEN)
---

# Analyze Aura Latency

Reads LLM latency straight from the Aura `/ai/dialogue/metrics` endpoint, which aggregates per-turn latency **server-side** (out of the Postgres `dialogue.metrics` JSONB column) and returns one row per group with percentiles already computed in milliseconds. The script is a single POST + render — no pagination, no client-side percentile math.

By default the skill produces **a single report for yesterday (UTC)**, grouped by `prompterName` and sorted by TTLT P95 descending — so the slowest prompter floats to the top.

> **Migration note (ENG-677 · ENG-676 · umbrella PROD-2456).** This skill formerly pulled span durations from 14 Braintrust projects via BTQL and computed percentiles client-side. It now reads the Aura `/ai/dialogue/metrics` endpoint, which aggregates server-side per prompter. Braintrust is no longer involved. The old per-Braintrust-project grouping (Conversation, Memory, Quick Processing, …) is replaced by the finer per-prompterName grouping. The back-compat trigger phrases ("braintrust latency") are kept so discovery still works.

## What's surfaced

| Field | What it measures |
|---|---|
| **TTFT** — `ttft` | Time to first token. "How long until the user sees the first character streaming?" (≈ the old Braintrust TTFT.) |
| **TTLT** — `ttlt` | Time to last token — the full LLM response finished generating. The stand-in for the old end-to-end / E2E root-span latency. |
| **API dur** — `api.duration` | Handler-level duration inside IntelligenceService — the outermost view of how long a turn took. New; useful, included. |

All three come back in **milliseconds** already — the script does no scaling.

## Step 0 — Check prerequisites

```bash
if [ -n "$AURA_API_TOKEN" ]; then
  echo "OK"
else
  echo "MISSING"
fi
```

If missing, walk the user through adding `AURA_API_TOKEN` via the `update-config` skill — it's a privileged IntelligenceService bearer token (ask `#ai-eng`). Restart Claude Code after adding so the new env loads. A missing token is a hard fail, not a degrade — there is no MCP fallback for this endpoint.

## Step 1 — Resolve the time window

Default is **yesterday (UTC)**, i.e. the closed 00:00 → 24:00 window for the previous day. Use this as a daily reporting baseline so numbers are stable and comparable day-over-day. The window is **half-open `[since, until)`** and capped at **90 days** server-side.

Honour the user's window if they ask for one:

- `--window yesterday` *(default)* — full prior UTC day
- `--window today` — UTC midnight → now (partial day, numbers shift during the day)
- `--window last-7d` — rolling 7 days ending now
- `--since YYYY-MM-DDTHH:MM:SSZ --until YYYY-MM-DDTHH:MM:SSZ` — custom range

If the user names a single calendar day, translate it to `--since <date>T00:00:00Z --until <date+1>T00:00:00Z`. A window wider than 90 days returns a `422 DIALOGUE_METRICS_WINDOW_TOO_LARGE` — the script surfaces a clear message; narrow the range and retry.

## Step 2 — Run the report

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-aura-latency/scripts/report.py \
  --window yesterday \
  --json /tmp/aura_latency.json
```

### Common variants

```bash
# Last 7 days (rolling)
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-aura-latency/scripts/report.py \
  --window last-7d

# Today so far (partial UTC day, numbers shift)
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-aura-latency/scripts/report.py \
  --window today

# Custom range
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-aura-latency/scripts/report.py \
  --since 2026-05-01T00:00:00Z --until 2026-05-08T00:00:00Z

# Dev environment (override the base URL; default is prod)
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-aura-latency/scripts/report.py \
  --base-url https://services.dev.aurahealth.io
```

The script writes a one-line progress note to stderr and the report to stdout.

## Step 3 — Show the report inline

The script's stdout already contains a one-line headline + the markdown table (and a small per-model table when more than one model is in play). Show it directly. Example headline:

> _"Aura latency, yesterday (UTC): guardrails-handler-prompter is the slowest prompter — TTLT P95 6.36s and guardrails-handler-prompter has the slowest TTFT P95 (4.35s)."_

When commenting on the table, the things worth flagging:

- **TTLT P99 spikes** — a P99 more than 3-4× the P95 usually points at a handful of hung/slow turns, not a real regression. Cross-check the `max_ms` in the `--json` payload — if Max is huge (>60s, e.g. coach-prompter routinely shows a ~300s tail) and P99 is too, it's tail noise; if P99 is high but Max is moderate, it's a real fat tail.
- **TTFT > 2s P95** — streaming UX feels broken. Worth investigating if it's sustained across multiple days.
- **TTFT vs TTLT gap** — a large gap (TTLT P95 8s, TTFT P95 1s) means the response starts streaming fast but the full reply is long; a small gap means the call is genuinely slow to start.
- **The `(none)` prompter row** — turns with a null `prompterName` (un-prompted handler calls). It typically carries only `api.duration` (no streaming tokens, so TTFT/TTLT are blank) and sorts to the bottom. Mention it only if relevant.

## Step 4 — Ask where to route it

After the report, ask the user what to do next:

1. **Post a summary to Slack** *(optional)* — same pattern as `eng:analyze-openai-costs` Step 4. Compose the message — the top-3 slowest prompters from the table + the headline. Then **invoke [`shared:post-to-slack`](../../../shared/skills/post-to-slack/SKILL.md)** to send it to `#eng-budget` (`C05PC18APLH`, canonical ID in `Tools/Slack`) with `--footer eng:analyze-aura-latency --footer-extra prod` — the helper stamps the versioned footer (`_Generated by `eng:analyze-aura-latency:<version>` · <YYYY-MM-DD> · prod_`) from `metadata.version` and owns the connection (MCP-first, `SLACK_BOT_TOKEN` script fallback) and the unfurl-off default. Don't hand-type the footer in the body or call Slack directly.
2. **Save a Markdown summary** to a path the user provides.
3. **Nothing, just show it** — stop here.

Never route automatically; always wait for the user's pick.

## Notes & edge cases

- **Percentiles are aggregated server-side.** The endpoint computes P50/P95/P99/Max per group inside IntelligenceService and returns them in milliseconds. There's no pagination, no client-side math, and no `*1000` scaling — the script is a single POST per groupBy + render. (This replaced the old BTQL approach, where BTQL exposed only `COUNT/AVG/MIN/MAX` and the script had to paginate raw durations across 14 projects and interpolate percentiles in Python.)
- **Two POSTs, not one.** The script issues one POST grouped by `prompterName` (primary) and one grouped by `model` (secondary). The per-model table only renders when more than one distinct model is present. Both are cheap and run on a daily window in well under a second.
- **Window is half-open and capped at 90 days.** `[since, until)`. A wider range returns `422 DIALOGUE_METRICS_WINDOW_TOO_LARGE` (the response carries `maxWindowDays`); the script surfaces the cap and exits cleanly rather than crashing.
- **`(none)` / null group.** The endpoint returns a row with `prompterName: null` (and `model: null`) for handler traffic that isn't routed through a named prompter. These rows usually report only `api.duration` — TTFT/TTLT come back null and render as `—`. The row is kept (rendered as `(none)`) and naturally sorts to the bottom.
- **No per-app split today.** The endpoint groups by `prompterName`, `model`, `userId`, or `role` — there is **no app dimension** (Aurie iOS vs Aurie Web is indistinguishable). Per-platform splits remain blocked on [ENGBACK-1069](https://linear.app/aura-health/issue/ENGBACK-1069); the old blocker was "no `metadata.app` tag on Braintrust spans", now it's "the metrics endpoint has no app group dimension yet". When an app dimension lands, add a `--by app` option.
- **TTFT coverage is uneven.** Streaming prompters (coach-prompter, quick-processing-prompter) report TTFT/TTLT; handler-only traffic reports `api.duration` only. A blank TTFT/TTLT for such a group is correct, not a bug.
- **Outlier Max values.** Some prompters report Max in the hundreds of seconds (coach-prompter regularly shows a ~300s tail) — typically hung/slow turns. P99 is the right number to cite for "tail latency"; treat `max_ms` (in the `--json` payload) as a noise diagnostic.
- **The endpoint contract.** Full request/response shape, allowed `groupBy` (prompterName, model, userId, role) and metric list (ttft, ttlt, api.duration, stt.duration, tts.duration, tts.ttfb, eou.latency, vad.latency, + token paths) lives on the AI Knowledge "Conversations > Metrics" page (<https://app.notion.com/p/Metrics-37675e23af20813d8975d1d09b01e8e7>), adjacent to `Tools/OpenAI`.
- **Parent skill.** [ENGBACK-1068](https://linear.app/aura-health/issue/ENGBACK-1068) `eng:analyze-latency` consumes this skill's `--json` payload alongside `eng:analyze-elevenlabs-latency` to produce one combined daily latency report. This skill is callable standalone — the parent is additive, not required.
