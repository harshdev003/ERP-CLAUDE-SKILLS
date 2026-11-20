---
name: analyze-livekit-usage
description: >
  Usage deep-dive for the Aurie LiveKit voice agent over a window — session count, peak
  concurrent sessions, total and average session minutes, per-user distribution, and error
  plus dispatch-failure counts. Reads the forwarded LiveKit logs, NOT the k8s container logs
  that `analyze-gcp-logs` and `investigate-gcp-logs` read. A session is one LiveKit room;
  session minutes are the last-minus-first log timestamp for that room, since no explicit
  duration field is emitted. Defaults to yesterday (full UTC day) so numbers are stable;
  supports today, last-7d, last-30d, or a custom range. Sibling of `analyze-livekit-latency`
  (turn latency) and `analyze-livekit-cost` (derived spend). Trigger on "analyze livekit
  usage", "how many voice sessions", "livekit session count", "concurrent voice sessions",
  "voice agent minutes", "aurie voice usage", "how busy is the voice agent", "livekit dispatch
  failures", or a question about how much the LiveKit voice agent is being used.

metadata:
  version: "3.4.1"
knowledge:
  reads:
    - Guide/Index   # baseline routing aid (universal)
    - Tools/LiveKit # agent id, project, log-forwarding schema
    - Tools/Slack                    # Slack post/read conventions + footer format
    - Tools/GCP                      # Cloud Logging access (GCP_KEY)
---

# Analyze LiveKit Usage

Queries the forwarded LiveKit agent logs in GCP Cloud Logging and renders a usage summary — sessions, peak concurrency, minutes, per-user distribution, and failure counts — for the requested window.

This is the usage sibling of [`eng:analyze-livekit-latency`](../analyze-livekit-latency/SKILL.md) (turn latency) and [`eng:analyze-livekit-cost`](../analyze-livekit-cost/SKILL.md) (derived spend). All three share `scripts/livekit_core.py` (the GCP auth ladder + log query + session parsing). It is the LiveKit counterpart to the ElevenLabs [`analyze-elevenlabs-costs`](../analyze-elevenlabs-costs/SKILL.md) family.

## Data source — forwarded LiveKit logs, not k8s

The Aurie voice agent runs on **LiveKit Cloud**, not the GKE cluster. Its logs are forwarded to GCP under a dedicated stream:

```
logName   = projects/firebase-auratech16/logs/aurie-voice-agent
resource  = global
```

The `analyze-gcp-logs` / `investigate-gcp-logs` skills filter on `resource.type="k8s_container"` in the `aura-services` namespace, so they will **not** see these lines — and this skill won't see theirs. Cleanly separated.

## What's surfaced

| Metric | How it's derived |
|---|---|
| **Sessions** | Distinct LiveKit `room` values in the window |
| **Peak concurrent** | Max overlap of session `[first_ts, last_ts]` intervals |
| **Total / avg minutes** | Sum / mean of `(last_ts − first_ts)` per room — the honest proxy for connection minutes (no explicit duration field is logged) |
| **Total user turns** | Count of `Turn latency` / `eou_delay_ms` lines (one per user turn) |
| **Error lines** | Entries with GCP severity ≥ ERROR or the agent's own `level` in {error, crit} |
| **Dispatch failures** | Lines matching connect/join/init-failure markers (room connect failed, TTS_INIT_FAILED, etc.) |
| **Top users** | Per-UID rollup of sessions / minutes / turns |

## Step 0 — Check prerequisites

```bash
if [ -n "$GCP_KEY" ]; then
  echo "OK: GCP_KEY set (SA path)"
elif [ -n "$GOOGLE_APPLICATION_CREDENTIALS" ] && [ -f "$GOOGLE_APPLICATION_CREDENTIALS" ]; then
  echo "OK: GOOGLE_APPLICATION_CREDENTIALS set"
else
  echo "No SA env — will try ADC (gcloud auth application-default login). See references/auth in analyze-gcp-logs."
fi
python3 -m pip install --user -r ${CLAUDE_PLUGIN_ROOT}/skills/analyze-livekit-usage/scripts/requirements.txt >/dev/null 2>&1 || true
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
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-livekit-usage/scripts/report.py \
  --window yesterday \
  --json /tmp/livekit_usage.json
```

### Common variants

```bash
# Today so far
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-livekit-usage/scripts/report.py --window today

# Rolling 7 days
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-livekit-usage/scripts/report.py --window last-7d

# Custom range
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-livekit-usage/scripts/report.py --since 2026-06-25 --until 2026-06-28
```

The script writes `# auth`, `# query`, and `# fetched N` progress lines to stderr and the report to stdout.

## Step 3 — Show the report inline

The script prints a headline + a bullet summary + a top-users table. Show it directly. Example:

> _"LiveKit usage yesterday (Jun 30 UTC): 42 sessions, peak 4 concurrent, 118 minutes total (2.8 avg), 0 dispatch failures."_

Things worth flagging when commenting:

- **Peak concurrent approaching the worker replica cap** (see `lk agent status` — the `1/1/8` max-replicas figure) means autoscale headroom is getting tight.
- **Dispatch failures > 0** are always worth a look — drill in with [`eng:investigate-livekit-logs`](../investigate-livekit-logs/SKILL.md) or [`eng:investigate-gcp-logs`](../investigate-gcp-logs/SKILL.md) filtered to this log stream.
- **A single UID dominating minutes** in prod (not QA) can distort cost — cross-reference [`eng:analyze-livekit-cost`](../analyze-livekit-cost/SKILL.md).
- **Console/QA sessions** (rooms prefixed `console-`) are test traffic from the LiveKit Cloud playground, not real users — mention but don't treat as production load.

## Step 4 — Ask where to route it

After the report, ask what to do next:

1. **Post a summary to Slack** *(optional)* — compose the headline + top row, then invoke [`shared:post-to-slack`](../../../shared/skills/post-to-slack/SKILL.md) to send it to `#eng-budget` (canonical ID in `Tools/Slack`) with `--footer eng:analyze-livekit-usage --footer-extra prod` (it stamps the versioned footer; don't hand-type one). That skill owns the connection and unfurl-off default — don't call Slack directly.
2. **Save a Markdown summary** to a path the user provides.
3. **Nothing, just show it** — stop here.

Never route automatically; always wait for the user's pick.

## Notes & edge cases

- **A "session" is one room.** If a room is reused (rare), its minutes span the whole reuse window — treat multi-hour "sessions" as suspect and drill in.
- **Minutes are a log-span proxy.** Connection minutes = last log ts − first log ts for the room. There's no explicit session-duration field, so a session that logs nothing after connect reads as ~0 min. This is the same figure [`analyze-livekit-cost`](../analyze-livekit-cost/SKILL.md) bills against.
- **Privacy.** Aggregates by session/user; markdown truncates UIDs to an 8-char prefix. Full UIDs appear only in the `--json` payload (opt-in). No transcripts or free-text content are surfaced. Follows `Compliance/Privacy`.
- **`--max-entries` caps the fetch** at 50k lines by default — a very wide window on a busy day can hit it (a stderr note prints the count). Narrow the window or raise the cap.
- **LiveKit exposes no usage API.** Everything here is derived from logs; there is no `lk` CLI usage/billing endpoint to cross-check against (spend lives only in the LiveKit Cloud dashboard). See [`analyze-livekit-cost`](../analyze-livekit-cost/SKILL.md) `references/methodology.md`.
