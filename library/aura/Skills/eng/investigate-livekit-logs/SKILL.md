---
name: investigate-livekit-logs
description: >
  Read and triage the Aura LiveKit Cloud voice agent's production logs from inside Claude
  Code, via the `lk` CLI. Captures a bounded live window of the agent's runtime deploy logs,
  redacts user PII (raw user messages, first_message and metadata are logged at INFO today),
  groups lines by room and session, and surfaces ERROR, Traceback, disconnect and "agent did
  not join" events; falls back to build logs for failed deploys and points at `lk agent
  status` and `versions` for ops follow-up. Trigger on "investigate livekit logs", "read the
  livekit agent logs", "what's the voice agent doing in prod", "tail the lk agent logs", "lk
  agent logs", "is the voice agent erroring", "why did the agent not join the room", "check
  the livekit cloud agent", "livekit agent build logs", or "why did the agent deploy fail". Do
  NOT use for the IntelligenceService LLM or webhook backend logs — those run on GKE, use
  `eng:investigate-gcp-logs`. Do NOT use for per-turn cost or latency analysis.

metadata:
  version: "3.0.2"
knowledge:
  reads:
    - Guide/Index            # baseline routing aid (universal)
    - Tools/LiveKit          # LiveKit Cloud project/agent ids, lk CLI, deploy model (NOTE: planned — PROD-2638; create alongside if missing)
    - Compliance/Privacy     # the agent logs user PII; this skill redacts before output (UID prefix + email domain only; never full email/name)
    - Tools/GCP                      # Cloud Logging access (GCP_KEY)
---

# investigate-livekit-logs

Read-side debugging skill for the Aura **LiveKit Cloud voice agent** (`LivekitVoiceAgent`).
Read-only — it never writes, deploys, restarts, or disables redaction.

**Project is not hardcoded.** The Aura LiveKit project is **`Aurie`** (id `p_5bw0of3laoy`, URL
`poc-pf65xo6x.livekit.cloud` — the `poc-` subdomain is a fixed legacy name, *not* a separate
project). The agent `lk-voice-agent` runs there. Locally `lk` may alias the project under any
name (commonly `poc`), so the skill defaults to your selected `lk` project and reads the agent
id from `livekit.toml`; target explicitly with `--project <your-lk-alias>` and `--id <CA_…>`.
Requires the `lk` CLI installed (`brew install livekit-cli`) and authenticated (`lk cloud auth`).

## Goal

Answer "what is the voice agent doing / erroring on in prod?" without leaving Claude Code —
wrap `lk agent logs` (a live tail) into a bounded, **PII-redacted** snapshot plus a triage
digest, and hand off to the right next command.

## Hard limitation (set expectations first)

`lk agent logs` **only tails live**, and only from the **latest running instance** — there is
no time-window or historical query API in the CLI (unlike GCP Cloud Logging), and it is "not
a comprehensive view of all logs from all instances" at scale. So this skill captures a *live
window* (default ~20s): if the agent is idle (no active calls), you'll see little. Say this to
the user when the window comes back empty — don't imply "no logs" means "no activity ever".

For **history / search**, the CLI won't help — use one of:
- **Dashboard → Agent Observability.** `cloud.livekit.io` → the **Aurie** project → **Agents**
  → open a session → **Agent insights** (synced audio, transcript, turn traces, and logs).
  Build logs are kept per version (indefinitely for the latest deploy). (There is no top-level
  "Logs" tab — it's per-session under observability.)
- **Runtime log forwarding** (durable + searchable): LiveKit can auto-forward all runtime logs
  to Datadog / CloudWatch / Sentry / New Relic via secrets. Recommend this if the team needs
  real historical log search. See `references/lk-cli.md`.

## Privacy (mandatory)

The agent currently logs raw user utterances, `first_message`, and metadata at INFO
(ENG-707), so its prod logs **contain user PII**. The capture script redacts these before
anything reaches stdout and **has no opt-out flag** — do not add one, and never print raw
`lk agent logs` output yourself. Per `Compliance/Privacy`: surface only a Firebase UID's
8-char prefix and an email's domain — never a full email, given name, or family name.

## Inputs

The user provides one of:

- A **triage** intent: "investigate livekit logs", "is the agent erroring", "what's the
  voice agent doing in prod" → live snapshot of runtime logs.
- A **build** intent: "why did the deploy fail", "livekit build logs" → `--mode build`.
- A **scoped** intent: a specific room (`poc-room-…`) or a grep pattern.

## Workflow

1. **Resolve the request** into a mode (`triage` default, or `build`) and optional `--room` /
   `--grep` / `--seconds` (widen the window to ~45s when hunting for a live call).
2. **Resolve the agent id** (the script can't guess — the project hosts more than one agent,
   and running from the plugin dir means there's no `livekit.toml`). Run `lk agent list` and
   pick the **Aura voice agent** — dispatch name `lk-voice-agent` — and pass its `--id`. (If you
   instead run the script from the agent repo, `livekit.toml` supplies the id and you can omit
   `--id`.) If your `lk` default project isn't the right one, add `--project <alias>`.
   ```bash
   lk agent list          # → copy the CA_… id for dispatch name "lk-voice-agent"
   ```
3. **Capture + triage** with the bundled script (it auths via the user's configured `lk`
   project; no token handling here):
   ```bash
   python3 ${CLAUDE_PLUGIN_ROOT}/skills/investigate-livekit-logs/scripts/livekit_logs.py \
     --id <CA_…> [--project <alias>] [--mode triage|build] [--seconds N] [--room …] [--grep RE]
   ```
   If `--id` is omitted and there's no `livekit.toml` in the cwd, the script prints the agent
   list and exits asking for `--id` (it never silently tails the wrong/no agent). Otherwise it
   launches `lk agent logs`, captures a bounded window, redacts PII, groups by room/session, and
   prints a digest (counts → rooms → errors → warnings → next steps).
4. **Interpret** the digest for the user: what's erroring, which sessions are affected, and
   whether it points at the agent, a provider (Deepgram/TTS/LLM), or a deploy.
5. **Hand off** to the next command the digest suggests (`lk agent status`, `lk agent
   versions`, `--mode build`, or the dashboard for history).

## Output

A short narrative + the script's digest:
- one-line window summary (lines captured, error/warning/room counts),
- rooms/sessions seen,
- redacted error and warning lines,
- the next-step command block.

## Edge cases

- **`lk` not installed** → script says so (`brew install livekit-cli`); stop.
- **`lk` not authenticated / no project** → script detects it and points at `lk cloud auth` /
  `lk project list`; relay that, don't retry blindly.
- **Empty window** → report it as "no activity in the window (agent may be idle)" + the
  dashboard pointer; do not claim the agent is broken.
- **Deploy failure** → re-run with `--mode build`.
- **Re-run** → idempotent; it's a read. Two runs just capture two windows.

## References

- `references/lk-cli.md` — `lk` commands, the Aurie/poc projects, agent resolution, log shape,
  redaction patterns, and the dashboard fallback.
