---
name: analyze-elevenlabs-latency
description: >
  Daily latency report for ElevenLabs Conversational AI (Aurie voice). Walks the conversations
  API for the window, fetches per-conversation detail, and computes P50/P95/P99/Max
  client-side for the three latency fields ElevenLabs exposes per turn — LLM time to first
  sentence (how fast the agent starts thinking out loud), TTS time to first byte (how fast the
  voice starts playing), and ASR trailing latency on user turns (how snappy turn-taking is).
  Defaults to yesterday (full day UTC) so numbers are stable and always inside the 10-day
  retention window. Output is a markdown table plus an optional `--json` payload consumed by
  `eng:analyze-latency`. Trigger on "analyze elevenlabs latency", "11labs latency report",
  "aurie voice latency", "how slow is aurie voice today", "TTS first byte report", "LLM TTFS
  yesterday", "convai latency", "agent response time", "turn taking latency", "latency by
  platform", or a question about how responsive the Aurie voice agent has been.

metadata:
  version: "3.0.3"
knowledge:
  reads:
    - Guide/Index   # baseline routing aid (universal)
    - Tools/ElevenLabs  # 10-day retention, dynamic variables, scope requirements
    - Tools/Slack                    # Slack post/read conventions + footer format
    - Tools/Aura API                 # Aura API access pattern (AURA_API_TOKEN)
---

# Analyze ElevenLabs Latency

Walks `/v1/convai/conversations` for the requested window, fetches per-conversation detail (so we can read the turn-level metric blocks), and renders a per-platform markdown table of P50/P95/P99/Max latencies.

This is the latency sibling of [`eng:analyze-elevenlabs-costs`](../analyze-elevenlabs-costs/SKILL.md) (workspace spend) and [`eng:analyze-elevenlabs-cost-per-user`](../analyze-elevenlabs-cost-per-user/SKILL.md) (per-UID attribution). Same data source as cost-per-user (the `/v1/convai/conversations` list + detail pair) so the throughput and rate-limit profile match; the cost-per-user skill is the closest precedent for skill shape.

## What's surfaced

Each row is one platform (or one SDK source if `--by source`):

| Field | What it measures |
|---|---|
| **LLM TTFS** — `convai_llm_service_ttf_sentence` | Time until the LLM finished generating the first sentence of the agent's reply. Strongest proxy for *"how fast does the agent start speaking?"*. Agent turns only. |
| **TTS TTFB** — `convai_tts_service_ttfb` | Time until the TTS service returned the first audio bytes. Combined with LLM TTFS, this is the user-perceived response delay. Agent turns only. |
| **ASR trailing** — `convai_asr_trailing_service_latency` | Time after the user stopped speaking until ASR finalised the transcript. Affects *"how snappy does turn-taking feel?"*. User turns only. |

The two LLM/TTS metrics combine to drive perceived agent responsiveness. ASR is the inverse: it lives between the user finishing speaking and the agent getting a chance to think.

## ⚠️ 10-day retention

The Aura workspace runs with a **10-day Conversations Retention Period** (ElevenLabs Privacy panel). After 10 days the detail endpoint deletes transcripts + dynamic variables, which is where the per-turn metric blocks live. The default `yesterday` window always lands inside retention; longer windows (`--window last-10d` is the cap) print a stderr warning and silently skip any 404s.

See `Tools/ElevenLabs > Data retention` in AI Knowledge for the canonical write-up.

## Step 0 — Check prerequisites

```bash
if [ -n "$ELEVENLABS_ADMIN_API_KEY" ]; then
  echo "OK: admin key set"
elif [ -n "$ELEVENLABS_API_KEY" ]; then
  echo "WARN: only ELEVENLABS_API_KEY set — fallback path, may under-count if scoped narrow"
else
  echo "MISSING — see Tools/ElevenLabs in AI Knowledge"
fi
```

