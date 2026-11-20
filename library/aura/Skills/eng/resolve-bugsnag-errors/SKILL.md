---
name: resolve-bugsnag-errors
description: >
  Resolve, reopen, or ignore a Bugsnag error — and optionally add a
  comment — via the Bugsnag Data Access API. This is the WRITE side of
  the Bugsnag skill set (investigate / report / analyze are read-only).
  Safe by construction: a run is a DRY RUN unless --confirm is passed,
  and Claude must show the before-state and get explicit approval before
  any confirmed write. Trigger when the user says "resolve this bugsnag
  error", "mark this bugsnag error fixed", "reopen this bugsnag error",
  "ignore this bugsnag error", "close this error in bugsnag", or
  "comment on this bugsnag error", with a specific error id or after an
  investigate focus. Do NOT use to read/triage errors (that's
  eng:investigate-bugsnag-errors), for digests (eng:report-bugsnag-errors),
  or for release analysis (eng:analyze-bugsnag-releases).
metadata:
  version: "3.3.0"
knowledge:
  reads:
    - Guide/Index    # baseline routing aid (universal)
    - Tools/Bugsnag  # auth shape, project inventory, app-family routing, filter fields, PII rules, snake_case response fields
---

# resolve-bugsnag-errors

The **write side** of the Bugsnag skill set: resolve / reopen / ignore an error and add comments, via the [Data Access API v2](https://developer.smartbear.com/bugsnag/docs/data-access). Every other Bugsnag skill is read-only; this one mutates production state, so it is gated.

## Operations

| Operation | API | Effect |
|---|---|---|
| `fix` | `PATCH /errors/<id>` `{"operation":"fix"}` | Resolve — status `open` → `fixed` |
| `open` | `PATCH /errors/<id>` `{"operation":"open"}` | Reopen — status → `open` |
| `ignore` | `PATCH /errors/<id>` `{"operation":"ignore"}` | Mute — status → `ignored` |
| comment | `POST /errors/<id>/comments` `{"message":"…"}` | Add a comment |

Snooze (with reopen rules) and bulk multi-error operations are out of scope for now.

## Safety model — read this before running with `--confirm`

This skill is the only Bugsnag skill that writes. Two layers protect against accidental mutation:

1. **Dry run by default.** Without `--confirm`, `resolve.py` fetches the error, prints its current status, and prints the **exact** request(s) it *would* send — then exits without mutating anything.
2. **Explicit approval before a confirmed write.** Before ever running with `--confirm`, **show the user the dry-run output (current status + intended change) and get a clear go-ahead.** Never resolve/ignore/reopen an error without the user explicitly approving that specific error + operation. Resolving or ignoring a live error hides it from triage and can trigger reopen rules — treat it as a real production change.

## Inputs

| Flag | Required | Meaning |
|---|---|---|
| `--error <id>` | yes | The error to operate on |
| `--operation <fix\|open\|ignore>` | one of operation/comment | The state change |
| `--comment "<text>"` | one of operation/comment | A comment to add (no PII) |
| `--confirm` | — | Actually send the write(s). Omit for a dry run |

## Run

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/resolve-bugsnag-errors/scripts/resolve.py \
  --error <id> [--operation fix|open|ignore] [--comment "…"] [--confirm]
```

Recommended flow:

1. Run **without** `--confirm` → relay the dry-run (current status + the requests it would send) to the user.
2. On explicit approval, re-run the **same** command **with** `--confirm`. The script applies the write(s) and prints the before → after status.

Chains naturally from [`eng:investigate-bugsnag-errors`](../investigate-bugsnag-errors/SKILL.md): after a focus, "resolve this error" → `resolve bugsnag --error <id> --operation fix`.

## Setup (one-time, per user)

1. **Bugsnag personal auth token with write access** — self-serve at <https://app.bugsnag.com/settings/my-account/auth-tokens>. The token must be able to update errors (the read-only investigate/report skills use the same `BUGSNAG_TOKEN`; for this skill it needs write scope). A `403 Forbidden` on a confirmed write means the token lacks update permission.
2. **Store it** as `BUGSNAG_TOKEN` under `env` in `~/.claude/settings.json`. Restart Claude Code.
3. **Python deps** — `python3 -m pip install --user -r ${CLAUDE_PLUGIN_ROOT}/skills/resolve-bugsnag-errors/scripts/requirements.txt`. Just `requests`.

No MCP fallback — a missing `BUGSNAG_TOKEN` is a hard fail with the create-token message.

**Cowork caveat ([PROD-1305](https://linear.app/aura-health/issue/PROD-1305)):** `mcp__workspace__bash` strips host env. Inside Cowork, paste-export once at session start:

```bash
export BUGSNAG_TOKEN='<paste from host ~/.claude/settings.json>'
```

## PII guardrail

The skill shows only the error signature + status — no event / user / device / request / metadata payloads. **Comments are an external surface**: don't put end-user PII (full email, name, free-text user content) in a `--comment`; reference a UID prefix or an internal ticket instead.

## Edge cases

- **No `--operation` and no `--comment`** → exits with "nothing to do" (code 2). One of the two is required.
- **Error id not found (404)** → printed clearly (likely a typo or wrong org).
- **Token lacks write scope (403)** → printed with the fix (use a token with error-update permission).
- **Dry run is always safe** and idempotent. A confirmed `fix` on an already-`fixed` error is a no-op at Bugsnag; reopen with `--operation open` to revert.

## References

- Sibling (read-side entrypoint): [`eng:investigate-bugsnag-errors`](../investigate-bugsnag-errors/SKILL.md) — focus → resolve handoff.
- [Tools/Bugsnag](https://www.notion.so/35a75e23af2081b1a021d7b2b8999e2f) (AI Knowledge).
- [Bugsnag Data Access API v2](https://developer.smartbear.com/bugsnag/docs/data-access).
