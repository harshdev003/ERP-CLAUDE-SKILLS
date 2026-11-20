# Mode 4 — Action handoffs

After Mode 2 (focus), Mode 2.5 (code context), or Mode 3 (trace), the user can ask the skill to take an action: file a Linear card, reply in a Slack thread, or hand off to a fixer agent. Mode 4 is **always gated on explicit user confirmation** — these are visible side-effects, never auto-triggered.

Same Python-prepares + Claude-orchestrates split as Mode 2.5: Python emits a structured payload (JSON), Claude does the actual write through the relevant skill / MCP after the user says yes.

## When the user asks for a Mode 4 action

Common phrasings:

- "create a Linear card for this", "file this", "open a ticket"
- "reply in the Slack thread", "post a summary in `#alerts-bugsnag`"
- "send this to a fixer", "have Claude fix this"

After a focus or trace, Claude shows a confirmation summary first ("I'll create a Linear card titled X with description Y, suggested team `Engineering` — proceed?") and only writes after the user agrees.

## Mode 4a — Linear card

### 1. Build the payload

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/investigate-bugsnag-errors/scripts/investigate.py \
  --prepare-linear-card <error-id> \
  [--code-context-file "$OUT/mode-2-5-<error-id>.txt"]   # optional, splices Mode 2.5 in
```

Output is a JSON object:

```json
{
  "title": "[Aura / web (consumer)] TypeError: undefined is not an object …",
  "description": "**Bugsnag error:** [TypeError](https://app.bugsnag.com/...) …",
  "labels": ["bugsnag", "bug", "aura-web-consumer"],
  "team_hint": "Engineering",
  "_bugsnag_url": "https://app.bugsnag.com/aura-health/aura-userweb/errors/abc123",
  "_project_slug": "aura-userweb",
  "_error_id": "abc123"
}
```

The `_`-prefixed fields are metadata for the orchestrator's confirmation prompt — strip before passing to `shared:add-linear-issues`.

### 2. Confirm with the user, then create the card

Show a summary like:

> I'll file this Linear card:
> - **Title:** `[Aura / web (consumer)] TypeError: undefined is not an object…`
> - **Team:** `Engineering` (suggested — confirm or override)
> - **Labels:** `bugsnag`, `bug`, `aura-web-consumer`
> - Bugsnag link in description
> Proceed?

On approval, invoke [`shared:add-linear-issues`](../../../../shared/skills/add-linear-issues/SKILL.md) with the title / description / labels / team. That skill handles the actual Linear API call.

### 3. Confirm in chat

After `shared:add-linear-issues` returns, just confirm the URL to the user:

> Filed [PROD-XXXX](<url>).

**Don't** put internal AI-tool references inside the card description — no `/linear-task` suggestions, no agent names, no skill names. The card is read by humans (engineers, PMs, on-call) who don't need to see the AI orchestration scaffolding. Whoever picks up the ticket can decide how to fix — manually, by running an AI tool of their own, whatever.

If the user wants to chain straight into a fix, they can say so explicitly ("now run linear-task on it") — but Claude doesn't auto-suggest that next step in chat or in the card. Mode 4 stops at "card filed."

### Team-hint resolution

The Python builder maps the app family to a free-form team suggestion:

| App family | `team_hint` |
|---|---|
| Aurie / mobile | `AI` |
| Aurie / desktop | `AI` |
| Aurie / web | `AI` |
| Aura / mobile | `Mobile` |
| Aura / web (any audience) | `Engineering` |
| Aura / desktop | `Engineering` |
| Aura / embed | `Engineering` |
| Unknown | `Engineering` |

`shared:add-linear-issues` resolves the hint against the live Linear team list and prompts the user when the suggestion doesn't match exactly. Don't bind to a specific Linear team ID in this skill — team names rotate as the org grows.

## Mode 4b — Slack reply

### 1. Build the payload

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/investigate-bugsnag-errors/scripts/investigate.py \
  --prepare-slack-reply <error-id> \
  [--code-context-file "$OUT/mode-2-5-<error-id>.txt"]
```

Output:

```json
{
  "text": "*TypeError* — _Aura / web (consumer)_\n> undefined is not an object…\n`42` events  ·  `5` users affected  ·  last seen `2026-05-08T22:00:00Z`\n<https://app.bugsnag.com/...|Open in Bugsnag>",
  "_bugsnag_url": "...",
  "_project_slug": "aura-userweb",
  "_error_id": "abc123"
}
```

The `text` is **Slack mrkdwn** — `*bold*` not `**bold**`, no headers, plain `<url|label>` links. Pass it straight to `shared:post-to-slack` without further transformation.

### 2. Confirm with the user, then post

The orchestrator needs to know **which channel/thread** to post into. The user typically pastes a Slack thread URL alongside their request — Claude extracts `channel` + `thread_ts` from the URL via the Slack MCP and confirms before posting.

Show:

> I'll post this in `#alerts-bugsnag` (thread from earlier today):
> ```
> *TypeError* — _Aura / web (consumer)_
> > undefined is not an object…
> `42` events  ·  `5` users affected  ·  last seen `…`
> <bugsnag-url|Open in Bugsnag>
> ```
> Proceed?

On approval, post by **invoking the `shared:post-to-slack` skill** with the channel + `thread_ts` (it owns the connection — MCP-first, `SLACK_BOT_TOKEN` script fallback).

## Where to splice the Mode 2.5 code context

Both flags accept `--code-context-file <path>`. Workflow:

1. Run Mode 2.5 (`--prepare-code-context`); spawn `eng:code-investigator`; receive its structured report.
2. Save the report to a temp file (`"$OUT/code-context-<error-id>.txt"`).
3. Pass `--code-context-file "$OUT/code-context-<error-id>.txt"` to the Mode 4 prepare flag.

The Python builder splices the context under a `## Code context` section (Linear) or quote block (Slack). If the report is a `[Mode 2.5 skipped]` line, it's elided.

## What happens when there's no Bugsnag URL in scope

If Claude doesn't have an error ID (e.g., the user said "create a Linear card" without context), don't guess — ask which error. Mode 4 always operates on a specific focused error; it doesn't run from a Mode 1 triage digest directly.

## Edge cases

- **`shared:add-linear-issues` not installed.** Surface a clear error rather than failing silently. The orchestrator should suggest installing the `shared` plugin.
- **Slack MCP not connected.** Same as above — clear error, suggest connecting.
- **User wants to post to a thread the bot isn't in.** Slack MCP surfaces this; relay the failure to the user with a "invite the `aura-ai` bot to that channel first" hint.
- **Card already exists for this error.** This skill doesn't dedupe — the user (or `add-linear-issues`) can spot an existing card via the Bugsnag URL in the description and skip filing a duplicate. A future enhancement could pre-search Linear for issues mentioning the Bugsnag URL.
- **Bugsnag URL not yet shareable** (private project). Both payloads still build — the link just won't open for non-engineers.

## Future work tracked separately

- **A bespoke fixer-agent subagent** with stronger Bugsnag priors (e.g., recognises common React/RN error shapes and proposes targeted fixes). Today the fixer route goes through `eng:linear-task` → `eng:implementation-agent` (general-purpose).
- **Auto-resolve / comment** writes against Bugsnag's Data Access API. Not in scope for this skill — explicit follow-up if needed.
