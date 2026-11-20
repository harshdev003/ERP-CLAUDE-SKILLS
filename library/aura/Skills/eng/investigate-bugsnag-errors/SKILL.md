---
name: investigate-bugsnag-errors
description: >
  Investigate Bugsnag errors from inside Claude Code via a chained workflow: triage (what's
  broken in this project or across the org?), focus (drill into one error), and trace (full
  event payload). Follows Bugsnag URLs — paste any app.bugsnag.com error link and the skill
  parses the project, error and event ids out of it, fetches the error via the Data Access
  API, and returns a structured digest. Multi-project aware, with app-family routing that
  selects the right stacktrace formatter and records the suspect repo for downstream
  investigation. Trigger when the user pastes a Bugsnag URL, says "investigate bugsnag",
  "what's broken in {project}", "top errors in {project}", "bugsnag triage", "trace event
  {id}", "follow this Bugsnag link", or asks about a specific Bugsnag error or error class. To
  search the errors a given user id hit, use `eng:search-bugsnag-by-user`. Do NOT use for
  setting up projects, modifying alerts, or resolving errors via the API — those go through
  the Bugsnag UI.

metadata:
  version: "3.5.0"
knowledge:
  reads:
    - Guide/Index    # baseline routing aid (universal)
    - Tools/Bugsnag  # auth shape, project inventory, app-family routing, filter fields, PII rules, snake_case response fields
    - Tools/Slack    # channel directory + Post format footer spec for Mode 4b Slack reply
---

# investigate-bugsnag-errors

