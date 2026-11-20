---
name: check-livekit-health
description: >
  On-demand health and alert triage for the Aurie LiveKit voice agent — runs a set of named
  checks over a recent window and returns PASS / WARN / FAIL per check plus an overall
  verdict, exiting non-zero when anything FAILs. This is the go-live "is anything on fire
  right now?" button, NOT always-on alerting, which belongs in GCP Monitoring alert policies.
  Checks error rate (error lines per session), dispatch failures, worker health via the `lk`
  CLI (degrading to SKIP if unauthed), and cost budget (derived spend, only when a budget is
  given). Log-derived checks read the forwarded LiveKit logs, NOT the k8s container logs.
  Because it exits 0/1 it can be wrapped in cron or CI later. Trigger on "check livekit
  health", "is the voice agent ok", "livekit alert check", "are there voice agent errors right
  now", "voice go-live check", "is aurie voice healthy", "run a livekit health check", or a
  question about whether the LiveKit voice agent is currently in a good state.

metadata:
  version: "3.4.1"
knowledge:
  reads:
    - Guide/Index   # baseline routing aid (universal)
    - Tools/LiveKit # agent id, project, log-forwarding schema
    - Tools/Slack                    # Slack post/read conventions + footer format
    - Tools/GCP                      # Cloud Logging access (GCP_KEY)
---

# Check LiveKit Health

Runs a fixed set of health checks against the Aurie LiveKit voice agent over a recent window and returns a compact PASS / WARN / FAIL checklist with an overall verdict. It is the fast "is anything on fire right now?" triage button you run by hand before a go-live, after a deploy, or when someone reports the voice agent misbehaving.

## On-demand, not always-on

**This skill is an on-demand triage tool — it is not always-on alerting.** It answers the [PROD-3141] "alerts" ask as a check you (or a scheduled wrapper) invoke, snapshots the last window, prints a verdict, and exits. It does not sit resident, page anyone, or maintain state between runs.

- For **persistent, always-on alerting** the right home is **GCP Monitoring alert policies** (log-based metrics + alerting on the `aurie-voice-agent` log stream). That's a possible follow-up and is out of scope here.
- Because `report.py` **exits non-zero on any FAIL** (0 on PASS/WARN), this skill *can* be wrapped in a cron job or CI step later without any code change — the exit code is the contract a scheduler needs.

This is the health sibling of [`eng:analyze-livekit-usage`](../analyze-livekit-usage/SKILL.md) (usage deep-dive), [`eng:analyze-livekit-latency`](../analyze-livekit-latency/SKILL.md) (turn latency), and [`eng:analyze-livekit-cost`](../analyze-livekit-cost/SKILL.md) (derived spend). All share `scripts/livekit_core.py` (the GCP auth ladder + log query + session parsing).

## Data source — forwarded LiveKit logs, not k8s

The Aurie voice agent runs on **LiveKit Cloud**, not the GKE cluster. Its logs are forwarded to GCP under a dedicated stream:

```
logName   = projects/firebase-auratech16/logs/aurie-voice-agent
resource  = global
```

The `analyze-gcp-logs` / `investigate-gcp-logs` skills filter on `resource.type="k8s_container"` in the `aura-services` namespace, so they will **not** see these lines — and this skill won't see theirs. The `worker_health` check is the one exception: it goes to the `lk` CLI (LiveKit Cloud control plane), not GCP logs.

## The checks

