---
name: deploy-agent
description: Deploy the LiveKit voice agent (aurie-voice-agent) to LiveKit Cloud. Use when the user wants to deploy, ship, release, roll out, or roll back the agent. Covers preflight (lint/test), secret config, deploying via a v* git tag (CircleCI) or `lk agent deploy`, creating a new agent, verifying, and rollback. For the secret catalog (required + optional), use the manage-agent-secrets skill.
---

# Deploy the LiveKit voice agent

Deploys to **LiveKit Cloud** via `livekit.toml` (entrypoint `src/main.py`). During the lk→aurie
rebrand there are two agents: the **new `aurie-voice-agent`** (serves `aurie-room-*`) and the
**old `lk-voice-agent`** (serves legacy `poc-room-*`, retired). Don't break the old one.

Prereqs: the `lk` CLI authenticated to the LiveKit Cloud project (`lk cloud auth` / `lk project list`).

## 1. Preflight

```bash
make lint && make test          # must be green (CI gates these too)
```

Confirm `livekit.toml`: `entrypoint = "src/main.py"`, the room glob is `aurie-room-*`, and `[agent].id`
points at the agent you intend to deploy (see step 3 for new vs existing).

## 2. Secrets

Make sure the target agent's secrets are set (required + any optional you want) **before** deploying —
use the **`manage-agent-secrets`** skill. A missing required secret makes the worker `sys.exit(1)`
at startup (fail-fast). Recommended in prod: `LOGGER_USE_JSON=true`.

## 3. Deploy

**Production (preferred) — tag a release; CircleCI runs tests then deploys:**
```bash
git tag v0.2.0 && git push origin v0.2.0     # CI `build_test_deploy`: test -> lk agent deploy (v* tags only)
```

**Manual — from the repo root (has `livekit.toml`):**
```bash
lk agent deploy            # == npm run deploy
```

**First deploy of a NEW agent (e.g. standing up `aurie-voice-agent` separately from the old one):**
```bash
lk agent create            # creates a new agent id; set it as [agent].id in livekit.toml
lk agent deploy
```
Keep the old `lk-voice-agent` running until traffic has moved.

## 4. Verify

```bash
lk agent status            # version live & healthy
lk agent logs              # tail logs (or the eng:investigate-livekit-logs skill)
```
Smoke-test with the local-QA runbook (`docs/local-qa.md`): `console`/`dev` with `AGENT_METADATA`.
Check **Agent insights** in the LiveKit Cloud dashboard for session metrics.

## 5. Rollback

```bash
lk agent versions          # list deployed versions
lk agent rollback          # revert to the previous version
```

## Notes
- CI deploy only fires on `v*` tags and **requires the test job** — a red build blocks deploy.
- `lk agent update-secrets` and `lk agent deploy` both **restart** the agent (brief session drop).
- Never put secret values in commits, logs, or chat — reference by env-var name (see `security` rule).