The key needs two scopes (set at <https://elevenlabs.io/app/developers/api-keys>):

| Scope | Why |
|---|---|
| **Workspace Analytics → Access** | Lists every conversation in the workspace |
| **ElevenAgents → Read** | Detail fetch returns the `transcript[].conversation_turn_metrics` blob — without this the metric block is missing and every turn shows `n=0` |

If missing, walk the user through adding the key via the `update-config` skill, then prompt to restart Claude Code for the env to load.

## Step 1 — Resolve the time window

Default is **yesterday (UTC)** — the closed 00:00 → 24:00 window for the previous day. Stable, comparable day-over-day, always within retention.

- `--window yesterday` *(default)* — full prior UTC day
- `--window today` — UTC midnight → now (partial day)
- `--window last-7d` — rolling 7 days
- `--window last-10d` — full retention window
- `--since YYYY-MM-DD --until YYYY-MM-DD` — custom range (UTC, half-open)

Longer windows scale linearly — yesterday is ~200 conversations / ~45s; `last-7d` is ~1,400 conversations / ~5min.

## Step 2 — Run the report

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-elevenlabs-latency/scripts/report.py \
  --window yesterday \
  --json /tmp/elevenlabs_latency.json
```

### Common variants

```bash
# Today so far (partial UTC day, numbers shift)
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-elevenlabs-latency/scripts/report.py --window today

# Rolling 7 days — slower (~5min) but useful for trend framing
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-elevenlabs-latency/scripts/report.py --window last-7d

# Break down by SDK source rather than platform (swift_sdk vs web_sdk)
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-elevenlabs-latency/scripts/report.py --by source

# Custom range
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-elevenlabs-latency/scripts/report.py \
  --since 2026-05-10 --until 2026-05-13
```

The script writes progress lines to stderr every 50 conversations and the report to stdout.

## Step 3 — Show the report inline

The script already prints a one-line headline + the markdown table. Show it directly. Example:

> _"ElevenLabs latency, yesterday (May 13 UTC): 195 conversations on mobile — LLM TTFS P95 5.45s, TTS TTFB P95 173ms."_

When commenting on the numbers, things worth flagging:

- **LLM TTFS > 5s P95** is the dominant contributor to "the agent feels slow to respond" — almost always the variable to act on.
- **TTS TTFB > 500ms P95** points at ElevenLabs-side issues (model selection, region, capacity), not Aurie code.
- **ASR trailing > 1s** is unusual and worth investigating — could indicate noisy audio or an SDK upgrade introducing buffering.
- **A wide P99–P95 gap** usually means a handful of stuck turns (network drops, model timeouts). Cross-reference the `Max` column — if Max is multi-second, P99 is fat-tailed not regressed.
- **Empty `(unknown)` platform rows** show up when a conversation has no platform dynamic variable — typically agent-internal test calls. Mention but don't act on them.

## Step 4 — Ask where to route it

After the report, ask the user what to do next:

1. **Post a summary to Slack** *(optional)* — same pattern as `eng:analyze-openai-costs` Step 4. Compose the message — the headline + the top platform row — then **invoke [`shared:post-to-slack`](../../../shared/skills/post-to-slack/SKILL.md)** to send it to `#eng-budget` (`C05PC18APLH`, canonical ID in `Tools/Slack`) with `--footer eng:analyze-elevenlabs-latency --footer-extra prod` (it stamps the versioned footer; don't hand-type one). That skill owns the connection (MCP-first, `SLACK_BOT_TOKEN` script fallback) and the unfurl-off default — don't call Slack directly here.
2. **Save a Markdown summary** to a path the user provides.
3. **Nothing, just show it** — stop here.

Never route automatically; always wait for the user's pick.

## Notes & edge cases

- **Percentiles are computed client-side.** No ElevenLabs endpoint returns aggregated latency stats — the only way to get them is to walk the conversations and aggregate locally. (The sibling `analyze-aura-latency` skill, by contrast, gets percentiles aggregated server-side from the Aura `/ai/dialogue/metrics` endpoint.)
- **One detail fetch per conversation.** Throughput is limited by the detail endpoint, not the list. Expect ~3-5 conversations/sec on a clean run, ~30-60s for a typical day. Rate-limit retries (429) use exponential backoff capped at 30s.
- **The metric block lives at `transcript[].conversation_turn_metrics.metrics`.** Each metric is shaped `{"elapsed_time": <seconds>}` and is float-seconds (rendered in ms by this script). If ElevenLabs ever changes the shape, this is the first thing to check.
- **Per-app split is built in, but mostly inert today.** `--by platform` reads `dynamic_variables.platform` (set to `"mobile"` by the swift SDK and presumably `"web"` by the web SDK). As of the initial ship, 30/30 sampled conversations were mobile — so a single-row table is the norm. The breakdown becomes meaningful once web traffic exists. This is the per-platform split that *is not yet possible* in the sibling `analyze-aura-latency` skill (see [ENGBACK-1069](https://linear.app/aura-health/issue/ENGBACK-1069)) — here the Aurie SDK already tags the field, so no upstream work is needed.
- **Privacy.** This skill aggregates by platform, never by user. It does NOT surface UIDs, names, or transcripts — so the redaction rules from `Compliance/Privacy` and the sibling cost-per-user skill don't apply. The `--json` payload also contains no PII (only platform buckets and metric arrays).
- **404 on detail = past retention.** Counted and skipped silently; the count shows up in the stderr summary as `N 404'd (likely past retention)`. Within yesterday this is always 0.
- **Empty-metrics turns are counted but contribute no latency samples.** That's correct — `convai_*` metrics only fire on streaming agent/user turns. Tool-call turns, system events, etc. show up in `transcript[]` with no metric block.
- **Parent skill.** [ENGBACK-1068](https://linear.app/aura-health/issue/ENGBACK-1068) `eng:analyze-latency` consumes this skill's `--json` payload alongside [`eng:analyze-aura-latency`](../analyze-aura-latency/SKILL.md) to produce one combined daily latency report. This skill is callable standalone — the parent is additive, not required.