Read-side debugging skill for Bugsnag errors across the 12 `aura-health` projects. Chains three modes — **triage → focus → trace** — and follows Bugsnag UI URLs. Read-only against the [Data Access API v2](https://developer.smartbear.com/bugsnag/docs/data-access). To search the errors a specific user id hit, use the sibling [`eng:search-bugsnag-by-user`](../search-bugsnag-by-user/SKILL.md).

## Goal

Replace the manual loop of "open Bugsnag → eyeball the error → copy the project + error ID → drill into one event → cross-reference recent commits" with a chained set of CLI prompts that hand off to each other. Every output ends with the exact command for the next step, so investigation never stalls because the syntax was forgotten.

## Inputs

The user provides one of:

- **A Bugsnag error URL** — `https://app.bugsnag.com/aura-health/<project-slug>/errors/<error-id>` (optionally with `?event_id=<event-id>` to focus on one specific event). Auto-detected; the skill parses project + error + event out of the path/query.
- **A Bugsnag project URL** — `https://app.bugsnag.com/aura-health/<project-slug>/errors`. Triggers Mode 1 triage on that project.
- **A specific error or event ID** via `--error <id>` / `--event <id>` flags.
- **A free-text triage intent** like "what's broken in `aura-userweb` in the last 24h" — `--project <slug>` (with fuzzy slug match, so `userweb` resolves to `aura-userweb`).

**Specific-error shortcut.** If the input identifies a single error (an error URL, an error ID, or an event URL), the skill **skips Mode 1 (triage) entirely** and runs Mode 2 (focus) directly. Mode 1 only kicks in for "what's broken" intents — never when the user has already pointed at a specific error.

## Workflow

### Resolve mode and inputs

| User said… | Mode | CLI flags |
|---|---|---|
| Pasted any `app.bugsnag.com/...` URL | Auto-detect from URL | `--url "<url>"` |
| "errors in `aura-userweb` in the last 4h" | **Triage (Mode 1)** | `--project aura-userweb --since <iso-8601>` |
| "what's broken everywhere" | **Triage (Mode 1, cross-project)** | `--all-projects --since <iso-8601>` |
| "drill into error 69c540...", "tell me more about this error" | **Focus (Mode 2)** | `--error <error-id>` |
| "investigate the code", "what changed recently?" (after a Mode 2 focus) | **Code investigation (Mode 2.5)** | `--prepare-code-context <error-id>`, then spawn `eng:code-investigator` |
| "trace event 69fe68..." | **Trace (Mode 3)** | `--event <event-id> --project <slug>` |
| "create a Linear card for this", "file this" | **Action — Linear (Mode 4a)** | `--prepare-linear-card <error-id>`, then `shared:add-linear-issues` |
| "reply in this Slack thread", "post a summary in `#alerts-bugsnag`" | **Action — Slack (Mode 4b)** | `--prepare-slack-reply <error-id>`, then post by composing `shared:post-to-slack` |

Defaults when the user is vague: `--since 24h`, `--limit 10`. `--since` accepts Bugsnag's duration shorthand — `1h`, `24h`, `7d`, `30d`, etc. (not ISO timestamps; that filter shape returns 400 on this endpoint).

### Run the query

Invoke the CLI from anywhere:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/investigate-bugsnag-errors/scripts/investigate.py <flags>
```

Pass the digest through to the user verbatim — every output ends with a `→ Drill in:` hint that the user can copy-paste back as their next prompt.

### Don't auto-chain

The skill never picks which row to drill into. The engineer reads the triage digest, decides which signature is interesting, and pastes the suggested Mode 2 command back. Same for Mode 2 → Mode 3.

### App-family routing

The project name is the **app-routing key** — it tells the skill which app the error belongs to, which determines stacktrace handling and (in upcoming PRs) the suspect repo for code-investigation handoffs. The 12 projects map to 6 app families (Aurie mobile/desktop/web; Aura mobile/desktop/web/embed). See [`references/app-family-routing.md`](references/app-family-routing.md) for the full table.

The skill **never asks "which app is this?"** — the project slug already answers it.

### PII guardrail

- **Modes 1 (triage) and 2 (focus)** never echo full `request` / `user` / `device` / `metadata` blocks — only specifically extracted fields appear in the digest.
- **Mode 3 (trace)** shows the full event payload by design, since the engineer has explicitly drilled in.
- **Aurie projects** (`AI - Aurie (iOS)`, `AI - Aurie (macOS)`, `AIWeb`) are flagged as high-PII because they carry user-conversation text in metadata. Treat trace output from those projects as more sensitive — the metadata sections are *named* in the trace digest, but the deep payload is left in the Bugsnag UI rather than echoed.

## Setup (one-time, per user)

**1. Bugsnag personal auth token.** Self-serve at <https://app.bugsnag.com/settings/my-account/auth-tokens> → Personal auth tokens.

**2. Store the token.** Add to `~/.claude/settings.json` under the `env` block as `BUGSNAG_TOKEN`. Restart Claude Code.

**3. Python deps.** `python3 -m pip install --user -r ${CLAUDE_PLUGIN_ROOT}/skills/investigate-bugsnag-errors/scripts/requirements.txt`. Just `requests`.

There is **no MCP fallback** for Bugsnag today, so a missing `BUGSNAG_TOKEN` is a hard fail with a clear "create a token + add to settings + restart" message — same shape as `BRAINTRUST_API_KEY` failure. Full walkthrough in [`references/auth-setup.md`](references/auth-setup.md).

**Cowork caveat ([PROD-1305](https://linear.app/aura-health/issue/PROD-1305)):** `mcp__workspace__bash` strips host env. Inside Cowork, paste-export `BUGSNAG_TOKEN` once at session start:

```bash
export BUGSNAG_TOKEN='<paste from host ~/.claude/settings.json>'
```

## Output

Each mode produces a fixed-shape text digest:

- **Mode 1 — triage** — top-N error signatures, one block per error (class, message, project, count, users, last-seen, `Drill in: investigate bugsnag --error <id>`).
- **Mode 2 — focus** — error metadata, app-family classification, top 8 stacktrace frames from the latest event, last 8 breadcrumbs, recent events list, `Drill in: investigate bugsnag --event <id>`.
- **Mode 3 — trace** — full event payload (stacktrace, breadcrumbs, user, device, request, metadata sections), then `→ Done — last mode in the chain.`

Examples are stable contracts — the renderers in `scripts/format.py` lock these shapes.

## Searching by user id

To find the error signatures a specific Firebase user id hit, use the sibling skill [`eng:search-bugsnag-by-user`](../search-bugsnag-by-user/SKILL.md) (`search.py --user <firebase-uid>`). Its results chain back here — each row ends with `investigate bugsnag --error <id>` for the focus → trace → resolve flow.

## Mode 2.5 — Code investigation (delegated to eng:code-investigator)

After a Mode 2 focus, the user can ask for code context with phrases like "investigate the code", "what changed recently?", or "look at the source". The skill chains to [`eng:code-investigator`](../../agents/code-investigator.md) — a read-only subagent that finds the local checkout of the suspect repo, refreshes to `origin/main` in a temporary worktree, and returns a structured report on the focus stacktrace frame.

**Orchestration is split between Python and Claude** because the `Agent` tool only exists at the model layer:

1. **Python builds the prompt.** Run:
   ```bash
   python3 ${CLAUDE_PLUGIN_ROOT}/skills/investigate-bugsnag-errors/scripts/investigate.py \
     --prepare-code-context <error-id>
   ```
   Output is either a structured prompt body, or a `[Mode 2.5 skipped]` line (no repo mapping, no usable frames, etc.).

2. **If the output starts with `[Mode 2.5 skipped]`,** relay it to the user as-is. Don't spawn the agent.

3. **Otherwise, spawn the subagent:**
   ```
   Agent({
     subagent_type: "eng:code-investigator",
     description: "Investigate code context for Bugsnag error <id>",
     prompt: <output of step 1>
   })
   ```

4. **Fold the agent's structured report into a "Code context" block** appended to the original Mode 2 focus digest. Don't re-render the whole digest — just label and append.

The subagent owns repo discovery, worktree management, and the file/`git log`/grep work. The Bugsnag skill never touches the user's working tree. Full orchestration spec, prompt template, and skip-condition catalogue: [`references/code-investigation.md`](references/code-investigation.md).

## Mode 4 — Action handoffs (Slack reply / Linear card)

After Mode 2 / 2.5 / 3, the user can ask the skill to take an action. **Always gated on explicit user confirmation** — Mode 4 never auto-triggers. Same Python-prepares + Claude-orchestrates split as Mode 2.5: Python emits a structured payload (JSON), Claude executes the actual write after the user confirms.

### Mode 4a — Linear card

`$OUT` is a folder of your own (e.g. `mktemp -d`), never a fixed `/tmp` name another session could share.

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/investigate-bugsnag-errors/scripts/investigate.py \
  --prepare-linear-card <error-id> \
  [--code-context-file "$OUT/mode-2-5-<error-id>.txt"]   # optional, splices Mode 2.5 in
```

Output is a JSON object with `title`, `description` (markdown — Bugsnag URL, error metadata, app family, top stacktrace frames, optional code context), `labels`, and a free-form `team_hint` derived from the app family. Claude shows the user a confirmation summary, then invokes [`shared:add-linear-issues`](../../../shared/skills/add-linear-issues/SKILL.md) on approval. The card description never references internal AI tools (no slash commands, agent names, or skill names) — it's read by humans (engineers, PMs, on-call) who don't need the orchestration scaffolding.

### Mode 4b — Slack reply

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/investigate-bugsnag-errors/scripts/investigate.py \
  --prepare-slack-reply <error-id> \
  [--code-context-file "$OUT/mode-2-5-<error-id>.txt"]
```

Output is a JSON object with `text` formatted in Slack mrkdwn (`*bold*`, `<url|label>`). Claude shows the user the draft + confirms which channel/thread to post into, then posts on approval by composing [`shared:post-to-slack`](../../../shared/skills/post-to-slack/SKILL.md) with `channel=<id>`, `thread_ts=<ts>` and the `text` payload — the canonical Slack path for every skill, rather than calling a Slack primitive directly.

The reply ends with the canonical attribution footer per `Tools/Slack` → Post format (pre-fetched via `knowledge.reads`): `_Generated by `eng:investigate-bugsnag-errors:<version>` · <YYYY-MM-DD>_`. The `--prepare-slack-reply` script appends this footer to the `text` payload itself — versioned (`<version>` read from this skill's `metadata.version`) and dated (UTC today) — so the draft is post-ready; don't add a second footer. Because the footer is already in `text`, pass `footer=''` to `shared:post-to-slack` so it does not append its own.

### Why no direct fixer-agent spawn

The PROD-1659 spec called out a third Mode 4 sub-action — direct handoff to `eng:implementation-agent`. Mode 4 stops at "card filed" instead. Two reasons: (1) the card body must stay clean of AI-tool references — auto-suggesting a fixer flow inside the card pollutes a human-facing artifact; (2) if the user later does want a fix, the canonical entrypoint is `eng:linear-task`, which already wraps `implementation-agent` with the right input shape — re-implementing that orchestration here would duplicate logic and bypass any future improvements to `linear-task`. If the user explicitly says "now run linear-task on it", they can chain themselves; Mode 4 doesn't auto-suggest.

Full orchestration spec, payload shapes, team-hint mapping, and edge cases: [`references/action-handoffs.md`](references/action-handoffs.md).

## Edge cases

- **Bugsnag returns 0 errors for the project + window** → print "0 errors in `<scope>` since `<since>`" with a suggestion to broaden the window. Don't error out.
- **Auth fails (401)** → print the specific failure with the one-line fix (regenerate token, add to settings.json, restart), not an opaque traceback.
- **Project slug typo** → fuzzy-match against the live project list; if no single match, list the available slugs.
- **Cross-project triage hits a rate limit (429)** → handled inside the API client (5 retries with `Retry-After`); per-project failures during the fan-out are skipped silently rather than failing the whole run.
- **URL with no `event_id`** in the query string → focus mode picks the latest event for the error and notes which one in the digest.
- **Project not in the app-family table** → falls through to `Unknown` family. Triage / focus / trace still work; the family label just reads "Unknown" and the suspect-repo column is blank. Add the slug to `scripts/projects.py` to fix.
- **Re-running the same query** → safe (read-only); no idempotency concern.

## References

- [`references/api-recipes.md`](references/api-recipes.md) — endpoint reference, headers, pagination conventions.
- [`references/auth-setup.md`](references/auth-setup.md) — one-time token creation walkthrough, Cowork workaround, rotation guidance.
- [`references/app-family-routing.md`](references/app-family-routing.md) — project → app-family → suspect-repo table; the source of truth that drives stacktrace formatting and downstream handoffs.
- [`references/code-investigation.md`](references/code-investigation.md) — Mode 2.5 orchestration spec, prompt template, skip-condition catalogue.
- [`references/action-handoffs.md`](references/action-handoffs.md) — Mode 4 orchestration spec for Linear and Slack writes, payload shapes, team-hint mapping, fixer-handoff handover to `eng:linear-task`.
- [Linear PROD-1659](https://linear.app/aura-health/issue/PROD-1659) — full design spec, scope of follow-up PRs.
- [Tools/Bugsnag](https://www.notion.so/35a75e23af2081b1a021d7b2b8999e2f) (AI Knowledge) — the same content surfaced for cross-skill discovery.
