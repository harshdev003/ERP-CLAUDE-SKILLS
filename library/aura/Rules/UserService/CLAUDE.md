# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Rules are organized by topic in `.claude/rules/`.

**Security review:** `.claude/rules/security.md` is the checklist every reviewer applies before approving a PR: Claude, Cursor (`.cursor/rules/security.mdc`), BugBot (`.cursor/BUGBOT.md`) and people. All three files are copies of AI Knowledge → Compliance → Security → Code Review Checklist, and identical in every backend service. Change that page first, then every copy.

## Commands

```bash
nvm use               # Always run first — enforces correct Node.js version
npm run dev           # Run service locally (NODE_ENV=development)
npm run serve         # Run service for production (--max-old-space-size=2048)
npm run lint          # ESLint — must pass before any commit
npm run lint:cache    # Lint with cache (faster for iterative dev)
npm test              # Run all tests (Jest, --runInBand)
npm test -- --testPathPattern=<path>  # Run a single test file
```

**Background jobs** (run standalone):
```bash
npm run job:alert
npm run job:country
npm run job:report
npm run job:test
```

## Architecture

The service follows a strict layered architecture. Each layer has a single responsibility:

```
HTTP Request
    └── Router (src/http/router/)
        └── Middleware (authenticate, authorize, context injection)
            └── Handler (src/http/handlers/) — thin controller: parse params, call model, check context.error once
                └── Model (src/models/) — all business logic, validation, DB calls, context.addError()
                    └── Service (src/services/) — external I/O (PostgreSQL, Firebase, Google APIs, Redis, Pub/Sub)
```

### Key files

- **`src/context.js`** — `Context` class carried through every layer. Holds `userId`, `transaction` UUID for tracing, and first-error-wins accumulation via `context.addError(key, extras)`. Use `context.meta(options)` for structured log metadata.
- **`src/errors.js`** — Canonical registry of all error keys. Every `addError()` call must reference a key defined here (alphabetical order within category).
- **`src/config.js`** — All app constants and env-var bindings. Destructure at top of module: `const { service: { version: SERVICE_VERSION } } = config;`
- **`src/services/postgres.js`** — PostgreSQL adapter with connection pooling. Exposes `initialize`, `finalize`, and helpers like `getOneById`, `insert`, `update`.
- **`src/http/index.js`** — Express setup: helmet → cors → logging → compression → JSON → context middleware → router → error handler.

### Handler pattern (thin controller)

```javascript
async function retrieve(request, response) {
  const { context } = request;
  const result = await model.retrieve(context.userId, { context });
  if (context.error) return response.status(context.error.status).json({ error: context.error });
  return response.status(200).json({ result });
}
```

### Model pattern (all business logic)

```javascript
async function retrieve(userId, { context }) {
  const user = await postgres.getOneById(USER_TABLE, userId, { context });
  if (!user) { context.addError('USER_NOT_FOUND', { userId }); return null; }
  // ... business logic ...
  return result;
}
```

### Services

All services expose `initialize({context})` and `finalize({context})` lifecycle hooks called at app startup/shutdown. Integrations include: PostgreSQL, Firebase (auth + RTDB), Google APIs, Redis (cache), Google Cloud Pub/Sub, Mixpanel, Iterable, Slack, SmartyStreets.

### Background jobs & listeners

- `src/jobs/` — standalone Node.js scripts run via `npm run job:*`
- `src/listeners/` — Pub/Sub event listeners using a command pattern

A recommended shape for new jobs, not a requirement: AI Knowledge → Procedures → Engineering → Backend Jobs (a `Job` subclass in `src/jobs`, typically one SQL query for the candidates, a per-item method run in batches, a `config.jobs` block, an npm script, a README row and a CronJob in AuraInfra).

### Database

- `database/create.sql` — full schema for fresh installs
- `database/update.sql` — incremental `ALTER TABLE` migrations appended at the bottom (idempotent)

**Shared postgres core.** In `src/services/postgres.js`, the block between the `Shared postgres core` and `End of the shared postgres core` markers is byte-identical in all 13 backend services. Never change it in one service only: make the same change in every service and diff the marked block against another service before opening the PR. Service-specific functions go below the end marker. `test/unit/services/postgres.spec.js` is shared the same way.

**Bind every SQL value.** Never interpolate a value into SQL text. Use `getMany` / `count` filters, or pass a query config `{text, values}` to `execute` / `executeMany`. Column, table and operator names come from code only, never from a request or an event.

## Shared files

Some files must be byte-identical in all 13 backend services: the logger, `context.js`, `redact.js`, the HTTP connector and the shared HTTP middlewares. `shared-files.txt` lists each one with its canonical git blob sha. It is identical in every service, and so is `scripts/check-shared-files.sh`, the first step of the CircleCI `test` job, which fails the build when a listed file differs. A file marked `optional` is checked only in the services that have it.

To change a shared file on purpose:

1. Make the same change in every service that has the file. For a middleware, follow the HTTP middleware standard in Linear.
2. Get the new sha with `git hash-object <path>`, and update its line in `shared-files.txt` in every service, in the same PR batch.
3. Before you push, run `scripts/check-shared-files.sh --compare <dir>`, where `<dir>` holds all the service checkouts. It checks those checkouts against this list too, so a stale list or file in another service shows up.

Only files documented as shared (as the HTTP middleware standard documents the middlewares) belong on the list. Some other files are identical today only by coincidence (`cache.js`, `pubsub.js`, `guard.js`, the listeners, the `status` handler, `sleep.js`), so they stay off, to leave room for legitimate per-service changes. `.claude/rules/security.md` and `.cursor/rules/security.mdc` also stay off: they are built from the shared security checklist and kept in sync across every repo type separately.

## Rules

Keep this list in sync whenever `.claude/rules/` files are added, renamed, or removed.

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
