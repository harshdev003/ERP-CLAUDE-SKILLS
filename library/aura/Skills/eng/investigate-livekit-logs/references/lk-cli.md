# LiveKit Cloud agent — `lk` CLI reference

Reference for `eng:investigate-livekit-logs`. The canonical service doc is `Tools/LiveKit` in
AI Knowledge (planned — PROD-2638); this file is the skill-local cheatsheet.

## What's deployed

The Aura voice agent (`LivekitVoiceAgent` repo) runs as a **LiveKit Cloud Agent** — not on GKE.
There is **one** project:

| Field | Value |
|-------|-------|
| Project name | **Aurie** |
| Project id | `p_5bw0of3laoy` |
| Project URL | `poc-pf65xo6x.livekit.cloud` (the `poc-` subdomain is a fixed legacy name — renaming the project does not change it) |
| Voice agent | `lk-voice-agent` (id `CA_NQ9eyfBoKUjp`) — also an `electron-poc-agent` on the same project |
| Entrypoint / rooms | `agent.py` / `poc-room-*` (from `livekit.toml`) |

The local `lk` CLI may alias this project under any name — on the original setup machine it's
`poc` (see `~/.livekit/cli-config.yaml`), pointing at the same `poc-pf65xo6x` URL. So the skill
**does not hardcode a project or agent id**: it uses your selected `lk` default project and
reads the agent id from `livekit.toml`. Target explicitly with `--project <your-lk-alias>` and
`--id <CA_…>`. These are identifiers, not secrets; `lk` authenticates via the configured
project (`lk project list` / `lk cloud auth`). The skill never handles tokens.

## Commands

| Command | Use |
|---------|-----|
| `lk agent logs` (alias `lk agent tail`) | tail live **runtime** logs from the **latest instance only** (not comprehensive at scale) |
| `lk agent logs --log-type build` | **build** logs — debug a failed deploy |
| `lk agent logs --project <alias> --id <CA_…>` | target a project/agent explicitly (else uses lk default + `livekit.toml`) |
| `lk agent status` | rollout / health of the deployed agent |
| `lk agent versions` | deployed versions (for rollback) |
| `lk agent secrets` | env/secret keys set on the agent (names only) — e.g. `LOGGER_LEVEL`, `AGENT_NAME` |

`lk agent logs` flags are limited to `--id` and `--log-type` — **no** `--since`, `-n`, or
`--tail`. It is a pure streaming tail, which is why the skill's script bounds the capture
itself (time + line budget) and then terminates the process.

## History / search (the CLI can't do it)

`lk agent logs` only tails what arrives *while it runs*, from the latest instance — no
log-search or time-window API. For history or search, use one of:

- **Dashboard → Agent Observability.** [cloud.livekit.io](https://cloud.livekit.io) → select
  the **Aurie** project → **Agents** → open a **session** → **Agent insights**
  tab: synced audio
  playback, transcript, turn-by-turn traces, and logs (agent backend + media server + client).
  There is **no top-level "Logs" tab** — runtime logs live under per-session observability.
- **Build logs** are in the dashboard per version and "stored indefinitely for the most
  recently deployed version".
- **Runtime log forwarding** for durable, searchable history: LiveKit can automatically
  forward all runtime logs to **Datadog, CloudWatch (AWS), Sentry, or New Relic**, configured
  via the project's secrets management. This is the right answer when the team needs real log
  search/retention beyond the live tail. (Note: GCP Cloud Logging is not a built-in target —
  CloudWatch/Datadog/Sentry/New Relic are.)

Docs: <https://docs.livekit.io/deploy/agents/logs/> · Agent observability:
<https://blog.livekit.io/streamline-troubleshooting-with-agent-observability/>

## Log shape

The agent uses stdlib `logging` to stdout: `TIMESTAMP - name - LEVEL - message`, plus the
livekit-agents logger's coloured structured lines (`INFO livekit.agents - <event> {json}`).
Useful markers the triage script keys on:

- Session lifecycle: `registered worker`, `job received`, room ids `poc-room-*`,
  `participant ... joined`, `agent did not join`, `disconnect`.
- Failures: `ERROR`, `Traceback`, `failed to connect`, `retrying`, TTS/STT/LLM init errors.

## PII in these logs (ENG-707)

The agent logs raw user content at INFO — `User message: …`, `Found first_message: …`,
`Metrics payload: {…}`, and metadata dumps. The skill's script **always** redacts these
before output and has no opt-out. It also masks Firebase UIDs to an 8-char prefix and emails
to `[EMAIL_REDACTED]`, per `Compliance/Privacy`. If you read logs in the dashboard instead,
the same PII is visible there — handle accordingly. (Fixing the logging is tracked in ENG-707.)

## Related

- `eng:investigate-gcp-logs` — the IntelligenceService LLM/webhook backend (GKE), a different
  system. Use that, not this, for backend logs.
- ENG-707 — redact agent PII logs. PROD-2638 — create the `Tools/LiveKit` AI Knowledge page.
  PROD-2645 — LiveKit deployment consolidation. (Note: the *deployed* agent dispatches as
  `lk-voice-agent`, which matches what the iOS app dispatches — so there's no prod AGENT_NAME
  mismatch. The only stale value is the `voice-agent` default in the agent's `.env.sample`,
  which a local `make dev` run picks up; the deployed config is correct. Check dispatch names
  with `lk agent list`, env keys with `lk agent secrets`.)
