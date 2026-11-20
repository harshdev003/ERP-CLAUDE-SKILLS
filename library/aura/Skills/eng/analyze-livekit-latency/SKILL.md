---
name: analyze-livekit-latency
description: >
  Turn-latency deep-dive for the Aurie LiveKit voice agent over a window —
  P50/P90/P95/P99/Max/mean for each per-turn latency metric the agent emits. Reads the
  forwarded LiveKit logs, NOT the k8s container logs that `analyze-gcp-logs` and
  `investigate-gcp-logs` read. The agent emits five per-turn metrics: end-to-end felt latency
  (user finished speaking until the agent begins responding — the headline number),
  end-of-utterance delay, transcription delay (STT lag), LLM time-to-first-token, and TTS
  time-to-first-byte. Percentiles are computed client-side over per-turn samples; a secondary
  per-session view surfaces the agent's own shutdown-logged averages. Defaults to yesterday so
  numbers are stable; also today, last-7d, last-30d, custom. Sibling of
  `analyze-livekit-usage` and `analyze-livekit-cost`. Trigger on "analyze livekit latency",
  "aurie voice turn latency", "eou delay", "llm ttft", "how responsive is the voice agent",
  "livekit response time", "voice agent latency", or "how fast does aurie start replying".

metadata:
  version: "3.4.1"
knowledge:
  reads:
    - Guide/Index   # baseline routing aid (universal)
    - Tools/LiveKit # agent id, project, log-forwarding schema, latency metric names
    - Tools/Slack                    # Slack post/read conventions + footer format
    - Tools/GCP                      # Cloud Logging access (GCP_KEY)
---

# Analyze LiveKit Latency

Queries the forwarded LiveKit agent logs in GCP Cloud Logging and renders a per-turn latency table — P50/P90/P95/P99/Max/mean for each metric the agent emits — for the requested window.

This is the latency sibling of [`eng:analyze-livekit-usage`](../analyze-livekit-usage/SKILL.md) (sessions / minutes / concurrency) and [`eng:analyze-livekit-cost`](../analyze-livekit-cost/SKILL.md) (derived spend). All three share `scripts/livekit_core.py` (the GCP auth ladder + log query + latency parsing). It is the LiveKit counterpart to the ElevenLabs [`analyze-elevenlabs-latency`](../analyze-elevenlabs-latency/SKILL.md) skill.

## Data source — forwarded LiveKit logs, not k8s

The Aurie voice agent runs on **LiveKit Cloud**, not the GKE cluster. Its logs are forwarded to GCP under a dedicated stream:

```
logName   = projects/firebase-auratech16/logs/aurie-voice-agent
resource  = global
```

The `analyze-gcp-logs` / `investigate-gcp-logs` skills filter on `resource.type="k8s_container"` in the `aura-services` namespace, so they will **not** see these lines — and this skill won't see theirs. Cleanly separated.

## The metrics

The agent logs one `Turn latency` line per metric per turn, carrying `jsonPayload.metric` + `jsonPayload.value_ms`. It emits five:

| Metric | What it measures | How to read it |
|---|---|---|
| **`e2e_latency_ms`** — end-to-end felt latency | Time from the user finishing speaking until the agent begins responding | The single number a user actually experiences ("I stopped → Aurie started"). The headline latency figure; the other four are its components. Report this first. |
| **`eou_delay_ms`** — end-of-utterance delay | Time from the user stopping speaking until the agent decides the turn ended | Values clustered near **~3000ms are the configured max-silence fallback** firing — the agent waited out the silence timeout because it couldn't confidently detect end-of-turn. That's a turn-detection cost, **not** model slowness. A low, tight `eou_delay_ms` means crisp turn-taking. |
| **`transcription_delay_ms`** — STT lag | Time to obtain the transcript after the user stops speaking | The speech-to-text slice. A spike points at Deepgram / STT, not turn detection or the LLM. |
| **`llm_ttft_ms`** — LLM time-to-first-token | How fast the agent starts generating its reply once the turn is handed to the LLM | This is the "does the agent feel slow to start speaking?" signal. **P95 > 2s is worth flagging** as the agent being sluggish to begin replying. |
| **`tts_ttfb_ms`** — TTS time-to-first-byte | How fast the first audio chunk comes back once text is flowing to the TTS | Usually small (~100-200ms) with Cartesia `sonic-3.5`. A spike here points at the TTS provider, not turn detection or the LLM. |

`e2e_latency_ms` is NOT the sum of the other four — it's LiveKit's own measured end-to-end figure. Treat the components as a breakdown of where the felt latency goes, not addends.

## Step 0 — Check prerequisites

```bash
if [ -n "$GCP_KEY" ]; then
  echo "OK: GCP_KEY set (SA path)"
elif [ -n "$GOOGLE_APPLICATION_CREDENTIALS" ] && [ -f "$GOOGLE_APPLICATION_CREDENTIALS" ]; then
  echo "OK: GOOGLE_APPLICATION_CREDENTIALS set"
else
  echo "No SA env — will try ADC (gcloud auth application-default login). See references/auth in analyze-gcp-logs."
fi
python3 -m pip install --user -r ${CLAUDE_PLUGIN_ROOT}/skills/analyze-livekit-latency/scripts/requirements.txt >/dev/null 2>&1 || true
```

