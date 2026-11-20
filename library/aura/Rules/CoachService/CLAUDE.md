# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**Security review:** `.claude/rules/security.md` is the checklist every reviewer applies before approving a PR: Claude, Cursor (`.cursor/rules/security.mdc`), BugBot (`.cursor/BUGBOT.md`) and people. All three are copies of AI Knowledge → Compliance → Security → Code Review Checklist, identical in every backend service repo. Change that page first, then every copy.

## Commands

```bash
nvm use                    # Switch to Node 24.15.0 (required before anything else)
npm run dev                # Start development server
npm run lint               # Lint (required after any code change)
npm run lint:cache         # Lint with cache (faster, used in CI)
npm test                   # Run all tests (sequential, 30s timeout)
```

**Tests need `ONSCHED_EXTERNAL_ID` set** (any value, e.g. `ONSCHED_EXTERNAL_ID=test npm test`). The OnSched webhook handlers hash it when the module loads, so an unset value throws before any spec runs.

To run a single test file:
```bash
npx jest test/unit/models/coach.test.js
```

Background jobs are run individually:
```bash
node src/jobs/sendAppointmentReminder.js
```

## Architecture

Layered Node.js/Express REST API:

- `src/http/handlers/` — Thin controllers organized by feature (e.g. `coaching/`, `scheduling/`, `events/`). Each handler extracts params, calls a model method, checks `context.error`, and returns an HTTP response. No business logic here.
- `src/models/` — All business logic and database access (PostgreSQL). 38 model files covering coaches, appointments, subscriptions, communities, chat, events, video, etc.
- `src/services/` — External service clients: Zoom, Agora, Sendbird, Google Calendar, Outlook Calendar, OnSched (scheduling platform), Iterable, Mixpanel, Pub/Sub, Slack, Instagram, email/notification/payment services.
- `src/jobs/` — Standalone background scripts for reminders, auto-cancellations, reports, and automated messages. Each job imports services, runs, and exits.
- `src/listeners/` — Google Pub/Sub event listeners.
- `src/utils/` — Shared utilities.

## Key Patterns

### Thin Controller / Context Error Pattern

Handlers check `context.error` exactly once — no redundant null checks:

```javascript
// handler
async function retrieve(request, response) {
  const {context} = request;
  const result = await model.retrieve(context.userId, {context});
  if (context.error) {
    return response.status(context.error.status).json({error: context.error});
  }
  return response.status(200).json({result});
}

// model — sets context error and returns null on failure
async function retrieve(userId, {context}) {
  const user = await postgres.getOneById(USER_TABLE, userId, {context});
  if (!user) {
    context.addError('USER_NOT_FOUND', {userId});
    return null;
  }
  return user;
}
```

### Error Keys

All error keys must be defined in `src/errors.js`, sorted alphabetically within category. Keys are semantic (describe the error, not the model): e.g. `TOKEN_MISSING_PROJECT` not `AUTH_TOKEN_MISSING`.

### Config Binding

Destructure config at the top of each module:
```javascript
const {service: {version: SERVICE_VERSION}} = config;
```
Use config constants for non-environment values; use env vars only for secrets and per-environment URLs.

### Logging

Use `logger` exclusively — no `console.*` in production code.
- `debug`: operational traces (default for handlers/services)
- `info`: completion logs, important business events (one per endpoint: `logger.info('Done getting recommendations', meta)`)
- `warn`: potential issues, fallback scenarios
- `error` / `crit`: actual failures

Always include a `meta` object with relevant identifiers.

### Naming

- Files: `camelCase.js`
- Handler functions: `create`, `retrieve`, `list`, `update`, `remove` (CRUD verbs)
- For JS reserved words (e.g. `delete`): name the function `deleteJournal`, export as `delete: deleteJournal`
- Constants: `UPPER_SNAKE_CASE`

## Database

- `database/create.sql` — Full schema for fresh installs
- `database/update.sql` — Append-only incremental migrations (never edit existing lines)

When adding a column: update the table definition in `create.sql` AND append an `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` at the bottom of `update.sql`. Use double-quoted identifiers (`"columnName"`), `TIMESTAMPTZ` for timestamps, and `SERIAL` + uuid for primary keys.

**Shared postgres core.** In `src/services/postgres.js`, the block between the `Shared postgres core` and `End of the shared postgres core` markers is byte-identical in all 13 backend services. Never change it in one service only: make the same change in every service and diff the marked block against another service before opening the PR. Service-specific functions go below the end marker. `test/unit/services/postgres.spec.js` is shared the same way.

**Bind every SQL value.** Never interpolate a value into SQL text. Use `getMany` / `count` filters, or pass a query config `{text, values}` to `execute` / `executeMany`. Column, table and operator names come from code only, never from a request or an event.

## Commit Convention

Conventional commits: `feat(auth): add JWT validation`. Branch names: `feature/`, `bugfix/`, `refactor/`, `quality/`.

## Shared files

Some files must be byte-identical in all 13 backend services: the logger, `context.js`, `redact.js`, the HTTP connector and the shared HTTP middlewares. `shared-files.txt` lists each one with its canonical git blob sha. It is identical in every service, and so is `scripts/check-shared-files.sh`, the first step of the CircleCI `test` job, which fails the build when a listed file differs. A file marked `optional` is checked only in the services that have it.

To change a shared file on purpose:

1. Make the same change in every service that has the file. For a middleware, follow the HTTP middleware standard in Linear.
2. Get the new sha with `git hash-object <path>`, and update its line in `shared-files.txt` in every service, in the same PR batch.
3. Before you push, run `scripts/check-shared-files.sh --compare <dir>`, where `<dir>` holds all the service checkouts. It checks those checkouts against this list too, so a stale list or file in another service shows up.

Only files documented as shared (as the HTTP middleware standard documents the middlewares) belong on the list. Some other files are identical today only by coincidence (`cache.js`, `pubsub.js`, `guard.js`, the listeners, the `status` handler, `sleep.js`), so they stay off, to leave room for legitimate per-service changes. `.claude/rules/security.md` and `.cursor/rules/security.mdc` also stay off: they are built from the shared security checklist and kept in sync across every repo type separately.
