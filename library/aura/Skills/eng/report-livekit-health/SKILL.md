---
name: report-livekit-health
description: >
  One-pane DAILY health snapshot of the Aurie LiveKit voice agent for standup and go-live
  watch — combines WORKER HEALTH (live agent status from the `lk` CLI: status, CPU, memory,
  replicas, version, region) with LOG-DERIVED usage and errors from the forwarded LiveKit
  logs: sessions, peak concurrent, minutes, user turns, error lines, dispatch failures, agent
  versions seen, latency P95s, the STT-gate suppression rate, and the effective noise guards.
  Defaults to yesterday so numbers are stable; supports today, last-7d, or custom. Produces a
  Slack-pasteable headline and bullet block. Degrades gracefully when `lk` is unavailable,
  still rendering the log-derived section. Trigger on "report livekit health", "voice agent
  health", "aurie voice daily snapshot", "is the voice agent healthy", "livekit worker
  status", "voice agent standup", "check the voice worker", or "stt gate suppression rate".
  Also triggers on "how's the voice agent doing", "is the phantom-turn gate eating real
  turns", "noise guard values in prod".

metadata:
  version: "3.7.1"
knowledge:
  reads:
    - Guide/Index   # baseline routing aid (universal)
    - Tools/LiveKit # agent id, project, log-forwarding schema, worker replicas
    - Tools/Slack   # #eng-budget channel id + the versioned post footer, for the optional Step 4 routing
    - Tools/GCP                      # Cloud Logging access (GCP_KEY)
---

# Report LiveKit Health

A single-screen daily health snapshot for the Aurie LiveKit voice agent — the "is the voice agent healthy this morning?" glance for standup and go-live watch. It fuses **two independent sources** into one report:

1. **Worker health** — the live status of the deployed agent, from the **`lk` CLI** (LiveKit Cloud): is it `Running`, how many replicas, CPU/Mem, which version, when deployed.
2. **Log-derived health** — usage + errors over a window, from the **forwarded GCP logs** (`logName projects/firebase-auratech16/logs/aurie-voice-agent`): sessions, peak concurrency, minutes, user turns, error lines, dispatch failures, and turn-latency P95s.

Sibling of [`eng:analyze-livekit-usage`](../analyze-livekit-usage/SKILL.md) (usage deep-dive), [`eng:analyze-livekit-latency`](../analyze-livekit-latency/SKILL.md) (turn latency), and [`eng:analyze-livekit-cost`](../analyze-livekit-cost/SKILL.md) (derived spend). All share `scripts/livekit_core.py` (GCP auth ladder + log query + session parsing). Where the `analyze-*` skills produce multi-section deep-dives on one dimension, this **`report-*`** skill condenses everything into one Slack-pasteable pane defaulted to yesterday — the daily-snapshot counterpart to [`eng:report-elevenlabs-costs`](../report-elevenlabs-costs/SKILL.md).

## Two sources — worker health vs log-derived

| Section | Source | Auth | Degrades? |
|---|---|---|---|
| **Worker health** | `lk agent status` (LiveKit Cloud API) | `lk cloud auth` (or `LIVEKIT_*` env) | Yes — prints "worker health unavailable" and continues |
| **Log-derived** | GCP Cloud Logging, forwarded LiveKit stream | `GCP_KEY` / ADC | No — this is the core of the report |

The Aurie agent runs on **LiveKit Cloud**, not the GKE cluster. Its logs are forwarded to GCP under a dedicated stream:

```
logName   = projects/firebase-auratech16/logs/aurie-voice-agent
resource  = global
```

The `analyze-gcp-logs` / `investigate-gcp-logs` skills filter on `resource.type="k8s_container"`, so they won't see these lines — and this skill won't see theirs. Cleanly separated.

## What's surfaced

