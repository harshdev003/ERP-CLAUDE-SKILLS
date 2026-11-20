---
name: search-bugsnag-by-user
description: >
  Search the Bugsnag error signatures a given Firebase user id hit, newest first — org-wide
  across the 12 aura-health projects by default, or scoped to one with --project. Read-only
  against the Bugsnag Data Access API v2. This is the "which errors did this user see?"
  entrypoint; results chain into eng:investigate-bugsnag-errors for drilling into a specific
  error. Trigger when the user says "errors for user {uid}", "search bugsnag for user {uid}",
  "what is this user hitting in bugsnag", "bugsnag for firebase uid {uid}", "which bugsnag
  errors did {uid} see", or pairs a Firebase UID with Bugsnag. Do NOT use to drill into one
  error or trace an event (that's eng:investigate-bugsnag-errors), for a cross-project digest
  (eng:report-bugsnag-errors), or to resolve errors (eng:resolve-bugsnag-errors).

metadata:
  version: "3.3.0"
knowledge:
  reads:
    - Guide/Index    # baseline routing aid (universal)
    - Tools/Bugsnag  # auth shape, project inventory, app-family routing, filter fields, PII rules, snake_case response fields
---

# search-bugsnag-by-user

Find the Bugsnag error signatures one Firebase user id hit, across the 12 `aura-health` projects. Read-only against the [Data Access API v2](https://developer.smartbear.com/bugsnag/docs/data-access).

## Goal

Answer *"which errors did this user see?"* in one command — across web and mobile — without opening Bugsnag project by project. It's the user-pivot companion to [`eng:investigate-bugsnag-errors`](../investigate-bugsnag-errors/SKILL.md) (drill into one error / trace an event): every result row ends with an `investigate bugsnag --error <id>` hint, so you pick a signature and hand off to investigate for focus → trace → resolve.

## Inputs

| Flag | Default | Meaning |
|---|---|---|
| `--user <firebase-uid>` | *(required)* | The Firebase user id to search for |
| `--project <slug>` | — | Limit to one project (fuzzy match, so `userweb` → `aura-userweb`). Default: all 12 projects (org-wide) |
| `--since <dur>` | `30d` | Window as Bugsnag duration shorthand: `1h`, `24h`, `7d`, `30d` |
| `--limit <n>` | `10` | Cap the digest at N error signatures |

## Run

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/search-bugsnag-by-user/scripts/search.py \
  --user <firebase-uid> [--project <slug>] [--since <dur>] [--limit <n>]
```

## Output

A digest under a header `Errors for user <uid> across <N> projects since <since>` (or `in \`<project>\`` when scoped), one block per error signature: app family, error class, message, `project` / `events` / `users` / `last_seen`, and a `→ Drill in: investigate bugsnag --error <id>` hint. The per-row `events` / `users` are the error's overall totals (blast radius), not just this user's slice — Bugsnag returns the error's aggregate counts even when the event list is user-filtered. 0 results print a "0 errors for user `<uid>`…" line rather than erroring.

## How it works

- **Filter shape.** Built on the `user.id` filter on `GET /projects/<id>/errors`, bounded by `event.since`. The script never hand-builds a filter param — `bugsnag.py`'s `build_filters()` validates each key against the project's `event_fields` and picks the wire shape, because **Bugsnag ignores an unknown filter key and returns the unfiltered set with HTTP 200**. Field list, the two shapes, and the repeated-pair last-wins trap: `Tools/Bugsnag` in AI Knowledge (canonical).
- **Org-wide fan-out.** With no `--project`, one API call per project (≈12), merged and sorted by `last_seen` desc. It's sequential and a touch slow (tens of seconds); any project that rate-limits or fails transiently is skipped rather than failing the run. Scope with `--project` when you know the surface.

## PII guardrail

Read-only, same posture as the investigate skill's triage/focus modes: only extracted error-signature fields appear (class, message, counts, timestamps, project). No `request` / `user` / `device` / `metadata` blocks are echoed. Aurie projects (`ai-aurie-ios`, `ai-aurie-macos`, `aiweb`) carry user-conversation text in metadata, but this skill never reaches into it. The supplied `user.id` is shown because the user provided it.

## Setup (one-time, per user)

1. **Bugsnag personal auth token** — self-serve at <https://app.bugsnag.com/settings/my-account/auth-tokens> → Personal auth tokens.
2. **Store it** as `BUGSNAG_TOKEN` under `env` in `~/.claude/settings.json`. Restart Claude Code.
3. **Python deps** — `python3 -m pip install --user -r ${CLAUDE_PLUGIN_ROOT}/skills/search-bugsnag-by-user/scripts/requirements.txt`. Just `requests`.

No MCP fallback for Bugsnag — a missing `BUGSNAG_TOKEN` is a hard fail with the create-token message.

**Cowork caveat ([PROD-1305](https://linear.app/aura-health/issue/PROD-1305)):** `mcp__workspace__bash` strips host env. Inside Cowork, paste-export once at session start:

```bash
export BUGSNAG_TOKEN='<paste from host ~/.claude/settings.json>'
```

## Edge cases

- **No errors for the user (± `--project`)** → prints "0 errors for user `<uid>`…" with a suggestion to widen `--since` or recheck the uid. A user with no recent errors is the common case, not a failure.
- **Org-wide search is slower** → one API call per project (≈12); scope with `--project` to speed it up.
- **Auth fails (401)** → the API client prints the specific failure with the one-line fix.
- **Project slug typo** → fuzzy-match; if no single match, the available slugs are listed.
- **Re-running** → safe (read-only).

## References

- Companion (drill-in target): [`eng:investigate-bugsnag-errors`](../investigate-bugsnag-errors/SKILL.md) — focus / trace / resolve a specific error.
- [Tools/Bugsnag](https://www.notion.so/35a75e23af2081b1a021d7b2b8999e2f) (AI Knowledge).
- [Bugsnag Data Access API v2](https://developer.smartbear.com/bugsnag/docs/data-access).