| Check | What it measures | Verdict logic | Threshold flag (default) |
|---|---|---|---|
| **error_rate** | Error log lines ÷ sessions in the window (known-benign SDK lines excluded — see below) | FAIL above the max; WARN above half the max; `N/A` if 0 sessions | `--max-error-rate` (1.0) |
| **dispatch_failures** | Lines matching connect/join/init-failure markers (`room connect failed`, `TTS_INIT_FAILED`, …) | FAIL above the max (default 0 → *any* failure FAILs) | `--max-dispatch-failures` (0) |
| **worker_health** | `lk agent status` — worker `Status` must be `Running`; `Replicas` `cur/desired/max` must have `cur ≥ desired` | FAIL if not Running; WARN if below desired replicas; **SKIP if lk is missing/unauthed/unparseable** | (uses `lk`; `--agent-id` to pin) |
| **cost_budget** | Derived spend rate = (total session minutes × `--rate`) ÷ window days, projected to a full day | FAIL if the daily rate > `--budget`; SKIP if no `--budget` given (spend still printed) | `--budget` (a daily USD ceiling; unset → SKIP), `--rate` (0.01 USD/min) |

The overall **verdict** is the worst single check: FAIL if any FAIL, else WARN if any WARN, else PASS. SKIP and N/A never worsen the verdict.

## Step 0 — Check prerequisites

```bash
if [ -n "$GCP_KEY" ]; then
  echo "OK: GCP_KEY set (SA path)"
elif [ -n "$GOOGLE_APPLICATION_CREDENTIALS" ] && [ -f "$GOOGLE_APPLICATION_CREDENTIALS" ]; then
  echo "OK: GOOGLE_APPLICATION_CREDENTIALS set"
else
  echo "No SA env — will try ADC (gcloud auth application-default login). See references/auth in analyze-gcp-logs."
fi
python3 -m pip install --user -r ${CLAUDE_PLUGIN_ROOT}/skills/check-livekit-health/scripts/requirements.txt >/dev/null 2>&1 || true

# worker_health uses the `lk` CLI. If it's absent or unauthed the check SKIPs (never crashes).
command -v lk >/dev/null && echo "OK: lk CLI present" || echo "lk CLI absent — worker_health will SKIP"
```

`GCP_KEY` (single-line SA JSON in `~/.claude/settings.json` env) is the same key the GCP log skills use — the `<redacted-email>` SA with `roles/logging.viewer`. If none is set the script falls back to ADC. The `lk` CLI is independent of GCP auth: if it isn't configured, only `worker_health` degrades — every log-derived check still runs.

## Step 1 — Resolve the window

Default is the **last 1h** — the freshest slice for a "right now?" check.

- `--since 1h` *(default)* — rolling last hour
- `--since 30m` / `--since 6h` / `--since 24h` — other rolling windows (`m`/`h`/`d` units)

Use a **short** window (1h–6h) for go-live / post-deploy triage so the verdict reflects the current state, and a **longer** window (24h) when confirming a quiet day overall.

## Step 2 — Run the check

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/check-livekit-health/scripts/report.py \
  --since 1h \
  --json /tmp/lk_check.json
```

### Common variants

```bash
# Post-deploy triage — last 6h, tighter error budget
python3 ${CLAUDE_PLUGIN_ROOT}/skills/check-livekit-health/scripts/report.py --since 6h --max-error-rate 0.5

# Enforce a spend ceiling for the window (turns cost_budget from SKIP into PASS/FAIL)
python3 ${CLAUDE_PLUGIN_ROOT}/skills/check-livekit-health/scripts/report.py --since 24h --budget 5.00 --rate 0.01

# Pin the worker to a specific agent id (skip dispatch-name resolution)
python3 ${CLAUDE_PLUGIN_ROOT}/skills/check-livekit-health/scripts/report.py --agent-id CA_6vDm9dFvw6FN
```

The script writes `# auth`, `# query`, and `# fetched N` progress lines to stderr and the checklist to stdout. Ignore the harmless `nvm is not compatible` shell noise and any gRPC `ev_poll_posix` fork lines from the `lk` subprocess.

## Step 3 — Show the checklist inline

The script prints one line per check plus the verdict. Show it directly. Example:

```
LiveKit health — last 1h

[PASS] error_rate: 0.02 err/session (1 lines / 50 sessions) (threshold 1.0)
[PASS] dispatch_failures: 0 connect/join/init-failure line(s) (threshold 0)
[PASS] worker_health: status=Running, replicas=1/1, id=CA_6vDm9dFvw6FN (threshold Running)
[SKIP] cost_budget: derived spend \$0.42 (no --budget set)

VERDICT: PASS
```