| Metric | How it's derived |
|---|---|
| **Worker Status / CPU / Mem / Replicas / Version / Region / Deployed At** | Parsed from the `lk agent status` table for the aurie agent |
| **Sessions** | Distinct LiveKit `room` values in the window |
| **Peak concurrent** | Max overlap of session `[first_ts, last_ts]` intervals |
| **Total / avg minutes** | Sum / mean of `(last_ts − first_ts)` per room over the selected rows — `Session starting` / `Session config resolved` open the span, `Session ended; reporting conversation metrics` closes it |
| **Total user turns** | Count of `Turn latency` / `eou_delay_ms` lines (one per user turn) |
| **Error lines** | Entries with GCP severity ≥ ERROR or the agent's own `level` in {error, crit} |
| **Sessions with errors** | Rooms with ≥ 1 error line |
| **Dispatch failures** | Lines matching connect/join/init-failure markers (room connect failed, TTS_INIT_FAILED, etc.) |
| **eou_delay_ms / llm_ttft_ms P95** | `lk.percentiles(lk.latency_values(...))` over the window |
| **STT gate** | `Suppressed low-confidence STT final` rows (one per final the agent dropped as low-confidence noise) as a share of user turns + suppressed finals, the denominator the expectation is measured against. Expect ~2.2%; flags ⚠️ above 5% |
| **Noise guards** | Distinct `noise_cancellation` / `min_interruption_words` values on the window's `Turn handling configured` rows, with session counts. Flags ⚠️ when a guard has more than one value in the window |
| **7-day delta** | Window sessions + minutes vs prior-7-day daily average |

## Step 0 — Check prerequisites

Two independent auth surfaces — one for each source. Log-derived needs GCP; worker health needs `lk`.

```bash
# GCP (log-derived section — the core; report fails only if this is missing)
if [ -n "$GCP_KEY" ]; then
  echo "OK: GCP_KEY set (SA path)"
elif [ -n "$GOOGLE_APPLICATION_CREDENTIALS" ] && [ -f "$GOOGLE_APPLICATION_CREDENTIALS" ]; then
  echo "OK: GOOGLE_APPLICATION_CREDENTIALS set"
else
  echo "No SA env — will try ADC (gcloud auth application-default login). See analyze-gcp-logs references/auth."
fi

# lk CLI (worker-health section — optional; report degrades gracefully without it)
if command -v lk >/dev/null 2>&1 && lk project list >/dev/null 2>&1; then
  echo "OK: lk CLI installed and authed"
else
  echo "lk not installed or not authed — worker health will show 'unavailable'; log-derived section still runs. Fix: brew install livekit-cli && lk cloud auth"
fi

python3 -m pip install --user -r ${CLAUDE_PLUGIN_ROOT}/skills/report-livekit-health/scripts/requirements.txt >/dev/null 2>&1 || true
```

`GCP_KEY` (single-line SA JSON in `~/.claude/settings.json` env) is the same key the other GCP log skills use — the `<redacted-email>` SA with `roles/logging.viewer`. If none is set the script falls back to ADC.

Worker health is a **nice-to-have**: if `lk` is missing or unauthed the report still produces the full log-derived snapshot — it just labels the worker block "unavailable" and marks the verdict `[log-only]`.

## Step 1 — Resolve the window

Default is **yesterday (UTC)** — stable and comparable day-over-day.

- `--window yesterday` *(default)* — full prior UTC day
- `--window today` — UTC midnight → now (partial; the headline says "so far")
- `--window last-7d` — rolling 7 days
- `--since YYYY-MM-DD --until YYYY-MM-DD` — custom UTC range (half-open)

The 7-day delta baseline is the 7 full UTC days *ending at the window start*, so it never overlaps the window being reported.

## Step 2 — Run the report

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-livekit-health/scripts/report.py \
  --window yesterday \
  --json /tmp/lk_health.json
```

### Common variants

```bash
# Today so far (go-live watch)
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-livekit-health/scripts/report.py --window today

# Rolling 7 days
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-livekit-health/scripts/report.py --window last-7d

# Custom range
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-livekit-health/scripts/report.py --since 2026-06-25 --until 2026-06-28

# Log-derived only (skip the lk CLI probe entirely)
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-livekit-health/scripts/report.py --no-worker-health

