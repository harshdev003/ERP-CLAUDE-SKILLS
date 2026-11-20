---
name: investigate-circleci
description: >
  Investigate a CircleCI CI failure for AuraConsumerApp. Given a PR number,
  workflow ID, or CircleCI URL, fetches the failing job's log, classifies
  the failure as infra (transient, safe to rerun) vs real (code or config
  defect that needs a fix), and summarizes the next step. Trigger when the
  user says "investigate circleci", "check CI", "why did CI fail",
  "what failed in CircleCI", "look at the CircleCI logs", pastes a
  https://app.circleci.com URL, or mentions a PR number alongside CI failure.
  Use eng:rerun-circleci to trigger a rerun after confirming infra-failure.
metadata:
  version: "3.1.0"
knowledge:
  reads:
    - Guide/Index      # baseline routing aid (universal)
    - Tools/CircleCI   # project slug, token setup, personal-vs-project token, v1.1/v2 API basics
    - Tools/GitHub     # gh CLI: resolve a PR's head branch for --pr pipeline lookups
---

# investigate-circleci

Read-side CI debugging skill for AuraConsumerApp CircleCI pipelines. Given a PR # or workflow URL, reads the failing job log via the CircleCI API, classifies infra-vs-real failure, and summarizes the next step. Read-only — use [`eng:rerun-circleci`](../rerun-circleci/SKILL.md) to trigger a rerun.

## Goal

Replace the loop of "open CircleCI → find the failed job → read the raw log → decide if it's worth a rerun" with one CLI invocation that returns a structured digest and a recommended action.

## Inputs

One of:

- **PR number** — `--pr 1234` → resolves the latest pipeline for that PR's branch via the v2 API
- **Workflow ID** — `--workflow <uuid>` → fetches directly
- **CircleCI URL** — `--url https://app.circleci.com/pipelines/gh/AuraHealth/AuraConsumerApp/…` → parsed automatically
- **Build number** (v1.1) — `--build 4597` → fetches via `GET /api/v1.1/project/gh/AuraHealth/AuraConsumerApp/{build}`

Default project slug: `gh/AuraHealth/AuraConsumerApp`. Override with `--project <slug>`.

## Workflow

### Run the script

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/investigate-circleci/scripts/investigate.py <flags>
```

### Output shape

```
CircleCI Investigation — PR #<n> / Workflow <id>
=================================================
Pipeline: #<pipeline_num>  Branch: <branch>  Trigger: <push|api|…>
Workflow: <workflow_name>  Status: failed  Started: <timestamp>

Failed jobs:
  - <job_name>  (#<build_num>)  Duration: <Xs>

--- Job: <job_name> ---
Steps:
  [PASS] Checkout code
  [PASS] Install dependencies
  [FAIL] Run SwiftLint

Failure output (last 50 lines):
  <log tail>

Classification: <INFRA | REAL>
Reason: <one-line rationale>

→ Next step: <recommended action>
```

### Classification logic

| Signal | Classification |
|---|---|
| Duration ≤ 5s for a job that normally takes >30s | INFRA |
| `"context deadline exceeded"`, `"i/o timeout"`, `"connection reset"` in log | INFRA |
| `"resource temporarily unavailable"` | INFRA |
| xcodebuild exit code 70 (simulator boot fail) | INFRA |
| A download answered `500` / `502` / `503` / `504` / `429` (installer, package index, release CDN) | INFRA |
| `curl: (6)` `(7)` `(28)` `(35)` `(52)` `(55)` `(56)` — DNS, connect, timeout, TLS, empty reply, send/recv | INFRA |
| SwiftLint violations named in log | REAL |
| Test assertion failures, compile errors | REAL |
| Exit code non-zero with substantive log output | REAL |
| Ambiguous / can't read log | UNKNOWN (surface raw tail) |

INFRA → next step: `rerun-circleci --workflow <id>` (or `--pr <n>`)
REAL → next step: description of what to fix
UNKNOWN → next step: link to the CircleCI log URL

The INFRA list is scanned **before** the REAL list, and that ordering carries weight: the REAL list
matches `error: ` as a bare substring, which nearly every failed download also prints, so a signal
placed in the wrong list is silently outvoted. Full table and the reasoning behind the download
signals — including why they key on the HTTP status rather than curl's exit 22, which would swallow a
404 — in [`references/classification.md`](references/classification.md).

## Setup (one-time, per user)

**1. CircleCI personal API token.** Create at <https://app.circleci.com/settings/user/tokens> → Personal API Tokens. Must be a **personal** token — project tokens are rejected by the v2 rerun endpoint. (Read paths work with either token type.)

**2. Store the token.** Add to `~/.claude/settings.json` under the `env` block as `CIRCLE_TOKEN`. Restart Claude Code. Fallback names accepted: `CIRCLECI_TOKEN`, `CIRCLE_CI_TOKEN`, `CIRCLECI_API_TOKEN`.

**3. Python deps.** `pip install requests`.

There is no CircleCI MCP — a missing token is a hard fail with a "create a personal token" message.

**Cowork caveat ([PROD-1305](https://linear.app/aura-health/issue/PROD-1305)):** `mcp__workspace__bash` strips host env. Inside Cowork, paste-export `CIRCLE_TOKEN` once at session start:

```bash
export CIRCLE_TOKEN='<paste from host ~/.claude/settings.json>'
```

## Edge cases

- **PR has multiple pipelines** → uses the most recent (highest pipeline number).
- **No failed jobs in workflow** → "Workflow status is `<status>` but no failed jobs found — may have been rerun already."
- **Auth 401** → print token-creation instructions, not a raw traceback.
- **Log too large** → tail the last 200 lines; note that the full log is at the CircleCI URL.
- **Pipeline not found for PR** → "No pipeline found for branch `<branch>` — check if CI ran for this PR."

## References

- [`references/api-recipes.md`](references/api-recipes.md) — endpoint reference, auth header, pagination.
- [`references/classification.md`](references/classification.md) — full infra-signal table with examples.
- [PROD-2978](https://linear.app/aura-health/issue/PROD-2978) — design spec.
- [PROD-2976](https://linear.app/aura-health/issue/PROD-2976) — parent issue (CircleCI integration).
