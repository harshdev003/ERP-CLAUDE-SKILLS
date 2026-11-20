# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**Security review:** `.claude/rules/security.md` is the checklist every reviewer applies before approving a PR: Claude, Cursor (`.cursor/rules/security.mdc`), BugBot (`.cursor/BUGBOT.md`) and people. All three are copies of AI Knowledge → Compliance → Security → Code Review Checklist, identical in every backend service repo. Change that page first, then every copy.

## Commands

```bash
nvm use                    # Switch to correct Node.js version (do this first)
npm run dev                # Start development server
npm run test               # Run all tests (Jest, single-band, from ./test)
npm run lint               # Lint code (must pass before submitting changes)
npm run lint:cache         # Lint with cache

# Run a single test file
NODE_ENV=test npx jest --runInBand ./test/path/to/test.js

# Background jobs
npm run job:alert
npm run job:analytics
npm run job:report
```

**Always run `nvm use` before making changes, and `npm run lint` after.**

## Architecture

B2B business service (Aura Health) managing business entities, members, subscriptions, users, analytics, alerts, and reports.

**Layers under `src/`:**

| Layer | Path | Responsibility |
|---|---|---|
| HTTP | `src/http/` | Thin controllers — parse request, call model, check `context.error`, return response |
| Models | `src/models/` | All business logic, validation, DB operations |
| Services | `src/services/` | External integrations: Postgres, PubSub, Email, Payment, User, Slack |
| Jobs | `src/jobs/` | Scheduled background processing (alert, analytics, report) |
| Listeners | `src/listeners/` | Google Cloud PubSub event handlers |

**Key files:** `src/config.js` (all env vars), `src/errors.js` (all error keys), `src/context.js` (request context), `src/logger.js` (Winston logger).

## Thin Controller Pattern

Handlers only handle HTTP I/O. All logic lives in models.

```javascript
// Handler — extract params, call model, check error once, return response
async function handler(request, response) {
  const {context} = request;
  const result = await model.retrieve(context.userId, {context});
  if (context.error) return response.status(context.error.status).json({error: context.error});
  return response.status(200).json({result});
}

// Model — validate, query DB, add errors to context
async function retrieve(userId, {context}) {
  if (!userId) { context.addError('MISSING_USER_ID'); return null; }
  const record = await postgres.getOneById(TABLE, userId, {context});
  if (!record) { context.addError('BUSINESS_NOT_FOUND', {userId}); return null; }
  return record;
}
```

Do **not** add a redundant null check in the handler after `context.error` — if the model returned null, it already set the error.

## Error Keys

All error keys must be defined in `src/errors.js`, sorted alphabetically within their category. Keys describe **what the error is**, not which model uses them (e.g. `TOKEN_MISSING_PROJECT`, not `AUTH_MISSING_PROJECT`).

Error response shape:
```json
{"error": {"key": "BUSINESS_NOT_FOUND", "message": "...", "status": 404, "transaction": "...", "transactionSlice": "..."}}
```

## Logging

Use `src/logger.js` — no `console.*` in production code.

- `logger.debug()` — operational traces (default for service internals)
- `logger.info()` — important business events, one per endpoint completion
- `logger.warn()` — potential issues, fallbacks
- `logger.error()` / `logger.crit()` — failures / immediate-attention issues

Always include a `meta` object: `{component, method, ...relevantIds}`.

## Database Conventions

Schema files:
- `database/create.sql` — full schema for fresh installs
- `database/update.sql` — incremental `ALTER TABLE` statements, **append at the bottom**
- `database/migrate.sql` — non-idempotent or complex one-off migrations

When adding a column: add it to `create.sql` in the table definition **and** append `ALTER TABLE "table" ADD COLUMN IF NOT EXISTS "col" TYPE;` to `update.sql`.

Schema conventions: double-quoted identifiers, `SERIAL PRIMARY KEY` + UUID business `id`, `TIMESTAMPTZ` timestamps (`createdAt`/`updatedAt`/`deletedAt`), unique index on `id`, indexes on FKs and frequently filtered columns.

**Shared postgres core.** In `src/services/postgres.js`, the block between the `Shared postgres core` and `End of the shared postgres core` markers is byte-identical in all 13 backend services. Never change it in one service only: make the same change in every service and diff the marked block against another service before opening the PR. Service-specific functions go below the end marker. `test/unit/services/postgres.spec.js` is shared the same way.

**Bind every SQL value.** Never interpolate a value into SQL text. Use `getMany` / `count` filters, or pass a query config `{text, values}` to `execute` / `executeMany`. Column, table and operator names come from code only, never from a request or an event.

## Code Style

- Single quotes, 2-space indent, trailing commas, async/await
- Guard clauses over nesting
- `camelCase` files/functions/variables, `UPPER_SNAKE_CASE` constants
- Model CRUD method names: `create`, `retrieve`, `list`, `update`, `remove`, `delete`
- Named parameters for optional arguments: `model.retrieve(id, {context, options})`
- Return `result`/`results` from model methods
- Services use `initialize` / `finalize` lifecycle hooks

## Git Workflow

Branch naming: `feature/`, `bugfix/`, `refactor/`, `quality/` prefixes. Conventional commits with scope (`feat(members): ...`). First line under 50 chars.

## Shared files

Some files must be byte-identical in all 13 backend services: the logger, `context.js`, `redact.js`, the HTTP connector and the shared HTTP middlewares. `shared-files.txt` lists each one with its canonical git blob sha. It is identical in every service, and so is `scripts/check-shared-files.sh`, the first step of the CircleCI `test` job, which fails the build when a listed file differs. A file marked `optional` is checked only in the services that have it.

To change a shared file on purpose:

1. Make the same change in every service that has the file. For a middleware, follow the HTTP middleware standard in Linear.
2. Get the new sha with `git hash-object <path>`, and update its line in `shared-files.txt` in every service, in the same PR batch.
3. Before you push, run `scripts/check-shared-files.sh --compare <dir>`, where `<dir>` holds all the service checkouts. It checks those checkouts against this list too, so a stale list or file in another service shows up.

Only files documented as shared (as the HTTP middleware standard documents the middlewares) belong on the list. Some other files are identical today only by coincidence (`cache.js`, `pubsub.js`, `guard.js`, the listeners, the `status` handler, `sleep.js`), so they stay off, to leave room for legitimate per-service changes. `.claude/rules/security.md` and `.cursor/rules/security.mdc` also stay off: they are built from the shared security checklist and kept in sync across every repo type separately.
