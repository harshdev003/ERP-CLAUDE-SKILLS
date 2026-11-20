# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

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

**Production logs** are in GCP project `firebase-auratech16` (GKE cluster `aura-prod-cluster-a`, namespace `aura-services`), which also holds the LiveKit voice agent's logs. `aura-prod` grants no log access and `aura-main` only holds BigQuery logs, so a query against either returns `PERMISSION_DENIED` or nothing, which is easy to misread as "no matching logs". Pass `--project firebase-auratech16` explicitly; `gcloud`'s default project is usually `aura-dev`.

**Authentication for local API calls:** `SERVICE_TOKEN` in `.env` does not expire — use it directly as a `Bearer` token without inspecting the JWT expiry. Only ask for a fresh token if a call returns an actual 401.

**OpenAI keys:** The IS routes per-feature traffic through project-scoped keys (`OPENAI_CONVERSATION_API_KEY`, `OPENAI_MEMORY_API_KEY`, `OPENAI_INSIGHTS_API_KEY`, etc.), each pointing at its own `[Production]`/`[Development]` OpenAI project. The plain `OPENAI_API_KEY` in `.env`/`.env.development` is the *original* general key from before the per-feature split — it maps to the **Production** (`proj_JrTkUhXyRFMYmnrL9eYJaI6D`) and **Development** (`proj_0jz2V8zQlwQEtMYPRlOab4CZ`) projects (via the `prod-gpt-model`/`dev-gpt-model` service accounts).

Do not confuse it with the `OPENAI_API_KEY` in `~/.claude/settings.json` — that is a *different* key scoped to the **Claude** project (`proj_a0a3k7ZPWbxGwQ8MeBhRzr6s`), used by Claude Code tooling, not the IS. To read a feature project's stored completions from a skill, use `OPENAI_ADMIN_API_KEY` + an `OpenAI-Project: <proj_id>` header (stored completions are scoped per project). Full project inventory: AI Knowledge → `Tools/OpenAI`.

## Architecture

The service is an Express.js AI/ML backend. All routes are mounted under `/ai`. The startup sequence in `src/index.js` initializes services in order: config → braintrust → cache → postgres → bigquery → mixpanel → openai → pubsub → slack → listeners → http.

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

**Mocking a lazily-required sibling model.** Models break circular dependencies by
requiring each other *inside* the function (`const dialogueModel = require('./dialogue');`
in `conversation.summarize` and `conversation.processEndSideEffects`). That require runs
during the test, **after** `jest.isolateModules(...)` has returned, so it resolves against
the outer registry — and a `jest.mock(path, () => freshMock)` re-registered per test in a
`loadFreshModules()` helper only takes effect the *first* time. Every later test silently
gets the first test's mock. The symptom is an assertion that looks like a source bug
(a test passing `dialogues: []` sees the previous test's three dialogues).

Hoist that one mock to file scope and re-program it per test instead:

```js
const mockDialogueList = jest.fn();
jest.mock('../../../src/models/dialogue', () => ({
  list: (...args) => mockDialogueList(...args),
}));
// per test: mockDialogueList.mockReset(); mockDialogueList.mockResolvedValue(...);
```

Mocks for `services` / `prompters` don't need this — those are required at module top,
inside the `isolateModules` callback, so they isolate correctly. See
`test/unit/models/conversation.summarize.spec.js` ([#457](https://github.com/AuraHealth/IntelligenceService/pull/457)).

### Tools pattern

Each AI tool has two files in `src/tools/`: a `.json` schema (OpenAI function definition) and a `.js` implementation. `toolManager.js` registers and dispatches tool calls.

### Jobs

Jobs are standalone Node scripts that use the same service layer. Run via `npm run job:<name>`. They call `service.initialize()` at start and are not part of the HTTP server.

A recommended shape for new jobs, not a requirement: AI Knowledge → Procedures → Engineering → Backend Jobs (a `Job` subclass in `src/jobs`, typically one SQL query for the candidates, a per-item method run in batches, a `config.jobs` block, an npm script, a README row and a CronJob in AuraInfra).

## Postgres

**Shared postgres core.** In `src/services/postgres.js`, the block between the `Shared postgres core` and `End of the shared postgres core` markers is byte-identical in all 13 backend services. Never change it in one service only: make the same change in every service and diff the marked block against another service before opening the PR. Service-specific functions go below the end marker. `test/unit/services/postgres.spec.js` is shared the same way.

**Bind every SQL value.** Never interpolate a value into SQL text. Use `getMany` / `count` filters, or pass a query config `{text, values}` to `execute` / `executeMany`. Column, table and operator names come from code only, never from a request or an event.

## Shared files

Some files must be byte-identical in all 13 backend services: the logger, `context.js`, `redact.js`, the HTTP connector and the shared HTTP middlewares. `shared-files.txt` lists each one with its canonical git blob sha. It is identical in every service, and so is `scripts/check-shared-files.sh`, the first step of the CircleCI `test` job, which fails the build when a listed file differs. A file marked `optional` is checked only in the services that have it.

To change a shared file on purpose:

1. Make the same change in every service that has the file. For a middleware, follow the HTTP middleware standard in Linear.
2. Get the new sha with `git hash-object <path>`, and update its line in `shared-files.txt` in every service, in the same PR batch.
3. Before you push, run `scripts/check-shared-files.sh --compare <dir>`, where `<dir>` holds all the service checkouts. It checks those checkouts against this list too, so a stale list or file in another service shows up.

Only files documented as shared (as the HTTP middleware standard documents the middlewares) belong on the list. Some other files are identical today only by coincidence (`cache.js`, `pubsub.js`, `guard.js`, the listeners, the `status` handler, `sleep.js`), so they stay off, to leave room for legitimate per-service changes. `.claude/rules/security.md` and `.cursor/rules/security.mdc` also stay off: they are built from the shared security checklist and kept in sync across every repo type separately.
