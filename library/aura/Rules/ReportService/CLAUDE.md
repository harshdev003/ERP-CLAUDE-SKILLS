# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**Security review:** `.claude/rules/security.md` is the checklist every reviewer applies before approving a PR: Claude, Cursor (`.cursor/rules/security.mdc`), BugBot (`.cursor/BUGBOT.md`) and people. All three are copies of AI Knowledge → Compliance → Security → Code Review Checklist, identical in every backend service repo. Change that page first, then every copy.

## Setup

Run `nvm use` before making any changes to ensure you're on Node 24.15.0.

## Commands

```bash
npm run dev          # Start in development mode
npm run serve        # Start production server (2GB heap)
npm run test         # Run all tests
npm test -- --testPathPattern=<path>  # Run a single test file
npm run lint         # Lint the codebase
npm run lint:cache   # Lint with caching (used in CI)
npm run job:alert    # Run alert job
npm run job:analytics  # Run analytics job
npm run job:report   # Run report job
```

Always run `npm run lint` after making code changes.

## Architecture

Layered architecture with strict separation of concerns:

- **`src/http/handlers/`** — Thin controllers: extract params, call model, return response
- **`src/models/`** — All business logic, input validation, error registration, caching
- **`src/services/`** — Infrastructure clients (postgres, redis, pubsub, slack, user service)
- **`src/jobs/`** — Background jobs (alert, analytics, report)
- **`src/listeners/commands/`** — ~62 Pub/Sub event handlers
- **`src/utils/`** — Shared utilities

### Request Lifecycle

1. Request hits handler → handler extracts params and calls model
2. Model validates input, queries DB, caches results, adds errors to context on failure
3. Handler checks `context.error` and returns appropriate HTTP response

### Context Pattern

Every request has a `Context` instance (`src/context.js`) that carries the transaction ID, userId, auth token, and a single error slot. Models call `context.addError('ERROR_KEY', data)` — first error wins. Handlers check `context.error` after each model call.

### Error Handling

All error keys are defined in `src/errors.js` (sorted alphabetically), with `status` and `message`. Error response shape:
```json
{"error": {"key": "...", "message": "...", "status": 400, "transaction": "...", "transactionSlice": "..."}}
```

### Database

- `database/create.sql` — Full schema for fresh installs
- `database/update.sql` — Append incremental DDL changes at the bottom
- Double-quoted identifiers: `"user"`, `"createdAt"`, `"updatedAt"`
- All timestamps use `TIMESTAMPTZ`
- Tables have `rowid SERIAL PRIMARY KEY` plus a business `id TEXT` column

**Shared postgres core.** In `src/services/postgres.js`, the block between the `Shared postgres core` and `End of the shared postgres core` markers is byte-identical in all 13 backend services. Never change it in one service only: make the same change in every service and diff the marked block against another service before opening the PR. Service-specific functions go below the end marker. `test/unit/services/postgres.spec.js` is shared the same way.

**Bind every SQL value.** Never interpolate a value into SQL text. Use `getMany` / `count` filters, or pass a query config `{text, values}` to `execute` / `executeMany`. Column, table and operator names come from code only, never from a request or an event.

### Logging

Always use the logger from `src/logger.js`, never `console.log()`. Levels: `debug`, `info`, `warn`, `error`, `crit`. Include structured metadata objects. The logger blacklists sensitive fields automatically.

### Caching

Redis caching via `src/cache.js`. Cache keys are built with `src/utils/buildKey.js`. Models handle their own cache reads/writes.

### CRUD Naming

Model methods follow: `create`, `retrieve`, `list`, `update`, `remove`.

### Named Parameters

Use named parameters for optional arguments: `function foo(required, {optionA, optionB} = {})`.

## Environment Variables

Key variables (see `.env.sample` and `src/config.js` for full list):

| Variable | Purpose |
|---|---|
| `POSTGRES_DATABASE_URI` | PostgreSQL connection string |
| `REDIS_HOST` / `REDIS_PORT` | Redis connection |
| `GOOGLE_PROJECT_ID` | GCP project for Pub/Sub |
| `SERVICE_TOKEN` | Internal service authentication |
| `USER_SERVICE_BASE_URL` | URL for user-service (default: `http://user-service:3001`) |
| `SLACK_BOT_TOKEN` | Slack bot integration |

## Testing

Tests live in `test/unit/` and `test/integration/`. Test setup is in `test/setup/setupTestFramework`. Jest config roots at `test/`.

## CI/CD

CircleCI runs lint + tests on all branches. Docker images are built and pushed to GCR only on version tags (`v*`). The app runs on port 3001 inside Docker.

## Shared files

Some files must be byte-identical in all 13 backend services: the logger, `context.js`, `redact.js`, the HTTP connector and the shared HTTP middlewares. `shared-files.txt` lists each one with its canonical git blob sha. It is identical in every service, and so is `scripts/check-shared-files.sh`, the first step of the CircleCI `test` job, which fails the build when a listed file differs. A file marked `optional` is checked only in the services that have it.

To change a shared file on purpose:

1. Make the same change in every service that has the file. For a middleware, follow the HTTP middleware standard in Linear.
2. Get the new sha with `git hash-object <path>`, and update its line in `shared-files.txt` in every service, in the same PR batch.
3. Before you push, run `scripts/check-shared-files.sh --compare <dir>`, where `<dir>` holds all the service checkouts. It checks those checkouts against this list too, so a stale list or file in another service shows up.

Only files documented as shared (as the HTTP middleware standard documents the middlewares) belong on the list. Some other files are identical today only by coincidence (`cache.js`, `pubsub.js`, `guard.js`, the listeners, the `status` handler, `sleep.js`), so they stay off, to leave room for legitimate per-service changes. `.claude/rules/security.md` and `.cursor/rules/security.mdc` also stay off: they are built from the shared security checklist and kept in sync across every repo type separately.
