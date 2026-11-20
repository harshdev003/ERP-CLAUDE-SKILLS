# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**Security review:** `.claude/rules/security.md` is the checklist every reviewer applies before approving a PR: Claude, Cursor (`.cursor/rules/security.mdc`), BugBot (`.cursor/BUGBOT.md`) and people. All three are copies of AI Knowledge → Compliance → Security → Code Review Checklist, identical in every backend service repo. Change that page first, then every copy.

## Commands

```bash
npm run dev          # Start server in development mode (NODE_ENV=development)
npm run test         # Run all Jest tests (NODE_ENV=test, --runInBand)
npm run lint         # Run ESLint
npm run serve        # Start production server (2GB heap)
npm run job:alert    # Run the alert job standalone
npm run job:report   # Run the report job standalone
```

To run a single test file:
```bash
NODE_ENV=test npx jest test/path/to/file.spec.js --runInBand
```

## Architecture

Node.js/Express microservice (port 3001) serving as a support hub for the Aura Health platform. Integrates with coach, content, email, experiment, notification, payment, user, and Slack services.

**Startup sequence** (`src/index.js`): validate config → init Redis cache (optional, graceful degradation) → init Postgres pool → init PubSub → init Slack bots → start ListenerManager → start HTTP server → register Slack notification handlers.

### Key Directories

- `src/commands/` — 50+ Slack slash-command implementations (each exports an async handler)
- `src/services/` — External service clients (postgres, pubsub, slack, email, payment, coach, user, experiment); each exposes `initialize()` / `finalize()` methods
- `src/http/` — Express routes, handlers, and middleware
- `src/models/` — Data access layer for command, prompt, and user tables
- `src/jobs/` — Standalone alert and report runners
- `src/listeners/` — Google Cloud PubSub subscription handlers (content, payment, user events)
- `src/notifications/` — Slack notification handlers
- `database/` — SQL schema (`create.sql`) defining the `supportDB` tables

### Context Pattern

Nearly every function receives a `Context` object: `async function({ context })`. The context carries a UUID transaction ID, user info, and forward headers. It is created per-request by `contextMiddleware` and used throughout for distributed tracing and structured logging.

### Configuration

All configuration lives in `src/config.js` (env-var driven, ~1,237 lines). A `validate()` call at startup aborts if critical vars are missing (PubSub projectId, Slack tokens, service token, Postgres URI). Redis is optional — the cache layer degrades gracefully if unavailable.

### Error Handling

Predefined error types with HTTP status codes are in `src/errors.js`. Errors are attached to the context via `context.addError()` and emitted as structured JSON responses including the transaction ID.

### Logging

Winston-based with levels: `audit`, `crit`, `error`, `warn`, `info`, `debug`. Logs are JSON-formatted (Stackdriver-compatible) and include the transaction ID from context.

### Database

PostgreSQL with tables: `user`, `report`, `alert`, `commandAudit`, `prompt`, `promptAudit`. The Postgres service is initialized as a connection pool at startup and accessed via `src/services/postgres.js`.

**Shared postgres core.** In `src/services/postgres.js`, the block between the `Shared postgres core` and `End of the shared postgres core` markers is byte-identical in all 13 backend services. Never change it in one service only: make the same change in every service and diff the marked block against another service before opening the PR. Service-specific functions go below the end marker. `test/unit/services/postgres.spec.js` is shared the same way.

**Bind every SQL value.** Never interpolate a value into SQL text. Use `getMany` / `count` filters, or pass a query config `{text, values}` to `execute` / `executeMany`. Column, table and operator names come from code only, never from a request or an event.

### Command Pattern

Commands in `src/commands/` extend the base `Command` class. Each command declares regex patterns via `appliesTo()` for matching, overrides `doExecute()` for logic, and is audited to PostgreSQL after execution. New commands must be registered in `src/commands/index.js`.

### Slack Integration

Two Slack bots run concurrently: the service bot (port 3001 path) and the Aura bot (Slack Bolt framework, port 3002). Commands are registered in `src/commands/` and invoked via the Slack event handler.

## Shared files

Some files must be byte-identical in all 13 backend services: the logger, `context.js`, `redact.js`, the HTTP connector and the shared HTTP middlewares. `shared-files.txt` lists each one with its canonical git blob sha. It is identical in every service, and so is `scripts/check-shared-files.sh`, the first step of the CircleCI `test` job, which fails the build when a listed file differs. A file marked `optional` is checked only in the services that have it.

To change a shared file on purpose:

1. Make the same change in every service that has the file. For a middleware, follow the HTTP middleware standard in Linear.
2. Get the new sha with `git hash-object <path>`, and update its line in `shared-files.txt` in every service, in the same PR batch.
3. Before you push, run `scripts/check-shared-files.sh --compare <dir>`, where `<dir>` holds all the service checkouts. It checks those checkouts against this list too, so a stale list or file in another service shows up.

Only files documented as shared (as the HTTP middleware standard documents the middlewares) belong on the list. Some other files are identical today only by coincidence (`cache.js`, `pubsub.js`, `guard.js`, the listeners, the `status` handler, `sleep.js`), so they stay off, to leave room for legitimate per-service changes. `.claude/rules/security.md` and `.cursor/rules/security.mdc` also stay off: they are built from the shared security checklist and kept in sync across every repo type separately.