`GCP_KEY` (single-line SA JSON in `~/.claude/settings.json` env) is the same key the GCP log skills use — the `<redacted-email>` SA with `roles/logging.viewer`. If none is set the script falls back to ADC.

## Step 1 — Resolve the window

Default is **yesterday (UTC)** — stable and comparable day-over-day.

- `--window yesterday` *(default)* — full prior UTC day
- `--window today` — UTC midnight → now (partial)
- `--window last-7d` / `--window last-30d` — rolling windows
- `--since YYYY-MM-DD --until YYYY-MM-DD` — custom UTC range (half-open)

## Step 2 — Run the report

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-livekit-latency/scripts/report.py \
  --window yesterday \
  --json /tmp/livekit_latency.json
```

### Common variants

```bash
# Today so far
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-livekit-latency/scripts/report.py --window today

# Rolling 7 days
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-livekit-latency/scripts/report.py --window last-7d

# Custom range
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-livekit-latency/scripts/report.py --since 2026-06-25 --until 2026-06-28
```

The script writes `# auth`, `# query`, and `# fetched N` progress lines to stderr and the report to stdout.

## Step 3 — Show the report inline

The script prints a headline + a bullet summary + a per-metric percentile table (and, when present, a secondary per-session-averages table from the agent's own shutdown summary). Show it directly. Example:

> _"LiveKit turn latency, today (Jul 1 UTC): 20 turns — EOU delay P95 3,003ms, LLM TTFT P95 2,493ms."_

Interpretation guidance when commenting on the numbers:

- **`eou_delay_ms` pinned at ~3,000ms** (P50 through Max all hugging 3s) means turns are routinely hitting the **max-silence fallback** — the agent isn't confidently detecting end-of-turn and is waiting out the timeout. This adds ~3s of dead air per turn. Worth flagging as a turn-detection tuning opportunity, not an LLM problem.
- **`llm_ttft_ms` P95 > 2s** = the agent is slow to *start* replying. This is the dominant "feels laggy" contributor once EOU is under control. Cross-reference the OpenAI-side latency skills if the LLM itself is the suspect.
- **A wide P99–P95 gap** with a high `max` usually means a handful of stuck turns (network drops, model timeouts) rather than a broad regression — check `max` before concluding the metric regressed.
- **Low turn count** (single-digit `n`) makes percentiles noisy — widen the window before drawing conclusions.
- The **per-session averages** table is the agent's own arithmetic (from its `Turn-latency summary` shutdown line); it's a cheap sanity cross-check against the client-side percentiles, not a substitute for them.

## Step 4 — Ask where to route it

After the report, ask what to do next:

1. **Post a summary to Slack** *(optional)* — compose the headline + the two P95 figures, then invoke [`shared:post-to-slack`](../../../shared/skills/post-to-slack/SKILL.md) to send it to `#eng-budget` (canonical ID in `Tools/Slack`) with `--footer eng:analyze-livekit-latency --footer-extra prod` (it stamps the versioned footer; don't hand-type one). That skill owns the connection and unfurl-off default — don't call Slack directly.
2. **Save a Markdown summary** to a path the user provides.
3. **Nothing, just show it** — stop here.

Never route automatically; always wait for the user's pick.

## Notes & edge cases

- **Percentiles are computed client-side.** LiveKit exposes no latency API and the forwarded logs carry only raw per-turn `value_ms` samples — the only way to get P50/P95/P99 is to walk the turn lines and aggregate locally (nearest-rank). Same posture as the ElevenLabs latency sibling.
- **Five metrics.** `e2e_latency_ms`, `eou_delay_ms`, `transcription_delay_ms`, `llm_ttft_ms`, and `tts_ttfb_ms` are the set the agent emits (`livekit_core.LATENCY_METRICS`). When the agent starts emitting more, extend the tuple and the percentile table follows automatically — but `livekit_core.py` is a **shared file with six copies**, so extend the canonical (`analyze-livekit-usage`) and propagate to every copy in the same PR, never this skill's copy alone. Narrowing the tuple silently drops rows from the table below — no error, no empty section — so `tests/test_livekit_latency_metrics.py` pins the set by name.
- **EOU ~3s is a fallback, not a regression.** The configured max-silence timeout is ~3,000ms; turns that can't be confidently ended land on it. A day where `eou_delay_ms` is pinned at 3s across all percentiles is telling you turn detection fired the fallback, not that latency "got worse."
- **Privacy.** This skill aggregates by metric, never by user. It surfaces no UIDs, names, or transcripts — the redaction rules from `Compliance/Privacy` don't apply here, and the `--json` payload contains only metric buckets and percentile arrays.
- **`--max-entries` caps the fetch** at 50k lines by default — a very wide window on a busy day can hit it (a stderr note prints the count). Narrow the window or raise the cap.
- **A window with no turns prints a note, not a table.** If unexpected, widen `--window` or confirm the agent is live and emitting metrics (`lk agent status`).
