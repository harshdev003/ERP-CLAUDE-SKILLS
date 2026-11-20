---
name: analyze-latency
description: >
  Daily latency orchestrator across Aurie's two AI latency sources — Aura LLM latency per
  prompter (the chat/LLM backend) and ElevenLabs (the Conversational AI voice agent). Composes
  `eng:analyze-aura-latency` and `eng:analyze-elevenlabs-latency` in parallel for the same
  window, then synthesises one combined report with a single headline connecting both sources.
  Defaults to yesterday (full day UTC) so numbers are stable day-over-day; also today,
  last-7d, or a custom range that maps cleanly onto each child. Output is a unified markdown
  report — synthesised headline, then an Aura section and an ElevenLabs section. Optional
  Slack route to #eng-budget, gated behind explicit approval. Trigger on "daily latency
  report", "analyze latency", "latency report yesterday", "how slow is aurie today", "p95
  latency report", "latency across aura and elevenlabs", "voice + chat latency", "combined
  latency view", or "aurie health check". Do NOT use for per-feature drill-down — invoke the
  children directly.

metadata:
  version: "3.0.4"
knowledge:
  reads:
    - Guide/Index       # baseline routing aid (universal)
    - Products/Aurie  # brand voice for the synthesised headline
    - Tools/Slack                    # Slack post/read conventions + footer format
    - Tools/ElevenLabs                   # ElevenLabs API access (ELEVENLABS_API_KEY)
    - Tools/Aura API                 # Aura API access pattern (AURA_API_TOKEN)
---

# Analyze Latency

Front door for daily latency reporting. Composes the two child skills that cover Aurie's two AI latency surfaces, runs them in parallel for the same window, and synthesises one combined report.

| Source | Child skill | What it answers |
|---|---|---|
| **Aura** (chat / LLM) | [`eng:analyze-aura-latency`](../analyze-aura-latency/SKILL.md) | Which Aurie prompter is slow (coach-prompter, memory-prompter, quick-processing-prompter, etc.)? Time-to-first-token, time-to-last-token, and handler-level API duration. |
| **ElevenLabs** (voice) | [`eng:analyze-elevenlabs-latency`](../analyze-elevenlabs-latency/SKILL.md) | How responsive is the voice agent? LLM time-to-first-sentence, TTS time-to-first-byte, ASR trailing. |

The two sources are complementary:

- The Aura per-prompter TTLT answers "how long until the agent finished thinking *and* writing a reply" — the full LLM response.
- The ElevenLabs LLM TTFS answers "how long until the agent had something to start *saying*" — the user-perceived "thinking" delay before voice starts. Compare it against the Aura side's TTFT (time to first token).
- TTS TTFB and ASR trailing are voice-specific and have no Aura analogue.

Run this orchestrator when the user wants a one-stop daily picture. Run a child directly when the user has already named the surface.

## Step 0 — Check prerequisites

Both children gate on their own env vars. This skill doesn't pre-check — invoke the children and let them surface their own Step 0 if a key is missing.

- Aura child: `AURA_API_TOKEN` (`update-config` skill to add).
- ElevenLabs child: `ELEVENLABS_ADMIN_API_KEY` (or fallback `ELEVENLABS_API_KEY`).

If either child stops at its Step 0, surface the missing-key message and stop here — don't synthesise from a partial picture.

## Step 1 — Resolve the window once

Default: **yesterday (full UTC day)**. Honour the user's choice when they ask:

- `--window yesterday` *(default)* — closed prior UTC day. Always safe. Both children align.
- `--window today` — UTC midnight → now. Partial day on both sides, comparable.
- `--window last-7d` — rolling 7 days. Both children support this verbatim.
- Custom date range — the children diverge on format:
  - Aura child accepts `--since/--until` as ISO timestamps (e.g. `2026-05-10T00:00:00Z`), half-open `[since, until)`, **capped at 90 days** server-side.
  - ElevenLabs child accepts `--since/--until` as `YYYY-MM-DD` dates.
  - Translate the user's input to both forms before invoking — never pass mismatched windows.

Custom windows wider than the ElevenLabs **10-day retention** print a stderr warning in the ElevenLabs child and skip 404'd detail fetches; the Aura child runs the full window up to the **90-day** endpoint cap (a wider range returns a `422 DIALOGUE_METRICS_WINDOW_TOO_LARGE`). Surface this asymmetry in the headline if relevant.

## Step 2 — Invoke both children in parallel

**Compose; do not re-implement.** Use the `Skill` tool in a single message with two parallel calls so both children run concurrently. Pass the resolved window to each in the format that child expects.

For the default `yesterday` window, pass `--window yesterday` to both. For other forms, pass through the matching flags. Always include `--json /tmp/<source>_latency.json` so the payload is parseable for the synthesis step.

Children expected to write structured payloads to:

