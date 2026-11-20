# Mode 2.5 — Code investigation orchestration

Mode 2.5 enriches the focus digest (Mode 2) with a "Code context" block sourced from the [`eng:code-investigator`](../../../agents/code-investigator.md) read-only subagent. The subagent finds the local checkout of the suspect repo, refreshes to `origin/main` in a temporary worktree, and returns a structured report on the focus stacktrace frame — all without touching the user's working tree.

## Why Claude (the model) orchestrates, not Python

Python can't spawn subagents — the `Agent` tool is only available to the model. So the architecture splits the work:

- **Python (`scripts/code_investigation.py`)** — fetches the error + latest event from Bugsnag, resolves the suspect repo from the app-family table, builds the structured prompt the subagent expects.
- **Claude** — reads that prompt, calls `Agent({ subagent_type: "eng:code-investigator", prompt: <prompt> })`, takes the agent's structured report, and folds it into the user-facing message.

This composition keeps Python data-fetching simple and lets Claude do the agent-orchestration work it's actually designed for.

## End-to-end flow

1. **User triggers Mode 2.5** after seeing a Mode 2 focus digest. Common phrasings: "investigate the code", "what changed recently?", "code context for this error", or just clicking through the drill-in hint.

2. **Claude runs the prompt builder:**
   ```bash
   python3 ${CLAUDE_PLUGIN_ROOT}/skills/investigate-bugsnag-errors/scripts/investigate.py \
     --prepare-code-context <error-id>
   ```
   The output is either a structured prompt body or a `[Mode 2.5 skipped]` message.

3. **If the output starts with `[Mode 2.5 skipped]`** (no repo mapping for the project, no usable frames in the latest event, etc.), Claude relays it as-is to the user and stops. No subagent spawn.

4. **Otherwise, Claude spawns the subagent:**
   ```
   Agent({
     subagent_type: "eng:code-investigator",
     description: "Investigate code context for Bugsnag error <id>",
     prompt: <output of step 2>
   })
   ```

5. **The subagent runs its workflow** (repo discovery → worktree on `origin/main` → focus-frame read → `git log` → callers → hypothesis) and returns a fixed-shape structured report. See [`code-investigator.md`](../../../agents/code-investigator.md) for the report shape.

6. **Claude folds the report into a "Code context" block** appended to the original focus digest. Don't re-render the whole digest — just append a labelled section.

## Prompt shape (what Python emits, what the subagent receives)

Per `eng:code-investigator`'s input schema:

```
Investigate the source code around a runtime error and return a short
structured report. You are spawned with all the context you need below.

repo: AuraHealth/UserWeb
error_summary: TypeError: undefined is not an object (...) (first_seen=…, last_seen=…)

focus_frames:
  - path: src/profile/avatar.tsx:42
    function: renderFallback
  - path: src/profile/header.tsx:18
    function: ProfileHeader
  - … up to 5 frames

time_window:
  since: <error.first_seen>
  until: <error.last_seen>

extra_context: app family is `Aura / web (consumer)` (stack: react). This is a standard surface.

max_words: 200

Per your read-only contract, do not modify any code. Return the report shape
defined in your system prompt …
```

`focus_frames` are sourced from the **latest event's** stacktrace, with vendor/`node_modules` frames filtered out where possible (`inProject == True` from Bugsnag's frame metadata, with stack-aware defaults for missing fields). Top 5 in-project frames; if there aren't any in-project frames, falls back to the top 5 raw frames.

## When Mode 2.5 is skipped

The Python builder emits a `[Mode 2.5 skipped]` line instead of a prompt when:

- **The project has no repo mapping** in the app-family table (TBD entries — `ai-aurie-ios`, `ai-aurie-macos`, `aura-aimacos`, `aura-businessweb`, `aura-crmweb`, `aura-investweb`, `aura-front-plugin` today). Fix: add the repo to `scripts/projects.py` and `references/app-family-routing.md`.
- **The latest event has no usable stacktrace** (e.g., Bugsnag pruned frame detail because the event aged out, or sourcemaps haven't resolved on a deployed bundle).

The skip message includes a one-line "how to fix" hint where applicable.

## When NOT to use Mode 2.5

- **The user is just triaging across many errors** (Mode 1). No focus, no code context.
- **The focus error is already understood** (the user already knows the cause). Don't burn an agent run on something the user has already grokked.
- **The repo isn't on this machine and `gh repo clone` would be slow / unauthorised.** The subagent handles this internally (falls back to a temp clone, surfaces an error if even that fails), but if the user explicitly says "skip the code lookup", honour that.

## Other read-only subagents (when not to extract a named one)

`eng:code-investigator` exists because *every* `investigate-*` skill needs the same primitive (repo discovery + worktree + focus-frame read). For one-off questions where the workflow shape is unique, prefer the generic `Explore` or `general-purpose` agents — see CLAUDE.md "Architecture principles" #4 ("When to extract a named subagent").

## Future shape

When [PROD-1660](https://linear.app/aura-health/issue/PROD-1660) gains a Mode 2.5 retrofit on `eng:investigate-gcp-logs`, the same orchestration applies: the GCP logs skill emits its own structured prompt for `code-investigator` (with log-line file/function refs as the focus frames), and Claude composes the same way.