Things worth flagging when commenting:

- **VERDICT: FAIL** — surface it immediately and drill in. `error_rate` / `dispatch_failures` FAILs → [`eng:investigate-livekit-logs`](../investigate-livekit-logs/SKILL.md) or [`eng:investigate-gcp-logs`](../investigate-gcp-logs/SKILL.md) filtered to this log stream. `worker_health` FAIL (not Running) → check the LiveKit Cloud dashboard / recent deploy.
- **worker_health WARN (below desired replicas)** — autoscale hasn't caught up or a replica is unhealthy; usually transient, but worth a second run.
- **worker_health SKIP** — `lk` isn't configured in this shell. The check didn't run; it did not pass. Note it explicitly rather than implying the worker is healthy.
- **error_rate N/A** — no sessions in the window. A quiet window is neither healthy nor broken; widen `--since` or confirm the agent is live.

## Step 4 — Ask where to route it (optional)

After showing the checklist, ask what to do next:

1. **Post the verdict to Slack** *(optional)* — compose the verdict + any FAIL/WARN lines, then invoke [`shared:post-to-slack`](../../../shared/skills/post-to-slack/SKILL.md) to send it to `#eng-budget` (canonical ID in `Tools/Slack`) with `--footer eng:check-livekit-health --footer-extra prod` (it stamps the versioned footer; don't hand-type one). That skill owns the connection and unfurl-off default — don't call Slack directly.
2. **Save the JSON payload** to a path the user provides (already written if `--json` was passed).
3. **Nothing, just show it** — stop here.

Never route automatically; always wait for the user's pick.

## Notes & edge cases

- **Exit code is the contract.** `0` = no FAIL (PASS or WARN); `1` = at least one FAIL. WARN deliberately stays exit 0 so a wrapping cron/CI only pages on hard failures. This is what makes the on-demand check safe to schedule later without touching the code.
- **worker_health never crashes the run.** Missing `lk`, unauthed `lk`, an unresolvable agent id, or an unparseable status table all resolve to `SKIP` — the log-derived checks still produce a verdict.
- **`lk agent status` has no `--json` today.** The check parses the plain box-drawing table (Status + `Replicas` `cur/desired/max`). If a future `lk` adds `--json`, the script detects it via `--help` and prefers it automatically.
- **cost_budget is derived, not billed.** Spend = session minutes × `--rate`; LiveKit exposes no usage/billing API (real spend lives only in the LiveKit Cloud dashboard). It's the same figure [`analyze-livekit-cost`](../analyze-livekit-cost/SKILL.md) uses. Without `--budget` the check SKIPs but still prints the derived spend.
- **error_rate is line-based.** It counts entries with GCP severity ≥ ERROR or the agent's own `level` in {error, crit}, divided by sessions — a coarse but honest signal. A single noisy session can push it up; drill in before treating a WARN as an incident.
- **Known-benign SDK lines are excluded from error_rate.** The LiveKit SDK logs some normal, recovered events at ERROR severity — RTC data-channel teardown on every disconnect (`data channel … closed unexpectedly`) and Deepgram STT idle-timeout reconnects (`deepgram connection closed unexpectedly`, status 1011, retryable — the plugin auto-reconnects). These originate in the SDK (the agent can't downgrade them at the source), so they'd otherwise inflate the rate and fire false WARNs. They're subtracted from the count, and the number excluded is always shown in the detail (`… , N benign SDK line(s) excluded`) — never silently dropped. `dispatch_failures` is unaffected and still FAILs on any genuine connect/join/init failure.
- **Privacy.** No transcripts or free-text content are surfaced; only aggregate counts and the (public) agent id. Follows `Compliance/Privacy`.
- **`--max-entries` caps the fetch** at 50k lines by default — a very wide window on a busy day can hit it. Health checks use short windows, so this rarely matters.