- `/tmp/aura_latency.json` — shape `{ window_label, since, until, prompters: [{ prompterName, n, ttft: {p50_ms, p95_ms, p99_ms, max_ms}, ttlt: {…}, api_duration: {…} }] }`
- `/tmp/elevenlabs_latency.json` — shape `{ window_label, start_unix, end_unix, by, rows: [{ platform, conversations, turns, metrics: {convai_*: {n, p50_ms, p95_ms, p99_ms, max_ms}} }] }`

If one child exits non-zero (key missing, rate-limited beyond retries, etc.), present the surviving child's report alone with a clear note that the other source is missing. Don't fail the whole run.

## Step 3 — Synthesise one combined report

Lead with a one-paragraph headline that cites the load-bearing numbers from both sources and connects them where useful, then present the two source tables side by side (or stacked — single column reads better in most chat surfaces).

### Headline guidance

A good headline answers, in order:

1. **Which surface is slowest?** Cite the worst prompter on the Aura side (by TTLT P95) and the worst platform's LLM TTFS on the ElevenLabs side.
2. **Are the numbers consistent across sources?** The Aura per-prompter TTFT and ElevenLabs LLM TTFS both reflect the same agent's LLM step from different angles — if they diverge sharply (e.g. ElevenLabs P95 5s but Aura coach-prompter TTFT P95 12s), say so and hypothesise (e.g. "ElevenLabs streams the first sentence; the Aura TTLT measures the full response").
3. **What's the actionable number?** TTFT / LLM TTFS over 5s P95 is the actionable threshold; flag it explicitly.

Example shape:

> _"**Yesterday's Aurie latency:** the voice agent's LLM took 5.45s P95 to start replying — the dominant contributor to perceived slowness. The coach-prompter (chat backend) reported TTLT P95 4.15s with TTFT P95 1.83s, consistent with the voice side. Voice TTS first-byte was snappy at 173ms P95. guardrails-handler-prompter TTLT P99 spiked to 7.63s — investigate."_

### Body layout

```
## Aura (chat / LLM backend)

<rendered table — prompters sorted by TTLT P95 desc>

## ElevenLabs (voice agent)

<rendered table — platforms by call volume>
```

The Aura child's table (9 columns, plus a small optional by-model table) renders cleanly verbatim and should be passed through unchanged. The ElevenLabs child's table is **18 columns wide** (LLM TTFS + TTS TTFB + ASR trailing × n/P50/P95/P99/Max) and wraps badly in chat surfaces — for the combined report, drop the Max column on TTS TTFB and ASR trailing (Max for these two is almost always noise; LLM TTFS Max is the only one worth keeping for tail-latency flags). The full Max values are still preserved in `/tmp/elevenlabs_latency.json` if the user wants them.

## Step 4 — Ask where to route it

After the report, ask the user what to do next:

1. **Post a summary to Slack** *(optional)* — same pattern as `eng:analyze-openai-costs` Step 4. Compose the message — the synthesised headline + the top 3 rows from each source's table, leading with the headline then a brief "Worst by source" line for each — then **invoke [`shared:post-to-slack`](../../../shared/skills/post-to-slack/SKILL.md)** to send it to `#eng-budget` (`C05PC18APLH`, canonical ID in `Tools/Slack`) with `--footer eng:analyze-latency --footer-extra prod` (it stamps the versioned footer; don't hand-type one). That skill owns the connection (MCP-first, `SLACK_BOT_TOKEN` script fallback) and the unfurl-off default — don't call Slack directly here.
2. **Save a Markdown summary** to a path the user provides.
3. **Nothing, just show it** — stop here.

Never route automatically; always wait for the user's pick.

## Notes & edge cases

- **Atomic-child fallback.** If the user has already named "Aura latency yesterday" / "prompter latency" or "ElevenLabs voice latency", skip this orchestrator and invoke the child directly. This skill is the **front door for one-stop daily reporting**, not the only way to reach the children.
- **Window asymmetry.** The Aura child supports `last-7d` cleanly and any custom range up to the 90-day endpoint cap; the ElevenLabs child supports up to `last-10d` (its full retention window). For wider ranges, drop the ElevenLabs side or note that ElevenLabs data is incomplete in the headline.
- **Parallel invocation.** The two children are I/O bound on separate APIs and do not contend. Always invoke them in parallel (single message, two `Skill` tool calls) — the wall-clock time of the orchestrator is roughly the max of the two children, not the sum.
- **Privacy.** Neither child surfaces user-identifying data, so the parent doesn't need redaction logic. The ElevenLabs child aggregates by platform (mobile/web), not user. The Aura child aggregates by prompter (and optionally model). The combined report is safe to paste anywhere internal — but follow the `Compliance/Privacy` rule of thumb and double-check before any external paste.
- **Per-app split status.** ElevenLabs splits cleanly by `platform` (Aurie SDK already tags this). The Aura side does NOT split by app today — the `/ai/dialogue/metrics` endpoint groups by prompterName/model/userId/role, with no app dimension yet ([ENGBACK-1069](https://linear.app/aura-health/issue/ENGBACK-1069)). When an app dimension ships, this orchestrator can add a `--by app` view that combines both children's per-app rows into a single split.