# Pin a specific agent id (skips dispatch-name auto-detect)
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-livekit-health/scripts/report.py --agent-id CA_6vDm9dFvw6FN
```

The script:

- Probes **worker health** via `lk agent list` (to resolve the aurie agent by dispatch name `aurie-voice-agent`) then `lk agent status --id <id>`, parsing the box-drawing status table defensively (splits on the vertical char, maps columns off the header row — no `awk -F'│'`). Auto-detects the aurie agent; `--agent-id` overrides.
- Queries the **forwarded GCP logs** for the window with a server-side filter that selects only the rows the report counts (session start/end, `eou_delay_ms` / `llm_ttft_ms` turn latency, STT-gate drops, turn-handling config, error-severity rows and dispatch-failure markers — `window_rows_clause()` in `report.py`), aggregates sessions, counts errors + dispatch failures, and computes eou/ttft P95s. A full busy day is ~17k rows, not the 100k+ of the whole stream.
- Fetches the prior-7-day baseline for the **delta** (skipped cleanly if there's no baseline data or with `--no-delta`).
- Renders a headline + worker-health block + log-derived block + delta, and writes `--json` if requested.

The script writes `# worker`, `# auth`, `# query`, and `# fetched N` progress lines to stderr and the report to stdout.

## Step 3 — Show the report inline

The script prints a headline + a worker-health block + a log-derived block + a 7-day delta. Show it directly — single screen, don't paginate or reformat. Example headline:

> _"Aurie voice health — Jun 30 (UTC): Worker Running ✅ (1 / 1 / 8 replicas, 8DQie6P5UHhZ) | 42 sessions, peak 4, 118 min | 0 err lines, 0 dispatch fails [healthy]"_

Things worth flagging when commenting:

- **Worker not `Running`** (or worker health unavailable) is the top-line concern — everything below is moot if the agent isn't up.
- **Peak concurrent approaching the max-replica cap** (the third number in Replicas, e.g. `1 / 1 / 8`) means autoscale headroom is getting tight.
- **Dispatch failures > 0** are always worth a look — drill in with [`eng:investigate-livekit-logs`](../investigate-livekit-logs/SKILL.md).
- **Latency P95 creeping up** (eou_delay_ms / llm_ttft_ms) — cross-reference [`eng:analyze-livekit-latency`](../analyze-livekit-latency/SKILL.md).
- **Multiple agent versions in the logs** during a window usually means a deploy landed mid-window — expected right after a rollout, suspect otherwise.
- **A large 7-day delta** (↑/↓) flags a usage or reliability shift worth a second look.
- **STT gate well above ~2.2%** — the confidence floor is dropping real quiet answers ("yes", "no"), which in a therapy conversation is the worse failure. Lower `STT_MIN_FINAL_CONFIDENCE` in the agent's `.env` and `make sync-secrets-apply` — no release needed. **Near zero while phantom-turn reports continue** means the phantoms are multi-word or high-confidence, a mechanism the gate does not cover.
- **Noise guards with more than one value in a window**, or a value other than `bvc` / `1`, means a deploy or secrets change landed mid-window or a deployed secret drifted from `.env`. `not logged by this agent version` means every session ran an agent older than 1.7.0.

## Step 4 — Ask where to route it

After the report, ask what to do next — never route automatically:

