---
name: rerun-circleci
description: >
  Rerun a failed CircleCI workflow for AuraConsumerApp using from_failed
  mode via the v2 API. Given a workflow ID, PR number, or CircleCI URL,
  triggers a rerun of only the failed jobs. Requires a personal CircleCI
  token — project tokens are rejected by the v2 rerun API. Trigger when
  the user says "rerun circleci", "rerun CI", "trigger a rerun", "retry
  the failing jobs", or after eng:investigate-circleci classifies a failure
  as INFRA. Always asks for user confirmation before POSTing the rerun.
  Use eng:investigate-circleci first if unsure whether the failure is
  infra (safe to rerun) or a real code defect.
metadata:
  version: "3.0.0"
knowledge:
  reads:
    - Guide/Index      # baseline routing aid (universal)
    - Tools/CircleCI   # project slug, token setup, v1.1/v2 API basics
    - Tools/GitHub     # gh CLI: resolve a PR's head branch for --pr pipeline lookups
---

# rerun-circleci

Write-side CI skill for AuraConsumerApp CircleCI workflows. Triggers a `from_failed` rerun of a workflow via the v2 API after user confirmation. Read [`eng:investigate-circleci`](../investigate-circleci/SKILL.md) first to confirm the failure is infra before rerurning.

## Goal

Close the loop for infra-class CI failures without leaving the terminal. `investigate-circleci` → classify → `rerun-circleci` → done.

## Inputs

One of:

- **Workflow ID** — `--workflow <uuid>` → reruns directly
- **PR number** — `--pr 1234` → resolves the latest failed workflow for that PR, then reruns
- **CircleCI URL** — `--url https://app.circleci.com/pipelines/…` → parsed to extract workflow ID

Default project slug: `gh/AuraHealth/AuraConsumerApp`. Override with `--project <slug>`.

## Workflow

**Always confirm before posting.** The script dry-runs by default — shows what will be rerun, then prompts the user. Pass `--confirm` to skip the prompt (use only in non-interactive pipelines).

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/rerun-circleci/scripts/rerun.py --workflow <id>
# → prints confirmation summary, waits for user approval
# → on approval, POSTs to /api/v2/workflow/{id}/rerun

python3 ${CLAUDE_PLUGIN_ROOT}/skills/rerun-circleci/scripts/rerun.py --pr 1234
python3 ${CLAUDE_PLUGIN_ROOT}/skills/rerun-circleci/scripts/rerun.py --url https://app.circleci.com/...
python3 ${CLAUDE_PLUGIN_ROOT}/skills/rerun-circleci/scripts/rerun.py --workflow <id> --confirm
```

## Output

```
Rerun summary
=============
Project:  gh/AuraHealth/AuraConsumerApp
Workflow: fd392e6d-fc... (from_failed=true)
Jobs to retry: lint (failed)

Confirm rerun? [y/N]: y
Rerun triggered — new workflow ID: <new-id>
-> Next step: investigate-circleci --workflow <new-id>   (check the result)
```

## Setup (one-time, per user)

**Requires a personal token** — the v2 rerun endpoint returns 403 for project tokens. See [`eng:investigate-circleci`](../investigate-circleci/SKILL.md) → Setup for the full walkthrough.

Token stored as `CIRCLE_TOKEN` in `~/.claude/settings.json` env. Fallbacks: `CIRCLECI_TOKEN`, `CIRCLE_CI_TOKEN`, `CIRCLECI_API_TOKEN`.

**Cowork caveat ([PROD-1305](https://linear.app/aura-health/issue/PROD-1305)):** paste-export `CIRCLE_TOKEN` at session start.

## Edge cases

- **Token is a project token (403)** → print "This endpoint requires a personal token, not a project token. See https://app.circleci.com/settings/user/tokens." and exit 1.
- **Workflow not found** → clear error with the workflow ID.
- **Workflow already succeeded** → warn and do not rerun.
- **Concurrent rerun already running** → CircleCI returns 409 — surface the error message.

## References

- [`../investigate-circleci/references/api-recipes.md`](../investigate-circleci/references/api-recipes.md) — shared endpoint reference.
- [PROD-2979](https://linear.app/aura-health/issue/PROD-2979) — design spec.
- [PROD-2976](https://linear.app/aura-health/issue/PROD-2976) — parent issue.
