# Auth setup for investigate-openai-trace

> **Migrated off Braintrust 2026-06-05** (PROD-2464, umbrella PROD-2456). This skill no longer uses `BRAINTRUST_API_KEY`. It reads OpenAI stored chat-completions via the OpenAI API using a **dual-key model (PROD-3572)**: `OPENAI_ADMIN_API_KEY` (the same Admin key the cost skills `eng:analyze-openai-costs` and `eng:report-openai-user-cost` use) authenticates the `GET /v1/organization/projects` discovery call, and per-project service-account keys `OPENAI_<PROJECT>_API_KEY` authenticate the stored-completions reads (OpenAI enforces a `model.request` scope on `/v1/chat/completions` that Admin keys can't hold). No MCP fallback exists, so the Admin key is a hard requirement; a missing project key soft-skips that project.

## One-time setup

1. **Create an Admin API key** (for project discovery). Go to <https://platform.openai.com/settings/organization/admin-keys> → Create new key.
   - This requires **Owner** role on the Aura OpenAI org. If you don't have it, ask in `#ai-eng`.
   - The Admin key starts with `sk-admin-…` and is **distinct** from the project-scoped service-account keys (`OPENAI_<PROJECT>_API_KEY`, `sk-svcacct-…`) this skill uses for the actual stored-completions reads. The Admin key authenticates only the `/v1/organization/projects` discovery call used to enumerate the production projects.
   - The key is shown once. Copy it before leaving the page.

2. **Create project-scoped service-account keys** (for stored-completions reads — PROD-3572). Since OpenAI enforces a `model.request` scope on `GET /v1/chat/completions` that Admin keys can't hold, each production project needs its own key. Go to <https://platform.openai.com/api-keys> → Create new secret key → Owned by: **Service account** → Project: `<name>`, then store it under the env var derived from the project display name:
   - `Memory [Production]` → `OPENAI_MEMORY_API_KEY`
   - `Conversation [Production]` → `OPENAI_CONVERSATION_API_KEY`
   - `Journal [Production]` → `OPENAI_JOURNAL_API_KEY`
   - …one per production project you want to read completions from.

3. **Add them to your Claude Code settings.** Edit `~/.claude/settings.json` and add (or update) the `env` block:

   ```json
   {
     "env": {
       "OPENAI_ADMIN_API_KEY": "sk-admin-...",
       "OPENAI_CONVERSATION_API_KEY": "sk-...",
       "OPENAI_MEMORY_API_KEY": "sk-...",
       "OPENAI_JOURNAL_API_KEY": "sk-..."
     }
   }
   ```

   If you already have an `env` block (you likely do, if you've run the OpenAI cost skills), just confirm `OPENAI_ADMIN_API_KEY` is present and add the `OPENAI_<PROJECT>_API_KEY` entries alongside it. Don't duplicate the block.

4. **Restart Claude Code.** The CLI loads `env` at startup, so changes only take effect after a restart.

5. **Verify** by running any small triage:

   ```bash
   python3 ${CLAUDE_PLUGIN_ROOT}/skills/investigate-openai-trace/scripts/investigate.py \
     --project memory --since 10m
   ```

   If you see a list of recent Memory completions (and a `# projects: discovered N [Production] project(s)` line on stderr), you're good. If you see `AUTH ERROR: OPENAI_ADMIN_API_KEY is not set`, the env block hasn't loaded — confirm the file path, the JSON syntax, and that Claude Code has actually been restarted. A `401 Unauthorized` **on the projects discovery call** means the Admin key isn't an Admin key with Owner role; a `401` **on a completions read** (not discovery) means the project-scoped `OPENAI_<PROJECT>_API_KEY` is missing/wrong, or the Admin key was mistakenly used for a completions read — PROD-3572 requires the project key there, since the Admin key can't hold the `model.request` scope.

## Cowork workaround ([PROD-1305](https://linear.app/aura-health/issue/PROD-1305))

Cowork's `mcp__workspace__bash` strips host env, so `~/.claude/settings.json`'s `env` block doesn't reach the sandbox. If you're running this skill inside a Cowork session, paste-export the Admin key **and** the project keys you need once at session start:

```bash
# inside mcp__workspace__bash, at session start
export OPENAI_ADMIN_API_KEY='<paste from host ~/.claude/settings.json>'
export OPENAI_CONVERSATION_API_KEY='<paste from host ~/.claude/settings.json>'
export OPENAI_MEMORY_API_KEY='<paste from host ~/.claude/settings.json>'
# … plus any other OPENAI_<PROJECT>_API_KEY the investigation needs
```

The export persists for the lifetime of the workspace bash process. Rules:

- **Don't echo, log, or write the value to `outputs/`.** Treat it like a one-shot in-memory secret.
- **Re-prompt on session restart.** If Cowork rotates the workspace, the export is gone — ask the user again rather than caching.

## Rotation

Admin keys can be revoked at <https://platform.openai.com/settings/organization/admin-keys> at any time. After revocation:

1. Create a new Admin key.
2. Replace the value in `~/.claude/settings.json`.
3. Restart Claude Code.

The skill picks up the new value on the next invocation. No other action required — there's no token cache outside the env var.

## Permissions

The Admin key inherits the permissions of the Owner who created it. This skill is read-only — it only `GET`s `/v1/organization/projects`, `/v1/chat/completions`, `/v1/chat/completions/{id}`, and `/v1/chat/completions/{id}/messages`. It never writes.

## Token inventory cross-reference

The full token inventory lives in [`AINative/CLAUDE.md`](../../../../../CLAUDE.md) under "Token inventory" — that's the canonical list. `OPENAI_ADMIN_API_KEY` is shared between this skill and the OpenAI cost-analysis skills (`eng:analyze-openai-costs`, `eng:analyze-openai-cost-per-user`, `eng:report-openai-user-cost`, etc.) for the `/v1/organization/*` endpoints, so the one-time Admin-key setup unlocks all of them. The project-scoped `OPENAI_<PROJECT>_API_KEY` keys are the PROD-3572 additions specific to this skill's stored-completions reads. The canonical access-pattern doc is the `Tools/OpenAI` page in AI Knowledge.