1. **Post the snapshot to Slack** *(optional)* — compose the headline + a couple of bullets, then invoke [`shared:post-to-slack`](../../../shared/skills/post-to-slack/SKILL.md) to send it to **`#eng-budget`** (canonical ID in `Tools/Slack`) with `--footer eng:report-livekit-health --footer-extra prod` (it stamps the versioned footer; don't hand-type one). That skill owns the connection and the unfurl-off default — don't call Slack directly.
2. **Drill into usage** — chain to [`eng:analyze-livekit-usage`](../analyze-livekit-usage/SKILL.md) for the per-user distribution.
3. **Drill into latency** — chain to [`eng:analyze-livekit-latency`](../analyze-livekit-latency/SKILL.md) for full turn-latency percentiles.
4. **Investigate errors** — hand the window to [`eng:investigate-livekit-logs`](../investigate-livekit-logs/SKILL.md) if dispatch failures or error lines are non-zero.
5. **Nothing, just show it** — stop here.

## Notes & edge cases

- **Graceful degrade when `lk` is unavailable.** If the `lk` CLI is missing, unauthed, hangs, or the aurie agent can't be resolved, the worker-health block prints `worker health unavailable (<reason>)`, the headline shows `Worker health unavailable ⚠️`, the verdict is `[log-only]`, and the log-derived section renders normally. The report never hard-fails on worker health. Fix worker health with `brew install livekit-cli && lk cloud auth` (or set `LIVEKIT_URL` / `LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET`).
- **Agent auto-detection.** The script matches the aurie agent by dispatch name (`aurie-voice-agent`) rather than a hard-coded ID, since agent IDs rotate on redeploy. Use `--agent-id` to pin one explicitly.
- **A "session" is one room.** Minutes = last selected row − first selected row for the room: session start to the `Session ended` row. A session that dies before logging its end row runs to its last turn or error row instead, and one with no row after start reads as ~0 min. The span omits the few seconds of connect/teardown logging around those rows, so it reads marginally below [`analyze-livekit-cost`](../analyze-livekit-cost/SKILL.md)'s whole-stream log span.
- **The STT-gate expectation is a measured figure, not a target.** ~2.2% is the share of finals the live `word_count ≤ 1 ∧ confidence < 0.5` floor suppressed over 2026-08-25..09-15 — 25,102 user turns plus all 237 suppressed finals, of which 2.16% in the Inference era alone (Sep 10–15, n=6,430). The ⚠️ bar of 5% is the daily mean plus three standard deviations (2.30% ± 1.02 over 11 days), so an ordinary noisy day doesn't flag. It moves with traffic mix and with the floor itself, so re-derive rather than trust it once either changes. The denominator is user turns **plus** suppressed finals, because a suppressed final never becomes a turn.
- **Re-derive it from the suppression rows, never from `User turn shape`.** The two count different populations, and the turn-level one reads roughly 6x low. `User turn shape`'s `transcript_confidence` is the SDK's unweighted mean over every final in the committed turn and its `word_count` is the whole turn's text, whereas the gate tests **one final's** confidence against that final's own word count — so a low-confidence one-word final arriving mid-utterance is averaged into a longer turn and never appears as a low-confidence one-word turn. Counting `Suppressed low-confidence STT final` rows over (user turns + suppressed finals) is the only like-for-like measure. ENG-1295 has the derivation; PROD-5455 has the gate's original design.
- **7-day delta needs history.** If the prior-7-day baseline has no sessions, the delta is skipped cleanly (renders nothing) rather than dividing by zero. `--no-delta` skips it too.
- **Privacy.** This is an aggregate health snapshot — no per-user rows and no UIDs are surfaced in the rendered report. Full detail (worker fields, latency, delta) lives in the `--json` payload. No transcripts or free-text content. Follows `Compliance/Privacy`.
- **`--max-entries` caps the fetch** at 50k lines by default (per window and per 7-day baseline). The targeted filter keeps a busy day at roughly a third of that; a multi-day window (`last-7d`) can still hit it, and the report then prints a **Truncated read** banner and marks totals as a lower bound. The baseline reads only session start/end rows (~2–3 per session), so it stays well under the cap.
- **LiveKit exposes no usage/billing API.** Worker health comes from `lk agent status`; usage is derived from logs. Spend lives only in the LiveKit Cloud dashboard — see [`analyze-livekit-cost`](../analyze-livekit-cost/SKILL.md) `references/methodology.md`.
- **Cowork sandbox.** `mcp__workspace__bash` strips host env (PROD-1305) — `GCP_KEY` won't reach the sandbox, and `lk` isn't installed there. Use the per-session paste-export workaround in AINative `CLAUDE.md` for `GCP_KEY`; worker health will show unavailable in that context.
