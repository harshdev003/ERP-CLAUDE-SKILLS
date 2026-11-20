# AGENTS.md

This file provides guidance to coding agents (Codex, and any agent that reads AGENTS.md) when working with code in this repository.

**Security review:** `.claude/rules/security.md` is the checklist every reviewer applies before approving a PR: Claude, Cursor (`.cursor/rules/security.mdc`), BugBot (`.cursor/BUGBOT.md`) and people. All three are copies of AI Knowledge → Compliance → Security → Code Review Checklist, identical in every backend service repo. Change that page first, then every copy.

## Rules

Project rules are organized by topic in `.claude/rules/`. Keep this list in sync whenever files are added, renamed, or removed.

@.claude/rules/project-rules.md
@.claude/rules/architecture.md
@.claude/rules/coding.md
@.claude/rules/config.md
@.claude/rules/database.md
@.claude/rules/documentation.md
@.claude/rules/error-handling.md
@.claude/rules/logging.md
@.claude/rules/naming.md
@.claude/rules/performance.md
@.claude/rules/security.md

## Commands

```bash
nvm use                        # Switch to correct Node.js version (always run first)
npm run dev                    # Start server in development mode
npm run serve                  # Start server in production mode
npm run lint                   # Run ESLint
npm run lint:cache             # Run ESLint with cache (faster)
npm test                       # Run all tests (unit + integration)
npx jest --runInBand test/unit/utils      # Run a specific test folder
npx jest --runInBand ready.spec           # Run a specific spec file
```

**Mandatory workflow before any code change:** `nvm use && npm run lint`

To run a subset, call `jest` directly as above — don't try to filter through
`npm test`. The `test` script is `jest --runInBand ./test`, and jest treats
positional patterns as an OR, so `npm test -- --testPathPatterns="foo"` matches
`./test|foo` and quietly runs the whole suite. It reports a pass, which reads
as a green focused run when nothing was actually filtered. (`--testPathPattern`,
singular, was removed in jest 30 and now errors outright.)

## Local development

There is no local Postgres in this project. Both env files point at remote databases via `kubectl port-forward`:

- `.env` — prod (`127.0.0.1:5432` → prod cluster)
- `.env.development` — dev (`127.0.0.1:5431` → dev cluster)

`npm run dev` sets `NODE_ENV=development` and runs `node .`, which calls `require('dotenv').config({quiet: true})` with no `path` — that loads **`.env`** (not `.env.development`), so the dev server actually points at the prod DB by default. Make sure the right port-forward is up before booting.

When you need to mutate shared state (run migrations, create indexes, drop columns), give the SQL to the user to run rather than executing it from this session — it's the user's call to write to a shared DB. Read-only queries via the tunnel are fine.

**Authentication for local API calls:** `SERVICE_TOKEN` in `.env` does not expire — use it directly as a `Bearer` token without inspecting the JWT expiry. Only ask for a fresh token if a call returns an actual 401.

**OpenAI keys:** The IS routes per-feature traffic through project-scoped keys (`OPENAI_CONVERSATION_API_KEY`, `OPENAI_MEMORY_API_KEY`, `OPENAI_INSIGHTS_API_KEY`, etc.), each pointing at its own `[Production]`/`[Development]` OpenAI project. The plain `OPENAI_API_KEY` in `.env`/`.env.development` is the *original* general key from before the per-feature split — it maps to the **Production** (`proj_JrTkUhXyRFMYmnrL9eYJaI6D`) and **Development** (`proj_0jz2V8zQlwQEtMYPRlOab4CZ`) projects (via the `prod-gpt-model`/`dev-gpt-model` service accounts).

Do not confuse it with the `OPENAI_API_KEY` in `~/.claude/settings.json` — that is a *different* key scoped to the **Claude** project (`proj_a0a3k7ZPWbxGwQ8MeBhRzr6s`), used by Claude Code tooling, not the IS. To read a feature project's stored completions from a skill, use `OPENAI_ADMIN_API_KEY` + an `OpenAI-Project: <proj_id>` header (stored completions are scoped per project). Full project inventory: AI Knowledge → `Tools/OpenAI`.

## Architecture

The service is an Express.js AI/ML backend. All routes are mounted under `/ai`. The startup sequence in `src/index.js` initializes services in order: config → braintrust → cache → postgres → mixpanel → openai → pubsub → slack → vertexai → mcp → listeners → http.

### Layer overview (`src/`)

| Directory | Purpose |
|---|---|
| `http/` | Express app, router, middlewares, handlers (thin controllers) |
| `models/` | All business logic and DB access — one file per domain |
| `services/` | External service clients (OpenAI, Postgres, Braintrust, etc.) with `initialize`/`finalize` lifecycle |
| `jobs/` | Standalone background scripts run via npm scripts (e.g. `npm run job:dailyJournal`) |
| `tools/` | AI tool definitions (JSON schema + JS implementation) used for OpenAI tool-calling |
| `connectors/` | HTTP connector utility |
| `listeners/` | Pub/Sub event listeners managed by `ListenerManager` |
| `mcp/` | Model Context Protocol server integration |
| `prompters/` | Prompt-building utilities |
| `utils/` | Shared pure utilities |

### Key files

- `src/context.js` — `Context` class passed through every request. Holds `transaction`, `userId`, `token`, and `error`. Call `context.addError('ERROR_KEY', extras)` in models; check `context.error` once in handlers.
- `src/errors.js` — Canonical registry of all error keys (alphabetical). Every `addError` key must exist here.
- `src/config.js` — Merges all env vars into a structured config object. Destructure at module top using named aliases.
- `src/cache.js` — Redis-backed cache with `initialize`/`finalize`.

### Request flow

```
Router → authenticate middleware → Handler (extracts params, calls model) → Model (business logic + DB) → Handler (checks context.error, returns response)
```

### Testing

Tests live in `test/unit/` and `test/integration/`. Integration tests use `supertest` against the Express app built by `http/index.js#buidExpress`. The jest config points roots at `test/` and loads `test/setup/setupTestFramework.js` which sets a 30s timeout. Mocks are in `test/mocks/`.

### Tools pattern

Each AI tool has two files in `src/tools/`: a `.json` schema (OpenAI function definition) and a `.js` implementation. `toolManager.js` registers and dispatches tool calls.

### Jobs

Jobs are standalone Node scripts that use the same service layer. Run via `npm run job:<name>`. They call `service.initialize()` at start and are not part of the HTTP server.

A recommended shape for new jobs, not a requirement: AI Knowledge → Procedures → Engineering → Backend Jobs (a `Job` subclass in `src/jobs`, typically one SQL query for the candidates, a per-item method run in batches, a `config.jobs` block, an npm script, a README row and a CronJob in AuraInfra).
