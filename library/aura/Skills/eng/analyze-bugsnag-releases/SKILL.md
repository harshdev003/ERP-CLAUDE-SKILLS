---
name: analyze-bugsnag-releases
description: >
  Per-release error / regression view for the aura-health Bugsnag
  projects. Two modes: an org rollup (latest release per project, worst
  regressions first) and a per-project recent-releases table (version,
  time, stage, errors introduced, errors seen). Answers "did this
  release introduce new errors?" / "which release introduced the
  regressions?". Read-only against the Bugsnag Data Access API v2.
  Trigger when the user says "bugsnag releases", "bugsnag release
  health", "which release introduced these errors", "release
  regressions in bugsnag", "errors introduced per release", "analyze
  bugsnag releases", or asks about a project's recent releases. Note:
  crash-free / stability rate is NOT available — Bugsnag session
  tracking is off across the projects, so this skill reports per-release
  error counts, not crash-free percentages. Do NOT use to drill into a
  single error (eng:investigate-bugsnag-errors) or for the cross-error
  digest (eng:report-bugsnag-errors).
metadata:
  version: "3.3.0"
knowledge:
  reads:
    - Guide/Index    # baseline routing aid (universal)
    - Tools/Bugsnag  # auth shape, project inventory, app-family routing, filter fields, PII rules, snake_case response fields
---

# analyze-bugsnag-releases

Per-release error / regression view across the 12 `aura-health` Bugsnag projects, from the [Data Access API v2](https://developer.smartbear.com/bugsnag/docs/data-access) `releases` endpoint. Read-only.

## Goal

Answer *"did this release introduce new errors, and which release introduced the regressions?"* using Bugsnag's per-release `errors_introduced_count` (errors first seen in that release) and `errors_seen_count`. Sits alongside [`eng:investigate-bugsnag-errors`](../investigate-bugsnag-errors/SKILL.md) (drill into one error) and [`eng:report-bugsnag-errors`](../report-bugsnag-errors/SKILL.md) (cross-error digest) — this one is organised by *release*.

## Why not crash-free stability?

Bugsnag's headline "stability" metric is the crash-free **session** rate. That requires the SDK to report sessions — which the aura-health projects **don't** today: every `total_sessions_count`, `unhandled_sessions_count`, and `accumulative_daily_users_*` field comes back null across all 12 projects. So crash-free rate isn't computable. The releases endpoint's **error** counts (`errors_introduced_count` / `errors_seen_count`) *are* populated everywhere, and are a real release-health signal — that's what this skill uses. (Enabling session tracking in the SDKs is separate infra work; if it lands, a stability mode can be added here.)

## Inputs

| Flag | Default | Meaning |
|---|---|---|
| `--project <slug>` | — | Detailed recent-releases table for one project (fuzzy match). Omit for the org rollup (latest release per project) |
| `--limit <n>` | `10` | Detail mode: number of recent releases to show |
| `--stage <name>` | — | Filter to a release stage (`production`, `PROD`, `staging`, …), case-insensitive substring. Scans deeper (up to 100 releases) so matching releases aren't missed behind other-stage ones |

The org rollup runs by default when `--project` is omitted.

## Run the query

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-bugsnag-releases/scripts/analyze.py <flags>
```

## Modes

### Org rollup (default)

The latest release of every project, one line each, sorted by **errors introduced in that latest release** (worst first). Each line: project · app family · version · date · stage · `introduced=` · `seen=`. A `⚠️` marks any latest release that introduced errors. Ends with a hint to drill into one project.

### Per-project detail (`--project <slug>`)

The N most recent releases for one project, newest first: version · date · stage · `introduced=` · `seen=`. The release that introduced the most new errors is flagged `⚠️ most regressions`. Ends with a `investigate bugsnag --project <slug>` hint to see the errors currently active in that project.

## Caveats

- **`errors_introduced_count`** = errors whose first occurrence falls in that release (regressions the release added). **`errors_seen_count`** = distinct error signatures seen in the release. Both are Bugsnag's own per-release counts.
- **Multiple records per version.** Bugsnag can emit several release records for one `app_version` (different build sources / stages); they appear as separate rows. That's expected, not a dedupe bug.
- **`per_page` cap.** The releases endpoint caps `per_page` at 10 ("Per page must be less than or equal to 10"); the client paginates to reach `--limit`.
- **Cross-project rollup is sequential** and skips any project that fails transiently, same as the other Bugsnag skills.

## PII guardrail

Read-only and release-level only — version, timestamps, stage, error counts. No event / user / device / request / metadata payloads are fetched or shown.

## Setup (one-time, per user)

1. **Bugsnag personal auth token** — self-serve at <https://app.bugsnag.com/settings/my-account/auth-tokens> → Personal auth tokens.
2. **Store it** as `BUGSNAG_TOKEN` under `env` in `~/.claude/settings.json`. Restart Claude Code.
3. **Python deps** — `python3 -m pip install --user -r ${CLAUDE_PLUGIN_ROOT}/skills/analyze-bugsnag-releases/scripts/requirements.txt`. Just `requests`.

No MCP fallback for Bugsnag — a missing `BUGSNAG_TOKEN` is a hard fail with the create-token message.

**Cowork caveat ([PROD-1305](https://linear.app/aura-health/issue/PROD-1305)):** `mcp__workspace__bash` strips host env. Inside Cowork, paste-export once at session start:

```bash
export BUGSNAG_TOKEN='<paste from host ~/.claude/settings.json>'
```

## Edge cases

- **No releases for the scope (± `--stage`)** → prints a "no releases found" line with a suggestion to drop `--stage` or try a single `--project`. Doesn't error out.
- **Auth fails (401)** → the API client prints the specific failure with the one-line fix.
- **Project slug typo** → fuzzy-match; if no single match, the available slugs are listed.
- **Re-running** → safe (read-only).

## References

- Siblings: [`eng:investigate-bugsnag-errors`](../investigate-bugsnag-errors/SKILL.md), [`eng:report-bugsnag-errors`](../report-bugsnag-errors/SKILL.md).
- [Tools/Bugsnag](https://www.notion.so/35a75e23af2081b1a021d7b2b8999e2f) (AI Knowledge).
- [Bugsnag Data Access API v2](https://developer.smartbear.com/bugsnag/docs/data-access).
