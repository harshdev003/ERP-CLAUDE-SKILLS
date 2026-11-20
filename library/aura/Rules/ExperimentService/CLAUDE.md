# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**Security review:** `.claude/rules/security.md` is the checklist every reviewer applies before approving a PR: Claude, Cursor (`.cursor/rules/security.mdc`), BugBot (`.cursor/BUGBOT.md`) and people. All three are copies of AI Knowledge → Compliance → Security → Code Review Checklist, identical in every backend service repo. Change that page first, then every copy.

## Commands

```bash
nvm use               # Switch to Node.js 24.15.0 (required before any work)
npm install           # Install dependencies
npm run dev           # Run server in development mode
npm run test          # Run all tests (Jest, --runInBand)
npm run lint          # Run ESLint
npm run release       # Create a release
```

**Run a single test:**
```bash
npm run test -- test/unit/sanity.spec.js
```

**Background jobs:**
```bash
npm run job:alert     # Send data alerts
npm run job:report    # Send data reports
npm run job:backup    # Backup database tables
```

## Mandatory Workflow

1. **Run `nvm use` first** before any code changes
2. **Run `npm run lint` after every code change** — fix all errors before proceeding

## Architecture

The service is a Node.js/Express REST API that manages A/B experiment assignments for users, backed by PostgreSQL and Redis cache.

**Source layers under `src/`:**
- `http/handlers/` — Thin controllers: extract params, call model, check `context.error`, return response
- `http/router/` — Express route definitions
- `http/middlewares/` — Auth, context injection, etc.
- `models/` — All business logic, validation, DB queries
- `services/` — External integrations: `postgres.js`, `cache.js` (Redis), `pubsub.js`, `slack.js`, `mixpanel.js`, `user.js`
- `jobs/` — Background processing (alert, report, backup)
- `listeners/` — Google Cloud Pub/Sub event listeners
- `connectors/` — HTTP client wrapper
- `config.js` — All env var bindings
- `logger.js` — Winston logger
- `context.js` — Per-request context object
- `errors.js` — All error key definitions (must be kept alphabetical)

**Thin controller pattern — handlers only do:**
```javascript
async function handler(request, response) {
  const {context} = request;
  const result = await model.retrieve(userId, {context});
  if (context.error) {
    return response.status(context.error.status).json({error: context.error});
  }
  return response.status(200).json({result});
}
```

**Models contain all business logic and add errors via:**
```javascript
context.addError('ERROR_KEY', {userId});
return null;
```

All error keys must be defined in `src/errors.js`, sorted alphabetically.

## Database Schema

- `database/create.sql` — Full schema for fresh installs
- `database/update.sql` — Incremental migrations; **always append new changes at the bottom**
- `database/migrate.sql` — One-off non-idempotent data migrations

When adding a column: update `create.sql` in the table definition AND append `ALTER TABLE "tableName" ADD COLUMN IF NOT EXISTS ...` to `update.sql`.

**Conventions:** double-quoted identifiers, `"rowid" SERIAL PRIMARY KEY` + business `"id"`, `TIMESTAMPTZ` for timestamps, unique index on `id`.

**Shared postgres core.** In `src/services/postgres.js`, the block between the `Shared postgres core` and `End of the shared postgres core` markers is byte-identical in all 13 backend services. Never change it in one service only: make the same change in every service and diff the marked block against another service before opening the PR. Service-specific functions go below the end marker. `test/unit/services/postgres.spec.js` is shared the same way.

**Bind every SQL value.** Never interpolate a value into SQL text. Use `getMany` / `count` filters, or pass a query config `{text, values}` to `execute` / `executeMany`. Column, table and operator names come from code only, never from a request or an event.

## Code Style

- Single quotes, 2-space indent, trailing commas in multi-line structures
- `async/await` only (no callbacks or raw promises)
- Model method names: `create`, `retrieve`, `list`, `update`, `remove`, `delete`
- Named parameters for optional arguments: `model.retrieve(id, {context, options})`
- Use `logger` (never `console.*`) with levels: `debug` for traces, `info` for completions, `warn` for potential issues, `error`/`crit` for failures
- Log metadata: `{component, method, userId, ...}`
- Use guard clauses to reduce nesting
- Wrap all I/O in `try/catch`; log and rethrow meaningful errors

## Git

- Branches: `feature/`, `bugfix/`, `refactor/`, `quality/`
- Conventional commits: `feat(scope): description` (first line under 50 chars)

## Shared files

Some files must be byte-identical in all 13 backend services: the logger, `context.js`, `redact.js`, the HTTP connector and the shared HTTP middlewares. `shared-files.txt` lists each one with its canonical git blob sha. It is identical in every service, and so is `scripts/check-shared-files.sh`, the first step of the CircleCI `test` job, which fails the build when a listed file differs. A file marked `optional` is checked only in the services that have it.

To change a shared file on purpose:

1. Make the same change in every service that has the file. For a middleware, follow the HTTP middleware standard in Linear.
2. Get the new sha with `git hash-object <path>`, and update its line in `shared-files.txt` in every service, in the same PR batch.
3. Before you push, run `scripts/check-shared-files.sh --compare <dir>`, where `<dir>` holds all the service checkouts. It checks those checkouts against this list too, so a stale list or file in another service shows up.

Only files documented as shared (as the HTTP middleware standard documents the middlewares) belong on the list. Some other files are identical today only by coincidence (`cache.js`, `pubsub.js`, `guard.js`, the listeners, the `status` handler, `sleep.js`), so they stay off, to leave room for legitimate per-service changes. `.claude/rules/security.md` and `.cursor/rules/security.mdc` also stay off: they are built from the shared security checklist and kept in sync across every repo type separately.
