---
name: manage-agent-secrets
description: List, set, or update the deployed LiveKit agent's secrets in LiveKit Cloud (`lk agent secrets` / `lk agent update-secrets`). Use when configuring a new agent, rotating a key, enabling a feature (e.g. structured JSON logs), or checking which secrets are set. Documents the full catalog — which secrets are REQUIRED vs OPTIONAL — and the naming gotchas. Pairs with the deploy-agent skill.
---

# Manage agent secrets

Deployed secrets live in **LiveKit Cloud**, not the repo (`.env` is gitignored; only `.env.sample`
is committed). Manage them with the `lk` CLI.

```bash
lk agent secrets --id <AGENT_ID>                                   # list (names only)
lk agent update-secrets --id <AGENT_ID> --secrets-file .env --overwrite --ignore-empty-secrets   # push (RESTARTS the agent)
```

**Never** paste real secret values into chat, logs, or commits — reference them by env-var name.
Rotate anything that leaks; never reuse a compromised key. Updating secrets restarts the agent
(brief session drop).

## Updating deployed secrets — push the whole `.env`, never one key

The local `.env` is the working copy of the deployed secret set. To change a value, edit it in
`.env` and push the **entire file**. Do not push a single `--secrets KEY=VALUE` — the file is the
record of what is deployed, and a one-off push drifts the two apart until the next full push
silently reverts it.

```bash
lk agent update-secrets --id <AGENT_ID> --secrets-file .env --overwrite --ignore-empty-secrets
```

- `--overwrite` — required to change keys that already exist. Without it existing keys are not
  updated.
- `--ignore-empty-secrets` — required. `.env` carries intentionally empty keys (e.g.
  `TTS_FALLBACK_PROVIDER=`, `EXPERIMENT_NAMES=`); without the flag the command **fails** on them.
  With it they are skipped and the CLI reports `Skipped N empty secret(s): …` — that line is
  expected, not an error.
- `LIVEKIT_URL` / `LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET` are in `.env` for local runs but are
  **platform-injected** on LiveKit Cloud and never appear in `lk agent secrets`. Their absence from
  the deployed list is normal.
- **The push restarts the agent** and drops any live voice sessions. Check for active rooms in the
  LiveKit dashboard first, and prefer a quiet window.

**Pre-flight: prove nothing deployed will be lost.** Because the push replaces the set, diff key
names before pushing. Anything deployed that is missing from `.env` would be dropped.

```bash
lk agent secrets --id <AGENT_ID> | grep -oE "^│ [A-Z][A-Z0-9_]+ " | tr -d '│ ' | sort > /tmp/deployed.txt
grep -oE "^[A-Z][A-Z0-9_]+=" .env | tr -d '=' | sort > /tmp/env.txt
comm -23 /tmp/deployed.txt /tmp/env.txt    # must be empty (deployed but not in .env = LOSS)
```

Verify after: `lk agent secrets --id <AGENT_ID>` shows the changed keys with fresh timestamps and
the same key count; `lk agent status --id <AGENT_ID>` shows `Running`.

## Required (the worker `sys.exit(1)`s at startup without these — see `Settings.required_config_errors`)

| Secret | When |
|--------|------|
| `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` | always |
| **active TTS provider key** | by `TTS_DEFAULT_PROVIDER`: `ELEVENLABS_API_KEY` \| `OPENAI_API_KEY` \| `CARTESIA_API_KEY` \| `INWORLD_API_KEY` (the LLM does **not** use `OPENAI_API_KEY` — it routes through Aura) |
| `AURA_API_URL`, `AURA_API_KEY` | always (the LLM routes through the Aura backend) |

## Optional (have defaults, or gate a feature/provider)

| Secret | Default / purpose |
|--------|-------------------|
| `LOGGER_LEVEL` | `INFO` (formatting is owned by the LiveKit runtime — no JSON toggle) |
| `TTS_DEFAULT_PROVIDER` | `elevenlabs`; selects the active provider |
| `AGENT_NAME` | `aurie-voice-agent` (the dispatch identity / worker name) |
| `AGENT_MIN_ENDPOINTING_DELAY`, other `AGENT_*` | session/latency tuning (the greeting is metadata-only, no env var) |
| `AGENT_USER_AWAY_TIMEOUT` | `120.0`; idle seconds before the user is marked `away` and the session ends |
| `AGENT_AWAY_CHECK_IN_ENABLED` | `true`; when on, the agent speaks one of `_AWAY_CHECK_IN_MESSAGES` (hardcoded in `src/sessions/session_manager.py`) on going away, then closes after `AGENT_AWAY_GRACE` unless the user answers. **Disable during any A/B against ElevenLabs** — ElevenLabs has no equivalent, so the check-in is an arm-asymmetric difference in how sessions end. |
| `AGENT_AWAY_GRACE` | `10.0`; seconds after the check-in before closing. Inert while `AGENT_AWAY_CHECK_IN_ENABLED=false` (that path closes at the timeout with no wait). |
| `ELEVENLABS_VOICE_ID` / `ELEVENLABS_MODEL` | ElevenLabs voice/model |
| `CARTESIA_VOICE_ID` | Cartesia voice |
| `INWORLD_VOICE_ID` / `INWORLD_MODEL` / `INWORLD_SAMPLE_RATE` | Inworld voice/model |
| `OPENAI_TTS_VOICE` | OpenAI TTS voice |
| non-active TTS provider keys | only if you switch providers per-session via metadata |
| `LLM_MODEL`, `STT_MODEL`, `STT_LANGUAGE` | model/STT tuning |

`.env.sample` is the source of truth for the full var list and defaults; keep it in sync.

## Naming gotchas
- ElevenLabs key is **`ELEVENLABS_API_KEY`** (consistent with `ELEVENLABS_VOICE_ID`/`_MODEL`), not the legacy `ELEVEN_API_KEY`.

## New-agent setup (aurie-voice-agent)
When configuring the new agent's secrets fresh, set the required set above + `LOGGER_USE_JSON=true`,
and use the canonical names (e.g. `ELEVENLABS_API_KEY`). Don't carry over the old
agent's redundant/misnamed secrets.
